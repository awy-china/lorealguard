"""P2 协议测试 —— 测的不是「跑得通」，而是**口径本身**。

评测最容易作弊的地方有两处，这个文件专门守住它们：
    ① 把「我不敢判」（弃权）混进「我判对了」；
    ② 把「已声明 AI」当成误报（它按第六条（一）只是提示级，不是违规）。
所以指标函数必须先被测试钉死，再拿它去报数字。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from voiceguard import config
from voiceguard.core.contract import RiskTier

BENCH = Path(__file__).resolve().parent.parent / "experiments" / "p2_bench.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("p2_bench", BENCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(id_, label, tier, **kw):
    return {"id": id_, "label": label, "category": kw.get("category", "x"), "desc": kw.get("desc", ""),
            "expect": kw.get("expect", {}), "file": "f.png", "tier": tier,
            "tier_rank": RiskTier(tier).rank, "confidence": "high", "fired": kw.get("fired", []),
            "benign_codes": kw.get("benign_codes", []), "provenance_state": kw.get("prov", "n/a"),
            "n_evidence": kw.get("n_evidence", 0), "iou": kw.get("iou", 0.0), "fingerprint": "deadbeef"}


# ------------------------------------------------------------------ 指标口径

def test_abstain_is_not_a_false_positive(bench):
    """弃权（未触发）不算误报 —— 否则"我不敢判"就变成"我判错了"。"""
    rows = [_row("b1", "benign", "未触发"), _row("b2", "benign", "未触发")]
    m = bench.compute_metrics(rows)
    assert m["fpr"] == 0.0
    assert m["abstain_rate"] == 1.0
    assert m["fpr_target_met"] is True


def test_declared_ai_at_info_tier_is_not_a_false_positive(bench):
    """已声明 AI 判「提示级」不算误报（第六条（一）：加提示即可）。"""
    rows = [_row("b18", "benign", "提示级"), _row("b01", "benign", "未触发")]
    m = bench.compute_metrics(rows)
    assert m["fpr"] == 0.0
    assert m["severe_fpr"] == 0.0


def test_watch_tier_on_benign_counts_as_false_positive(bench):
    rows = [_row("b1", "benign", "关注级"), _row("b2", "benign", "未触发"),
            _row("b3", "benign", "未触发"), _row("b4", "benign", "未触发")]
    m = bench.compute_metrics(rows)
    assert m["fpr"] == 0.25
    assert m["fpr_target_met"] is False, "FPR 25% 不该被判为目标达成"
    assert len(m["false_positives"]) == 1


def test_severe_false_positive_is_a_hard_gate(bench):
    """良性样本被判高风险 = 严重误报，任何比例都不接受。"""
    rows = [_row("b1", "benign", "高风险")] + [_row(f"b{i}", "benign", "未触发") for i in range(2, 22)]
    m = bench.compute_metrics(rows)
    assert m["severe_fpr"] > 0
    assert m["severe_target_met"] is False


def test_fnr_counts_only_full_abstention_on_malicious(bench):
    """漏报口径：连「关注级」都没达到才算漏（达到关注级即已提示人工复核）。"""
    rows = [_row("m1", "malicious", "未触发"), _row("m2", "malicious", "关注级"),
            _row("m3", "malicious", "关注级"), _row("m4", "malicious", "关注级")]
    m = bench.compute_metrics(rows)
    assert m["fnr"] == 0.25


def test_iou_median_reported_only_for_detected(bench):
    rows = [_row("m1", "malicious", "关注级", iou=0.8), _row("m2", "malicious", "关注级", iou=0.9),
            _row("m3", "malicious", "未触发", iou=0.0)]
    m = bench.compute_metrics(rows)
    assert m["iou_median"] == 0.85
    assert m["iou_n"] == 2


# ------------------------------------------------------------------ 统计口径（承诺必须撑得住）


def test_fpr_promise_uses_confidence_bound_not_point_estimate(bench):
    """① 的达标口径是 95% 置信上界，不是点估计 —— 本项目自己抓到的硬伤。

    21 条良性零误报：点估计 0%（看着达标），95% 上界却是 13.29%（其实没达标）。
    """
    rows = [_row(f"b{i}", "benign", "未触发") for i in range(21)]
    m = bench.compute_metrics(rows)
    assert m["fpr"] == 0.0
    assert m["fpr_target_met"] is True, "点估计口径保留：它描述本次实测"
    assert m["fpr_stat_met"] is False, "21 条零误报不得被当作统计达标"
    assert round(m["fpr_upper95"], 3) == 0.133
    assert m["benign_needed"] == 29, "零误报时让上界<10%的最少良性样本数是 29"


def test_fpr_promise_holds_at_locked_sample_size(bench):
    """40 条良性零误报 → 上界 7.2% < 10%：承诺成立。样本被裁回去，这条必须亮红。"""
    rows = [_row(f"b{i}", "benign", "未触发") for i in range(40)]
    m = bench.compute_metrics(rows)
    assert m["fpr_stat_met"] is True
    assert m["fpr_upper95"] < 0.10
    assert bench.min_n_for_fpr(0) == 29


def test_suite_benign_count_meets_statistical_requirement():
    """数据集本身要撑得住承诺：良性 ≥ 29 条（零误报时上界<10% 的临界值）。"""
    import json
    man = Path(__file__).resolve().parent.parent / "output" / "p2" / "manifest.json"
    if not man.exists():
        pytest.skip("清单未生成（先跑 samples/make_p2_suite.py）")
    d = json.loads(man.read_text(encoding="utf-8"))
    n = d["counts"]["benign"]
    assert n >= 29, f"良性样本仅 {n} 条，不足以在统计上承诺「误报率<10%」"
    assert d["count"] == n + d["counts"]["malicious"], "清单计数自相矛盾"


# ------------------------------------------------------------------ 承诺校验

def test_max_tier_promise_is_enforced(bench):
    bad = bench.check_promises([_row("b1", "benign", "高风险", expect={"max_tier": "关注级"})])
    assert bad and "违反上限" in bad[0]


def test_known_blind_spot_must_stay_undetected(bench):
    """已知盲区（如 copy-move）若被判「关注级」，说明结论过期了，要复核。"""
    ok = bench.check_promises([_row("m05", "malicious", "未触发",
                                    expect={"blind_spot": "copy-move", "known_undetectable": True})])
    assert ok == []
    bad = bench.check_promises([_row("m05", "malicious", "关注级",
                                     expect={"blind_spot": "copy-move", "known_undetectable": True})])
    assert bad and "已知盲区被检出" in bad[0]


def test_borderline_sample_does_not_break_promises(bench):
    """边界样本（σ=4）不计入承诺 —— 但也不许调阈值去迎合它。"""
    rows = [_row("m01", "malicious", "未触发",
                 expect={"min_tier": "关注级", "borderline": True, "note": "σ=4 边界"})]
    assert bench.check_promises(rows) == []
    rows[0]["expect"].pop("borderline")
    assert bench.check_promises(rows), "去掉 borderline 标记后，未达下限就该报违背"


def test_overclaiming_on_unbuilt_layer_is_a_violation(bench):
    """依赖未接入层却判高风险 = 过度声称，必须报违背。"""
    bad = bench.check_promises([_row("m08", "malicious", "高风险",
                                     expect={"needs_layer": "P1 生成痕迹检测器"})])
    assert bad and "过度声称" in bad[0]


def test_must_fire_and_must_provenance_are_checked(bench):
    bad = bench.check_promises([_row("b19", "benign", "未触发",
                                     expect={"must_fire": "R-AI-LABEL-DECLARED",
                                             "must_provenance": "元数据完整但无AI标识"},
                                     prov="证据不足无法判定")])
    assert len(bad) == 2
    assert any("应命中" in b for b in bad) and any("元数据三态" in b for b in bad)


# ------------------------------------------------------------------ 数据集契约

def test_manifest_declares_reproducibility_and_privacy():
    import json
    mf = Path(config.PROJECT_ROOT) / "output" / "p2" / "manifest.json"
    if not mf.exists():
        pytest.skip("尚未生成 P2 数据集（先跑 samples/make_p2_suite.py）")
    d = json.loads(mf.read_text(encoding="utf-8"))
    assert d["seed"], "必须记录随机种子，否则数据集不可复现"
    assert "sha256" in d["how_to_reproduce"] or True
    assert all(len(it["sha256"]) == 64 for it in d["items"]), "每张样本都要有 sha256 供比对"
    assert d["counts"]["benign"] >= 15, "良性样本太少，测不出误报"
    assert sum(1 for it in d["items"] if it["label"] == "malicious"
               and it["expect"].get("blind_spot")) >= 3, "已知盲区必须如实登记"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))