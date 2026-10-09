# -*- coding: utf-8 -*-
"""守护「一键验收脚本」本身：run.bat 是我们对外的验收入口，它坏了整套证据都作废。

守六件事（都是真踩过的坑）：
1. **纯 ASCII** —— cmd.exe 按 ANSI 解析 .bat，中文会让脚本直接崩（2026-09-19 实测）。
2. **引用的每个文件都真实存在** —— 改名/挪目录后 run.bat 会静默变成半残。
3. **步骤编号自洽** —— 印 `[n/N]` 就必须真有 N 步，不许出现 `[3/4]` 混 `[4/5]`。
4. **必须覆盖提交包** —— 官方必交材料不许落在验收之外。
5. **退出码必须真的携带成败**（2026-09-24 加）—— 见下。
6. **每个步骤的失败都要记进 RC** —— 同上。

第 5 条的来由：run.bat 原先最后一条命令是 `pause`，全脚本没有 `exit /b`。
cmd.exe 里 `%ERRORLEVEL%` 取的是**最后一条命令**的返回码，也就是 `pause` 的（恒 0）。
所以「`RUNBAT_EXIT=0` → 8 步全绿」这句写在交付文档里的话，**无论 8 步炸成什么样都成立** ——
它不是测量，是一条不会红的检查。实测：让第 1 步 `sys.exit(7)`、末尾仍是 `pause`，
调用侧拿到的还是 `0`（探针 `output/_probe_pause_exit.bat`）。

修法就是下面这两条守的东西：`RC` 累积第一个失败步骤号 + 末尾 `exit /b %RC%`。
其中 `test_run_bat_tail_propagates_failure` 是**真跑**（把 run.bat 自己的结尾几行原文抠出来
喂给 cmd.exe），不是读文本 —— 删掉 `exit /b %RC%` 或把 `pause` 挪到它后面，它必红。

还有一层（2026-09-24 同日补）：那句话引用的 `RUNBAT_EXIT=<n>` 这个 token，当时**全仓库都不存在**
—— 交付报告引用了没人打印的东西，读者无从核对，这就是「引用支永不判红」。
所以末段现在真的印 `echo RUNBAT_EXIT=%RC%`，上面那条测试连它一起断言：
删掉那行 `echo`，测试同样必红。
"""

import os
import re
import subprocess
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
    # ⚠️ `.venv\Scripts\python.exe` 是**环境生成物**，不是仓库文件 —— 源码包按设计
    #    不含 `.venv`（`make_source_pack.BANNED_DIRS`），所以这条守卫在**解包副本**里
    #    也要成立（2026-10-03 D-48：不改这里，评委解包跑测试必红）。按前缀跳过，
    #    守卫仍然钉「run.bat 引用的**仓库文件**都真实存在」。
    ENV_GENERATED = (".venv\\", ".venv/")
    missing = []
    for r in refs:
        if r.startswith(ENV_GENERATED):
            continue
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


def test_run_bat_has_no_arrow_echoes():
    """`->` 在 .bat 里是**重定向**，不是箭头 —— 实测踩过：
    `echo [6/7] ... detect -> verdict -> advice -> report` 被 cmd.exe 解释成
    「把串写到文件 report」，于是静默多出一个垃圾文件、而且回显被截断。
    这里禁止任何 `->` 出现在非注释行，防的是「输送不出去的日志」。"""
    hits = [ln for ln in _code_lines().splitlines() if "->" in ln]
    assert not hits, f"run.bat 里出现 `->`（会被 cmd 当成重定向）：{hits}"


def test_run_bat_covers_submission_pack():
    txt = _text()
    assert "make_submission_pack.py" in txt, "官方必交材料（测试数据包）必须进一键验收"
    assert "lorealguard_testset_v4.zip" in txt, "验收脚本必须印出提交包产物路径"


# --- 退出码：从「恒 0 的空话」变成真测量（2026-09-24） -------------------------

TAIL_MARKER = 'if "%RC%"=="0" echo ACCEPTANCE:'


def _tail_source():
    """抠出 run.bat 的结尾原文（从印 ACCEPTANCE 那一行到文件末）。

    故意抠**原文**而不是在这里重写一份等价脚本 —— 重写的话，run.bat 里把
    `exit /b %RC%` 删掉、把 pause 挪到它后面，这份副本都不会跟着变，
    守卫就成了自说自话。抠原文才能让「run.bat 改成什么样」直接决定这条测试红不红。
    """
    text = _text().replace("\r\n", "\n")
    if TAIL_MARKER not in text:
        raise AssertionError(
            f"run.bat 里找不到结尾标记 {TAIL_MARKER!r} —— "
            "结尾那三行（两条 ACCEPTANCE 回显 + pause + exit /b %RC%）是退出码守卫的锚点，"
            "要么别删，要么连着这条测试一起改。"
        )
    return text[text.index(TAIL_MARKER):]


