"""P6 红队守卫 —— 八条**能变红**的检查，防的是"把口径改软"而不是记录数字。

设计原则（沿用仓库既有铁律）：一条不会红的检查等于没有检查。每条都能被一次最小改动弄红，
且红的时候含义明确：
    1. 删掉协议文档 → 红（口径必须留档）
    2. 台账不自洽 / 超出目标 → 红（接单+拒答+其它失败必须等于总调用）
    3. 欠样却不印 → 红（样本没到 33 就必须把"欠样"和原因写进报告，不许悄悄少给）
    4. 样本量低于地板 20 → 红（0/20 的 CP 上界已宽到 16.1%，再少就没有统计意义）
    5. 改 rules.yaml → 红（靶子变了就必须重新生成对手样本，不许拿旧样本跑新阈值）
    6. 篡改样本指纹 → 跑分器必须**拒绝出分**（行为测试，实跑 exit 3）
    7. 良性侧被推到「高风险」→ 红（红线）
    8. 判了却无规则支撑/无归因码/无可复算数字 → 红（归因完备性）
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))

import red_team_bench as bench          # noqa: E402
from voiceguard.rules.engine import load_rules   # noqa: E402

PAIRS = ROOT / "samples" / "red_team" / "red_team.json"
KNOWN = ROOT / "samples" / "red_team" / "known_breaches.json"
PROTOCOL = ROOT / "docs" / "REDTEAM-PROTOCOL.md"
RULES_YAML = ROOT / "voiceguard" / "rules" / "rules.yaml"
ARMS = ("evade", "frame", "blind")
FLOOR_PER_ARM = 20          # 地板：再少则 CP 上界宽到没有统计意义

requires_corpus = pytest.mark.skipif(
    not (PAIRS.exists() and KNOWN.exists()),
    reason="红队语料/登记表未生成（先跑 samples/make_red_team.py）")


def payload() -> dict:
    return json.loads(PAIRS.read_text(encoding="utf-8"))


def render_report(tmp_path, monkeypatch) -> str:
    """把跑分器渲染到临时目录，返回报告全文（不改动仓库里的真报告）。"""
    monkeypatch.setattr(bench, "P6", tmp_path)
    monkeypatch.setattr(bench, "REPORT", tmp_path / "r.md")
    monkeypatch.setattr(bench, "LOG", tmp_path / "r.txt")
    assert bench.main() == 0
    return (tmp_path / "r.md").read_text(encoding="utf-8")


# ---- 1. 协议在场，且判据写死
def test_protocol_doc_pins_red_line():
    assert PROTOCOL.exists(), "红队协议文档缺失"
    text = PROTOCOL.read_text(encoding="utf-8")
    for key in ("写于测量之前", "必须是 0", "靶子不变性", "禁止事后放宽阈值补漏"):
        assert key in text, f"协议缺少口径：{key}"


# ---- 2. 台账自洽：对手接单 / 拒答 / 其它失败 必须与总调用对得上
@requires_corpus
def test_opponent_ledger_is_self_consistent():
    data = payload()
    for arm in ARMS:
        m = data["arms"][arm]
        assert m["calls_ok"] + m["calls_refused"] + m["calls_failed_other"] == m["calls_total"], \
            f"{arm} 台账不平：{m}"
        assert m["threads"] == data["counts"][arm], f"{arm} 计数与 items 不一致"
        assert m["threads"] <= data["target_per_arm"], f"{arm} 超出目标线程数（不该发生）"
        for it in [x for x in data["items"] if x["arm"] == arm]:
            assert len(it["comments"]) >= 5, f"{it['id']} 评论数 <5（低于本层判定下限）"
            assert it["expect"], f"{it['id']} 缺 expect（承诺必须先写）"


# ---- 3. 欠样必须印出来（不是藏起来，也不是靠改 brief 补圆）
@requires_corpus
def test_shortfall_is_declared_not_hidden(tmp_path, monkeypatch):
    data = payload()
    text = render_report(tmp_path, monkeypatch)
    for arm in ARMS:
        m = data["arms"][arm]
        if m["threads"] >= data["target_per_arm"]:
            continue
        assert m["calls_refused"] + m["calls_failed_other"] > 0, \
            f"{arm} 欠样却没有任何失败记录 → 台账不可信"
        assert "欠样" in text and arm in text, \
            f"{arm} 欠样（{m['threads']}/{data['target_per_arm']}）却没印进报告"


# ---- 4. 样本量地板
@requires_corpus
def test_sample_floor_per_arm():
    data = payload()
    for arm in ARMS:
        n = data["counts"][arm]
        assert n >= FLOOR_PER_ARM, f"{arm} 只有 {n} 条（地板 {FLOOR_PER_ARM}）"


# ---- 5. 靶子指纹必须是"当前这只靶子"
@requires_corpus
def test_target_fingerprint_pinned_to_current_rules():
    cur = hashlib.sha256(RULES_YAML.read_bytes()).hexdigest()[:16]
    got = payload()["rules_yaml_sha256_16"]
    assert got == cur, (
        f"rules.yaml 已改动（样本生成时 {got}，当前 {cur}）。"
        "按红队协议第 2 节，必须**重新生成**对手样本，不许拿旧样本跑新阈值。"
    )


# ---- 6. 行为测试：指纹不符 → 跑分器拒绝出分（证明第 5 条不是空话）
@requires_corpus
def test_fingerprint_mismatch_refuses_to_score(tmp_path, monkeypatch):
    bad = json.loads(PAIRS.read_text(encoding="utf-8"))
    bad["rules_yaml_sha256_16"] = "deadbeefdeadbeef"
    f = tmp_path / "bad.json"
    f.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bench, "PAIRS", f)
    assert bench.main() == 3, "指纹不符时跑分器必须 exit 3（拒绝出分），否则这把锁是假的"


# ---- 7. 红线：登记式棘轮（不是"必须 0"的假绿，也不是删掉检查）
@requires_corpus
def test_red_line_breaches_match_registry():
    """实测的红线突破必须与 `known_breaches.json` **逐条一致**。

    为什么不是直接断言 0 条：本轮实测就是被击穿了 3 条（真人拼单接龙形态），
    那是真实结果，按协议**禁止改阈值抹掉它**。所以这里做棘轮：
      - 多出一条（新突破没登记）→ 红
      - 少一条（登记过期 / 规则被改）→ 红，提示要么更新登记、要么重生成语料
      - 登记里缺机制说明 → 红（登记必须能被人读懂，不能只报 id）
    """
    registry = json.loads(KNOWN.read_text(encoding="utf-8"))
    declared = sorted(b["id"] for b in registry["breaches"])
    assert declared, "登记表为空 —— 要么真修好了（那就该写清'已修复'并留证据），要么在藏"
    for b in registry["breaches"]:
        assert b.get("mechanism"), f"{b['id']} 缺机制说明（登记必须可读）"
    assert registry["target_fingerprint"] == payload()["rules_yaml_sha256_16"], \
        "登记表绑的是另一只靶子 → 必须重新登记（对应新语料）"

    rows = bench.run_rows(payload()["items"], load_rules())
    actual = sorted(r["id"] for r in rows
                    if r["arm"] in ("frame", "blind") and r["tier"] == "高风险")
    assert actual == declared, (
        f"红线突破与登记表不一致：实测 {actual} / 登记 {declared}。"
        "多出的一条是新突破，必须登记；少一条说明登记过期或规则被改（规则一改必须重生成语料）。"
    )


# ---- 8. 归因完备性：判了就必须说清凭什么
@requires_corpus
def test_attribution_completeness():
    rows = bench.run_rows(payload()["items"], load_rules())
    assert bench.attrib_violations(rows) == []


# ---- 10. 绕过余量必须真的算得出来（防"解析落空 → 全 +inf 的空值假通过"）
@requires_corpus
def test_evade_margins_computable_and_consistent():
    rules = load_rules()
    rows = bench.run_rows(payload()["items"], rules)
    ev = [r for r in rows if r["arm"] == "evade" and r["tier"] not in bench.RISK]
    ms = [(r, bench.evade_margin(r, rules)[0]) for r in ev]
    assert ms, "没有可算的绕过样本"
    inf = [r["id"] for r, m in ms if m == float("inf")]
    assert not inf, f"余量出现 +inf（{inf[:5]}…）→ 键名/算子解析又落空了"
    bad = [r["id"] for r, m in ms if m <= 0]
    assert not bad, f"口径异常：{bad} 的规则条件已全满足、引擎却未触发"


# ---- 9. 报告必须印满协议第 5 节的四条（缺一条即红）
@requires_corpus
def test_report_prints_all_mandatory_sections(tmp_path, monkeypatch):
    text = render_report(tmp_path, monkeypatch)
    rows = bench.run_rows(payload()["items"], load_rules())
    ev = [r for r in rows if r["arm"] == "evade"]
    det = sum(1 for r in ev if r["tier"] in bench.RISK)
    for key, why in (
        ("绕过清单", "绕过必须逐条登记"),
        (f"检出 {det}/{len(ev)}", "印的检出数必须与重算一致（口径不许手改）"),
        ("靶子指纹", "靶子指纹必须在场"),
        ("归因完备性", "归因完备性必须在场"),
        ("诚实登记", "诚实缺口必须印在报告里"),
    ):
        assert key in text, f"报告缺少必备内容：{key}（{why}）"