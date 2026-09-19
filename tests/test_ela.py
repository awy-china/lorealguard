"""ELA 检测器回归测试 —— 把 P0 换来的结论**钉死**，防止以后改参数悄悄退化。

跑法： env -u PYTHONPATH .venv/Scripts/python.exe -m pytest -q
（PYTHONPATH 必须清：本机它被 Hermes 全局占用，会污染 import）

这些用例断言的不是"不报错"，而是**方向对不对、误报有没有、边界有没有声明**：
  · 干净图不误报（护栏）
  · 拼接必检出且形态判为直线/矩形（能力）
  · 磨皮不得被误判成拼接（头号误报，护栏）
  · q≥95 极性反转（把默认 q=90 的决策依据锁进测试）
  · PNG 无 JPEG 史必须声明"不适用"而不是"无异常"
  · 同图复制 ELA 看不见（已知边界，防后人误当 bug 修）
"""
from __future__ import annotations

import numpy as np
import pytest

from tests.conftest import COPY_MOVE, PASTE, _write, beatify
from voiceguard.forensics import load_and_prepare, make_evidence_sheet
from voiceguard.forensics.ela import ela_detector


def _run(img, meta, **cfg_over):
    cfg = dict(cfg_over) if cfg_over else None
    return ela_detector(img, cfg=cfg, meta=meta)


# ------------------------------------------------------------------ 护栏


def test_干净样图不产生任何误报(base_img, env_meta):
    r = _run(base_img, env_meta)
    assert r.regions == []
    assert r.confidence == "high"
    assert r.score == 0.0


@pytest.mark.parametrize("op", ["bilateral", "median", "beauty"])
def test_磨皮不得被误判成拼接(tmp_path, base_img, op):
    """头号误报风险：美颜种草图不能被 ELA 打成"拼接伪造"。"""
    img = beatify(base_img, op)
    p = _write(img, tmp_path / f"{op}.jpg")
    i, m = load_and_prepare(p)
    r = _run(i, m)
    brighter = [x for x in r.regions if x["polarity"] == "brighter"]
    assert brighter == [], f"{op} 误报偏亮区域：{brighter}"


# ------------------------------------------------------------------ 能力


def test_拼接被检出为偏亮且形态判为直线矩形(splice):
    img, meta, gt = splice
    r = _run(img, meta)
    brighter = [x for x in r.regions if x["polarity"] == "brighter"]
    assert brighter, "外源像素拼接必须被检出"
    best = max(brighter, key=lambda x: x["z_peak"])
    assert best["z_peak"] >= 4.0
    # 与已知粘贴位置有实质重叠
    x, y, w, h = best["bbox"]
    ox = max(0, min(x + w, gt[0] + gt[2]) - max(x, gt[0]))
    oy = max(0, min(y + h, gt[1] + gt[3]) - max(y, gt[1]))
    inter = ox * oy
    union = w * h + gt[2] * gt[3] - inter
    assert inter / union >= 0.5, f"框位置不符 IoU={inter / union:.2f}"
    assert "直线" in best["shape"] or "矩形" in best["shape"], best["shape"]


# ------------------------------------------------------------------ 已知边界


def test_同图复制ELA看不见(copy_move):
    """ELA 对此无能力 —— 不是 bug。若哪天"检出了"，先怀疑是误报而非改进。"""
    img, meta = copy_move
    assert _run(img, meta).regions == []
    assert COPY_MOVE[2] * COPY_MOVE[3] > 0


def test_PNG无JPEG史必须声明不适用(tmp_path, base_img):
    p = tmp_path / "shot.png"
    import cv2
    cv2.imencode(".png", base_img)[1].tofile(str(p))
    i, m = load_and_prepare(p)
    r = _run(i, m)
    assert r.confidence == "low"
    assert any("不适用" in n for n in r.notes), r.notes
    # 不适用时不得给出任何评价性结论（"未检出异常块"会被顺读成"没篡改"）
    assert not any(("未检出" in n or "未篡改" in n) for n in r.notes if "不适用" not in n), r.notes


# ------------------------------------------------------------------ 配置决策锚


def test_q95极性反转_锁住默认q90的依据(tmp_path, base_img):
    """q≈原图自身质量时，磨皮区残差反升、极性变"偏亮"→ 会被误判成拼接。

    这条测试把"默认 q=90"的原因固化：若有人把默认值改成 95，上面那条
    磨皮护栏就会红。
    """
    img = beatify(base_img, "beauty")
    p95 = _write(img, tmp_path / "b95.jpg", q=95)
    i95, m95 = load_and_prepare(p95)
    r95 = _run(i95, m95, quality=95, quality_cross=85)
    assert [x for x in r95.regions if x["polarity"] == "brighter"], "q=95 下反转未复现（样本或实现已变）"
    assert r95.confidence != "high" or any("⚠️" in n for n in r95.notes), "q≥95 应主动告警"


# ------------------------------------------------------------------ 接口 / 产物


def test_区域指标三口径齐全(splice):
    img, meta, _ = splice
    r = _run(img, meta)
    for reg in r.regions:
        assert {"z_peak", "z_mean", "n_blocks", "polarity", "shape", "bbox"} <= set(reg)
        assert reg["n_blocks"] >= 1
        assert np.isfinite(reg["z_peak"]) and np.isfinite(reg["z_mean"])


def test_中文路径与中文标注证据图(tmp_path, splice):
    """cv2 读中文路径会失败、画中文会变 ??? —— 两条都必须绕开。"""
    img, meta, _ = splice
    p = tmp_path / "中文目录" / "证据图.png"
    out = make_evidence_sheet(img, _run(img, meta), p, title="测试")
    assert p.is_file() and p.stat().st_size > 10_000
    from voiceguard.forensics.base import _cjk_font
    assert _cjk_font(24) is not None, "缺少中文字体，标注会退化成 ???"