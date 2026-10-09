"""闭环演示：**三场景 × 识别 → 判定 → 预警 → 建议 → 报告**（赛题细则②的最小可见单元）。

三场景对应赛题原文的三个点名对象：
    ① 种草文章（图 + 文案）
    ② 评论区（评论线程）
    ③ AI 生成素材（图，且平台可能已抹掉标识）

每条用例都跑完整链条，并打印 Agent 决策卡 —— 决策卡里的每一个数字都来自**本次实跑**，
不是写死的文案。视频/PPT 里的屏幕内容与这里逐字一致。

跑法（单行）：
    env -u PYTHONPATH .venv/Scripts/python.exe experiments/demo_closed_loop.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voiceguard.agent import advise, render_decision             # noqa: E402
from voiceguard.core.contract import ContentItem                 # noqa: E402
from voiceguard.guard import analyze_content                     # noqa: E402

IMG_SPLICE = "samples/fw_splice.jpg"          # 合成样图（程序合成，无个人数据）
IMG_BASE = "samples/base_neutral.jpg"         # 合成中性底图

BOT_THREAD = [
    "姐妹们这个真的好用到爆，我用了一周皮肤绝了，私信我领券",
    "姐妹们这个真的好用到爆，我用了一周皮肤绝了～私信我领券",
    "姐妹们这个真的好用到爆，我用了一周皮肤绝了，私信我领券！",
    "姐妹们这个真的好用到爆，我用了一周皮肤绝了，私信我领券",
    "姐妹们这个真的好用到爆，我用了一周皮肤绝了，私信我领券～",
    "姐妹们这个真的好用到爆，我用了一周皮肤绝了",
    "私信我发你内部渠道价，比专柜便宜一半",
]
BENIGN_THREAD = [
    "30ml 用了两周，混油皮夏天有点闷，但早上上妆不搓泥。",
    "我是干皮，换季用还行，就是香味有点重，个人感受。",
    "回购第二瓶了，配合烟酰胺精华用，效果一般般。",
    "质地挺薄的，空调房里需要叠涂，仅供参考。",
    "用了 5 天没什么感觉，可能我皮太稳了。",
]

CASES = [
    ("① 种图 + 功效文案", dict(image_path=IMG_SPLICE, kind="image",
                          text="7天根除痘印，使用前后对比，链接在评论区")),
    ("② 评论区（批量注水）", dict(comments=BOT_THREAD, kind="comment_thread")),
    ("② 评论区（真实分享）", dict(comments=BENIGN_THREAD, kind="comment_thread")),
    ("② 评论区（仅 2 条 → 弃权）", dict(comments=["好用", "已回购"], kind="comment_thread")),
    ("③ AI 素材（无文案）", dict(image_path=IMG_BASE, kind="ai_asset")),
    ("① 纯文案（周期合理）", dict(text="坚持28天淡化细纹，配合防晒", kind="copy")),
]


def main() -> int:
    print("=" * 104)
    print(f"{'场景':<26}{'全局等级':<10}{'F4':<18}{'F5':<16}命中规则")
    print("=" * 104)
    for i, (name, kw) in enumerate(CASES, 1):
        item = ContentItem(meta={"id": f"loop{i:02d}"}, **kw)
        rep = analyze_content(item, outdir="output/guard", tag=f"loop{i:02d}", sheet=False)
        f4 = next((x for x in rep.layers if x["layer"].startswith("F4")), {})
        f5 = next((x for x in rep.layers if x["layer"].startswith("F5")), {})
        fired = "、".join(h.rule_id for h in rep.verdict.fired) or "—"
        print(f"{name:<26}{rep.verdict.tier.value:<10}"
              f"{f4.get('status', '')[:16]:<18}{f5.get('status', '')[:14]:<16}{fired}")
        benign = "、".join(b.code for b in rep.benign) or "—"
        print(f"{'':<26}免责账本：{benign}")
        print(render_decision(advise(rep)))
        print("-" * 104)
    print("产物：output/guard/loop0{1..6}_report.{md,json}（含「附一、评论区核验」）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())