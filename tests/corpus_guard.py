# -*- coding: utf-8 -*-
"""交付面语料缺席时的**显式弃权**（`docs/DEBT.md` D-49）。

为什么需要它
------------
`output/`（生成物）、`dist/`（打包产物）与交付区文档（`D:/deliver/compete/`）
按设计**不随公开仓 / 源码包分发** —— `tools/make_source_pack.py` 的 `BANNED_DIRS`
把它们明确排除。但少数几条守卫判的**正是**那些不在场的东西，旧形态是
「读到就崩」（`FileNotFoundError` / `assert path.exists()`）：在别人机器上表现为
「解包跑 pytest = 一堆红」，看起来像代码坏了，其实是**判据的对象不在这台机器上**。

形态（D-49 预登记的修法）
------------------------
  缺**具体**语料 ⇒ `pytest.skip` 并**列出缺哪一件**；语料在场 ⇒ 照旧真跑（照红照绿）。
  即 **skip 只在缺件时发生** —— 这条由 `selftest()` 钉住（缺席必判缺、在场必判不缺），
  它被**并入**既有用例（`tests/test_rebuild_from_scratch.py`），不新开一条：
  新增用例会把收集数 527 → 528，而那串数字已经烙进冻结的 deck / PDF（`docs/DEBT.md` D-34）。

判据写宽会怎样
--------------
把闸写成「只要 `output/` 整个不在就 skip 一整个模块」会**连真红一起吞掉**
（同一个模块里有些用例根本不需要语料，比如 `test_submission_pack` 里
「包内生成器不得含挂钟时间」只读生成器源码）。所以本模块只提供按**具体路径**
判的助手，缺哪一件由每个用例自己点名。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import pytest

ROOT = Path(__file__).resolve().parents[1]
#: 交付区：源文件不在仓库内，只有本机挂载。写法与 `tools/check_number_consistency.py` 同一处常量一致（绝对路径）。
DELIVERY = Path("D:/deliver/compete")


def missing_repo(*relpaths: str) -> list[str]:
    """仓库内相对路径里**不在盘上**的那些（顺序与入参一致；空 = 全在）。"""
    return [r for r in relpaths if not (ROOT / r).exists()]


def missing_delivery(*globs: str) -> list[str]:
    """交付区里**不在盘上**的那些（传相对 glob，如 `loreal-video-script.md`）。"""
    if not DELIVERY.exists():
        return [f"{DELIVERY}（整个交付区）"]
    return [f"{DELIVERY}/{g}" for g in globs if not list(DELIVERY.glob(g))]


def missing_paths(paths: Iterable[Path]) -> list[str]:
    """任意路径里**不在盘上**的那些（给「打包产物」这类非固定路径用）。"""
    return [str(p) for p in paths if not Path(p).exists()]


def _skip(miss: Sequence[str], note: str) -> None:
    reason = "交付面语料不随公开仓/源码包分发，缺: " + "；".join(miss)
    if note:
        reason += f"（{note}）"
    pytest.skip(reason)


def require_repo(*relpaths: str, note: str = "") -> None:
    """缺仓库内某一/几件 ⇒ 弃权并点名；都在 ⇒ 什么也不做（照旧真跑）。"""
    miss = missing_repo(*relpaths)
    if miss:
        _skip(miss, note)


def require_delivery(*globs: str, note: str = "") -> None:
    """缺交付区某一/几件（或整个交付区不在）⇒ 弃权并点名。"""
    miss = missing_delivery(*globs)
    if miss:
        _skip(miss, note)


def require_paths(paths: Iterable[Path], note: str = "") -> None:
    """缺任意路径（如 `dist/` 下的打包产物）⇒ 弃权并点名。"""
    miss = missing_paths(paths)
    if miss:
        _skip(miss, note)


def selftest() -> None:
    """哨兵：证明上面的判据是**存在性**判断，不是「一律判缺 / 一律判有」。

    - 不存在的路径 ⇒ 必须判缺（否则闸形同虚设：缺件也照跑 ⇒ 崩）
    - 仓库里确实存在的件（`README.md`）⇒ 必须判不缺（否则闸会把**真跑**也 skip 掉 ——
      那等于在有语料的机器上把检查静默关掉，是另一种假绿）
    """
    assert missing_repo("__这个目录不存在__") == ["__这个目录不存在__"], \
        "缺席没被判缺 ⇒ 守卫永远 skip，等于没有检查"
    assert missing_repo("README.md") == [], "在场的件被判成缺 ⇒ 守卫会把真跑也 skip 掉"
    assert missing_paths([ROOT / "__不存在__"]) == [str(ROOT / "__不存在__")], \
        "绝对路径判据反了（缺席没判缺）"
    assert missing_paths([ROOT / "README.md"]) == [], "绝对路径判据把在场的判成缺了"
    assert missing_delivery("__不存在__.md") != [], "交付区判据把缺席的判成在场了"