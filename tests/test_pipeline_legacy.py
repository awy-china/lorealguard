"""P0 遗留骨架 `voiceguard/pipeline.py` 的回归。

它**不是**现行入口（现行是 `guard.py`），但它是 `tests/test_contract.py` 依赖的兼容面，
且在它被删掉之前必须继续可跑 —— 「没人用的代码慢慢腐烂」正是不敢删它的理由本身。

这里的断言分两类，故意混在一起：
  ① 行为断言（能跑、产物长什么样）→ 保证别人 import 它不会炸；
  ② 契约断言（docstring 里必须有 DEPRECATED、必须指向 guard.py）
     → 如果哪天有人把标记删了却留着文件，测试会红。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard import pipeline                                                 # noqa: E402
from voiceguard.core.contract import ContentItem                                # noqa: E402


def test_pipeline_is_marked_deprecated_and_points_to_guard():
    doc = pipeline.__doc__ or ""
    assert "DEPRECATED" in doc
    assert "guard.py" in doc, "遗留文件必须写明现行入口在哪，否则下一个人会照它改"


def test_analyze_forensics_returns_detector_results(base_img, env_meta):
    results = pipeline.analyze_forensics(base_img, env_meta, ["ela"])
    assert results, "至少应跑出 1 个检测器结果"
    assert results[0].name == "ela"


def test_analyze_writes_p0_skeleton_dict(tmp_path):
    img = tmp_path / "t.jpg"
    import cv2
    import numpy as np
    gx, _ = np.meshgrid(np.linspace(0, 1, 64), np.linspace(0, 1, 64))
    cv2.imencode(".jpg", (gx * 255).astype(np.uint8),
                 [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tofile(str(img))

    rep = pipeline.analyze(ContentItem(image_path=str(img)), dets=["ela"],
                           outdir=tmp_path / "o", tag="t")
    assert set(rep) >= {"item", "meta", "detectors", "evidence_files", "todo"}
    assert rep["item"]["shape"], "shape 是 ELA 定位的基础元信息，不能缺"
    assert (tmp_path / "o" / "t_report.json").exists()


def test_analyze_rejects_text_only_item():
    """文案单独送进这条老路必须**显式报错**，不能静默返回空结果。"""
    with pytest.raises(ValueError):
        pipeline.analyze(ContentItem(text="只给了文案，没有图"))