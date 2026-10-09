"""守卫：申诉包导出器（`tools/make_appeal_pack.py`）。

这个导出器一句话概括：**把报告右栏从"一节"变成"可提交的产物"**。
它反过来也是本项目最容易做坏的一处 —— 只要它开始"替报告说好话"，
整条「判定与解释解耦」的设计就烂在这里。所以本文件守的不是功能，是**纪律**：

    ① tier / verdict 只能抄，不能算          → test_tier_is_copied_verbatim_not_recomputed
    ② 同一份报告导出两次必须逐字节相同        → test_two_exports_are_byte_identical
    ③ **右栏被抽掉就必须拒发**（本文件的核心）  → test_refuses_when_right_column_is_stripped
    ④ 拒发判据本身不能是"永远拒发"           → test_the_refusal_guard_is_not_vacuous
    ⑤ 右栏原文必须逐字搬运，不许润色          → test_benign_original_text_is_carried_verbatim
    ⑥ 包自己不许下结论                       → test_the_pack_never_asserts_a_verdict_of_its_own
    ⑦ 包必须仍是纯文本包（不含图像字节）       → test_the_pack_contains_no_image_bytes
    ⑧ 无时钟、无网络（AST 扫描，非子串）      → test_the_exporter_has_no_clock_and_no_network
    ⑨ 规则表指纹三处实现必须同值              → test_three_rules_fingerprint_implementations_agree

③ 与 ④ 是一对：③ 单独存在时，一个"无论如何都抛异常"的实现也能让它变绿 ——
那正是 CLAUDE.md 说的「一条不会红的检查等于没有检查」的镜像形态
（不会绿 = 不是检查）。④ 就是钉住这一点的自证伪。
"""

from __future__ import annotations

import ast
import datetime
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samples import make_p2_suite as P2                                    # noqa: E402
from tools.make_appeal_pack import (                                       # noqa: E402
    FORBIDDEN_OWN_CLAIMS, OWN_SENTENCES, AppealRefused, attribution_covers_evidence,
    build_appeal_pack, own_text_violations, refusal_reason,
)
from voiceguard.core.contract import ContentItem                           # noqa: E402
from voiceguard.guard import analyze_content                               # noqa: E402

TOOL = ROOT / "tools" / "make_appeal_pack.py"

#: 包内应有的文件（顺序即写入顺序）。多一个少一个都要红。
EXPECTED_FILES = ["files/report.json", "files/report.md",
                  "APPEAL.md", "appeal.json", "SHA256SUMS.txt"]


# ---------------------------------------------------------------- 夹具

@pytest.fixture(scope="session")
def _real(tmp_path_factory) -> dict:
    """跑一份**真实**报告（不是手搓字典）。

    为什么坚持跑真的：手搓的报告 JSON 里字段名对不对、`explains` 到底是不是空、
    `label` 是不是被 `as_dict()` 补过 —— 全靠猜。③ 的教训是
    「命令跑过 ≠ 验证过，假通过要核对被判定的字段确有值」，
    这里同理：夹具里断言一次"左栏非空、右栏非空"，否则下面大半测试是空转。

    工况取 ③ 记录在案的那条边界（尺寸不变的原位 q75 重压缩）：
    它同时具备本文件需要的最坏形态 —— 左栏 3 条坐标级证据，右栏只有一条
    `NO_TEXT_PROVIDED` 且 `explains` 为空（即"右栏有内容，但不解释这些坐标"）。
    """
    out = tmp_path_factory.mktemp("appeal_pack")
    base = P2.load_base()
    img = out / "inplace_q75.jpg"
    Image.fromarray(np.asarray(P2.jpeg_roundtrip(base, 75)).astype(np.uint8)).save(
        img, "JPEG", quality=93)
    report = analyze_content(ContentItem(image_path=str(img), meta={"id": "appeal-fixture"}),
                             outdir=out, tag="item", sheet=False)
    js = out / "item_report.json"
    assert js.exists(), "analyze_content 没落盘报告 JSON —— 夹具本身失效了"

    d = json.loads(js.read_text(encoding="utf-8"))
    #: 夹具自检：这一条不成立，下面所有"拒发/覆盖"类断言都在空转。
    assert d["evidence"], "夹具工况左栏为空 —— 与 ③ 记录的边界不符，请换工况"
    assert d["benign"], "夹具工况右栏为空 —— 那是在测另一件事"
    return {"report": d, "image": img, "json_path": js,
            "copies": {"files/report.json": js.read_bytes(),
                       "files/report.md": (out / "item_report.md").read_bytes()}}


