# -*- coding: utf-8 -*-
"""光影/阴影一致性（`lighting`）—— 预注册 v2 §2.1 的落地。

**先验（为什么这样做）**：真实单次拍摄的光照方向在全图**一致**；跨源拼接 / 贴块 / 局部重打光
会让局部区域的亮度梯度方向**违背全图主导方向**。判据落在「**违背一致性**」上，而不是
「亮不亮」—— 后者会被美颜 / 滤镜骗到（预注册 §2.1 原文）。

⚠️ 落地形态 = **观察栏（B 档）**：`guard.build_observations` 收它，但
  · **不进 `report.evidence`**（左栏物证）· **不进 `rules.yaml` 的任何 `when:`** ⇒ 判定面不变。
  机制而非自觉：规则表只引用 `forensics.ela.*` / `text.*` / `review.*` / `provenance.*` /
  `semantic.*`，`forensics.lighting.*` 从未出现 ⇒ 规则引擎连"看得见"它都做不到。

**数字纪律**：所有判据 / 阈值**一律**从 `config.LIGHTING_CFG` 读。文件里只剩两类数字：
①数学公式内部常数（圆周均值的 0.5/2、稳健 z 的 1e-6 兜底、归一化的 0/1）；
②形态措辞的分档（见 `_FILL_*`，只影响 `shape` 文案，不影响任何判据）。
"""

from __future__ import annotations

import cv2
import numpy as np

from .. import config
from .base import DetectionResult

NAME = "lighting"

#: 亮度权重（预注册 §2.1 写死："与 ELA 同源，不引入新色彩空间"）——公式常数，非可调参数。
_L_B, _L_G, _L_R = 0.114, 0.587, 0.299

#: 形态措辞分档（与 `ela._shape_hint` 同族口径）：只决定 `shape` 那行中文怎么写，
#: 不参与任何判据；写在这里是为了本文件里不出现孤立数字。
_FILL_LINE, _FILL_ELLIPSE, _APPROX_EPS = 0.92, 0.72, 0.02

#: 自述边界（预注册 §2.3：`notes` 必须写明测什么、不测什么）。
#: **单一来源**：notes、观察栏的 what_it_measures / what_it_does_not 都取自这里，各处不另写。
_MEASURES = "全图光照方向一致性：块级亮度梯度方向 vs 全图圆均值主导方向"
_NOT_MEASURES = ("不判真伪：单光源布光、强反光、局部补光、抠图后重打光都能让局部方向偏离；"
                 "本项只给「与全图不一致」的位置与数字，是否异常由人复核")

#: 极性说明：只取 brighter/darker 两个合法值（预注册 §2.3），且**不是**残差符号。
_POLARITY_NOTE = ("polarity = 该区域平均亮度相对全图中位亮度的方向（区域偏亮=brighter / 偏暗=darker）；"
                  "显著性一律看 z_peak（幅值稳健 z，恒为正）")


def _luminance(img_bgr: np.ndarray) -> np.ndarray:
    """BGR → L = 0.299R + 0.587G + 0.114B（预注册 §2.1，与 ELA 同源）。"""
    b = img_bgr[:, :, 0].astype(np.float32)
    g = img_bgr[:, :, 1].astype(np.float32)
    r = img_bgr[:, :, 2].astype(np.float32)
    return _L_R * r + _L_G * g + _L_B * b


