"""F3 守卫的**变异自证**：每条守卫都必须会红。

「一条不会红的检查等于没有检查。」
本文件把 test_semantic_explain.py 里的守卫函数拿出来，喂**故意做错**的东西，
断言它们**确实报错**。守卫哪天被改松了，这里就会红。
"""

from __future__ import annotations

import json

import pytest

from voiceguard import config
from voiceguard.core.contract import Evidence, RiskReport, Verdict

from tests.test_semantic_explain import (assert_allowed, assert_explain_default_off,
                                         assert_explanation_honest,
                                         assert_judgment_identical,
                                         assert_no_network_imports,
                                         assert_whitelist_is_open_weight, judgment_json)

ROOT_GUARD = "voiceguard/guard.py"


def _base() -> RiskReport:
    return RiskReport(content_id="c1", fingerprint="ab" * 32,
                      verdict=Verdict(headline="低风险"), evidence=[])


# ---------------------------------------------------------------- M1 解释写回判定
def test_M1_guard_catches_verdict_pollution():
    a, b = _base(), _base()
    assert_judgment_identical(a, b)                       # 正常：不报错
    b.verdict.headline = "高风险：疑似伪造（模型说的）"       # 变异：解释层把结论写进判定
    with pytest.raises(AssertionError, match="判定被解释层污染"):
        assert_judgment_identical(a, b)


def test_M1b_guard_catches_evidence_pollution():
    a, b = _base(), _base()
    b.evidence = [Evidence(id="X1", layer="semantic", kind="llm_says",
                           label="模型认为这是假的")]
    with pytest.raises(AssertionError):
        assert_judgment_identical(a, b)


def test_M1c_guard_catches_silent_field_drop():
    """变异：解释层顺手删掉一条证据（判定同样被改）。"""
    a = _base()
    a.evidence = [Evidence(id="E1", layer="forensics", kind="ela_region", label="压缩异常")]
    b = _base()
    with pytest.raises(AssertionError):
        assert_judgment_identical(a, b)


# ---------------------------------------------------------------- M2 白名单放行
def test_M2_guard_catches_whitelist_loosening():
    assert_allowed(config.MODEL_AGENT)
    assert_whitelist_is_open_weight()                       # 真白名单：只含已核实的档 ✓
    poisoned = dict(config.OPEN_WEIGHT_MODELS)
    poisoned["qwen3.8-max"] = "闭源托管档"                 # 变异：把闭源档塞进白名单
    with pytest.raises(AssertionError, match="未经核实"):
        assert_whitelist_is_open_weight(poisoned)


def test_M2b_guard_catches_empty_whitelist_surprise():
    """变异：白名单被清空/改名 → 守卫必须报错而不是静默放行。"""
    with pytest.raises(AssertionError):
        assert_allowed(config.MODEL_AGENT, {})
    with pytest.raises(AssertionError, match="白名单为空"):
        assert_whitelist_is_open_weight({})


# ---------------------------------------------------------------- M3 冒充成功
def test_M3_guard_catches_fabricated_answer():
    assert_explanation_honest({"status": "ok", "answer": "这块区域看起来很自然。"})
    with pytest.raises(AssertionError, match="编造"):
        assert_explanation_honest({"status": "ok", "answer": ""})
    with pytest.raises(AssertionError, match="短到不可能"):
        assert_explanation_honest({"status": "ok", "answer": "ok"})


# ---------------------------------------------------------------- M4 默认改成开启
def test_M4_guard_catches_default_on():
    src = open(ROOT_GUARD, encoding="utf-8").read()
    assert_explain_default_off(src)                        # 真源码：默认 False ✓
    with pytest.raises(AssertionError, match="默认关闭"):
        assert_explain_default_off(src.replace("explain: bool = False",
                                               "explain: bool = True", 1))


def test_M4b_guard_catches_missing_switch():
    with pytest.raises(AssertionError, match="找不到 explain 开关"):
        assert_explain_default_off("def run(self):\n    pass\n")


# ---------------------------------------------------------------- M5 判定链偷偷联网
def test_M5_guard_catches_network_import():
    assert_no_network_imports("voiceguard/rules/engine.py", "import json\nfrom .base import X\n")
    with pytest.raises(AssertionError, match="零网络"):
        assert_no_network_imports("voiceguard/rules/engine.py", "import requests\n")
    with pytest.raises(AssertionError, match="零网络"):
        assert_no_network_imports("voiceguard/guard.py", "from openai import OpenAI\n")
    with pytest.raises(AssertionError, match="零网络"):
        assert_no_network_imports("voiceguard/report/ledger.py", "import socket\n")


# ---------------------------------------------------------------- M6 守卫函数自身可证伪
def test_M6_all_guards_are_callable_and_positive_path_ok():
    """守卫在正确输入上必须静默通过 —— 否则它只是"永远报错"，不是检查。"""
    a, b = _base(), _base()
    assert_judgment_identical(a, b)
    assert_allowed(config.MODEL_SEMANTIC)
    assert_explanation_honest({"status": "ok", "answer": "坐标 (x=1,y=2,w=3,h=4) 是人脸皮肤区。"})
    assert json.loads(judgment_json(a))["content_id"] == "c1"


# ---------------------------------------------------------------- M7 层状态假装全绿
def _layers_ok() -> dict:
    return {"F1 像素取证层": "已跑（3 个检测器）", "F2 元数据溯源层": "已跑（no_label）",
            "F3 语义解释层": "已接入（默认关闭，需 --explain）",
            "F4 文案/语义一致性层": "已跑（consistent）", "F5 评论区真实性层": "不适用（非评论区）",
            "F6 免责归因层": "已跑（2 条免责规则）", "F7 决策与建议层": "已跑（低风险）",
            "F8 报告层": "已跑（md + json）"}


def test_M7_guard_catches_faked_layer_status():
    from tests.test_guard_e2e import assert_layers_disclose_honestly

    assert_layers_disclose_honestly(_layers_ok())              # 正常：不报错
    faked = dict(_layers_ok())
    faked["F3 语义解释层"] = "已跑（qwen3.8-27b，仅描述）"      # 变异：默认关闭却写成已跑
    with pytest.raises(AssertionError, match="必须写明开启方式"):
        assert_layers_disclose_honestly(faked)
    all_green = {k: "已跑（全部 OK）" for k in _layers_ok()}     # 变异：八层全绿
    with pytest.raises(AssertionError, match="没有任何层声明"):
        assert_layers_disclose_honestly(all_green)
    with pytest.raises(AssertionError, match="不可解释的措辞"):
        assert_layers_disclose_honestly({**_layers_ok(), "F9 神秘层": "没问题"})