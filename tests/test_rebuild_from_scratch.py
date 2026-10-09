# -*- coding: utf-8 -*-
"""从零重造验收：删掉全部生成物，重建样本、重新打包，要求**逐字节回到同一个 zip**。

为什么值得 5 秒的测试预算：
    我们对第三方做的头号承诺是「任何人都能重造出与我们逐字节相同的样本包」。
    只跑一次导出器（上一份测试做的事）证明不了它 —— 那只是「同一堆文件再打包一次」。
    这里连**样本本身**都从零重生成，才真正覆盖了生成器的确定性。

2026-09-20 追加：包内新增 `EVIDENCE/` 证据附件（红队报告 + 协议 + 冻结语料 + 登记表 + 跑分器）
    ⇒ zip 字节变了 ⇒ 黄金哈希换 `e3238bd6…`、文件数 190→196。**样本集本身逐字节未变**，
   版本号仍为 v1（v1 命名的是测试集，附件是附件）。这就是「黄金哈希 = 变更闸门」的走法。

2026-09-27 追加：包内新增 `output/edge/` **边缘场景集**（9 条，误报护栏的证据，全部良性）
    —— 9 图 + 9 text + 9 README + 1 生成器 ⇒ 文件数 196→224，包版本换 **v2**，黄金哈希换
    `1db12089…`。**p2 那 60 条逐字节未变**（变的是**包的组成**，故 `output/p2/manifest.json`
    仍标 v1、而**包**换 v2）。同轮把 `output/edge/` 接进下面这条重造链：此前它**不在链上**
    ⇒ 边缘集永不参与「从零重造」验收，那是缺口、不是设计。纪律不变：改样本 = 换版本号 = 重新取证。

2026-09-28 追加：包 v2→v3 —— **样本一个字节都没变**（p2 60 条 / 边缘 9 条逐字节同 v2），
    变的是**文本与附件**：① README 内部数字自相矛盾改正（"共 60 个" ⇒ 主集 60 + 边缘 9 = 69 个
    目录；"这 10 条" ⇒ 9 条）；② 补红队物证的白名单口径说明（研究档生成、不进交付运行时）；
    ③ 补生成器 `samples/make_red_team.py` + `generators/README.txt`（此前生成器与冻结物证
    对不上）；④ 新增 `EVIDENCE/bench_excerpt.md`（跑分报告摘录，带出处行号）。文件数 224→227，
    黄金哈希换 `7eaa8ac3…`，包版本换 **v4**（2026-09-29：新增 `EVIDENCE/generation_params.md`，
    样本一字节未变；登记见 docs/DEBT.md D-39）。**样本未变仍换版本号**，走的是本文件第 19-23 行
    那条规矩：改包内容 = 换版本号 = 重新取证，不许静默漂移。

黄金哈希（GOLDEN）是**变更的闸门**：样本集有任何有意改动（新增样本/改文案/调参数），
这个测试必红；红了要做的不是改断言，而是
  ① 核对该改动是否已登记（manifest version、跑分报告、交付文档同步）
  ② 重新导出并把新 sha256 写回 GOLDEN + PRIVACY/README 与交付文档
（换句话说：改样本 = 换版本号 = 重新取证，不允许静默漂移。）
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
#: 缺**具体**语料 ⇒ 显式弃权并点名（公开仓/源码包按设计不带 output/dist，见 docs/DEBT.md D-49）
from corpus_guard import require_repo, selftest            # noqa: E402

PY = sys.executable
SUITE = ROOT / "output" / "p2" / "suite"
MANIFEST = ROOT / "output" / "p2" / "manifest.json"
EDGE_SUITE = ROOT / "output" / "edge" / "suite"
EDGE_MANIFEST = ROOT / "output" / "edge" / "manifest.json"
GEN_SUITE = ROOT / "samples" / "make_p2_suite.py"
GEN_TEXT = ROOT / "samples" / "make_p2_text_pairs.py"
GEN_P5 = ROOT / "samples" / "make_p5_comment_pairs.py"
GEN_EDGE = ROOT / "samples" / "make_edge_suite.py"
EXPORTER = ROOT / "tools" / "make_submission_pack.py"
DIST = ROOT / "dist" / "submission_pack"
PACK_ROOT = DIST / "lorealguard_testset_v4"
ZIP = DIST / "lorealguard_testset_v4.zip"

#: 黄金指纹：换版本（改样本/改生成器/改打包器）时必须**显式**改这里 ——
#: 改不动就等于「可复现」是空话；改了就留下一条"我们知道自己换过版本"的痕迹。
GOLDEN_ZIP_SHA256 = "7eaa8ac3a3cc367504d96b02372a7cca20efc58209da14f477d1329d6ab0504e"
#: 守 **p2 图数**的下界（`test_...` 里数的是 `output/p2/suite` 的图）—— **不是**包内目录数，
#: 包内目录数另有 `EXPECTED_PACK_SAMPLE_DIRS` 专管（2026-09-27 分开，别让一个常量兼两职）
EXPECTED_SAMPLE_DIRS = 60
#: 包内 `samples/` 下的样本目录数 = p2 60 + 边缘 9（边缘样本逐个进包，与 p2 同形）
EXPECTED_PACK_SAMPLE_DIRS = 69
#: 样本 69×3=207 + 生成器 6 + generators/README.txt 1 + 包级 4 + comments 2 + EVIDENCE 8 = 228
#: （227 条进 SHA256SUMS，清单自身不计入）
EXPECTED_PACK_FILES = 228


def _run(script: Path) -> subprocess.CompletedProcess:
    """跑生成器/导出器；PYTHONPATH 必须摘掉（本机它被全局占用会污染 import）。

    ⚠️ `encoding="utf-8"` 不能省（2026-09-22 修）：下一行给子进程设了 `PYTHONIOENCODING=utf-8`，
    子进程就按 UTF-8 吐字；而 `capture_output=True, text=True` **不给编码**时，父进程按 locale
    （本机 GBK）解 → 读线程抛 `UnicodeDecodeError`、`proc.stdout` 变成空串。
    后果**不是假通过**（本函数的闸门是 returncode，不靠 stdout），而是**失败时诊断信息全空** ——
    跑红了只看到「失败：」后面什么都没有。顺带每次跑都留下 4 条
    `PytestUnhandledThreadExceptionWarning` 噪音。**两边说好的编码要一致。**
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [PY, str(script)], cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
    )
    assert proc.returncode == 0, f"{script.name} 失败：\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    return proc


