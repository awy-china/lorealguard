# -*- coding: utf-8 -*-
"""从零重造验收：删掉全部生成物，重建样本、重新打包，要求**逐字节回到同一个 zip**。

为什么值得 5 秒的测试预算：
    我们对第三方做的头号承诺是「任何人都能重造出与我们逐字节相同的样本包」。
    只跑一次导出器（上一份测试做的事）证明不了它 —— 那只是「同一堆文件再打包一次」。
    这里连**样本本身**都从零重生成，才真正覆盖了生成器的确定性。

黄金哈希（GOLDEN）是**变更的闸门**：样本集有任何有意改动（新增样本/改文案/调参数），
这个测试必红；红了要做的不是改断言，而是
  ① 核对该改动是否已登记（manifest version、跑分报告、交付文档同步）
  ② 重新导出并把新 sha256 写回 GOLDEN + PRIVACY/README 与交付文档
（换句话说：改样本 = 换版本号 = 重新取证，不允许静默漂移。）
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SUITE = ROOT / "output" / "p2" / "suite"
MANIFEST = ROOT / "output" / "p2" / "manifest.json"
GEN_SUITE = ROOT / "samples" / "make_p2_suite.py"
EXPORTER = ROOT / "tools" / "make_submission_pack.py"
ZIP = ROOT / "dist" / "submission_pack" / "lorealguard_testset_v1.zip"

GOLDEN_ZIP_SHA256 = "0ad084365d49bc814b4ca21ee2c910aa1fe90e7dbd4888452a09edccc4e4f00e"
EXPECTED_SAMPLE_DIRS = 60
EXPECTED_PACK_FILES = 187  # 样本 180 + 生成器 3 + 包级 4（186 条进 SHA256SUMS）


def _run(script: Path):
    """跑生成器/导出器；PYTHONPATH 必须摘掉（本机它被全局占用会污染 import）。"""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [PY, str(script)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, f"{script.name} 失败：\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}"


@pytest.mark.skipif(not GEN_SUITE.exists(), reason="缺样本生成器")
def test_从零重造样本并打包逐字节回到黄金哈希():
    shutil.rmtree(SUITE, ignore_errors=True)
    MANIFEST.unlink(missing_ok=True)

    _run(GEN_SUITE)
    assert len([p for p in SUITE.iterdir() if p.suffix in {".jpg", ".png"}]) >= EXPECTED_SAMPLE_DIRS

    _run(EXPORTER)
    assert ZIP.exists()
    got = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    assert got == GOLDEN_ZIP_SHA256, (
        "从零重造得到的 zip 与黄金哈希不符 → 生成器或打包过程有非确定性，"
        f"或者样本集被改过却没走换版本流程。\n  期望 {GOLDEN_ZIP_SHA256}\n  实际 {got}"
    )
    assert EXPECTED_PACK_FILES == sum(1 for p in (ROOT / "dist" / "submission_pack" / "lorealguard_testset_v1").rglob("*") if p.is_file())