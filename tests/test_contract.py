"""契约层测试 —— 锁住「双向账本」不会在后续迭代里被悄悄退化。

这一组测试测的不是算法，而是**产品主张的代码形态**：
    ① 免责证据必须与可疑证据同为契约中的一等公民；
    ② 法条必须能区分「已核准原文」与「待复核」，不许混为一谈；
    ③ 能力边界必须显式声明「不做真假终审」。
"""

from __future__ import annotations

import json

import pytest

from voiceguard.core.contract import (
    BENIGN_CODES,
    CLAUSES,
    DEFAULT_BOUNDARIES,
    UNVERIFIED_LAWS,
    BenignEvidence,
    ContentItem,
    Evidence,
    ProvenanceResult,
    ProvenanceState,
    RiskTier,
    SourceRef,
    draft_ref,
    law_ref,
    max_tier,
)


def test_law_clauses_are_verified_and_quoted():
    """已核准条文必须有原文摘句；没核过的不许混进来。"""
    assert CLAUSES, "法规条文表不能为空"
    for key, (article, quote) in CLAUSES.items():
        assert article, f"{key} 缺条号"
        assert len(quote) >= 8, f"{key} 原文摘句太短，不足以支撑引用"
        assert law_ref(key).verified is True


def test_unverified_laws_never_claim_verified():
    """只知存在性、未核原文的法律 → 必须 verified=False（报告里显示"依据待复核"）。"""
    assert UNVERIFIED_LAWS
    for key in UNVERIFIED_LAWS:
        ref = draft_ref(key)
        assert ref.verified is False
        assert "待复核" in ref.display()


def test_display_marks_pending_for_draft_law():
    assert "待复核" not in law_ref("MARK_ART10_TAMPER").display()
    assert "待复核" in draft_ref("COSMETIC_REG").display()


def test_tier_order_and_max():
    assert RiskTier.UNDETERMINED.rank < RiskTier.INFO.rank < RiskTier.WATCH.rank < RiskTier.HIGH.rank
    assert max_tier([RiskTier.INFO, RiskTier.HIGH, RiskTier.WATCH]) is RiskTier.HIGH
    assert max_tier([]) is RiskTier.UNDETERMINED


def test_evidence_carries_coordinates_and_numbers():
    """左半栏的每条证据都必须能回到像素：坐标 + 数字 + 形态。"""
    ev = Evidence(
        id="E1", layer="forensics", kind="ela_region", label="偏亮异常区",
        score=0.87, confidence="high", bbox=[10, 20, 30, 40],
        metrics={"z_peak": 42.3, "z_mean": 3.1, "n_blocks": 12, "polarity": "brighter"},
        detail="形态：直线/矩形边界", detector="ela",
        sources=[law_ref("MARK_ART6_3")],
    )
    d = ev.as_dict()
    json.dumps(d, ensure_ascii=False)          # 必须可序列化
    assert d["bbox"] == [10, 20, 30, 40]
    assert d["metrics"]["z_peak"] == 42.3


def test_benign_evidence_is_a_first_class_citizen():
    """免责证据必须能"解释掉"具体某几条可疑证据（链接要看得见）。"""
    hits = {
        "BEAUTY_RETOUCH": "美颜磨皮",
        "PLATFORM_STRIP": "平台重压缩",
        "NO_JPEG_HISTORY": "无 JPEG 史",
        "NO_ANOMALY_REGION": "无坐标级异常",
    }
    for code, keyword in hits.items():
        assert code in BENIGN_CODES, f"契约缺少免责码 {code}"
        assert keyword in BENIGN_CODES[code] or len(BENIGN_CODES[code]) > 6

    b = BenignEvidence(code="BEAUTY_RETOUCH", explains=["E1", "E2"], detail="偏暗低残差区",
                       confidence="high", sources=[])
    assert b.as_dict()["explains"] == ["E1", "E2"]


def test_boundaries_declare_no_truth_verdict():
    """产品主张的代码形态：边界声明里必须写明"不做真假终审"。"""
    joined = "".join(DEFAULT_BOUNDARIES)
    assert "真假" in joined
    assert len(DEFAULT_BOUNDARIES) >= 4


def test_provenance_state_has_three_law_states_plus_abstain():
    """三态对应《标识办法》第十条的三档，另加一个"证据不足"弃权态。"""
    values = {s.value for s in ProvenanceState}
    assert len(values) == 4
    assert any("剥离" in v for v in values)
    assert any("无法判定" in v or "证据不足" in v for v in values)


def test_provenance_abstain_is_legal_not_fake_clean():
    """缺证据时必须能表达"弃权"，禁止用 0 分冒充"清白"。"""
    p = ProvenanceResult(state=ProvenanceState.UNKNOWN, confidence="low", notes=["缺少证据"])
    assert p.as_dict()["state"] == ProvenanceState.UNKNOWN.value
    assert p.confidence == "low"


def test_content_item_is_single_source_of_truth():
    """ContentItem 定义在契约层；旧的 pipeline.ContentItem 必须是同一个类。"""
    from voiceguard.pipeline import ContentItem as Legacy
    assert Legacy is ContentItem
    assert ContentItem(image_path="x.jpg").text == ""


def test_source_ref_kinds_are_enumeration_not_free_text():
    ok = {law_ref("MARK_ART5_META").kind, draft_ref("AD_LAW_28").kind,
          SourceRef(kind="detector", label="ela").kind}
    assert ok == {"law", "draft", "detector"}


if __name__ == "__main__":                      # 允许单文件直跑
    raise SystemExit(pytest.main([__file__, "-q"]))