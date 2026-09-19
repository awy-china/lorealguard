"""P2 评测协议 · 执行器

# 协议的核心不是"跑个分"，而是**先把口径定义清楚**

本系统不输出「AI 生成概率」，判决是「分级 + 带出处的证据」，所以传统二分类指标
（准确率 / AUC / F1）在本项目里**没有定义** —— 拿它们评价我们等于换了一把尺子。
协议必须先说清六件事，再谈数字：

| # | 指标 | 口径 | 我们的目标 |
|---|---|---|---|
| ① | **误报率 FPR** | 良性样本中判定为「关注级及以上」的比例 | **< 10%（第一指标）** |
| ② | **严重误报率** | 良性样本中判定为「高风险」的比例 | **0（硬门槛）** |
| ③ | 漏报率 FNR | 恶意样本中判定为「未触发」的比例 | 尽量低，但**次要**（宁可漏判） |
| ④ | **弃权率** | 全部样本中「未触发」的比例 | 不为 0 才好：弃权是诚实结果，不算命中也不算清白 |
| ⑤ | 定位精度 | 有真值框且检出时，区域与真值框的 IoU 中位数 | ≥ 0.5 才算"定位可用" |
| ⑥ | **承诺兑现率** | manifest 里逐条写下的 `expect` 被满足的比例 | 100%（含"已知盲区应被漏判"） |

「已声明 AI」被判「提示级」**不算误报**（《标识办法》第六条（一）：加提示即可，不是违规）。
「弃权」也**不算**误报 —— 把"我不敢判"和"我判错了"混为一谈，是评测最容易作弊的地方。

用法：  py.bat experiments/p2_bench.py
产物：  output/p2/bench_report.md  +  output/p2/bench_result.json
"""

from __future__ import annotations

import json
import statistics
import sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard import config                                    # noqa: E402
from voiceguard.core.contract import ContentItem, RiskTier       # noqa: E402
from voiceguard.guard import GuardPipeline                       # noqa: E402

P2 = ROOT / "output" / "p2"
MANIFEST = P2 / "manifest.json"
SUITE = P2 / "suite"


def iou(a, b) -> float:
    if not a or not b:
        return 0.0
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def cp_upper95(k: int, n: int, alpha: float = 0.05) -> float:
    """Clopper-Pearson 单侧 95% 置信上界：解 P(X<=k | n, p) = alpha。

    为什么不用点估计：n 条良性样本误报 k 条时，k/n 只回答"这次实测到多少"，
    回答不了"我们能承诺的上限"——而评委追问的永远是后者。
    k=0 有闭式解 1 - alpha**(1/n)；k>0 用二分（精确二项，小数样本下正态近似会失真）。
    """
    if n <= 0:
        return 1.0
    if k <= 0:
        return 1.0 - alpha ** (1.0 / n)
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        cdf = sum(comb(n, i) * mid**i * (1 - mid) ** (n - i) for i in range(k + 1))
        if cdf > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def min_n_for_fpr(k: int, target: float = 0.10, alpha: float = 0.05) -> int:
    """误报 k 条时，要让 95% 置信上界 < target，最少需要多少条良性样本。"""
    n = max(k + 1, 1)
    while cp_upper95(k, n, alpha) >= target:
        n += 1
    return n


# ------------------------------------------------------------------ 跑集子

def run_suite(manifest: dict, sheet: bool = False) -> list[dict]:
    pipe = GuardPipeline()
    rows: list[dict] = []
    for it in manifest["items"]:
        path = SUITE / it["file"]
        if not path.exists():
            raise FileNotFoundError(f"缺样本 {path}（先跑 samples/make_p2_suite.py）")
        r = pipe.run(ContentItem(image_path=str(path)), outdir=P2 / "runs",
                     tag=it["id"], sheet=sheet)
        bright = [e for e in r.evidence if e.metrics.get("polarity") == "brighter"]
        best_iou = max((iou(e.bbox, it["gt_bbox_xywh"]) for e in bright), default=0.0)
        rows.append({
            "id": it["id"], "label": it["label"], "category": it["category"],
            "desc": it["desc"], "expect": it["expect"], "file": it["file"],
            "tier": r.verdict.tier.value, "tier_rank": r.verdict.tier.rank,
            "confidence": r.verdict.confidence,
            "fired": [h.rule_id for h in r.verdict.fired],
            "benign_codes": [b.code for b in r.benign],
            "provenance_state": r.provenance.state.value if r.provenance else "n/a",
            "n_evidence": len(r.evidence), "iou": round(best_iou, 3),
            "fingerprint": r.fingerprint[:12],
        })
    return rows


