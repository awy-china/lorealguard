# -*- coding: utf-8 -*-
"""把**决赛源码包**导出成一个逐字节可复现的 zip。

    env -u PYTHONPATH .venv/Scripts/python.exe tools/make_source_pack.py

产出：
    dist/source_pack/lorealguard_source_v1.zip
    dist/source_pack/lorealguard_source_v1.zip.sha256

为什么要有它：赛题评分第 1 位要「**可运行、可展示的完整项目形态**」，决赛是**现场跑** ——
源码包就是那个"跑得起来"的载体。而仓库里有三类**不该交出去**的东西：
本机跑测产物（`output/`、`dist/`）、会话过程记录（`cc-dialogue/`）、内部探针与历史备份
（`tools/_probe_*.py`、`_backup_*`）；密钥类（`.env`、`*credentials*`）更是红线。

所以是**白名单 + 两层过滤**：
  · `collect()` 决定"收什么" —— 只走 `WHITELIST_DIRS` / `WHITELIST_FILES`，逐分量过 `_bad_reason`；
  · `assert_clean()` 是**独立的硬断言**，且在 `build()` 里**真的被调用** ——
    守卫 `tests/test_source_pack.py` 会 monkeypatch `collect` 让它漏出一个 `.env`，
    断言必须当场抛。**一条不会红的检查等于没有检查**：这两层各自都能单独证伪。

纪律：
  · **零时钟**：zip 内每个条目的时间戳写死 `1980-01-01 00:00:00`，按相对路径**排序**写入
    ⇒ 同一棵源码树两次打包 sha256 相同（守卫实测）。
  · **零网络 / 零外部依赖**：只用标准库 `zipfile` / `hashlib` / `pathlib` / `os` / `sys`。
  · **不判定、不改被测面**：只读源码树，只写 `dist/source_pack/` 与 stdout。
"""
from __future__ import annotations

import hashlib
import os
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "dist" / "source_pack"
PACK_VERSION = "v1"

#: 顶层白名单：目录（整棵收，内部仍逐分量过滤）与单文件（点名收）。
#: `samples/` 实测仅 1.3 MB（合成样图 + 生成器），可整目录进包 —— 别人要能重造样本。
#: 2026-09-30 增 `experiments/`（F3）：官方数据包 README 的「复现（单行）」跑的就是 `experiments/*.py`
#:   —— 而它此前**不在任何交付包里**（源码包 133 文件里 `experiments/` 零命中）⇒ 评委照抄命令会找不到文件。
#:   三个 bench 脚本的 ROOT 由 `__file__` 推出 ⇒ 解包后从包根就能跑（path 不写死本机）。
WHITELIST_DIRS = ("voiceguard", "demo", "tools", "tests", "samples", "experiments", "docs")
WHITELIST_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "requirements.lock",
    "README.md",
    "CLAUDE.md",
    # 2026-09-29 补两件（理由与守卫见 docs/DEBT.md D-36）：
    # `pytest.ini` 决定判据强度 —— testpaths / addopts / filterwarnings
    #   error::DeprecationWarning:voiceguard.*；`tests/` 在包内而它不在 ⇒ 解包者按
    #   canonical 命令跑会静默降级成"默认配置"，判据强度与 README 承诺不一致。
    # `LICENSE`（Apache-2.0）是交付源码的许可件，缺了等于没给许可。
    "pytest.ini",
    "LICENSE",
    # 2026-10-03 补三个启动器（理由与守卫见 docs/DEBT.md D-48）：
    # 包内 `README.md`（第 104-106 行）与交付面 `RUN-GUIDE.md` 都点名 `guard.bat` /
    #   `run.bat` / `py.bat` 是 Windows 下的入口，而白名单此前只列 .py/.md/.toml
    #   ⇒ 三个文件一个都没进包：解包者照指南点不到文件，包内
    #   `tests/test_acceptance_launcher.py` 七条守卫就地全红（守的正是 run.bat 本体）。
    # 三个都是纯 ASCII（run.bat 那条有守卫钉住），进包不破坏 `assert_clean`。
    "run.bat",
    "guard.bat",
    "py.bat",
)

#: 禁件：目录名（任一分量命中即整棵剪掉）与后缀。
BANNED_DIRS = ("__pycache__", "output", "dist", "cc-dialogue", ".venv")
BANNED_SUFFIXES = (".pyc",)

#: zip 内条目的固定时间戳 ⇒ 打包与"什么时候跑的"无关。
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
#: 固定压缩级别 ⇒ 同一份字节两次打包结果相同。
ZIP_COMPRESSLEVEL = 9


