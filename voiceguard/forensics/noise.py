# -*- coding: utf-8 -*-
"""噪声残差一致性（`noise`）—— 层1 观察栏维度（B 档）：为「同源 / 异源」提供一条可复核的度量。

**先验（为什么这样做）**：跨源拼接贴进来的那块来自**另一台设备 / 另一张图**，它带来的是
「不匹配的噪声场」—— `samples/make_splice_sample.py:7-12` 记录了实测：**纯提亮 gain 1.0→2.0
全部检不出，噪声差异 σ≥4 才稳定检出**。所以判据该落在**噪声残差的台阶**上，而不是残差本身的
幅值：后者会被**同源硬边**（产品瓶矩形边界、高光带）与**重压缩伪影**同样满足。

**这条维度是实测检验过的（照登，含证伪）**：把它接进判定（让 `R-SPLICE-SUSPECT-REVIEW`
以它为佐证）在「拼接**后**再重编码」的样本上同时失去佐证（两侧噪声场被一起同质化）
⇒ 误报换来漏判。预注册与持出集证伪：
`D:/_lg_survey/P1-7-PREREG-noise-step-2026-10-01.md`（§6 期望 → §7 证伪照登）。

⚠️ 落地形态 = **观察栏（B 档）**：`guard.build_observations` 收它，但
  · **不进 `report.evidence`**（左栏物证）· **不进 `rules.yaml` 的任何 `when:`** ⇒ 判定面不变。
  ⚠️ 机制真身**不是**「挡在信号表外」（2026-10-01 round42 亲测更正）：`guard.build_signals`
  （`guard.py:170` 的 `for r in _ran(results)`）**不看** `default`，`forensics.noise.*` 照样写进信号表
  （实测 32 个键，`D:/_lg_survey/round42/probe_signals_r42.txt`）。真正挡住判定的是
  **`rules.yaml` 从不引用 `forensics.noise.*`**（实测引用 0 次；`forensics.ela.*` 16 次）。
  `guard._judging()`（`guard.py:141-151`）另挡两处 —— 兜底 `confidence`（`guard.py:432`）
  与证据表挑拣（`guard.py:447`）；这两处才让「开了观察栏判定逐字节不变」成立。

**数字纪律**：判据 / 阈值**一律**从 `config.NOISE_CFG` 读；文件里只剩公式常数与措辞分档。
"""

from __future__ import annotations

import cv2
import numpy as np

from .. import config
from .base import DetectionResult
from .ela import ela_detector

NAME = "noise"

#: 自述边界（单一来源）：notes、观察栏的 what_it_measures / what_it_does_not 都取自这里。
_MEASURES = ("ELA 偏亮区域**框内**与**框外环带**的高通残差中位幅值之比（log2）："
             "度量「该区是否来自与全图不同的噪声场」")
_NOT_MEASURES = ("不判真伪：只报「框内噪声残差是否显著高于框外」。⚠️ 图像**被再编码**"
                 "（含尺寸不变的降质重压缩）后台阶会消失 —— 实测真拼接再压 q70 后台阶 −0.15，"
                 "与良性原位重压缩 −0.07 同值 ⇒ **无台阶不能排除拼接**；"
                 "本维度只在「未再压缩」区制有分辨力。")

#: 极性与显著性口径：与 ELA / lighting 同族措辞，便于人横读。
_POLARITY_NOTE = ("polarity = 参照区域相对全图的残差方向（brighter / darker），与 ELA 同口径；"
                  "显著性一律看 z_peak（幅值稳健 z，恒为正）")

#: 措辞分档（只影响 `notes` 那句中文与 `hetero` 的命名，**不是**判据 —— 判据是 `step_hetero_min`）。
_W_YES = "台阶明显"
_W_NO = "无台阶"


def _step(hp: np.ndarray, bbox, cfg: dict) -> tuple[float, float, float] | None:
    """框内（内缩）与框外（环带）噪声残差中位幅值 → `(step_log2, inner, outer)`。

    取不到就返回 None（面积不足 / 贴边越界）—— 由调用方登记为「不适用」，绝不当成 0。
    """
    H, W = hp.shape
    x, y, w, h = (int(v) for v in bbox)
    m = np.zeros((H, W), np.uint8)
    m[max(0, y):min(H, y + h), max(0, x):min(W, x + w)] = 1
    if int(m.sum()) == 0:
        return None
    ker = lambda px: np.ones((int(px), int(px)), np.uint8)        # noqa: E731（仅是内核算子）
    inner = cv2.erode(m, ker(cfg["inner_erode_px"])).astype(bool)
    outer = ((cv2.dilate(m, ker(cfg["ring_outer_px"])) > 0)
             & (cv2.dilate(m, ker(cfg["ring_gap_px"])) == 0))
    if int(inner.sum()) < int(cfg["min_px"]) or int(outer.sum()) < int(cfg["min_px"]):
        return None
    ni = float(np.median(np.abs(hp[inner])))
    no = float(np.median(np.abs(hp[outer])))
    return float(np.log2((ni + 1e-3) / (no + 1e-3))), ni, no


