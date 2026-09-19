# -*- coding: utf-8 -*-
"""守护「一键验收脚本」本身：run.bat 是我们对外的验收入口，它坏了整套证据都作废。

守四件事（都是真踩过的坑）：
1. **纯 ASCII** —— cmd.exe 按 ANSI 解析 .bat，中文会让脚本直接崩（2026-09-19 实测）。
2. **引用的每个文件都真实存在** —— 改名/挪目录后 run.bat 会静默变成半残。
3. **步骤编号自洽** —— 印 `[n/N]` 就必须真有 N 步，不许出现 `[3/4]` 混 `[4/5]`。
4. **必须覆盖提交包** —— 官方必交材料不许落在验收之外。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAT = ROOT / "run.bat"


def _text():
    raw = BAT.read_bytes()
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError as e:
        raise AssertionError(
            f"run.bat 含非 ASCII 字节（cmd.exe 按 ANSI 解析会崩）：偏移 {e.start}，"
            f"坏字节 0x{e.object[e.start]:02x}"
        ) from e


def test_run_bat_is_pure_ascii():
    raw = BAT.read_bytes()
    bad = [(i, b) for i, b in enumerate(raw) if b > 127]
    assert not bad, f"run.bat 含非 ASCII 字节（cmd.exe 会按 ANSI 解析而崩溃）：{bad[:5]}"


def _code_lines():
    """剥掉 `rem` 注释行：注释里提到 cmd.exe 这类系统二进制不算「引用」。"""
    return "\n".join(
        ln for ln in _text().splitlines() if not ln.strip().lower().startswith(("rem ", "::"))
    )


def test_run_bat_referenced_paths_exist():
    refs = set(re.findall(r"[\w\\.]+\.(?:py|json|exe)", _code_lines()))
    # 脚本里以反斜杠写 Windows 相对路径；.json 允许是运行时产物（带 if not exist 兜底）
    missing = []
    for r in refs:
        p = ROOT / r.replace("\\", "/")
        if p.suffix == ".json":
            continue
        if not p.exists():
            missing.append(r)
    assert not missing, f"run.bat 引用了不存在的文件：{missing}"


def test_run_bat_step_numbering_is_self_consistent():
    steps = re.findall(r"\[(\d+)/(\d+)\]", _text())
    assert steps, "run.bat 里找不到任何 [n/N] 步骤标记"
    totals = {n for _, n in steps}
    assert len(totals) == 1, f"步骤总数不一致（半改状态）：{sorted(totals)}"
    total = int(totals.pop())
    assert [int(i) for i, _ in steps] == list(range(1, total + 1)), (
        f"步骤编号不是 1..{total} 连续：{[i for i, _ in steps]}"
    )


def test_run_bat_covers_submission_pack():
    txt = _text()
    assert "make_submission_pack.py" in txt, "官方必交材料（测试数据包）必须进一键验收"
    assert "lorealguard_testset_v1.zip" in txt, "验收脚本必须印出提交包产物路径"