def _block_dirs(lum: np.ndarray, block: int, patch: int) -> tuple[np.ndarray, np.ndarray]:
    """逐块 Sobel 梯度向量和 → (θ, m)。

    θ ∈ [0, π)（**方向**是轴性的，所以后面用倍角圆统计）；m = 块内梯度向量和的模 / 像素数
    —— 它衡量的是"这块有没有**一致的**方向性光照梯度"，而不是"这块亮不亮"。
    块内取中心 patch×patch（patch ≥ 块尺寸时取整块，绝不跨块取，否则方向会被邻居污染）。
    """
    H, W = lum.shape
    hb, wb = H // block, W // block
    p = min(int(patch), block)
    pad = (block - p) // 2
    ang = np.zeros((hb, wb), np.float32)
    mag = np.zeros((hb, wb), np.float32)
    for i in range(hb):
        y0 = i * block + pad
        for j in range(wb):
            x0 = j * block + pad
            pa = lum[y0:y0 + p, x0:x0 + p]
            gx = cv2.Sobel(pa, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(pa, cv2.CV_32F, 0, 1, ksize=3)
            sx, sy = float(gx.sum()), float(gy.sum())
            mag[i, j] = float(np.hypot(sx, sy)) / float(pa.size)
            ang[i, j] = float(np.arctan2(sy, sx)) % np.pi
    return ang, mag


def shape_hint(mask: np.ndarray) -> str:
    """连通域形态提示（三种措辞与 `ela._shape_hint` 同族，便于人读时横向对比）。

    为什么另写一份：`ela._shape_hint(diff, bbox, block)` 的入参语义绑在 ELA 差值图上
    （它自己切片、按块判填充率），而这里的入参**已经是块级连通域掩膜**。把 ELA 的内部约定
    搬过来只会让两处口径互相牵制，且 `ela.py` 本轮一行都不许动。

    **公开名字**：`color_edge` 也 import 这一份 —— 形态措辞只许有一套，否则两处会漂移。
    """
    m = ((np.asarray(mask) > 0).astype(np.uint8)) * 255
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return "未知"
    c = max(cnts, key=cv2.contourArea)
    (_, _), (rw, rh), _ = cv2.minAreaRect(c)
    fill = float(cv2.contourArea(c)) / max(float(rw * rh), 1.0)
    peri = float(cv2.arcLength(c, True)) or 1.0
    verts = int(len(cv2.approxPolyDP(c, _APPROX_EPS * peri, True)))
    detail = f"填充率 {fill:.2f}／顶点 {verts}"
    if fill > _FILL_LINE and verts <= 8:
        return f"直线/矩形边界（{detail}）"
    if fill > _FILL_ELLIPSE:
        return f"椭圆或多边形边界（{detail}）"
    return f"不规则曲线边界（{detail}）"


def _raw(cfg: dict, *, n_blocks: int, block: int, median_grad: float, r_cons: float,
         theta_main_deg: float, n_candidates: int, regions: list, triggered: bool,
         reliable: bool) -> dict:
    """`raw` 的**唯一**构造点 —— 预注册 §一 要求的 10 个键在这里一次给全。

    弃权路径也走这里（`reliable=False`、`n_regions=0`）：**"这次没报"也要能被复核**，
    而不是留一片空白让人以为"没异常"。
    """
    zs = [float(g["z_peak"]) for g in regions if g["polarity"] == "brighter"]
    zd = [float(g["z_peak"]) for g in regions if g["polarity"] == "darker"]
    area_b = sum(float(g["area_ratio"]) for g in regions if g["polarity"] == "brighter")
    area_d = sum(float(g["area_ratio"]) for g in regions if g["polarity"] == "darker")
    return {
        # ---- §一 必填 10 项 ----
        "n_blocks": int(n_blocks),
        "r_consistency": round(float(r_cons), 4),
        "theta_main_deg": round(float(theta_main_deg), 2),
        "n_regions": len(regions),
        "median_grad": round(float(median_grad), 4),
        "brighter_z_peak_max": round(max(zs), 3) if zs else 0.0,
        "darker_z_peak_max": round(max(zd), 3) if zd else 0.0,
        "brighter_area_ratio": round(float(area_b), 6),
        "darker_area_ratio": round(float(area_d), 6),
        "reliable": bool(reliable),
        # ---- 以下为诊断量：回答"这次为什么没报"，并让复核者能复算 ----
        "n_candidates": int(n_candidates),      # 过 Δθ+提取线 的连通域数（**未过触发线**）
        "triggered": bool(triggered),
        "block": int(block), "grid": cfg["grid"], "patch": cfg["patch"],
        "collect_theta_orth_deg": cfg["theta_orth_deg"], "collect_z_extract": cfg["z_extract"],
        "trigger_z_peak_min": cfg["z_trigger"], "trigger_r_min": cfg["r_consistency_min"],
        "abstain_median_grad_min": cfg["median_grad_min"],
        "region_min_area_ratio": cfg["min_area_ratio"],
        "polarity_note": _POLARITY_NOTE,
        "measures": _MEASURES, "not_measures": _NOT_MEASURES,
    }


def _abstain(raw: dict, reason: str) -> DetectionResult:
    """弃权 = **显式「不适用 + 原因」**，绝不静默缺席、更不输出「无异常」。

    与 `DetectionResult.unavailable` 同一口径（score=0 / confidence=low / notes 首行写明原因），
    多出来的只有 `raw`：弃权时也把本次测量值交出去。
    """
    one = " ".join(str(reason).split())[:200]
    return DetectionResult(name=NAME, score=0.0, raw=raw, heatmap=None, regions=[],
                           confidence="low", notes=[f"不适用（原因：{one}）", _NOT_MEASURES],
                           status="unavailable", reason=one)


def lighting_detector(img_bgr: np.ndarray, cfg: dict | None = None,
                      meta: dict | None = None) -> DetectionResult:
    cfg = {**config.LIGHTING_CFG, **(cfg or {})}
    H, W = img_bgr.shape[:2]
    block = int(min(H, W) // int(cfg["grid"]))
    if block < 3:
        # 短边装不下一个能估方向的 Sobel 窗口：缩块只会让方向变成噪声，宁可不报。
        raw = _raw(cfg, n_blocks=0, block=block, median_grad=0.0, r_cons=0.0,
                   theta_main_deg=0.0, n_candidates=0, regions=[], triggered=False,
                   reliable=False)
        return _abstain(raw, f"图太小（短边 {min(H, W)}px，块内装不下 ≥3px 的方向估计窗），"
                             "无法估计光照方向")

    lum = _luminance(img_bgr)
    ang, mag = _block_dirs(lum, block, int(cfg["patch"]))
    n_blocks = int(mag.size)
    median_grad = float(np.median(mag))
    if median_grad < cfg["median_grad_min"]:
        # 过曝/过暗/无纹理：方向估计的分母太小，测出来的"方向"是噪声。
        raw = _raw(cfg, n_blocks=n_blocks, block=block, median_grad=median_grad, r_cons=0.0,
                   theta_main_deg=0.0, n_candidates=0, regions=[], triggered=False,
                   reliable=False)
        return _abstain(raw, "纹理/亮度不足，无法估计光照方向")

    # ---- 全图主导方向（圆统计：方向是 π 周期的轴量，所以用倍角 2θ）
    c2 = float(np.mean(np.cos(2.0 * ang)))
    s2 = float(np.mean(np.sin(2.0 * ang)))
    r_cons = float(np.hypot(c2, s2))                     # R ∈ [0,1]：1=全图方向完全一致
    theta_main = 0.5 * float(np.arctan2(s2, c2)) % np.pi

    # ---- 局部违背：Δθ（最小圆周夹角，∈[0, π/2]）× 幅值稳健 z（沿用 ELA 的 z 纪律）
    dev = np.abs(((ang - theta_main + np.pi / 2.0) % np.pi) - np.pi / 2.0)
    dev_deg = np.degrees(dev)
    med_m = float(np.median(mag))
    sigma = 1.4826 * float(np.median(np.abs(mag - med_m))) + 1e-6
    z = (mag - med_m) / sigma
    mask = ((dev_deg > cfg["theta_orth_deg"]) & (z >= cfg["z_extract"])).astype(np.uint8)

    n_lab, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    min_area = float(cfg["min_area_ratio"]) * float(max(n_blocks, 1))
    l_med = float(np.median(lum))          # 极性判定的参照：全图中位亮度（只算一次）
    regions: list = []
    n_candidates = 0
    for i in range(1, n_lab):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < max(min_area, 1.0):                     # 面积占比不足 → 细描边，不报
            continue
        n_candidates += 1
        sel = lab[y:y + h, x:x + w] == i
        sub_z = z[y:y + h, x:x + w][sel]
        sub_l = lum[y * block:(y + h) * block, x * block:(x + w) * block]
        sub_ang = ang[y:y + h, x:x + w][sel]
        l_mean = float(sub_l.mean())
        th = 0.5 * float(np.arctan2(np.mean(np.sin(2.0 * sub_ang)),
                                    np.mean(np.cos(2.0 * sub_ang)))) % np.pi
        regions.append({
            "bbox": [x * block, y * block, w * block, h * block],   # 像素坐标（与 ELA 同构）
            "z_peak": round(float(sub_z.max()), 3),
            "z_mean": round(float(sub_z.mean()), 3),
            "n_blocks": int(sel.sum()),
            "polarity": "brighter" if l_mean >= l_med else "darker",
            "area_ratio": round(float(area) / float(max(n_blocks, 1)), 6),
            "shape": shape_hint(sel),
            "theta_deg": round(float(np.degrees(th)), 2),
            "l_mean": round(l_mean, 2),                            # 极性判定的原始依据
        })

    # ---- 触发（预注册 §2.1 三条同开）：有坐标 且 全图本一致 且 局部显著
    z_peak_max = max((float(g["z_peak"]) for g in regions), default=0.0)
    triggered = (len(regions) >= 1 and r_cons >= float(cfg["r_consistency_min"])
                 and z_peak_max >= float(cfg["z_trigger"]))
    if not triggered:
        # 未触发 = 预注册 §3 的 C 档：**只出全图测量值，不出 region**（有数字、有坐标才叫 B 档）。
        regions = []
    score = min(1.0, z_peak_max / float(cfg["z_score_full"])) if regions else 0.0

    raw = _raw(cfg, n_blocks=n_blocks, block=block, median_grad=median_grad, r_cons=r_cons,
               theta_main_deg=float(np.degrees(theta_main)), n_candidates=n_candidates,
               regions=regions, triggered=triggered, reliable=True)

    notes = [f"测什么：{_MEASURES}；不测什么：{_NOT_MEASURES}",
             f"全图：{n_blocks} 块，主导光照方向 θ̄={np.degrees(theta_main):.1f}°，"
             f"方向一致性 R={r_cons:.2f}（≥{cfg['r_consistency_min']} 才可能触发），"
             f"块级梯度中位 {median_grad:.2f}"]
    if not triggered:
        notes.append(f"未触发（预注册 §2.1 三条同开）：过线连通域 {n_candidates} 个、"
                     f"局部 z 峰值 {z_peak_max:.1f}（需 ≥{cfg['z_trigger']}）"
                     f"→ 按 B 档只出全图测量值，**不出坐标**；"
                     "注意「未触发」≠「光影正常」，只表示本维度这次没给出可复核的位置。")
    for g in regions[:5]:
        x, y, w, h = g["bbox"]
        dth = float(np.degrees(abs(((np.radians(g["theta_deg"]) - theta_main + np.pi / 2.0)
                                    % np.pi) - np.pi / 2.0)))
        notes.append(f"光影不一致区域：位于 (x={x}, y={y}, {w}×{h})，"
                     f"该区方向 θ={g['theta_deg']:.1f}°、偏离主导方向 Δθ={dth:.1f}°"
                     f"（判据 >{cfg['theta_orth_deg']:.0f}°），"
                     f"z 峰值 {g['z_peak']:.1f}（均值 {g['z_mean']:.1f}，{g['n_blocks']} 块，"
                     f"{g['polarity']}），形态：{g['shape']}。"
                     "**仅供人工复核**：本项不进左栏物证、不进分级。")

    heat = np.clip(np.abs(z) / float(cfg["z_score_full"]), 0.0, 1.0).astype(np.float32)
    return DetectionResult(name=NAME, score=float(score), raw=raw, heatmap=heat,
                           regions=regions, confidence="mid", notes=notes)
