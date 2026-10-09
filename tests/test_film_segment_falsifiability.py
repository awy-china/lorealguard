"""`tools/make_film_segment.py` 那几条守卫的**变异自证** —— 证明它们不是装饰品。

三个变异体全部取自 2026-09-22 真实踩过的写法，不是等价改写：
  M51 帧数用「时长 × 30」**算**（而不是让 ffmpeg 数）——D4 算出 178、实得 176，
      整段短 2 帧，尾帧读回抽到不存在的 509 帧。**这条是最值钱的**：它抓的是
      "卡片会把总长凑到槽位、所以只看总长看不出来"的那个盲区。
  M52 `clip_sel` 用**前一帧**的有理数（掐头早一帧）——内容整体前移一帧，
      段长不变、尾帧照样读得回来，只有 ④ 那条相邻帧比较能抓。
  M53 去掉 `main()` 里的 `_utf8_stdout()`——成功行带 `✅`，GBK 下 stdout 一重定向就崩，
      **崩在最后一行**：判据全过、mp4 已落盘，却报非 0（`docs/DEBT.md` D-07 第六起）。

⚠️ M53 与 `tests/test_encoding_guard_falsifiability.py` 里的守卫同源（同一个 bug 类），
   但它守的是**另一个文件**，所以变异体登记在这里，守卫节点写全路径。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable

#: 被测目标：剪辑工具本体。
FILM_SRC = ROOT / "tools" / "make_film_segment.py"

#: 出片帧数 / 末帧可抽 —— 抓 M51。
LEN_GUARD = "tests/test_film_segment.py::test_出片帧数等于槽位_且末帧抽得出来"
#: 剪点落在 onset 那一帧 —— 抓 M52。
CUT_GUARD = "tests/test_film_segment.py::test_剪点落在onset那一帧_不是前一帧"
#: 编码守卫（在另一个文件里）—— 抓 M53。
UTF8_GUARD = ("tests/test_encoding_guard_falsifiability.py"
              "::test_film_segment_成功行不因编码崩掉")

# (标签, 目标串, 换成什么, 期望变红的用例)
MUTANTS = [
    ("M51 帧数算出来而不是数出来（卡片把总长凑齐，看不出少帧）",
     '            frames = _clip_len(EV / pc["src"], sel)',
     '            frames = int(round(float(probe(EV / pc["src"])'
     '.get("duration", 0) or 0) * FPS))',
     LEN_GUARD),

    ("M52 掐头用前一帧的有理数（早一帧，段长不变）",
     'return f"gte(t\\\\,{onsets[pc[\'src\']][0]})"',
     'return f"gte(t\\\\,{onsets[pc[\'src\']][2]})"',
     CUT_GUARD),

    ("M53 main() 不钉 UTF-8（成功行在 GBK stdout 上崩掉）",
     "def main() -> int:\n    _utf8_stdout()\n",
     "def main() -> int:\n",
     UTF8_GUARD),
]


def _pytest(node: str) -> int:
    """跑一条用例，返回退出码。**摘掉 PYTHONIOENCODING / PYTHONUTF8** —— 带着它们 M53 会假绿。

    2026-09-26 实测：本仓库的 shell 里 `PYTHONUTF8=1` 常驻（harness 注入），
    它让 CPython 的 stdout **无视** `main()` 里那句 UTF-8 钉法 ⇒ M53 退化成空操作、
    守卫照绿。这与 D-07 第六起同一类：**掩盖性环境变量会让守卫失去变红的能力**。
    """
    log = ROOT / "output" / "_film_mutant.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONIOENCODING", "PYTHONUTF8")}
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        return subprocess.run([PY, "-m", "pytest", node, "-q", "--no-header"],
                              cwd=str(ROOT), env=env,
                              stdout=fh, stderr=subprocess.STDOUT).returncode


def test_every_film_mutant_makes_its_guard_red():
    original = FILM_SRC.read_text(encoding="utf-8")
    try:
        for label, needle, repl, node in MUTANTS:
            assert needle in original, f"{label}: 变异目标串不在 {FILM_SRC.name} 里（守卫已改？）"
            FILM_SRC.write_text(original.replace(needle, repl, 1), encoding="utf-8")
            try:
                rc = _pytest(node)
            finally:
                FILM_SRC.write_text(original, encoding="utf-8")
            assert rc != 0, f"变异体没被抓到：{label} → {node.split('::')[-1]} 仍是绿的"
    finally:
        FILM_SRC.write_text(original, encoding="utf-8")
        assert FILM_SRC.read_text(encoding="utf-8") == original, "还原失败"


def test_real_source_keeps_all_film_guards_green():
    """反向断言：真源码下三条守卫必须全绿。没有这条，把守卫写成永远红也能骗过上面。"""
    for node in dict.fromkeys(m[3] for m in MUTANTS):
        assert _pytest(node) == 0, f"真源码下守卫没绿：{node}"
