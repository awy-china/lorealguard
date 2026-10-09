# -*- coding: utf-8 -*-
"""打包声明的一致性 —— 让「能装上」这句话可验证，而不是注释里的一句话。

为什么要有这个文件（2026-09-23）
--------------------------------
`pyproject.toml` 第 8-9 行一直写着「version 漂移有测试锁死
（`tests/test_packaging.py::test_version_matches_guard`）」——
**而那个文件根本不存在**，全仓库也没有任何测试碰过 `pyproject.toml`。
于是它声称要防的两件事，两件都真发生了：

  ① `[tool.setuptools] packages` 漏了 `voiceguard.agent` 与 `voiceguard.comments`，
     而 `voiceguard/guard.py:22` 有 `from .comments import evaluate_thread`
     ⇒ **非 editable 安装（wheel / sdist）出来的包一 import 就崩**。
  ② version 已经漂了：`pyproject.toml` 写 `0.3.0`，`voiceguard/guard.py:44` 写 `0.3.0+f4`。

两件事一直没爆，是因为本机 `.venv` 里装的是 **editable**
（`__editable__.lorealguard-0.3.0.pth` 直接指向源码目录，**不走 packages 列表**），
而 README 的「快速开始」全程用 `.venv` 里的解释器、不教 `pip install`。
**潜伏的缺口不是没有缺口** —— 它只是没人走到。

本文件只做**静态一致性**（读 pyproject 与磁盘实际对照），不真构建 wheel：
实测本机 venv 里没有 `wheel` 包（`pip wheel .` 报 `invalid command 'bdist_wheel'`），
真构建要改环境。静态检查抓的正是上面两类缺陷，够用，且秒级、离线、可复算。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
PKG = ROOT / "voiceguard"

sys.path.insert(0, str(ROOT))

from voiceguard.guard import VERSION  # noqa: E402


def _cfg() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _declared_packages() -> list:
    return list(_cfg()["tool"]["setuptools"]["packages"])


def _subpackages_on_disk(pkg_dir: pathlib.Path = PKG) -> set:
    """磁盘上真实存在的子包：`voiceguard/` 下**含 .py 文件**的目录。

    判据用「有没有 .py」而**不是**「有没有 `__init__.py`」——
    `voiceguard/semantic/` 就没有 `__init__.py`（靠 PEP 420 命名空间包工作），
    按 `__init__.py` 判会把它误报成"多余声明"。
    """
    root = pkg_dir.name
    found = {root}
    found |= {f"{root}.{d.name}" for d in pkg_dir.iterdir()
              if d.is_dir() and any(d.glob("*.py"))}
    return found


def _missing_subpackages(declared, on_disk) -> set:
    """被声明漏掉的子包。**抽成纯函数**，是为了让下面那条"检查本身会不会红"的测试
    能喂它一个已知有缺口的输入 —— 否则检查逻辑写错了也没人知道。"""
    return set(on_disk) - set(declared)


# ---------------------------------------------------------------- ① 版本一致

def test_version_matches_guard():
    """`pyproject.toml` 的 version 与 `voiceguard/guard.py` 的 `VERSION` 必须逐字一致。

    （本项目的 version 带 `+f4` 这种 local 段，PEP 440 允许；**正因为不常见，才更容易漂**。）
    """
    got = _cfg()["project"]["version"]
    assert got == VERSION, (
        f"版本漂了：pyproject.toml = {got!r}，voiceguard/guard.py:44 VERSION = {VERSION!r}。"
        f"改一个就必须改另一个（`pip install` 装出来的版本号以 pyproject 为准）。")


# ---------------------------------------------------------------- ② 子包不漏

def test_每个子包都被声明():
    """`packages` 必须覆盖磁盘上每一个子包 —— 漏一个，装出来的包就少一个模块。

    这条正是 `voiceguard.comments` 漏声明（导致 `guard.py:22` 的 import 崩）的守卫。
    """
    missing = _missing_subpackages(_declared_packages(), _subpackages_on_disk())
    assert not missing, (
        f"pyproject.toml 的 [tool.setuptools] packages 漏了：{sorted(missing)}。"
        f"这些子包不会进 wheel/sdist ⇒ 装出来的包 import 时崩。"
        f"（editable 安装看不出来：它直接读源码目录，不走这个列表。）")


def test_漏声明子包会被这个检查抓住():
    """**这条守的是上面那条检查本身。**

    上面那条如果逻辑写反了（比如把差集写成交集、或对空集合恒真），
    真实缺口出现时它照样是绿的 —— 那就白写了。
    这里喂一个**已知漏了一个子包**的输入，要求检查必须报出来。
    """
    assert _missing_subpackages(["voiceguard"], {"voiceguard", "voiceguard.agent"}) == {"voiceguard.agent"}
    assert _missing_subpackages(["voiceguard", "voiceguard.agent"],
                                {"voiceguard", "voiceguard.agent"}) == set()


# ---------------------------------------------------------------- ③ 规则表随包分发

def test_规则表随包分发():
    """`package-data` 的 glob 必须真的兜住 `voiceguard/rules/rules.yaml`。

    rules.yaml 是**判定阈值的唯一住所**（`docs/DEBT.md` 与本文件所在项目的第一铁律）。
    装出来的包若没有它，规则层会**静默失效** —— 不是报错，是判定无声地变了味道。
    """
    globs = _cfg()["tool"]["setuptools"]["package-data"]["voiceguard"]
    matched = set()
    for g in globs:
        matched |= {p.relative_to(PKG).as_posix() for p in PKG.glob(g)}
    assert "rules/rules.yaml" in matched, (
        f"package-data 的 glob {globs} 展开后没兜住 rules/rules.yaml（展开结果：{sorted(matched)}）")


# ---------------------------------------------------------------- ④ 入口指向真函数

def test_命令行入口指向真实函数():
    """`[project.scripts]` 是"装完就有 `lorealguard 图.jpg` 命令"的承诺 ——
    里面的 `模块:函数` 必须真的存在、真的可调用。写错一个字母，
    用户要等到**装完并运行**才发现，那时错误信息只是 `ImportError`。
    """
    scripts = _cfg()["project"]["scripts"]
    assert scripts, "[project.scripts] 是空的 —— 「装完就有命令行」这句承诺没有落点"
    for name, target in scripts.items():
        mod_name, sep, fn_name = target.partition(":")
        assert sep and mod_name and fn_name, f"{name} 的入口 {target!r} 不是 `模块:函数` 形式"
        mod = importlib.import_module(mod_name)
        fn = getattr(mod, fn_name, None)
        assert callable(fn), f"{name} → {target}：{mod_name} 里没有可调用的 {fn_name}"