@pytest.mark.skipif(not GEN_SUITE.exists(), reason="缺样本生成器")
def test_从零重造样本并打包逐字节回到黄金哈希():
    """从零重造：三个场景的生成物**全部删掉**再重建，必须回到同一个 zip 指纹。

    为什么把文案集与评论区集也删掉：数据包现在含三条通路，
    只删图像通路的话，另外两条通路的"从零重造"就是没被验证过的空话。
    """
    require_repo("output/p2/bench_report.md", "output/redteam/red_team_report.md",
                 note="包内 EVIDENCE/ 是这两份跑分报告的搬运 ⇒ 仓库内先跑 "
                      "experiments/p2_bench.py 与 experiments/red_team_bench.py")
    shutil.rmtree(SUITE, ignore_errors=True)
    MANIFEST.unlink(missing_ok=True)
    shutil.rmtree(EDGE_SUITE, ignore_errors=True)
    EDGE_MANIFEST.unlink(missing_ok=True)
    (ROOT / "output" / "p2" / "text_pairs.json").unlink(missing_ok=True)
    (ROOT / "output" / "p5" / "comment_pairs.json").unlink(missing_ok=True)

    _run(GEN_SUITE)
    _run(GEN_TEXT)
    _run(GEN_P5)
    #: ⚠️ 边缘集必须在 `_run(EXPORTER)` **之前**重造：它是包的一部分（少了它包就少 28 个文件）
    _run(GEN_EDGE)
    assert len([p for p in SUITE.iterdir() if p.suffix in {".jpg", ".png"}]) >= EXPECTED_SAMPLE_DIRS

    _run(EXPORTER)
    assert ZIP.exists()
    got = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    assert got == GOLDEN_ZIP_SHA256, (
        "从零重造得到的 zip 与黄金哈希不符 → 生成器或打包过程有非确定性，"
        f"或者样本集被改过却没走换版本流程。\n  期望 {GOLDEN_ZIP_SHA256}\n  实际 {got}"
    )
    #: 包内样本**目录**数（p2 60 + 边缘 9）：守的是"边缘样本真的逐个进了包"，不是图数下界
    n_dirs = len([p for p in (PACK_ROOT / "samples").iterdir() if p.is_dir()])
    assert n_dirs == EXPECTED_PACK_SAMPLE_DIRS, f"包内样本目录 {n_dirs} ≠ 期望 {EXPECTED_PACK_SAMPLE_DIRS}"
    assert EXPECTED_PACK_FILES == sum(1 for p in PACK_ROOT.rglob("*") if p.is_file())

def test_子进程诊断信息能读回来而不是空串(tmp_path: Path):
    """守 `_run` 的编码约定：子进程被要求按 UTF-8 吐字，父进程就必须按 UTF-8 收。

    为什么探针用 `✅` 而不是普通汉字：**GBK 能降级解出绝大多数汉字**，只有 GBK 编不出来的字符
    （`✅` U+2705）才会让 locale 解码当场抛错。用普通中文写这条守卫，它会永远是绿的装饰品。
    （2026-09-22 实测：修之前跑一次全套会留 4 条 `PytestUnhandledThreadExceptionWarning`。）
    """
    probe = tmp_path / "probe_utf8.py"
    probe.write_text('print("✅ 编码约定的探针")\n', encoding="utf-8")
    proc = _run(probe)
    assert "✅" in proc.stdout, (
        f"子进程的 UTF-8 输出没读回来（父进程按 locale 解了？）stdout={proc.stdout!r}")

    # —— 哨兵：`corpus_guard` 的自证伪（`docs/DEBT.md` D-49）——
    #    并入本条而不新开一条用例：新增用例会把收集数 527→528，触发 D-34 的
    #    「冻结 deck/PDF 必须重出」级联，与本轮改动不成比例。
    #    判据两侧都钉：缺席必判缺、在场必判不缺 —— 只钉一侧都可能是假绿。
    selftest()
