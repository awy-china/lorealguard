# -*- coding: utf-8 -*-
"""`tests/test_film.py` 那八条守卫的**变异自证** —— 证明它们不是装饰品。

九个变异体全部取自 2026-09-26 真实踩过 / 差点踩到的写法，不是等价改写：
  M61 归零滤波器写成"音量 1"（= 不归零）——「镜 5–7 环境静音」整句落空，判据必须抓。
  M62 归零窗口起点**偏了 1.5s**（`silent[0]` → `silent[0] + 1.5`）—— 窗口位置错的典型形态。
  M63 判据窗口退回**压在闸门边缘**（去掉内侧守护带）—— D-25 的原形，这条本来永远红。
  M64 字幕字号偏离台本 `:153`（28 → 22）—— 画面上每个数字都得能追到一行。
  M65 SRT 解析把**终点当起点**（`secs(g, 4)` → `secs(g)`）—— 时间轴静默塌成 0 长度。
  M66 字幕带裁错区域（底部带 → 顶部带）—— 没烧字幕也会数到白像素 ⇒ 判据失效。
  M67 抽帧退回**帧号寻址**（去掉 `-ss`）—— D-24 的原形：长文件上取到别的帧。
  M68 定格全帧扫描**退化成抽样**（`range(1, total)` → `range(1, total, total // 4)`）
      —— D-22 的盲区重开：30 帧下定采样帧号 60/67/74/81/88 **不含 75**。
  M69 摘掉 `verify()` 里对 `_hold_scan()` 的**调用**（判据退回三帧抽检）——同一盲区的另一种形态。

⚠️ 与 `test_film_segment_falsifiability.py` 的区别：那边一对一变异的只有**一个**源文件，
   这里跨两把工具（`make_film.py` / `make_film_final.py`）⇒ 变异体多带一个"源文件"字段。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable

SRC_FINAL = ROOT / "tools" / "make_film_final.py"
SRC_MASTER = ROOT / "tools" / "make_film.py"
SRC_SEG = ROOT / "tools" / "make_film_segment.py"

#: `make_film_segment.py` 的字节数**快照**（2026-09-27 接上全帧扫描后）。
#: 作用不是防人改源码，而是给「变异 harness 的 `read_text`/`write_text` 往返不许改字节」
#: 一个可对照的锚 —— 源码真被改了就同步改这里（见文件末尾那条字节保真用例）。
#: （2026-10-01 同步：round44 把 `make_film_segment.py:210` 的 expect 换成 v4 口径 ⇒ 103472 → 103533；2026-10-03：S11 随 D5 源片换版同步砍头帧数与注释 ⇒ 103533 → 103683；2026-10-05：音色换云希 ⇒ 9 处 `narration=` ＋ 5 处实测注释同步（注释压回原行数，保 775 行等文档引用零位移）⇒ 103683 → 103800）
SEG_BYTES = 103800

G1 = "tests/test_film.py::test_bgm_归零窗口是数字静音_窗口外仍有声"
G2 = "tests/test_film.py::test_静音判据从闸门内侧起量_不压在边缘"
G3 = "tests/test_film.py::test_字幕样式与台本一致"
G4 = "tests/test_film.py::test_srt_解析起止时间与文本_且吃_CRLF"
G5 = "tests/test_film.py::test_底部字幕带的白像素计数能红能绿"
G6 = "tests/test_film.py::test_抽帧按时间定位_不用帧号"
G7 = "tests/test_film.py::test_定格全帧扫描_能红能绿"
G8 = "tests/test_film.py::test_定格判据走全帧扫描_不用三帧抽检"

#: 归零那一行（M61 / M62 共用同一个锚点，各自独立测一次）。
GATE_LINE = "f\"volume=volume=0:enable='between(t,{silent[0]:.3f},{silent[1]:.3f})',\""

# (标签, 源文件, 目标串, 换成什么, 期望变红的用例)
MUTANTS = [
    ("M61 归零写成音量 1（等于不归零）",
     SRC_FINAL, GATE_LINE,
     "f\"volume=volume=1:enable='between(t,{silent[0]:.3f},{silent[1]:.3f})',\"",
     G1),

    ("M62 归零窗口起点偏 1.5s（窗口位置错）",
     SRC_FINAL, GATE_LINE,
     "f\"volume=volume=0:enable='between(t,{silent[0] + 1.5:.3f},{silent[1]:.3f})',\"",
     G1),

    ("M63 判据窗口退回压在闸门边缘（D-25 原形）",
     SRC_FINAL,
     "vol = max_vol(bgm, silent[0] + 1.0, silent[1] - 1.0)",
     "vol = max_vol(bgm, silent[0], silent[1])",
     G2),

    ("M64 字幕字号偏离台本（28 → 22）",
     SRC_FINAL,
     'FONT, SIZE, ALPHA60 = "DengXian", 28, "&H99000000"',
     'FONT, SIZE, ALPHA60 = "DengXian", 22, "&H99000000"',
     G3),

    ("M65 SRT 解析把终点当起点",
     SRC_FINAL,
     "out.append((secs(g), secs(g, 4), ",
     "out.append((secs(g), secs(g), ",
     G4),

    ("M66 字幕带裁错区域（底部 → 顶部）",
     SRC_FINAL,
     "SUB_BAND = (0, 950, 1920, 1080)",
     "SUB_BAND = (0, 0, 1920, 130)",
     G5),

    ("M67 抽帧退回帧号寻址（去掉 -ss）",
     SRC_MASTER,
     '["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),',
     '["ffmpeg", "-v", "error", "-i", str(path),',
     G6),

    #: M68 的全帧扫描退化成 `total // 4` 一采样（30 帧 ⇒ 帧号 60/67/74/81/88，不含 75）；
    #: M69 把 `verify()` 里那行调用换成空扫描（`_hold_scan(` 只剩定义那一处）。
    ("M68 定格全帧扫描退化成抽样（盲区重开）",
     SRC_SEG,
     "        for k in range(1, total):",
     "        for k in range(1, total, max(1, total // 4)):",
     G7),

    ("M69 verify() 不调全帧扫描（判据退回三帧抽检）",
     SRC_SEG,
     "                hold_bad, scanned = _hold_scan(out, ref_n, last)",
     "                hold_bad, scanned = ([], 0)  # M69：摘掉全帧扫描",
     G8),
]


def _assert_mutant_live(label: str, src: pathlib.Path, before: str, repl: str) -> None:
    """自证变异**真的落到盘上** —— 否则"守卫是绿的"分不清是"没抓到"还是"没生效"。

    实测 2026-09-26：整套运行里 M66 出现过 1 次假绿（同一变异孤立跑 7/7 红、手动注入也红）
    ⇒ 把"变异没生效"变成一句断言，别让它变成悬案。
    判据用**出现次数 +1**（对源码里的重复串免疫）。
    """
    now = src.read_text(encoding="utf-8")
    assert now.count(repl) == before.count(repl) + 1, (
        f"{label}: 变异没落到盘上 —— {src.name} 里替换串出现次数没 +1"
        f"（替换前 {before.count(repl)} 次、现在 {now.count(repl)} 次）⇒ 此刻的绿不算证明")


def _pytest(tag: str, *nodes: str) -> tuple[int, str]:
    """跑若干条用例 → `(退出码, 日志全文)`。

    **摘掉 PYTHONIOENCODING / PYTHONUTF8** —— 带着它们会让编码类守卫假绿。
    日志按 `tag` 落 `output/_film_mutants/<tag>.txt`：退出码非 0 时要能查**为什么**红。
    """
    d = ROOT / "output" / "_film_mutants"
    d.mkdir(parents=True, exist_ok=True)
    log = d / f"{tag}.txt"
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONIOENCODING", "PYTHONUTF8")}
    # 禁 pyc 缓存：变异改写源码后，若子进程按 (mtime, size) 命中旧 pyc，就会拿旧字节判"绿"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run([PY, "-m", "pytest", "-p", "no:cacheprovider",
                             *nodes, "-q", "--no-header"],
                            cwd=str(ROOT), env=env,
                            stdout=fh, stderr=subprocess.STDOUT).returncode
    return rc, log.read_text(encoding="utf-8", errors="replace")


def test_every_pipeline_mutant_makes_its_guard_red():
    originals = {p: p.read_text(encoding="utf-8") for p in (SRC_FINAL, SRC_MASTER, SRC_SEG)}
    try:
        for label, src, needle, repl, node in MUTANTS:
            assert needle in originals[src], (
                f"{label}: 变异目标串不在 {src.name} 里（守卫/源码已改？）")
            src.write_text(originals[src].replace(needle, repl, 1), encoding="utf-8")
            tag = label.split()[0]
            try:
                _assert_mutant_live(label, src, originals[src], repl)
                rc, text = _pytest(tag, node)
                # 跑完仍在盘 ⇒ 这次的"红/绿"确实是这份变异下的判定（不是缓存/竞态）
                _assert_mutant_live(label, src, originals[src], repl)
            finally:
                src.write_text(originals[src], encoding="utf-8")
            assert rc != 0, f"变异体没被抓到：{label} → {node.split('::')[-1]} 仍是绿的"
            # ⚠️ 只认"守卫变红"：退出码非 0 也可能是变异把源码改出语法错 ⇒ 收集失败（根本没跑）。
            #    记号取 `FAILED tests/test_film.py::`（收集错打的是 `ERROR tests/...`，不是 FAILED）。
            #    实测坑：本机 pytest 的 `-q --no-header` **不打印** "1 failed in …" 汇总行（grep = 0）
            #    ⇒ 别拿那行判"跑没跑"，会写出一条永远红的元判据（本文件第一版就这么栽的）。
            assert "FAILED tests/test_film.py::" in text and "ERROR" not in text, (
                f"{label}: 退出码非 0 但**不是**守卫变红（疑似收集/语法错）"
                f" ⇒ 见 output/_film_mutants/{tag}.txt")
    finally:
        for p, txt in originals.items():
            p.write_text(txt, encoding="utf-8")
            assert p.read_text(encoding="utf-8") == txt, f"{p.name} 还原失败"


def test_real_source_keeps_all_pipeline_guards_green():
    """反向断言：真源码下六条守卫必须全绿（否则把守卫写成永远红也能骗过上面）。"""
    nodes = list(dict.fromkeys(m[4] for m in MUTANTS))
    rc, text = _pytest("real", *nodes)
    assert rc == 0, f"真源码下守卫没全绿（{len(nodes)} 条，见 output/_film_mutants/real.txt）"
    assert "FAILED" not in text and "ERROR" not in text, "真源码下出现 FAILED/ERROR"
    head = text.split("[100%]")[0].splitlines()[-1]
    assert head.count(".") == len(nodes), (
        f"进度行只有 {head.count('.')} 个通过记号，该有 {len(nodes)} 个（被跳过/没收集到？）")


def test_源码读写往返字节保真_不被变异harness改行尾():
    """整套跑完（含变异与还原）后，`tools/make_film_segment.py` 必须**逐字节**回到原样。

    `read_text()` 是 `newline=None`（CRLF → `\n`），`write_text()` 再按 `os.linesep` 写回
    （Windows 上 ＝ CRLF）⇒ 只有**纯 CRLF** 的文件才往返字节不变。哪天它变成纯 LF，
    写回就把每个 `\n` 变成 `\r\n` —— 整套变异跑完源码被静默改了行尾，
    而还原断言（`test_every_pipeline_mutant_makes_its_guard_red` 的 `finally`，比的是
    `read_text` 之后的**文本**、行尾差异已被归一掉）**看不见**这件事。
    所以这里比**字节**：纯 CRLF ＋ 往返字节相等 ＋ 尺寸等于 `SEG_BYTES`。
    """
    raw = SRC_SEG.read_bytes()
    cr, lf = raw.count(b"\r"), raw.count(b"\n")
    assert cr == lf and raw.count(b"\r\n") == lf, (
        f"make_film_segment.py 不是纯 CRLF（CR={cr} / LF={lf}）"
        " ⇒ `read_text`/`write_text` 往返会改行尾，变异 harness 会静默改源码")
    d = ROOT / "output" / "_film_mutants"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "_roundtrip_check.py"
    try:
        # 与 harness 走**同一条路**：read_text() → write_text()
        tmp.write_text(SRC_SEG.read_text(encoding="utf-8"), encoding="utf-8")
        assert tmp.read_bytes() == raw, (
            "`read_text` → `write_text` 往返改了字节 ⇒ 变异 harness 跑完会把"
            " make_film_segment.py 的行尾改掉（比文本的还原断言看不见这件事）")
    finally:
        tmp.unlink(missing_ok=True)
    assert len(raw) == SEG_BYTES, (
        f"make_film_segment.py 现在是 {len(raw)} B，登记的是 {SEG_BYTES} B ——"
        " 改过那个源文件就同步改 SEG_BYTES（这两个数不是判据，是快照；"
        "先确认变的是**人手改的源码**，而不是 harness 往返改的行尾）")