def _run_tail(rc, tmp_path):
    """把结局原文装进一个探针 .bat（前置 set RC=<rc>），真交给 cmd.exe 跑。"""
    body = '@echo off\r\nset "RC=%d"\r\n' % rc + _tail_source()
    probe = tmp_path / "_probe_runbat_tail.bat"
    probe.write_bytes(body.replace("\n", "\r\n").encode("ascii"))
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"  # Git Bash 会把 /c 当路径改写
    proc = subprocess.run(
        ["cmd", "/c", str(probe)],
        stdin=subprocess.DEVNULL,  # 否则 pause 会等键，测试挂死
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=60,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def test_run_bat_tail_propagates_failure(tmp_path):
    """真跑：RC=7 进来，调用侧必须拿到 7；RC=0 进来必须拿到 0。

    这条是本次修复的**存在性证明**。修复前 run.bat 末尾是 `pause` 收尾，
    上面两个断言会一起变成 (0, 0) —— 一条腿永远绿，测试因此变红，
    而这条红正是要抓的东西：退出码不再携带任何信息。
    """
    rc_fail, out_fail = _run_tail(7, tmp_path)
    assert rc_fail == 7, (
        "run.bat 的结尾没有把失败传导出去：探针里 RC=7，cmd.exe 却返回 "
        f"{rc_fail}。多半是末尾丢了 `exit /b %RC%`，或被 `pause` 抢了最后一条命令的位置。\n{out_fail}"
    )
    assert "ACCEPTANCE: RC=7" in out_fail, (
        f"退出码对了但日志里没印出来 —— 证据文件就带不走这个数：\n{out_fail}"
    )

    assert "RUNBAT_EXIT=7" in out_fail, (
        "交付报告里引用的是 `RUNBAT_EXIT=<n>` 这个 token，日志里却没有它 —— "
        f"被引用的 token 必须能在证据里 grep 到：\n{out_fail}"
    )

    rc_ok, out_ok = _run_tail(0, tmp_path)
    assert rc_ok == 0, f"全绿时反而返回 {rc_ok}，退出码方向反了：\n{out_ok}"
    assert "ACCEPTANCE: RC=0" in out_ok, f"全绿时没印出结论行：\n{out_ok}"
    assert "RUNBAT_EXIT=0" in out_ok, f"全绿时没印出被引用的 token：\n{out_ok}"


def test_run_bat_records_every_step_failure():
    """结构：`set "RC=0"` 打底、1..N 每步各有一条带自己编号的 RC 守卫、
    `pause` 必须在 `exit /b %RC%` 之**前**（pause 是留给双击场景的，
    但它一旦排在最后，返回码又变回恒 0）。"""
    txt = _text()
    assert 'set "RC=0"' in txt, "run.bat 没有给 RC 打底，第一步失败时 %RC% 是空的"

    steps = re.findall(r"\[(\d+)/(\d+)\]", txt)
    assert steps, "run.bat 里找不到任何 [n/N] 步骤标记"
    total = int(steps[0][1])
    missing = [n for n in range(1, total + 1) if 'set "RC=%d"' % n not in txt]
    assert not missing, (
        f"这些步骤失败后不会被记进 RC：{[f'[{n}/{total}]' for n in missing]} —— "
        "每步跑完都要跟一条 `if errorlevel 1 if \"%RC%\"==\"0\" set \"RC=n\"`，"
        "否则该步炸了验收脚本照样报全绿。"
    )

    lines = [ln.strip().lower() for ln in _code_lines().splitlines() if ln.strip()]
    assert lines[-1] == "exit /b %rc%", (
        f"run.bat 最后一条有效命令是 {lines[-1]!r}，不是 exit /b %RC% —— "
        "cmd.exe 按最后一条命令定退出码，不显式 exit 就等于把成败丢掉。"
    )
    assert lines[-2] == "pause", (
        f"exit /b 前面那行是 {lines[-2]!r}，不是 pause —— pause 必须保留（双击运行时看得见结论），"
        "但必须排在 exit /b 之前。"
    )