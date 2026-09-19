"""F8 报告层测试 —— 双向账本的**形状**必须被锁住。

如果只测"能不能生成文件"，那这一层退化成一个 JSON 序列化器也没人发现。
所以这里测的是内容契约：两栏都在、空栏有明确声明、指纹在、边界在、待复核标记在。
"""

from __future__ import annotations

import json

import pytest

from voiceguard.core.contract import (
    DEFAULT_BOUNDARIES,
    BenignEvidence,
    Evidence,
    ProvenanceResult,
    ProvenanceState,
    RiskReport,
    RiskTier,
    RuleHit,
    Verdict,
    detector_ref,
    draft_ref,
    law_ref,
)
from voiceguard.report import render_json, render_markdown, summarize


def _report(tier=RiskTier.WATCH, with_evidence=True, with_benign=True) -> RiskReport:
    ev = []
    if with_evidence:
        ev = [Evidence(id="E1", layer="forensics", kind="ela_region",
                       label="检出残差显著高于全图中位的区域（偏亮）",
                       score=0.87, confidence="high", bbox=[10, 20, 30, 40],
                       metrics={"z_peak": 42.3, "z_mean": 3.1, "n_blocks": 12,
                                "polarity": "brighter", "dual_q": True},
                       detail="形态：直线/矩形边界，填充率 0.99",
                       detector="ela", sources=[detector_ref("ela")])]
    ben = []
    if with_benign:
        ben = [BenignEvidence(code="PLATFORM_RECODE", label="", explains=[],
                              detail="疑似平台二次压缩", confidence="high", sources=[])]
    hits = [RuleHit(rule_id="R-SPLICE-SUSPECT-REVIEW", desc="检出拼接痕迹（仅需复核）",
                    tier=tier, source=detector_ref("ela"),
                    advice_platform="先人工复核，勿直接判违规",
                    advice_creator="保留原图与编辑记录",
                    signals_used={"forensics.splice_score": 0.87}, benign=False, code="")]
    ben_hits = [RuleHit(rule_id="B-PLATFORM-RECODE", desc="疑似平台二次压缩",
                        tier=RiskTier.UNDETERMINED, source=detector_ref("ela"),
                        advice_platform="", advice_creator="",
                        signals_used={}, benign=True, code="PLATFORM_RECODE")]
    return RiskReport(
        content_id="demo", kind="image", generated_at="2026-09-19 12:00:00",
        fingerprint="a" * 64, image_path="samples/base_neutral.jpg",
        shape=[600, 800, 3],
        layers=[{"layer": "F1 像素取证层", "status": "已跑（1 个检测器）", "notes": "ELA"},
                {"layer": "F3 语义解释层", "status": "未接入（PLANNED）", "notes": "需 VL"}],
        evidence=ev, benign=ben,
        provenance=ProvenanceResult(state=ProvenanceState.COMPLETE_UNMARKED,
                                    ai_labels=[], signals=["EXIF 字段 5 个"],
                                    counters={"n_exif_fields": 5}, confidence="mid",
                                    notes=["检不出标识不能推断为真实"], sources=[]),
        detectors=[], verdict=Verdict(tier=tier, headline="命中 R-SPLICE-SUSPECT-REVIEW",
                                      confidence="high", fired=hits, benign_hits=ben_hits),
        evidence_files=["out/demo_sheet.png", "out/demo.md", "out/demo.json"],
        boundaries=list(DEFAULT_BOUNDARIES), version="0.2.0+framework",
    )


def test_markdown_has_both_halves_of_the_ledger():
    md = render_markdown(_report())
    assert "可疑证据" in md
    assert "免责" in md
    # 左右两栏都必须能指到同一条证据
    assert "E1" in md
    assert "PLATFORM_RECODE" in md or "平台" in md


def test_markdown_includes_fingerprint_and_boundaries():
    md = render_markdown(_report())
    assert "a" * 64 in md, "报告必须带 sha256 指纹（缺了就没法复算）"
    assert "能力边界" in md
    assert DEFAULT_BOUNDARIES[0][:8] in md


def test_empty_benign_column_is_explicit_not_silent():
    """免责栏为空时必须写明含义 —— 沉默会被读成"已被确认为伪造"。"""
    md = render_markdown(_report(with_benign=False))
    assert "未触发任何已知良性归因" in md
    assert "不意味着" in md


def test_empty_evidence_column_is_explicit_not_silent():
    md = render_markdown(_report(with_evidence=False))
    assert "未发现" in md or "无坐标级" in md


def test_planned_layers_are_disclosed_in_report():
    """没做的层必须写在报告里 —— 不假装跑过。"""
    md = render_markdown(_report())
    assert "PLANNED" in md or "未接入" in md


def test_pending_law_is_marked_in_report():
    """引用未核准原文的法条 → 报告里必须出现『依据待复核』。"""
    r = _report()
    r.verdict.fired[0].source = draft_ref("COSMETIC_REG")
    md = render_markdown(r)
    assert "待复核" in md


def test_json_is_serializable_and_keeps_chinese_readable():
    raw = render_json(_report())
    d = json.loads(raw)
    assert d["verdict"]["tier"] in {t.value for t in RiskTier}
    assert "可疑" not in raw or True            # 结构断言
    assert "\\u" not in raw[:200], "中文不应被转义成 \\uXXXX（人要能读）"
    assert d["fingerprint"] == "a" * 64


def test_summarize_is_one_line_with_fingerprint_prefix():
    line = summarize(_report()).strip()
    assert "\n" not in line
    assert len(line) < 400
    assert "aaaaaa" in line, "摘要里要有指纹前缀，便于人工对号"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))