def _build(_real, report: dict | None = None, image=None) -> dict[str, bytes]:
    return build_appeal_pack(report if report is not None else _real["report"],
                             copies=_real["copies"], image=image,
                             rules_fp="0" * 16)       # 指纹给死值 ⇒ 测试与规则表内容解耦


# ---------------------------------------------------------------- ① 只抄不算

def test_tier_is_copied_verbatim_not_recomputed(_real):
    """包里的 `verdict` 必须与报告里的 `verdict` **完全相等**。

    有意用 `==` 比整块，而不是只比 `tier`：只比等级的话，
    一个"重新判一遍恰好同等级、但 `fired` 换了规则"的实现也能通过 ——
    而那正是"悄悄地又判了一次"。`signals_used` 里的实测数字同样在比对范围内。
    """
    files = _build(_real)
    manifest = json.loads(files["appeal.json"].decode("utf-8"))
    assert manifest["verdict"] == _real["report"]["verdict"], (
        "包里的 verdict 与原报告不一致 —— 导出器动了判定面（铁律 1/3）")
    assert manifest["benign"] == _real["report"]["benign"], (
        "包里的右栏不是原报告的右栏 —— 是否被改写/补全了？")
    # 反向自证伪：夹具的 verdict 确实非空，否则上面两条是拿空比空。
    assert manifest["verdict"]["fired"], "夹具 verdict.fired 为空 ⇒ 上面的比对是空转"


# ---------------------------------------------------------------- ② 逐字节可复算

def test_two_exports_are_byte_identical(_real):
    """同一份报告导出两次，每个文件都必须逐字节相同。

    这是「不含自有时间戳」的**可执行定义**：只要实现里出现 `datetime.now()`，
    两次导出的字节就不该一样。

    ⚠️ **两次导出之间必须真的隔开一个时钟刻度** —— 这条不是仪式，是本测试能不能红的
    分界线。实测（2026-09-25，Windows 11）：**连调 2000 次 `datetime.now()` 只得到 2 个
    不同取值**，相邻两次相隔 1.1 µs 却完全相同 —— 本平台时钟粒度约 15.6 ms。
    不 sleep 的话，一个植入了 `datetime.now()` 的实现会在这里**绿着过去**：
    这正是「一条不会红的检查等于没有检查」。`sleep` 的 60 ms 是为此付的费。
    """
    a = _build(_real)
    t0 = datetime.datetime.now()
    time.sleep(0.06)                      # > 4 个 Windows 时钟刻度（~15.6 ms）
    b = _build(_real)
    t1 = datetime.datetime.now()

    # 自证伪：先证明「这段时间里时钟确实走动了」。否则 sleep 若被优化掉/粒度比预期粗，
    # 下面的逐字节比对就退化成"同一刻导两次"，对时钟型缺陷彻底失去区分力。
    assert t1 != t0, (
        "两次导出之间时钟没有走动 —— 本测试对 `datetime.now()` 型缺陷是瞎的，"
        "先解决时钟粒度，别当成绿")
    assert list(a) == list(b), "两次导出的文件清单不一致"
    for rel in a:
        assert hashlib.sha256(a[rel]).hexdigest() == hashlib.sha256(b[rel]).hexdigest(), (
            f"{rel} 两次导出字节不同 —— 导出器引入了非确定性（时钟？随机？字典序？）")


# ---------------------------------------------------------------- ③ 核心守卫：抽掉右栏必须拒发

def test_refuses_when_right_column_is_stripped(_real):
    """**抽掉一条免责归因原文，必须报错** —— 本文件的核心。

    判据走 `refusal_reason()`（唯一实现），再断言 `build_appeal_pack()` 真的拦住。
    两层都要断：光有判据不拦是死代码，光会拦而无判据就没法自证伪。
    """
    stripped = json.loads(json.dumps(_real["report"]))      # 深拷贝
    stripped["benign"] = []
    assert stripped["evidence"], "抽右栏前左栏已空 —— 这条测试没意义"

    reason = refusal_reason(stripped)
    assert reason is not None and "右栏" in reason, (
        "左栏非空 + 右栏为空时 `refusal_reason()` 没给出拒发理由 —— "
        "一份只有可疑坐标、没有归因的申诉材料是反效果")
    with pytest.raises(AppealRefused):
        build_appeal_pack(stripped, copies=_real["copies"], rules_fp="0" * 16)


