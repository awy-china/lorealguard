# -*- coding: utf-8 -*-
"""`lighting`（光影/阴影一致性）—— 预注册 v2 §2.1 的落地回归。

**口径（写于测量之前，跑之前就写在这段注释里）**

`lighting` 自己不下任何真伪结论，它只回答一个问题：**这张图里有没有一块区域的亮度
梯度方向违背了全图主导方向**。所以本文件断言的不是"没报错"，而是四条：

  A. 正常路径：跨源拼接标定图（`samples/calib_lighting_color.py` 的图②）→ **必须出坐标**，
     且 `raw` 里 §一 要求的 10 个键一个不少；`raw` 里**不许**有 `diff_img`（§一 明令）；
  B. 弃权路径：纯色卡 / 极暗图 / 装不下 Sobel 窗的小图 → `status=="unavailable"` 且带一行原因，
     **不崩、不 0 除**，且弃权时 `raw` 仍把本次测量值交出去（"没报"也要能被复核）；
  C. 触发三条同开（§2.1）：有坐标 且 R≥0.35 且 z≥6，缺一条即**不出坐标**（只留全图数字）；
  D. 默认遍历**不含**观察栏检测器（B 档硬约束）—— 它们只能被显式点名。

**自证伪（③，工作单 §一 要求）**：把触发线 `r_consistency_min` 从 0.35 拧到 0.99，
**同一条断言必须变红**。这一条同时钉住两件事：① 断言不是永远绿的装饰品；② 阈值确实是从
`config.LIGHTING_CFG` 读的（若代码里写死 0.35，`cfg=` 覆盖与 monkeypatch 都改不动它）。

⚠️ 铁律 1/2：断言不符时**只许改协议并留档**，绝不许改检测器阈值。

⚠️ **测试函数名一律 ASCII**（docstring 才写中文）：pytest 的 `tmp_path` 目录名由测试名派生，
中文测试名 ⇒ 中文临时目录 ⇒ `cv2.imread` 在 Windows 上直接返回 None（`test_ela.py:121`
记的就是这个坑），而本文件必须走 `samples/calib_lighting_color.py::_jpeg` 那条**与生成器
逐字节相同**的 q92 读回路径（换 `load_and_prepare` 读回就多了一层未知处理，数字不再可比）。
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
from voiceguard.forensics import registry                                  # noqa: E402
from voiceguard.forensics.lighting import lighting_detector                 # noqa: E402

#: §一 必填 10 项。
RAW_KEYS = {"n_blocks", "r_consistency", "theta_main_deg", "n_regions", "median_grad",
            "brighter_z_peak_max", "darker_z_peak_max", "brighter_area_ratio",
            "darker_area_ratio", "reliable"}

#: §一 要求 region 携带的字段。
REGION_KEYS = {"bbox", "polarity", "z_peak", "z_mean", "n_blocks", "area_ratio",
               "shape", "theta_deg"}


def _splice_jpeg(tmp_path: Path) -> tuple[np.ndarray, dict]:
    """标定图②（跨源拼接）—— 走**与生成器逐字节相同**的 q92 编解码路径。"""
    img, truth = CAL.make_splice_cross_source()
    return CAL._jpeg(img, tmp_path / "calib_splice_cross_source.jpg"), truth


def _assert_out_of_coordinates(r) -> None:
    """① 与 ③ 共用的**同一条**断言（③ 靠它变红来证伪）。"""
    assert r.status == "ok", f"应该正常出数，却是 {r.status}（{r.reason}）"
    assert len(r.regions) >= 1, "跨源拼接必须给出至少一处坐标"
    assert r.raw["r_consistency"] >= float(config.LIGHTING_CFG["r_consistency_min"])
    assert max(float(g["z_peak"]) for g in r.regions) >= float(config.LIGHTING_CFG["z_trigger"])


# ---------------------------------------------------------------- ① 正常路径

def test_cross_source_splice_yields_coordinates_with_all_keys(tmp_path):
    """①：图② 两个维度都命中（本文件只管 lighting 这一维）。"""
    img, truth = _splice_jpeg(tmp_path)
    r = lighting_detector(img)
    _assert_out_of_coordinates(r)

    assert RAW_KEYS <= set(r.raw), f"缺必填键：{RAW_KEYS - set(r.raw)}"
    assert "diff_img" not in r.raw, "§一 明令：raw 里不许塞差异图"
    assert r.raw["n_regions"] == len(r.regions)
    assert r.raw["reliable"] is True and isinstance(r.raw["reliable"], bool)
    assert r.raw["n_blocks"] > 0 and r.raw["median_grad"] >= float(
        config.LIGHTING_CFG["median_grad_min"])

    best = max(r.regions, key=lambda g: float(g["z_peak"]))
    assert REGION_KEYS <= set(best), f"region 缺字段：{REGION_KEYS - set(best)}"
    assert best["polarity"] in ("brighter", "darker")
    bx, by, bw, bh = best["bbox"]
    assert all(isinstance(v, int) for v in best["bbox"]) and bw > 0 and bh > 0

    # 坐标必须落在**造图时写死的真值带**里（不是"随便报了一个框"）
    x0, yc_top, bw_band, bh_band = truth["band"]                 # → 40, 265, 360, 50
    band = CAL._band_mask(CAL.W, CAL.H, 40, 400, 290, 25.0, 10.0, truth["seed"])
    cx, cy = bx + bw // 2, by + bh // 2
    assert band[cy, cx], f"报出的中心 ({cx},{cy}) 不在真值带内（带 y≈{yc_top}..{yc_top + bh_band}）"


def test_not_triggered_yields_no_coordinates_but_keeps_global_numbers(tmp_path):
    """C：触发三条同开 —— 把 z 触发线拧高，region 必须清空，但全图数字与候选数必须留着。"""
    img, _ = _splice_jpeg(tmp_path)
    r = lighting_detector(img, cfg={"z_trigger": 99.0})
    assert r.regions == []
    assert r.raw["triggered"] is False
    assert r.raw["n_regions"] == 0
    # "为什么没报"要能被复核：候选数、R、z 峰值都在
    assert r.raw["n_candidates"] >= 1, "候选连通域不该因为没触发就消失"
    assert r.raw["r_consistency"] > 0.0
    assert r.raw["darker_z_peak_max"] == 0.0 and r.raw["brighter_z_peak_max"] == 0.0


# ---------------------------------------------------------------- ② 弃权路径

@pytest.mark.parametrize("name,img_bgr,reason_head", [
    ("纯色卡", np.full((480, 640, 3), (168, 150, 132), np.uint8), "纹理/亮度不足"),
    ("极暗图", np.full((480, 640, 3), (3, 3, 4), np.uint8), "纹理/亮度不足"),
    ("8×8 小图", np.full((8, 8, 3), (120, 130, 140), np.uint8), "图太小"),
    ("2×2 小图", np.full((2, 2, 3), (120, 130, 140), np.uint8), "图太小"),
])
def test_unavailable_instead_of_silent_no_anomaly(name, img_bgr, reason_head):
    """②：弃权 = 显式「不适用 + 原因」，绝不静默缺席、更不输出「无异常」。"""
    r = lighting_detector(img_bgr)
    assert r.status == "unavailable", f"{name} 应弃权，实际 {r.status}"
    assert r.reason and r.reason.startswith(reason_head), f"{name}：{r.reason}"
    assert "\n" not in r.reason, "原因必须是一行"
    assert r.regions == [] and r.score == 0.0 and r.confidence == "low"
    assert r.notes and r.notes[0] == f"不适用（原因：{r.reason}）"
    assert not any(("未触发" in n or "正常" in n) for n in r.notes), r.notes
    # 弃权也要能被复核：必填 10 键一个不少（reliable=False 就是那句"这次不算数"）
    assert RAW_KEYS <= set(r.raw), f"{name} 弃权时缺键：{RAW_KEYS - set(r.raw)}"
    assert r.raw["reliable"] is False


def test_abstain_path_has_no_division_by_zero():
    """②：0 块 / 0 像素这些"分母为零"的入口都要走弃权，不许抛异常。"""
    for shape in ((0, 0, 3), (1, 640, 3), (480, 1, 3), (3, 3, 3)):
        r = lighting_detector(np.zeros(shape, np.uint8))     # 不抛异常即为通过
        assert r.status == "unavailable", f"{shape} 应弃权，实际 {r.status}"
        assert np.isfinite(r.score)


# ---------------------------------------------------------------- ③ 自证伪

def test_falsify_tightening_the_trigger_line_turns_it_red(tmp_path):
    """③：`r_consistency_min` → 0.99，① 的那条断言必须红。

    两个方向一起证：`cfg=` 显式覆盖**改得动**、`config.LIGHTING_CFG` monkeypatch
    **也改得动** —— 后者证明阈值不是写死在代码里的。
    """
    img, _ = _splice_jpeg(tmp_path)
    _assert_out_of_coordinates(lighting_detector(img))          # 螺丝没拧之前是绿的

    with pytest.raises(AssertionError):
        _assert_out_of_coordinates(lighting_detector(img, cfg={"r_consistency_min": 0.99}))


def test_falsify_trigger_line_read_from_config_not_hardcoded(tmp_path, monkeypatch):
    """③（第二向）：改 `config.LIGHTING_CFG` 本体，默认调用路径也必须跟着变。"""
    img, _ = _splice_jpeg(tmp_path)
    _assert_out_of_coordinates(lighting_detector(img))

    monkeypatch.setitem(config.LIGHTING_CFG, "r_consistency_min", 0.99)
    with pytest.raises(AssertionError):
        _assert_out_of_coordinates(lighting_detector(img))
    # 且 raw 里回显的就是被改后的那个值（"配置即口径"必须可复核）
    assert lighting_detector(img).raw["trigger_r_min"] == 0.99


def test_falsify_required_key_set_is_not_vacuous(tmp_path):
    """③（第三向）：把 raw 里一个必填键删掉，`RAW_KEYS <= set(raw)` 必须红。"""
    img, _ = _splice_jpeg(tmp_path)
    raw = dict(lighting_detector(img).raw)
    raw.pop("r_consistency")
    with pytest.raises(AssertionError):
        assert RAW_KEYS <= set(raw), "缺必填键"


# ---------------------------------------------------------------- D 观察栏纪律

def test_observation_detectors_are_not_in_the_default_sweep():
    """D：`lighting` / `color_edge` 是 B 档 —— 只有显式点名才跑，默认遍历仍是 ela 一个。"""
    assert "lighting" in registry.names() and "color_edge" in registry.names()
    assert "lighting" not in registry.default_names()
    assert "color_edge" not in registry.default_names()
    assert registry.default_names() == ["ela"], (
        "默认遍历变了 ⇒ 默认报告会多出 observations 段、F1 计数会变，"
        "冻结样张的判定 sha256 当场失效")


def test_they_only_run_when_explicitly_named(tmp_path):
    """D（对照）：`only=[...]` 点名时两个观察栏检测器都在清单里，且都是三态之一。"""
    from voiceguard.forensics import run_all
    img, _ = _splice_jpeg(tmp_path)
    got = {r.name: r for r in run_all(img, meta=None, only=["ela", "lighting", "color_edge"])}
    assert set(got) == {"ela", "lighting", "color_edge"}
    assert got["lighting"].status == "ok" and got["lighting"].regions
