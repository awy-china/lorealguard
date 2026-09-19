"""数据契约 —— 全项目唯一的事实格式（框架中枢）

为什么必须有这一层，而不是让各层各自返回 dict：

1. 赛题要的「可解释性」，最小单位不是热力图，而是**一条能被引用、能被追责、能挂上法条的
   结构化结论**。热力图能说明「哪里怪」，说明不了「为什么这么判、依据哪条规则、适用哪一款」。
2. 我们的差异化是**双向账本**：可疑证据与免责证据**并列**在同一份报告里。
   如果各层自由返回 dict，报告层就无法保证免责栏不被悄悄省掉 ——
   所以把「并列」写进类型，而不是写进文档。
3. 「不确定」必须是合法结论。confidence / state 必须能表达「证据不足 / 不适用」，
   禁止用 score=0 冒充「清白」（P0 已踩过：ELA 对 PNG 不适用，绝不能说「未检出异常」）。

三条硬约束（评审可据此核对实现）：
    ① 进入报告的每条结论都要带 SourceRef（法条 / 平台规则 / 检测器）；无出处不许进报告。
    ② 可疑(Evidence) 与 免责(BenignEvidence) 同为一等公民。
    ③ 法条未核准原文时必须 verified=False —— 报告里显示为「依据待复核」，不许假装确凿。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ==================================================================== 一、出处

LAW_MARK = "《人工智能生成合成内容标识办法》"
LAW_MARK_URL = "https://www.cac.gov.cn/2025-03/14/c_1743654684782215.htm"
LAW_MARK_VERIFIED_AT = "2026-09-17（cac.gov.cn 抓全文，逐条核对）"

#: 已逐条核实原文的条文：key → (条号, 原文摘句)。
#: ⚠️ 只放**抓到全文并逐条核对过**的；把握不大的一律放 UNVERIFIED_LAWS 并标 verified=False。
CLAUSES: dict[str, tuple[str, str]] = {
    "MARK_ART3_DEF": (
        "第三条",
        "人工智能生成合成内容标识包括显式标识和隐式标识。……隐式标识是指采取技术措施在生成合成内容"
        "文件数据中添加的，不易被用户明显感知到的标识。",
    ),
    "MARK_ART4_3": ("第四条（三）", "在图片的适当位置添加显著的提示标识"),
    "MARK_ART5_META": (
        "第五条",
        "服务提供者应当…在生成合成内容的文件元数据中添加隐式标识，隐式标识包含生成合成内容属性信息、"
        "服务提供者名称或者编码、内容编号等制作要素信息。…鼓励服务提供者在生成合成内容中添加数字水印"
        "等形式的隐式标识。",
    ),
    "MARK_ART6_1": (
        "第六条（一）",
        "核验文件元数据中是否含有隐式标识，文件元数据明确标明为生成合成内容的，采取适当方式在发布内容"
        "周边添加显著的提示标识",
    ),
    "MARK_ART6_2": (
        "第六条（二）",
        "文件元数据中未核验到隐式标识，但用户声明为生成合成内容的，…提醒公众该内容可能为生成合成内容",
    ),
    "MARK_ART6_3": (
        "第六条（三）",
        "文件元数据中未核验到隐式标识，用户也未声明，但…检测到显式标识或者其他生成合成痕迹的，"
        "识别为疑似生成合成内容，…提醒公众该内容疑似生成合成内容",
    ),
    "MARK_ART9_NO_VISIBLE": (
        "第九条",
        "用户…可以申请不含显式标识的版本，服务提供者可在通过用户协议明确用户标识义务与使用责任后提供，"
        "但须依法留存提供对象信息等相关日志不少于六个月。",
    ),
    "MARK_ART10_TAMPER": (
        "第十条",
        "任何组织和个人不得恶意删除、篡改、伪造、隐匿本办法规定的生成合成内容标识，"
        "不得为他人实施上述恶意行为提供工具或者服务，不得通过不正当标识手段损害他人合法权益。",
    ),
}

#: 【原文未核实】只知存在性与大致内容 —— 引用时 verified=False，
#: 报告里必须显示「依据待复核」。复核方法见 loreal-research/04-法规与平台规则.md 第二节。
UNVERIFIED_LAWS: dict[str, tuple[str, str]] = {
    "AD_LAW_28": ("《中华人民共和国广告法》", "第 28 条（虚假广告认定）"),
    "COSMETIC_REG": ("《化妆品监督管理条例》", "功效宣称应当有科学依据，不得明示或暗示医疗作用"),
    "ADMIN_INTERNET_AD": (
        "《互联网广告管理办法》",
        "通过知识介绍、体验分享、消费测评等形式推销商品并附购物链接的，应当显著标明「广告」",
    ),
    "ANTI_UNFAIR_2024": ("《网络反不正当竞争暂行规定》", "规制虚假评价、刷单炒信、流量造假"),
    "E_COMMERCE_17": ("《中华人民共和国电子商务法》", "第 17 条（不得虚构交易、编造用户评价）"),
    "PLATFORM_XHS": ("小红书《社区公约》/《社区规范》", "虚假营销、未报备商业推广、AI 生成内容未标注"),
    # ---- F4 文案/功效宣称层的领域依据（全部未核准原文 → 报告自动标「依据待复核」）----
    "COSMETIC_EFFICACY_GUIDE": (
        "《化妆品功效宣称评价规范》（国家药监局）",
        "功效宣称应当有评价依据。本项目**只引用其原则**（宣称需有依据），未核准条文原文；"
        "功效最短可信周期数值为本项目自建的保守下界，**不是规范原文**。",
    ),
    "SKIN_TURNOVER": (
        "皮肤生理学：表皮更替与黑色素代谢周期",
        "常用口径约 28 天。本项目将其作为色素/真皮类改变的**保守下界**（数值待核准到具体文献），"
        "仅用于「关注级」提示，不用于高风险判定。",
    ),
}


@dataclass
class SourceRef:
    """一处出处。报告里每条结论都要挂至少一个。"""

    kind: str                     # law | draft（未核实） | detector | human
    label: str
    locator: str = ""
    quote: str = ""
    verified: bool = True
    url: str = ""

    def display(self) -> str:
        tail = "" if self.verified else "  【依据待复核】"
        loc = f" {self.locator}" if self.locator else ""
        return f"{self.label}{loc}{tail}"

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "locator": self.locator,
                "quote": self.quote, "verified": bool(self.verified), "url": self.url}


def law_ref(key: str) -> SourceRef:
    """已核准条文 → SourceRef。key 必须是 CLAUSES 里逐条核对过的（KeyError 即早暴露错字）。"""
    art, quote = CLAUSES[key]
    return SourceRef("law", LAW_MARK, art, quote, True, LAW_MARK_URL)


def draft_ref(key: str) -> SourceRef:
    """未核准条文 → verified=False，报告里自动显示「依据待复核」。"""
    label, loc = UNVERIFIED_LAWS[key]
    return SourceRef("draft", label, loc, "", False, "")


def detector_ref(name: str, locator: str = "") -> SourceRef:
    return SourceRef("detector", f"检测器 {name}", locator, "", True, "")


def resolve_source(spec: Any) -> SourceRef:
    """YAML 里的 source 字段 → SourceRef。

    写法（保持 rules.yaml 短小，法条原文只在 contract.CLAUSES 存一份，不抄两遍）：
        source: law:MARK_ART6_3        → 已核准条文
        source: draft:COSMETIC_REG     → 未核准条文（自动标"待复核"）
        source: detector:ela           → 检测器自身
    未知写法不抛异常（避免一条 typo 让整条流水线挂掉），而是降级成 verified=False 的原样引用。
    """
    if isinstance(spec, dict):
        spec = next(iter(spec.values()), "")
    s = str(spec or "").strip()
    if s.startswith("law:"):
        return law_ref(s[4:])
    if s.startswith("draft:"):
        return draft_ref(s[6:])
    if s.startswith("detector:"):
        return detector_ref(s[9:])
    return SourceRef("raw", s or "（未标注出处）", "", "", False, "")


# ==================================================================== 二、物证两侧

@dataclass
class Evidence:
    """一条**可疑**证据（双向账本的左半）。"""

    id: str
    layer: str                  # forensics | provenance | semantic | review
    kind: str                   # ela_region | ai_label | stripped_mark | ...
    label: str                  # 人话结论（一句话能被引用）
    score: float = 0.0
    confidence: str = "mid"     # high | mid | low
    bbox: list | None = None    # [x, y, w, h]
    metrics: dict = field(default_factory=dict)   # 答辩会被追问的数字都在这
    detail: str = ""
    sources: list[SourceRef] = field(default_factory=list)
    detector: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "layer": self.layer, "kind": self.kind, "label": self.label,
                "score": round(float(self.score), 4), "confidence": self.confidence,
                "bbox": self.bbox, "metrics": _plain(self.metrics), "detail": self.detail,
                "detector": self.detector, "sources": [s.as_dict() for s in self.sources]}


#: 免责归因的法定/工程口径。code 是机器可读键，报告里必须用同一套措辞（不许各层自己造句）。
BENIGN_CODES: dict[str, str] = {
    "NO_JPEG_HISTORY": "输入无 JPEG 压缩历史（PNG/截图）→ ELA 不适用",
    "PLATFORM_RECODE": "疑似平台二次压缩 → 原始压缩史已被覆盖，ELA 证据力下降",
    "BEAUTY_RETOUCH": "检出大面积偏暗低残差区 → 优先归因美颜磨皮（正常内容，须降权）",
    "GLOBAL_FILTER": "全图残差离散度极低 → 可能整图套了同一滤镜，ELA 敏感性下降",
    "TEXTURE_ARTIFACT": "双质量交叉验证不一致 → 可疑区更可能来自纹理/伪影，而非拼接",
    "PLATFORM_STRIP": "元数据缺失可由平台重编码解释 → **不得**归因「恶意抹除」（第十条要件不成立）",
    "NO_ANOMALY_REGION": "全图未出现任何显著残差异常连通域 → 无坐标级证据，本系统不出结论",
    # ---- 以下 4 条由 F4 文案/语义一致性层产生 ----
    "NO_EFFICACY_CONTEXT": "图像有坐标级证据，但文案未处于功效语境 → 不升级为虚假宣传"
                           "（对比图/拼版记录也可能只是正常修图）",
    "EFFICACY_TIMELINE_PLAUSIBLE": "功效宣称给出了时间承诺，但该承诺在生理保守尺度内可信"
                                   " → 生物学上不构成疑点",
    "STRONG_CLAIM_NO_PERIOD": "有功效宣称但未给出具体周期 → 无可对照的承诺，本系统弃权",
    "NO_TEXT_PROVIDED": "未提供文案 → 图文一致性层**不适用**（不适用 ≠ 内容无异常）",
}


@dataclass
class BenignEvidence:
    """一条**免责 / 良性归因**证据（双向账本的右半）。

    存在的理由：误伤真实创作者的代价远大于漏判。报告只说"哪里怪"，等于把
    "美颜导致的正常低残差"丢给平台去脑补成伪造 —— 所以必须显式写出"为什么不怀疑你"。
    """

    code: str
    label: str = ""
    explains: list = field(default_factory=list)   # 它能解释掉哪些 Evidence.id
    detail: str = ""
    confidence: str = "mid"
    sources: list[SourceRef] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"code": self.code, "label": self.label or BENIGN_CODES.get(self.code, ""),
                "explains": list(self.explains), "detail": self.detail,
                "confidence": self.confidence, "sources": [s.as_dict() for s in self.sources]}


# ==================================================================== 三、标识核验（F2）

class ProvenanceState(str, Enum):
    """文件元数据/隐式标识的核验结果 —— **逐条对齐《标识办法》第十条与第六条**。

    三态与第四态的区别是刻意的：法规只有三态，但工程上「证据不足」必须能表达，
    否则就会把「平台压缩导致元数据消失」误读成「恶意抹除」—— 这正是我们的头号误报源。
    """

    MARKED = "有AI标识"                 # 第六条（一）：元数据明确标明为生成合成内容
    COMPLETE_UNMARKED = "元数据完整但无AI标识"   # 无标识证据，但元数据本身完整（原图直出特征）
    STRIPPED = "元数据被剥离"            # 第十条：「恶意删除、篡改、伪造、隐匿」的候选形态
    UNKNOWN = "证据不足无法判定"          # 弃权。不做真假终审，也不把缺失当罪证。


@dataclass
class ProvenanceResult:
    state: ProvenanceState = ProvenanceState.UNKNOWN
    ai_labels: list = field(default_factory=list)   # [{"field","value","tool"}] 找到的 AI 标识
    signals: list = field(default_factory=list)     # 支撑判定的人话信号（可复现）
    counters: dict = field(default_factory=dict)    # 计数：n_exif / n_png_text / has_c2pa ...
    confidence: str = "low"
    notes: list = field(default_factory=list)       # 主动声明局限
    sources: list[SourceRef] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"state": self.state.value, "ai_labels": _plain(self.ai_labels),
                "signals": list(self.signals), "counters": _plain(self.counters),
                "confidence": self.confidence, "notes": list(self.notes),
                "sources": [s.as_dict() for s in self.sources]}


# ==================================================================== 四、分级判定（F7）

class RiskTier(str, Enum):
    """风险等级。**风险三档 + 一个「未触发」态**，两者不同层次，故意分开：

    提示级 / 关注级 / 高风险 三档**逐条对齐《标识办法》第六条**（官方自己写的分级逻辑），
    比自创一套分级标准更有说服力；而 UNDETERMINED 表示"没有任何规则命中"，
    它**不等于"已确认为真实"** —— 本系统不做真假终审。
    """

    UNDETERMINED = "未触发"
    INFO = "提示级"        # 第六条（一）：已知 AI 内容，加提示即可，不算风险
    WATCH = "关注级"       # 第六条（二）(三)：需提醒公众"可能 / 疑似"
    HIGH = "高风险"        # 第十条等：法定违规形态（如"AI 生成 + 抹标识 + 冒充实拍"）

    @property
    def rank(self) -> int:
        return _TIER_RANK[self]


_TIER_RANK: dict[RiskTier, int] = {
    RiskTier.UNDETERMINED: 0, RiskTier.INFO: 1, RiskTier.WATCH: 2, RiskTier.HIGH: 3,
}


def max_tier(tiers) -> RiskTier:
    """取最高档。空集返回 UNDETERMINED（而不是 INFO —— 别把"没判"说成"提示级"）。"""
    ts = [t for t in tiers if isinstance(t, RiskTier)]
    return max(ts, key=lambda t: t.rank) if ts else RiskTier.UNDETERMINED


@dataclass
class RuleHit:
    """一条规则的命中记录（**分级必须规则驱动，不由模型拍脑袋**）。"""

    rule_id: str
    desc: str
    tier: RiskTier = RiskTier.UNDETERMINED
    source: SourceRef | None = None
    advice_platform: str = ""
    advice_creator: str = ""
    signals_used: dict = field(default_factory=dict)   # 触发时的实测数字（可复算）
    benign: bool = False                               # True = 免责规则命中
    code: str = ""                                     # 免责规则的 BenignEvidence.code

    def as_dict(self) -> dict:
        return {"rule_id": self.rule_id, "desc": self.desc, "tier": self.tier.value,
                "benign": bool(self.benign), "code": self.code,
                "source": self.source.as_dict() if self.source else None,
                "advice_platform": self.advice_platform, "advice_creator": self.advice_creator,
                "signals_used": _plain(self.signals_used)}


@dataclass
class Verdict:
    tier: RiskTier = RiskTier.UNDETERMINED
    headline: str = ""
    confidence: str = "low"
    fired: list = field(default_factory=list)        # list[RuleHit] 风险规则
    benign_hits: list = field(default_factory=list)  # list[RuleHit] 免责规则

    def as_dict(self) -> dict:
        return {"tier": self.tier.value, "headline": self.headline,
                "confidence": self.confidence,
                "fired": [h.as_dict() for h in self.fired],
                "benign_hits": [h.as_dict() for h in self.benign_hits]}


# ==================================================================== 五、输入与报告

@dataclass
class ContentItem:
    """一条待核验的「种草内容」。三个场景共用同一入口：①种草图 ②评论区 ③AI 视觉素材。"""

    image_path: str | None = None
    text: str = ""
    source: str = ""                 # 来源渠道（小红书/抖音/私信…）
    comments: list = field(default_factory=list)   # 场景②：评论列表
    kind: str = "image"              # image | comment_thread | ai_asset
    meta: dict = field(default_factory=dict)


@dataclass
class RiskReport:
    """一份完整报告 = 双向账本 + 分级依据 + 能力边界。"""

    content_id: str
    kind: str = "image"
    generated_at: str = ""
    fingerprint: str = ""             # sha256(文件字节) —— 报告的"物证锚"
    image_path: str = ""
    shape: tuple | None = None
    layers: list = field(default_factory=list)      # 每层运行情况（功能只增不减的可视化）
    evidence: list = field(default_factory=list)    # list[Evidence]
    benign: list = field(default_factory=list)      # list[BenignEvidence]
    provenance: ProvenanceResult | None = None
    detectors: list = field(default_factory=list)   # 原始检测结果（保真，供复算）
    verdict: Verdict = field(default_factory=Verdict)
    evidence_files: list = field(default_factory=list)
    boundaries: list = field(default_factory=list)  # 能力边界：做了什么 / 没做什么
    version: str = ""

    def as_dict(self) -> dict:
        return {
            "content_id": self.content_id, "kind": self.kind, "version": self.version,
            "generated_at": self.generated_at, "fingerprint": self.fingerprint,
            "image_path": self.image_path,
            "shape": list(self.shape) if self.shape else None,
            "layers": _plain(self.layers),
            "evidence": [e.as_dict() for e in self.evidence],
            "benign": [b.as_dict() for b in self.benign],
            "provenance": self.provenance.as_dict() if self.provenance else None,
            "detectors": _plain(self.detectors),
            "verdict": self.verdict.as_dict(),
            "evidence_files": _plain(self.evidence_files),
            "boundaries": list(self.boundaries),
        }


#: 报告尾部固定的能力边界声明。**主动声明局限，是可信度的一部分。**
DEFAULT_BOUNDARIES: list[str] = [
    "本系统不做「真假终审」：输出的是分级风险与物证坐标，不是判决；最终认定权在平台/监管。",
    "本系统不输出「AI 生成 / 非 AI 生成」的二分类概率当作结论。",
    "「未触发规则」≠「已确认为真实」；「检测器不适用」≠「无异常」。",
    "文件元数据核验是不对称使用的：检出 AI 标识 = 强结论；检不出**不能**推断为真实拍摄"
    "（平台二次压缩会剥离元数据）。",
    "所有可疑结论均给出坐标、极性与形态，可回到原图逐点复核。",
]


def _plain(v: Any) -> Any:
    """numpy / Enum / dataclass → 原生 python，便于 json.dumps。"""
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if hasattr(v, "as_dict"):
        return v.as_dict()
    if hasattr(v, "item"):        # numpy 标量
        return v.item()
    if hasattr(v, "tolist"):      # numpy 数组
        return v.tolist()
    return v