def _bad_reason(component: str) -> str | None:
    """单个路径分量是否禁件；禁则返回人读理由，否则 `None`。

    ⚠️ 顺序要紧：`__pycache__` 与 dunder 文件**同为双下划线包裹**，所以必须**先**判
    `BANNED_DIRS`，再放行 `__init__.py` / `__main__.py` 这类 dunder。
    """
    if component == ".env":
        return "密钥类文件"
    if "credentials" in component.lower():
        return "密钥类文件名"
    if component in BANNED_DIRS:
        return "本机产物 / 会话过程目录 / 字节码目录"
    if component.startswith("_backup"):
        return "历史备份目录"
    if component.endswith(BANNED_SUFFIXES):
        return "字节码缓存"
    # dunder 判定要落在**词干**上：`__init__.py` 的词干是 `__init__`，整名却以 `.py` 收尾
    # —— 拿整名判会把 `__init__.py` / `__main__.py` 误挡（这两件是包能跑起来的前提）。
    stem = Path(component).stem
    if component.startswith("_") and not (stem.startswith("__") and stem.endswith("__")):
        return "内部件（单个下划线前缀）"
    return None


def _path_reason(relname: str) -> str | None:
    """整条相对路径逐分量过 `_bad_reason`；命中则返回「分量: 理由」。"""
    for component in relname.replace("\\", "/").split("/"):
        if not component or component == ".":
            continue
        why = _bad_reason(component)
        if why is not None:
            return f"{component}: {why}"
    return None


def rel(root, path) -> str:
    """仓库内相对路径，**POSIX 风格**（`/` 分隔）—— zip 条目名与跨平台日志都用它。"""
    root_p = Path(root).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = root_p / p
    return p.resolve().relative_to(root_p).as_posix()


def collect(root) -> list[Path]:
    """递归收集白名单内的文件，返回 `Path` 列表（按相对路径排序）。

    两层过滤里的第一层：白名单之外的顶层**根本不走**；白名单之内逐分量剪枝
    （`os.walk` 的 `dirnames[:] = ...` 让废目录连遍历都不发生），最后对整条相对路径
    再过一遍 `_path_reason` —— 冗余是刻意的，剪枝逻辑日后被改坏时它还能兜住。
    """
    root_p = Path(root)
    found: list[Path] = []
    for dirname in WHITELIST_DIRS:
        base = root_p / dirname
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if _bad_reason(d) is None)
            current = Path(dirpath)
            for filename in sorted(filenames):
                if _bad_reason(filename) is not None:
                    continue
                found.append(current / filename)
    for filename in WHITELIST_FILES:
        candidate = root_p / filename
        if candidate.is_file():
            found.append(candidate)
    kept = [p for p in found if _path_reason(rel(root_p, p)) is None]
    return sorted(kept, key=lambda p: rel(root_p, p))


def assert_clean(names) -> None:
    """硬断言：任何一个名字命中禁件就抛 `AssertionError`（**收名字**，不碰文件系统）。

    独立于 `collect()`：它接受的是"你以为要写进包里的东西"，所以哪怕上游过滤坏了、
    或者有人绕过 `collect()` 直接点名加文件，这一道仍然拦得住。
    """
    offenders = [(n, why) for n, why in ((n, _path_reason(n)) for n in names) if why]
    assert not offenders, "源码包混进了不该交出去的东西：\n" + "\n".join(
        f"  - {n}  ({why})" for n, why in offenders
    )


def build(root, out_dir=None) -> Path:
    """造 zip，返回 zip 路径。`assert_clean()` **接线在这里**（不是只在 `main()` 里）。"""
    root_p = Path(root)
    out_p = Path(out_dir) if out_dir is not None else OUT_DIR
    files = collect(root_p)
    names = [rel(root_p, p) for p in files]
    assert_clean(names)                      # ← 唯一闸口：collect 漏了也过不去
    out_p.mkdir(parents=True, exist_ok=True)
    zip_path = out_p / f"lorealguard_source_{PACK_VERSION}.zip"
    entries = sorted(zip(names, files), key=lambda pair: pair[0])
    with zipfile.ZipFile(
        zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=ZIP_COMPRESSLEVEL
    ) as zf:
        for name, path in entries:
            info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0           # 与宿主 OS 解耦
            info.external_attr = 0o644 << 16  # 固定权限位
            zf.writestr(info, path.read_bytes())
    return zip_path


def main() -> int:
    zip_path = build(ROOT, OUT_DIR)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    # 侧车格式固定：`<sha256>  <zip 文件名>`（两个空格），LF 收尾 —— 交给 `sha256sum -c` 能直接用。
    sidecar = zip_path.with_suffix(zip_path.suffix + ".sha256")
    sidecar.write_bytes(f"{digest}  {zip_path.name}\n".encode("utf-8"))
    print(f"OK  {rel(ROOT, zip_path)}")
    print(f"    sha256  = {digest}")
    print(f"    sha16   = {digest[:16]}")
    print(f"    bytes   = {zip_path.stat().st_size:,}")
    print(f"    entries = {len(zipfile.ZipFile(zip_path).namelist())}")
    print(f"    sidecar = {rel(ROOT, sidecar)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
