"""层1 取证层：纯 CPU、零大模型 —— 本项目的「物证来源」。

对外只需两个名字：registry.run_all / make_evidence_sheet。
"""

from .base import (  # noqa: F401
    DetectionResult,
    draw_regions,
    is_platform_recompressed,
    load_and_prepare,
    make_evidence_sheet,
    render_diff_amplified,
    render_heatmap_overlay,
    save_result,
)
from . import registry  # noqa: F401
from .registry import run_all  # noqa: F401

__all__ = [
    "DetectionResult",
    "load_and_prepare",
    "make_evidence_sheet",
    "render_heatmap_overlay",
    "render_diff_amplified",
    "draw_regions",
    "save_result",
    "is_platform_recompressed",
    "registry",
    "run_all",
]