# ------------------------------------------------------------------ 指标

def compute_metrics(rows: list[dict]) -> dict:
    benign = [r for r in rows if r["label"] == "benign"]
    mali = [r for r in rows if r["label"] == "malicious"]
    watch = RiskTier.WATCH.rank

    fp = [r for r in benign if r["tier_rank"] >= watch]                 # 误报（含严重）
    severe = [r for r in benign if r["tier"] == RiskTier.HIGH.value]     # 严重误报
    missed = [r for r in mali if r["tier_rank"] < watch]                # 漏报
    abstain = [r for r in rows if r["tier"] == RiskTier.UNDETERMINED.value]

    ious = [r["iou"] for r in mali if r["iou"] > 0]
    n_fp = len(fp)
    p_upper = cp_upper95(n_fp, len(benign)) if benign else None
    return {
        "n_total": len(rows), "n_benign": len(benign), "n_malicious": len(mali),
        "fpr": round(len(fp) / len(benign), 4) if benign else None,
        "fpr_target_met": (len(fp) / len(benign) < 0.10) if benign else None,
        # 统计口径：点估计达标 ≠ 统计上成立。样本补齐前，「误报率<10%」如实记为未达标。
        "fpr_upper95": round(p_upper, 4) if p_upper is not None else None,
        "fpr_stat_met": (p_upper < 0.10) if p_upper is not None else None,
        "benign_needed": min_n_for_fpr(n_fp) if benign else None,
        "severe_fpr": round(len(severe) / len(benign), 4) if benign else None,
        "severe_target_met": len(severe) == 0,
        "fnr": round(len(missed) / len(mali), 4) if mali else None,
        "abstain_rate": round(len(abstain) / len(rows), 4) if rows else None,
        "iou_median": round(statistics.median(ious), 3) if ious else None,
        "iou_n": len(ious),
        "false_positives": [f"{r['id']} {r['category']}（{r['tier']}）：{r['desc']}" for r in fp],
        "false_negatives": [f"{r['id']} {r['category']}（{r['tier']}）：{r['desc']}" for r in missed],
        "severe_list": [f"{r['id']} {r['category']}：{r['desc']}" for r in severe],
    }


# ------------------------------------------------------------------ 承诺校验

def check_promises(rows: list[dict]) -> list[str]:
    """逐条核对 manifest 里写下的承诺（含"已知盲区必须被漏判"）。"""
    bad: list[str] = []
    for r in rows:
        e = r["expect"]
        if "max_tier" in e:
            limit = RiskTier(e["max_tier"]).rank
            if r["tier_rank"] > limit:
                bad.append(f"[{r['id']}] 违反上限：{r['tier']} > {e['max_tier']}（{r['desc']}）")
        if "min_tier" in e and not e.get("known_undetectable") and not e.get("borderline"):
            if r["tier_rank"] < RiskTier(e["min_tier"]).rank:
                bad.append(f"[{r['id']}] 未达下限：{r['tier']} < {e['min_tier']}（{r['desc']}）")
        if "must_fire" in e and e["must_fire"] not in r["fired"]:
            bad.append(f"[{r['id']}] 应命中 {e['must_fire']}，实际命中 {r['fired'] or '无'}")
        if "must_benign" in e and e["must_benign"] not in r["benign_codes"]:
            bad.append(f"[{r['id']}] 应给出免责归因 {e['must_benign']}，实际 {r['benign_codes'] or '无'}")
        if "must_provenance" in e and r["provenance_state"] != e["must_provenance"]:
            bad.append(f"[{r['id']}] 元数据三态应为「{e['must_provenance']}」，实际「{r['provenance_state']}」")
        if e.get("needs_layer") and r["tier"] == RiskTier.HIGH.value:
            bad.append(f"[{r['id']}] **过度声称**：依赖未接入的层（{e['needs_layer']}）却判了高风险")
        if e.get("known_undetectable") and r["tier_rank"] >= RiskTier.WATCH.rank:
            bad.append(f"[{r['id']}] 已知盲区被检出（可疑，需复核是否误报或盲区结论已过期）")
    return bad


