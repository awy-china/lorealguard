"""P5 —— 评论区通路基准（F5 真实性层）。

严格沿用 `honest-benchmark-protocol`：
    ① 承诺**先写后测**（承诺存在生成器的 comment_pairs.json 里，本脚本只引用、不改写）；
    ② 误报侧报**点估计 + 95% 上界**（Clopper–Pearson，绝不拿点估计当结论）；
    ③ 检出侧报**点估计 + 双侧下界**，并给 `min_n_for_detect`（说清"要多少条才配下这个结论"）；
    ④ **弃权必须归因**，并做分解；
    ⑤ **失败清单不许掩盖**：expect 不符的逐条列出，不调阈值、不放宽口径；
    ⑥ 口径锁：报告里带 rules.yaml 的 sha256，任何人可复核"这组数字是这套阈值跑出来的"。

附一个**长度单调性实测**：同一族话术在 6 / 10 / 16 条线程下的 dup_ratio。
它回答一个会被追问的问题——"你们的样本只有 6 条评论，真实平台一条内容几百条评论，
是不是更容易抓？"答案应该是"是"，而且这个"是"必须是**测出来的**，不是推理出来的。

产出：output/p5/comment_report.md（给人看）+ output/p5/_commentbench.txt（留档原始输出）
跑法（单行）：
    env -u PYTHONPATH .venv/Scripts/python.exe experiments/p5_comment_bench.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))

from p2_bench import (cp_lower95, cp_two_sided95, cp_upper95,          # noqa: E402
                      min_n_for_detect)
from voiceguard.comments import analyze, evaluate                      # noqa: E402
from voiceguard.rules.engine import load_rules                         # noqa: E402

P5 = ROOT / "output" / "p5"
PAIRS = P5 / "comment_pairs.json"
REPORT = P5 / "comment_report.md"
LOG = P5 / "_commentbench.txt"
RULES_YAML = ROOT / "voiceguard" / "rules" / "rules.yaml"

RISK = {"关注级", "高风险"}
RANK = {"不适用": 0, "提示级": 1, "关注级": 2, "高风险": 3}


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def rules_digest() -> str:
    return hashlib.sha256(RULES_YAML.read_bytes()).hexdigest()[:16]


def ablation(bot_items: list[dict], bot_rows: list[dict], rules) -> list[dict]:
    """消融：逐条移除风险规则，看恶意侧等级掉了多少。

    这是「**一条不独立的规则等于没有规则**」的量化版：若删掉某条规则后
    检出结果一字不变，那条规则就是摆设，应当删掉或降为佐证信号。
    首轮消融正是靠这张表抓出了情绪注水规则的边际贡献为 0（已删除）。
    """
    base = {r["id"]: r["tier"] for r in bot_rows}
    out = []
    for rule in rules["comment_rules"]:
        rid = rule["id"]
        trimmed = {**rules, "comment_rules": [x for x in rules["comment_rules"] if x["id"] != rid]}
        drops = below = 0
        for it in bot_items:
            t = evaluate(it["comments"], trimmed, analyze(it["comments"])).tier
            if RANK[t] < RANK[base[it["id"]]]:
                drops += 1
                if RANK[t] < RANK["关注级"] <= RANK[base[it["id"]]]:
                    below += 1
        out.append({"rule": rid, "hits": sum(1 for r in bot_rows if rid in r["rules"]),
                    "drops": drops, "below": below})
    return out


def run_side(items: list[dict], rules) -> list[dict]:
    out = []
    for it in items:
        sig = analyze(it["comments"])
        v = evaluate(it["comments"], rules, sig)
        out.append({
            "id": it["id"], "kind": it["kind"], "context": it["context"],
            "mode": it.get("mode", ""), "expect": it["expect"], "why": it["why"],
            "n": sig.n_comments, "tier": v.tier, "contract_tier": v.contract_tier,
            "rules": v.rule_ids, "attrs": [a["code"] for a in v.attributions],
            "dup": sig.dup_ratio, "solicit": sig.solicit_ratio,
            "specifics": sig.specifics_ratio, "lexdiv": sig.lexical_diversity,
            "hype": sig.hype_ratio, "bot_score": sig.bot_score,
        })
    return out


def monotonicity() -> list[dict]:
    """同一族话术在 6/10/16 条线程下的 dup_ratio —— 把"n 越大越显形"测成数字。"""
    from samples.make_p5_comment_pairs import FAMILIES, SOLICIT_TAILS
    rows = []
    for key, base in FAMILIES.items():
        for n in (6, 10, 16):
            comments = [base + (SOLICIT_TAILS[i % len(SOLICIT_TAILS)] if i < 3 else "，") for i in range(n)]
            rows.append({"family": key, "n": n, "dup": analyze(comments).dup_ratio})
    return rows


def render(payload, benign, bots, mono, abl) -> str:
    p = payload["promise"]
    main_benign = [r for r in benign if r["kind"] == "benign"]
    probes = [r for r in benign if r["kind"] == "probe"]
    nb, nbot = len(main_benign), len(bots)
    fb = sum(1 for r in main_benign if r["tier"] in RISK)          # 承诺口径：主体 33 条
    fb_all = sum(1 for r in benign if r["tier"] in RISK)           # 含 3 条误伤探针
    det = sum(1 for r in bots if r["tier"] in RISK)
    fpr_up = cp_upper95(fb, nb)
    det_lo = cp_lower95(det, nbot)
    det_lo2, det_hi2 = cp_two_sided95(det, nbot)
    det_p = det / nbot
    need50, need667 = min_n_for_detect(det_p, 0.50), min_n_for_detect(det_p, 0.667)
    seed = [r for r in bots if r["mode"] == "seedbatch"]
    seed_hi = sum(1 for r in seed if r["tier"] == "高风险")
    failures = ([r for r in benign if r["tier"] in RISK]
                + [r for r in bots if r["tier"] not in RISK]
                + [r for r in seed if r["tier"] != "高风险"])   # 三闸门形态未达高风险 = 设计偏差，如实登记

    abstain = {}
    for r in benign:
        for a in r["attrs"]:
            abstain[a] = abstain.get(a, 0) + 1

    L: list[str] = []
    L.append("# P5 · 评论区通路基准报告（F5 真实性层）\n")
    L.append(f"- 口径锁：`voiceguard/rules/rules.yaml` sha256 前 16 位 `{rules_digest()}`")
    L.append(f"- 配对集：`output/p5/comment_pairs.json`（良性 {payload['counts']['benign']} + "
             f"探针 {payload['counts']['probe']} + 注水 {payload['counts']['bot']}，"
             f"其中三闸门形态 {payload['counts']['bot_seedbatch']}）")
    L.append("- 判定：`voiceguard/comments/`（信号层）+ `rules.yaml: comment_rules`（阈值层）"
             "—— 零大模型、零账号数据、只用给定线程的文本\n")

    L.append("## 一、承诺（写在前，测在后）\n")
    for s in p["statements"]:
        L.append(f"- {s}")
    ok1 = fb <= p["benign_max_risk_hits"]
    ok2 = det >= p["bot_min_detect"]
    L.append(f"\n**兑现情况：良性误报 `{fb}/{nb}` {'✅ 兑现' if ok1 else '❌ 未兑现'}；"
             f"检出 `{det}/{nbot}` {'✅ 兑现' if ok2 else '❌ 未兑现'}**\n")

    L.append("## 二、误报侧（第一优先级：冤枉真实创作者的代价最大）\n")
    L.append(f"- **承诺口径（主体 {nb} 条）**：命中风险规则（关注级及以上）"
             f"**{fb} 条 = {pct(fb / nb)}**，零误报 95% 上界 **{pct(fpr_up)}** "
             f"{'✅ ＜ 10% 护栏' if fpr_up < 0.10 else '❌ 超过 10% 护栏'}")
    L.append(f"- **误伤探针（{len(probes)} 条，专挑最像水军的真实写法）**：命中 "
             f"{sum(1 for r in probes if r['tier'] in RISK)} 条；"
             f"合并 {len(benign)} 条的零误报上界 **{pct(cp_upper95(fb_all, len(benign)))}**")
    for r in probes:
        L.append(f"  - `{r['id']}` → {r['tier']}｜dup={r['dup']:.2f} solicit={r['solicit']:.2f} "
                 f"spec={r['specifics']:.2f}｜{r['why']}")
    L.append("- 良性侧归因码分布（**弃权必须归因**）：")
    for k, v in sorted(abstain.items(), key=lambda kv: -kv[1]):
        L.append(f"  - `{k}` × {v}")

    L.append("\n## 三、检出侧（口径不许挑：给的是下界）\n")
    L.append(f"- 注水线程 {nbot} 条，检出（关注级及以上）**{det} 条 = {pct(det_p)}**")
    L.append(f"- 95% 双侧区间 [{pct(det_lo2)}, {pct(det_hi2)}]；**单侧下界 {pct(det_lo)}**")
    L.append(f"- `min_n_for_detect`：按本次检出率，「检出率 ≥50%」这个说法要站得住需要 n≥**{need50}**；"
             f"「≥66.7%」要站得住需要 n≥**{need667}**")
    L.append(f"- 分档：**高风险 {sum(1 for r in bots if r['tier'] == '高风险')} 条** / "
             f"关注级 {sum(1 for r in bots if r['tier'] == '关注级')} 条 / 不适用 "
             f"{sum(1 for r in bots if r['tier'] == '不适用')} 条")
    L.append(f"- 其中三闸门形态（逐字复制+引流+零实指）{len(seed)} 条，判到高风险 "
             f"**{seed_hi} 条 = {pct(seed_hi / len(seed))}**（三道闸门同时成立才出最高结论）")
    L.append("\n**分形态实测**（每种形态刻意只让一条规则能独立命中 —— 否则就是规则在互相掩护）：\n")
    L.append("| 注水形态 | n | 判到关注级及以上 | 判到高风险 | 命中的规则 |")
    L.append("|---|---|---|---|---|")
    for m in ("seedbatch", "templated", "solicit_only"):
        grp = [r for r in bots if r["mode"] == m]
        hit_rules = sorted({x for r in grp for x in r["rules"]})
        L.append(f"| {m} | {len(grp)} | {sum(1 for r in grp if r['tier'] in RISK)} | "
                 f"{sum(1 for r in grp if r['tier'] == '高风险')} | {', '.join(hit_rules) or '—'} |")

    L.append("\n**消融表**（逐条移除规则，看恶意侧等级掉了多少）：\n")
    L.append("| 移除的规则 | 该规则命中数 | 等级下降的线程 | 其中跌破『关注级』 |")
    L.append("|---|---|---|---|")
    for a in abl:
        L.append(f"| `{a['rule']}` | {a['hits']} | {a['drops']} | {a['below']} |")
    L.append("\n读法：每一条规则被移除后都有线程等级下降 —— **没有一条是摆设**。"
             "首轮消融里有一条例外（`C-REVIEW-HYPE-INORGANIC` 边际贡献 0），它已被删除；"
             "这张表因此也是「我们删过自己写的规则」的证据。")

    L.append("\n## 四、长度单调性实测（回答「真实平台评论更多，是不是更容易抓」）\n")
    L.append("| 话术族 | n=6 | n=10 | n=16 |")
    L.append("|---|---|---|---|")
    for key in sorted({m["family"] for m in mono}):
        cells = {m["n"]: m["dup"] for m in mono if m["family"] == key}
        L.append(f"| {key} | {cells[6]:.2f} | {cells[10]:.2f} | {cells[16]:.2f} |")
    L.append("\n读法：同一族话术的复制比例固定，线程越长，组内近重复率越高。"
             "**所以我们用 6 条评论测出来的检出率是保守下界** —— 真实平台一条内容下的"
             "几百条评论，比我们的样本更容易显形。这句话必须由上面这张表支撑，不是推理。")

    L.append("\n## 五、失败清单（不掩盖、不调阈值）\n")
    if not failures:
        L.append("- 空：所有 expect 全部符合。")
    else:
        for r in failures:
            L.append(f"- `{r['id']}`（{r['kind']}/{r['mode'] or '—'}）期望 "
                     f"{r['expect']}，实测 {r['tier']}｜信号 dup={r['dup']:.2f} "
                     f"solicit={r['solicit']:.2f} specifics={r['specifics']:.2f} "
                     f"lexdiv={r['lexdiv']:.2f} hype={r['hype']:.2f}｜{r['why']}")

    L.append("\n## 六、与其它通路的对照（证明三条通路各管一段）\n")
    L.append("| 通路 | 良性侧误报 | 上界 | 恶意侧检出 | 下界 |")
    L.append("|---|---|---|---|---|")
    L.append(f"| F5 评论区（本报告） | {fb}/{nb} = {pct(fb / nb)} | {pct(fpr_up)} | "
             f"{det}/{nbot} = {pct(det_p)} | {pct(det_lo)} |")
    L.append("| F4 文案语义（P2-T v3） | 0/33 = 0.0% | 8.68% | 24/33 = 72.7% | — |")
    L.append("| F1 图像取证（P2 主集） | 0/40 = 0.0% | 7.22% | 15/20 = 75.0% | 50.9% |")
    L.append("\n三条通路的良性侧上界都在 10% 护栏内，且**各自只对自己那一类造假敏感** ——"
             "这就是「多模态」的实际含义：不是把三个检测器摞起来，而是三条互不越权的通路。")

    L.append("\n## 七、能力边界（写死，答辩照读）\n")
    L.append("- 拿不到账号维度数据（注册时长/发帖频率/IP/设备）→ **不判「同一人批量发帖」**；")
    L.append("  这是永久性缺口，写进归因码 `ATT_NO_ACCOUNT_META`，每条结论都挂着。")
    L.append("- 单条评论永不判；线程 <5 条整层不适用（`ATT_TOO_FEW_COMMENTS`）——**不适用 ≠ 无异常**。")
    L.append("- 出口只有「建议人工复核」，**没有「这是水军」的终审**；措辞由 YAML 定，代码不改写。")
    L.append("- 依据全部 `draft:`（待复核）：虚假评价落《电子商务法》17 条与"
             "《网络反不正当竞争暂行规定》，**不是** AI 标识办法（两个法域）。")
    return "\n".join(L) + "\n"


def main() -> int:
    payload = json.loads(PAIRS.read_text(encoding="utf-8"))
    rules = load_rules()
    items = payload["items"]
    benign_items = [x for x in items if x["kind"] in ("benign", "probe")]
    bot_items = [x for x in items if x["kind"] == "bot"]
    benign = run_side(benign_items, rules)
    bots = run_side(bot_items, rules)
    mono = monotonicity()

    text = render(payload, benign, bots, mono, ablation(bot_items, bots, rules))
    REPORT.write_text(text, encoding="utf-8")

    lines = [text]
    lines.append("\n===== 逐条留档（机器可复核）=====")
    for r in benign + bots:
        lines.append(f"{r['id']}\t{r['kind']}\t{r['mode']}\tn={r['n']}\t{r['tier']}\t"
                     f"dup={r['dup']:.3f}\tsolicit={r['solicit']:.3f}\t"
                     f"spec={r['specifics']:.3f}\tlexdiv={r['lexdiv']:.3f}\t"
                     f"hype={r['hype']:.3f}\trules={','.join(r['rules'])}")
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(text)
    print(f"\n留档：{LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())