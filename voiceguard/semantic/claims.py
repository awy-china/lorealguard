"""F4-A 文案功效宣称层 —— 生物科学轴的落点。

为什么这一层是这个项目里最不可替代的部分（答辩第一句话就说这个）：
    图像取证只能回答「这段像素被动过没有」。但美妆内容最典型的造假方式是
    **图是真的、话是假的**：两张真实拍摄的照片，配一句「7 天根除痘印」。
    任何 ELA / 噪声指纹 / C2PA 核验都看不见这种造假 —— 它的载体不是像素，
    是**承诺**。要判它，必须把承诺与**生理时间尺度**对照。这就是生物科学这一轴。

本层三件事，全部零大模型、可复算（同一份文本每次得同一结果）：
    ① 抽取功效宣称并分级（弱 / 中 / 强 / 绝对）
    ② 抽取时间承诺（7天 / 一周 / 28天 / 即刻）
    ③ 时间承诺 × 生理最短可信周期 → 可行性

⚠️ 三条纪律（与法条同一套，答辩会被追问）：
    ① 生理周期数值当前**全部为 draft（未核准到官方或文献原文）**：报告会自动显示
       「依据待复核」。**不许把技术判断伪装成规范判断。**
    ② 误报护栏优先：每类取**保守下界**；凡生理上可即时成立的类别（保湿/清洁）下界
       一律 0，**永不判不可行** —— 宁可漏判，不可冤枉。
    ③ 本层只产出信号，最终等级由 rules.yaml 决定：**代码里零阈值**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ============================================================ 一、领域数据（全部 draft）

#: 功效宣称词典：类目 code → (中文名, 关键词元组, 生理最短可信周期[天], 依据说明)
#: 下界取法：以**表皮更替周期（约 28 天）**为色素/真皮类改变的保守下界；炎症类以数日计；
#: 物理/保湿类为 0（即时成立）。⚠️ 数值未核准 → 见 contract.UNVERIFIED_LAWS。
CLAIM_LEXICON: dict[str, tuple[str, tuple[str, ...], int, str]] = {
    "MOISTURIZE":   ("保湿补水", ("保湿", "补水", "水润", "锁水", "滋润"), 0,
                     "角质层含水量可即时提升 → 即刻诉求生理上成立，永不判不可行"),
    "CLEANSE":      ("清洁卸妆", ("清洁", "卸妆", "洁面", "洗掉", "去除角质"), 0,
                     "物理/表面活性过程，即时成立"),
    "SOOTHE":       ("舒缓镇静", ("舒缓", "镇静", "退红", "消红", "抗敏", "止痒"), 0,
                     "主观舒缓可即时发生（保守取 0）"),
    "ANTI_ACNE":    ("祛痘控油", ("祛痘", "抗痘", "控油", "去痘", "痘痘"), 3,
                     "炎症性痤疮可见改善以周计；3 天为保守下界"),
    "ACNE_MARK":    ("痘印淡化", ("痘印", "痘痕", "痘坑"), 28,
                     "色素性痘印依赖表皮更替周期"),
    "WHITEN":       ("美白淡斑", ("美白", "淡斑", "祛斑", "色斑", "斑点", "雀斑", "晒斑", "色沉",
                                 "提亮", "白皙", "去黄", "匀肤"), 28,
                     "黑色素代谢依赖表皮更替周期（约 28 天）"),
    "ANTI_WRINKLE": ("抗皱淡纹", ("抗皱", "淡纹", "细纹", "皱纹", "去纹", "平纹"), 28,
                     "依赖真皮重塑，通常以月计；28 天为保守下界"),
    "FIRM":         ("紧致提拉", ("紧致", "提拉", "弹力", "弹性", "抗松弛", "下垂"), 28,
                     "真皮层改变，同抗皱"),
    "REPAIR":       ("屏障修护", ("修护", "修复", "屏障", "角质层修复"), 7,
                     "屏障指标恢复通常需数日到两周；7 天为保守下界"),
    "ANTI_AGING":   ("抗老抗衰", ("抗老", "抗衰", "抗氧化", "抗初老"), 28,
                     "结构性改变，同抗皱"),
}

#: 宣称强度词典：等级 → 关键词。等级越高，绝对化程度越高。
#: 分级只用于「能不能与生理尺度对照」——弱宣称（有助于/辅助）不做时间对照，避免误伤。
STRENGTH_LEXICON: dict[str, tuple[str, ...]] = {
    "绝对": ("根除", "根治", "彻底祛除", "彻底去除", "永久", "一劳永逸", "100%", "百分之百",
             "绝对", "永不", "消灭", "包治", "秒变", "换脸"),
    "强":   ("祛除", "去除", "消除", "褪去", "消失", "平复", "祛斑", "祛痘", "抗皱",
             "紧致", "提拉", "美白", "淡斑", "逆转", "淡化"),
    "中":   ("改善", "提亮", "修护", "修复", "减少", "缓解", "减轻", "提升", "紧实"),
    "弱":   ("滋润", "补水", "清洁", "舒缓", "辅助", "有助于", "帮助", "呵护", "保湿"),
}
_STRENGTH_RANK = {"弱": 0, "中": 1, "强": 2, "绝对": 3}

#: 时间承诺：单位 → 天（小时/分钟折算）
_UNIT_DAYS: dict[str, float] = {
    "天": 1, "日": 1, "周": 7, "星期": 7, "礼拜": 7, "个月": 30, "月": 30, "年": 365,
    "小时": 1 / 24, "分钟": 1 / 1440,
}
_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
              "七": 7, "八": 8, "九": 9, "十": 10}
#: 无需数字即表示"立即"的词 → 承诺周期 0 天
_IMMEDIATE = ("即刻", "立即", "马上", "立刻", "瞬间", "一用就", "一秒", "秒")

_NUM = r"(?:\d{1,3}|[一二两三四五六七八九十]{1,3})"
_PERIOD_RE = re.compile(
    r"(?:第\s*" + _NUM + r"\s*(?:天|日))"                 # 第7天（打卡体）
    r"|(?:" + _NUM + r"\s*(?:个月|星期|礼拜|小时|分钟|天|日|周|月|年))"  # 7天 / 一周 / 28天
)


# ============================================================ 二、抽取

@dataclass
class ClaimHit:
    code: str
    name: str
    surface: str
    span: tuple[int, int]

    def as_dict(self) -> dict:
        return {"code": self.code, "name": self.name,
                "surface": self.surface, "span": list(self.span)}


@dataclass
class PeriodHit:
    days: int
    surface: str
    span: tuple[int, int]

    def as_dict(self) -> dict:
        return {"days": self.days, "surface": self.surface, "span": list(self.span)}


@dataclass
class ClaimAnalysis:
    """文案功效分析结果。signals() 出的键名必须与 rules.yaml 里写的完全一致。"""

    claims: list = field(default_factory=list)
    periods: list = field(default_factory=list)
    strength: str = "无"
    strength_words: list = field(default_factory=list)
    implausible: list = field(default_factory=list)   # [{claim, constant_days, promised_days}]
    feasible: bool | None = None                      # True 可行 / False 不可行 / None 弃权
    notes: list = field(default_factory=list)

    def signals(self) -> dict:
        # 功效语境 = 抽到**类别词** 或 出现**强/绝对强度词**（兜底）。
        # 兜底是修真实缺陷修出来的：曾漏收「色斑」这个类别词，于是
        # 「彻底祛除色斑」被判成「无功效宣称」→ 图文交叉与弃权声明两条通路同时关掉（漏判）。
        # 强度词兜底让整句不再因词典收录不全而"失明"。
        return {
            "text.efficacy_claim": (bool(self.claims)
                                    or _STRENGTH_RANK.get(self.strength, -1) >= 2),
            "text.efficacy_categories": [c.code for c in self.claims],
            "text.efficacy_strength": self.strength,
            "text.efficacy_strength_rank": _STRENGTH_RANK.get(self.strength, -1),
            "text.claim_surfaces": [c.surface for c in self.claims],
            "text.n_periods": len(self.periods),
            "text.promised_period_days": (min(p.days for p in self.periods)
                                          if self.periods else None),
            "text.period_surfaces": [p.surface for p in self.periods],
            "text.efficacy_implausible": bool(self.implausible),
            "text.efficacy_feasible": self.feasible,
        }


def _cn_or_arabic(tok: str) -> int:
    tok = tok.strip()
    if tok.isdigit():
        return int(tok)
    # 中文数字：支持 十 / 十三 / 二十 / 二十八 这类常见写法
    if "十" in tok:
        parts = tok.split("十")
        tens = _CN_DIGITS.get(parts[0], 1) if parts[0] else 1
        ones = _CN_DIGITS.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(tok, 0)


def extract_claims(text: str) -> list[ClaimHit]:
    """抽出功效宣称。同一类目只记首次出现（避免"保湿"三遍算三条）。"""
    out: list[ClaimHit] = []
    seen: set[str] = set()
    for code, (_name, words, _days, _basis) in CLAIM_LEXICON.items():
        for w in words:
            i = text.find(w)
            if i >= 0:
                if code not in seen:
                    seen.add(code)
                    out.append(ClaimHit(code, _name, w, (i, i + len(w))))
                break
    out.sort(key=lambda c: c.span[0])
    return out


def extract_periods(text: str) -> list[PeriodHit]:
    """抽出时间承诺。含两种形态：①数字+单位（7天/一周/28天）②即刻类词（承诺 0 天）。"""
    out: list[PeriodHit] = []
    for m in _PERIOD_RE.finditer(text):
        s = m.group(0)
        if s.startswith("第"):
            num = re.search(_NUM, s)
            days = _cn_or_arabic(num.group(0)) if num else 0
        else:
            unit = next((u for u in sorted(_UNIT_DAYS, key=len, reverse=True) if s.endswith(u)), "")
            num = re.sub(r"[^0-9一二两三四五六七八九十]", "", s[:len(s) - len(unit)]) if unit else ""
            days = round(_cn_or_arabic(num) * _UNIT_DAYS[unit]) if unit and num else 0
        out.append(PeriodHit(int(days), s, (m.start(), m.end())))
    for w in _IMMEDIATE:
        i = text.find(w)
        if i >= 0:
            out.append(PeriodHit(0, w, (i, i + len(w))))
    out.sort(key=lambda p: p.span[0])
    return out


def detect_strength(text: str) -> tuple[str, list[str]]:
    """宣称强度 = 文本里出现的最高等级词（绝对 > 强 > 中 > 弱）。"""
    best, words = "无", []
    for level in ("绝对", "强", "中", "弱"):
        hit = [w for w in STRENGTH_LEXICON[level] if w in text]
        if hit and best == "无":
            best, words = level, hit
    return best, words


def assess_feasibility(claims: list[ClaimHit], periods: list[PeriodHit],
                       strength: str) -> tuple[list[dict], bool | None, list[str]]:
    """时间承诺 × 生理最短可信周期 → 可行性。

    三道保守闸门（缺一即**弃权**，绝不硬判）：
        ① 该类别下界为 0（生理上可即时成立）→ 跳过，永不判不可行；
        ② 宣称强度低于「强」→ 跳过（"7 天改善"这类表述不做时间对照，避免误伤）；
        ③ 没给出任何时间承诺 → 跳过（没有可对照的承诺 = 证据不足）。
    只有三道闸门都过了、且承诺周期 < 生理下界，才判不可行。
    """
    notes: list[str] = []
    if not claims:
        return [], None, ["文案未出现功效宣称 → 本层不适用"]
    if not periods:
        return [], None, ["文案有功效宣称但未给出具体时间承诺 → 无可对照周期，弃权"]
    if _STRENGTH_RANK.get(strength, -1) < _STRENGTH_RANK["强"]:
        return [], None, [f"宣称强度为「{strength}」（低于『强』）→ 不做时间对照，弃权"]

    promised = min(p.days for p in periods)
    bad: list[dict] = []
    for c in claims:
        _n, _w, min_days, basis = CLAIM_LEXICON[c.code]
        if min_days <= 0:
            notes.append(f"「{c.name}」生理上可即时成立 → 永不判不可行")
            continue
        if promised < min_days:
            bad.append({"claim": c.name, "code": c.code, "promised_days": promised,
                        "min_plausible_days": min_days, "basis": basis})
    if bad:
        notes.append(
            f"最短承诺 {promised} 天 < 相关类别保守下界（"
            + "、".join(f"{b['claim']} {b['min_plausible_days']} 天" for b in bad)
            + "）→ 判「生理不可行」；仅提示复核，不作处罚依据")
        return bad, False, notes
    notes.append(f"最短承诺 {promised} 天 ≥ 相关类别保守下界 → 生理上不构成疑点（判「可行」，不触发提示）")
    return [], True, notes


def analyze(text: str) -> ClaimAnalysis:
    """一行调用：analyze('7天根除痘印，素颜也能打')"""
    text = text or ""
    claims = extract_claims(text)
    periods = extract_periods(text)
    strength, words = detect_strength(text)
    implausible, feasible, notes = assess_feasibility(claims, periods, strength)
    return ClaimAnalysis(claims=claims, periods=periods, strength=strength,
                         strength_words=words, implausible=implausible,
                         feasible=feasible, notes=notes)