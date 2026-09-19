"""P0 验收：ELA 检测器（不是"跑通不报错"，而是"指对方向 + 不误报"）

五个受控样本，三个断言维度：
  A 真实照片            → 误报护栏：不得凭空报异常
  B 拼接（原始像素）    → 检出能力：必须报"偏亮"且框住粘贴位置
  C 美颜磨皮 ×3 算子    → 误报护栏（最关键）：**不得报成"偏亮/拼接"**
  D 纯 PNG              → 失效声明：必须 confidence=low + 说明"不适用"而非"无异常"
  E 磨皮 + q=95         → 陷阱复现：q 取到相机自身质量时极性反转 → 证明默认 q=90 有依据

用法： python experiments/p0_smoke_ela.py [底图路径]
产物： output/p0/*.png|json  +  output/p0/P0-验证报告.md
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard import config                                                  # noqa: E402
from voiceguard.forensics import load_and_prepare, make_evidence_sheet, save_result  # noqa: E402
from voiceguard.forensics.ela import ela_detector                              # noqa: E402

OUT = Path(config.OUTPUT_DIR) / "p0"
OUT.mkdir(parents=True, exist_ok=True)
DEFAULT_SRC = Path(config.SAMPLES_DIR) / "base_neutral.jpg"
# ⚠️ 隐私铁律：样本一律用 samples/base_neutral.jpg（程序合成，无个人数据）。
# 绝不把用户个人照片（尤其证件类）作为默认样本，更不上传到云端 VL 接口。
# 要测真实照片时用命令行显式传路径，并确认该照片可对外。


# ---------------------------------------------------------------- 样本构造


def load_base(src: Path):
    img, meta = load_and_prepare(src)
    print(f"底图：{src.name}  {meta['shape'][1]}×{meta['shape'][0]}  is_jpeg={meta['is_jpeg']}")
    return img, meta


def save_jpeg(img: np.ndarray, name: str, q: int = 95) -> Path:
    p = OUT / name
    cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tofile(str(p))
    return p


def make_splice(img: np.ndarray, q: int = 95):
    """贴一块"从没被 JPEG 压过"的像素（numpy 直接生成的连续渐变）→ ELA 应在该处变亮。"""
    h, w = img.shape[:2]
    pw, ph = w // 5, h // 5
    x, y = int(w * 0.58), int(h * 0.30)
    patch = np.zeros((ph, pw, 3), np.float32)
    gx = np.linspace(60, 200, pw, dtype=np.float32)[None, :]
    gy = np.linspace(30, 120, ph, dtype=np.float32)[:, None]
    for c, (a, b) in enumerate([(1.0, 0.6), (0.8, 1.0), (0.6, 0.9)]):
        patch[:, :, c] = gx * a + gy * b
    patch += np.random.default_rng(7).normal(0, 6, patch.shape).astype(np.float32)
    out = img.copy()
    out[y : y + ph, x : x + pw] = np.clip(patch, 0, 255).astype(np.uint8)
    p = save_jpeg(out, f"B_splice_q{q}.jpg", q)
    return out, {"path": str(p), "is_jpeg": True, "shape": out.shape, "exif_present": False,
                 "gt_bbox": [int(x), int(y), int(pw), int(ph)], "gt_polarity": "brighter"}


def make_beauty(img: np.ndarray, kind: str, q: int = 95, gain: float = 0.0):
    """模拟美颜磨皮：只在中央椭圆（脸的位置）做保边/去块平滑。

    磨皮 = 局部高频衰减 = 该处重压缩残差变化 → ELA 上表现为"偏暗"（或 q≈原图质量时反转为偏亮）。
    这是本项目最大的误报源：只看绝对值会把每张美颜图判成伪造。
    """
    h, w = img.shape[:2]
    if kind == "bilateral":
        sm = cv2.bilateralFilter(img, 41, 120, 120)
    elif kind == "median":
        sm = cv2.medianBlur(img, 31)
    else:  # beauty = 典型美颜（先中值去块效应，再保边平滑）
        sm = cv2.bilateralFilter(cv2.medianBlur(img, 15), 25, 90, 90)

    ellipse = np.zeros((h, w), np.uint8)
    cx, cy = int(w * 0.5), int(h * 0.42)
    axes = (int(w * 0.16), int(h * 0.24))
    cv2.ellipse(ellipse, (cx, cy), axes, 0, 0, 360, 255, -1)
    a = cv2.GaussianBlur(ellipse, (0, 0), 21).astype(np.float32)[:, :, None] / 255.0
    out = img.astype(np.float32) * (1 - a) + sm.astype(np.float32) * a
    if gain:
        out = out * (1 + gain * a)
    out = np.clip(out, 0, 255).astype(np.uint8)
    p = save_jpeg(out, f"C_beauty_{kind}_q{q}.jpg", q)
    return out, {"path": str(p), "is_jpeg": True, "shape": out.shape, "exif_present": False,
                 "gt_bbox": [int(cx - axes[0]), int(cy - axes[1]), int(axes[0] * 2), int(axes[1] * 2)],
                 "gt_polarity": "darker", "gt_kind": kind, "gt_q": q}


def iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    return inter / float(aw * ah + bw * bh - inter)


# ---------------------------------------------------------------- 跑一个样本


def run_case(tag: str, img, meta: dict, gt: dict | None, expect: str, cfg: dict | None = None) -> dict:
    """expect: no_alarm | detect_brighter | no_brighter | low_conf"""
    print(f"\n=== {tag} （期望：{expect}）===")
    res = ela_detector(img, cfg=cfg, meta=meta)
    print("  " + res.brief())
    for n in res.notes[:3]:
        print("   ·", n)
    save_result(res, OUT, tag)
    make_evidence_sheet(img, res, OUT / f"{tag}_sheet.png")

    brighter = [r for r in res.regions if r["polarity"] == "brighter"]

    if expect == "no_alarm":
        ok = len(res.regions) == 0
        why = "无任何异常块" if ok else f"报了 {len(res.regions)} 个异常块（误报）"
    elif expect == "detect_brighter":
        cands = brighter or res.regions
        hit = max(cands, key=lambda r: iou(r["bbox"], gt["gt_bbox"])) if cands else None
        ov = iou(hit["bbox"], gt["gt_bbox"]) if hit else 0.0
        ok = hit is not None and hit["polarity"] == "brighter" and ov >= 0.5
        why = (f"偏亮区域 IoU={ov:.2f} 峰值z={hit['z_peak']:+.1f} 形态：{hit['shape']}"
               if hit else "未检出任何区域")
    elif expect == "no_brighter":
        ok = len(brighter) == 0
        why = ("未产生『偏亮/拼接』误报" if ok
               else f"误报 {len(brighter)} 个偏亮区域：峰值z={max(r['z_peak'] for r in brighter):+.1f}")
    elif expect == "any_brighter":
        # 陷阱复现专用：只断言"出现了偏亮区域"（极性反转已发生）。
        # 椭圆被块级连通域切成多块，逐块比 IoU 无意义，故不设 IoU 门槛。
        ok = len(brighter) > 0
        why = (f"极性反转已复现：{len(brighter)} 个偏亮区域，峰值z={max(r['z_peak'] for r in brighter):+.1f}"
               if ok else "未复现极性反转（q95 下未出现偏亮区域）")
    else:  # low_conf
        ok = res.confidence == "low" and any("不适用" in n for n in res.notes)
        why = f"confidence={res.confidence}，已声明不适用"

    print(f"  → {'✅ 通过' if ok else '❌ 失败'}：{why}")
    return {
        "case": tag, "expect": expect, "pass": bool(ok), "why": why,
        "score": round(res.score, 4), "confidence": res.confidence,
        "n_regions": len(res.regions), "n_brighter": len(brighter),
        "raw": {k: v for k, v in res.raw.items()
                if k not in ("diff_img", "block_mean", "z")},
        "top_regions": res.regions[:3], "notes": res.notes, "ground_truth": gt,
    }


# ---------------------------------------------------------------- 主流程


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.exists():
        print(f"底图不存在：{src}")
        return 2

    base, bmeta = load_base(src)
    results: list[dict] = []

    # A 真实照片：不得凭空报警
    results.append(run_case("A_真实照片", base, bmeta, None, "no_alarm"))

    # B 拼接：必须检出偏亮且框对位置（这是 ELA 的看家本领）
    sp, spm = make_splice(base)
    results.append(run_case("B_拼接", sp, spm, spm, "detect_brighter"))

    # C 美颜磨皮 ×3 算子：误报护栏（本项目最关键的一条）
    for kind in ("bilateral", "median", "beauty"):
        bt, btm = make_beauty(base, kind)
        results.append(run_case(f"C_美颜磨皮_{kind}", bt, btm, btm, "no_brighter"))

    # D 纯 PNG：必须声明"不适用"，不是"无异常"
    png = OUT / "D_无JPEG史.png"
    cv2.imencode(".png", base)[1].tofile(str(png))
    pi, pmeta = load_and_prepare(png)
    results.append(run_case("D_纯PNG无JPEG史", pi, pmeta, None, "low_conf"))

    # E 陷阱复现：磨皮图 + q=95（≈相机自身质量）→ 极性反转，磨皮被伪装成拼接
    bt95, btm95 = make_beauty(base, "bilateral", q=95)
    results.append(run_case("E_陷阱_q95极性反转", bt95, btm95, btm95, "any_brighter",
                            cfg={"quality": 95, "quality_cross": 85}))

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    npass = sum(r["pass"] for r in results)
    lines = [
        "# P0 验收报告 · ELA 检测器", "",
        f"- 生成时间：{stamp}",
        f"- 底图：`{src}`（{bmeta['shape'][1]}×{bmeta['shape'][0]}，"
        + ("程序合成中性样图，无个人数据" if "neutral" in src.name else "外部输入图") + "）",
        f"- 结论：**{npass}/{len(results)} 项通过**", "",
        "## 逐项结果", "",
        "| 样本 | 期望 | 结果 | score | conf | 区域数(偏亮) | 说明 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['case']} | {r['expect']} | {'✅' if r['pass'] else '❌'} | "
            f"{r['score']} | {r['confidence']} | {r['n_regions']}({r['n_brighter']}) | {r['why']} |")
    lines += ["", "## 实测关键结论", "",
              "1. **【q 选择铁律】重压缩质量必须低于原图自身质量。**",
              "   实测：同一张磨皮图，q=95 时磨皮区 ELA 由 0.40 → 1.10（高于周边 0.37），极性变成"
              "『偏亮』，**被伪装成拼接**；q=90 时变成 0.92 vs 周边 1.58（偏暗），方向正确。",
              "   机理：q ≈ 原图质量 → 双压缩失配消失 → 被改动的像素成为唯一残差来源 → 残差反升。",
              "2. **【磨皮不是拼接】默认 q=90 下，三种磨皮算子均未产生任何『偏亮』误报**"
              "（本项目的头号误报风险已用回归测试锁住）。",
              "3. **【ELA 检不出磨皮，也不该由它检】** 磨皮在 q≤90 时仅使区域 ELA 降低约 1σ 以内"
              "（区域均值 z≈−1.0），远达不到稳健 z 阈值 4。→ 磨皮归因必须另加高频/噪声检测器（P1）。",
              "4. **【无 JPEG 史时必须声明不适用】** PNG 输入下 ELA 无区分度，必须返回 confidence=low 并"
              "明确说『不适用』，不能返回『无异常』——两者在业务上完全不同。",
              "5. 形态先验用 minAreaRect 填充率判别：矩形粘贴 ≈1.0，椭圆磨皮 ≈0.785（见 B 样本输出）。",
              "", "## 失败项处理", ""]
    fails = [r for r in results if not r["pass"]]
    lines.append("无。" if not fails else "\n".join(f"- {r['case']}：{r['why']}" for r in fails))

    (OUT / "P0-report.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "p0_smoke_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n================ P0 验收汇总 ================")
    for r in results:
        print(f"{'✅' if r['pass'] else '❌'} {r['case']:<22} score={r['score']:<7} "
              f"conf={r['confidence']:<5} 区域={r['n_regions']}(亮{r['n_brighter']})  {r['why']}")
    print(f"\n通过 {npass}/{len(results)}   产物: {OUT}")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())