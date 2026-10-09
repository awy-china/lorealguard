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
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "make_submission_pack.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_guard import require_paths, require_repo        # noqa: E402

#: `main()` 的上游语料：全在 `output/`（生成物）⇒ 公开仓/源码包按设计不带它们。
#: 缺了就**显式弃权**并点名，而不是读到崩（`docs/DEBT.md` D-49）。
UPSTREAM = ("output/edge/manifest.json", "output/p2/bench_report.md",
            "output/redteam/red_team_report.md")


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


def test_包内生成器不得含挂钟时间(pack_module):
    """**包内不许出现挂钟时间** —— 否则"逐字节可复现"过一天就作废。

    实测教训（2026-09-20 被抓）：`samples/make_p2_text_pairs.py` 原本写
    `"generated": date.today().isoformat()`，于是黄金 zip 只在生成当天成立；
    第二天跑「从零重造」测试立刻红，而且差异只在一行日期上、极难发现。
    这里做静态扫描：生成器源码里出现 `date.today()` / `datetime.now()` / `time.time()`
    → 立即红（并指出是哪个文件哪一处）。日期只允许是**版本常量**（`PACK_VERSION_DATE`）。
    """
    import ast
    import re
    # 只看**真实调用**（AST），不看注释/字符串 —— 否则连"解释这个坑"的注释都会被误判。
    PAT = re.compile(r"(^|\.)(date|datetime|time)\.(today|now|utcnow|time)$")
    offenders: list[str] = []
    for g in pack_module.GENERATORS:
        p = ROOT / g
        if not p.exists():
            offenders.append(f"{g}: 文件不存在")
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if PAT.search(name):
                    offenders.append(f"{g}:{node.lineno} {name}()")
    assert not offenders, (
        "包内生成器出现挂钟时间（会让提交包不可复现，黄金哈希只在当天成立）："
        + "; ".join(offenders)
    )


def test_导出器结构符合官方格式(pack_module):
    """每样本一文件夹 + 文案/图/README.txt 三件套。"""
    require_repo(*UPSTREAM,
                 note="仓库内先跑 experiments/p2_bench.py 与 experiments/red_team_bench.py")
    rc = pack_module.main()
    assert rc == 0
    pack_root = pack_module.OUT_DIR / f"lorealguard_testset_{pack_module.PACK_VERSION}"
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
    root = pack_module.OUT_DIR / f"lorealguard_testset_{pack_module.PACK_VERSION}"
    require_paths([root], note="仓库内先跑 tools/make_submission_pack.py")
    assert (root / "PRIVACY.txt").exists()
    assert (root / "SHA256SUMS.txt").exists()
    assert (root / "MANIFEST.json").exists()
    assert (root / "README.md").exists()
    # 生成器随包分发（可复现性：别人能自己重造样本）
    gens = list((root / "generators").glob("*.py"))
    assert len(gens) >= 3, "生成器脚本应随包提供"


def test_zip逐字节可复现(pack_module):
    """同一份输入跑两次 → 同一个 sha256。不成立就等于「可复现」是空话。"""
    zip_path = pack_module.OUT_DIR / f"lorealguard_testset_{pack_module.PACK_VERSION}.zip"
    require_paths([zip_path], note="仓库内先跑 tools/make_submission_pack.py")

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
    root = pack_module.OUT_DIR / f"lorealguard_testset_{pack_module.PACK_VERSION}"
    require_paths([root], note="仓库内先跑 tools/make_submission_pack.py")
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

# ---------------------------------------------------------------------------
# v2 → v3 新增守卫（2026-09-28）：文本一致 · 物证来路 · 附录只搬运
# 三条都是"合成场景可证伪"的：把对应的源改一个字，它们就红。
# ---------------------------------------------------------------------------

def test_包README的样本数与实际目录数一致(pack_module):
    """主集 60 与边缘 9 必须**分列**，且总数等于包内真实目录数。

    修前实况（v2 生成物 `README.md:9`）写的是「共 60 个」—— 把主集说成了全部，
    而 `:40` 又说有 9 条边缘样本 ⇒ 同一份 README 内部数字打架。
    这条守卫同时钉住两件事：**分列表述**（防再含糊）与**总数==实际**（防改漏一处）。
    """
    import json
    root = pack_module.OUT_DIR / f"lorealguard_testset_{pack_module.PACK_VERSION}"
    require_paths([root], note="仓库内先跑 tools/make_submission_pack.py")
    readme = (root / "README.md").read_text(encoding="utf-8")
    n_dirs = len([p for p in (root / "samples").iterdir() if p.is_dir()])
    main_n = json.loads(pack_module.MAIN_MANIFEST.read_text(encoding="utf-8"))["count"]
    edge_n = json.loads(pack_module.EDGE_MANIFEST.read_text(encoding="utf-8"))["count"]
    assert main_n + edge_n == n_dirs, f"主集 {main_n} + 边缘 {edge_n} ≠ 包内目录 {n_dirs}"

    assert f"主集 {main_n} 个 + 边缘场景 {edge_n} 个" in readme, "README 未把主集与边缘分列"
    assert f"**共 {n_dirs} 个样本目录**" in readme, "README 的总数与包内实际目录数不一致"
    assert f"这 {edge_n} 条的回答方式" in readme, "README 的边缘样本条数与清单不一致"
    # 反向：修前那两处说法不许复活
    assert "（共 60 个）" not in readme, "README 又把主集说成了全部（修前的老毛病）"
    assert "这 10 条" not in readme, "README 仍写「这 10 条」（实为 9 条）"


