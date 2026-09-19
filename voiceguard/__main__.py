"""命令行入口：`python -m voiceguard <图片路径> [--out DIR] [--tag NAME] [--quiet]`

为什么要有 CLI：评审/评委要能在**不给模型、不给网络**的条件下把这套东西跑起来。
跑出来的是一份双向账本报告（md + json）+ 一张证据图 + 一个 sha256 指纹。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.contract import ContentItem
from .guard import GuardPipeline
from .report import render_markdown, summarize


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="voiceguard", description="LorealGuard 内容真实性核验（双向账本报告）")
    ap.add_argument("image", help="待核验的图片路径")
    ap.add_argument("--out", default=None, help="输出目录（默认 output/guard/）")
    ap.add_argument("--tag", default=None, help="产物文件名前缀（默认取图片名）")
    ap.add_argument("--quiet", action="store_true", help="只打印一行摘要")
    ap.add_argument("--no-sheet", action="store_true", help="不生成证据图")
    args = ap.parse_args(argv)

    p = Path(args.image)
    if not p.exists():
        print(f"输入不存在：{p}", file=sys.stderr)
        return 2

    report = GuardPipeline().run(ContentItem(image_path=str(p)), outdir=args.out,
                                 tag=args.tag or p.stem, sheet=not args.no_sheet)
    print(summarize(report))
    if not args.quiet:
        print()
        print(render_markdown(report))
    for f in report.evidence_files:
        print(f"产物：{f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())