# -*- coding: utf-8 -*-
"""色彩过渡（`color_edge`）—— 预注册 v2 §2.2 的落地。

**先验（为什么这样做）**：自然物体的色度边界两侧**纹理连续**；拼接缝的色度跳变两侧
**色温统计不同 + 纹理不连续**。只看色度跳变会把口红 / 眼影边界全报出来（那正是本系统
最该防的误伤）⇒ **必须加"缝"的先验**：**五条同开**才出 region（强度 / 面积 / ΔE / 纹理比 /
非弃权），缺一条即不报 —— 宁可漏，不冤枉。

⚠️ 落地形态 = **观察栏（B 档）**：不进左栏物证、不进 `rules.yaml`、不进 tier（机制同 `lighting`）。

**数字纪律**：判据 / 阈值一律从 `config.COLOR_EDGE_CFG` 读；文件里只剩公式常数与
可计算性兜底（见 `_TEX_EPS` / `_MIN_PX_PER_SIDE`，都不参与判据）。
"""

from __future__ import annotations

import cv2
import numpy as np

from .. import config
from .base import DetectionResult
from .lighting import shape_hint          # 形态措辞只许有一套（口径共用，避免两处漂移）

NAME = "color_edge"

#: 8bit Lab 的 a/b 偏移（OpenCV: a = a + 128）。公式常数，非可调参数。
_AB_OFFSET = 128.0

#: 纹理比的分母兜底：Laplacian 方差可能为 0（纯色侧），0 除必须避免。
_TEX_EPS = 1e-6

#: 环带单侧至少这么多像素才谈得上"统计"；不够就**跳过该候选**（不报），不算判据放宽。
_MIN_PX_PER_SIDE = 8

#: 自述边界（预注册 §2.3）。**单一来源**：notes 与观察栏共用。
_MEASURES = "色彩过渡：色度跳变线两侧的色温差 (ΔE) 与纹理连续性（Laplacian 方差比）"
_NOT_MEASURES = ("不判真伪：口红/眼影/眼线等真实物体的色度边界两侧纹理也是连续的（本项的"
                 "纹理比判据正是为把它们排除掉而设），但强反光与半透明边界仍可能被记为候选；"
                 "本项只给候选位置与两侧测量值，是否异常由人复核")

#: 极性说明：只取 brighter/darker 两值（预注册 §2.3）。
_POLARITY_NOTE = ("polarity = 跳变线**亮侧**的方向（两侧 L 均值高的一侧=brighter）；"
                  "区域显著性看 z_peak（色度梯度 S 的稳健 z，恒为正）")


