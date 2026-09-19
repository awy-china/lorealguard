"""检测器注册表：新增检测器不改上层。

用法：
    from voiceguard.forensics import registry
    registry.register(my_detector)          # 任何 (img, cfg, meta) -> DetectionResult
    results = registry.run_all(img, meta=meta)
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

from .base import DetectionResult, DetectorFn
from .ela import ela_detector

_REGISTRY: dict[str, DetectorFn] = {}


def register(fn: DetectorFn, name: str | None = None) -> None:
    n = name or getattr(fn, "__name__", "").replace("_detector", "")
    if not n:
        raise ValueError("检测器需要名字")
    _REGISTRY[n] = fn


def get(name: str) -> DetectorFn:
    return _REGISTRY[name]


def names() -> list[str]:
    return list(_REGISTRY)


def run_all(img: np.ndarray, cfg: dict | None = None, meta: dict | None = None,
            only: Iterable[str] | None = None) -> list[DetectionResult]:
    out = []
    for n in (list(only) if only else names()):
        fn = _REGISTRY.get(n)
        if fn is None:
            raise KeyError(f"未注册的检测器: {n}（已注册：{names()}）")
        out.append(fn(img, cfg, meta))
    return out


# ---- 内置检测器 ----
register(ela_detector, "ela")
# P1 待接入：noise / double_jpeg / lighting / color_edge / texture