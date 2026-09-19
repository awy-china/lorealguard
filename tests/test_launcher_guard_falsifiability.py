# -*- coding: utf-8 -*-
"""测试的测试（mutation testing）：证明 run.bat 的守卫**不是空转**。

一条不会红的检查等于没有检查 —— 本项目已在「空值互比得到 True」上栽过一次。
所以这里把同一套判定喂给**故意做坏的** run.bat 假文本，要求：
1. 每个变异体至少触发一条守卫；
2. 真实 run.bat 下所有守卫全绿。

（变异体示例：混入中文 / 引用不存在的文件 / 步骤编号不连续 / 漏掉官方必交材料。）
"""

import tempfile
from pathlib import Path

import pytest

import test_acceptance_launcher as G

MUTANTS = {
    "混入中文": "[1/1] regression 回归测试\n",
    "引用不存在的文件": '[1/1] x\n".venv\\Scripts\\python.exe" ghost\\nope.py\n',
    "步骤编号不连续": "[1/4] a\n[4/4] b\n",
    "漏掉提交包": "[1/1] regression\n",
}


def _fired_guards(text: str) -> list:
    """把假 run.bat 写进临时目录，返回被触发的守卫名列表（读完立即还原全局 BAT）。"""
    original = G.BAT
    with tempfile.TemporaryDirectory() as d:
        fake = Path(d) / "run.bat"
        fake.write_bytes(text.encode("utf-8"))
        G.BAT = fake
        fired = []
        for fn in (
            G.test_run_bat_is_pure_ascii,
            G.test_run_bat_referenced_paths_exist,
            G.test_run_bat_step_numbering_is_self_consistent,
            G.test_run_bat_covers_submission_pack,
        ):
            try:
                fn()
            except AssertionError:
                fired.append(fn.__name__)
        G.BAT = original
        return fired


@pytest.mark.parametrize("name,text", MUTANTS.items())
def test_every_mutant_is_caught(name, text):
    assert _fired_guards(text), f"守卫对变异体「{name}」完全没反应 —— 这些守卫是装饰品"


def test_guards_pass_on_real_run_bat():
    assert not _fired_guards(G.BAT.read_bytes().decode("utf-8", errors="replace")), (
        "真实 run.bat 竟然触发守卫 —— 先修 run.bat，别改守卫"
    )