# ------------------------------------------------------------------ 报告

def render(manifest: dict, rows: list[dict], m: dict, bad: list[str], rules_digest: str) -> str:
    n_ok = sum(1 for r in rows if not any(b.startswith(f"[{r['id']}]") for b in bad))
    a = []
    a.append("# P2 评测结果 · LorealGuard 误报压力测试集 v0")
    a.append("")
    a.append(f"- 数据集：`{manifest['name']} {manifest['version']}`（seed={manifest['seed']}，"
             f"{m['n_total']} 条 = 良性 {m['n_benign']} + 恶意 {m['n_malicious']}）")
    a.append(f"- 判定配置：`voiceguard/rules/rules.yaml`（rules_digest=`{rules_digest}`）"
             " —— **阈值一旦冻结，不得为了让分数好看而改动**")
    a.append("")
    a.append("## 一、六项指标")
    a.append("")
    a.append("| 指标 | 口径 | 实测 | 目标 | 结论 |")
    a.append("|---|---|---|---|---|")
    a.append(f"| **① 误报率 FPR** | 良性被判「关注级及以上」的比例 | **{m['fpr']:.1%}** "
             f"（{len(m['false_positives'])}/{m['n_benign']}） | < 10% | {'✅' if m['fpr_target_met'] else '❌'} |")
    a.append(f"| **①' 误报率 95% 置信上界** | 同一结果的统计解读（Clopper-Pearson 单侧） | "
             f"**≤ {m['fpr_upper95']:.2%}** | < 10% | {'✅' if m['fpr_stat_met'] else '❌ 样本不足'} |")
    a.append(f"| **② 严重误报率** | 良性被判「高风险」的比例 | **{m['severe_fpr']:.1%}** | 0 | "
             f"{'✅' if m['severe_target_met'] else '❌'} |")
    a.append(f"| ③ 漏报率 FNR | 恶意被判「未触发」的比例 | {m['fnr']:.1%} | 尽量低（次要） | — |")
    a.append(f"| ④ 弃权率 | 全部被判「未触发」的比例 | {m['abstain_rate']:.1%} | 不为 0 才是诚实 | — |")
    a.append(f"| ⑤ 定位精度 | 恶意样本检出时与真值框 IoU 中位数 | "
             f"{m['iou_median'] if m['iou_median'] is not None else 'n/a'}（n={m['iou_n']}） | ≥ 0.5 | "
             f"{'✅' if (m['iou_median'] or 0) >= 0.5 else '—'} |")
    a.append(f"| ⑥ 承诺兑现率 | manifest 逐条 expect 被满足 | **{n_ok}/{len(rows)}** | 100% | "
             f"{'✅' if n_ok == len(rows) else '❌'} |")
    a.append("")
    a.append("> 「已声明 AI」被判「提示级」不算误报（第六条（一）：加提示即可）；"
             "「弃权」也不算误报 —— 把「我不敢判」和「我判错了」混为一谈，是评测最容易作弊的地方。")
    a.append("")
    if not m["fpr_stat_met"]:
        a.append(f"> ⚠️ **诚实登记（不许为了好看而放宽口径）**：① 的 {m['fpr']:.1%} 是**点估计**；"
                 f"{m['n_benign']} 条良性样本下零误报的 95% 置信上界是 **{m['fpr_upper95']:.2%}**，"
                 f"因此「误报率 < 10%」**在统计意义上尚未成立**。零误报时需良性样本 **≥ {m['benign_needed']} 条**"
                 f"（现有 {m['n_benign']} 条，缺口 {m['benign_needed'] - m['n_benign']} 条）。"
                 f"论证与复算见 `loreal-fpr-power-analysis.md`。")
        a.append("")

    a.append("## 二、承诺违背清单（这是本协议最有用的输出）")
    a.append("")
    if bad:
        for b in bad:
            a.append(f"- {b}")
    else:
        a.append("全部承诺已兑现。")
    a.append("")

    a.append("## 三、误报清单（我们该修什么）")
    a.append("")
    if m["false_positives"]:
        for x in m["false_positives"]:
            a.append(f"- {x}")
    else:
        a.append("无。")
    a.append("")

    a.append("## 四、漏报清单（含已知盲区）")
    a.append("")
    for x in m["false_negatives"]:
        a.append(f"- {x}")
    a.append("")

    a.append("## 五、边界与盲区样本（如实登记，不作为承诺）")
    a.append("")
    edge = [r for r in rows if r["expect"].get("borderline") or r["expect"].get("blind_spot")
            or r["expect"].get("needs_layer")]
    if edge:
        a.append("| id | 类别 | 类型 | 实测判定 | 说明 |")
        a.append("|---|---|---|---|---|")
        for r in edge:
            e = r["expect"]
            kind = ("边界样本" if e.get("borderline") else
                    "已知盲区" if e.get("blind_spot") else "依赖未接入层")
            note = e.get("note") or e.get("blind_spot") or e.get("needs_layer") or ""
            a.append(f"| {r['id']} | {r['category']} | {kind} | {r['tier']} | {note} |")
        a.append("")
        a.append("> 这一节的存在就是协议的一部分：**把检出下界和原理盲区写进公告**，"
                 "而不是等别人发现后再说。")
    else:
        a.append("无。")
    a.append("")

    a.append("## 六、逐条结果")
    a.append("")
    a.append("| id | 标签 | 工况 | 判定 | 命中规则 | 免责归因 | 标识三态 | 证据 | IoU |")
    a.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        a.append(f"| {r['id']} | {r['label']} | {r['category']} | {r['tier']} | "
                 f"{'、'.join(r['fired']) or '—'} | {'、'.join(r['benign_codes']) or '—'} | "
                 f"{r['provenance_state']} | {r['n_evidence']} | {r['iou'] or '—'} |")
    a.append("")
    a.append("## 七、复现方式")
    a.append("")
    a.append("```bash")
    a.append("env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p2_suite.py   # 重建数据集并比对 sha256")
    a.append("env -u PYTHONPATH .venv/Scripts/python.exe experiments/p2_bench.py     # 重跑本表")
    a.append("```")
    a.append("")
    return "\n".join(a)


