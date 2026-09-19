# -*- coding: utf-8 -*-
"""提交包导出器（tools/make_submission_pack.py）的冒烟测试。

守两件事：
1. **官方格式**：每样本一文件夹，且四要素（文案 .txt / 图 .jpg / README.txt）齐全。
2. **逐字节可复现**：同样输入跑两次 → zip 的 sha256 必须完全相同。
   （这是我们对第三方做出的承诺：「任何人可重造出逐字节相同的样本」。承诺要么被测试焊死，
   要么就是一句营销话。）
"""
from __future__ import annotations

import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "make_submission_pack.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("make_submission_pack", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pack_module():
    if not TOOL.exists():
        pytest.skip("导出器不存在")
    if not (ROOT / "output" / "p2" / "manifest.json").exists():
        pytest.skip("缺 P2 清单，先跑 samples/make_p2_suite.py")
    return _load_tool()


def test_导出器结构符合官方格式(pack_module):
    """每样本一文件夹 + 文案/图/README.txt 三件套。"""
    rc = pack_module.main()
    assert rc == 0
    pack_root = pack_module.OUT_DIR / "lorealguard_testset_v1"
    assert pack_root.is_dir()
    sample_dirs = sorted(p for p in (pack_root / "samples").iterdir() if p.is_dir())
    assert len(sample_dirs) >= 60, "样本文件夹数不应少于 60"
    first = sample_dirs[0]
    for required in ("image.jpg", "text.txt", "README.txt"):
        assert (first / required).exists(), f"缺官方要求的 {required}"
    # README.txt 四要素必须逐条出现（官方点名要求）
    txt = (first / "README.txt").read_text(encoding="utf-8")
    for field in ("来源", "类型", "是否伪造", "伪造方式"):
        assert field in txt, f"单样本 README.txt 缺「{field}」"


def test_包根必须有隐私声明与校验清单(pack_module):
    root = pack_module.OUT_DIR / "lorealguard_testset_v1"
    assert (root / "PRIVACY.txt").exists()
    assert (root / "SHA256SUMS.txt").exists()
    assert (root / "MANIFEST.json").exists()
    assert (root / "README.md").exists()
    # 生成器随包分发（可复现性：别人能自己重造样本）
    gens = list((root / "generators").glob("*.py"))
    assert len(gens) >= 3, "生成器脚本应随包提供"


def test_zip逐字节可复现(pack_module):
    """同一份输入跑两次 → 同一个 sha256。不成立就等于「可复现」是空话。"""
    zip_path = pack_module.OUT_DIR / "lorealguard_testset_v1.zip"

    def sha(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    assert pack_module.main() == 0
    s1 = sha(zip_path)
    assert pack_module.main() == 0
    s2 = sha(zip_path)
    assert s1 == s2, "同输入两次打包 sha256 不一致 → 打包过程有非确定性（时间戳/顺序）"

    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        assert any(n.endswith("README.md") for n in names)
        assert z.testzip() is None, "zip 完整性自检失败"


def test_SHA256SUMS逐条覆盖且哈希真实(pack_module):
    """校验清单必须覆盖包内每一个文件（自身除外），且每条哈希与磁盘实物一致。

    差一个文件 = 交付包里存在「没被校验的漏网文件」，那这份清单就是装饰品。
    """
    root = pack_module.OUT_DIR / "lorealguard_testset_v1"
    lines = [ln for ln in (root / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
    listed = {ln.split("  ", 1)[1] for ln in lines}
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name != "SHA256SUMS.txt"
    }
    assert listed == actual, f"清单与实际文件不符：漏 {actual - listed}／多 {listed - actual}"
    for ln in lines:
        want, rel = ln.split("  ", 1)
        got = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        assert want == got, f"{rel} 的哈希与清单不符（包已被人改过或写入非确定性）"