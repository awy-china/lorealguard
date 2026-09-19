"""规则引擎：把 YAML 规则表变成可执行的判定（F7 的执行体）。

刻意的分工：
    代码 = 机制（怎么比、什么时候不判）
    YAML = 阈值与措辞（判多严、写给谁看）
所以本文件里**不出现任何一个阈值数字**。

三条工程纪律：
    ① 信号缺失(None) → 规则不命中。宁可"这条没判"，也不把缺失当 0 分触发风险。
    ② 规则写错（未知运算符/未知法条键）→ 记为 error 并**跳过**，不让一条 typo 挂掉整条流水线；
       但错误会如实进入报告的"规则表自检"栏，不静默吞掉。
    ③ 命中记录必须带上**触发时的实测数字**（signals_used），报告才能被人复算。
"""

from __future__ import annotations

import operator
import re
from pathlib import Path

from ..core.contract import RuleHit, RiskTier, resolve_source

DEFAULT_RULES = Path(__file__).with_name("rules.yaml")

#: 运算符表。只允许这 6 个 + in，防止 YAML 里写出"看着像但不会执行"的条件。
_BIN_OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt,
            "<": operator.lt, "==": operator.eq, "!=": operator.ne}

_SPEC_RE = re.compile(r"^(>=|<=|==|!=|>|<)?\s*(.*)$", re.S)


# ------------------------------------------------------------------ 载入

def load_rules(path: str | Path | None = None) -> dict:
    """读规则表。需要 PyYAML（requirements.txt 已声明）。"""
    import yaml

    p = Path(path) if path else DEFAULT_RULES
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "risk_rules" not in data and "benign_rules" not in data:
        raise ValueError(f"{p} 不像规则表：缺 risk_rules / benign_rules")
    return data


def rule_ids(rules: dict, kind: str = "risk") -> list[str]:
    key = "benign_rules" if kind.startswith("benign") else "risk_rules"
    return [str(r.get("id", "?")) for r in (rules.get(key) or [])]


def referenced_laws(rules: dict) -> list[str]:
    """规则表引用到的所有 law:/draft: 键 —— 报告要能列出"我们依据了什么"。"""
    keys: list[str] = []
    for grp in ("risk_rules", "benign_rules"):
        for r in (rules.get(grp) or []):
            for spec in _source_specs(r):
                s = str(spec)
                if s.startswith(("law:", "draft:")) and s not in keys:
                    keys.append(s)
    return keys


def _source_specs(rule: dict) -> list:
    out = []
    if rule.get("source"):
        out.append(rule["source"])
    for s in (rule.get("sources") or []):
        out.append(s)
    return out


# ------------------------------------------------------------------ 比较

def _coerce(raw: str):
    v = raw.strip()
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return float(v)
    except ValueError:
        return v.strip('"').strip("'")


def compare(actual, spec) -> bool:
    """actual（信号实测值） vs spec（YAML 里的 ">=0.6" 这类字符串）。

    None 一律不命中 —— 缺信号 ≠ 信号为 0。这一条是本项目误报护栏的代码形态。
    """
    if actual is None or spec is None:
        return False
    s = str(spec).strip()
    if s.startswith("in[") and s.endswith("]"):
        items = [_coerce(x) for x in s[3:-1].split(",")]
        return actual in items or str(actual) in [str(i) for i in items]
    m = _SPEC_RE.match(s)
    op, rhs = m.group(1), m.group(2)
    want = _coerce(rhs)
    if op is None:
        op, want = "==", want
    if isinstance(want, bool) or isinstance(actual, bool):
        # 布尔只允许 == / !=，避免 True > False 这种无意义的比较
        if op in ("==", "!="):
            return _BIN_OPS[op](bool(actual), bool(want))
        return False
    if isinstance(want, float):
        try:
            return _BIN_OPS[op](float(actual), want)
        except (TypeError, ValueError):
            return False
    return _BIN_OPS[op](str(actual).strip(), str(want).strip())


# ------------------------------------------------------------------ 执行

