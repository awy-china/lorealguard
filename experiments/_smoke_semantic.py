"""F4 文案/语义层冒烟测试（不进 pytest，跑给人看）。

跑法：  ./.venv/Scripts/python.exe experiments/_smoke_semantic.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voiceguard.guard import analyze_content          # noqa: E402
from voiceguard.core.contract import ContentItem      # noqa: E402
from voiceguard.semantic import claims, textstats     # noqa: E402

TEXTS = [
    ("7天根除痘印", "强宣称 + 短周期 → 应判不可行"),
    ("28天淡化细纹", "强宣称 + 28天 → 应可行"),
    ("即刻补水，上脸就水润", "保湿类下界为 0 → 永不判不可行"),
    ("坚持使用，感觉皮肤变好了", "弱宣称 + 无周期 → 弃权"),
]

for t, why in TEXTS:
    a = claims.analyze(t)
    print(f"\n【{t}】  ({why})")
    print(f"  宣称={[c.name for c in a.claims]}  强度={a.strength}  周期={[p.surface for p in a.periods]}"
          f"  最短={a.signals()['text.promised_period_days']}")
    print(f"  不可行={a.signals()['text.efficacy_implausible']}  可行={a.feasible}")
    for n in a.notes:
        print(f"  · {n}")
    for b in a.implausible:
        print(f"  ✗ {b['claim']}：承诺 {b['promised_days']} 天 < 保守下界 {b['min_plausible_days']} 天"
              f"（{b['basis']}）")

print("\n" + "=" * 70)
demo = "7天根除痘印！素颜也能打，链接在评论区 #广告"
s = textstats.analyze(demo)
print(f"【{demo}】")
for k, v in s.hits.items():
    print(f"  {k}: {v}")

print("\n" + "=" * 70)
it = ContentItem(text="7天根除痘印，我用完就消失了", kind="copy", meta={"id": "demo-copy-01"})
rep = analyze_content(it, outdir="output/guard", tag="demo_copy", sheet=False)
print(f"判定：{rep.verdict.tier.value}｜命中：{'、'.join(h.rule_id for h in rep.verdict.fired) or '—'}")
print(f"证据：{'；'.join(e.label for e in rep.evidence) or '—'}")
print(f"报告：output/guard/demo_copy_report.md（双向账本全文）")