def main() -> int:
    if not MANIFEST.exists():
        raise SystemExit(f"缺 manifest：{MANIFEST}（先跑 samples/make_p2_suite.py）")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    rows = run_suite(manifest, sheet=("--sheet" in sys.argv))
    m = compute_metrics(rows)
    bad = check_promises(rows)

    import hashlib
    rules_digest = hashlib.sha256(
        (ROOT / "voiceguard" / "rules" / "rules.yaml").read_bytes()).hexdigest()[:16]

    (P2 / "bench_result.json").write_text(json.dumps({
        "manifest_version": manifest["version"], "seed": manifest["seed"],
        "rules_digest": rules_digest, "metrics": m, "violations": bad, "items": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    md = render(manifest, rows, m, bad, rules_digest)
    (P2 / "bench_report.md").write_text(md, encoding="utf-8")

    for r in rows:
        flag = "FP" if (r["label"] == "benign" and r["tier_rank"] >= RiskTier.WATCH.rank) else \
               ("MISS" if (r["label"] == "malicious" and r["tier_rank"] < RiskTier.WATCH.rank) else "ok")
        print(f"{r['id']} {r['label'][:4]:5} {r['category'][:6]:7} {r['tier']:5} "
              f"IoU={r['iou'] or 0:.2f}  {flag}")
    print()
    print(f"① 误报率 FPR      = {m['fpr']:.1%}  (目标 <10%)  {'PASS' if m['fpr_target_met'] else 'FAIL'}")
    _stat = ("PASS" if m["fpr_stat_met"]
             else f"未达标（零误报需良性 n>={m['benign_needed']}，现 {m['n_benign']}）")
    print(f"①' 误报率95%上界   = {m['fpr_upper95']:.1%}  (目标 <10%)  {_stat}")
    print(f"② 严重误报率      = {m['severe_fpr']:.1%}  (目标 0)     {'PASS' if m['severe_target_met'] else 'FAIL'}")
    print(f"③ 漏报率 FNR      = {m['fnr']:.1%}")
    print(f"④ 弃权率          = {m['abstain_rate']:.1%}")
    print(f"⑤ 定位 IoU 中位数 = {m['iou_median']}  (n={m['iou_n']})")
    print(f"⑥ 承诺兑现        = {len(rows) - len({b[1:5] for b in bad})}/{len(rows)}"
          f"{'  PASS' if not bad else '  FAIL'}")
    if bad:
        print("\n违背清单：")
        for b in bad:
            print("  -", b)
    print(f"\n报告：{P2 / 'bench_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())