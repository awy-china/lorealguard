"""规则层测试 —— 三条纪律必须被锁住。

    ① 阈值只在 YAML 里（代码里一个阈值都不许有）；
    ② 信号缺失 → 不命中（宁可"没判"，不许拿缺失当 0 分触发风险）；
    ③ 规则写错 → 收集成错误，不许静默跳过、也不许把整条链路打崩。
"""

from __future__ import annotations

import pytest

from voiceguard.core.contract import resolve_source
from voiceguard.rules import compare, describe, evaluate, load_rules
from voiceguard.rules.engine import DEFAULT_RULES, referenced_laws


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def test_rules_file_loads_without_errors(rules):
    info = describe(rules)
    assert info["n_risk"] >= 4, "风险规则太少，分级判定立不起来"
    assert info["n_benign"] >= 4, "免责规则太少，双向账本立不起来"
    assert info["errors"] == [], f"规则表自检报错：{info['errors']}"
    assert info["version"] == 2, "规则表版本号变了就必须显式改这条断言（防无声改判定）"


def test_no_float_thresholds_inside_engine_code():
    """机制与阈值分离：剥掉 docstring 与注释后，engine.py 的**代码**里不许有浮点字面量。"""
    import re
    src = (DEFAULT_RULES.parent / "engine.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)          # 去 docstring
    code = re.sub(r"#[^\n]*", "", code)                        # 去注释
    code = re.sub(r"'(?:[^'\\]|\\.)*'", "''", code)             # 去单引号字符串
    code = re.sub(r'"(?:[^"\\]|\\.)*"', '""', code)             # 去双引号字符串
    floats = re.findall(r"\b\d+\.\d+\b", code)
    assert floats == [], f"engine.py 代码里出现阈值 {floats} —— 阈值应写在 rules.yaml"


def test_compare_operators():
    assert compare(0.61, ">=0.6") is True
    assert compare(0.59, ">=0.6") is False
    assert compare(3, ">2") is True
    assert compare(True, "==true") is True
    assert compare(False, "!=true") is True
    assert compare("元数据被剥离", "==元数据被剥离") is True
    assert compare("有AI标识", "!=有AI标识") is False


def test_missing_signal_never_fires(rules):
    """核心防误报纪律：信号缺失一律不命中，不许把 None 当 0 分。"""
    assert compare(None, ">=0.6") is False
    assert compare(None, "==true") is False
    assert compare(None, "!=true") is False

    hits = evaluate({"provenance.ai_label_found": None, "forensics.splice_score": None},
                    rules, "risk")
    assert hits == [], f"缺信号却命中了风险规则：{[h.rule_id for h in hits]}"


def test_declared_ai_label_is_info_not_violation(rules):
    hits = evaluate({"provenance.ai_label_found": True}, rules, "risk")
    ids = {h.rule_id for h in hits}
    assert "R-AI-LABEL-DECLARED" in ids
    top = max(hits, key=lambda h: h.tier.rank)
    assert top.tier.value == "提示级", "已声明 AI 内容不是违规"


def test_hard_risk_requires_cross_layer_evidence(rules):
    """第十条（抹标识）要件严格：必须①元数据三态判为"被剥离" ②像素层给出生成痕迹。"""
    only_state = evaluate({"provenance.state": "元数据被剥离"}, rules, "risk")
    assert all(h.rule_id != "R-AI-MARK-STRIPPED" for h in only_state), \
        "只有元数据缺失就判高风险 = 会冤枉平台二次压缩的图"

    both = evaluate({"provenance.state": "元数据被剥离", "forensics.ai_trace_score": 0.91},
                    rules, "risk")
    ids = {h.rule_id for h in both}
    assert "R-AI-MARK-STRIPPED" in ids


def test_splice_without_efficacy_claim_is_watch_not_high(rules):
    """误报护栏：检出拼接形态本身不等于违规，必须叠加『功效语境 + 对比语境』才升级。"""
    sig = {"forensics.ela.reliable": True,
           "forensics.ela.n_brighter_regions": 1, "forensics.ela.brighter_z_peak_max": 16.56}
    plain = evaluate(sig, rules, "risk")
    top = max(plain, key=lambda h: h.tier.rank)
    assert top.tier.value == "关注级"
    assert any(h.rule_id == "R-SPLICE-SUSPECT-REVIEW" for h in plain)

    # 只有功效词、不在对比语境 → 仍不升级（2026-09-19 新增的第三道闸门）
    only_claim = evaluate({**sig, "text.efficacy_claim": True}, rules, "risk")
    assert max(h.tier.rank for h in only_claim) == top.tier.rank, \
        "拼接 + 功效词但无对比语境 → 不许升级为虚假宣传（拼版/日常记录会被冤枉）"

    # 功效 + 对比语境（两个信号同时成立）→ 才升级
    full = evaluate({**sig, "text.efficacy_claim": True, "text.before_after_context": True},
                    rules, "risk")
    assert max(h.tier.rank for h in full) > top.tier.rank


def test_area_weighted_score_alone_does_not_trigger(rules):
    """回归锁：面积加权分 splice_score 不再作为触发条件。

    实测教训：它把「区域多大」混进「有多可疑」，8.8% 面积的跨源拼接只得 0.586，
    卡在 0.6 阈值下 → 报告出现「左栏有证据、判定说未触发」的自相矛盾。
    现在触发条件必须是坐标级证据本身。
    """
    hits = evaluate({"forensics.splice_score": 0.99, "forensics.ela.n_brighter_regions": 0,
                     "forensics.ela.brighter_z_peak_max": 0.0}, rules, "risk")
    assert all(h.rule_id != "R-SPLICE-SUSPECT-REVIEW" for h in hits)


def test_brighter_z_below_floor_does_not_trigger(rules):
    """有坐标级证据但峰值 z 未过下界（6）→ 不升级，只留在左栏等人工看。"""
    hits = evaluate({"forensics.ela.n_brighter_regions": 1,
                     "forensics.ela.brighter_z_peak_max": 4.5}, rules, "risk")
    assert all(h.rule_id != "R-SPLICE-SUSPECT-REVIEW" for h in hits)


def test_benign_rules_emit_codes(rules):
    hits = evaluate({"forensics.ela.darker_area_ratio": 0.02}, rules, "benign")
    assert any(h.code == "BEAUTY_RETOUCH" for h in hits)

    hits = evaluate({"forensics.ela.jpeg_history": False}, rules, "benign")
    assert any(h.code == "NO_JPEG_HISTORY" for h in hits)


def test_platform_recode_suppresses_stripped_attribution(rules):
    """平台重压缩会解释掉"元数据缺失" → 不得归因恶意抹除。"""
    hits = evaluate({"provenance.strip_candidate": True,
                     "forensics.ela.platform_recompressed": True}, rules, "benign")
    assert any(h.code == "PLATFORM_STRIP" for h in hits)


def test_rule_hits_record_measured_values(rules):
    hits = evaluate({"forensics.ela.reliable": True, "forensics.ela.n_brighter_regions": 1,
                     "forensics.ela.brighter_z_peak_max": 16.56}, rules, "risk")
    hit = next(h for h in hits if h.rule_id == "R-SPLICE-SUSPECT-REVIEW")
    assert hit.signals_used.get("forensics.ela.brighter_z_peak_max") == 16.56


def test_unreliable_detector_never_escalates(rules):
    """回归锁（P2 样本 b03 误报）：检测器不可靠时，它的区域结论不参与升级。

    实测：正常图经平台二次压缩（短边 1080 + q60）后，ELA 产出 4 个 z_peak 高达 **32.8** 的
    「可疑区域」，其实是重压缩伪影。检测器只把 score 降权到 0.3，**区域列表没降权**；
    而规则改用坐标级证据触发后就绕过了那道降权 → 冤枉了一张正常图。
    现在由 reliable 前置条件拦住：不可靠 ⇒ 不升级（宁可漏判）。
    """
    hot = {"forensics.ela.n_brighter_regions": 4, "forensics.ela.brighter_z_peak_max": 32.8}
    assert any(h.rule_id == "R-SPLICE-SUSPECT-REVIEW"
               for h in evaluate({**hot, "forensics.ela.reliable": True}, rules, "risk"))
    assert all(h.rule_id != "R-SPLICE-SUSPECT-REVIEW"
               for h in evaluate({**hot, "forensics.ela.reliable": False}, rules, "risk"))
    assert all(h.rule_id != "R-SPLICE-SUSPECT-REVIEW"
               for h in evaluate(hot, rules, "risk")), "缺 reliable 信号时必须不命中（不得默认可信）"


def test_detector_reliable_derivation():
    """可靠性信号的推导口径：无 JPEG 史 或 平台重压缩 → 不可靠。"""
    from voiceguard.guard import detector_reliable

    class _R:
        """模拟一个 **ELA** 结果。

        `detector_reliable` 按**检测器名**分派（`guard.py:96`）：`ela` 走下面两条 JPEG 史判据，
        其余检测器由 `raw["reliable"]` 自报。所以这个辅类必须自报 `name="ela"` —— 否则
        四条判据全被跳过，测的就不是 ELA 的推导口径了（2026-09-25 round11 验收实测抓到）。
        """

        name = "ela"

        def __init__(self, raw):
            self.raw = raw

    assert detector_reliable(_R({"jpeg_history": True, "platform_recompressed": False})) is True
    assert detector_reliable(_R({"jpeg_history": True, "platform_recompressed": True})) is False
    assert detector_reliable(_R({"jpeg_history": False})) is False
    assert detector_reliable(_R({})) is True, "信号缺失时按可控处理，但规则表必须显式要求 ==true"


def test_detector_reliable_for_non_ela_is_self_reported():
    """非 ELA 检测器（含观察级）的可靠性**由它自己 `raw["reliable"]` 自报**，缺省不认账。

    为什么不让 ELA 的两条 JPEG 判据套到光影 / 色彩过渡上：那两个维度根本没有 JPEG 史概念，
    套过去等于用 ELA 的适用性替别的维度背书（`guard.py:89-93`）。

    反向也要锁：ELA **不许**用 `raw["reliable"]` 顶掉它自己的 JPEG 史判据。
    """
    from voiceguard.guard import detector_reliable

    class _R:
        def __init__(self, raw, name):
            self.raw = raw
            self.name = name

    assert detector_reliable(_R({"reliable": True}, "lighting")) is True
    assert detector_reliable(_R({"reliable": False}, "color_edge")) is False
    assert detector_reliable(_R({}, "color_edge")) is False, "缺省不认账（缺 reliable ⇒ False）"
    assert detector_reliable(_R({"reliable": True, "jpeg_history": False}, "ela")) is False, \
        "ELA 的可靠性必须走它自己的判据，不能被 raw['reliable'] 顶掉"


def test_pending_law_sources_are_visible(rules):
    """规则引用的待复核法条必须能被报告层列出来（不许藏在规则内部）。"""
    refs = referenced_laws(rules)
    drafts = [s for s in refs if s.startswith("draft:")]
    assert drafts, "至少应有一项待复核依据（本项目现阶段确实有未核原文的法律）"
    info = describe(rules)
    assert drafts[0] in info["pending_sources"]
    assert info["n_clauses_verified"] >= 8, "已核准原文的条文数"
    # 待复核的出处必须能解析成 verified=False，报告才敢标「依据待复核」
    for s in drafts:
        try:
            ref = resolve_source(s)
        except KeyError as e:
            pytest.fail(f"规则引用了契约里不存在的法条键：{s} ({e})")
        assert ref.verified is False


def test_broken_rule_is_collected_not_crashing():
    bad = {"version": 9, "risk_rules": [
        {"id": "X-BAD-OP", "desc": "未知运算符", "tier": "高风险",
         "when": {"a.b": "~~~1"}, "source": "law:MARK_ART6_1"},
        {"id": "X-NO-WHEN", "desc": "缺 when", "tier": "高风险", "source": "law:MARK_ART6_1"},
    ], "benign_rules": []}
    errors: list[str] = []
    hits = evaluate({"a.b": 1}, bad, "risk", errors)
    assert errors, "坏规则必须被收集成错误"
    assert all(h.rule_id not in ("X-BAD-OP", "X-NO-WHEN") for h in hits)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))