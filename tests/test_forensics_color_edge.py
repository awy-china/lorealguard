# -*- coding: utf-8 -*-
"""`color_edge`（色彩过渡）—— 预注册 v2 §2.2 的落地回归。

**口径（写于测量之前，跑之前就写在这段注释里）**

`color_edge` 的先验是**缝**而不是**边界**：自然物体的色度边界两侧**纹理连续**，拼接缝的
色度跳变两侧**色温统计不同 + 纹理不连续**。所以它**五条同开**才出 region
（强度 / 面积 / ΔE / 纹理比 / 非弃权），缺一条即不报 —— 宁可漏，不冤枉。

本文件断言四组：

  A. 正常路径：跨源拼接标定图（图②）→ **必须出坐标**，`raw` 里 §一 要求的 6 个键一个不少、
     `raw` 里**不许**有 `diff_img`；region 的 ΔE / 纹理比 / 面积必须**真的过闸门**；
  B. 弃权路径：纯色卡 / 极暗图 / 装不下 Sobel 窗的小图 → `status=="unavailable"` + 一行原因，
     **不崩、不 0 除**；
  C. 五条闸门逐条自证伪：把任意**一条**闸门拧死，同一条断言必须变红，且 `raw` 里的
     `n_rejected_*` 要指出**是哪一条**拦下的（"没报"必须可归因，不许一句"没异常"糊过去）；
  D. `_ring_sides` 的两条切分路径：闭合缝 → `loop_in_out`；开口缝 → 退路 `normal_projection`；
     空环带 → `empty`（早退，不碰任何除法）。

**为什么 D 要有**：预注册 §2.2 **没有**规定"缝的两侧"怎么切，切法是落地实现（已登记在
回执 §七）。闭合缝用主轴法向投影会切成"半个内圈 + 半个外圈"，ΔE 与纹理比双双塌掉
（实测投影法 ΔE=0.33 / 纹理比 1.10，环内环外法 ΔE=14.06 / 纹理比 18.81）—— 两条闸门
一条都过不去。这条路径必须被测试钉住，否则后人"顺手简化"回单一投影法时不会有任何提示。

**自证伪**：C 组任一条螺丝拧紧后 A 组的断言必须红（工作单 §一 要求）。
⚠️ 铁律 1/2：断言不符时**只许改协议并留档**，绝不许改检测器阈值。

⚠️ **测试函数名一律 ASCII**（docstring 才写中文）：pytest 的 `tmp_path` 由测试名派生，
中文测试名 ⇒ 中文临时目录 ⇒ `cv2.imread` 在 Windows 上返回 None，而本文件必须走
`samples/calib_lighting_color.py::_jpeg` 那条与生成器逐字节相同的 q92 读回路径。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samples import calib_lighting_color as CAL                            # noqa: E402
from voiceguard import config                                              # noqa: E402
from voiceguard.forensics.color_edge import (                              # noqa: E402
    _ring_sides, _seam_normal, color_edge_detector,
)

CFG = config.COLOR_EDGE_CFG

#: §一 必填 6 项。
RAW_KEYS = {"n_seams", "median_s", "max_seam_de", "texture_ratio_max",
            "area_ratio_max", "reliable"}

#: §一 要求 region 携带的字段（前四个是观察栏的通用契约，后三个是本维度自己的判据量）。
REGION_KEYS = {"bbox", "polarity", "z_peak", "shape", "delta_e", "texture_ratio",
               "area_ratio", "split_mode"}


def _splice_jpeg(tmp_path: Path) -> tuple[np.ndarray, dict]:
    """标定图②（跨源拼接）—— 走**与生成器逐字节相同**的 q92 编解码路径。"""
    img, truth = CAL.make_splice_cross_source()
    return CAL._jpeg(img, tmp_path / "calib_splice_cross_source.jpg"), truth


def _assert_seam_reported(r) -> None:
    """A 与 C 共用的**同一条**断言（C 靠它变红来证伪）。"""
    assert r.status == "ok", f"应该正常出数，却是 {r.status}（{r.reason}）"
    assert r.raw["n_seams"] == len(r.regions) >= 1, "跨源拼接缝必须给出至少一处坐标"
    best = max(r.regions, key=lambda g: float(g["delta_e"]))
    assert float(best["delta_e"]) >= float(CFG["delta_e_min"])
    assert float(best["texture_ratio"]) >= float(CFG["texture_ratio_min"])
    assert float(best["area_ratio"]) >= float(CFG["min_area_ratio"])


# ---------------------------------------------------------------- A 正常路径

def test_cross_source_splice_yields_a_seam_with_all_keys(tmp_path):
    img, truth = _splice_jpeg(tmp_path)
    r = color_edge_detector(img)
    _assert_seam_reported(r)

    assert RAW_KEYS <= set(r.raw), f"缺必填键：{RAW_KEYS - set(r.raw)}"
    assert "diff_img" not in r.raw, "§一 明令：raw 里不许塞差异图"
    assert r.raw["reliable"] is True and isinstance(r.raw["reliable"], bool)
    assert r.raw["median_s"] >= float(CFG["median_s_min"]), "弃权线之上才谈得上出坐标"

    best = max(r.regions, key=lambda g: float(g["delta_e"]))
    assert REGION_KEYS <= set(best), f"region 缺字段：{REGION_KEYS - set(best)}"
    assert best["polarity"] in ("brighter", "darker")
    assert best["shape"] and isinstance(best["shape"], str)
    assert np.isfinite(float(best["z_peak"]))
    # 贴块是**闭环** ⇒ 分侧必须走"环内/环外"那条路（D 组钉住它对数值的决定性）
    assert best["split_mode"] == "loop_in_out", best["split_mode"]

    # 坐标必须与**造图时写死的真值带**实质重叠（不是"随便报了一个框"）
    bx, by, bw, bh = best["bbox"]
    tx, ty, tw, th = truth["bbox"]
    ox = max(0, min(bx + bw, tx + tw) - max(bx, tx))
    oy = max(0, min(by + bh, ty + th) - max(by, ty))
    inter = ox * oy
    assert inter / (bw * bh + tw * th - inter) >= 0.5, f"框位置不符（IoU 太低）bbox={best['bbox']}"


def test_no_seam_means_no_coordinates_but_keeps_global_numbers(tmp_path):
    """正常图（同一来源九宫格）→ 不出坐标，但全图数字必须留着（B/C 档只出测量值）。"""
    grid = CAL._jpeg(CAL.make_grid_same_source(), tmp_path / "grid.jpg")
    r = color_edge_detector(grid)
    assert r.regions == [] and r.raw["n_seams"] == 0
    assert r.raw["max_seam_de"] == 0.0 and r.raw["texture_ratio_max"] == 0.0
    # "为什么没报"可复核：中位梯度、阈值、候选数都在
    assert r.raw["k_sigma"] == CFG["k_sigma"] and r.raw["need_delta_e_min"] == CFG["delta_e_min"]


# ---------------------------------------------------------------- B 弃权路径

@pytest.mark.parametrize("name,img_bgr,reason_head", [
    ("纯色卡", np.full((480, 640, 3), (168, 150, 132), np.uint8), "色度梯度不足"),
    ("极暗图", np.full((480, 640, 3), (3, 3, 4), np.uint8), "色度梯度不足"),
    # 8×8 装得下 3×3 Sobel ⇒ **不该**走"图太小"，而是中位梯度为 0 → 走色度不足那条
    # （探针实测过：这条口子与 lighting 的 `短边//grid < 3` 不是同一条线，别混）。
    ("8×8 小图", np.full((8, 8, 3), (120, 130, 140), np.uint8), "色度梯度不足"),
    ("2×2 小图", np.full((2, 2, 3), (120, 130, 140), np.uint8), "图太小"),
])
def test_unavailable_instead_of_silent_no_anomaly(name, img_bgr, reason_head):
    r = color_edge_detector(img_bgr)
    assert r.status == "unavailable", f"{name} 应弃权，实际 {r.status}"
    assert r.reason and r.reason.startswith(reason_head), f"{name}：{r.reason}"
    assert "\n" not in r.reason, "原因必须是一行"
    assert r.regions == [] and r.score == 0.0 and r.confidence == "low"
    assert r.notes and r.notes[0] == f"不适用（原因：{r.reason}）"
    assert not any(("未出坐标" in n or "正常" in n) for n in r.notes), r.notes
    assert RAW_KEYS <= set(r.raw), f"{name} 弃权时缺键：{RAW_KEYS - set(r.raw)}"
    assert r.raw["reliable"] is False


def test_abstain_path_has_no_division_by_zero():
    """②：0 像素 / 1px 宽的入口都要走弃权，不许抛异常（`_TEX_EPS` 兜底也不许被绕过）。"""
    for shape in ((0, 0, 3), (1, 640, 3), (480, 1, 3), (3, 3, 3)):
        r = color_edge_detector(np.zeros(shape, np.uint8))   # 不抛异常即为通过
        assert r.status == "unavailable", f"{shape} 应弃权，实际 {r.status}"
        assert np.isfinite(r.score)


def test_flat_side_of_a_hard_seam_does_not_divide_by_zero():
    """②：一侧**完全平坦**（Laplacian 方差 = 0）时走 `_TEX_EPS`，不许 0 除、不许崩。"""
    img = np.full((240, 320, 3), 120, np.uint8)
    img[:, 160:] = (120, 96, 168)                 # 一条贯穿上下的色度硬缝，右侧照样平坦
    r = color_edge_detector(img)                  # 不抛异常即为通过
    assert RAW_KEYS <= set(r.raw)
    assert np.isfinite(r.raw["texture_ratio_max"])


# ---------------------------------------------------------------- C 五条闸门逐条自证伪

@pytest.mark.parametrize("over,needle", [
    ({"median_s_min": 1e9}, "色度梯度不足"),        # 非弃权：弃权线本身来自 config
    ({"k_sigma": 1e9}, None),                       # 强度：阈值高到没有像素过线
    ({"min_area_ratio": 0.9}, None),                # 面积：整张图都算细描边
    ({"delta_e_min": 999.0}, "n_rejected_delta_e"),  # ΔE：色温不够不同
    ({"texture_ratio_min": 999.0}, "n_rejected_texture"),   # 纹理比：纹理太连续
])
def test_falsify_any_single_gate_turns_the_assertion_red(tmp_path, over, needle):
    """C：五条闸门拧死任意一条 → A 组的断言必须红，且"是哪条拦的"要能从 raw 读出来。"""
    img, _ = _splice_jpeg(tmp_path)
    _assert_seam_reported(color_edge_detector(img))            # 螺丝没拧之前是绿的

    r = color_edge_detector(img, cfg=over)
    with pytest.raises(AssertionError):
        _assert_seam_reported(r)

    if needle is None:
        # 强度 / 面积两条拦在**候选**阶段：连通域根本没进候选集
        assert r.raw["n_candidates"] == 0, over
    elif needle == "色度梯度不足":
        assert r.status == "unavailable" and r.reason.startswith(needle)
    else:
        # ΔE / 纹理比拦在**逐候选判定**阶段：候选存在、但被明确记账
        assert r.raw["n_candidates"] >= 1, over
        assert r.raw[needle] >= 1, f"{over} 拦下后没有记账：{r.raw[needle]}"


# ---------------------------------------------------------------- D 分侧的两条路径

def _band_of(comp: np.ndarray, ring_px: int = 1) -> tuple[np.ndarray, np.ndarray]:
    import cv2
    kernel = np.ones((2 * ring_px + 1, 2 * ring_px + 1), np.uint8)
    band = (cv2.dilate(comp.astype(np.uint8), kernel) > 0) & (~comp)
    return np.nonzero(band)


def test_ring_sides_closed_loop_uses_in_out():
    """D：闭合缝（贴一块内容 = 一条闭环）→ 补集两域 → `loop_in_out`。"""
    comp = np.zeros((80, 80), bool)
    comp[20:60, 20:60] = True
    comp[21:59, 21:59] = False                     # 只剩 1px 厚的环
    ry, rx = _band_of(comp)
    ys, xs = np.nonzero(comp)
    s1, s2, mode = _ring_sides(comp, ry, rx, _seam_normal(comp),
                               np.array([xs.mean(), ys.mean()], np.float32))
    assert mode == "loop_in_out"
    assert int(s1.sum()) >= 8 and int(s2.sum()) >= 8, "两侧都得有足够像素才谈得上统计"


def test_ring_sides_open_seam_falls_back_to_normal_projection():
    """D：开口缝（从边框伸进图内、末端悬空）→ 补集只有一个域 → 退回主轴法向投影。"""
    comp = np.zeros((80, 80), bool)
    comp[40, 0:30] = True
    ry, rx = _band_of(comp)
    ys, xs = np.nonzero(comp)
    s1, s2, mode = _ring_sides(comp, ry, rx, _seam_normal(comp),
                               np.array([xs.mean(), ys.mean()], np.float32))
    assert mode == "normal_projection", "开口缝的两侧本来就是两个半平面，投影是对的"
    assert int(s1.sum()) >= 8 and int(s2.sum()) >= 8


def test_ring_sides_empty_band_early_exits():
    """D：空环带 → `empty` 早退，**不碰任何除法**（这是 0 除的唯一真入口）。"""
    comp = np.zeros((40, 40), bool)
    comp[10:30, 10:30] = True
    s1, s2, mode = _ring_sides(comp, np.zeros(0, int), np.zeros(0, int),
                               np.array([0.0, 0.0], np.float32), np.array([1.0, 0.0], np.float32))
    assert mode == "empty" and s1.size == 0 and s2.size == 0


def test_ring_sides_of_whatever_it_gets_never_returns_disjoint_sides():
    """D：两条路径都必须给**互补**的两侧（同一像素不许两边都算，也不许两边都不算）。"""
    for comp in (np.zeros((60, 60), bool), np.ones((60, 60), bool)):
        if comp.all():
            comp[0, :] = False                     # 保证补集非空
        ry, rx = _band_of(comp)
        if ry.size == 0:
            continue
        ys, xs = np.nonzero(comp)
        s1, s2, _ = _ring_sides(comp, ry, rx, _seam_normal(comp),
                                np.array([xs.mean(), ys.mean()], np.float32))
        if s1.size == 0:
            continue
        assert not (s1 & s2).any(), "两侧重叠 ⇒ ΔE 会拿一半自己跟自己比"
        assert (s1 | s2).all(), "两侧没覆盖全部环带像素 ⇒ 有像素被悄悄丢了"