def _block_noise(hp: np.ndarray, block: int) -> np.ndarray | None:
    """块级噪声残差（每块的 |高通| 中位）—— 全图分布，作答「这次为什么没报」。

    ⚠️ **不可与 ELA 的块级残差直接互读**（2026-10-01 round42 更正）：本量是**灰度**高通残差的
    块内**中位**（`hp_sigma` 1.2，块恒 `block`=16）；ELA 那个是 **JPEG 重压缩差取通道最大**
    （`ela.py:37`）后的块内**均值**（`ela.py:67`），且短边 < 600 时块降为 8（`ela.py:157`）。
    不同量、不同块网格 ⇒ 量纲与数值都不可比（只有`brighter/darker` 极性词是同族的）。
    """
    H, W = hp.shape
    hb, wb = H // block, W // block
    if hb < 1 or wb < 1:
        return None
    sub = np.abs(hp[: hb * block, : wb * block]).reshape(hb, block, wb, block)
    return np.median(sub.transpose(0, 2, 1, 3).reshape(hb, wb, block * block), axis=2)


def _raw(cfg: dict, *, n_blocks: int, block: int, global_median: float, global_cv: float,
         n_ref_regions: int, ref_bbox, ref_z: float, step: float | None,
         inner: float | None, outer: float | None, hetero: bool, status_ok: bool) -> dict:
    """`raw` 的**唯一**构造点 —— 弃权路径也走这里（「这次没报」也要能被复核）。"""
    live = bool(status_ok and ref_bbox is not None)
    return {
        # ---- 主测量（参照区台阶）----
        "step_log2": None if step is None else round(float(step), 3),
        "inner_noise": None if inner is None else round(float(inner), 4),
        "outer_noise": None if outer is None else round(float(outer), 4),
        "hetero": bool(hetero),                       # step ≥ step_hetero_min
        "n_regions": 1 if live else 0,                # 与 lighting 同口径：出了坐标才算一个区域
        "ref_bbox": list(ref_bbox) if live else None,
        "ref_z_peak": round(float(ref_z), 3) if live else 0.0,
        "reliable": bool(status_ok),
        # ---- 全图分布（诊断：回答「这次为什么没报」）----
        "n_blocks": int(n_blocks),
        "global_noise_median": round(float(global_median), 4),
        "global_noise_cv": round(float(global_cv), 4),
        "n_ref_regions_raw": int(n_ref_regions),
        # ---- 本次工作点（复核者可复算）----
        "block": int(block), "hp_sigma": cfg["hp_sigma"],
        "inner_erode_px": cfg["inner_erode_px"], "ring_gap_px": cfg["ring_gap_px"],
        "ring_outer_px": cfg["ring_outer_px"], "min_px": cfg["min_px"],
        "region_z_min": cfg["region_z_min"], "step_hetero_min": cfg["step_hetero_min"],
        "step_score_full": cfg["step_score_full"],
        # ---- 注解（给人读；不许进 measurements）----
        "polarity_note": _POLARITY_NOTE,
        "measures": _MEASURES, "not_measures": _NOT_MEASURES,
    }


def _abstain(raw: dict, reason: str) -> DetectionResult:
    """弃权 = **显式「不适用 + 原因」**，绝不静默缺席、更不输出「无异常」。"""
    one = " ".join(str(reason).split())[:200]
    return DetectionResult(name=NAME, score=0.0, raw=raw, heatmap=None, regions=[],
                           confidence="low", notes=[f"不适用（原因：{one}）", _NOT_MEASURES],
                           status="unavailable", reason=one)


