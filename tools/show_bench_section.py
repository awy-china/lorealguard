"""录屏 C 用：把 `output/p2/bench_report.md` 的某一小节原样打到屏幕上。

为什么要有这个脚本：录屏 C 的作业要求是"打开报告、滚到《弃权分解》表"。
用一个**真读盘上文件**的小脚本比手工截图/手打表格可信 —— 屏幕上的每一行
都来自报告原文，脚本不做任何改写，并额外打印来源路径与文件 sha256 前 16 位，
让观众能把屏幕内容与仓库里的文件对上。

用法（录屏窗口里）：
    .venv\\Scripts\\python.exe tools\\show_bench_section.py --section 弃权分解
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

REPORT = pathlib.Path("output/p2/bench_report.md")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument("--section", default="弃权分解")
    ap.add_argument("--before", type=int, default=0, help="小节标题前保留的行数")
    ap.add_argument("--max-lines", type=int, default=18,
                    help="最多打印多少行（默认 18：控制台只有 ~29 行可视，印多了表格会被顶出屏外）")
    a = ap.parse_args()

    p = pathlib.Path(a.report)
    if not p.exists():
        print(f"[X] 找不到报告：{p.resolve()}")
        return 1
    raw = p.read_bytes()
    text = raw.decode("utf-8")
    idx = text.find(a.section)
    if idx < 0:
        print(f"[X] 报告里没有小节“{a.section}”；现有文件名：{p.name}")
        return 1

    lines = text.splitlines()
    # ⚠️ 只认**标题行**（以 # 开头且含小节名）。踩过的坑：按"首次出现"匹配时，
    # 报告正文里一句"见《弃权分解》"会把起点提前，屏幕上就只剩尾部文字、表格被顶出屏外。
    start = None
    for i, ln in enumerate(lines):
        if a.section in ln and ln.lstrip().startswith("#"):
            start = i
            break
    if start is None:  # 退路：报告里没有规范标题时才退回"首次提到"
        for i, ln in enumerate(lines):
            if a.section in ln:
                start = i
                break
    if start is None:
        print(f"[X] 报告里没有小节“{a.section}”；报告={p.as_posix()}")
        return 1

    body: list[str] = []
    for ln in lines[max(0, start - a.before):]:
        if body and ln.lstrip().startswith("## "):
            break  # 到下一节就停
        body.append(ln)
        if len(body) >= a.max_lines:
            break
    clipped = len(body) >= a.max_lines and (start + len(body)) < len(lines)

    dig = hashlib.sha256(raw).hexdigest()[:16]
    print(f"===== {p.as_posix()}  sha256={dig}  bytes={len(raw)} =====")
    print()
    print("\n".join(body))
    if clipped:
        print(f"...（本小节共 {len(lines) - start} 行，此处只印前 {a.max_lines} 行）")
    print()
    print(f"===== 以上逐字取自 {p.as_posix()}（本脚本只读取，不改写任何一行）=====")
    return 0


if __name__ == "__main__":
    sys.exit(main())