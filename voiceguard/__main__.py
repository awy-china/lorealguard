"""命令行入口：`python -m voiceguard <图片路径> [--text 文案] [--comments-file 评论.txt] [...]`

为什么要有 CLI：评审/评委要能在**不给模型、不给网络**的条件下把这套东西跑起来。
跑出来的是一份双向账本报告（md + json）+ 一张证据图 + 一个 sha256 指纹。

**三个入口与网页（`demo/`）同源**：`guard.py:245` 的 `GuardPipeline.run` 一直收
「图 / 文案 / 评论」的任意组合，`contract.ContentItem` 也一直有这三个字段 ——
2026-09-23 之前**只是这个 CLI 没把它们接出来**，于是"文案通路"与"评论区通路"
只能从网页进（README 里三环只有一环在命令行上有出口）。
现在两边都能进三个入口，且「一行一条评论」这件事只有一处定义
（`core/contract.py::parse_comment_lines`）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.contract import ContentItem, parse_comment_lines
from .core.tags import tag_error
from .guard import GuardPipeline
from .report import render_markdown, summarize


def main(argv: list | None = None) -> int:
    #: 产品侧编码兜底（2026-09-28 实测修）：把输出接管道 / 重定向 / IDE 运行窗时，
    #: 流按 locale（中文 Windows＝cp936）编码，报告里的 `⚠️`（U+26A0，ledger.py:99）
    #: 不在 GBK 内 ⇒ print 抛 UnicodeEncodeError，**报告主体整段打不出来**。
    #: 这里刻意不设 encoding="utf-8"：那会把本来可读的中文变成乱码；
    #: 只放宽 errors ⇒ 中文照旧可读，仅不可表示的 U+26A0 退化成反斜杠转义字面量。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="backslashreplace")
        except Exception:                            # noqa: BLE001 —— pytest 捕获流未必支持
            pass

    ap = argparse.ArgumentParser(
        prog="voiceguard", description="LorealGuard 内容真实性核验（双向账本报告）")
    ap.add_argument("image", nargs="?", default=None,
                    help="待核验的图片路径。可省略 —— 只核验文案或评论时不必给图。")
    ap.add_argument("--text", default="",
                    help="文案/口播文本（「图是真的、话是假的」那条通路，F4）。")
    ap.add_argument("--comments-file", default=None,
                    help="评论文件，**一行一条**（评论区通路 F5）。空行忽略。")
    ap.add_argument("--out", default=None, help="输出目录（默认 output/guard/）")
    ap.add_argument("--tag", default=None, help="产物文件名前缀（默认取图片名）")
    ap.add_argument("--quiet", action="store_true", help="只打印一行摘要")
    ap.add_argument("--no-sheet", action="store_true", help="不生成证据图")
    ap.add_argument(
        "--explain", action="store_true",
        help="额外跑 F3 语义解释层：由**开源权重**模型（Apache-2.0）把已定位疑点翻成人话。"
             "默认关闭 → 默认路径零模型、零网络（拔网线可跑）；开启即表示同意把该图送往百炼。"
             "解释永不参与判定，只写进报告的独立字段与附二。")
    ap.add_argument(
        "--agent", action="store_true",
        help="额外跑 F9 Agent 编排层（**非判定**）：由**开源权重**模型在账本算完之后，"
             "决定还该补哪一步取证、该向创作者追问什么。它**不产出**真伪结论，"
             "追加取证的结果只写进报告附三（不回填双向账本）。"
             "默认关闭 → 默认路径零模型、零网络；开启即表示同意把账本摘要送往百炼。")
    ap.add_argument(
        "--observe", action="store_true",
        help="加跑层 1 的**观察级**检测器（光影一致性 / 色彩过渡 / 噪声残差一致性）—— 前两个是官方点名的、第三个是我们自建的，"
             "但它们的结论**不进判定**：只写报告「附四 观察栏」（不进左栏物证、不进规则表）。"
             "默认关闭 → 默认只跑 ela；打开后判定面（含 confidence）逐字节不变。")
    args = ap.parse_args(argv)

    p = None
    if args.image:
        p = Path(args.image)
        if not p.exists():
            print(f"输入不存在：{p}", file=sys.stderr)
            return 2

    comments: list = []
    if args.comments_file:
        cf = Path(args.comments_file)
        if not cf.exists():
            print(f"评论文件不存在：{cf}", file=sys.stderr)
            return 2
        comments = parse_comment_lines(cf.read_text(encoding="utf-8", errors="replace"))

    if p is None and not args.text and not comments:
        print("没有可核验的输入：至少要给 <图片>、--text、--comments-file 之一。\n"
              "（本系统不猜 —— 三个都没有时它不会假装能判什么。）", file=sys.stderr)
        return 2

    #: 无图时不生成证据图：证据图是拿像素坐标画的，没有像素就无从画起。
    sheet = (not args.no_sheet) and p is not None

    #: tag 是**输入**（2026-09-25 · round8 缺陷④⑤）：它一路透传到
    #: `report/ledger.py:296` 拼文件名，中间零校验 —— 修前 `--tag '../_ESCAPED'`
    #: 会把产物写到 `--out` 的**上级目录**且 rc=0，`--tag 'a:b'` 在 Windows 上抛裸栈。
    #: 规则只在 `core/tags.py` 一处，这里只是把门（网页侧早已是同一条）。
    #: 校验点在**用到 tag 之前**（`GuardPipeline` 一跑就会 mkdir 并落盘）。
    tag = args.tag if args.tag is not None else (p.stem if p is not None else "text")
    tag_err = tag_error(tag)
    if tag_err is not None:
        print(f"非法 tag（{tag}）：{tag_err}", file=sys.stderr)
        print("（tag 只是产物文件名前缀，必须自己就是安全的名字。）", file=sys.stderr)
        return 2

    pipe = GuardPipeline(explain=args.explain, agent=args.agent, observe=args.observe)
    try:
        report = pipe.run(
            ContentItem(image_path=str(p) if p is not None else None,
                        text=args.text,
                        comments=comments,
                        kind="comment_thread" if comments else "image"),
            outdir=args.out, tag=tag, sheet=sheet)
    except (ValueError, OSError) as e:
        #: 坏文件口径就是 ValueError（`forensics/base.py:119-134` 刻意把 0 字节 / 坏魔数
        #: 收敛到同一条中文 ValueError）；OSError 覆盖权限与磁盘。**真 bug 仍要露栈** ——
        #: 这里刻意不接 Exception，否则会把程序缺陷登记成"用户输入不合法"。
        print(f"输入无法处理：{e}", file=sys.stderr)
        print("（这一跑没有出账本；上面的说法来自文件本身，不是判定。）", file=sys.stderr)
        return 2
    print(summarize(report))
    if args.explain:
        ex = report.explanation or {}
        print(f"[F3 语义解释] 状态={ex.get('status')} 模型={ex.get('model')}"
              f"（{ex.get('license') or '—'}）", file=sys.stderr)
        if ex.get("status") != "ok":
            print(f"[F3 语义解释] 未产出：{ex.get('note')}", file=sys.stderr)
        print("[F3 语义解释] 该层只做描述与解释，**不参与判定**；判定结果见报告第一至四节。",
              file=sys.stderr)
    if args.agent:
        tr = report.agent_trace or {}
        print(f"[F9 Agent 编排] 状态={tr.get('status')} 模型={tr.get('model')}"
              f"（{tr.get('license') or '—'}）", file=sys.stderr)
        if tr.get("status") != "ok":
            print(f"[F9 Agent 编排] 未产出：{tr.get('note')}", file=sys.stderr)
        print("[F9 Agent 编排] 该层只做追加取证与追问，**不参与判定**；"
              "判定结果见报告第一至四节。", file=sys.stderr)
    if args.observe:
        obs = report.observations or {}
        names = [d if isinstance(d, str) else d.get("name")
                 for d in (obs.get("detectors") or [])]
        print(f"[观察栏] 已加跑 {len(names)} 个观察级检测器：{'、'.join(names) or '—'}",
              file=sys.stderr)
        print("[观察栏] 该栏只做记录，**不参与判定**（不进左栏物证、不进规则表）；"
              "判定结果见报告第一至四节。", file=sys.stderr)
    if not args.quiet:
        print()
        print(render_markdown(report))
    for f in report.evidence_files:
        print(f"产物：{f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())