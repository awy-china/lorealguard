"""F4-B 文案侧统计信号 —— 商业语境判定（零大模型，全部可复算）。

只做三件事，每件都**摘出命中原文**（可解释性的最小单位是"引用原句"）：
    ① 是否含购物链接/购买引导 → text.has_shopping_link
    ② 是否声明了广告属性   → text.ad_label_declared
    ③ 是否处于「前后对比」语境 → text.before_after_context

为什么必须保守（这是误报护栏的一部分）：
    「广告未标注」是**关注级**结论，一旦词典太宽（例如把"好用"也算推广），
    就会把普通用户的真实分享误伤成软广。所以词典只收**明确的商业符号**：
    URL、平台商品域、明确的购买引导语，以及明确的广告声明词。
    任何模糊词（安利/推荐/种草/好用）**一律不收** —— 收进来就是把普通用户当广告。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 明确的商业符号。⚠️ 刻意不收「安利/推荐/种草/实测」这类模糊词。
COMMERCE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("URL", r"https?://[^\s，。；、）)]+"),
    ("淘宝短链", r"\btb\.cn/"),
    ("淘宝商品域", r"item\.taobao\.com|detail\.tmall\.com"),
    ("京东商品域", r"item\.jd\.com|mall\.jd\.com"),
    ("小红书链接", r"xhs\.link|xiaohongshu\.com/goods"),
    ("抖音商城", r"抖音商城|douyin\.com/goods"),
    ("购买引导", r"点击购买|点击下单|购买链接|下单链接|链接在评论区|见评论[区链]?|"
                 r"购物车|小黄车|橱窗|拍一发"),
)

#: 广告声明词（出现即认为已声明）
AD_LABEL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("广告", r"#?\s*广告(?![语词])"),
    ("赞助", r"赞助|品牌方赞助|品牌提供"),
    ("合作", r"品牌合作|合作推广|推广合作|商单|商务合作|报备"),
    ("英文标签", r"(?<![a-zA-Z])#?ad(?![a-zA-Z])|sponsored"),
)

#: 前后对比语境（用于判断图像差异是否处于功效对比场景）
BEFORE_AFTER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("对比", r"对比图?|对照组?|左右对比|同机位"),
    ("前后", r"使用前|使用后|用前|用后|前后|before|after|前\s*/\s*后"),
    ("周期打卡", r"第\s*\d+\s*天|打卡|连续\s*\d+\s*(?:天|周)|坚持了"),
    ("亲测", r"亲测|实测|自测|素颜出镜"),
)


@dataclass
class TextStats:
    has_shopping_link: bool = False
    ad_label_declared: bool = False
    before_after_context: bool = False
    hits: dict = field(default_factory=dict)      # 信号名 → 命中原文列表（可被报告引用）

    def signals(self) -> dict:
        return {
            "text.has_shopping_link": self.has_shopping_link,
            "text.ad_label_declared": self.ad_label_declared,
            "text.before_after_context": self.before_after_context,
            "text.commerce_hits": self.hits.get("commerce", []),
            "text.ad_label_hits": self.hits.get("ad", []),
            "text.before_after_hits": self.hits.get("before_after", []),
        }


def _scan(text: str, pats: tuple[tuple[str, str], ...]) -> list[str]:
    """返回命中原文（去重、保序）。报告里直接引用这些片段 = 可解释。"""
    found: list[str] = []
    for label, pat in pats:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            frag = f"{label}：{m.group(0)}"
            if frag not in found:
                found.append(frag)
    return found


def analyze(text: str) -> TextStats:
    text = text or ""
    commerce = _scan(text, COMMERCE_PATTERNS)
    ad = _scan(text, AD_LABEL_PATTERNS)
    ba = _scan(text, BEFORE_AFTER_PATTERNS)
    return TextStats(
        has_shopping_link=bool(commerce),
        ad_label_declared=bool(ad),
        before_after_context=bool(ba),
        hits={"commerce": commerce, "ad": ad, "before_after": ba},
    )