def test_the_refusal_guard_is_not_vacuous(_real):
    """**自证伪**：把右栏放回去，同一条判据必须放行、包必须出得来。

    没有这一条，`test_refuses_when_right_column_is_stripped` 会被一个
    "无论如何都抛异常"的实现骗过去（不会绿 = 不是检查）。
    另外顺带钉住：判据是**条件式**的 —— 左栏也空时（本次未发现可疑证据）允许生成。
    """
    assert refusal_reason(_real["report"]) is None, (
        "原样报告被拒发了 —— 判据过严，正常工况出不了材料")
    files = _build(_real)
    assert "APPEAL.md" in files

    clean = json.loads(json.dumps(_real["report"]))
    clean["evidence"] = []          # 左右皆空 = 未发现可疑证据，应可生成
    assert refusal_reason(clean) is None, (
        "左栏为空时仍拒发 —— 未触发风险的工况也该能出材料（材料里会写明未发现）")


def test_refuses_when_an_attribution_has_no_original_text(_real):
    """右栏只剩一个代码、没有原文 ⇒ 拒发；补回原文 ⇒ 放行（自带自证伪）。

    `label` 与 `detail` 双空才算"没有原文"：`label` 单空是合法状态
    （`ledger.py:115` 会用 `BENIGN_CODES[code]` 兜），不能误伤。
    """
    bad = json.loads(json.dumps(_real["report"]))
    for b in bad["benign"]:
        b["detail"], b["label"] = "", ""
    assert refusal_reason(bad) is not None, "右栏只有代码时没拒发"
    with pytest.raises(AppealRefused):
        build_appeal_pack(bad, copies=_real["copies"], rules_fp="0" * 16)

    # 自证伪：只把 label 补回来仍应拒发吗？—— 不，label 有原文即够（与 ledger 的兜底一致）。
    partly = json.loads(json.dumps(bad))
    for b in partly["benign"]:
        b["label"] = "某条归因原文"
    assert refusal_reason(partly) is None, (
        "label 有原文却被判成'没有原文' —— 判据过严，会误伤 label 单空这条合法状态")


# ---------------------------------------------------------------- 原图核对

def test_refuses_when_the_image_is_not_the_judged_one(_real, tmp_path):
    """申诉的必须是**被核验的那张图**。

    换一张同尺寸的图 ⇒ 指纹不同 ⇒ 拒发。这条防的是最普通也最致命的一类申诉事故：
    材料里描述的问题与平台手里的图对不上号。
    """
    other = tmp_path / "other.jpg"
    Image.fromarray(np.asarray(P2.load_base()).astype(np.uint8)).save(other, "JPEG", quality=95)
    with pytest.raises(AppealRefused):
        build_appeal_pack(_real["report"], copies=_real["copies"], image=other,
                          rules_fp="0" * 16)

    # 正例：给对了图 ⇒ verified=True，且如实写进材料。
    files = _build(_real, image=_real["image"])
    manifest = json.loads(files["appeal.json"].decode("utf-8"))
    assert manifest["original_image"]["verified"] is True
    assert manifest["original_image"]["sha256"] == _real["report"]["fingerprint"]

    # 不给图 ⇒ 如实记 None，**不假装核对过**。
    assert json.loads(_build(_real)["appeal.json"].decode("utf-8")
                      )["original_image"]["verified"] is None
    assert "本包没有核对原图" in _build(_real)["APPEAL.md"].decode("utf-8")


# ---------------------------------------------------------------- ⑤ 逐字搬运

def test_benign_original_text_is_carried_verbatim(_real):
    """右栏原文必须**逐字**出现在材料里 —— 申诉包不许替报告润色。

    只断言"包含"而不比对全文措辞，是因为材料里确实有本工具自己写的话；
    但**每一条归因的 detail 与 label** 都必须原样在场，一个字都不能改。
    """
    md = _build(_real)["APPEAL.md"].decode("utf-8")
    for b in _real["report"]["benign"]:
        for key in ("detail", "label"):
            text = (b.get(key) or "").strip()
            if text:
                assert text in md, f"右栏 {b.get('code')} 的 {key} 没有逐字出现在材料里：{text}"


