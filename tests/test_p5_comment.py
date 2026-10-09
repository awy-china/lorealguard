"""F5 评论区层的回归守卫 —— 每条断言都**能变红**（否则等于没有测试）。

覆盖六件事：
    ① 阳性对照：逐字复制的注水线程必须判到「高风险」（守卫非空转的证明）；
    ② 阴性对照：良性线程不得出现风险等级；
    ③ 缺数据不判：线程 <5 条 → `不适用`，且**必须带归因码**（不适用 ≠ 无异常）；
    ④ 阈值不在代码：信号层源码里不许出现数值阈值比较（阈值只许在 rules.yaml）；
    ⑤ 措辞纪律：评论区规则文本不得出现「水军/删除/处罚」这类终审或处置措辞；
    ⑥ 口径锁定：报告里的关键数字必须与本次实跑一致（谁放松阈值，这条就红）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard.comments import analyze, evaluate          # noqa: E402
from voiceguard.rules.engine import load_rules             # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_guard import require_repo                       # noqa: E402

PAIRS = ROOT / "output" / "p5" / "comment_pairs.json"
REPORT = ROOT / "output" / "p5" / "comment_report.md"
SIGNALS_SRC = ROOT / "voiceguard" / "comments" / "signals.py"
RULES_YAML = ROOT / "voiceguard" / "rules" / "rules.yaml"
RISK = {"关注级", "高风险"}

#: 禁语用**模式**而不是裸词 —— 裸词会把「不作处罚建议」这种合规措辞一起判违规
#: （第一版就栽在这：`处罚` 命中了自己的免责声明）。判的是**主张**，不是字面。
BANNED_PATTERNS = (
    r"是水军", r"确定为水军", r"判定为水军", r"已确认造假",
    r"(建议|应当|应予|必须)处罚", r"封号", r"删除评论", r"建议删除",
)
#: 合规措辞必须真的存在 —— 否则禁语检查可能因为「什么都没写」而假通过
REQUIRED_PHRASES = ("不作真假终审", "不是对你的内容定假")


def _payload() -> dict:
    # 缺配对集 ⇒ **显式弃权**并点名（公开仓/源码包按设计不带 output/，见 docs/DEBT.md D-49）
    require_repo("output/p5/comment_pairs.json",
                 note="仓库内先跑 samples/make_p5_comment_pairs.py 生成")
    return json.loads(PAIRS.read_text(encoding="utf-8"))


def _rules():
    return load_rules()


# ① 阳性对照：守卫必须能红
def test_seedbatch_thread_reaches_high_risk():
    comments = ["姐妹们这个真的好用到爆，我用了一周皮肤绝了" + t for t in
                ("，私信我领券", "～私信我领券", "，私信我领券！", "，私信我领券", "，私信我领券～")]
    comments.append("私信我发你内部渠道价，比专柜便宜一半")
    sig = analyze(comments)
    v = evaluate(comments, _rules(), sig)
    assert sig.dup_ratio >= 0.5 and sig.solicit_ratio >= 0.5 and sig.specifics_ratio <= 0.2, \
        f"三闸门信号未达到: dup={sig.dup_ratio} solicit={sig.solicit_ratio} spec={sig.specifics_ratio}"
    assert v.tier == "高风险", f"逐字复制 + 引流 + 零实指应判高风险，实得 {v.tier}"
    assert "C-REVIEW-SEEDBATCH" in v.rule_ids


# ② 阴性对照
def test_benign_thread_never_flagged():
    comments = [
        "30ml 用了两周，混油皮夏天有点闷，但早上上妆不搓泥。",
        "我是干皮，换季用还行，就是香味有点重，个人感受。",
        "回购第二瓶了，配合烟酰胺精华用，效果一般般。",
        "质地挺薄的，空调房里需要叠涂，仅供参考。",
        "用了 5 天没什么感觉，可能我皮太稳了。",
    ]
    v = evaluate(comments, _rules(), analyze(comments))
    assert v.tier not in RISK, f"良性线程被误判为 {v.tier}"


# ③ 缺数据不判 + 弃权必须归因
def test_too_few_comments_abstains_with_reason():
    v = evaluate(["好用", "已回购"], _rules(), analyze(["好用", "已回购"]))
    assert v.tier == "不适用"
    assert v.contract_tier == "未触发"
    codes = [a["code"] for a in v.attributions]
    assert "ATT_TOO_FEW_COMMENTS" in codes, f"弃权必须归因，实得 {codes}"
    assert v.rule_ids == ["C-NOT-APPLICABLE-FEW-COMMENTS"], f"弃权标记不对：{v.rule_ids}"
    assert not [r for r in v.rule_ids if r.startswith("C-REVIEW")], "弃权时不得有任何形态规则命中"


def test_every_abstention_carries_a_reason():
    rules = _rules()
    for it in _payload()["items"]:
        sig = analyze(it["comments"])
        v = evaluate(it["comments"], rules, sig)
        assert v.tier != "不适用" or v.attributions, f"{it['id']} 弃权却没有归因码"
        assert v.attributions, f"{it['id']} 任何结论都必须至少带一条归因"


# ④ 阈值只许在 YAML
def test_no_numeric_thresholds_in_signal_layer():
    src = SIGNALS_SRC.read_text(encoding="utf-8")
    hits = re.findall(r"^.*[<>]=?\s*0\.\d+.*$", src, flags=re.M)
    assert not hits, f"信号层出现了数值阈值比较（阈值必须留在 rules.yaml）：{hits[:3]}"
    assert "comment_rules" in RULES_YAML.read_text(encoding="utf-8")


# ⑤ 措辞纪律（免责权归创作者）
def test_comment_rules_wording_stays_non_punitive():
    """判出口的措辞纪律：只许形态描述 + 复核建议，不许终审或处置主张。"""
    rules = _rules()
    outgoing = []
    for grp in ("comment_rules", "comment_benign_rules"):
        for r in rules[grp]:
            outgoing.append(" ".join(str(r.get(k, "")) for k in
                                     ("desc", "advice_platform", "advice_creator", "detail")))
    blob = " || ".join(outgoing)
    for pat in BANNED_PATTERNS:
        assert not re.search(pat, blob), f"出口措辞里出现了处置/终审主张：/{pat}/"
    for phrase in REQUIRED_PHRASES:
        assert phrase in blob, f"缺少合规措辞「{phrase}」——禁语检查可能空转"
    assert "comment_rules" in RULES_YAML.read_text(encoding="utf-8")


# ⑥ 口径锁定：报告数字必须与实跑一致（谁放松阈值这条就红）
def test_report_numbers_match_live_run():
    require_repo("output/p5/comment_report.md",
                 note="仓库内先跑 experiments/p5_comment_bench.py 生成")
    payload = _payload()
    rules = _rules()
    items = payload["items"]
    benign = [x for x in items if x["kind"] == "benign"]
    probes = [x for x in items if x["kind"] == "probe"]
    bots = [x for x in items if x["kind"] == "bot"]
    fb = sum(1 for x in benign if evaluate(x["comments"], rules, analyze(x["comments"])).tier in RISK)
    fp = sum(1 for x in probes if evaluate(x["comments"], rules, analyze(x["comments"])).tier in RISK)
    det = sum(1 for x in bots if evaluate(x["comments"], rules, analyze(x["comments"])).tier in RISK)
    assert (fb, fp, det) == (0, 0, len(bots)), f"实跑口径漂移：良性误报 {fb}、探针 {fp}、检出 {det}/{len(bots)}"

    text = REPORT.read_text(encoding="utf-8")
    assert f"`{fb}/{len(benign)}`" in text and "✅ 兑现" in text
    for stmt in payload["promise"]["statements"]:
        assert stmt in text, f"报告没有原样引用承诺：{stmt}"


def test_promises_are_written_before_measurement():
    p = _payload()["promise"]
    assert p["benign_max_risk_hits"] == 0
    assert p["bot_min_detect"] >= 24
    assert len(p["statements"]) == 3