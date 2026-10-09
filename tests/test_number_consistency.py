"""`tools/check_number_consistency.py` 的守卫 —— 它是"数字口径单一来源"，此前**一条守卫都没有**。

为什么补（2026-09-22 实测）：CLAUDE.md §5 明令「`| tail` 会吃掉上游退出码，别用它判成败」，
于是**推荐的跑法就是重定向到文件**。而本工具的输出带 `✅`/`❌`/`🔧`，Windows 上 stdout 一旦不是终端
就按 locale（本机 GBK）编码 → `UnicodeEncodeError` → **exit 1**。
看的人会以为**数字漂移了**，然后去改本来没错的数字 —— 一条**假的红**和一条**永远不红的检查**同样坏。

本文件三道（+ 文末 ⑦⑧：`--fix` 回填时的**行尾**，2026-09-26 补）：
  ① 真重定向（stdout 指向文件）不许假红；
  ② 自证伪：把 `_utf8_stdout()` 那一行**从源码里删掉**，①必须变红（否则①是装饰品）；
  ③ `--self-check` 模式仍能抓到注入的错数（工具自带的自证伪入口，此前没人自动跑它）。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "check_number_consistency.py"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pytest                                             # noqa: E402
#: 缺交付区 ⇒ 显式弃权并点名（公开仓/源码包按设计不带交付面，见 docs/DEBT.md D-49）
from corpus_guard import require_delivery                    # noqa: E402

#: 变异体落点：放 tests/ 下（`__file__.parent.parent` 才等于仓库根，工具才能找对 SCAN_GLOBS）；
#: 故意不以 `test_` 开头 —— pytest 不会收集它，但它自己会去数 tests/ 的用例数，别把自己算进去。
MUTANT = ROOT / "tests" / "_mutant_numcheck.py"


def _run(tool: pathlib.Path, *args: str, encoding_env: str | None = None):
    """跑工具，**stdout 真的指到一个文件**（不是 capture —— capture 走管道，行为与真实重定向不同）。

    返回 (returncode, 文件内容)。
    """
    out_file = ROOT / "output" / "_numcheck_guard.txt"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": ""}
    if encoding_env:
        env["PYTHONIOENCODING"] = encoding_env
    else:
        env.pop("PYTHONIOENCODING", None)
    with open(out_file, "w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run([sys.executable, str(tool), *args],
                            cwd=str(ROOT), env=env, stdout=fh,
                            stderr=subprocess.STDOUT).returncode
    return rc, out_file.read_text(encoding="utf-8", errors="replace")


def test_redirected_stdout_does_not_fake_a_red():
    """① 真重定向 + 强制 GBK：工具**不许自己崩掉**（这是本用例唯一的主题）。

    ⚠️ 这里**只断言编码**，不断言 `rc == 0` —— 数字漂移是另一件事，归 ④ 管。
    混在一起写过（2026-09-22 改）：那样会让这条"编码守卫"因为 `D:/deliver/compete`
    里某个 markdown 文件没跟上套件数而变红，红得**毫无关系**，看的人会去查错地方。

    加 `PYTHONIOENCODING=gbk` 是为了**可控** —— 否则这条只在"本机 locale 恰好是 GBK"时才红，
    换台机器就成了永远绿的装饰品。
    """
    _rc, text = _run(TOOL, encoding_env="gbk")
    assert "UnicodeEncodeError" not in text, (
        "重定向时工具自己崩了（假红）—— 而 CLAUDE.md §5 推荐的正是重定向到文件这条跑法：\n"
        + text[-800:])
    assert text.strip(), "重定向后一个字都没读到 —— 输出没落地，等于没检查"


def test_without_the_utf8_fix_the_redirect_guard_goes_red():
    """② 自证伪：删掉 `_utf8_stdout()` 的调用 → ① 必须变红。

    变异目标是**源码里那一行调用**（不是函数定义 —— 留着定义但没人调，正是"看起来修了其实没修"）。
    """
    src = TOOL.read_text(encoding="utf-8")
    needle = "def main() -> int:\n    _utf8_stdout()\n"
    assert needle in src, "变异目标串不在 tools/check_number_consistency.py 里（守卫已改？）"
    MUTANT.write_text(src.replace(needle, "def main() -> int:\n", 1), encoding="utf-8")
    try:
        rc, text = _run(MUTANT, encoding_env="gbk")
        assert rc != 0, "删掉 UTF-8 修正后仍然 exit 0 —— 那①根本没在守东西"
        assert "UnicodeEncodeError" in text, (
            "变异体失败的原因不是编码崩溃，那①守的就不是它以为的那件事：\n" + text[-500:])
    finally:
        MUTANT.unlink(missing_ok=True)


def test_self_check_mode_still_catches_a_wrong_number():
    """③ 工具自带的自证伪入口（注入错数必须被抓到、且能精确回填）此前**只写在 CLAUDE.md 里**。

    `--self-check` 会往 `output/_selfcheck_number.md` 写一份假文件再删掉；写盘失败要显式报出来，
    不然它静默变成永远绿（"生成物不许留在证据目录里冒充素材"的同一类问题）。
    """
    rc, text = _run(TOOL, "--self-check", encoding_env="gbk")
    assert rc == 0, f"自证伪模式自己红了（扫描器失去可证伪性？）：\n{text[-800:]}"
    assert "自证伪结论" in text and "✅" in text, f"没看到自证伪结论：\n{text[-500:]}"
    assert not (ROOT / "output" / "_selfcheck_number.md").exists(), "自证伪的临时文件没清掉"


def test_current_numbers_are_not_drifted():
    """④ 当前态不许有漂移（正常模式 exit 0）。

    这是把「数字口径单一来源」从**文档纪律**变成**每次跑测试都强制**的那一条。
    最常见的触发：刚加了测试 → 真实套件数变了 → 交付物里的旧数字没跟上。
    修法（工具自己的 --fix，按列精确回填、不碰引用/历史行）：
        env -u PYTHONPATH .venv/Scripts/python.exe tools/check_number_consistency.py --fix

    ⚠️ 只跑 `--numbers-only`（2026-09-24 改）：本用例的主题是**回归项数漂移**，
    而通道 B 守的是**别的**东西（对外稿的误报宣称是否条件化，见 ⑤）。混跑的话，
    通道 B 一红这里也红，看的人会去查错地方 —— 与 ① 只断言编码是同一条纪律。
    """
    require_delivery("*.md",
                     note="口径闸扫的对象是交付区文档（DELIVER/*.md）⇒ 没挂交付区时本闸没有对象")
    rc, text = _run(TOOL, "--numbers-only", encoding_env="gbk")
    assert rc == 0, (
        "数字口径出现漂移 —— 跑一次 --fix 再提交：\n"
        "    env -u PYTHONPATH .venv/Scripts/python.exe tools/check_number_consistency.py --fix\n"
        + text[-1200:])
    assert "无漂移" in text, f"没看到一致性结论：\n{text[-800:]}"


# ---------------------------------------------------------------------------
# 通道 B（HANDOFF-to-cc.md §2 的 ④）：对外稿里的「误报低」必须**条件化**
# ---------------------------------------------------------------------------
#: 与 tools/check_number_consistency.py 顶部同一个常量、同一种写法（绝对路径）——
#: 副本要放进仓库内，但**源文件在交付区**，这正是本通道要守的东西。
DELIVER = pathlib.Path("D:/deliver/compete")
SCRIPT = DELIVER / "loreal-video-script.md"
DECK = DELIVER / "loreal-ppt" / "build_deck.py"
FPR_MUTANT = ROOT / "output" / "_fpr_mutant"


def test_fpr_claims_are_conditional():
    """⑤ 通道 B 必须绿：PPT 源 / 口播稿 / 页面文案里每一处「误报低」都得**同一段**给出对抗数字。

    为什么必须有这条：④ 那一轮改的是**散文**（PPT 源、口播稿、README）。
    散文没有守卫 ⇒ 下一次有人把前提删掉（或者新写一页 PPT 又只讲"误报低"），
    谁都不会知道。"红"必须由会红的测试固定。
    """
    require_delivery(SCRIPT.name, "loreal-ppt/build_deck.py",
                     note="通道 B 的对象是交付区的口播稿与 PPT 源")
    rc, text = _run(TOOL, "--fpr-conditional", encoding_env="gbk")
    assert rc == 0, "对外稿里出现**无条件**的「误报低」宣称：\n" + text[-2000:]
    assert "已条件化" in text, f"没看到通道 B 的结论：\n{text[-800:]}"
    assert "命中「误报低」声称 0 处" not in text, (
        "一处都没扫到 —— 路径变了或写法变了，这条守卫已经等于没检查：\n" + text[-800:])


def test_fpr_guard_goes_red_when_the_fix_is_reverted():
    """⑥ 自证伪：把 ④ 加的对抗条款从**副本**里抽掉 → 通道 B 必须变红，且只点名口播稿。

    两跑：先跑没动过的副本（必须绿）—— 否则这条在"`--fpr-targets` 一律返回 1"时也会绿，是假绿。
    变异判据自带断言：副本里必须真找到那几行含 `69.7` 的对抗条款，否则变异没发生、
    而"没发生"同样会绿 —— 那是本文件 ① 一模一样要防的假红/假绿。
    """
    import shutil

    if not (SCRIPT.exists() and DECK.exists()):
        import pytest
        pytest.skip(f"对外稿不在盘上（{DELIVER}），本机没挂交付区")

    FPR_MUTANT.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(SCRIPT, FPR_MUTANT / SCRIPT.name)
        shutil.copy2(DECK, FPR_MUTANT / DECK.name)

        rc, text = _run(TOOL, "--fpr-conditional", "--fpr-targets", "output/_fpr_mutant",
                        encoding_env="gbk")
        assert rc == 0, (
            "**没动过**的副本就已经红了 —— `--fpr-targets` 这条路本身有问题，"
            "下面那条自证伪证明不了任何事：\n" + text[-1200:])

        lines = (FPR_MUTANT / SCRIPT.name).read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [l for l in lines if "69.7" not in l]
        assert len(lines) - len(kept) >= 3, (
            f"副本里只找到 {len(lines) - len(kept)} 行含 `69.7` 的对抗条款（应 ≥3）——"
            "变异没发生，这条会假绿：口播稿的 ④ 条款被改写或删除了？")
        (FPR_MUTANT / SCRIPT.name).write_text("".join(kept), encoding="utf-8")

        rc, text = _run(TOOL, "--fpr-conditional", "--fpr-targets", "output/_fpr_mutant",
                        encoding_env="gbk")
        assert rc != 0, "抽掉对抗条款后仍然 exit 0 —— 通道 B 抓不到**自己那处修**，守卫是装饰品"
        assert SCRIPT.name in text, f"红了，但没点名口播稿副本：\n{text[-1200:]}"
        assert DECK.name not in text, (
            "**没动过**的 `build_deck.py` 副本也被判红了 —— 判据把两个文件混在一起了：\n"
            + text[-1200:])
    finally:
        shutil.rmtree(FPR_MUTANT, ignore_errors=True)


# ---------------------------------------------------------------------------
# ⑦⑧：`--fix` 回填时的**行尾**（真伤现场见 tools/check_number_consistency.py 的 apply_fix docstring）
#
# 为什么单独两道：④ 只断言"没有漂移"，而漂移是**数字**层面的事。行尾是**字节**层面的事 ——
# 上一版把纯 LF 的交付区稿子整体写成了 CRLF，④ 照样绿、`--fix` 照样打印"✅ 无漂移"。
# 一条只看得见自己关心那件事的检查是好事；但没人检查的那件事，就真的没人看。
# ---------------------------------------------------------------------------
def _load_tool(path: pathlib.Path = TOOL):
    """把工具当模块载入（`tools/` 不是包）—— ⑦⑧ 要直接调 `scan()` / `apply_fix()`。

    只做模块级导入：`main()` 在 `if __name__ == "__main__"` 之下，不会被跑起来。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("_numcheck_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: 探针文件：**纯 LF** 写、用完删。放 output/ 下，不进 tests/ 的收集面。
LF_PROBE = ROOT / "output" / "_numfix_lf_probe.md"
FIX_MUTANT = ROOT / "output" / "_mutant_numfix.py"
PROBE_TEXT = "全量 999 passed，覆盖率 90%。\n"


def test_fix_keeps_line_endings_of_a_lf_file():
    """⑦ `--fix` 回填数字时**不许动行尾**。

    判据逐字节：回填后 CR 数还是 0，并且新数字真的写进去了。
    不拿"它打印了什么"当证据 —— 上一个版本正是打印一切正常、同时把文件整体换成 CRLF。
    """
    mod = _load_tool()
    LF_PROBE.parent.mkdir(parents=True, exist_ok=True)
    # 探针自己也得走 bytes：本机文本模式写会把它变 CRLF，这条用例就成了永远绿。
    LF_PROBE.write_bytes(PROBE_TEXT.encode("utf-8"))
    try:
        drift, _q, _h, hits, recs = mod.scan([LF_PROBE], 700)
        assert len(drift) == 1 and hits == 1, (
            f"探针没被判成漂移 —— 用例前提不成立（drift={drift} hits={hits}）")
        assert mod.apply_fix(recs, 700) == 1, "回填处数不对（应为 1）"
        raw = LF_PROBE.read_bytes()
        cr = raw.count(b"\r")
        assert cr == 0, (
            f"回填把**纯 LF** 文件写成了 CRLF（CR={cr}）—— 多出来的字节数正好等于行数，"
            "交付区稿子会在没人注意的情况下整体换行尾，而 --fix 的打印里一个字都不会提")
        assert b"700 passed" in raw, f"数字没真的回填进去：{raw!r}"
    finally:
        LF_PROBE.unlink(missing_ok=True)


def test_fix_guard_goes_red_if_the_bytes_fix_is_reverted():
    """⑧ 自证伪：把 `apply_fix` 换回 `read_text/write_text` → ⑦ 的判据必须变红。

    变异**读侧写侧一起改**（只改写侧，文本模式照样会把行尾改掉；但读侧拿不到原始字节，
    "原样带过"的语义就没了 —— 变异不完整，自证伪本身失真）。
    两处替换各自断言命中：否则"变异没发生"同样会让这条绿。
    """
    if os.linesep != "\r\n":
        pytest.skip(
            "本条的变异体靠 **Windows 文本模式的行尾翻译**（写 `\\n` 落成 CRLF）才抓得到；"
            "POSIX 上文本模式读写不改行尾 ⇒ 谓词为空。这是**平台前提不成立**、不是漏了检查"
            "（2026-10-09 GitHub Actions 首跑实测）。")
    src = TOOL.read_text(encoding="utf-8")
    read_old = '        raw = f.read_bytes()\n        lines = raw.decode("utf-8", errors="replace").splitlines(keepends=True)'
    read_new = '        lines = f.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)'
    write_old = '        f.write_bytes("".join(lines).encode("utf-8"))'
    write_new = '        f.write_text("".join(lines), encoding="utf-8")'
    for s in (read_old, write_old):
        assert s in src, f"变异目标串不在源码里（apply_fix 已改？）：{s!r}"
    mut = src.replace(read_old, read_new, 1).replace(write_old, write_new, 1)
    FIX_MUTANT.parent.mkdir(parents=True, exist_ok=True)
    FIX_MUTANT.write_text(mut, encoding="utf-8")
    try:
        mmod = _load_tool(FIX_MUTANT)
        LF_PROBE.write_bytes(PROBE_TEXT.encode("utf-8"))
        drift, _q, _h, _hits, recs = mmod.scan([LF_PROBE], 700)
        assert drift, "变异体连漂移都扫不出来，这条自证伪证明不了任何事"
        mmod.apply_fix(recs, 700)
        cr = LF_PROBE.read_bytes().count(b"\r")
        assert cr > 0, (
            "把 apply_fix 换回文本模式读写之后，纯 LF 文件**没有**被写成 CRLF ⇒ ⑦ 抓不到这个回归，"
            "它是个装饰品（若是本机语义变了，先查清楚再改判据，别把断言删了）")
    finally:
        FIX_MUTANT.unlink(missing_ok=True)
        LF_PROBE.unlink(missing_ok=True)
