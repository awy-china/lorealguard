"""F4 接入后的端到端锁 —— 文案通路 + 图文交叉（全程零大模型）。

本文件锁住的是**同一张图、换文案、判定就变**这件事，也就是细则②要的
「从识别到决策的完整闭环」的最小可见单元：

    图 + 无文案        → 关注级 + 免责 NO_TEXT_PROVIDED
    图 + 图集记录文案   → 关注级 + 免责 NO_EFFICACY_CONTEXT（把拼接嫌疑压回去）
    图 + 功效对比文案   → 高风险（拼接 + 功效 + 对比语境，三者同时成立）
    纯文案（无图）      → 关注级（生物科学轴单独成立）
    纯文案（周期合理）  → 未触发 + 免责 EFFICACY_TIMELINE_PLAUSIBLE
"""
from __future__ import annotations

from pathlib import Path

import pytest

from voiceguard.core.contract import BENIGN_CODES, ContentItem
from voiceguard.guard import analyze_content
from voiceguard.rules import load_rules

ROOT = Path(__file__).resolve().parent.parent
SPLICE = ROOT / "samples" / "fw_splice.jpg"      # 物理真实跨源拼接（非程序合成）
NEUTRAL = ROOT / "samples" / "base_neutral.jpg"  # 程序合成中性图，无个人数据


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def _run(tmp_path, **kw):
    item = ContentItem(meta={"id": "t"}, **kw)
    return analyze_content(item, outdir=tmp_path, tag="t", sheet=False)


# ---------------------------------------------------------------- 纯文案通路

def test_text_only_implausible_claim_is_watch(tmp_path):
    rep = _run(tmp_path, text="7天根除痘印，素颜也能打", kind="copy")
    assert rep.verdict.tier.value == "关注级"
    assert any(h.rule_id == "R-EFFICACY-TIMELINE-IMPLAUSIBLE" for h in rep.verdict.fired)
    assert rep.evidence and rep.evidence[0].layer == "semantic"
    assert rep.evidence[0].sources and rep.evidence[0].sources[0].verified is False, \
        "生理尺度依据未核准原文 → 证据必须带 draft 出处，报告才能标『依据待复核』"
    assert rep.provenance is None, "纯文案输入没有元数据层对象"
    assert "不适用" in next(l["status"] for l in rep.layers if l["layer"].startswith("F1"))


def test_text_only_plausible_claim_is_not_flagged(tmp_path):
    rep = _run(tmp_path, text="坚持28天淡化细纹，配合防晒", kind="copy")
    assert rep.verdict.tier.value != "关注级" and rep.verdict.fired == []
    assert any(b.code == "EFFICACY_TIMELINE_PLAUSIBLE" for b in rep.benign)


def test_strong_claim_without_period_abstains(tmp_path):
    rep = _run(tmp_path, text="彻底祛除色斑，效果肉眼可见", kind="copy")
    assert rep.verdict.fired == [], "无可对照尺度 → 弃权，不许硬判"
    assert any(b.code == "STRONG_CLAIM_NO_PERIOD" for b in rep.benign)


def test_no_input_at_all_raises(tmp_path):
    with pytest.raises(ValueError):
        _run(tmp_path)


# ---------------------------------------------------------------- 图文交叉

@pytest.mark.skipif(not SPLICE.exists(), reason="缺跨源拼接样本 samples/fw_splice.jpg")
def test_splice_with_efficacy_and_before_after_escalates(tmp_path):
    rep = _run(tmp_path, image_path=str(SPLICE), text="7天根除痘印，使用前后对比")
    assert rep.verdict.tier.value == "高风险"
    ids = {h.rule_id for h in rep.verdict.fired}
    assert "R-SPLICE-WITH-EFFICACY-CLAIM" in ids
    assert "R-SPLICE-SUSPECT-REVIEW" in ids and "R-EFFICACY-TIMELINE-IMPLAUSIBLE" in ids
    assert len(rep.evidence) >= 2, "左半栏应同时有像素证据与文案证据"


@pytest.mark.skipif(not SPLICE.exists(), reason="缺跨源拼接样本 samples/fw_splice.jpg")
def test_same_image_without_claim_stays_watch(tmp_path):
    """⭐ 误报护栏的现场证明：同一张图，文案换成日常记录 → 不许升级为虚假宣传。"""
    rep = _run(tmp_path, image_path=str(SPLICE), text="今天去公园拍的花，拼了个九宫格记录一下")
    assert rep.verdict.tier.value == "关注级"
    assert any(b.code == "NO_EFFICACY_CONTEXT" for b in rep.benign)
    assert all(h.rule_id != "R-SPLICE-WITH-EFFICACY-CLAIM" for h in rep.verdict.fired)


@pytest.mark.skipif(not NEUTRAL.exists(), reason="缺中性样图")
def test_missing_text_yields_none_not_false(tmp_path):
    """「没给我文案」≠「文案里没有功效宣称」——两条不同的责任，不许混为一谈。"""
    from voiceguard.semantic import consistency
    res = consistency.analyze(ContentItem(image_path=str(NEUTRAL)), {})
    assert res.signals["text.efficacy_claim"] is None
    assert res.signals["text.has_shopping_link"] is None
    assert res.signals["semantic.text_provided"] is False

    rep = _run(tmp_path, image_path=str(NEUTRAL))
    assert any(b.code == "NO_TEXT_PROVIDED" for b in rep.benign)


# ---------------------------------------------------------------- 契约/规则一致性

def test_new_benign_codes_exist_in_contract(rules):
    for r in rules["benign_rules"]:
        assert r["code"] in BENIGN_CODES, f"规则用了契约里没有的免责码：{r['code']}"


def test_text_signals_are_no_longer_planned():
    from voiceguard.guard import PLANNED_SIGNALS
    for k in ("text.efficacy_claim", "text.has_shopping_link", "text.ad_label_declared"):
        assert k not in PLANNED_SIGNALS, f"{k} 已接入，不该还挂在 PLANNED 里"
    assert "review.bot_score" in PLANNED_SIGNALS, "F5 未接入，应如实标 PLANNED"


def test_rules_selfcheck_clean(rules):
    from voiceguard.rules import describe
    assert describe(rules)["errors"] == []