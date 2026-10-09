"""红队守卫的**变异测试**：证明这 10 条守卫不是空转。

铁律：一条不会红的检查等于没有检查。所以这里**故意注入 5 种故障**，
断言对应的守卫必须变红（`AssertionError`）。任何一条注入没被抓住 → 本文件红。

沿用仓库既有惯例（`tests/test_launcher_guard_falsifiability.py`）：不碰真语料，
全部在 monkeypatch / 临时副本上做。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))
sys.path.insert(0, str(ROOT / "tests"))

import red_team_bench as bench                      # noqa: E402
import test_red_team as trt                         # noqa: E402


def _turns_red(fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except AssertionError:
        return True
    except Exception as e:                          # noqa: BLE001
        raise AssertionError(f"守卫不是断言失败，而是崩了：{type(e).__name__}: {e}") from e
    return False


def _corpus(tmp_path, mutate) -> Path:
    data = json.loads(trt.PAIRS.read_text(encoding="utf-8"))
    mutate(data)
    f = tmp_path / "mutated.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return f


def test_mutation_1_shrunken_sample_is_caught(tmp_path, monkeypatch):
    """把样本量砍到 5 条/矛 → 地板守卫必须红（否则"为了好看缩水样本"没人管）。"""
    def mutate(d):
        for a in ("evade", "frame", "blind"):
            d["counts"][a] = 5
            d["arms"][a]["threads"] = 5
    monkeypatch.setattr(trt, "PAIRS", _corpus(tmp_path, mutate))
    assert _turns_red(trt.test_sample_floor_per_arm), "砍样本量居然没被抓住"


def test_mutation_2_changed_target_is_caught(tmp_path, monkeypatch):
    """把语料里的靶子指纹改成假的 → 指纹守卫必须红（防"改完规则拿旧样本跑"）。"""
    monkeypatch.setattr(trt, "PAIRS",
                        _corpus(tmp_path, lambda d: d.update(rules_yaml_sha256_16="0" * 16)))
    assert _turns_red(trt.test_target_fingerprint_pinned_to_current_rules), \
        "换了靶子居然没被抓住"


def test_mutation_3_broken_ledger_is_caught(tmp_path, monkeypatch):
    """把台账改到不平（接单数 +1）→ 台账守卫必须红。"""
    monkeypatch.setattr(trt, "PAIRS",
                        _corpus(tmp_path, lambda d: d["arms"]["evade"].update(calls_ok=999)))
    assert _turns_red(trt.test_opponent_ledger_is_self_consistent), "台账不平居然没被抓住"


def test_mutation_4_undeclared_breach_is_caught(tmp_path, monkeypatch):
    """在登记表里塞一条不存在的突破 → 棘轮必须红（实测与登记必须逐条一致）。"""
    reg = json.loads(trt.KNOWN.read_text(encoding="utf-8"))
    reg["breaches"].append({"id": "F99", "mechanism": "伪造的突破"})
    f = tmp_path / "reg.json"
    f.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(trt, "KNOWN", f)
    assert _turns_red(trt.test_red_line_breaches_match_registry), "登记表被篡改居然没被抓住"


def test_mutation_5_silent_margin_parse_failure_is_caught(monkeypatch):
    """让余量解析退化成 +inf（正是真实踩过的坑）→ 余量守卫必须红。"""
    monkeypatch.setattr(bench, "evade_margin", lambda row, rules: (float("inf"), "—"))
    assert _turns_red(trt.test_evade_margins_computable_and_consistent), \
        "余量全 +inf（空值假通过）居然没被抓住"


def test_all_five_mutations_were_exercised():
    """元检查：变异测试本身必须真的注入了 5 种故障（防止有人把上面几条注释掉）。"""
    names = [n for n in dir(sys.modules[__name__]) if n.startswith("test_mutation_")]
    assert len(names) == 5, f"变异注入只剩 {len(names)} 种：{names}"