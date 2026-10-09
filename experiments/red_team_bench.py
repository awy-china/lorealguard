"""P6 红队跑分器 —— 三支矛的攻击成功率（见 `docs/REDTEAM-PROTOCOL.md`）。

它只做四件事，全部按协议写死的判据：
    ① **靶子不变性**：当前 rules.yaml 指纹 ≠ 样本生成时指纹 → **拒绝出分**（exit 3）；
    ② 三支矛的 绕过率 / 误报率（点估计 + 单侧 95% 上界）与**红线计数**（矛 B 不得出现高风险）；
    ③ **归因完备性**：有判定就必须有规则支撑、有归因码、有可复算的实测数字（缺一项即违规）；
    ④ **突破清单**：谁绕过了哪条规则、还差多少（绕过余量），机制用人话写清。

产出：output/redteam/red_team_report.md（给人看）+ _redteambench.txt（逐条留档）
跑法（单行）：
    env -u PYTHONPATH .venv/Scripts/python.exe experiments/red_team_bench.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))

from p2_bench import cp_lower95, cp_upper95, min_n_for_detect     # noqa: E402
from voiceguard.comments import analyze, evaluate                 # noqa: E402
from voiceguard.rules.engine import load_rules                    # noqa: E402

P6 = ROOT / "output" / "redteam"
PAIRS = ROOT / "samples" / "red_team" / "red_team.json"
REPORT = P6 / "red_team_report.md"
LOG = P6 / "_redteambench.txt"
RULES_YAML = ROOT / "voiceguard" / "rules" / "rules.yaml"

RISK = {"关注级", "高风险"}
ARM_ROLE = {"evade": ("恶意", "绕过率 = 1 − 检出率（允许高，如实登记为下界）"),
            "frame": ("良性", "红线：不得出现「高风险」；关注级及以上计数即对抗性误报"),
            "blind": ("良性", "无信息对手：命中风险等级即泛化误报")}


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def rules_digest() -> str:
    return hashlib.sha256(RULES_YAML.read_bytes()).hexdigest()[:16]


def run_rows(items: list[dict], rules) -> list[dict]:
    rows = []
    for it in items:
        sig = analyze(it["comments"])
        v = evaluate(it["comments"], rules, sig)
        rows.append({
            "id": it["id"], "arm": it["arm"], "context": it["context"],
            "n": sig.n_comments, "tier": v.tier, "contract_tier": v.contract_tier,
            "rules": v.rule_ids, "attrs": [a["code"] for a in v.attributions],
            "dup": sig.dup_ratio, "solicit": sig.solicit_ratio,
            "specifics": sig.specifics_ratio, "lexdiv": sig.lexical_diversity,
            "hits": v.hits,
        })
    return rows


def attrib_violations(rows: list[dict]) -> list[str]:
    """归因完备性 —— **一条能变红的检查**（不是"总有 ATT_NO_ACCOUNT_META 所以恒过"）。

    三处可失败点：
      · 判到关注级及以上却没有任何规则命中 → 判了说不出凭什么；
      · 规则命中却缺 `attribution` 码（YAML 里漏写 code/attribution）→ 无法归因；
      · 命中记录缺 `signals_used`（触发时的实测数字）→ 报告不可复算。
    """
    bad = []
    for r in rows:
        if r["tier"] in RISK:
            if not r["rules"] or not r["hits"]:
                bad.append(f"{r['id']}：判到 {r['tier']} 但无规则支撑")
            for h in r["hits"]:
                if not str(h.get("attribution") or "").strip():
                    bad.append(f"{r['id']}：规则 {h.get('id')} 命中但无归因码")
                if not (h.get("signals_used") or {}):
                    bad.append(f"{r['id']}：规则 {h.get('id')} 命中但无可复算数字")
        elif not r["attrs"]:
            bad.append(f"{r['id']}：{r['tier']} 却无任何归因码（沉默代替结论）")
    return bad


def evade_margin(row: dict, rules) -> tuple[float, str]:
    """这条线程离"**某条规则整条触发**"还差多少。

    规则是 **AND 语义**（`when` 里所有条件同时成立才触发），所以：
        单条件亏欠量 d = 阈值与实测的差（`>=` 取 th−val、`<=` 取 val−th）；
        **规则距离 = max(各条件亏欠量)**  —— 由最远的那条条件决定；
        整条线程的余量 = min(各规则距离)。
    余量 > 0 = 没触发（差这么多）；余量 ≤ 0 = 该规则条件已全满足却仍未触发 → **口径异常**。
    """
    vals = {"dup_ratio": row["dup"], "solicit_ratio": row["solicit"],
            "specifics_ratio": row["specifics"]}
    best: tuple[float, str] = (float("inf"), "—")
    for rule in rules.get("comment_rules") or []:
        dists: list[float] = []
        label: list[str] = []
        for k, cond in (rule.get("when") or {}).items():
            # ⚠️ 实测坑（第二层）：YAML 的键带信号命名空间前缀（`review.dup_ratio`），
            # 裸名查表会**全部落空** → 余量全退化成 +inf，且不报错。必须剥前缀。
            k = str(k).split(".")[-1]
            if k not in vals or not isinstance(cond, str):
                dists = []
                break
            # ⚠️ 实测坑（第一层）：原本用 `cond[0]` 取算子，`">=0.35"` 被解析成 `"=0.35"`，
            # float() 抛错 → continue → 余量退化成 +inf（空值假通过）。必须正则连算子一起取。
            m2 = re.match(r"^\s*(>=|<=|>|<)\s*(-?[0-9.]+)\s*$", cond)
            if not m2:
                dists = []
                break
            op, th = m2.group(1), float(m2.group(2))
            dists.append((th - vals[k]) if op in (">", ">=") else (vals[k] - th))
            label.append(f"{k}{op}{th:g}={vals[k]:.2f}")
        if not dists:
            continue
        rd = max(dists)                       # AND：最远的条件决定
        if rd < best[0]:
            best = (rd, f"{rule.get('id')}（需 {' 且 '.join(label)}）")
    return best


def render(payload, rows, viol, fingerprint_ok: bool) -> str:
    by_arm = {a: [r for r in rows if r["arm"] == a] for a in ("evade", "frame", "blind")}
    L: list[str] = []
    L.append("# P6 · 红队实测报告 —— 把刀递给对手\n")
    L.append(f"- 协议：`docs/REDTEAM-PROTOCOL.md v1`（口径写于测量之前）")
    L.append(f"- 靶子指纹：样本生成时 `{payload['rules_yaml_sha256_16']}` vs 跑分时 "
             f"`{rules_digest()}` → **{'一致 ✅' if fingerprint_ok else '不一致 ❌ 拒绝出分'}**")
    L.append(f"- 对手：`{payload['model']}`（temperature={payload['temperature']}，"
             f"生成于 {payload['generated_at']}）；白盒两支拿到 `comment_rules` 全文与全部数值门槛")
    L.append(f"- 样本：三支矛各 {len(by_arm['evade'])} 条线程 × 6 条评论（一次性封存，"
             f"基准只读 JSON、不重跑 LLM）\n")

    # ---- 红线：最先印，因为它是最贵的那条
    red = [r for a in ("frame", "blind") for r in by_arm[a] if r["tier"] == "高风险"]
    L.append("## 一、红线（矛 B 不得出现「高风险」）\n")
    L.append(f"**良性侧（陷害 + 对照）出现「高风险」`{len(red)}` 条 → "
             f"{'✅ 红线守住' if not red else '❌ 红线被击穿，如实登记（见失败清单）'}**\n")

    L.append("## 二、三支矛的战果\n")
    L.append("| 矛 | 角色 | n | 关注级及以上 | 高风险 | 绕过率 / 误报率 | 合规口径 |")
    L.append("|---|---|---|---|---|---|---|")
    for arm, grp in by_arm.items():
        role, _ = ARM_ROLE[arm]
        n = len(grp)
        risk = sum(1 for r in grp if r["tier"] in RISK)
        hi = sum(1 for r in grp if r["tier"] == "高风险")
        if arm == "evade":
            rate = f"绕过 {pct(1 - risk / n)}（检出口径给**下界**）"
        else:
            rate = f"误报 {pct(risk / n)}（上界 **{pct(cp_upper95(risk, n))}**）"
        L.append(f"| {'A' if arm == 'evade' else 'B' if arm == 'frame' else 'C'} "
                 f"`{arm}` | {role} | {n} | {risk} | {hi} | {rate} | "
                 f"{ARM_ROLE[arm][1]} |")

    # ---- 逐支矛细读
    L.append("\n## 三、逐支矛细读\n")

    ev = by_arm["evade"]
    det = sum(1 for r in ev if r["tier"] in RISK)
    L.append(f"### 矛 A `evade`（白盒·恶意）—— 检出 {det}/{len(ev)} = {pct(det / len(ev))}\n")
    L.append(f"- 单侧 95% **下界 {pct(cp_lower95(det, len(ev)))}**"
             f"（口径不挑：检出侧只给下界）")
    if det:
        L.append(f"- `min_n_for_detect`：按本次检出率，「检出率 ≥50%」要站得住需要 n≥"
                 f"**{min_n_for_detect(det / len(ev), 0.50)}**")
    else:
        L.append("- ⚠️ 一条都没检出：下界退化为 0%，此时**任何「我们能抓白盒对手」的说法都不成立**")
    L.append("- 绕过清单（按绕过余量从小到大排 —— 余量越小说明对手越贴近我们的阈值走）：")
    marg_rows = [(evade_margin(x, load_rules()), x) for x in ev if x["tier"] not in RISK]
    for (m, who), r in sorted(marg_rows, key=lambda t: t[0][0]):
        flag = "" if m > 0 else "  ⚠️ **口径异常**：该规则条件已全满足却仍未触发"
        L.append(f"  - `{r['id']}`（{r['context']}）→ {r['tier']}｜余量 **{m:+.3f}**"
                 f"｜最近的一条：{who}{flag}")
    fin = [m for (m, _), _ in marg_rows if m != float("inf")]
    L.append(f"- 余量可算率：**{len(fin)}/{len(marg_rows)}**"
             f"{'（全部有限 ✅）' if len(fin) == len(marg_rows) else ' ⚠️ 有 +inf：键名/算子解析又落空了'}"
             f"；余量中位数 **{sorted(fin)[len(fin) // 2]:+.3f}**" if fin else
             "- ⚠️ 全部余量都是 +inf → 解析失败（空值假通过），结论不可用")
    if det:
        L.append("- 被抓住的（对手也会失手）：")
        for r in (x for x in ev if x["tier"] in RISK):
            L.append(f"  - `{r['id']}`（{r['context']}）→ {r['tier']}｜命中 {', '.join(r['rules'])}｜"
                     f"dup={r['dup']:.2f} solicit={r['solicit']:.2f} spec={r['specifics']:.2f}")

    for arm, label in (("frame", "矛 B `frame`（白盒·良性·陷害）"), ("blind", "矛 C `blind`（黑盒·对照）")):
        grp = by_arm[arm]
        risk = [r for r in grp if r["tier"] in RISK]
        hi = [r for r in grp if r["tier"] == "高风险"]
        L.append(f"\n### {label} —— 误报 {len(risk)}/{len(grp)} = {pct(len(risk) / len(grp))}，"
                 f"单侧 95% 上界 **{pct(cp_upper95(len(risk), len(grp)))}**"
                 f"{' ✅ ＜10% 护栏' if cp_upper95(len(risk), len(grp)) < 0.10 else ' ❌ 超过 10% 护栏（如实登记）'}\n")
        if hi:
            L.append(f"- 🔴 **击穿红线的 {len(hi)} 条**（判到「高风险」= 对真人形态内容出具了组织化指控）：")
            for r in hi:
                L.append(f"  - `{r['id']}`（{r['context']}）｜命中 {', '.join(r['rules'])}｜"
                         f"dup={r['dup']:.2f} solicit={r['solicit']:.2f} spec={r['specifics']:.2f}")
        else:
            L.append("- ✅ 无「高风险」：对手没能把真人形态内容推到最高档")
        L.append("- 被判到关注级的（**这是「形态提示、建议人工复核」，不是定性**）：")
        for r in (x for x in grp if x["tier"] == "关注级"):
            L.append(f"  - `{r['id']}`（{r['context']}）｜命中 {', '.join(r['rules'])}｜"
                     f"dup={r['dup']:.2f} solicit={r['solicit']:.2f} spec={r['specifics']:.2f}")
        if not risk:
            L.append("  - （空）")

    L.append("\n## 三·补、对手履约台账（欠样必须解释清楚，不许悄悄少给）\n")
    L.append("| 矛 | 线程/目标 | 调用 | 接单 | **合规拒答** | 其它失败 |")
    L.append("|---|---|---|---|---|---|")
    for arm in ("evade", "frame", "blind"):
        m = payload["arms"][arm]
        L.append(f"| `{arm}` | {m['threads']}/{payload['target_per_arm']} | {m['calls_total']} | "
                 f"{m['calls_ok']} | **{m['calls_refused']}** | {m['calls_failed_other']} |")
    for arm in ("evade", "frame", "blind"):
        m = payload["arms"][arm]
        if m["threads"] >= payload["target_per_arm"]:
            continue
        L.append(f"\n- **欠样登记**：`{arm}` 只拿到 **{m['threads']}/{payload['target_per_arm']}** 条。"
                 f"原因不是故障，是**对手拒答 {m['calls_refused']} 次 / 其它失败 "
                 f"{m['calls_failed_other']} 次** —— 拒答原文见下；"
                 f"按协议第 6 节，**不通过改写 brief 去把缺口补圆**（那等于换一个更配合的对手）。")
        for ex in m.get("refusal_examples", [])[:2]:
            L.append(f"  - 拒答原文：`{ex}`")
    if payload["arms"]["evade"]["calls_refused"]:
        L.append(f"\n> **选择效应（必须与绕过率一起读）**：矛 A 的样本是「对手**愿意接单**」的子集"
                 f"（{payload['arms']['evade']['calls_refused']} 次直接拒答，拒绝写刷评话术）。"
                 f"真实水军没有这层顾虑 → **矛 A 的绕过率是乐观读法**，真实对抗只会更糟。")

    L.append("\n## 四、归因完备性（「解释权归语言」：判了就必须说清凭什么）\n")
    L.append(f"- 违规条数：**{len(viol)}** {'✅' if not viol else '❌'}")
    for v in viol[:20]:
        L.append(f"  - {v}")

    L.append("\n## 五、与自造集的对照（红队到底多花了多少代价）\n")
    L.append("| 通路 | 样本来源 | 良性侧 | 上界 | 检出侧 |")
    L.append("|---|---|---|---|---|")
    L.append("| F5 评论区（P5 自造集） | 我们写的 | 0/33 = 0.0% | 8.68% | 33/33 = 100% |")
    fr = by_arm["frame"]
    rk = sum(1 for r in fr if r["tier"] in RISK)
    L.append(f"| F5 评论区（P6 红队·白盒陷害） | 对手写的 | {rk}/{len(fr)} = {pct(rk / len(fr))} | "
             f"{pct(cp_upper95(rk, len(fr)))} | — |")
    bl = by_arm["blind"]
    rk2 = sum(1 for r in bl if r["tier"] in RISK)
    L.append(f"| F5 评论区（P6 红队·黑盒对照） | 对手写的 | {rk2}/{len(bl)} = {pct(rk2 / len(bl))} | "
             f"{pct(cp_upper95(rk2, len(bl)))} | — |")
    L.append(f"| F5 评论区（P6 红队·白盒规避） | 对手写的 | — | — | "
             f"{det}/{len(ev)} = {pct(det / len(ev))} |")
    L.append("\n读法：**自造集上的 0/33 不是护身符**。同一套阈值，换成对手写的样本，"
             "误报代价立刻显形 —— 这张表就是「把刀递给对手」的全部意义。")

    L.append("\n## 六、诚实登记（现在就写，不等被问）\n")
    L.append("1. 良性侧的「真人」是 **LLM 拟真**，不是真实平台用户 → 给出的只是**下界**；"
             "真实世界误报率仍需平台数据（`loreal-hardening-v3.md` / `docs/DEBT.md`）。")
    L.append("2. 攻防同源（同一个模型扮演对手）→ 只能说明「规则是否易被语言表层变换绕过」，"
             "不能推广为「人类水军也这样」。")
    L.append("3. 我们不掌握账号维度数据（`ATT_NO_ACCOUNT_META`）→ 白盒对手天生占优，"
             "**这是结构性让步，不是失误**；矛 A 的绕过率应被读作上界性质的对手优势。")
    L.append("4. 出口仍是「建议人工复核」：**关注级 = 形态提示，不是定性**；"
             "系统从不出「这是水军」的终审。")
    L.append("\n## 七、协议修订史\n")
    L.append("- 无修订（口径先写后测，测完未改动判据）—— 这本身是一条证据。")
    return "\n".join(L) + "\n"


def main() -> int:
    payload = json.loads(PAIRS.read_text(encoding="utf-8"))
    rules = load_rules()
    cur = rules_digest()
    fingerprint_ok = cur == payload["rules_yaml_sha256_16"]
    if not fingerprint_ok:
        print(f"❌ 靶子指纹不符：样本生成时 {payload['rules_yaml_sha256_16']}，当前 {cur}")
        print("   按协议第 2 节「靶子不变性」，跑分器**拒绝出分**。")
        print("   若确实改了规则，正确做法是重新生成红队样本（新靶子），而不是拿旧样本跑新阈值。")
        return 3

    rows = run_rows(payload["items"], rules)
    viol = attrib_violations(rows)
    text = render(payload, rows, viol, fingerprint_ok)
    P6.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")

    lines = [text, "\n===== 逐条留档（机器可复核）====="]
    for r in rows:
        lines.append(f"{r['id']}\t{r['arm']}\t{r['context']}\tn={r['n']}\t{r['tier']}\t"
                     f"dup={r['dup']:.3f}\tsolicit={r['solicit']:.3f}\t"
                     f"spec={r['specifics']:.3f}\trules={','.join(r['rules'])}\t"
                     f"attrs={','.join(r['attrs'])}")
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(text)
    print(f"留档：{LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())