def noise_detector(img_bgr: np.ndarray, cfg: dict | None = None,
                   meta: dict | None = None) -> DetectionResult:
    cfg = {**config.NOISE_CFG, **(cfg or {})}
    H, W = img_bgr.shape[:2]
    block = int(cfg["block"])
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    hp = gray - cv2.GaussianBlur(gray, (0, 0), float(cfg["hp_sigma"]))
    bm = _block_noise(hp, block)
    if bm is None:
        raw = _raw(cfg, n_blocks=0, block=block, global_median=0.0, global_cv=0.0,
                   n_ref_regions=0, ref_bbox=None, ref_z=0.0, step=None, inner=None,
                   outer=None, hetero=False, status_ok=False)
        return _abstain(raw, f"图太小（短边 {min(H, W)}px，装不下一个 {block}px 的块）")
    n_blocks = int(bm.size)
    g_med = float(np.median(bm))
    g_cv = float(bm.std() / (g_med + 1e-6))
    if min(H, W) < int(cfg["min_side"]):
        raw = _raw(cfg, n_blocks=n_blocks, block=block, global_median=g_med, global_cv=g_cv,
                   n_ref_regions=0, ref_bbox=None, ref_z=0.0, step=None, inner=None,
                   outer=None, hetero=False, status_ok=False)
        return _abstain(raw, f"图太小（短边 {min(H, W)}px < {cfg['min_side']}px），"
                             "框内域与框外环带都取不满")

    # ---- 参照区：ELA 的偏亮区域（**公开 API**，不碰 ela 内部）----
    ela = ela_detector(img_bgr, None, meta)
    z_of = lambda r: float(r.get("z_peak") or r.get("z") or 0.0)      # noqa: E731
    regs = [r for r in (ela.regions or [])
            if str(r.get("polarity")) == "brighter" and z_of(r) >= float(cfg["region_z_min"])]
    if not regs:
        raw = _raw(cfg, n_blocks=n_blocks, block=block, global_median=g_med, global_cv=g_cv,
                   n_ref_regions=0, ref_bbox=None, ref_z=0.0, step=None, inner=None,
                   outer=None, hetero=False, status_ok=False)
        return _abstain(raw, f"ELA 未给出偏亮区域（z_peak ≥ {cfg['region_z_min']}）"
                             "⇒ 本维度没有可比的参照区")

    best = max(regs, key=z_of)
    got = _step(hp, best["bbox"], cfg)
    raw = _raw(cfg, n_blocks=n_blocks, block=block, global_median=g_med, global_cv=g_cv,
               n_ref_regions=len(regs), ref_bbox=best["bbox"], ref_z=z_of(best),
               step=None if got is None else got[0], inner=None if got is None else got[1],
               outer=None if got is None else got[2], hetero=False, status_ok=False)
    if got is None:
        return _abstain(raw, "参照区太小或贴边，内域/框外环带取不到足够像素（≥"
                             f"{cfg['min_px']}px）")

    step, inner, outer = got
    hetero = bool(step >= float(cfg["step_hetero_min"]))
    raw = _raw(cfg, n_blocks=n_blocks, block=block, global_median=g_med, global_cv=g_cv,
               n_ref_regions=len(regs), ref_bbox=best["bbox"], ref_z=z_of(best),
               step=step, inner=inner, outer=outer, hetero=hetero, status_ok=True)
    regions = [{
        "bbox": [int(v) for v in best["bbox"]],
        "polarity": "brighter",
        "z_peak": round(z_of(best), 3),
        "shape": str(best.get("shape", "未知")),
    }]
    score = min(1.0, max(0.0, step) / float(cfg["step_score_full"]))

    word = _W_YES if hetero else _W_NO
    notes = [
        f"测什么：{_MEASURES}；不测什么：{_NOT_MEASURES}",
        f"全图：{n_blocks} 块（{block}px），块级噪声残差中位 {g_med:.3f}、离散度 CV {g_cv:.2f}",
        f"参照区（ELA 偏亮区域 z_peak={z_of(best):.1f}）：位于 "
        f"(x={regions[0]['bbox'][0]}, y={regions[0]['bbox'][1]}, "
        f"{regions[0]['bbox'][2]}×{regions[0]['bbox'][3]})，"
        f"框内噪声残差 {inner:.3f} / 框外环带 {outer:.3f} ⇒ "
        f"台阶 log2 = {step:+.2f}（判据 ≥{cfg['step_hetero_min']}）＝**{word}**。"
        f"**仅供人工复核**：本项不进左栏物证、不进分级；"
        f"「{_W_NO}」只表示本维度这次没测到异源台阶，**不等于**同源。",
    ]
    return DetectionResult(name=NAME, score=float(score), raw=raw, heatmap=None,
                           regions=regions, confidence="mid", notes=notes)