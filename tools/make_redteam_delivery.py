"""把仓库里的红队报告拼成**交付版**（抬头 + 正文），字节可重造。

为什么需要它（2026-09-20 取证发现）：交付目录那份 `loreal-redteam-report.md` 比仓库报告
多 20 行「交付版」抬头，而那段抬头是**手工贴上去的** —— 手工贴的东西没法从零重造，
下次谁重跑一遍跑分器，交付版就悄悄对不上了。本脚本把抬头固化成一个常量，
用**字节级拼接**（HEADER + 报告原文，两边都是 CRLF）产出交付版：
    md5 必须与生成前盘上的那份逐字节一致，否则脚本自己报差异行。

用法：
    python tools/make_redteam_delivery.py                # 写到默认交付路径
    python tools/make_redteam_delivery.py --check        # 只校验、不写盘
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "output" / "redteam" / "red_team_report.md"
DEFAULT_OUT = Path("D:/deliver/compete/loreal-redteam-report.md")

# 交付版抬头（20 行）。逐字固化，不靠记忆：与 2026-09-20 盘上那份逐字节一致。
#
# ⚠️ 这里的**散文属于冻结产物**：`tools/make_submission_pack.py` 把本模块 build() 的字节
# 写进包内 EVIDENCE/redteam-report.md，所以改一句话就换掉整个提交包的 sha256
# （2026-09-24 实测：第 17 行多加 2 行注释式说明 ⇒ zip 由 e3238bd6… 变 fbb38a35…，
#  需要连带重出印章/PPT/PDF 与重录演示素材）。要补充说明请写进 docs/DEBT.md，
# 不要写进这个抬头。第 17 行的恢复依据：交付区 zip 里那份老报告的抬头原文
# （1069 字节，见 output/_old_header.txt）。
HEADER_LINES = [
    "# 红队实测报告（交付版）· LorealGuard / 信任守护师",
    "",
    "> **这份报告回答的问题**：把我们的规则表**全文连同数值门槛**交给对手，它能不能让我们翻车？",
    "> 协议写在测量之前（`loreal-redteam-protocol.md`），测完**判据零修订**；语料一次性封存，跑分器只读它。",
    "",
    "**一行复现**（离线、不调 API、约 2 秒）：",
    "",
    "```",
    "cd /d/LorealGuard && env -u PYTHONPATH .venv/Scripts/python.exe experiments/red_team_bench.py",
    "```",
    "",
    "跑分器有三条硬约束：① **靶子指纹不符就拒绝出分**（当前 `rules.yaml` sha256[:16] 必须等于语料生成时的",
    "`0e5ea247919138de`，否则 exit 3）—— 防\"看完结果偷偷改阈值再测\"；② 红线突破必须与",
    "`samples/red_team/known_breaches.json` **逐条一致**（多一条红、少一条也红）；③ 归因完备性：判了就必须有",
    "规则支撑、归因码、可复算数字（缺一项即违规）。",
    "",
    "在 `run.bat` 的一键验收里这是**第 7 步**（8 步全绿，`RUNBAT_EXIT=0`）。",
    "",
    "---",
    "",
]


def header_bytes() -> bytes:
    """抬头按 CRLF 落地（与仓库报告、与盘上交付版一致）。"""
    return ("\n".join(HEADER_LINES) + "\n").replace("\n", "\r\n").encode("utf-8")


def build() -> bytes:
    """交付版 = 抬头 + 报告原文（报告本身已是 CRLF，直接字节拼接，不做任何换行转换）。"""
    body = REPORT.read_bytes()
    return header_bytes() + body


def _first_diff(a: bytes, b: bytes) -> str:
    la, lb = a.split(b"\r\n"), b.split(b"\r\n")
    for i, (x, y) in enumerate(zip(la, lb), 1):
        if x != y:
            return f"首处差异在第 {i} 行：\n  盘上: {x[:90]!r}\n  新生成: {y[:90]!r}"
    return f"行前缀相同，长度不同：盘上 {len(a)} bytes / 新生成 {len(b)} bytes"


def _utf8_stdout() -> None:
    """把 stdout 钉成 UTF-8 —— 否则**重定向到文件**时本脚本会自己崩掉（2026-09-24 第二次实测）。

    本脚本的诊断行带 `⚠️`/`❌`/`✅`。Windows 上 stdout 一旦不是终端（`> output/x.txt`），
    Python 就按 locale（本机 GBK）编码 → 打印 `⚠️` 抛 `UnicodeEncodeError` → **exit 1**，
    而且崩在写盘**之前**：文件根本没被更新，看起来却像"生成器判定不一致"。
    与 `tools/check_number_consistency.py:128` 的 `_utf8_stdout()` 同一个修法。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass          # 已被包成非 TextIOWrapper：不拦，继续跑


def main() -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--check", action="store_true", help="只校验，不写盘")
    args = ap.parse_args()

    if not REPORT.exists():
        print(f"❌ 找不到仓库报告：{REPORT}\n   先跑 experiments/red_team_bench.py 生成。")
        return 2

    out = Path(args.out)
    new = build()
    prior = out.read_bytes() if out.exists() else b""
    md5_new = hashlib.md5(new).hexdigest()

    print(f"仓库报告 : {REPORT.name}  {len(REPORT.read_bytes())} bytes")
    print(f"交付版   : {out}  {len(new)} bytes  md5={md5_new}")
    if prior:
        print(f"生成前盘上: {len(prior)} bytes  md5={hashlib.md5(prior).hexdigest()}")

    if args.check:
        ok = prior == new
        print("结论：字节一致 ✅" if ok else f"结论：❌ 不一致 —— {_first_diff(prior, new)}")
        return 0 if ok else 1

    if prior and prior != new:
        print(f"⚠️ 与盘上那份不一致 —— {_first_diff(prior, new)}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(new)
    print("已写盘。")
    if prior and prior == new:
        print("结论：字节可重造 ✅（写盘内容与生成前那份逐字节一致）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())