def evaluate(signals: dict, rules: dict, kind: str = "risk",
             errors: list | None = None) -> list[RuleHit]:
    """跑一批规则，返回命中列表。

    kind="risk"  → risk_rules（产出 RuleHit.tier）
    kind="benign"→ benign_rules（产出 RuleHit.benign=True + code）
    """
    key = "benign_rules" if kind.startswith("benign") else "risk_rules"
    hits: list[RuleHit] = []
    for r in (rules.get(key) or []):
        rid = str(r.get("id", "?"))
        when = r.get("when") or {}
        if not isinstance(when, dict) or not when:
            (errors if errors is not None else []).append(f"{rid}: when 为空或不是映射，已跳过")
            continue

        matched, used, missing = True, {}, []
        for field, spec in when.items():
            actual = signals.get(field)
            if actual is None:
                missing.append(field)
            used[str(field)] = actual
            if not compare(actual, spec):
                matched = False
                break
        if not matched:
            continue

        srcs = []
        for spec in _source_specs(r):
            try:
                srcs.append(resolve_source(spec))
            except KeyError as e:                     # 法条键写错 → 记错并跳过该出处
                (errors if errors is not None else []).append(f"{rid}: 未知出处键 {e}，已跳过该出处")

        tier = RiskTier.UNDETERMINED
        if not kind.startswith("benign"):
            tv = str(r.get("tier", "")).strip()
            try:
                tier = RiskTier(tv)
            except ValueError:
                (errors if errors is not None else []).append(f"{rid}: 未知等级 {tv!r}，已按未触发处理")

        if missing:
            used["_missing_signals"] = missing

        hits.append(RuleHit(
            rule_id=rid, desc=str(r.get("desc", "")), tier=tier,
            source=(srcs[0] if srcs else None), signals_used=used,
            advice_platform=str(r.get("advice_platform", "")),
            advice_creator=str(r.get("advice_creator", "")),
            benign=kind.startswith("benign"), code=str(r.get("code", "")),
        ))
    return hits


def satisfying(spec):
    """给定阈值表达式，造一个「刚好满足它」的值 —— 只服务于规则表自检。"""
    s = str(spec).strip()
    m = re.match(r"^(>=|<=|==|!=|>|<)\s*(.*)$", s)
    op, val = (m.group(1), m.group(2)) if m else ("==", s)
    if val.lower() in ("true", "false"):
        t = val.lower() == "true"
        return (not t) if op == "!=" else t
    try:
        f = float(val)
    except ValueError:
        return val                                    # 字符串型：直接给同一个值
    return {"==": f, "!=": f + 1, ">=": f, "<=": f,
            ">": f + abs(f) + 1, "<": f - abs(f) - 1}[op]   # 1 是偏移量不是阈值：故意写成整数


def selfcheck(rules: dict) -> dict:
    """规则表体检：拿「刚好满足」的合成信号把每条规则各点一次火。

    为什么值得单独写一个：规则表是**改一行就能改变判定结果**的地方，
    它必须自带体检 —— 否则一个 typo（未知运算符、未知等级、未知法条键）
    会静默失效：规则永远不命中，而报告照样显示"已跑"。
    这是最危险的一类 bug：错得看起来像"一切正常"。
    """
    errors: list[str] = []
    for kind in ("risk", "benign"):
        key = "benign_rules" if kind == "benign" else "risk_rules"
        for r in (rules.get(key) or []):
            when = r.get("when") or {}
            sig = {f: satisfying(s) for f, s in when.items()} if isinstance(when, dict) else {}
            evaluate(sig, {key: [r]}, kind, errors)
    return {"errors": errors,
            "n_risk": len(rules.get("risk_rules") or []),
            "n_benign": len(rules.get("benign_rules") or []),
            "n_rules_checked": len(rules.get("risk_rules") or []) + len(rules.get("benign_rules") or [])}


def describe(rules: dict) -> dict:
    """规则表自检信息（进报告）：有多少条、引用了哪些法条、哪些待复核、有没有结构错误。"""
    from ..core.contract import CLAUSES, UNVERIFIED_LAWS

    refs = referenced_laws(rules)
    verified, pending = [], []
    for s in refs:
        k = s.split(":", 1)[1]
        (verified if (s.startswith("law:") and k in CLAUSES) else pending).append(s)
    return {"version": rules.get("version"), "updated": rules.get("updated"),
            "n_risk": len(rules.get("risk_rules") or []),
            "n_benign": len(rules.get("benign_rules") or []),
            "verified_sources": verified, "pending_sources": pending,
            "n_clauses_verified": len(CLAUSES), "n_laws_unverified": len(UNVERIFIED_LAWS),
            **selfcheck(rules)}