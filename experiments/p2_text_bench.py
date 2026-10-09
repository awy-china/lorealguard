"""P2-T 跑分器：**文案通路**的误报率 / 检出率 / 三道闸门表现。

不重复造指标函数：直接复用主集 `experiments/p2_bench.py` 里的
`compute_metrics` / `check_promises` —— **两把尺必须完全同一把**，
否则"文案通路误报率"和"图像通路误报率"就不可比了（这正是本项目最忌讳的事）。

跑法（单行）：
    env -u PYTHONPATH .venv/Scripts/python.exe experiments/p2_text_bench.py
产出：
    output/p2/text_report.md     人读报告
    output/p2/text_metrics.json  机器可读的指标（含全部区间）
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard.core.contract import ContentItem, RiskTier                        # noqa: E402
from voiceguard.guard import GuardPipeline                                        # noqa: E402
from voiceguard.semantic import claims                                            # noqa: E402

P2 = ROOT / "output" / "p2"
SUITE = P2 / "suite"
PAIRS = P2 / "text_pairs.json"
MANIFEST = P2 / "manifest.json"
WATCH = RiskTier.WATCH.rank


def _load_bench():
    """复用主集打分器（同一把尺）。"""
    spec = importlib.util.spec_from_file_location("p2_bench", ROOT / "experiments" / "p2_bench.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================ 三道闸门分解
#: 分类依据是 claims.analyze() 的 notes 原文 —— 不重新实现判断逻辑，只读它说了什么。
#: 顺序即优先级：一条文案可能同时命中（例如下界为 0 的类别往往强度也低），取第一条为主因。
GATES: tuple[tuple[str, str], ...] = (
    ("GATE1_ZERO_DAYS", "门① 该类别生理下界为 0 → 永不判不可行"),
    ("GATE2_WEAK_STRENGTH", "门② 宣称强度低于「强」→ 不做时间对照"),
    ("GATE3_NO_PERIOD", "门③ 未给出时间承诺 → 无可对照周期"),
    ("NOT_APPLICABLE", "本层不适用（文案无功效宣称）"),
    ("PLAUSIBLE", "判「可行」（承诺 ≥ 生理下界，不触发提示）"),
    ("IMPLAUSIBLE", "判「不可行」（三道门全过且承诺 < 下界 → 触发）"),
)


def _classify(note_list: list[str]) -> str:
    joined = " || ".join(note_list)
    if "生理不可行" in joined:
        return "IMPLAUSIBLE"
    # ⚠️ 「可即时成立」必须排在「不构成疑点」**之前**：零下界类别同时会出这两条 note，
    #    而真正被执行到的闸门是门①（先跳过该类别，再得出"不构成疑点"的结论）。
    #    排错顺序会让门①在表里永远显示 0 —— 这正是 v2 那版踩到的坑。
    if "可即时成立" in joined:
        return "GATE1_ZERO_DAYS"
    if "不构成疑点" in joined:
        return "PLAUSIBLE"
    if "本层不适用" in joined:
        return "NOT_APPLICABLE"
    if "低于『强』" in joined:
        return "GATE2_WEAK_STRENGTH"
    if "未给出具体时间承诺" in joined:
        return "GATE3_NO_PERIOD"
    return "UNCLASSIFIED"


TEXT_RULE_PREFIX = "R-EFFICACY"


def run_suite(pairs: dict, id2file: dict) -> list[dict]:
    pipe = GuardPipeline()
    rows: list[dict] = []
    for it in pairs["items"]:
        img = SUITE / id2file[it["image_id"]]
        if not img.exists():
            raise FileNotFoundError(f"缺样本 {img}（先跑 samples/make_p2_suite.py）")
        r = pipe.run(ContentItem(image_path=str(img), text=it["text"]),
                     outdir=P2 / "runs_text", tag=it["id"], sheet=False)
        ca = claims.analyze(it["text"])
        fired = [h.rule_id for h in r.verdict.fired]
        rows.append({
            "id": it["id"], "variant": it["variant"],
            "label": "benign" if it["variant"] == "benign_text" else "malicious",
            "category": "文案通路", "desc": it["text"][:28], "expect": it["expect"],
            "file": id2file[it["image_id"]], "image_id": it["image_id"],
            "tier": r.verdict.tier.value, "tier_rank": r.verdict.tier.rank,
            "confidence": r.verdict.confidence, "fired": fired,
            "benign_codes": [b.code for b in r.benign],
            "provenance_state": r.provenance.state.value if r.provenance else "n/a",
            "n_evidence": len(r.evidence), "iou": 0.0,
            # ---- 文案层自己的读数（用于闸门分解，不参与等级判定）----
            "gate": _classify(ca.notes),
            "strength": ca.strength, "promised_days": (min(p.days for p in ca.periods)
                                                      if ca.periods else None),
            "categories": [c.code for c in ca.claims],
            "notes": ca.notes,
            "text_channel_fired": [f for f in fired if f.startswith(TEXT_RULE_PREFIX)],
            "image_channel_fired": [f for f in fired if not f.startswith(TEXT_RULE_PREFIX)],
            "fingerprint": r.fingerprint[:12],
        })
    return rows


def render(pairs: dict, rows: list[dict], m: dict, bad: list[str]) -> str:
    a: list[str] = []
    a.append("# P2-T 文案通路报告（同一张图 × 两套文案，只改文案）")
    a.append("")
    a.append(f"生成时间：{datetime.now():%Y-%m-%d %H:%M}　样本：{pairs['design']}")
    a.append("")
    a.append("> 为什么要有这一份：主集 P2 的 60 条**全部**是无文案工况（逐条免责归因 60/60 带 "
             "`NO_TEXT_PROVIDED`），所以三道保守闸门、生理时间尺度这些**最有独创性的逻辑**在主报告里"
             "触发 0 次。赛题第一条点名「种草文章」，这一份就是那个轴的数字。")
    a.append("")
    if pairs.get("why_n"):
        a.append(f"> **样本量不是凑的**：{pairs['why_n']}")
        a.append("")

    # ---------------------------------------------------------- 一、指标
    a.append("## 一、指标（与主集**同一把尺**：直接调用主集相同的打分函数）")
    a.append("")
    a.append("| 指标 | 口径 | 实测 | 目标 | 结论 |")
    a.append("|---|---|---|---|---|")
    up = m["fpr_upper95"]
    a.append(f"| **① 文案通路误报率** | 合规话术被判「关注级及以上」 | **{m['fpr']:.1%}**"
             f"（{int(round(m['fpr'] * m['n_benign']))}/{m['n_benign']}） | < 10% | "
             f"{'✅' if m['fpr'] < 0.10 else '❌'} |")
    a.append(f"| **①' 误报率 95% 置信上界** | Clopper-Pearson 单侧 | **≤ {up:.2%}** | < 10% | "
             f"{'✅' if m['fpr_stat_met'] else '❌'} |")
    a.append(f"| **② 严重误报率** | 合规话术被判「高风险」 | **{m['severe_fpr']:.1%}** | 0 | "
             f"{'✅' if m['severe_target_met'] else '❌'} |")
    a.append(f"| ③ 文案通路漏报率 | 夸大功效未被提示 | {m['fnr']:.1%}"
             f"（{m['n_malicious'] - m['n_detected']}/{m['n_malicious']}；"
             f"95%CI {m['fnr_ci'][0]:.1%}~{m['fnr_ci'][1]:.1%}） | — | — |")
    a.append(f"| ③' 文案通路检出率 | 夸大功效被判「关注级及以上」 | {m['detect_rate']:.1%}"
             f"（{m['n_detected']}/{m['n_malicious']}；"
             f"95%CI {m['detect_ci'][0]:.1%}~{m['detect_ci'][1]:.1%}） | 报下界 | — |")
    a.append(f"| ⑤ 定位精度 | — | **不适用**（文案通路无坐标） | — | — |")
    a.append(f"| ⑥ 承诺兑现率 | 配对集逐条 expect | **{len(rows) - len(bad)}/{len(rows)}** | 100% | "
             f"{'✅' if not bad else '❌'} |")
    a.append("")
    if bad:
        a.append("**违反承诺的样本（必须逐条解释，不许改承诺）**：")
        a.append("")
        for b in bad:
            a.append(f"- {b}")
        a.append("")

    # ---------------------------------------------------------- 二、闸门分解
    a.append("## 二、三道保守闸门表现（④ 的分解：每一条文案落在哪一档）")
    a.append("")
    a.append("| 归属 | 良性文案 | 恶意文案 | 说明 |")
    a.append("|---|---|---|---|")
    for code, name in GATES + (("UNCLASSIFIED", "未分类（需复核：说明分类逻辑有洞）"),):
        nb = sum(1 for r in rows if r["label"] == "benign" and r["gate"] == code)
        nm = sum(1 for r in rows if r["label"] == "malicious" and r["gate"] == code)
        if nb or nm:
            a.append(f"| {name} | {nb} | {nm} | — |")
    a.append("")
    a.append("> 读法：良性侧落在**门①/门②/门③/不适用**上都是**设计内**（我们主动弃权，"
             "而不是硬判）；恶意侧落在这些门上就是**漏报**，且原因可指名。")
    a.append("")

    # ---------------------------------------------------------- 三、通路隔离
    a.append("## 三、通路隔离证据（配对设计到底证明了什么）")
    a.append("")
    n_txt = sum(len(r["text_channel_fired"]) for r in rows if r["label"] == "malicious")
    n_img = sum(len(r["image_channel_fired"]) for r in rows if r["label"] == "malicious")
    nb_txt = sum(len(r["text_channel_fired"]) for r in rows if r["label"] == "benign")
    nb_img = sum(len(r["image_channel_fired"]) for r in rows if r["label"] == "benign")
    a.append(f"- 恶意文案 {m['n_malicious']} 条上：**文案通路规则命中 {n_txt} 次**，"
             f"图像通路规则命中 **{n_img} 次**。")
    a.append(f"- 合规文案 {m['n_benign']} 条上：文案通路 {nb_txt} 次，图像通路 {nb_img} 次。")
    if nb_img or n_img:
        img_rules = sorted({f for r in rows for f in r["image_channel_fired"]})
        a.append(f"  - ⚠️ 图像通路那几次命中的是 **{', '.join(img_rules)}**（图自带 AI 标识声明）。"
                 f"按《标识办法》第六条（一），已声明 AI 加提示即可 → **提示级不算误报**，"
                 f"所以①误报率仍是 0。把这两个数字分开报，正是为了不让「加了提示」和「没造假」"
                 f"混为一谈。")
    a.append("- 图**没变**（同一批良性图），只有文案变了 → 等级差异只能来自文案通路。"
             "这就是「像素取证看不见的造假」这句话的实测支撑，而不是一句口号。")
    a.append("")
    pairs_flip = 0
    imgs: dict[str, list[dict]] = {}
    for r in rows:
        imgs.setdefault(r["image_id"], []).append(r)
    for img, rs in sorted(imgs.items()):
        tb = max((r["tier_rank"] for r in rs if r["label"] == "benign"), default=-1)
        tm = max((r["tier_rank"] for r in rs if r["label"] == "malicious"), default=-1)
        if tb < WATCH <= tm:
            pairs_flip += 1
    a.append(f"- **配对翻转**：{pairs_flip}/{len(imgs)} 张图在「换文案」后由「未达关注级」"
             f"翻到「关注级及以上」。")
    a.append("")

    # ---------------------------------------------------------- 四、逐条
    a.append("## 四、逐条结果")
    a.append("")
    a.append("| id | 变体 | 文案（节选） | 强度 | 承诺天 | 归属闸门 | 等级 | 命中规则 |")
    a.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        fired = ",".join(r["fired"]) or "—"
        pd = "—" if r["promised_days"] is None else str(r["promised_days"])
        a.append(f"| {r['id']} | {'合规' if r['label'] == 'benign' else '夸大'} | {r['desc']} | "
                 f"{r['strength']} | {pd} | {r['gate']} | {r['tier']} | {fired} |")
    a.append("")

    # ---------------------------------------------------------- 五、诚实登记
    a.append("## 五、诚实登记（不达标与盲区，逐条写明）")
    a.append("")
    probes = [r for r in rows if r["expect"].get("blind_spot")]
    a.append(f"- **已知盲区探针 {len(probes)} 条**（{', '.join(r['id'] for r in probes)}）："
             f"模糊时间表述（半个月 / 一星期半 / 十来天）当前解析不到 → 必然弃权。"
             f"这是**真缺口**，补法是扩 `_PERIOD_RE` 的中文数字表，**不需要动任何阈值**。")
    a.append(f"- 检出率区间宽 **{m['detect_ci'][1] - m['detect_ci'][0]:.0%} 个百分点**："
             f"n={m['n_malicious']} 条的真实信息量，同样不许只报点估计。")
    a.append("- **未覆盖**：本报告只覆盖**纯文案通路**（口径更新 2026-09-20：F5 评论区层已于 2026-09-19 接入、"
             "F3 语义解释层已于 2026-09-20 接入（默认关闭、需 --explain）；本报告的数字仍是当轮读数，未重算）。")
    a.append("- **数据来源**：本集与主集同源，都是**我们自造**的（见 `docs/DEBT.md` D-04："
             "这是 8 追问自测里唯一的 🔴，唯一解是外部真实数据，补自造样本改善不了它）。")
    a.append("- 依据出处：生理下界全部为 `draft:`（未核准到官方/文献原文），报告自动标「待复核」。")
    a.append("")

    # ---------------------------------------------------------- 六、协议修订
    if pairs.get("protocol_revisions"):
        a.append("## 六、协议修订记录（改的是**协议**，不是检测逻辑）")
        a.append("")
        for rev in pairs["protocol_revisions"]:
            # 修订条目不一定带 "violation"（例如"发现零覆盖 → 只加样本"这种修订，
            # 它不是承诺违规，而是**承诺覆盖不足**）—— 渲染器不许因此炸掉。
            line = f"- **{rev['v']}**：{rev['result']}"
            if rev.get("violation"):
                line += f" — {rev['violation']}"
            a.append(line)
            a.append(f"  - 根因：{rev['root_cause']}")
            a.append(f"  - 修订：{rev['fix']}")
        a.append("")
        a.append("> 这一节存在的意义：**协议写错了也要留档**。如果「跑出来不合期望就改期望」而不写明，"
                 "那整套承诺制就变成了自欺 —— 我们宁可把 v1 的 59/60 摆在报告里。")
        a.append("")

    # ---------------------------------------------------------- 七、复现
    a.append("## 七、复现方式")
    a.append("")
    a.append("```bash")
    a.append("cd /d/LorealGuard")
    a.append("env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p2_text_pairs.py")
    a.append("env -u PYTHONPATH .venv/Scripts/python.exe experiments/p2_text_bench.py")
    a.append("```")
    a.append("")
    return "\n".join(a)


def main() -> int:
    if not PAIRS.exists():
        print(f"缺配对集 {PAIRS}（先跑 samples/make_p2_text_pairs.py）")
        return 2
    pairs = json.loads(PAIRS.read_text(encoding="utf-8"))
    mf = json.loads(MANIFEST.read_text(encoding="utf-8"))
    id2file = {it["id"]: it["file"] for it in mf["items"]}

    bench = _load_bench()
    rows = run_suite(pairs, id2file)
    m = bench.compute_metrics(rows)
    bad = bench.check_promises(rows)

    (P2 / "text_report.md").write_text(render(pairs, rows, m, bad), encoding="utf-8")
    (P2 / "text_metrics.json").write_text(json.dumps(
        {"metrics": m, "violations": bad, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print(render(pairs, rows, m, bad).split("## 四、")[0])
    print(f"① 文案通路误报率 = {m['fpr']:.1%}  (95%上界 {m['fpr_upper95']:.1%})")
    print(f"③' 文案通路检出率 = {m['detect_rate']:.1%}  95%CI "
          f"[{m['detect_ci'][0]:.1%}, {m['detect_ci'][1]:.1%}]  (n={m['n_malicious']})")
    print(f"⑥ 承诺兑现 = {len(rows) - len(bad)}/{len(rows)}")
    print(f"\n报告：{P2 / 'text_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())