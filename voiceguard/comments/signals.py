"""F5 评论区真实性层 —— **信号层（机制）**：只算数，不判级。

架构铁律（与 F4 文案侧同源）：
    代码 = 机制（怎么算、缺数据时怎么退让）
    YAML = 阈值与措辞（判多严、写给谁看）
所以本文件里**不出现任何一个判定阈值**，也不出现「是不是水军」这种终审结论。

只用**文本内**可复算的量（零大模型、零账号数据）：
    dup_ratio          组内近重复占比        → 模板化刷评
    lexical_diversity  字符 2-gram 多样性     → 批量同质
    solicit_ratio      明确引流符号占比       → 私信/加微/领券/链接（只收明确商业符号）
    specifics_ratio    实指细节占比           → 真实分享多半有可核对细节
    hype_ratio         情绪/标点堆叠占比      → 情绪注水
    len_cv             长度变异系数           → 批量生成长度整齐
    account_meta_available  **恒为 None**     → 我们拿不到账号注册时长/发帖频率

⚠️ 为什么 `account_meta_available` 永远是 None 而不是 0/False：
    这是本层最大的结构性缺口（账号级数据在平台侧，我们没有）。
    按纪律「缺数据必须返回 None → 规则不命中」+「未达标如实登记」，
    它必须一路传到报告里变成一个**归因码**，而不是被静默当成"账号正常"。
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from ..semantic import textstats

# ---------------------------------------------------------------- 归一化

_PUNCT_EMOJI = re.compile(r"[\W_]+", re.UNICODE)   # Unicode 下 CJK 属 \w，保留汉字/字母/数字


def normalize(text: str) -> str:
    """剥掉空白/标点/emoji，只留汉字·字母·数字。近重复判定必须先归一化。"""
    return _PUNCT_EMOJI.sub("", (text or "").lower())


def _grams(s: str, n: int = 3) -> set[str]:
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------- 词典（只收明确符号）

#: 明确引流符号 —— 评论区的"商业符号"。刻意**不收**「推荐/好用/姐妹冲」这类模糊词：
#: 收进来就把普通用户当水军，和 F4 文案侧的词典纪律同一条红线。
SOLICIT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("私信引流", r"私信我|私我|扣我|扣1|扣①|加我|加微|加V|加vx|vx[:：]|微信号|微信同号|滴滴我|戳我|找我领"),
    ("优惠引流", r"领券|领优惠|优惠券|券后价|下单口令|口令我|返现|返利|内部价|福利价|拼单|代购|刷单|走量|拍下备注"),
    ("橱窗/链接", r"链接在评论区|见评论区|主页链接|小黄车|购物车|橱窗"),
)

#: 实指细节 —— 真实分享的可核对痕迹（数字+单位 / 成分 / 肤质场景 / 具体用法 / 型号）
SPECIFICS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("数字单位", r"\d+\s*(?:天|周|月|年|次|遍|ml|毫升|g|克|%|度|小时|分钟|片|支|瓶|颗)"),
    ("成分", r"烟酰胺|神经酰胺|视黄醇|a醇|维a|维生素c|维c|玻尿酸|透明质酸|水杨酸|果酸|角鲨烷|"
             r"积雪草|胜肽|多肽|氨基酸|甘油|尿囊素|神经酰|泛醇|b5"),
    ("肤质场景", r"油皮|干皮|混油|混干|敏感肌|痘肌|换季|通勤|妆前|卸妆|晒后|空调房|生理期|熬夜后"),
    ("具体用法", r"早晚|睡前|第一步|洗完脸|洁面后|配合|叠加|局部|薄涂|厚敷|点涂|先乳后水"),
    ("型号规格", r"[A-Z]{2,}\d*|\d+\s*号|\d+\s*ml"),
)

#: 情绪注水 —— 夸张词 / 标点堆叠 / 同表情重复
HYPE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("夸张情绪", r"绝绝子|yyds|永远的神|封神|无敌了|爱了爱了|巨好用|好用到哭|天花板|回购一万年|真的会谢"),
    ("标点堆叠", r"[！!？?]{3,}"),
    ("重复字", r"(?:好|赞|香|绝|爱){3,}"),
    ("表情堆叠", r"([\U0001F300-\U0001FAFF\u2600-\u27BF])\1{2,}"),
)

#: 近重复判定阈值（**这是"是不是同一个模板"的工程定义，不是"是不是水军"的判定**，
#:  所以它属于机制、可以留在代码里；而 tier/措辞/误导风险等级一律在 YAML。）
NEAR_DUP_JACCARD = 0.6


@dataclass
class CommentSignals:
    n_comments: int = 0
    dup_ratio: float = 0.0
    lexical_diversity: float = 1.0
    solicit_ratio: float = 0.0
    specifics_ratio: float = 0.0
    hype_ratio: float = 0.0
    len_cv: float | None = None
    account_meta_available: None = None          # 恒 None：平台侧数据不在我们手里
    bot_score: float = 0.0                        # 信号强度上限（**不是判定**，见下）
    hits: dict = field(default_factory=dict)

    def as_signals(self) -> dict:
        """给 guard 主信号表用的 review.* 键（供 F7 报告与外部复核读取）。"""
        return {
            "review.n_comments": self.n_comments,
            "review.dup_ratio": self.dup_ratio,
            "review.lexical_diversity": self.lexical_diversity,
            "review.solicit_ratio": self.solicit_ratio,
            "review.specifics_ratio": self.specifics_ratio,
            "review.hype_ratio": self.hype_ratio,
            "review.len_cv": self.len_cv,
            "review.bot_score": self.bot_score,
        }

    def signals(self) -> dict:
        return {
            "review.n_comments": self.n_comments,
            "review.dup_ratio": self.dup_ratio,
            "review.lexical_diversity": self.lexical_diversity,
            "review.solicit_ratio": self.solicit_ratio,
            "review.specifics_ratio": self.specifics_ratio,
            "review.hype_ratio": self.hype_ratio,
            "review.len_cv": self.len_cv,
            "review.account_meta_available": self.account_meta_available,
            "review.bot_score": self.bot_score,
            "review.hits": self.hits,
        }


def _scan(text: str, pats: tuple[tuple[str, str], ...]) -> list[str]:
    found: list[str] = []
    for label, pat in pats:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            frag = f"{label}：{m.group(0)}"
            if frag not in found:
                found.append(frag)
    return found


def analyze(thread: list[str] | tuple[str, ...], *, extra_solicit: bool = True) -> CommentSignals:
    """算出一条评论线程（一个内容下的全部评论）的信号。

    `extra_solicit=True` 时把 F4 文案侧的『明确商业符号』词典（URL/商品域/购物车…）
    也算进引流，避免两套词典各写一遍、各漏一半。
    """
    items = [t for t in (thread or []) if (t or "").strip()]
    n = len(items)
    if n == 0:
        return CommentSignals()                    # 空线程：所有统计量无从谈起

    norms = [normalize(t) for t in items]

    # ---- 近重复占比
    pairs = dup = 0
    for i in range(n):
        for j in range(i + 1, n):
            pairs += 1
            if _jaccard(_grams(norms[i]), _grams(norms[j])) >= NEAR_DUP_JACCARD:
                dup += 1
    dup_ratio = dup / pairs if pairs else 0.0

    # ---- 字符 2-gram 多样性（组内整体）
    all_grams: list[str] = []
    for s in norms:
        all_grams.extend(s[i:i + 2] for i in range(max(0, len(s) - 1)))
    lexical_diversity = (len(set(all_grams)) / len(all_grams)) if all_grams else 1.0

    # ---- 三类命中占比（命中原文留档，报告可直接引用）
    solicit_hits: list[str] = []
    spec_hits: list[str] = []
    hype_hits: list[str] = []
    n_solicit = n_spec = n_hype = 0
    for t in items:
        pats = SOLICIT_PATTERNS
        s_hit = _scan(t, pats)
        if extra_solicit:
            s_hit += [f"商业符号：{h}" for h in textstats._scan(t, textstats.COMMERCE_PATTERNS)]
        p_hit = _scan(t, SPECIFICS_PATTERNS)
        h_hit = _scan(t, HYPE_PATTERNS)
        if s_hit:
            n_solicit += 1
            solicit_hits.extend(s_hit)
        if p_hit:
            n_spec += 1
            spec_hits.extend(p_hit)
        if h_hit:
            n_hype += 1
            hype_hits.extend(h_hit)

    lengths = [len(x) for x in norms if x]
    if len(lengths) >= 3:
        mu = statistics.mean(lengths)
        len_cv = (statistics.pstdev(lengths) / mu) if mu else None
    else:
        len_cv = None                              # 样本不足 → None（规则不命中）

    sig = CommentSignals(
        n_comments=n,
        dup_ratio=round(dup_ratio, 4),
        lexical_diversity=round(lexical_diversity, 4),
        solicit_ratio=round(n_solicit / n, 4),
        specifics_ratio=round(n_spec / n, 4),
        hype_ratio=round(n_hype / n, 4),
        len_cv=None if len_cv is None else round(len_cv, 4),
        hits={"solicit": solicit_hits[:12], "specifics": spec_hits[:12], "hype": hype_hits[:12]},
    )
    # bot_score = **负向信号**归一化后的强度上限。它不是判定结果：判级永远发生在
    # comment_rules（YAML）里，这里只回答"文本层面的异常强度到哪了"。
    # ⚠️ 刻意**不含** (1 - specifics_ratio)：**"没有实指"本身不是造假证据** ——
    #    一句"好用，回购了"是千万真实用户的正常表达。把它算进强度分，
    #    会让短线程（n=2）直接显示 1.0 的强度，等于用沉默当罪证。
    #    实指只作**免责方向的证据**（见 comment_benign_rules）。
    sig.bot_score = round(max(
        sig.dup_ratio,
        sig.solicit_ratio,
        1.0 - sig.lexical_diversity,
        sig.hype_ratio,
    ), 4)
    return sig