"""core —— 数据契约层（框架中枢）。

对外只有一件事：**全项目只认这一套事实格式**。
上层（F1–F8）任何一层都不许自定义输出结构，否则「双向账本」的完整性无法保证。
"""

from .contract import (  # noqa: F401
    BENIGN_CODES,
    CLAUSES,
    DEFAULT_BOUNDARIES,
    LAW_MARK,
    LAW_MARK_URL,
    LAW_MARK_VERIFIED_AT,
    UNVERIFIED_LAWS,
    BenignEvidence,
    ContentItem,
    Evidence,
    ProvenanceResult,
    ProvenanceState,
    RiskReport,
    RiskTier,
    RuleHit,
    SourceRef,
    Verdict,
    detector_ref,
    draft_ref,
    law_ref,
    max_tier,
    resolve_source,
)

__all__ = [
    "BENIGN_CODES", "CLAUSES", "DEFAULT_BOUNDARIES", "LAW_MARK", "LAW_MARK_URL",
    "LAW_MARK_VERIFIED_AT", "UNVERIFIED_LAWS", "BenignEvidence", "ContentItem", "Evidence",
    "ProvenanceResult", "ProvenanceState", "RiskReport", "RiskTier", "RuleHit", "SourceRef",
    "Verdict", "detector_ref", "draft_ref", "law_ref", "max_tier", "resolve_source",
]