def test_红队物证带生成器一起交(pack_module):
    """包内有冻结物证 `EVIDENCE/red_team.json`，就必须有**生成它的脚本**。

    v2 及以前的缺口：物证在包内、生成器不在 ⇒ 第三方无法判断那份 JSON 怎么来的。
    这条守卫 = 把「生成器与物证对得上」焊死，而不是靠人记得往 GENERATORS 里加一行。
    """
    import json
    root = pack_module.OUT_DIR / f"lorealguard_testset_{pack_module.PACK_VERSION}"
    require_paths([root], note="仓库内先跑 tools/make_submission_pack.py")
    gens = {p.name for p in (root / "generators").glob("*.py")}
    assert "make_red_team.py" in gens, "包内缺红队语料生成器 ⇒ 物证来路不明（v2 的老缺口）"
    assert (root / "generators" / "make_red_team.py").read_bytes() == \
        (ROOT / "samples" / "make_red_team.py").read_bytes(), "包内生成器必须与仓库逐字节同源（只搬运不改逻辑）"
    assert (root / "generators" / "README.txt").exists(), "生成器目录缺依赖说明（第三方不知道它要什么）"
    # 它必须与「确定性生成器」分组隔离：混在一起会当场把挂钟时间守卫逼红
    assert "samples/make_red_team.py" not in pack_module.GENERATORS
    assert "samples/make_red_team.py" in pack_module.PROVENANCE_GENERATORS
    man = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    assert man["evidence"]["red_team_corpus"]["generator"] == "generators/make_red_team.py"
    assert man["evidence"]["red_team_corpus"]["runtime_use"].startswith("none")


def test_EVIDENCE附录是搬运不是改写(pack_module):
    """附录必须能从跑分报告**逐字回放** —— 否则「带出处行号」只是排版装饰。

    只做三件事，全是可证伪的：
      ① 每个出处区间 `bench_report.md:a-b` 指向的原文，必须逐字出现在附录里；
      ② 被摘的三节标题必须在附录里；
      ③ 附录**不得多出**报告里没有的表格行（= 不许夹带未经测量的新数字）。
    """
    import re
    root = pack_module.OUT_DIR / f"lorealguard_testset_{pack_module.PACK_VERSION}"
    require_paths([root], note="仓库内先跑 tools/make_submission_pack.py")
    report = pack_module.BENCH_REPORT.read_text(encoding="utf-8").splitlines()
    ex = (root / "EVIDENCE" / "bench_excerpt.md").read_text(encoding="utf-8")

    spans = re.findall(r"bench_report\.md:(\d+)-(\d+)`", ex)
    assert len(spans) == 3, f"附录应有 3 个出处区间（抬头 / 一+二节 / 七节），实得 {len(spans)}"
    for a, b in spans:
        seg = "\n".join(report[int(a) - 1:int(b)])
        assert seg.strip(), f"出处 {a}-{b} 是空区间"
        assert seg in ex, f"出处 {a}-{b} 的原文没有逐字进附录（被改写过？）"
    for h in ("## 一、六项指标", "## 二、承诺违背清单", "## 七、弃权分解"):
        assert h in ex, f"附录缺小节 {h}"
    extra = {ln for ln in ex.splitlines() if ln.startswith("|")} - set(report)
    assert not extra, f"附录多出了报告里没有的表格行（= 夹带了新数字）：{extra}"


def test_包README写明红队物证的模型口径(pack_module):
    """`red_team.json` 记的是研究档模型 ⇒ README 必须如实交代它**不进交付运行时**。

    口径三要素（缺一即红）：研究档 / 不参与交付运行时调用 / 唯一允许档与"一调用即拒"的出处。
    """
    root = pack_module.OUT_DIR / f"lorealguard_testset_{pack_module.PACK_VERSION}"
    require_paths([root], note="仓库内先跑 tools/make_submission_pack.py")
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "deepseek-v4.1-flash" in readme, "README 未点名物证里的模型档（会被误读成产品依赖）"
    for frag in ("研究档", "不参与交付运行时的任何调用", "qwen3.8-27b",
                 "voiceguard/config.py:37-38", "voiceguard/semantic/explain.py:86-90"):
        assert frag in readme, f"README 的物证口径缺「{frag}」"
    # 物证本身**一个字都不许改**：它必须仍写着当时的模型名
    assert '"model": "deepseek-v4.1-flash"' in \
        (root / "EVIDENCE" / "red_team.json").read_text(encoding="utf-8"), \
        "物证内容被改过 —— 测量内容不许修饰（要改只能连同生成器一起重取证并换版本）"