def test_attribution_coverage_is_reported_honestly(_real):
    """右栏"有内容"不等于"解释了那些坐标" —— 材料必须如实说出这个区别。

    ③ 记在案的那条边界上，左栏 3 条坐标级证据、右栏却是 `NO_TEXT_PROVIDED`
    （`explains` 为空）。材料若把右栏当成"对这些坐标的解释"，就是替系统撒谎。
    """
    assert attribution_covers_evidence(_real["report"]) is False, (
        "夹具工况的右栏竟然覆盖了左栏坐标 —— 工况变了，请重取本测试的预期")
    md = _build(_real)["APPEAL.md"].decode("utf-8")
    assert "归因覆盖提示" in md and "没有一条指向" in md

    # 自证伪：让某条归因真的 explains 一条证据坐标 ⇒ 该提示必须消失（不是写死的）。
    covered = json.loads(json.dumps(_real["report"]))
    covered["benign"][0]["explains"] = [covered["evidence"][0]["id"]]
    assert attribution_covers_evidence(covered) is True
    assert "归因覆盖提示" not in _build(_real, report=covered)["APPEAL.md"].decode("utf-8")

    # 左栏为空 ⇒ 无可归因之物，返回 True（不能凭空报"没覆盖"）。
    empty = json.loads(json.dumps(_real["report"]))
    empty["evidence"] = []
    assert attribution_covers_evidence(empty) is True


# ---------------------------------------------------------------- ⑥ 包自己不下结论

def test_the_pack_never_asserts_a_verdict_of_its_own(_real):
    """包只许引用，不许下判（铁律 3）。

    判据只扫**本工具自己写的句子**（`OWN_SENTENCES`）：报告原文里本来就有
    「拼接」「伪造」等词（规则描述、法条原文），扫整份 md 会把"如实引用"
    误判成"越权下判"。
    """
    assert own_text_violations() == [], (
        f"自有措辞里出现越权断言：{own_text_violations()}")
    for s in OWN_SENTENCES:
        assert not any(w in s for w in FORBIDDEN_OWN_CLAIMS)

    files = _build(_real)
    blob = files["APPEAL.md"].decode("utf-8") + files["appeal.json"].decode("utf-8")
    for s in OWN_SENTENCES:
        assert s in blob, f"自有声明没出现在材料里（被删了？）：{s}"


# ---------------------------------------------------------------- ⑦ 纯文本包

def test_the_pack_contains_no_image_bytes(_real):
    """包必须是纯文本包 —— 少一处图像副本就少一处泄露面（铁律 4）。

    同时钉住文件清单：多出一个 `.png`/`.jpg` 就是有人往里塞了图。
    """
    files = _build(_real)
    assert list(files) == EXPECTED_FILES, f"包内文件清单变了：{list(files)}"
    for rel, blob in files.items():
        assert Path(rel).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"), (
            f"{rel} 是图像文件 —— 申诉包不许含图像字节")
        assert not blob.startswith(b"\x89PNG") and not blob.startswith(b"\xff\xd8\xff"), (
            f"{rel} 的内容是图像字节（只看了后缀是不够的）")
    manifest = json.loads(files["appeal.json"].decode("utf-8"))
    assert manifest["contains_image_bytes"] is False


def test_sha256sums_covers_exactly_the_other_files(_real):
    """`SHA256SUMS.txt` 必须覆盖除自己以外的每一个文件，且哈希对得上。

    它是"交付时是哪几个字节"的唯一索引 —— 漏一个文件，收材料的人就无从核对那一个。
    """
    files = _build(_real)
    lines = files["SHA256SUMS.txt"].decode("utf-8").strip().splitlines()
    listed = {}
    for ln in lines:
        h, rel = ln.split("  ", 1)
        listed[rel] = h
    assert set(listed) == set(files) - {"SHA256SUMS.txt"}, (
        f"SHA256SUMS 覆盖的文件集合不对：{sorted(listed)}")
    for rel, h in listed.items():
        assert hashlib.sha256(files[rel]).hexdigest() == h, f"{rel} 的哈希与内容不符"


# ---------------------------------------------------------------- ⑧ 无时钟无网络

