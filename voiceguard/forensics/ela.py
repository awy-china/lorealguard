"""检测器一：ELA 误差水平分析（Error Level Analysis）

原理：把图以固定 JPEG 质量重压缩一次再求差。**已按某质量压缩过的区域再压缩残差小**，
从别处贴进来的"原始像素"残差大 → 压缩历史不同的区域会被点亮。

本实现严格照《图像取证算法手册》§1，并落实其最重要的工程结论：
    ELA 是「方向提示器」，不是「判定器」。必须看极性和形态，不能只看绝对值。
        z > +τ（更亮） → 拼接 / 亮度调整嫌疑（真正的伪造方向）
        z < −τ（更暗） → **优先归因美颜磨皮**（正常内容，须降权）
出处：Krawetz, Black Hat USA 2007（ELA 原始提出者，无同行评审论文，方案书勿编造 IEEE 引用）；
     Farid, IEEE SPM 2009, DOI 10.1109/MSP.2008.931079（信号基础）。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .. import config
from .base import DetectionResult, is_platform_recompressed, load_and_prepare

NAME = "ela"


# ------------------------------------------------------------------ 核心算法


def compute_ela(img_bgr: np.ndarray, quality: int = 90) -> np.ndarray:
    """返回 float32 ELA 差值图（未放大），HxW，范围 [0,255]。"""
    ok, buf = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("JPEG 编码失败")
    recomp = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    diff = cv2.absdiff(img_bgr, recomp).astype(np.float32)   # int16 语义，防 uint8 回绕
    return diff.max(axis=2)                                  # 取通道最大 → 对色度篡改更敏感


def ela_block_score(diff: np.ndarray, block: int = 16, robust_z: float = 4.0,
                    min_area_ratio: float = 0.005) -> tuple:
    """差值图 → 块级统计 + 稳健 z + 可疑区域（连通域）。

    稳健统计（median + MAD）是关键：脸上一大片高值会把 mean/std 抬飞，
    用 mean/std 定阈值会导致"越异常越检不出"。
    """
    H, W = diff.shape
    hb, wb = H // block, W // block
    if hb < 2 or wb < 2:            # 图太小，退回 cell 级
        hb, wb = max(hb, 1), max(wb, 1)
    d = diff[: hb * block, : wb * block].reshape(hb, block, wb, block)
    block_mean = d.mean(axis=(1, 3))                     # (hb, wb) 块级 ELA 强度

    med = float(np.median(block_mean))
    mad = float(np.median(np.abs(block_mean - med))) + 1e-6
    sigma_hat = 1.4826 * mad                             # 与正态 std 一致的稳健尺度
    z = (block_mean - med) / sigma_hat                   # 块级稳健 z

    mask = (np.abs(z) > robust_z).astype(np.uint8)       # 双向：亮/暗都算异常
    if mask.any():
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)

    regions, area_bad = [], 0
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area / float(hb * wb) < min_area_ratio:
            continue
        sel = lab[y : y + h, x : x + w] == i          # 只统计**掩膜内**的块
        sub = z[y : y + h, x : x + w]
        vals = sub[sel] if sel.any() else sub
        z_mean, z_peak = float(vals.mean()), float(vals.max())
        pix = (x * block, y * block, w * block, h * block)
        regions.append({
            "bbox": [int(v) for v in pix],
            "z": round(z_peak, 3),          # 兼容字段：阈值判定看峰值
            "z_peak": round(z_peak, 3),
            "z_mean": round(z_mean, 3),     # 区域整体强度（只在掩膜内平均）
            "n_blocks": int(sel.sum()),
            "polarity": "brighter" if z_mean > 0 else "darker",
            "area_ratio": float(area / float(hb * wb)),
            "shape": _shape_hint(diff, pix, block),
        })
        area_bad += area

    regions.sort(key=lambda r: abs(r["z_peak"]), reverse=True)
    cv_ratio = float(block_mean.std() / (block_mean.mean() + 1e-6))
    return block_mean, z, regions, float(area_bad / float(hb * wb)), cv_ratio


def _shape_hint(diff: np.ndarray, bbox, block: int) -> str:
    """形态先验（手册 §1.6.2）：直线/多边形 → 拼接嫌疑；平滑曲线 → 更贴合美颜轮廓。

    美颜 SDK 按人脸掩膜衰减强度，"脸内磨过/脸外没磨"的交界（下颌线、发际线）在数学上
    与拼接边界同构 —— 唯一能区分的就是形态。

    判别用 **minAreaRect 填充率**（实测有效，优于单纯数顶点）：
        矩形粘贴 → 掩膜填满外接旋转矩形，填充率 ≈ 1.0
        椭圆磨皮 → 掩膜是椭圆，填充率 ≈ π/4 ≈ 0.785
    """
    x, y, w, h = bbox
    patch = diff[y : y + h, x : x + w]
    if patch.size == 0:
        return "unknown"

    # Otsu 自适应阈值：区域内部通常是"可疑高/正常低"的双峰
    p8 = np.clip(patch, 0, 255).astype(np.uint8)
    thr, binary = cv2.threshold(p8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = binary if binary.mean() >= 127 * 0.15 else cv2.bitwise_not(binary)
    if mask.mean() < 8:          # 双峰不明显 → 整块都算可疑
        mask = np.full_like(p8, 255)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return "unknown"
    c = max(cnts, key=cv2.contourArea)
    carea = cv2.contourArea(c)
    (_, _), (rw, rh), _ = cv2.minAreaRect(c)
    rect_area = max(rw * rh, 1.0)
    fill = carea / rect_area                                  # 填充率
    peri = cv2.arcLength(c, True) or 1.0
    verts = len(cv2.approxPolyDP(c, 0.02 * peri, True))

    detail = f"填充率{fill:.2f}/顶点{verts}"
    if fill > 0.92 and verts <= 8:
        return f"直线/矩形边界 → 拼接或贴纸嫌疑高（{detail}）"
    if fill > 0.72:
        return f"椭圆或多边形边界 → 局部编辑/美颜掩膜均可能（{detail}）"
    return f"不规则曲线边界 → 更符合人脸轮廓/美颜掩膜（{detail}）"


# ------------------------------------------------------------------ 检测器入口


def ela_detector(img_bgr: np.ndarray, cfg: dict | None = None, meta: dict | None = None) -> DetectionResult:
    cfg = {**config.ELA_CFG, **(cfg or {})}
    meta = meta or {"is_jpeg": False, "shape": img_bgr.shape, "exif_present": False}
    H, W = img_bgr.shape[:2]

    # 小图降块（手册：短边<600 用 block=8）
    block = cfg["block"] if min(H, W) >= 600 else 8

    diff = compute_ela(img_bgr, cfg["quality"])
    block_mean, z, regions, area_ratio, cv_ratio = ela_block_score(
        diff, block=block, robust_z=cfg["robust_z"], min_area_ratio=cfg["min_area_ratio"]
    )

    # ---- 交叉验证 q=75：只在某个 q 下出现的区域大概率是纹理/美颜伪影
    diff_x = compute_ela(img_bgr, cfg["quality_cross"])
    _, _, regions_x, _, _ = ela_block_score(
        diff_x, block=block, robust_z=cfg["robust_z"], min_area_ratio=cfg["min_area_ratio"]
    )
    supported = 0
    for r in regions:
        if any(_iou(r["bbox"], rx["bbox"]) >= 0.3 and rx["polarity"] == r["polarity"] for rx in regions_x):
            r["dual_q"] = True
            supported += 1
        else:
            r["dual_q"] = False
    dual_q_consistency = (supported / len(regions)) if regions else 1.0

    # ---- 极性加权打分（手册 §1.6.1：w_pos=1.0, w_neg=0.33）
    weighted = 0.0
    for r in regions:
        w_ = cfg["w_pos"] if r["polarity"] == "brighter" else cfg["w_neg"]
        if not r["dual_q"]:                 # 双 q 不一致 → 再降一半
            w_ *= 0.5
        weighted += r["area_ratio"] * w_
    score = min(1.0, weighted / cfg["score_full_area"])

    # ---- confidence 三档（手册 §1.6.3 / §1.6.4）
    notes: list[str] = []
    conf = "high"
    platform_rc, signals = is_platform_recompressed(meta)
    no_jpeg_history = not meta.get("is_jpeg", False)
    if cfg["quality"] >= cfg["q_reversal_warn"]:
        notes.append(f"⚠️ 重压缩质量 q={cfg['quality']} ≥ {cfg['q_reversal_warn']}：已实测到"
                     "『磨皮区 ELA 反升、极性变偏亮』的误报（2026-09-18 回归测试），"
                     "建议 q 取原图质量−5~10（默认 90）。")

    if no_jpeg_history:
        # 关键：不能输出"无异常"，那会被误读为"没篡改"。必须声明不适用。
        conf = "low"
        score *= config.LOW_CONF_PENALTY
        notes.append("输入无 JPEG 压缩历史（PNG/截图）→ ELA 不适用，全图残差均匀无区分度；"
                     "建议改看噪声残差与纹理检测器。注意：这是『不适用』，不是『无异常』。")
    elif platform_rc:
        conf = "low"
        score *= config.PLATFORM_PENALTY
        notes.append("疑似平台二次压缩（" + "；".join(signals) + "）→ 原始压缩史已被覆盖，"
                     "ELA 证据力弱，分数已按 0.3 降权。主动声明局限，是可信度的一部分。")
    elif dual_q_consistency < 0.5 and regions:
        conf = "mid"
        notes.append(f"双质量交叉验证一致性仅 {dual_q_consistency:.0%}（q=90 与 q=75 区域不重合）"
                     "→ 可疑区更可能来自纹理/美颜伪影，已降级。")
    if cv_ratio < 0.15 and not no_jpeg_history:
        notes.append(f"全图块级 ELA 离散度很低（CV={cv_ratio:.3f}）→ 图像可能经过全局统一处理"
                     "（整图套了同一滤镜），此时 ELA 敏感性下降，勿据此下结论。")

    for r in regions[:5]:
        x, y, w, h = r["bbox"]
        if r["polarity"] == "brighter":
            notes.append(f"检测到残差高于全图中位的区域：位于 (x={x}, y={y}, {w}×{h})，"
                         f"峰值 z=+{r['z_peak']:.1f}（区域均值 z={r['z_mean']:+.1f}，{r['n_blocks']} 块），"
                         f"形态：{r['shape']}，"
                         f"双质量一致性={'是' if r['dual_q'] else '否'}。符合拼接/亮度调整特征。")
        else:
            notes.append(f"检测到大片低残差区：位于 (x={x}, y={y}, {w}×{h})，"
                         f"峰值 z={r['z_peak']:.1f}（区域均值 z={r['z_mean']:+.1f}，{r['n_blocks']} 块），"
                         f"形态：{r['shape']} → **优先归因美颜磨皮**（正常内容），已按 0.33 权重降权。")
    if not regions:
        if no_jpeg_history:
            # 不适用时不评价：说"未检出异常块"会被顺读成"没篡改"。
            notes.append("（本次输入 ELA 不适用，故不作『未检出异常』的评价。）")
        else:
            notes.append("未检出显著 ELA 异常块。注意：这不等于『未篡改』——平台重压缩/全局滤镜会抹平 ELA 对比。")

    heatmap = _to_unit(block_mean)
    return DetectionResult(
        name=NAME,
        score=float(score),
        raw={
            "diff_img": diff,
            "block_mean": block_mean,
            "z": z,
            "quality": cfg["quality"],
            "quality_cross": cfg["quality_cross"],
            "block": block,
            "robust_z": cfg["robust_z"],
            "median": float(np.median(block_mean)),
            "sigma_hat": float(1.4826 * np.median(np.abs(block_mean - np.median(block_mean)))),
            "cv_ratio": cv_ratio,
            "area_ratio_abs": area_ratio,
            "area_ratio_weighted": weighted,
            "dual_q_consistency": dual_q_consistency,
            "platform_recompressed": platform_rc,
            "platform_signals": signals,
            "jpeg_history": not no_jpeg_history,
            "viz_scale": cfg["viz_scale"],
        },
        heatmap=heatmap,
        regions=regions,
        confidence=conf,
        notes=notes,
    )


# ------------------------------------------------------------------ 小工具


def _to_unit(block_mean: np.ndarray) -> np.ndarray:
    """块级图 → [0,1]（用 99 分位归一，避免个别极大值把整图压暗）。"""
    hi = float(np.percentile(block_mean, 99))
    hi = hi if hi > 1e-6 else float(block_mean.max()) or 1.0
    return np.clip(block_mean / hi, 0, 1).astype(np.float32)


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    return inter / float(aw * ah + bw * bh - inter)


# ------------------------------------------------------------------ CLI

if __name__ == "__main__":
    import sys

    src = sys.argv[1] if len(sys.argv) > 1 else None
    if not src:
        print("用法: python -m voiceguard.forensics.ela <图片路径>")
        raise SystemExit(2)
    img, meta = load_and_prepare(src)
    r = ela_detector(img, meta=meta)
    print(r.brief())
    for n in r.notes:
        print("  ·", n)
    outp = Path(config.OUTPUT_DIR) / "cli"
    from .base import make_evidence_sheet, save_result

    print("证据图:", make_evidence_sheet(img, r, outp / "ela_sheet.png"))
    print("落盘:", save_result(r, outp, "cli"))