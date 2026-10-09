"""红队报告的**同源守卫**：交付的报告必须能从冻结语料重放出来（逐字节）。

为什么值得写（2026-09-20 实测教训）：
  这次先看到一个后台日志写着 "84 条线程"，而盘上语料是 99 条 —— 只靠"看着对"没法
  判断是**口径混源**还是**旧日志**。能分辨的只有一件事：把报告在盘上从语料**重算一遍**。
  重放 diff = 0 行才算同源；只要有人手改了报告、或语料被换掉，这条守卫立刻变红。

铁律对应：**口径同步必须带命中次数断言**；**一条不会红的检查等于没有检查**。
本文件含 3 条变异注入（手改报告 / 换语料 / 抬头少一行），断言之必须变红。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))

import red_team_bench as bench  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_guard import require_repo                          # noqa: E402

P5_REPORT = ROOT / "output" / "p5" / "comment_report.md"
REPORT_RAW = bench.REPORT
#: 仓库报告是**生成物**（`output/` 不随公开仓/源码包分发）⇒ 缺它时本文件几条**显式弃权**（D-49）
REDTEAM_REPORT_REL = "output/redteam/red_team_report.md"


def _regenerate() -> str:
    """从盘上语料重算报告文本（只读语料，不调 LLM，约 0.7s）。"""
    payload = json.loads(bench.PAIRS.read_text(encoding="utf-8"))
    rows = bench.run_rows(payload["items"], bench.load_rules())
    viol = bench.attrib_violations(rows)
    return bench.render(payload, rows, viol, fingerprint_ok=True)


def test_report_reproduces_byte_for_byte_from_frozen_corpus():
    """盘上的报告 == 从语料重算的报告（多一个字、少一个字都要红）。"""
    require_repo(REDTEAM_REPORT_REL)
    generated = _regenerate()
    on_disk = bench.REPORT.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "交付的报告与冻结语料不同源：要么有人手改了报告，要么语料被换过。\n"
        "正确做法：重跑 experiments/red_team_bench.py 重新生成，不要手改报告。"
    )


def test_headline_numbers_are_derived_from_corpus_not_typed_in():
    """报告里的头条数字必须能从语料重算出来（不是手写的常量）。"""
    payload = json.loads(bench.PAIRS.read_text(encoding="utf-8"))
    rows = bench.run_rows(payload["items"], bench.load_rules())
    text = _regenerate()

    # ① 样本量：三支矛各 33 条 → 语料自己声明的 counts 必须与 items 实际条数一致
    per_arm = {}
    for r in rows:
        per_arm[r["arm"]] = per_arm.get(r["arm"], 0) + 1
    assert per_arm == {"evade": 33, "frame": 33, "blind": 33}, f"臂样本量变了：{per_arm}"
    assert payload["counts"]["total"] == len(rows) == 99, "语料声明的总数与实际条数不符"

    # ② 三支矛战果：报告里的每一个数字都要能在语料上复算出来
    frame = [r for r in rows if r["arm"] == "frame"]
    blind = [r for r in rows if r["arm"] == "blind"]
    evade = [r for r in rows if r["arm"] == "evade"]
    frame_risk = sum(1 for r in frame if r["tier"] in bench.RISK)
    frame_hi = sum(1 for r in frame if r["tier"] == "高风险")
    blind_risk = sum(1 for r in blind if r["tier"] in bench.RISK)
    evade_det = sum(1 for r in evade if r["tier"] in bench.RISK)

    assert (frame_risk, frame_hi) == (23, 3), f"矛 B 误报/红线击穿变了：{frame_risk}/{frame_hi}"
    assert blind_risk == 0, f"矛 C 误报变了：{blind_risk}"
    assert evade_det == 0, f"矛 A 检出变了：{evade_det}"

    # ③ 这些数字必须真的出现在报告文本里（命中次数断言，防"算了但没写进去"）
    for needle in ("| A `evade` | 恶意 | 33 | 0 | 0 | 绕过 100.0%",
                   "| B `frame` | 良性 | 33 | 23 | 3 | 误报 69.7%",
                   "| C `blind` | 良性 | 33 | 0 | 0 | 误报 0.0%"):
        assert needle in text, f"报告里读不到这一行：{needle}"


def test_red_line_breaches_in_report_match_registry():
    """报告里点名的红线突破条数 = 登记表里的条数（两边不许各说各话）。"""
    reg = json.loads((ROOT / "samples" / "red_team" / "known_breaches.json")
                     .read_text(encoding="utf-8"))
    ids = [b["id"] for b in reg["breaches"]]
    text = _regenerate()
    assert len(ids) == 3, f"登记表条数变了：{ids}"
    # 报告里两处都要提到这个条数：§一 的结论行 + 矛 B 明细里的红线清单
    assert f"「高风险」`{len(ids)}` 条" in text, "§一 的击穿条数与登记表不一致"
    assert f"**击穿红线的 {len(ids)} 条**" in text, "矛 B 明细里的击穿条数与登记表不一致"
    for i in ids:
        assert f"`{i}`" in text, f"登记表里的 {i} 在报告里读不到"


def test_section_five_p5_numbers_are_checked_not_trusted():
    """报告第 242 行的 P5 数字是**写死的常量** → 这里给它配一条算术断言。

    `red_team_bench.py` 里那行 `| F5 评论区（P5 自造集） | 0/33 = 0.0% | 8.68% | 33/33 = 100% |`
    是手工常量（未从 P5 语料派生）。本守卫不放过它：用同一个 Clopper-Pearson 助手复算，
    并要求 P5 自己的报告里有同样的说法。**写死的数字必须被检查，否则它会悄悄过期。**
    """
    require_repo("output/p5/comment_report.md",
                 note="仓库内先跑 experiments/p5_comment_bench.py 生成")
    assert f"{bench.cp_upper95(0, 33) * 100:.2f}%" == "8.68%", "写死的上界与 CP 助手算出的不一致"
    assert f"{bench.cp_upper95(0, 33) * 100:.1f}%" == "8.7%", "P5 报告里的 8.7% 与 CP 助手不一致"
    p5 = P5_REPORT.read_text(encoding="utf-8")
    assert "0/33" in p5 and "33 条 = 100.0%" in p5, "P5 报告里的真数字变了，第 242 行常量已过期"


def test_mutation_hand_edited_report_is_caught(tmp_path, monkeypatch):
    """注入故障：有人手改交付报告（多写一行跑到最前面）→ 同源守卫必须红。"""
    require_repo(REDTEAM_REPORT_REL)
    tampered = tmp_path / "red_team_report.md"
    tampered.write_text("手工加的结论：我们零误报 ✅\n"
                        + bench.REPORT.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(bench, "REPORT", tampered)
    with pytest.raises(AssertionError):
        test_report_reproduces_byte_for_byte_from_frozen_corpus()


def test_mutation_swapped_corpus_is_caught(tmp_path, monkeypatch):
    """注入故障：语料被换成另一批（少一条）→ 样本量断言必须红。"""
    payload = json.loads(bench.PAIRS.read_text(encoding="utf-8"))
    payload["items"] = payload["items"][:-1]
    swapped = tmp_path / "red_team.json"
    swapped.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bench, "PAIRS", swapped)
    with pytest.raises(AssertionError):
        test_headline_numbers_are_derived_from_corpus_not_typed_in()


def test_delivery_copy_is_reproducible_from_repo_bytes():
    """交付版（抬头 + 正文）必须能从仓库字节重造 —— 抬头是固化常量，不是手工贴的。

    取证背景：交付目录那份比仓库报告多 20 行抬头，而抬头原先**手工贴上去**，
    手工的没法从零重造。本守卫钉住：抬头字节 + 报告字节 == 交付版字节。
    """
    require_repo(REDTEAM_REPORT_REL)
    sys.path.insert(0, str(ROOT / "tools"))
    import make_redteam_delivery as mrd  # noqa: E402

    built = mrd.build()
    assert built.startswith(mrd.header_bytes()), "交付版没以固化抬头开头"
    assert "# P6 · 红队实测报告 —— 把刀递给对手".encode("utf-8") in built, \
        "交付版正文里读不到仓库报告的开头"
    body = REPORT_RAW.read_bytes()
    assert built.endswith(body), "交付版正文与仓库报告不是逐字节相同"
    # 抬头必须是 CRLF（与正文、与盘上交付版一致），不许出现裸 LF
    hb = mrd.header_bytes()
    assert hb.count(b"\r\n") == len(mrd.HEADER_LINES), "抬头行数变了"
    assert hb.replace(b"\r\n", b"").count(b"\n") == 0, "抬头里混进了裸 LF"

    out = mrd.DEFAULT_OUT
    if out.exists():  # 本机有交付目录时必须逐字节一致
        assert built == out.read_bytes(), "交付版与脚本重造的字节不一致（有人手改了交付文件）"


def test_mutation_dropped_header_line_is_caught(monkeypatch):
    """注入故障：抬头少一行 → 字节比较必须看得出来。"""
    require_repo(REDTEAM_REPORT_REL)
    sys.path.insert(0, str(ROOT / "tools"))
    import make_redteam_delivery as mrd  # noqa: E402

    full = mrd.build()
    monkeypatch.setattr(mrd, "HEADER_LINES", mrd.HEADER_LINES[:-1])
    assert mrd.build() != full, "抬头少一行居然看不出来"


def test_all_mutations_were_exercised():
    """元检查：变异注入必须真的是 3 种（防止有人把上面几条注释掉）。"""
    names = [n for n in dir(sys.modules[__name__]) if n.startswith("test_mutation_")]
    assert len(names) == 3, f"变异注入只剩 {len(names)} 种：{names}"