_BANNED_IMPORTS = {"time", "datetime", "requests", "urllib", "socket", "http",
                   "httpx", "openai", "dashscope", "random"}
_BANNED_CALLS = {"datetime.now", "datetime.utcnow", "datetime.today", "time.time",
                 "time.monotonic", "time.perf_counter", "random.random", "random.choice",
                 "os.urandom"}


def _impurities(src: str) -> list[str]:
    """扫源码里的**时钟与网络**。这是判据的单一实现（绿断言与自证伪共用）。

    用 AST 而不是子串扫描：本工具的模块 docstring 里**正写着**「本工具若取
    `datetime.now()`…」这句话 —— 子串扫描会把这段说明文当成违规（实测踩过）。
    子串分不清「散文」和「代码」，AST 天生分得清：docstring 是 `ast.Constant`，
    真的调用是 `ast.Attribute` 节点。这不是洁癖，是判据要么有区分力、要么没有。
    """
    tree = ast.parse(src)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names
                      if a.name.split(".")[0] in _BANNED_IMPORTS]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] in _BANNED_IMPORTS:
                found.append(f"from {node.module} import ...")
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and f"{node.value.id}.{node.attr}" in _BANNED_CALLS):
            found.append(f"{node.value.id}.{node.attr}()")
    return sorted(set(found))


def test_the_exporter_has_no_clock_and_no_network():
    """结构性检查：导出器源码里不许出现时钟与网络。

    为什么扫源码而不是"跑一遍看结果"：`datetime.now()` 可能只在某条分支里被调用
    （比如"生成时间"那一行只在有图时写），跑一遍根本碰不到。
    源码扫描也不给它藏的机会 —— 前提是判据本身有区分力，所以下一段先证伪。
    """
    # 自证伪：这段合成源码**必须**被扫出来。扫不出来的判据 = 没有判据。
    planted = ("import time\nfrom urllib import request\n"
               "def f():\n    return datetime.now() if False else time.time()\n")
    hits = _impurities(planted)
    assert "import time" in hits and "from urllib import ..." in hits, (
        f"判据扫不出植入的违规（只扫到 {hits}）—— 这条检查不会红")
    assert "time.time()" in hits

    bad = _impurities(TOOL.read_text(encoding="utf-8"))
    assert bad == [], (
        f"导出器里出现 {bad} —— 申诉包必须无时钟（逐字节可复算）、无网络（离线可跑）")


# ---------------------------------------------------------------- ⑨ 指纹单一来源

def test_three_rules_fingerprint_implementations_agree():
    """规则表指纹有**三处**实现，它们必须同值。

    第三处（`voiceguard/rules/engine.py:rules_fingerprint`）是 2026-09-25 为申诉包新增的；
    前两处（`experiments/red_team_bench.py:45 rules_digest()`、
    `samples/make_red_team.py:49 sha16()`）**保持原样不动** —— 它们的结果已冻进
    `samples/red_team/red_team.json:11` 与红队物证（铁律 10：无指令不动无关代码）。

    既然不合并，就必须**证明它们等价**。这条检查是那次"不合并"的代价，也是它的保险：
    任何一处改了算法，这里当场变红。
    """
    from experiments.red_team_bench import RULES_YAML as RB_RULES, rules_digest
    from samples.make_red_team import sha16
    from voiceguard.rules.engine import DEFAULT_RULES, rules_fingerprint

    assert RB_RULES == DEFAULT_RULES, "红队基准与规则引擎指向的不是同一份 rules.yaml"
    vals = {"engine.rules_fingerprint": rules_fingerprint(),
            "red_team_bench.rules_digest": rules_digest(),
            "make_red_team.sha16": sha16(DEFAULT_RULES)}
    assert len(set(vals.values())) == 1, f"三处指纹实现不同值：{vals}"

    # 自证伪：变一个字节，指纹必须跟着变（否则它根本不是一个指纹）。
    mutated = ROOT / "output" / "_appeal_fp_mutant.yaml"
    mutated.parent.mkdir(parents=True, exist_ok=True)
    mutated.write_bytes(DEFAULT_RULES.read_bytes() + b"\n")
    try:
        assert rules_fingerprint(mutated) != rules_fingerprint(), (
            "改了一个字节指纹却没变 —— 这不是指纹")
    finally:
        mutated.unlink(missing_ok=True)
