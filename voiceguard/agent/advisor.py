"""Agent 处置建议层 —— 把「判定等级」翻译成「谁该做什么」。

赛题细则②要的是「结合风险预警与处理建议，展示从识别到决策的完整闭环」。
判定（F7）回答的是"这条内容风险多高"；本层回答的是"接下来谁做什么"。

三条设计约束（与全局铁律一致）：
    ① **措辞在 YAML**（`rules.yaml: agent_playbook`），代码只做等级 → 文本的映射；
    ② 每个等级都给**平台侧 + 创作者侧**两条建议：只让平台去查、不告创作者如何自证，
       就是把全部成本推给创作者 —— 这恰恰是我们要修的那件事；
    ③ **人在环上**要说清位置：本层最高只到「建议人工复核」，定性/处罚/下架全归人工。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Decision:
    """一条内容的处置决策（可 json 序列化，直接进报告与演示输出）。"""

    content_id: str = ""
    tier: str = "未触发"
    alert: str = ""
    basis: list[str] = field(default_factory=list)      # 依据：规则 + 触发时的实测数字
    actions: list[dict] = field(default_factory=list)   # [{owner, action, requires_human}]
    abstain_reasons: list[str] = field(default_factory=list)
    human_in_the_loop: str = ""
    disclaimer: str = ""

    def to_dict(self) -> dict:
        return {
            "content_id": self.content_id, "tier": self.tier, "alert": self.alert,
            "basis": self.basis, "actions": self.actions,
            "abstain_reasons": self.abstain_reasons,
            "human_in_the_loop": self.human_in_the_loop, "disclaimer": self.disclaimer,
        }


def _num(v) -> str:
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def advise(report, rules: dict | None = None) -> Decision:
    """由一份 `RiskReport`（或等价的轻量对象）生成处置决策。"""
    from ..rules.engine import load_rules

    rules = rules or load_rules()
    pb = rules.get("agent_playbook") or {}
    tiers = pb.get("tiers") or {}
    v = report.verdict
    tier = v.tier.value if hasattr(v.tier, "value") else str(v.tier)

    cv0 = getattr(report, "comment_verdict", None) or {}
    #: 弃权 ≠ 未触发。只有当**有层明确不适用**、且没有任何输入层给出过结论时，
    #: 才算本次弃权（并必须逐条写出原因）。纯图/纯文案即使未触发风险规则，
    #: 那也是「已判：无风险形态」，写成弃权就等于把「无异常」说成「判不了」。
    # 注意：「已跑（证据不足）」不算结论 —— 那是"没有可判的输入"，不是"判过且无异常"。
    # 第一版把它当成了结论，于是「仅 2 条评论」的弃权被印成「未触发」，
    # 正好踩中本项目的头号禁忌：把「判不了」说成「没问题」。
    concluded = []
    for x in (getattr(report, "layers", None) or []):
        if str(x.get("layer", ""))[:2] not in ("F1", "F2", "F3", "F4"):
            continue
        st = str(x.get("status", ""))
        if st.startswith("已跑") and "证据不足" not in st:
            concluded.append(x)
    # 判据只认「有没有层给出过结论」，不认等级名 —— 补了「未触发」档之后，
    # 用 `tier not in tiers` 当判据会失效（它恒为假），弃权就被吞掉了。
    abstained = (cv0.get("tier") == "不适用" and not concluded
                 and not list(getattr(v, "fired", []) or []))
    block = (pb.get("abstain", {}) if abstained
             else tiers.get(tier) or tiers.get("未触发") or {})

    #: 预警按命中规则族分流；取**等级最高**的那条命中作为主形态（措辞在 YAML）
    by_prefix = pb.get("alert_by_prefix") or {}
    rank = {"提示级": 1, "关注级": 2, "高风险": 3}
    fired_sorted = sorted(getattr(v, "fired", []) or [],
                          key=lambda h: rank.get(h.tier.value, 0), reverse=True)
    alert = str(block.get("alert", ""))
    for h in fired_sorted:
        for pref, txt in by_prefix.items():
            if h.rule_id.startswith(pref):
                alert = str(txt)
                break
        else:
            continue
        break

    basis: list[str] = []
    for h in getattr(v, "fired", []) or []:
        nums = "；".join(f"{k}={_num(x)}" for k, x in (h.signals_used or {}).items())
        basis.append(f"`{h.rule_id}`（{h.tier.value}）{nums}")

    cv = getattr(report, "comment_verdict", None) or {}
    reasons = [f"`{a.get('code')}` {a.get('detail', '')}"
               for a in (cv.get("attributions") or [])] if abstained else []

    return Decision(
        content_id=getattr(report, "content_id", ""),
        tier=tier,
        alert=alert,
        basis=basis,
        actions=[dict(a) for a in (block.get("actions") or [])],
        abstain_reasons=reasons,
        human_in_the_loop=str(block.get("human_in_the_loop", "")),
        disclaimer=str(pb.get("disclaimer", "")),
    )


def render_decision(d: Decision) -> str:
    """终端版决策卡（演示视频里逐字出现的，就是这段输出）。"""
    L = [f"┌─ Agent 决策 · {d.content_id} ─ 等级 {d.tier}",
         f"│ 预警：{d.alert}"]
    if d.basis:
        L.append("│ 依据（可复算）：")
        L += [f"│   · {b}" for b in d.basis]
    for a in d.actions:
        tag = "需人工" if a.get("requires_human") else "无需人工"
        L.append(f"│ → [{a.get('owner')}] {a.get('action')}（{tag}）")
    for r in d.abstain_reasons[:3]:
        L.append(f"│ ✗ 弃权原因：{r}")
    L.append(f"│ 人在环上：{d.human_in_the_loop}")
    L.append(f"└─ {d.disclaimer}")
    return "\n".join(L)


__all__ = ["Decision", "advise", "render_decision"]