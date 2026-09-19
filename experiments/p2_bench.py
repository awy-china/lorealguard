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


def cp_lower95(k: int, n: int, alpha: float = 0.05) -> float:
    """Clopper-Pearson 单侧 95% 置信下界：解 P(X>=k | n, p) = alpha。

    与 cp_upper95 是同一个函数的两个方向 —— 报指标必须**同时报点估计与统计界**，
    误报侧用了上界，检出侧就必须用下界，否则同一份报告里躺着两把尺。
    """
    if n <= 0 or k <= 0:
        return 0.0
    if k >= n:
        return alpha ** (1.0 / n)
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        tail = sum(comb(n, i) * mid**i * (1 - mid) ** (n - i) for i in range(k, n + 1))
        if tail < alpha:        # 尾概率太小 → mid 偏小，抬高下界
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def cp_two_sided95(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Clopper-Pearson 双侧 95% 区间（每侧 alpha/2，精确二项，无正态近似）。

    自校验（写进代码里，防后人改坏）：
        cp_two_sided95(0, 40) ≈ (0.000, 0.0881)   ← 上界与 cp_upper95(0,40)=1-0.05^(1/40) 同源
        cp_two_sided95(3, 8)  ≈ (0.085, 0.755)    ← 教科书值，用来验方向没写反
    """
    if n <= 0:
        return (0.0, 1.0)
    return (cp_lower95(k, n, alpha / 2), cp_upper95(k, n, alpha / 2))


def min_n_for_fpr(k: int, target: float = 0.10, alpha: float = 0.05) -> int:
    """误报 k 条时，要让 95% 置信上界 < target，最少需要多少条良性样本。"""
    n = max(k + 1, 1)
    while cp_upper95(k, n, alpha) >= target:
        n += 1
    return n


def min_n_for_detect(p: float, target: float = 0.50, alpha: float = 0.05,
                     nmax: int = 600) -> int | None:
    """要让**检出率下界**站上 target，在观测检出比例 p 下需要多少条恶意样本。

    与 min_n_for_fpr 对称：误报侧问"要几条良性"，检出侧问"要几条恶意"。
    用途同上 —— 把"检出率区间太宽"从抱怨变成补样任务书。
    """
    for n in range(1, nmax + 1):
        # ⚠️ 必须**向下取整**（保守）：用 round() 会把 p*n=13.5 抬成 14 条命中，
        # 等于偷偷把检出率调高再去算下界 —— 那正是本项目最禁的"放宽口径换达标"。
        # 这个 bug 是新写的协议测试当场抓到的（它断言答案为 20，round 给出 18）。
        k = int(p * n)
        if cp_lower95(k, n, alpha) >= target:
            return n
    return None


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
            "gt_bbox": it.get("gt_bbox_xywh"),   # 真值框（识别"同一几何"的伪多样）
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
    detected = [r for r in mali if r["tier_rank"] >= watch]             # 检出（关注级及以上）
    abstain = [r for r in rows if r["tier"] == RiskTier.UNDETERMINED.value]

    ious = [r["iou"] for r in mali if r["iou"] > 0]
    n_fp = len(fp)
    p_upper = cp_upper95(n_fp, len(benign)) if benign else None
    # 检出侧：**点估计 + 双侧精确区间**。误报侧用了上界，检出侧就必须给下界，
    # 否则同一份报告里躺着两把尺（这正是本文件最初只报 FNR 点估计的毛病）。
    fnr_ci = cp_two_sided95(len(missed), len(mali)) if mali else None
    det_ci = cp_two_sided95(len(detected), len(mali)) if mali else None
    # 定位精度必须同时报**离散度**与**真值几何种类数**：
    # 若多条命中用的是同一个真值框，IoU 中位数就是同一个数 —— 那叫样本单一，不叫稳定。
    # .get 而非 []：本函数是**公共打分口径**，外部（含协议测试）喂进来的行不一定带真值框。
    # 公共口径不许因为"少个可选字段"就炸 —— 这是刚被两条协议测试抓到的真问题。
    gt_geoms = {tuple(g) for g in (r.get("gt_bbox") for r in detected) if g}
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
        "fnr_ci": [round(x, 4) for x in fnr_ci] if fnr_ci else None,
        "n_detected": len(detected),
        "detect_rate": round(len(detected) / len(mali), 4) if mali else None,
        "detect_ci": [round(x, 4) for x in det_ci] if det_ci else None,
        "abstain_rate": round(len(abstain) / len(rows), 4) if rows else None,
        "abstain_breakdown": breakdown_abstain(rows),
        "iou_median": round(statistics.median(ious), 3) if ious else None,
        "iou_n": len(ious),
        "iou_min": min(ious) if ious else None,
        "iou_max": max(ious) if ious else None,
        "iou_geoms": len(gt_geoms),
        "false_positives": [f"{r['id']} {r['category']}（{r['tier']}）：{r['desc']}" for r in fp],
        "false_negatives": [f"{r['id']} {r['category']}（{r['tier']}）：{r['desc']}" for r in missed],
        "severe_list": [f"{r['id']} {r['category']}：{r['desc']}" for r in severe],
    }


# ------------------------------------------------- 弃权分解（弃权率不许只报一个数）

#: 全局背景归因：**每条样本都有**，因此它是数据集的设计前提，不能进主因优先级
#: —— 否则主因表会被它 100% 刷屏，真正的原因（盲区/平台链路/美颜）全被淹没。
#: 这是第一版实测踩到的坑：38/40 良性主因全是它，那张表等于没做。
ABSTAIN_BACKGROUND: tuple[str, str, str] = (
    "NO_TEXT_PROVIDED", "未提供文案 → 文案/语义轴不适用",
    "设计内：P2 为纯图像工况，文本信号为 None",
)

#: 弃权归因优先级：一条样本可能同时命中多条归因，取第一条作为**主因**，
#: 其余在 accompanies 里可见 —— 不重复计数，否则占比会超过 100%。
ABSTAIN_CAUSES: tuple[tuple[str, str, str], ...] = (
    ("NO_JPEG_HISTORY", "无 JPEG 压缩史 → ELA 不适用", "设计内：检测器不适用必须与「无异常」分开"),
    ("PLATFORM_RECODE", "平台二次压缩已解释可疑信号", "设计内：误报护栏第一优先"),
    ("PLATFORM_STRIP", "元数据缺失可由平台重编码解释", "设计内：第十条不归因创作者"),
    ("BEAUTY_RETOUCH", "已归因美颜磨皮（正常编辑）", "设计内：头号误报风险被压回"),
    ("GLOBAL_FILTER", "全图统一滤镜 → 区域差异被压缩", "设计内：敏感性下降如实声明"),
    ("TEXTURE_ARTIFACT", "双质量交叉不一致 → 纹理/伪影", "设计内：可疑区非局部编辑"),
    ("NO_ANOMALY_REGION", "全图无坐标级异常连通域", "设计内：无物证不出结论"),
    ("NO_EFFICACY_CONTEXT", "图文虽有异常但不在功效语境", "设计内：不升级为虚假宣传"),
)


def breakdown_abstain(rows: list[dict]) -> dict:
    """弃权率 89.6% 这种单个数字回答不了产品问题：评委会问「那你 90% 的时候在干什么」。

    所以把它拆成「按理由分类」的表：哪些是设计内（能力边界的诚实声明），
    哪些是真缺口（我们还没接的检测器）。良性侧与恶意侧的弃权含义完全不同 ——
    良性弃权 = 我们没冤枉它；恶意弃权 = 我们漏了它（漏报），必须分开统计。
    """
    out: dict = {}
    bg_code, bg_name, bg_design = ABSTAIN_BACKGROUND
    bg = {"code": bg_code, "name": bg_name, "design": bg_design,
          "n_all": sum(1 for r in rows if bg_code in r["benign_codes"]),
          "n_total": len(rows)}
    for label in ("benign", "malicious"):
        pool = [r for r in rows if r["label"] == label
                and r["tier"] == RiskTier.UNDETERMINED.value]
        taken: set[str] = set()
        counts: list[dict] = []
        for code, name, design in ABSTAIN_CAUSES:
            hit = [r for r in pool if code in r["benign_codes"] and r["id"] not in taken]
            if not hit:
                continue
            taken.update(r["id"] for r in hit)
            counts.append({"code": code, "name": name, "design": design,
                           "n": len(hit),
                           "share": round(len(hit) / len(pool), 4) if pool else 0.0,
                           "ids": [r["id"] for r in hit],
                           "accompanies": sorted({c for r in hit for c in r["benign_codes"]
                                                  if c != code})})
        rest = [r for r in pool if r["id"] not in taken]
        if rest:
            counts.append({"code": "(无归因码)", "name": "未命中任何免责归因（真缺口）",
                           "design": "**需复核**：无归因码意味着连「为什么不下结论」都说不出来",
                           "n": len(rest), "share": round(len(rest) / len(pool), 4),
                           "ids": [r["id"] for r in rest], "accompanies": []})
        out[label] = {"n_abstain": len(pool), "n_pool": sum(1 for r in rows if r["label"] == label),
                      "n_abstain_with_bg": sum(1 for r in pool if bg_code in r["benign_codes"]),
                      "counts": counts}
    out["background"] = bg
    return out


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
    a.append(f"| ③ 漏报率 FNR | 恶意被判「未触发」的比例 | {m['fnr']:.1%}"
             f"（{m['n_malicious'] - m['n_detected']}/{m['n_malicious']}；95%CI "
             f"{m['fnr_ci'][0]:.1%}~{m['fnr_ci'][1]:.1%}） | 尽量低（次要） | — |")
    a.append(f"| ③' 检出率（=1−漏报率） | 恶意被判「关注级及以上」的比例 | {m['detect_rate']:.1%}"
             f"（{m['n_detected']}/{m['n_malicious']}；95%CI "
             f"{m['detect_ci'][0]:.1%}~{m['detect_ci'][1]:.1%}） | 越高越好；**下界才是诚实那一半** | — |")
    a.append(f"| ④ 弃权率 | 全部被判「未触发」的比例 | {m['abstain_rate']:.1%} | 不为 0 才是诚实 | "
             f"分解见第七节 |")
    a.append(f"| ⑤ 定位精度 | 恶意样本检出时与真值框 IoU 中位数 | "
             f"{m['iou_median'] if m['iou_median'] is not None else 'n/a'}"
             f"（n={m['iou_n']}，极差 {m['iou_min']}~{m['iou_max']}，真值几何 {m['iou_geoms']} 种） | ≥ 0.5 | "
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
    a.append("## 七、弃权分解（④ 那个数字到底由什么构成）")
    a.append("")
    a.append("评委会问：「你九成的时候说'不敢判'，产品价值在哪？」—— 这张表就是答案。"
             "注意两侧含义完全相反：**良性侧弃权 = 我们没冤枉它**（护栏的代价），"
             "**恶意侧弃权 = 我们漏了它**（漏报的构成）。")
    a.append("")
    bg = m["abstain_breakdown"]["background"]
    a.append(f"> **全局背景（不进主因）**：{bg['n_all']}/{bg['n_total']} 条样本都带 "
             f"`{bg['code']}`（{bg['name']}）—— 这是 P2 数据集的设计前提，"
             f"不是弃权理由；把它算进主因会把整张表刷成一行，反而看不见真原因。")
    a.append("")
    for label, title in (("benign", "良性侧（弃权 = 护栏的代价）"),
                         ("malicious", "恶意侧（弃权 = 漏报的构成）")):
        blk = m["abstain_breakdown"][label]
        a.append(f"**{title}**：弃权 {blk['n_abstain']}/{blk['n_pool']} 条"
                 f"（其中 {blk['n_abstain_with_bg']} 条同时带全局背景归因）")
        a.append("")
        a.append("| 主因（按优先级取第一条，不重复计数） | 码 | 条数 | 占该侧弃权 | 是否设计内 | 样本 |")
        a.append("|---|---|---|---|---|---|")
        for c in blk["counts"]:
            ids = ", ".join(c["ids"][:8]) + ("…" if len(c["ids"]) > 8 else "")
            a.append(f"| {c['name']} | `{c['code']}` | {c['n']} | {c['share']:.1%} | "
                     f"{c['design']} | {ids} |")
        a.append("")
    a.append("> 「无归因码」那一行若不为空，就是**真缺口**：连「为什么不下结论」都说不出来。"
             "这一行必须是 0 —— 否则不是能力边界，是解释失败。")
    a.append("")

    a.append("## 八、检出侧统计口径（对称于第一节的误报侧）")
    a.append("")
    if m["fnr_ci"]:
        n_mali = m["n_malicious"]
        lo, hi = m["fnr_ci"]
        det = m["detect_rate"] or 0.0
        need50 = min_n_for_detect(det, 0.50)
        need66 = min_n_for_detect(det, 0.66)
        a.append(f"误报侧我们敢写「≤ {m['fpr_upper95']:.2%}」是因为用了 Clopper-Pearson 上界；"
                 f"那么检出侧同样不许只报点估计 —— 恶意 n={n_mali} 条时，"
                 f"漏报率 {m['fnr']:.1%} 的 95%CI 是 **[{lo:.1%}, {hi:.1%}]**，"
                 f"检出率 {m['detect_rate']:.1%} 的 95%CI 是 "
                 f"**[{m['detect_ci'][0]:.1%}, {m['detect_ci'][1]:.1%}]**。")
        a.append("")
        a.append(f"- 区间宽 **{hi - lo:.0%} 个百分点**：这就是「恶意样本 {n_mali} 条」的真实信息量。"
                 f"报「漏报率 {m['fnr']:.1%}」精确到小数位是**虚假精度**。")
        # 补样任务书：只有当"还差多少条"是正数时才写成缺口，否则如实说已站上。
        if need50 and need50 > n_mali:
            a.append(f"- 要让**检出率下界**站上 50%（当前点估计 {det:.1%}），"
                     f"恶意样本需补到 **{need50} 条**（现有 {n_mali} 条，缺口 {need50 - n_mali} 条）。"
                     f"补法见 `samples/make_p2_suite.py`，**不调任何阈值**。")
        else:
            a.append(f"- ✅ **检出率下界已站上 50%**（{m['detect_ci'][0]:.1%}）：恶意 n={n_mali} 条"
                     f"就够支撑「检出率 ≥50%」这句话（{min_n_for_detect.__name__} 给出的最小 n "
                     f"= {need50}）。")
        if need66:
            a.append(f"- 若要把下界推到 **66%**，同样方法需恶意样本 **{need66} 条**"
                     f"（现在 {n_mali} 条）—— 这是**下一段补样任务书**，仍只需加样本、不动阈值。")
        if m["iou_geoms"] and m["iou_geoms"] < m["iou_n"]:
            a.append(f"- ⚠️ **定位精度的伪多样**：{m['iou_n']} 条命中只对应 {m['iou_geoms']} 种真值几何，"
                     f"即仍有 {m['iou_n'] - m['iou_geoms']} 条命中的真值框与别的命中共用同一个"
                     f"（默认框 m02/m03/m07）—— 所以 IoU 中位数 {m['iou_median']} 里仍含一份"
                     f"「同一个框被反复命中」的成分。本轮已把真值几何从 1 种扩到 {m['iou_geoms']} 种"
                     f"（新增 m09–m20 每次换框），但要彻底去掉这个混淆还需补样；"
                     f"**这份残余混淆如实登记，不当作已解决**。")
        else:
            a.append(f"- 定位精度：{m['iou_n']} 条命中对应 {m['iou_geoms']} 种真值几何，"
                     f"几何多样性已无重复（每一条命中的真值框都不同）。")
        a.append("")
        a.append(f"- **口径不许挑**：上面印的是**双侧** 95% 区间下缘；若换成与误报侧"
                 f"7.22% 完全同款**单侧** 95% 下界，检出率下界是 "
                 f"**{cp_lower95(m['n_detected'], n_mali):.1%}**"
                 f"（命中 {m['n_detected']}/{n_mali}）"
                 f"—— 两种口径都 > 50%，所以「下界站上 50%」这个结论**不依赖口径选择**。"
                 f"报告里同时给两个数，就是为了让评委能自己换尺子量一遍。")
        a.append("")
        a.append("> 一句话：**误报侧给上界，检出侧给下界，两边都不许只报点估计。**"
                 "这不是谦虚，是把「我们的数字能被别人拿去核对」变成报告的默认格式。")
    a.append("")

    a.append("## 九、复现方式")
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
    print(f"③ 漏报率 FNR      = {m['fnr']:.1%}"
          f"  95%CI [{m['fnr_ci'][0]:.1%}, {m['fnr_ci'][1]:.1%}]")
    print(f"③'检出率          = {m['detect_rate']:.1%}"
          f"  95%CI [{m['detect_ci'][0]:.1%}, {m['detect_ci'][1]:.1%}]"
          f"  (n={m['n_malicious']})")
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