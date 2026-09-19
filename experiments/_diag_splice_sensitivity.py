"""ELA 拼接检出下界扫描 —— 回答一个必须回答的问题：**这套检测器到底在多弱的篡改下会漏？**

为什么要做这个实验（而不是直接把演示样本调到"能检出"）：
    一个只报成功案例的检测器，在答辩现场被问一句"那多弱就检不出？"就会当场失去可信度。
    本项目的主张是"可复算 + 不虚报能力"，所以我们要**主动测出下界并公开它**。
    这个表同时是 P2（误报压力测试集 / 评测协议）的第一块砖。

自变量（两个，因为真实拼接通常两项同时发生）：
    gain    —— 粘贴块亮度增益（1.00 = 不改）
    noise   —— 粘贴块叠加的噪声 σ（模拟"来自不同相机/另一张图"的纹理差异）
应变量：
    n_regions / max z_peak / IoU（与真实粘贴框的定位交并比）

用法：  py.bat experiments/_diag_splice_sensitivity.py
产物：  output/diag/splice_sensitivity.json + .md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                     # 让脚本能直接跑（不必先 pip install -e .）

from voiceguard import config                     # noqa: E402
from voiceguard.forensics import registry         # noqa: E402
from voiceguard.forensics.base import load_and_prepare  # noqa: E402

BASE = ROOT / "samples" / "base_neutral.jpg"
OUT = ROOT / "output" / "diag"

GAINS = [1.00, 1.08, 1.18, 1.35, 1.60, 2.00]
NOISES = [0, 2, 4, 8, 16]
QUALITY = 95

#: 粘贴窗（相对坐标）：足够大以免受 min_area_ratio 干扰，但不覆盖全图
BOX = (0.55, 0.30, 0.30, 0.28)      # x0, y0, w, h  (相对)


def iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def make_sample(gain: float, noise: float, dst: Path) -> list:
    a = np.asarray(Image.open(BASE).convert("RGB")).copy()
    h, w = a.shape[:2]
    x0, y0 = int(w * BOX[0]), int(h * BOX[1])
    bw, bh = int(w * BOX[2]), int(h * BOX[3])
    patch = a[y0:y0 + bh, x0:x0 + bw].astype(np.float32) * gain
    if noise > 0:
        rng = np.random.default_rng(20260919)          # 固定种子 = 可复现
        patch = patch + rng.normal(0, noise, patch.shape)
    a[y0:y0 + bh, x0:x0 + bw] = np.clip(patch, 0, 255).astype(np.uint8)
    Image.fromarray(a).save(dst, "JPEG", quality=QUALITY)
    return [x0, y0, bw, bh]


def run_one(gain: float, noise: float, tmp: Path) -> dict:
    gt = make_sample(gain, noise, tmp)
    img, meta = load_and_prepare(tmp)
    r = registry.run_all(img, meta=meta)[0]
    bright = [g for g in r.regions if g.get("polarity") == "brighter"]
    best = max((iou(g["bbox"], gt) for g in bright), default=0.0)
    top_z = max((g.get("z_peak", 0) for g in r.regions), default=0.0)
    return {"gain": gain, "noise": noise, "score": round(float(r.score), 4),
            "n_regions": len(r.regions), "n_brighter": len(bright),
            "max_z_peak": round(float(top_z), 2), "iou": round(best, 3),
            "hit": bool(bright)}


def main() -> int:
    if not BASE.exists():
        raise SystemExit(f"缺少底图 {BASE}（先跑 samples/make_neutral_base.py）")
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_scan_tmp.jpg"

    rows = [run_one(g, n, tmp) for g in GAINS for n in NOISES]

    lines = ["# ELA 拼接检出下界（实测，可复现）", "",
             f"底图 `samples/base_neutral.jpg`；粘贴窗 {BOX}；存盘 q={QUALITY}；"
             f"ELA 工作点 q={config.ELA_CFG['quality']}，robust_z={config.ELA_CFG['robust_z']}", "",
             "| gain \\ noise σ | " + " | ".join(f"{n}" for n in NOISES) + " |",
             "|---|" + "---|" * len(NOISES)]
    for g in GAINS:
        cells = []
        for n in NOISES:
            r = next(x for x in rows if x["gain"] == g and x["noise"] == n)
            cells.append(f"z={r['max_z_peak']:g}<br>IoU={r['iou']}" + (" ✅" if r["hit"] else " ❌"))
        lines.append(f"| **x{g:g}** | " + " | ".join(cells) + " |")
    lines += ["", "说明：✅=检出偏亮区域；z 为最大峰值稳健 z；IoU 为与真实粘贴框的交并比（多区域取最优）。"]

    hit = [r for r in rows if r["hit"]]
    lines.append("")
    if hit:
        weakest = min(hit, key=lambda r: (r["gain"], r["noise"]))
        lines.append(f"**检出下界（本轮扫描内）**：gain≥{weakest['gain']:g} 且 noise σ≥{weakest['noise']:g} "
                     f"时开始检出（z={weakest['max_z_peak']:g}，IoU={weakest['iou']}）。")
    else:
        lines.append("**本轮扫描全部未检出** —— 说明在合成底图上「仅提亮」不是 ELA 的有效信号，"
                     "必须叠加纹理/噪声差异（真实跨图拼接即属此类）。")

    (OUT / "splice_sensitivity.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "splice_sensitivity.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for r in rows:
        print(f"gain={r['gain']:.2f} noise={r['noise']:>4} score={r['score']:.3f} "
              f"regions={r['n_regions']} brighter={r['n_brighter']} "
              f"z={r['max_z_peak']:>7.2f} IoU={r['iou']:.2f} {'HIT' if r['hit'] else '-'}")
    print(f"\n表：{OUT / 'splice_sensitivity.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())