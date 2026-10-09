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
from .color_edge import color_edge_detector
from .ela import ela_detector
from .lighting import lighting_detector
from .noise import noise_detector

_REGISTRY: dict[str, DetectorFn] = {}

#: **非默认**检测器：只有调用方**显式点名**（`run_all(only=[...])`）才会跑。
#: 为什么需要这个集合（预注册 v2 §3 的 B 档硬约束）：观察栏检测器一旦进入默认遍历，
#: `analyze_content()` 的默认报告就会多出 `observations` 段、`_layers` 的 f1 计数会变成
#: 「已跑（3 个检测器）」⇒ **默认路径报告不再逐字节相同**，`tests/test_detector_isolation.py`
#: 的「默认只跑 ela」断言当场变红。B 档要求"宁可不出、也不许动判定面"，所以默认遍历
#: 保持不变，新维度只能由 A/B 对拍器这类显式调用方打开。
_OPTIONAL: set[str] = set()


def register(fn: DetectorFn, name: str | None = None, default: bool = True) -> None:
    """注册检测器。`default=False` = 观察栏（B 档）：不进默认遍历，须显式点名。"""
    n = name or getattr(fn, "__name__", "").replace("_detector", "")
    if not n:
        raise ValueError("检测器需要名字")
    _REGISTRY[n] = fn
    if default:
        _OPTIONAL.discard(n)
    else:
        _OPTIONAL.add(n)


def get(name: str) -> DetectorFn:
    return _REGISTRY[name]


def names() -> list[str]:
    """**全部**已注册检测器（含非默认项）。Agent 的检测器白名单用这个。"""
    return list(_REGISTRY)


def default_names() -> list[str]:
    """默认遍历跑哪些 —— 不含 `_OPTIONAL`。默认口径 = 改动前的 `names()。"""
    return [n for n in _REGISTRY if n not in _OPTIONAL]


def unregister(name: str) -> None:
    """摘掉一个检测器（测试与热插拔用）。不存在则静默 —— 摘干净比报错重要。"""
    _REGISTRY.pop(name, None)
    _OPTIONAL.discard(name)


def run_all(img: np.ndarray, cfg: dict | None = None, meta: dict | None = None,
            only: Iterable[str] | None = None) -> list[DetectionResult]:
    """逐个跑检测器。**单个检测器出问题不许拖垮整轮**。

    这是"后面要加检测器"的前置条件：新增的 noise / double_jpeg / lighting 里任何一个
    在这张图上抛异常，只该让它自己变成「不适用（原因：…）」，其余检测器照跑、判定照出。

    两种错误必须分开（2026-09-25 定）：
    * **名字没注册**（`:43` 的 KeyError）＝ 调用方写错了名字，是程序员错误 → 保持原样抛，
      吞掉它只会把写错的检测器名变成一条"不适用"，静默地少跑一层物证；
    * **跑的时候抛异常** ＝ 输入/环境问题（这张图这个检测器吃不下）→ 登记不适用 + 继续。
    """
    out = []
    for n in (list(only) if only else default_names()):
        fn = _REGISTRY.get(n)
        if fn is None:
            raise KeyError(f"未注册的检测器: {n}（已注册：{names()}）")
        try:
            out.append(fn(img, cfg, meta))
        except Exception as e:                       # noqa: BLE001 —— 隔离即本函数存在的理由
            out.append(DetectionResult.unavailable(n, f"异常：{type(e).__name__}: {e}"))
    return out


# ---- 内置检测器 ----
register(ela_detector, "ela")
# ⚠️ 以下两个是**预注册 v2 登记的观察栏维度（B 档）**，`default=False`：
#    · **不是**左栏物证（`guard.build_evidence` 只认 ela）
#    · **不是**规则触发项（`rules.yaml` 里不出现 `forensics.lighting` / `forensics.color_edge`）
#    · **不进** tier —— 落地形态就是"给人看的数字与坐标"，显式点名（`only=[...]`）才跑。
#    官方口径用词：`lighting` = 「光影/阴影一致性」、`color_edge` = 「色彩过渡」
#    （`loreal-topic2-details.md:48` / `:50` 点名的两个缺失维度）。
register(lighting_detector, "lighting", default=False)
register(color_edge_detector, "color_edge", default=False)
# `noise`（噪声残差一致性）：2026-10-01 以**观察栏维度**落地（`default=False`，见 `noise.py` 的
# 自述边界与预注册 `D:/_lg_survey/P1-7-PREREG-noise-step-2026-10-01.md`）。它**只作观察**：
# 接进判定会在「拼接后再重编码」的样本上拿误报换漏判（同处实测证伪）。
register(noise_detector, "noise", default=False)
# P1 待接入（仍未实现）：double_jpeg / texture