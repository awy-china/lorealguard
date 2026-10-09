"""F5 评论区真实性层 —— **判定层**：把 signals 送进 comment_rules（YAML）得到结论。

分工与 F4/F7 完全一致：**阈值与措辞在 YAML，代码只负责比较与归因**。

四条纪律落成代码：
    ① 信号缺失(None) → 规则不命中（复用 engine.compare 的语义）；
    ② **弃权必须有归因码**：不判也要说清"为什么不判"，不许用沉默代替结论；
    ③ 命中记录带上触发时的实测数字（signals_used），任何人可复算；
    ④ 本层永不出"这是水军/这是假评论"的终审 —— 只出「形态」与「建议复核」。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.contract import RiskTier, resolve_source
from . import signals as sig_mod

#: 等级排序。**只用于取最高级**，不参与判定（判定在 YAML 的 when 里）。
TIER_RANK = {"不适用": 0, "提示级": 1, "关注级": 2, "高风险": 3}

#: 结构性归因码 —— 与本层"缺什么数据"一一对应。弃权时至少要能指出其中一条。
ATT_NO_ACCOUNT_META = (
    "ATT_NO_ACCOUNT_META",
    "**结构性缺口**：本层只有评论文本，拿不到账号注册时长、发帖频率、"
    "IP/设备等平台侧数据 —— 凡是需要账号行为才能定的结论（例如「是否同一人批量发帖」），本系统一律不判。",
)
ATT_TOO_FEW_COMMENTS = (
    "ATT_TOO_FEW_COMMENTS",
    "评论条数低于可判定下限：样本太少时『多样性/重复率』这类统计量本身不稳，"
    "据此下结论就是拿噪声当证据。**不适用 ≠ 无异常**。",
)
ATT_NO_COMMERCE_SYMBOL = (
    "ATT_NO_COMMERCE_SYMBOL",
    "组内未检出任何**明确商业符号**（URL/商品域/购买引导/引流话术）："
    "词典只收明确符号、不收『推荐/好用/姐妹冲』这类模糊词 —— 缺符号即不判商业推广，这是误报护栏。",
)
ATT_ORGANIC_DETAIL = (
    "ATT_ORGANIC_DETAIL",
    "多条评论含可核对实指（用量/成分/肤质/用法），且长短不一 —— 与批量刷评的统计形态相反。",
)


@dataclass
class CommentVerdict:
    tier: str = "不适用"                 # 本层口径（含弃权态“不适用”）
    contract_tier: str = "未触发"          # 映射到全局 RiskTier，便于与 F7/报告同源比较
    rule_ids: list[str] = field(default_factory=list)
    hits: list[dict] = field(default_factory=list)
    attributions: list[dict] = field(default_factory=list)
    advice_platform: str = ""
    advice_creator: str = ""
    signals: dict = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "contract_tier": self.contract_tier,
            "rule_ids": self.rule_ids,
            "hits": self.hits,
            "attributions": self.attributions,
            "advice_platform": self.advice_platform,
            "advice_creator": self.advice_creator,
            "signals": self.signals,
            "sources": self.sources,
        }


def _as_rule_hits(self, benign: bool = False) -> list:
    """本层命中 → 全局 `RuleHit`，让 F5 与 F1/F4 走同一条判定与免责通路。"""
    from ..core.contract import RuleHit

    out = []
    for h in self.hits:
        if h["id"].startswith("B-") != benign:
            continue
        tier = (RiskTier(h["tier"]) if h["tier"] in {t.value for t in RiskTier}
                else RiskTier.UNDETERMINED)
        src = str(h.get("source") or "")
        ref = resolve_source(src) if src.startswith(("law:", "draft:", "detector:")) else None
        out.append(RuleHit(
            rule_id=h["id"], desc=h["desc"], tier=tier, source=ref,
            advice_platform=h.get("advice_platform", ""),
            advice_creator=h.get("advice_creator", ""),
            signals_used=dict(h.get("signals_used") or {}),
            benign=benign, code=(h.get("attribution") or "") if benign else "",
        ))
    return out


def _attr(pair: tuple[str, str], **extra) -> dict:
    d = {"code": pair[0], "detail": pair[1]}
    d.update(extra)
    return d


def evaluate(thread: list[str] | tuple[str, ...], rules: dict,
             sig: sig_mod.CommentSignals | None = None) -> CommentVerdict:
    """判定一条评论线程。`rules` 为 rules.yaml 全文（含 comment_rules 段）。"""
    from ..rules.engine import compare                     # 复用：None 不命中

    sig = sig if sig is not None else sig_mod.analyze(thread)
    values = sig.signals()
    cfg = rules.get("comment_config") or {}
    min_n = int(cfg.get("min_comments_for_verdict", 5))

    verdict = CommentVerdict(signals=values)
    fired: list[tuple[str, dict]] = []

    for grp in ("comment_rules", "comment_benign_rules"):
        for rule in (rules.get(grp) or []):
            conds = rule.get("when") or {}
            conds = {k: v for k, v in conds.items() if str(k) not in ("_", "comment")}
            if not conds:
                continue
            ok = all(compare(values.get(k), v) for k, v in conds.items())
            if not ok:
                continue
            used = {k: values.get(k) for k in conds}        # 触发时的实测数字 → 报告可复算
            fired.append((str(rule.get("tier", "提示级")), {
                "id": str(rule.get("id", "?")),
                "tier": str(rule.get("tier", "提示级")),
                "desc": str(rule.get("desc", "")),
                "detail": str(rule.get("detail", "")),
                "attribution": str(rule.get("attribution", "") or rule.get("code", "")),
                "signals_used": used,
                "advice_platform": str(rule.get("advice_platform", "")),
                "advice_creator": str(rule.get("advice_creator", "")),
                "source": str(rule.get("source", "") or ""),
                "sources": [str(s) for s in (rule.get("sources") or [])],
            }))

    risk = [f for f in fired if f[1]["id"].startswith("C-")]
    benign = [f for f in fired if f[1]["id"].startswith("B-")]

    # ---- 结构性退让：条数不够就整层不适用（且必须说清为什么）
    if sig.n_comments < min_n:
        verdict.tier = "不适用"
        verdict.rule_ids = ["C-NOT-APPLICABLE-FEW-COMMENTS"]
        verdict.attributions = [_attr(ATT_TOO_FEW_COMMENTS, n_comments=sig.n_comments,
                                     min_comments=min_n), _attr(ATT_NO_ACCOUNT_META)]
        verdict.advice_platform = "评论条数不足以做组内统计，请补充完整评论列表后复核。"
        verdict.advice_creator = "本条未对评论区作任何形态判定，**不构成对你的质疑**。"
        verdict.contract_tier = RiskTier.UNDETERMINED.value
        verdict.as_rule_hits = _as_rule_hits.__get__(verdict, CommentVerdict)
        return verdict

    if risk:
        top = max(risk, key=lambda f: TIER_RANK.get(f[0], 0))
        verdict.tier = top[0]
        verdict.rule_ids = [f[1]["id"] for f in risk]
        verdict.hits = [f[1] for f in risk]
        verdict.advice_platform = " / ".join(f[1]["advice_platform"] for f in risk if f[1]["advice_platform"])
        verdict.advice_creator = " / ".join(f[1]["advice_creator"] for f in risk if f[1]["advice_creator"])
    else:
        verdict.tier = "提示级"
        verdict.rule_ids = [f[1]["id"] for f in benign] or ["B-REVIEW-NO-RISK-FORM"]
        verdict.hits = [f[1] for f in benign]
        verdict.advice_platform = "未检出刷评形态特征，按正常内容处理。"
        verdict.advice_creator = "评论区未见异常形态，无需处理。"

    # ---- 弃权/形态归因：把"为什么不是更重的结论"逐条写出来（弃权必须归因）
    attrs = [_attr(ATT_NO_ACCOUNT_META)]
    for f in fired:
        code = f[1].get("attribution") or f[1].get("code")
        if code:
            attrs.append({"code": str(code), "detail": f[1].get("detail") or f[1].get("desc", "")})
    if sig.solicit_ratio == 0:
        attrs.append(_attr(ATT_NO_COMMERCE_SYMBOL))
    if sig.specifics_ratio >= 0.5 and not risk:
        attrs.append(_attr(ATT_ORGANIC_DETAIL, specifics_ratio=sig.specifics_ratio))
    verdict.attributions = attrs
    verdict.as_rule_hits = _as_rule_hits.__get__(verdict, CommentVerdict)
    verdict.contract_tier = (RiskTier(verdict.tier).value
                             if verdict.tier in {t.value for t in RiskTier}
                             else RiskTier.UNDETERMINED.value)

    verdict.as_rule_hits = _as_rule_hits.__get__(verdict, CommentVerdict)
    seen: list[dict] = []
    for f in fired:
        specs = [f[1].get("source") or ""] + [str(x) for x in (f[1].get("sources") or [])]
        for s in specs:
            if s and s not in [x.get("source") for x in seen]:
                ref = resolve_source(s) if s.startswith(("law:", "draft:", "detector:")) else None
                seen.append({"source": s,
                             "text": (ref.display() if ref else s)})
    verdict.sources = seen
    return verdict