def _lab_ab(img_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """→ (L, a, b)。Lab(D65)，只对 a/b 取梯度 —— 亮度跳变归 lighting/ela（预注册 §2.2）。"""
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
    return (lab[:, :, 0].astype(np.float32),
            lab[:, :, 1].astype(np.float32) - _AB_OFFSET,
            lab[:, :, 2].astype(np.float32) - _AB_OFFSET)


def _seam_normal(comp: np.ndarray) -> np.ndarray:
    """连通域主轴的**法向** = 跳变线的法向（PCA 最大特征向量转 90°）。

    ⚠️ 它现在只是 `_ring_sides` 的**退路**（缝是**开口**的、补集只有一个连通域时）。
    预注册 §2.2 未指定切分方法（已在回执 §七 登记）：缝是**细长**的，用主轴法向切分
    两侧，比"上下/左右二分"更稳（斜缝不会被切歪）。
    """
    ys, xs = np.nonzero(comp)
    pts = np.stack([xs, ys], axis=1).astype(np.float32)
    cov = np.cov((pts - pts.mean(axis=0)).T)
    _, vecs = np.linalg.eigh(cov)
    main = vecs[:, -1]                       # 最大特征值对应的方向 = 缝的走向
    return np.array([-main[1], main[0]], np.float32)


def _ring_sides(comp: np.ndarray, ry: np.ndarray, rx: np.ndarray,
                nrm: np.ndarray, mean_xy: np.ndarray) -> tuple[np.ndarray, np.ndarray, str]:
    """把环带像素分成**缝的两侧** → (侧1 掩膜, 侧2 掩膜, 用的是哪种切分)。

    ⚠️ 为什么不能一律用"主轴法向投影"（**标定集 ② 实测抓出来的**）：
    投影分的是**过质心的直线**两侧，而**闭合缝**（贴一块内容 = 一条闭环）的两侧其实是
    "环内 / 环外"—— 投影会把"环上方的外圈"和"环上方的内圈"判进同一侧，于是两侧各自
    混进另一半像素，ΔE 与纹理比双双塌掉。实测（`experiments/_probe_calib2.py`，同一候选线）：
    投影法 ΔE=0.33、纹理比 1.10（五条闸门一条都过不去）；按环内/环外分 ΔE=14.06、纹理比 18.81。
    ⇒ 缝的补集若有两个连通域，就用"环内/环外"；否则（缝**开口**，如一条贯穿到图边的直缝）
    退回主轴法向投影 —— 那种情形两侧本来就是两个半平面，投影是对的。
    """
    if ry.size == 0:
        return np.zeros(0, dtype=bool), np.zeros(0, dtype=bool), "empty"
    n_lab2, lab2 = cv2.connectedComponents((~comp).astype(np.uint8), 8)
    if n_lab2 - 1 >= 2:
        lab_ring = lab2[ry, rx]
        vals, cnts = np.unique(lab_ring, return_counts=True)
        if vals.size >= 2:                   # 环带确实落在两个不同的补集连通域上
            top2 = vals[np.argsort(cnts)[::-1][:2]]     # 取最大的两域（内/外）
            return lab_ring == top2[0], lab_ring == top2[1], "loop_in_out"
    proj = (np.stack([rx, ry], axis=1).astype(np.float32) - mean_xy) @ nrm
    return proj > 0, ~(proj > 0), "normal_projection"


def _raw(cfg: dict, *, n_blocks_img: int, median_s: float, threshold_s: float,
         regions: list, n_candidates: int, n_rej_de: int, n_rej_tex: int, n_ring_bad: int,
         reliable: bool) -> dict:
    """`raw` 的**唯一**构造点 —— 必填 6 键一次给全，弃权路径也走这里。"""
    des = [float(g["delta_e"]) for g in regions]
    tex = [float(g["texture_ratio"]) for g in regions]
    ars = [float(g["area_ratio"]) for g in regions]
    return {
        # ---- 必填 6 项 ----
        "n_seams": len(regions),
        "median_s": round(float(median_s), 4),
        "max_seam_de": round(max(des), 3) if des else 0.0,
        "texture_ratio_max": round(max(tex), 3) if tex else 0.0,
        "area_ratio_max": round(max(ars), 6) if ars else 0.0,
        "reliable": bool(reliable),
        # ---- 诊断量：回答"这次为什么没报"，并让复核者能复算 ----
        "threshold_s": round(float(threshold_s), 4),          # k_sigma × median_s
        "n_candidates": int(n_candidates),                    # 过强度+面积线的连通域数
        "n_rejected_delta_e": int(n_rej_de),                  # 因色温差不足被否
        "n_rejected_texture": int(n_rej_tex),                 # 因纹理连续（判为物体边界）被否
        "n_rejected_ring": int(n_ring_bad),                   # 环带单侧像素不足，无法统计
        "k_sigma": cfg["k_sigma"], "ring_px": cfg["ring_px"],
        "need_delta_e_min": cfg["delta_e_min"],
        "need_texture_ratio_min": cfg["texture_ratio_min"],
        "abstain_median_s_min": cfg["median_s_min"],
        "region_min_area_ratio": cfg["min_area_ratio"],
        "img_px": int(n_blocks_img),
        "polarity_note": _POLARITY_NOTE,
        "measures": _MEASURES, "not_measures": _NOT_MEASURES,
    }


def _abstain(raw: dict, reason: str) -> DetectionResult:
    """弃权 = 显式「不适用 + 原因」（口径同 lighting._abstain）。"""
    one = " ".join(str(reason).split())[:200]
    return DetectionResult(name=NAME, score=0.0, raw=raw, heatmap=None, regions=[],
                           confidence="low", notes=[f"不适用（原因：{one}）", _NOT_MEASURES],
                           status="unavailable", reason=one)


def color_edge_detector(img_bgr: np.ndarray, cfg: dict | None = None,
                        meta: dict | None = None) -> DetectionResult:
    cfg = {**config.COLOR_EDGE_CFG, **(cfg or {})}
    H, W = img_bgr.shape[:2]
    n_px = int(H * W)
    if min(H, W) < 3:
        raw = _raw(cfg, n_blocks_img=n_px, median_s=0.0, threshold_s=0.0, regions=[],
                   n_candidates=0, n_rej_de=0, n_rej_tex=0, n_ring_bad=0, reliable=False)
        return _abstain(raw, f"图太小（{W}×{H}，装不下 3×3 Sobel 窗），无法测色彩过渡")

    Lch, a, b = _lab_ab(img_bgr)
    gax = cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3)
    gay = cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3)
    gbx = cv2.Sobel(b, cv2.CV_32F, 1, 0, ksize=3)
    gby = cv2.Sobel(b, cv2.CV_32F, 0, 1, ksize=3)
    S = np.hypot(np.hypot(gax, gay), np.hypot(gbx, gby))      # 色度跳变强度（只取 a/b）
    median_s = float(np.median(S))
    threshold_s = float(cfg["k_sigma"]) * median_s
    if median_s < float(cfg["median_s_min"]):
        raw = _raw(cfg, n_blocks_img=n_px, median_s=median_s, threshold_s=threshold_s,
                   regions=[], n_candidates=0, n_rej_de=0, n_rej_tex=0, n_ring_bad=0,
                   reliable=False)
        return _abstain(raw, "色度梯度不足（单色/灰底），测不出色彩过渡边界")

    sigma_s = 1.4826 * float(np.median(np.abs(S - median_s))) + _TEX_EPS
    lap = cv2.Laplacian(Lch, cv2.CV_32F, ksize=3)             # 高频能量（纹理连续性的度量）
    ring = int(cfg["ring_px"])
    kernel = np.ones((2 * ring + 1, 2 * ring + 1), np.uint8)

    mask = (S > threshold_s).astype(np.uint8)
    n_lab, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    min_area = float(cfg["min_area_ratio"]) * float(max(n_px, 1))
    regions: list = []
    n_candidates = n_rej_de = n_rej_tex = n_ring_bad = 0
    for i in range(1, n_lab):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < max(min_area, 1.0):          # 面积不足 → 细描边，不报
            continue
        n_candidates += 1
        comp = lab == i                        # 全图布尔掩膜（环带要在全图上膨胀，不能切 bbox）
        ys, xs = np.nonzero(comp)
        nrm = _seam_normal(comp)
        mean_xy = np.array([xs.mean(), ys.mean()], np.float32)
        band = (cv2.dilate(comp.astype(np.uint8), kernel) > 0) & (~comp)
        ry, rx = np.nonzero(band)
        side1, side2, split_mode = _ring_sides(comp, ry, rx, nrm, mean_xy)
        if int(side1.sum()) < _MIN_PX_PER_SIDE or int(side2.sum()) < _MIN_PX_PER_SIDE:
            n_ring_bad += 1                    # 单侧像素太少 → 无法统计，跳过（不报）
            continue
        yp, xp, yn, xn = ry[side1], rx[side1], ry[side2], rx[side2]
        de = float(np.hypot(a[yp, xp].mean() - a[yn, xn].mean(),
                            b[yp, xp].mean() - b[yn, xn].mean()))
        t1 = float(lap[yp, xp].var())
        t2 = float(lap[yn, xn].var())
        # 纹理不连续（预注册：比值 ≥2.0 或 ≤0.5）：取**折叠比** max/min —— 与"或倒数和"等价，
        # 且天然不需要 0 除（_TEX_EPS 兜底）。
        tex_ratio = float(max(t1, t2) / max(min(t1, t2), _TEX_EPS))
        if de < float(cfg["delta_e_min"]):      # 五条之二：色温不够不同
            n_rej_de += 1
            continue
        if tex_ratio < float(cfg["texture_ratio_min"]):   # 五条之三：纹理连续 → 物体边界
            n_rej_tex += 1
            continue
        s_sel = S[ys, xs]
        l1, l2 = float(Lch[yp, xp].mean()), float(Lch[yn, xn].mean())
        regions.append({
            "bbox": [x, y, w, h],
            "z_peak": round(float((float(s_sel.max()) - median_s) / sigma_s), 3),
            "z_mean": round(float((float(s_sel.mean()) - median_s) / sigma_s), 3),
            # n_blocks 沿用 ELA 的字段名（§2.3 同构）—— 本检测器是**像素**级，故此处=像素数
            "n_blocks": int(area),
            "polarity": "brighter" if l1 >= l2 else "darker",
            "area_ratio": round(float(area) / float(max(n_px, 1)), 6),
            "shape": shape_hint(comp[y:y + h, x:x + w]),
            "delta_e": round(de, 3),
            "texture_ratio": round(tex_ratio, 3),
            "l_mean_hi": round(max(l1, l2), 2), "l_mean_lo": round(min(l1, l2), 2),
            # 分侧用的哪种切分（loop_in_out / normal_projection）—— 让复核者知道 ΔE 是"那两侧"的
            "split_mode": split_mode,
        })

    score = (min(1.0, max(float(g["delta_e"]) for g in regions) / float(cfg["delta_e_full"]))
             if regions else 0.0)
    raw = _raw(cfg, n_blocks_img=n_px, median_s=median_s, threshold_s=threshold_s,
               regions=regions, n_candidates=n_candidates, n_rej_de=n_rej_de,
               n_rej_tex=n_rej_tex, n_ring_bad=n_ring_bad, reliable=True)

    notes = [f"测什么：{_MEASURES}；不测什么：{_NOT_MEASURES}",
             f"全图：色度梯度中位 {median_s:.2f}、候选线 {threshold_s:.2f}"
             f"（{cfg['k_sigma']:.0f}×中位），过强度+面积的连通域 {n_candidates} 个；"
             f"被否：色温差不足 {n_rej_de}、纹理连续（判为物体边界）{n_rej_tex}、"
             f"环带像素不足 {n_ring_bad}"]
    if not regions:
        notes.append("未出坐标：五条同开（强度/面积/ΔE/纹理比/非弃权）缺一条即不报。"
                     "注意「未出坐标」≠「色彩过渡正常」——只表示本维度这次没给出可复核的位置。")
    for g in regions[:5]:
        bx, by, bw, bh = g["bbox"]
        notes.append(f"色彩过渡候选：位于 (x={bx}, y={by}, {bw}×{bh})，两侧色温差 ΔE={g['delta_e']:.1f}"
                     f"（判据 ≥{cfg['delta_e_min']:.0f}）、纹理比 {g['texture_ratio']:.2f}"
                     f"（判据 ≥{cfg['texture_ratio_min']:.1f}）、面积占比 {g['area_ratio']:.4f}、"
                     f"亮侧 {g['polarity']}（L {g['l_mean_hi']:.0f} vs {g['l_mean_lo']:.0f}）、"
                     f"分侧方式 {g['split_mode']}，形态：{g['shape']}。"
                     "**仅供人工复核**：本项不进左栏物证、不进分级。")

    heat = np.clip(S / max(threshold_s, _TEX_EPS), 0.0, 1.0).astype(np.float32)
    return DetectionResult(name=NAME, score=float(score), raw=raw, heatmap=heat,
                           regions=regions, confidence="mid", notes=notes)
