"""F4-A 文案功效宣称层测试 —— 生物科学轴的判定 + 三道保守闸门 + 依据出处纪律。

这一层是**唯一不依赖任何图像**的判定通路，因此它的三道闸门必须被锁死：
    闸门① 下界为 0 的类别（保湿/清洁）永不判不可行 → 生理上确实可即时成立；
    闸门② 弱于「强」的宣称不做时间对照 → 「7 天改善干燥」不许被判不可信；
    闸门③ 没有时间承诺就弃权 → 没有可对照的尺度，本系统不猜。
"""
from __future__ import annotations

from voiceguard.core.contract import UNVERIFIED_LAWS, draft_ref
from voiceguard.semantic import claims


def test_extracts_claim_and_period():
    a = claims.analyze("7天根除痘印")
    codes = {c.code for c in a.claims}
    assert "ACNE_MARK" in codes, f"未抽出痘印类宣称：{codes}"
    assert a.periods and a.periods[0].days == 7
    assert a.strength == "绝对"
    assert a.signals()["text.efficacy_strength_rank"] == 3


def test_strong_claim_with_short_period_is_implausible():
    a = claims.analyze("7天根除痘印")
    assert a.implausible, "强宣称 + 7 天 < 28 天保守下界 → 应判不可行"
    assert a.implausible[0]["promised_days"] == 7
    assert a.implausible[0]["min_plausible_days"] == 28
    s = a.signals()
    assert s["text.efficacy_implausible"] is True
    assert s["text.efficacy_feasible"] is False


def test_gate1_instantaneous_categories_never_implausible():
    """闸门①（最重要的误报护栏）：保湿/清洁类下界为 0，永不触发不可行。"""
    for t in ("即刻补水，一次就水润", "3秒卸干净，清洁力强", "马上舒缓泛红"):
        a = claims.analyze(t)
        assert a.implausible == [], f"把生理上可即时的功效判成不可行 → 冤枉：{t}"
    a = claims.analyze("7天深度补水")
    assert a.implausible == [] and a.feasible is None, "下界为 0 → 不做时间对照"


def test_gate2_weak_claims_skip_timeline_comparison():
    """闸门②：弱/中强度宣称不参与时间对照（不做夸大认定的替身）。"""
    a = claims.analyze("7天改善干燥粗糙")
    assert a.implausible == []
    assert a.feasible is None, "强度不足 → 弃权，而不是判可行"


def test_gate3_no_period_means_abstention():
    """闸门③：没有时间承诺 → 弃权（有宣称但无可对照的尺度）。"""
    a = claims.analyze("彻底祛除色斑，效果显著")
    assert a.strength == "绝对"
    assert a.signals()["text.n_periods"] == 0
    assert a.feasible is None and a.implausible == []
    assert a.signals()["text.promised_period_days"] is None


def test_feasible_when_period_meets_conservative_floor():
    a = claims.analyze("坚持28天淡化细纹")
    assert a.feasible is True
    assert a.implausible == []
    assert "可行" in "；".join(a.notes)


def test_chinese_numeral_and_week_units():
    assert claims.analyze("一周祛痘").signals()["text.promised_period_days"] == 7
    assert claims.analyze("两周淡化痘印").signals()["text.promised_period_days"] == 14
    assert claims.analyze("第7天打卡").signals()["text.promised_period_days"] == 7
    assert claims.analyze("一个月淡斑").signals()["text.promised_period_days"] == 30


def test_empty_text_is_all_abstention():
    a = claims.analyze("")
    assert a.claims == [] and a.periods == [] and a.strength == "无"
    s = a.signals()
    assert s["text.efficacy_claim"] is False
    assert s["text.efficacy_implausible"] is False
    assert s["text.efficacy_feasible"] is None


def test_every_constant_has_plausible_floor_and_basis():
    """表本身的自证：每个类别都要有下界与依据说明，且下界只允许是保守整数。"""
    assert claims.CLAIM_LEXICON, "功效词典不能为空"
    for code, row in claims.CLAIM_LEXICON.items():
        assert len(row) == 4, f"{code} 表结构应为 (名称, 关键词, 下界, 依据)"
        name, kws, days, basis = row
        assert isinstance(days, int) and days >= 0, f"{code} 下界必须是非负整数（保守）"
        assert kws and basis, f"{code} 缺关键词或依据"
        assert isinstance(name, str) and name


def test_physiology_basis_is_declared_unverified():
    """依据出处纪律：生理周期数值未核准到原文 → 报告必须能标『依据待复核』。"""
    assert "COSMETIC_EFFICACY_GUIDE" in UNVERIFIED_LAWS
    assert "SKIN_TURNOVER" in UNVERIFIED_LAWS
    assert draft_ref("COSMETIC_EFFICACY_GUIDE").verified is False
    assert draft_ref("SKIN_TURNOVER").verified is False