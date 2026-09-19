"""F4 闭环演示：同图不同文案 → 判定如何变化（这是细则②「从识别到决策」的最小可见单元）。

跑法：  ./.venv/Scripts/python.exe experiments/demo_closed_loop.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voiceguard.core.contract import ContentItem              # noqa: E402
from voiceguard.guard import analyze_content                  # noqa: E402

IMG = "samples/fw_splice.jpg"

CASES = [
    ("A 只给图（不提供文案）", dict(image_path=IMG)),
    ("B 图 + 「九宫格记录」文案", dict(image_path=IMG, text="今天去公园拍的花，拼了个九宫格记录一下")),
    ("C 图 + 「功效对比」文案", dict(image_path=IMG, text="7天根除痘印，使用前后对比，链接在评论区")),
    ("D 纯文案（无图）", dict(text="7天根除痘印，素颜也能打", kind="copy")),
    ("E 纯文案（周期合理）", dict(text="坚持28天淡化细纹，配合防晒", kind="copy")),
]

print("=" * 96)
print(f"{'用例':<26}{'等级':<8}{'F4 状态':<14}命中规则 / 免责归因")
print("=" * 96)
for i, (name, kw) in enumerate(CASES, 1):
    item = ContentItem(meta={"id": f"loop{i:02d}"}, **kw)
    rep = analyze_content(item, outdir="output/guard", tag=f"loop{i:02d}", sheet=False)
    f4 = next((l for l in rep.layers if l["layer"].startswith("F4")), {})
    fired = "、".join(h.rule_id for h in rep.verdict.fired) or "—"
    benign = "、".join(b.code for b in rep.benign) or "—"
    print(f"{name:<26}{rep.verdict.tier.value:<8}{f4.get('status',''):<14}{fired}")
    print(f"{'':<26}{'':<8}{'':<14}↳ 免责：{benign}")
    if rep.evidence:
        for e in rep.evidence:
            print(f"{'':<26}{'':<8}{'':<14}↳ {e.id} {e.label[:56]}")
    print("-" * 96)
print("产物： output/guard/loop0{1..5}_report.{md,json}")