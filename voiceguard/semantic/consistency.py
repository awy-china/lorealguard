"""F4-C 图文一致性层 —— 把「图像证据」与「文案承诺」交叉起来。

这一层解决的是单看任何一边都判不出的情形：

    情形 A（像素层判不出）：图是真的、话是假的。
        两张真实拍摄的照片 + 「7 天根除痘印」→ ELA 全绿，但内容在骗人。
        → 由 claims.py 的生理时间尺度对照判出（生物科学轴）。

    情形 B（像素层会误伤）：图动过、但没声称功效。
        博主把自己的拼版图发出来，只是记录生活 → ELA 报"拼接嫌疑"，
        若直接升级成"虚假宣传"就是冤枉。
        → 本层必须给出「非功效语境」的免责归因，把等级压回去。

    情形 C（两边都弱）：有差异描述、有功效词，但无时间承诺、无对比语境
        → 弃权（本层不做无依据的推断）。

输出：`semantic.consistency_state` + 若干 `text.*` 信号 + 最多两条 Evidence。
⚠️ 本层不产生等级 —— 等级只由 rules.yaml 决定（代码里零阈值）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.contract import BENIGN_CODES, Evidence, draft_ref, detector_ref
from . import claims as claims_mod
from . import textstats as stats_mod

STATE_CONSISTENT = "一致"
STATE_INSUFFICIENT = "证据不足"
STATE_CONFLICT = "图文不一致"
STATE_INFEASIBLE = "生理不可行"


@dataclass
class ConsistencyResult:
    state: str = STATE_INSUFFICIENT
    signals: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def analyze(item, image_signals: dict, start_index: int = 0) -> ConsistencyResult:
    """item：ContentItem（取 .text）；image_signals：F1 已产出的扁平信号表。

    start_index：Evidence 编号起点（保证全报告 id 连续不重号）。
    """
    text = getattr(item, "text", "") or ""
    ca = claims_mod.analyze(text)
    ts = stats_mod.analyze(text)

    sig: dict = {}
    sig.update(ca.signals())
    sig.update(ts.signals())
    # 文案缺失时，文本类信号一律登记为「缺信号」（None），而不是 False ——
    # 「没给我文案」与「文案里确实没有功效宣称」是两件不同的事，不许混为一谈。
    # （rules.yaml 语义：信号为 None 时规则不命中，见规则表头部约定。）
    if not text.strip():
        for k in ("text.efficacy_claim", "text.efficacy_strength", "text.efficacy_strength_rank",
                  "text.has_shopping_link", "text.ad_label_declared", "text.before_after_context",
                  "text.efficacy_implausible", "text.efficacy_feasible"):
            sig[k] = None
    sig["semantic.text_provided"] = bool(text.strip())

    res = ConsistencyResult(notes=list(ca.notes))
    n = start_index

    # 图像侧是否有坐标级可疑证据（且该检测器这次可靠）
    ela_ok = bool(image_signals.get("forensics.ela.reliable"))
    n_brighter = int(image_signals.get("forensics.ela.n_brighter_regions") or 0)
    img_evidence = ela_ok and n_brighter >= 1

    # ---- 情形 A：生理不可行（不依赖图像，纯文案 + 生理尺度）
    if ca.implausible:
        n += 1
        worst = ca.implausible[0]
        detail = "；".join(
            f"{d['claim']}：承诺 {d['promised_days']} 天，该类别保守下界 {d['min_plausible_days']} 天"
            for d in ca.implausible
        )
        res.evidence.append(Evidence(
            id=f"E{n}", layer="semantic", kind="efficacy_timeline",
            label=f"时间承诺短于该功效的生理保守下界（{worst['claim']}）→ 宣称可信度存疑",
            score=0.6, confidence="mid",
            metrics={"promised_days": worst["promised_days"],
                     "min_plausible_days": worst["min_plausible_days"],
                     "strength": ca.strength, "n_implausible": len(ca.implausible)},
            detail=f"{detail}。依据为保守下界，报告标注『依据待复核』。",
            detector="claims",
            sources=[draft_ref("COSMETIC_EFFICACY_GUIDE")],
        ))
        sig["semantic.consistency_state"] = STATE_INFEASIBLE
        res.state = STATE_INFEASIBLE
        res.signals = sig
        return res

    # ---- 情形 B：图像有坐标级证据 + 处于功效对比语境 → 图文不一致
    if img_evidence and ca.claims and ts.before_after_context:
        n += 1
        res.evidence.append(Evidence(
            id=f"E{n}", layer="semantic", kind="image_text_conflict",
            label="图像存在坐标级可疑证据，且文案处于功效对比语境 → 图文相互印证，非孤立疑点",
            score=0.7, confidence="mid",
            metrics={"n_brighter_regions": n_brighter, "strength": ca.strength,
                     "claim_surfaces": [c.surface for c in ca.claims],
                     "before_after_hits": ts.hits.get("before_after", [])},
            detail=("单看图像只是『有残差异常区』，单看文案只是『有功效表述』；"
                    "两者同时出现在对比语境里，才构成可复核的疑点链。"),
            detector="consistency",
            sources=[detector_ref("ela"), detector_ref("claims")],
        ))
        sig["semantic.consistency_state"] = STATE_CONFLICT
        res.state = STATE_CONFLICT
        res.signals = sig
        return res

    # ---- 情形 C：对照通过（有强宣称 + 给足了周期 + 落在生理尺度内）
    if ca.feasible is True:
        res.notes.append("对照通过：承诺周期落在该功效的生理保守尺度内 → 不构成疑点")
        sig["semantic.consistency_state"] = STATE_CONSISTENT
        res.state = STATE_CONSISTENT
        res.signals = sig
        return res

    # ---- 情形 D：弃权（并列登记免责归因所需的信号，等级交给 rules.yaml）
    sig["semantic.consistency_state"] = STATE_INSUFFICIENT
    if not text.strip():
        res.notes.append("未提供文案 → 图文一致性层不适用")
    elif not ca.claims:
        res.notes.append("文案未出现功效宣称 → 不构成功效语境")
    elif img_evidence:
        res.notes.append("图像有坐标级证据，但文案未给出时间承诺/未处于对比语境 → 不足以升级")
    else:
        res.notes.append("图像层无坐标级证据 → 不升级为虚假宣传")
    res.state = STATE_INSUFFICIENT
    res.signals = sig
    return res


__all__ = ["analyze", "ConsistencyResult", "BENIGN_CODES",
           "STATE_CONSISTENT", "STATE_INSUFFICIENT", "STATE_CONFLICT", "STATE_INFEASIBLE"]