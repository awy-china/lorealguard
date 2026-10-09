"""「子进程编码必须两边说好」这条守卫的**变异自证** —— 证明它不是装饰品。

历史故障（2026-09-22 实测，不是假想）：
    本机 locale = GBK。子进程被要求（或碰巧）按 UTF-8 吐字、而父进程按 locale 收时，
    `subprocess` 的 `_readerthread` 抛 `UnicodeDecodeError` → **`proc.stdout` 变成空串**。
    同一个 bug 当天在三个地方各中一次：
      ① `tools/check_number_consistency.py` —— stdout 一重定向到文件就自己崩、报出**假的红**
         （而 CLAUDE.md §5 推荐的恰恰是重定向到文件这条跑法）；
      ② `tests/test_rebuild_from_scratch.py` —— 子进程 env 写了 `PYTHONIOENCODING=utf-8`，
         父进程却按 locale 解 → 黄金 sha256 闸门跑红时**诊断信息全空**；
      ③ `tests/test_cli.py` —— 只在外部环境带 `PYTHONIOENCODING` 时才崩 ⇒ 成败取决于**环境变量**。

⚠️ 这不是"假通过"：两处的闸门都是 `returncode`，不靠 stdout。真实的代价是
**失败时看不到任何原因**，外加每次跑都留噪音警告 —— 而"看不见为什么红"正是修不动的原因。

同族第四起（2026-09-22 当天一并补齐，M49/M50）：两处 ffmpeg 抽帧
（`tools/record_all_demo.py` 的 A/B/C、`tools/record_demo_page.py` 的产品四镜）也是
`text=True` 无 `encoding`。ffmpeg 的日志实测是 **UTF-8**，而抽帧路径含非 ASCII 时按 locale 解会崩 ——
`r.stderr` 变成 **None**（不是空串），于是 `f"抽帧失败：{r.stderr.strip()[:200]}"` 抛 `AttributeError`：
**录屏工具在最需要说清"为什么抽不到帧"的时候崩成另一种错**。

变异体全部取自上面四次真实故障，不是等价改写。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
sys.path.insert(0, str(ROOT / "tools"))  # 两处 ffmpeg 抽帧的守卫要 import 录屏工具

#: 被测目标文件。变异只能改**源码字符串**（改模块对象没用 —— 见 `test_demo_recordings_falsifiability`
#: 里 M40 的教训：断言读模块对象的话，源码变异永远变不红）。
REBUILD_SRC = ROOT / "tests" / "test_rebuild_from_scratch.py"
CLI_SRC = ROOT / "tests" / "test_cli.py"
NUMCHECK_SRC = ROOT / "tools" / "check_number_consistency.py"
RECALL_SRC = ROOT / "tools" / "record_all_demo.py"
RECPAGE_SRC = ROOT / "tools" / "record_demo_page.py"
RECDEMO_SRC = ROOT / "tools" / "record_demo.py"

#: ffmpeg 抽帧失败的诊断守卫的节点（M49/M50 与反向断言共用，别写成两份字符串）。
FFMPEG_GUARD = "tests/test_encoding_guard_falsifiability.py::test_ffmpeg_抽帧失败时诊断信息读得回来"

# (标签, 目标文件, 目标串, 换成什么, 期望变红的用例)
MUTANTS = [
    ("M44 重定向时不钉 UTF-8（数字口径工具假红）", NUMCHECK_SRC,
     "def main() -> int:\n    _utf8_stdout()\n", "def main() -> int:\n",
     "tests/test_number_consistency.py::test_redirected_stdout_does_not_fake_a_red"),

    ("M45 黄金闸门不按 UTF-8 读子进程", REBUILD_SRC,
     'capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300',
     'capture_output=True, text=True, timeout=300',
     "tests/test_rebuild_from_scratch.py::test_子进程诊断信息能读回来而不是空串"),

    ("M46 CLI 子进程不按 UTF-8 读", CLI_SRC,
     'text=True,\n        encoding="utf-8", errors="replace", timeout=300',
     'text=True, timeout=300',
     "tests/test_cli.py::test_module_entry_runs_as_real_subprocess"),

    ("M47 CLI 只留 errors=replace（GBK 硬解成替换字符）", CLI_SRC,
     'text=True,\n        encoding="utf-8", errors="replace", timeout=300',
     'text=True, errors="replace", timeout=300',
     "tests/test_cli.py::test_module_entry_runs_as_real_subprocess"),

    ("M48 CLI 不给子进程钉 UTF-8（成败取决于外部环境）", CLI_SRC,
     'env["PYTHONIOENCODING"] = "utf-8"', 'env.pop("PYTHONIOENCODING", None)',
     "tests/test_cli.py::test_module_entry_runs_as_real_subprocess"),

    ("M49 A/B/C 抽帧不按 UTF-8 读 ffmpeg 日志", RECALL_SRC,
     'capture_output=True, text=True, encoding="utf-8", errors="replace")',
     'capture_output=True, text=True)',
     FFMPEG_GUARD),

    ("M50 产品四镜抽帧不按 UTF-8 读 ffmpeg 日志", RECPAGE_SRC,
     'capture_output=True, text=True, encoding="utf-8", errors="replace")',
     'capture_output=True, text=True)',
     FFMPEG_GUARD),
]

#: ⚠️ **故意没有** `record_demo.probe()` 的变异体（2026-09-22 亲手试过，写不出来）：
#: 它的 `encoding=` 那一行**行为上是惰性的** —— ffprobe 的报错走 **stderr**（实测 `stderr=None`），
#: 而 `probe()` 只读 stdout（`-of default=nw=1` 吐的全是数字），stdout 无论钉不钉都是 `""`
#: → 变异前后 `probe()` 都返回 `{}`，守卫永远绿。**一条不会红的检查等于没有检查**，
#: 所以这里只登记事实，不给它硬凑一条。那一行的价值仅在于消掉读线程崩溃的噪音警告。
#: （目标文件仍登记在 `RECDEMO_SRC`：留个入口，别让下一个人以为"漏了没查"。）


#: 抽帧探针：**故意用非 ASCII 路径** —— 非 ASCII 正是这条 bug 的触发条件
#: （ffmpeg 会把打不开的路径原样回显，那串 UTF-8 字节按 GBK 解就崩）。
PROBE_VIDEO = "不存在的探针视频.mp4"


def _ffmpeg_failure_message(mod_name: str) -> str:
    """调某处 `grab_frame` 去抽一个不存在的非 ASCII 路径，返回它报的错文本。

    期望：`RuntimeError`，且消息里**带着 ffmpeg 的原话**（含那个路径）。
    """
    import importlib

    mod = importlib.import_module(mod_name)
    try:
        mod.grab_frame(ROOT / "output" / PROBE_VIDEO, 1.0, "probe")
    except RuntimeError as exc:
        return str(exc)
    raise AssertionError(f"{mod_name}.grab_frame 该失败却成功了（探针路径不存在）")


def test_ffmpeg_抽帧失败时诊断信息读得回来():
    """两处 ffmpeg 抽帧的 `r.stderr` 必须真的读回来 —— 否则录屏工具崩成 `AttributeError`。

    ⚠️ **前提要说清**：`subprocess.run(text=True)` 按**本机 locale** 解子进程输出，
    所以这条只在"locale 不是 UTF-8"的机器上是活的（本机 GBK）；
    在 UTF-8 locale 的机器上它会恒绿 —— 与 M45/M48 同类，靠 `_pytest` 剥掉
    `PYTHONIOENCODING` 制造真实 locale 环境。交付机就是 Windows GBK，故以本机为准。
    """
    for mod_name in ("record_all_demo", "record_demo_page"):
        msg = _ffmpeg_failure_message(mod_name)
        assert "抽帧失败" in msg, f"{mod_name}: 报错前缀变了，守卫要跟着改：{msg[:120]!r}"
        assert PROBE_VIDEO in msg, (
            f"{mod_name}.grab_frame 的报错里没有 ffmpeg 的原话"
            f"（读线程崩了 → stderr 是 None → 真正的错因丢了）：{msg[:200]!r}")
        assert "No such file or directory" in msg, f"{mod_name}: ffmpeg 的原因串没读回来：{msg[:200]!r}"


def test_film_segment_成功行不因编码崩掉():
    """剪辑工具 `tools/make_film_segment.py` 的成功行带 `✅`，GBK stdout 上必须还能打出来。

    ⚠️ 这是 D-07 那类 bug 的**第六起**，而且中在当天刚写完的这个工具上（前五起见文件头）。
    最坏的地方不是崩，是**崩在最后一行**：四项判据全过、mp4 已经落盘，却报非 0 退出 ——
    看的人会以为片子剪坏了，去重剪一段本来没问题的片子。

    探针是工具自己的 `--self-check-utf8`：它走 `ok_line()`，与真跑成功行**同一份字符串**，
    所以这里绿就等于真跑的成功行绿。

    ⚠️ **前提**：与上面那条一样，只在"locale 不是 UTF-8"的机器上是活的（本机 GBK）；
    UTF-8 locale 上恒绿。所以子进程的 env 里摘掉 `PYTHONIOENCODING`（带着它会假绿）。
    """
    out = ROOT / "output" / "_selfcheck_utf8.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONIOENCODING")}
    with open(out, "w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run(
            [PY, str(ROOT / "tools" / "make_film_segment.py"), "--self-check-utf8"],
            cwd=str(ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT).returncode
    text = out.read_text(encoding="utf-8", errors="replace")
    assert rc == 0, (
        f"成功行打印时崩了（exit {rc}）—— stdout 不重定向到终端时按 locale 编码，"
        f"`✅` 编不出来。修法见 tools/make_film_segment.py 的 `_utf8_stdout()`。\n{text[-400:]}")
    assert "✅" in text and "实跑判据" in text, f"成功行没打全：{text[-200:]!r}"


def _pytest(node: str) -> int:
    """跑一条用例，返回退出码。**stdout 重定向到文件**（走真实路径，不靠 capture）。

    环境刻意取「canonical 命令」的样子：摘掉 `PYTHONPATH`（本机它被全局占用）
    与 `PYTHONIOENCODING`（没有它才暴露真实的 locale 行为 —— 带着它跑，M45/M48 会假绿）。
    """
    log = ROOT / "output" / "_encoding_mutant.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONIOENCODING")}
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        return subprocess.run([PY, "-m", "pytest", node, "-q", "--no-header"],
                              cwd=str(ROOT), env=env,
                              stdout=fh, stderr=subprocess.STDOUT).returncode


def test_every_encoding_mutant_makes_its_guard_red():
    # 目标文件从 MUTANTS 里推出来（**不是另写一份名单** —— 两份名单迟早对不上）
    originals = {p: p.read_text(encoding="utf-8") for p in {m[1] for m in MUTANTS}}
    try:
        for label, target, needle, repl, node in MUTANTS:
            src = originals[target]
            assert needle in src, f"{label}: 变异目标串不在 {target.name} 里（守卫已改？）"
            target.write_text(src.replace(needle, repl, 1), encoding="utf-8")
            try:
                rc = _pytest(node)
            finally:
                target.write_text(src, encoding="utf-8")
            assert rc != 0, f"变异体没被抓到：{label} → {node.split('::')[-1]} 仍是绿的"
    finally:
        for p, text in originals.items():
            p.write_text(text, encoding="utf-8")
            assert p.read_text(encoding="utf-8") == text, f"还原失败：{p}"


def test_real_sources_keep_all_encoding_guards_green():
    """反向断言：真源码下全部守卫必须绿。**在同一个进程里带 locale 环境跑** ——
    这正是它们被写出来要防的那种环境（外部没设编码变量）。

    ⚠️ 没有这条，M44–M50 只需"把守卫写成永远红"就能全过 —— 那种变异自证是假的。
    """
    for node in dict.fromkeys(m[4] for m in MUTANTS):  # 去重：M49/M50 共用一条守卫
        assert _pytest(node) == 0, f"真源码下守卫没绿：{node}"
