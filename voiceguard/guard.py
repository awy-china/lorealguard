"""F0 统一入口：把 F1 → F2 → F6/F7 → F8 串成一条**可复算**的链路。

三条设计约束（答辩会被追问，先说清）：

    ① **大模型不碰「是不是假的」**。本链路默认不调用任何模型，纯算法可复算；
       VL 只在 F3 作为「语义解释员」可选接入（semantic/vl.py），且永远不参与判定。
    ② 跑一次落盘三件套：证据图 / 报告 md / 报告 json，报告里带 sha256 指纹 ——
       任何人拿同一份输入都能复算出同一个指纹，这是"可复现"的最低要求。
    ③ 「功能只增不减」在代码里的形态是 `report.layers`：每层显式登记
       跑了没、跑到什么程度、哪些是待接（PLANNED）。**没接的层写 PLANNED，不假装跑过。**
       未接入层的信号在 signals 里显式登记为 None，而不是干脆不写 ——
       这样规则引擎能区分「缺信号」和「信号为 0」（缺信号不命中任何规则）。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from . import config
from .agent import planner as agent_planner
from .comments import evaluate_thread
from .core.contract import (
    DEFAULT_BOUNDARIES,
    BenignEvidence,
    ContentItem,
    Evidence,
    ProvenanceState,
    RiskReport,
    RiskTier,
    Verdict,
    detector_ref,
    law_ref,
    max_tier,
)
from .forensics import registry
from .forensics.base import load_and_prepare, make_evidence_sheet
from .provenance.metadata import finalize_state, read_provenance
from .rules.engine import describe, evaluate, load_rules
from .report import ledger
from .semantic import consistency as semantic_layer
from .semantic import explain as semantic_explain

VERSION = "0.3.0+f4"

#: 尚未接入的信号 + 它归哪一层负责。写在这里而不是藏在注释里：
#: 报告要如实说出"我们还没做这一块"，这比含糊带过更能建立可信度。
PLANNED_SIGNALS: dict[str, str] = {
    "forensics.ai_trace_score": "像素级生成痕迹检测器（P1 待接）",
    # 2026-09-19 变更：F5 评论区层已接入（voiceguard/comments/）→ review.* 由该层实际产出，
        # 不再是 PLANNED。2026-09-20 变更：F3 解释层已接入（semantic/explain.py，默认关闭、需 --explain），
        # 但它产出的是**自由文本解释**（report.explanation），不产出结构化信号
        # → 下面这条 signal 依旧没有产出者，如实留在这里。
        "scene.before_after": "场景结构化信号（F3 现只产自由文本解释，结构化 scene.* 尚未产出）",
}
#: 2026-09-19 变更：F4 文案/语义一致性层已接入 → text.* 系列信号不再是 PLANNED，
#: 由 semantic/consistency.py 实际产出（claims.py 抽功效宣称与时间承诺，
#: textstats.py 抽商业语境，两者交叉后给出 semantic.consistency_state）。

#: 免责归因 ↗ 证据 的对应关系：右半栏的每条归因要说清"它解释掉了左半栏的哪几条"。
#: 这个链接必须**看得见** —— 只说"哪里怪"、不说"为什么不怀疑你"，就是把判断权推给平台脑补。
_EXPLAINERS: dict[str, tuple] = {
    "BEAUTY_RETOUCH": ("darker",),          # 偏暗低残差区
    "TEXTURE_ARTIFACT": ("dual_q_false",),   # 双质量不一致的区域
    # 平台二次压缩会把整张图的压缩史覆盖掉，ELA 的区域结论整体失去证据力
    # （实测 b03：重压缩图上产出 z_peak=32.8 的"区域"，其实是重压缩伪影）
    "PLATFORM_RECODE": ("all_regions",),
    # F4 接入后新增：图像有坐标级证据、但文案不在功效语境 → 不升级（对比/拼版常属正常创作）
    "NO_EFFICACY_CONTEXT": ("brighter",),
    # 以下三条解释的是"我们为什么**没**升级"，因此不指向左半栏的任何一条证据
    "EFFICACY_TIMELINE_PLAUSIBLE": (),
    "STRONG_CLAIM_NO_PERIOD": (),
    "NO_TEXT_PROVIDED": (),
}


def detector_reliable(res) -> bool:
    """这次输入上，该检测器的结论能不能拿来**升级判定**？—— 提升为一等信号。

    实测教训（P2 v0，样本 b03）：平台二次压缩（短边 1080 + EXIF 被剥离）的正常图上，
    ELA 产出 4 个 z_peak 高达 32.8 的"可疑区域"，那是重压缩伪影、不是编辑痕迹。
    检测器自己已经把 score 按 0.3 降权，但**区域列表没有降权**；而规则一旦改用
    坐标级证据触发（正是为了修另一个 bug 而改的），那道降权就被绕过去了。

    所以：可靠性不能只体现在 score 上，必须作为独立信号暴露，由规则显式引用。
    「同一个降权机制必须在所有出口生效」—— 这是这次踩坑换来的纪律。

    按**检测器名**分派（2026-09-25，预注册 v2 §3）：`ela` 走下面两条 JPEG 史判据（逐字节未动）；
    其余检测器的可靠性由**它自己在 `raw["reliable"]` 里自报**（缺省 False＝不认账）。
    为什么不是给 ELA 的判据套到所有检测器上：`jpeg_history` / `platform_recompressed` 是
    JPEG 取证的概念，光影 / 色彩过渡这两个维度根本没有对应物 —— 套过去等于用 ELA 的
    适用性去替别的维度背书。
    """
    raw = res.raw or {}
    if getattr(res, "name", "") != "ela":
        # 观察栏（B 档）检测器不参与升级判定（规则表里没有它们），这里只做**如实回答**。
        return bool(raw.get("reliable", False))
    if raw.get("jpeg_history") is False:          # 无 JPEG 史 → ELA 原理上不适用
        return False
    if raw.get("platform_recompressed") is True:  # 压缩史被平台覆盖 → 证据力弱
        return False
    return True


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _first_image(files: list) -> str | None:
    """从产物清单里挑出第一张图（证据图优先）—— F3 解释要送出去的那张。"""
    for f in files:
        if isinstance(f, str) and f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            return f
    return None


# ---------------------------------------------------------------- 信号构造

def _ran(results: list) -> list:
    """真正跑成了的检测器（status=ok）。

    「不适用」的检测器不许影响任何判定 —— 既不许把 confidence 拉成 low，也不许在信号表里
    留一个 score=0 的键让人误读成"没异常"。三态（ok / 不适用+原因 / 无）里，这一层只放 ok。
    """
    return [r for r in results if getattr(r, "status", "ok") == "ok"]


def _observe_names() -> set:
    """观察级检测器（注册时 `default=False`）—— 只写附四观察栏，不进左栏、不进规则表。"""
    return set(registry.names()) - set(registry.default_names())


def _judging(results: list) -> list:
    """"参与判定的"检测器 —— 观察级不算。

    观察级**不许影响判定**（预注册 v2 §3：不进左栏物证、不进 tier）。confidence 也在判定面里
    （`Verdict` 全字段 ⇒ 它的 sha256 是冻结样张的一部分），所以它同样**不许被观察级碰到** ——
    否则"开了观察栏，判定逐字节相同"就是空话。这条是 `_ran()` 上面那句原则的具体化，
    不是新增口径：`_ran()` 只挡「不适用」，这里再挡「不参与判定」。
    """
    obs = _observe_names()
    return [r for r in _ran(results) if getattr(r, "name", None) not in obs]


def _extra_probes(trace: dict) -> int:
    """F9 这一轮**真的**多跑出来的取证项数（检测器 + 放大切片），被拦下的不算。

    口径写死在这里是为了不让 `_layers()` 里那句"追加取证 N 项"变成形容词 ——
    数字必须由 steps 数出来，不能靠手写。
    """
    return sum(1 for s in (trace.get("steps") or [])
               if s.get("action") in ("run_detector", "crop_zoom") and not s.get("blocked_reason"))


def build_signals(results: list, meta: dict, prov=None) -> dict:
    """各层输出 → 扁平信号表（规则表靠它判定）。

    约定：键名 `层.检测器.字段`；未接入的信号显式登记为 None。
    阈值不在这里出现 —— 阈值属于规则表（rules.yaml）。
    """
    s: dict = {}
    for r in _ran(results):
        p = f"forensics.{r.name}"
        s[f"{p}.score"] = float(r.score)
        s[f"{p}.confidence"] = r.confidence
        for k, v in (r.raw or {}).items():
            if isinstance(v, (int, float, bool, str, list, type(None))):
                s[f"{p}.{k}"] = v
        # 区域统计：偏亮 / 偏暗分开计，因为两者的处置完全相反
        bright = sum(float(g.get("area_ratio") or 0) for g in r.regions
                     if g.get("polarity") == "brighter")
        dark = sum(float(g.get("area_ratio") or 0) for g in r.regions
                   if g.get("polarity") == "darker")
        s[f"{p}.n_regions"] = len(r.regions)
        s[f"{p}.n_brighter_regions"] = sum(1 for g in r.regions if g.get("polarity") == "brighter")
        s[f"{p}.n_darker_regions"] = sum(1 for g in r.regions if g.get("polarity") == "darker")
        s[f"{p}.brighter_area_ratio"] = round(bright, 6)
        s[f"{p}.darker_area_ratio"] = round(dark, 6)
        # 坐标级证据本身（触发条件用这个，而不是面积加权分）：
        # 面积加权分把"区域多大"混进了"有多可疑"，会让小块高危拼接被稀释掉（实测踩过）。
        zs = [float(g.get("z_peak") or 0) for g in r.regions if g.get("polarity") == "brighter"]
        zd = [float(g.get("z_peak") or 0) for g in r.regions if g.get("polarity") == "darker"]
        s[f"{p}.brighter_z_peak_max"] = round(max(zs), 2) if zs else 0.0
        s[f"{p}.darker_z_peak_max"] = round(abs(min(zd)), 2) if zd else 0.0
        # 可靠性是一等信号：规则必须显式声明"我信这个检测器这次的结论"
        s[f"{p}.reliable"] = detector_reliable(r)

    # 派生：拼接综合分（当前= ELA 分；P1 多检测器融合后在此处替换，规则表不用改）
    s["forensics.splice_score"] = s.get("forensics.ela.score")
    s["forensics.splice_score_note"] = "当前由 ELA 单检测器给出（P1 融合后替换）"

    for k, owner in PLANNED_SIGNALS.items():
        s.setdefault(k, None)
        s[f"_planned.{k}"] = owner

    if prov is not None:
        s["provenance.state"] = prov.state.value
        s["provenance.ai_label_found"] = bool(prov.ai_labels)
        s["provenance.has_c2pa"] = bool((prov.counters or {}).get("has_c2pa_container"))
        s["provenance.strip_candidate"] = bool(any("元数据容器为空" in x for x in prov.signals))
        s["provenance.confidence"] = prov.confidence
    return s


# ---------------------------------------------------------------- 证据构造

def build_evidence(results: list, prov=None) -> list:
    """检测器输出 → 统一 Evidence（左半栏）。带坐标、极性、形态、数字。

    ⚠️ 左栏**只收 ELA**：观察栏（B 档）检测器有坐标、有数字，但**不进物证**（预注册 v2 §3）。
    下面那行 `continue` 就是这条边界的全部实现 —— **删掉它，B 档立刻变成左栏物证**（可自证伪）。
    """
    out: list = []
    n = 0
    for r in results:
        if r.name not in ("ela",):
            continue
        for g in r.regions:
            n += 1
            dark = g.get("polarity") == "darker"
            out.append(Evidence(
                id=f"E{n}", layer="forensics", kind=f"{r.name}_region",
                label=("检出残差显著高于全图中位的区域（偏亮）→ 符合局部拼接/亮度调整特征"
                       if not dark else
                       "检出大片低残差区（偏暗）→ 优先归因美颜磨皮，已按正常内容降权"),
                score=float(r.score), confidence=r.confidence, bbox=g.get("bbox"),
                metrics={k: g.get(k) for k in ("z_peak", "z_mean", "n_blocks", "area_ratio",
                                               "dual_q", "polarity")},
                detail=f"形态：{g.get('shape', '')}",
                detector=r.name,
                sources=[detector_ref(r.name, "voiceguard.forensics.ela")],
            ))
    if prov is not None and prov.state is ProvenanceState.STRIPPED:
        n += 1
        out.append(Evidence(
            id=f"E{n}", layer="provenance", kind="stripped_mark",
            label="元数据容器为空，且像素层证明该内容本为生成合成内容 → 存在『标识被剥离』形态",
            score=1.0, confidence=prov.confidence,
            metrics={k: v for k, v in (prov.counters or {}).items()
                     if k in ("n_exif_fields", "n_png_text", "has_xmp")},
            detail="第十条要件：①它本带标识 ②标识被抹掉 —— 两者同时成立才判本项。",
            sources=[law_ref("MARK_ART10_TAMPER")],
        ))
    return out


def build_benign(benign_hits: list, evidence: list, prov=None) -> list:
    """免责规则命中 → BenignEvidence（右半栏），并把「它解释了哪几条证据」连起来。"""
    out: list = []
    for h in benign_hits:
        explains: list = []
        for key in _EXPLAINERS.get(h.code, ()):
            if key == "darker":
                explains += [e.id for e in evidence if e.metrics.get("polarity") == "darker"]
            elif key == "dual_q_false":
                explains += [e.id for e in evidence if e.metrics.get("dual_q") is False]
            elif key == "brighter":
                explains += [e.id for e in evidence if e.metrics.get("polarity") == "brighter"]
            elif key == "all_regions":
                explains += [e.id for e in evidence if e.kind.endswith("_region")]
        out.append(BenignEvidence(
            code=h.code, explains=explains, detail=h.desc, confidence="high",
            sources=[h.source] if h.source else [],
        ))
    return out


# ---------------------------------------------------------------- 观察栏（B 档）

#: 检测器在 `raw` 里自报"我测什么 / 我不测什么"的键名（口径单一来源在检测器自己）。
_OBS_MEASURES = "measures"
_OBS_NOT_MEASURES = "not_measures"
#: 这几个键是**给人读的注解**，不是测量值 —— 不进 `measurements`（否则数字表里混进散文）。
_OBS_ANNOTATION_KEYS = frozenset({_OBS_MEASURES, _OBS_NOT_MEASURES, "polarity_note"})
#: `bbox` 给机器（数组），给人看的那版拼成这个格式（预注册 v2 §2.3 的观察栏口径）。
_OBS_BBOX_FMT = "({x},{y}) {w}×{h}"


def build_observations(results: list) -> dict:
    """观察栏（B 档）：**有数字、有坐标，但不是物证、不进分级**。

    入选条件 = 检测器在 `raw` 里自报了「测什么」（`raw["measures"]`）。这是一条**机制**：
    ELA 的 `raw` 里没有这个键 ⇒ 默认路径（只 ela）恒返回 `{}` ⇒ 默认报告 JSON 逐字节不变
    （`_judgment_invariance.py` 的 ①b 与 `test_detector_isolation.py` 都钉着这点）。
    新维度想进观察栏，必须自己声明边界；没声明的不会**悄悄**溜进来。

    结构（预注册 v2 §3 的口径，Hermes 的验收脚本按键名取用）：
        {"detectors": [{"name", "status", "reason", "score", "measurements",
                        "regions": [{"bbox", "polarity", "z_peak", "shape"}],
                        "what_it_measures", "what_it_does_not"}]}
    """
    detectors: list = []
    for r in results:
        raw = r.raw or {}
        measures = raw.get(_OBS_MEASURES)
        if not measures:
            continue                                  # 没自报边界的 → 不是观察栏检测器
        measurements = {k: v for k, v in raw.items()
                        if k not in _OBS_ANNOTATION_KEYS
                        and isinstance(v, (int, float, bool, str, type(None)))}
        regions: list = []
        for g in r.regions:
            bx = g.get("bbox")
            regions.append({
                "bbox": (_OBS_BBOX_FMT.format(x=bx[0], y=bx[1], w=bx[2], h=bx[3])
                         if isinstance(bx, (list, tuple)) and len(bx) == 4 else ""),
                "polarity": g.get("polarity"),
                "z_peak": g.get("z_peak"),
                "shape": g.get("shape"),
            })
        detectors.append({
            "name": r.name,
            "status": getattr(r, "status", "ok"),
            "reason": getattr(r, "reason", None) or "",
            "score": float(r.score),
            "measurements": measurements,
            "regions": regions,
            "what_it_measures": str(measures),
            "what_it_does_not": str(raw.get(_OBS_NOT_MEASURES) or ""),
        })
    return {"detectors": detectors} if detectors else {}


# ---------------------------------------------------------------- 主流程

class GuardPipeline:
    """统一入口。默认只跑**确定性、可复算**的层（F1/F2/F6/F7/F8）。"""

    def __init__(self, rules: dict | None = None, rules_path: str | Path | None = None,
                 detectors: list | None = None, trace_score: float | None = None,
                 explain: bool = False, explain_ask=None,
                 agent: bool = False, agent_ask=None,
                 observe: bool = False):
        self.rules = rules if rules is not None else load_rules(rules_path)
        #: 层 1 物证扩展（预注册 v2 §3）：观察级检测器（`register(..., default=False)`）**默认关**。
        #: 打开后它们只写报告「附四 观察栏」—— 不进左栏物证、不进 tier、不进规则表，
        #: 且判定面（含 `confidence`）**逐字节不变**；`_observe_names()` / `_judging()` 把这两条钉死。
        #: 显式传了 `detectors` 就以传入为准（A/B 对拍与 Agent 白名单走的就是这条路）。
        self.observe = observe
        if detectors is None and observe:
            self.detectors = sorted(registry.names())      # 默认 + 观察级，全跑
        else:
            self.detectors = detectors
        #: 像素级生成痕迹分（P1 接入前为 None → 第十条判定自动弃权，不假装能判）
        self.trace_score = trace_score
        #: F3 语义解释层开关：**默认关闭** → 默认路径零模型、零网络（拔网线可跑）。
        #: 开启后只做描述与解释，永不参与判定（semantic/explain.py，白名单 + 只读）。
        self.explain = explain
        #: 测试注入口：不传则走 semantic/vl.py 的真实网络出口；
        #: 传了就用它（测试里塞假客户端 → 跑测试**零网络**）。
        self.explain_ask = explain_ask
        #: F9 Agent 编排层开关：**默认关闭**。开启后也只做两件事 —— 追加取证、列出追问；
        #: 它在 report（含判定）构造完成**之后**运行，只写 `report.agent_trace`（agent/planner.py）。
        self.agent = agent
        #: 同 explain_ask 的测试注入口：传了就用它（假客户端 → 跑测试零网络）。
        self.agent_ask = agent_ask
        self.rule_errors: list[str] = []

    # -- 单条内容 --------------------------------------------------------
    def run(self, item: ContentItem, outdir: str | Path | None = None,
            tag: str = "item", sheet: bool = True) -> RiskReport:
        """核验**一条内容**。两个模态可各自独立进入：

            · 给图（image_path）→ F1 像素取证 + F2 标识核验 + F4 交叉
            · 只给文案（text）   → 只跑 F4 文案/语义层（这是"图是真的、话是假的"通路）
            · 两者都没有         → 直接报错（本系统不猜）
        """
        has_img = bool(item.image_path)
        if not has_img and not (item.text or item.comments):
            raise ValueError("ContentItem 既无 image_path 也无 text/comments：没有可核验的输入")
        path = Path(item.image_path) if has_img else None
        if path is not None and not path.exists():
            raise FileNotFoundError(f"输入不存在：{path}")

        out = Path(outdir) if outdir else Path(getattr(config, "OUTPUT_DIR", "output")) / "guard"
        out.mkdir(parents=True, exist_ok=True)

        # --- F1 像素取证层 + F2 标识核验层（无图像输入时整层跳过，并在 layers 里如实标注）
        if has_img:
            img, meta = load_and_prepare(path)
            results = registry.run_all(img, meta=meta, only=self.detectors)

            # --- F2 标识核验层（含跨层定案）
            prov = read_provenance(path)
            ela_raw = next((r.raw for r in results if r.name == "ela"), {}) or {}
            prov = finalize_state(prov, ai_trace_score=self.trace_score,
                                  platform_recompressed=bool(ela_raw.get("platform_recompressed")))
        else:
            img, meta, results, prov = None, {}, [], None

        # --- 信号表（F1/F2 部分）
        signals = build_signals(results, meta, prov)
        evidence = build_evidence(results, prov)

        # --- F4 文案 / 语义一致性层（不依赖图像，可独立跑）
        sem = semantic_layer.analyze(item, signals, start_index=len(evidence))
        signals.update(sem.signals)
        evidence += sem.evidence

        # --- F6/F7 规则层（免责 + 分级，一次判完）
        self.rule_errors = []
        risk_hits = evaluate(signals, self.rules, "risk", self.rule_errors)
        benign_hits = evaluate(signals, self.rules, "benign", self.rule_errors)

        # --- F5 评论区真实性层（只吃文本；条数不足 → 弃权并给归因码）
        #     命中直接并入 F6/F7 的同一条通路：风险命中进 verdict.fired，
        #     免责命中进右半栏账本 —— 这样评论区结论和像素结论共用一套判定与免责机制。
        cverdict = None
        if item.comments:
            csig, cverdict = evaluate_thread(item.comments, self.rules)
            signals.update(csig.as_signals())
            risk_hits = list(risk_hits) + cverdict.as_rule_hits(benign=False)
            benign_hits = list(benign_hits) + cverdict.as_rule_hits(benign=True)

        # --- 物证两侧
        benign = build_benign(benign_hits, evidence, prov)

        # --- 判定
        tier = max_tier([h.tier for h in risk_hits])
        top = max(risk_hits, key=lambda h: h.tier.rank) if risk_hits else None
        # 兜底 confidence 只由"跑成了的**且参与判定的**"检测器给：不适用的一律 low，
        # 观察级（只写附四观察栏）也不许混进来 —— 混进来会把整份报告的置信度拖一档
        # （`high`→`mid`），而它根本没参与判定（2026-09-25 round11 Hermes 验收实测抓到）。
        confs = [e.confidence for e in evidence] or [r.confidence for r in _judging(results)] or ["low"]
        conf = "low" if "low" in confs else ("mid" if "mid" in confs else "high")
        if top is not None:
            headline = f"命中 {top.rule_id}：{top.desc}"
        else:
            headline = ("未命中任何风险规则。这**不等于**「内容为真」——"
                        "本系统不做真假终审：未命中是「没判」，不是「判了清白」。")
        verdict = Verdict(tier=tier, headline=headline, confidence=conf,
                          fired=risk_hits, benign_hits=benign_hits)

        # --- 产物
        files: list = []
        if sheet and has_img:
            # 证据图要画"第一个真跑成了的检测器"的疑点 —— 不适用者没有证据可画。
            # 默认路径（全部 ok）下 first 就是 results[0]，产物与冻结样张逐字节相同。
            first = next(iter(_judging(results)), None)
            try:
                if first is None:
                    files.append("（所有检测器都不适用 → 没有可画的物证）")
                else:
                    files.append(make_evidence_sheet(img, first,
                                                     out / f"{tag}_evidence_sheet.png",
                                                     title=f"LorealGuard {VERSION}"))
            except Exception as e:  # noqa: BLE001  证据图失败不该让报告失败
                files.append(f"（证据图生成失败：{type(e).__name__}: {e}）")
        elif sheet:
            files.append("（无图像输入 → 不生成证据图；本报告的证据是**文案原文 + 生理尺度对照**）")

        #: 指纹 = 本次真正核验的那份物证的锚。有图就锚文件字节；
        #: 无图时锚文本，且**必须把评论算进去** ——
        #: ⚠️ 2026-09-23：这里原先是 `sha256(item.text or "")`，于是**任何**只给评论
        #: 的输入（text 为空）都会落成同一个 `e3b0c442…`（那就是空串的 sha256），
        #: 指纹在评论通路上等于失效 —— 两份完全不同的评论文件得到同一个物证锚。
        #: 这个缺口此前没爆，是因为两个入口都**到不了**这条分支：命令行原先必须有图
        #: （`voiceguard/__main__.py`），网页那边图片是必填（`demo/serve.py:140`）。
        #: CLI 接出 `--comments-file` 之后，这条分支第一次可达，缺口就露出来了。
        #: 纯文案（无评论）的算法**刻意保持不变**，让已冻结的产物逐字节重放得动。
        if has_img:
            fp = sha256_file(path)
        else:
            cmts = list(item.comments or [])
            blob = item.text or ""
            if cmts:
                #: 带上条数：否则 text="a\nb"/无评论 与 text="a"/评论["b"] 会撞成同一个指纹。
                blob += "\n\x1f" + str(len(cmts)) + "\x1f" + "\x1f".join(cmts)
            fp = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        report = RiskReport(
            content_id=item.meta.get("id") or tag, kind=item.kind,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            fingerprint=fp, image_path=str(path) if has_img else "",
            shape=meta.get("shape"), evidence=evidence, benign=benign,
            provenance=prov,
            detectors=[r.as_dict() for r in results],
            verdict=verdict, evidence_files=files,
            boundaries=list(DEFAULT_BOUNDARIES), version=VERSION,
            layers=self._layers(results, prov, risk_hits, benign_hits, meta, sem, cverdict),
            comment_verdict=cverdict.to_dict() if cverdict is not None else {},
            #: 观察栏（B 档）：**有数字有坐标、不是物证不进 tier**。默认路径（只 ela）恒为 {}，
            #: 因此默认报告 JSON 逐字节不变 —— 这条由 `build_observations` 的入选条件保证，
            #: 不靠这里自觉（见该函数的 docstring）。
            observations=build_observations(results),
        )
        # --- F3 语义解释层（可选）：**必须在 report（含判定）构造完成之后**才允许调模型。
        #     代码顺序即不变量：解释拿不到改判定的机会，只能写进独立的 .explanation 字段。
        #     这条不是靠自觉 —— tests/test_semantic_explain.py 会比对「开/关解释」两份 JSON，
        #     判定部分必须逐字节一致。
        if self.explain:
            report.explanation = semantic_explain.explain(
                report, sheet_path=_first_image(files), ask=self.explain_ask)
            report.layers = self._layers(results, prov, risk_hits, benign_hits, meta, sem,
                                         cverdict, explanation=report.explanation)
        # --- F9 Agent 编排层（可选）：与 F3 **同一条纪律、同一个位置** —— 必须在 report
        #     （含判定）构造完成之后才允许调模型。它拿到的是一份已经判完的账本，
        #     能做的只有两件：再补一步取证、列该向创作者追问什么。
        #     `run_detector` 的追加结果只进报告附三，**不并入 evidence/benign/verdict**。
        #     默认关闭 ⇒ `agent_trace` 为空 ⇒ JSON 与 md 都与今天逐字节相同（验收①）。
        if self.agent:
            report.agent_trace = agent_planner.plan(
                report, img=img, meta=meta,
                image_path=str(path) if has_img else None,
                text=item.text or "", comments=list(item.comments or []),
                outdir=out / "_agent", ask=self.agent_ask)
            report.layers = self._layers(results, prov, risk_hits, benign_hits, meta, sem,
                                         cverdict, explanation=report.explanation,
                                         agent_trace=report.agent_trace)
        rep_files = ledger.write_report(report, out, tag)
        report.evidence_files = files + [rep_files["markdown"], rep_files["json"]]
        ledger.write_report(report, out, tag)      # 二次落盘：让 md 里也含产物清单
        return report

    # -- 层运行情况 ------------------------------------------------------
    def _layers(self, results, prov, risk_hits, benign_hits, meta, sem=None, cv=None,
                explanation=None, agent_trace=None) -> list:
        info = describe(self.rules)
        ran = _ran(results)
        # 三态在报告里必须看得见：全跑成 → 老口径（逐字节不变）；有不适用的 → 明说几个，
        # 原因写在 notes 里（每个检测器的 brief() 自带「不适用（原因：…）」）。
        if not results:
            f1_status = "不适用（无图像输入）"
        elif len(ran) == len(results):
            f1_status = f"已跑（{len(results)} 个检测器）"
        else:
            f1_status = f"已跑（{len(ran)} 个检测器；{len(results) - len(ran)} 个不适用）"
        f4_status = f"已跑（{sem.state}）" if sem is not None else "未接入（PLANNED）"
        f4_notes = ("；".join(sem.notes) if sem is not None
                    else "文本与图像是否自相矛盾；提供 text.* 信号")
        if explanation and explanation.get("status") == "ok":
            f3_status = f"已跑（{explanation.get('model')}，仅描述）"
            f3_notes = ("开源权重档（Apache-2.0）；只解释已定位疑点，永不参与判定；"
                        f"引用证据 {', '.join(explanation.get('used_evidence') or []) or '整图'}")
        elif explanation:
            f3_status = f"未产出（{explanation.get('status')}）"
            f3_notes = explanation.get("note") or "解释层未产出，不影响 F1–F7 判定"
        else:
            f3_status = "已接入（默认关闭，需 --explain）"
            f3_notes = ("开启后由开源权重模型只做描述与解释，永不参与判定（semantic/explain.py）；"
                        "判定链 F1–F7 纯算法、零模型零网络")
        #: F9（Agent 编排层）：**只在真跑过时才出现这一行**。
        #: 理由不是"懒得写"，是**实测**：`layers` 在冻结判定 sha256 的覆盖范围内，
        #: 默认路径多一行 → 三条冻结样张的判定当场漂移（验收①）。默认关闭 = 8 行，逐字节不变。
        #: 措辞刻意不夸大：它是**编排**，不是第三层判定。
        f9_rows = []
        if agent_trace:
            if agent_trace.get("status") == "ok":
                f9_status = (f"已跑（{agent_trace.get('model')}，"
                             f"{len(agent_trace.get('steps') or [])} 步，"
                             f"追加取证 {_extra_probes(agent_trace)} 项）")
            else:
                f9_status = f"未产出（{agent_trace.get('status')}）"
            f9_rows.append({"layer": "F9 Agent 编排层", "status": f9_status,
                            "notes": (agent_trace.get("note") or
                                      "只做追加取证与追问；判定权仍在 F1–F7 算法链")})
        return [
            {"layer": "F1 像素取证层",
             "status": f1_status,
             "notes": ("；".join(r.brief() for r in results) if results
                       else "纯文案输入 → 像素层不适用；不适用 ≠ 无异常")},
            {"layer": "F2 标识核验层",
             "status": (f"已跑（{prov.state.value}）" if prov is not None
                        else "不适用（无图像输入）"),
             "notes": "元数据核验＝不对称使用：检不出标识不能推断为真实"},
            {"layer": "F3 语义解释层", "status": f3_status, "notes": f3_notes},
            {"layer": "F4 文案/语义一致性层", "status": f4_status, "notes": f4_notes},
            {"layer": "F5 评论区真实性层",
             "status": (f"已跑（{cv.tier}；命中 {len(cv.rule_ids)} 条）" if cv is not None
                        else "不适用（无评论输入）"),
             "notes": ("纯规则、零大模型、零账号数据；逐条命中与归因见报告「附一」"
                       if cv is not None else
                       "本条输入没有评论区 → 不适用；不适用 ≠ 无异常（提供 review.* 信号）")},
            {"layer": "F6 免责归因层", "status": f"已跑（{len(benign_hits)} 条命中）",
             "notes": "报告右半栏的唯一来源"},
            {"layer": "F7 分级判定层", "status": f"已跑（规则表 v{info.get('version')}，"
                                                  f"命中 {len(risk_hits)} 条）",
             "notes": f"风险规则 {info.get('n_risk')} 条 / 免责规则 {info.get('n_benign')} 条；"
                      f"已核准法条 {info.get('n_clauses_verified')} 条，待复核 {len(info.get('pending_sources') or [])} 项"},
            {"layer": "F8 报告输出层", "status": "已跑", "notes": "双向账本（md）+ 结构化（json）"},
        ] + f9_rows

    # -- 批量 ------------------------------------------------------------
    def run_many(self, items: list, outdir=None, tag_prefix: str = "item") -> list:
        out = []
        for i, it in enumerate(items, 1):
            out.append(self.run(it, outdir=outdir, tag=f"{tag_prefix}{i:02d}"))
        return out


def analyze_content(item: ContentItem, outdir=None, tag: str | None = None,
                    sheet: bool = True, **kw) -> RiskReport:
    """**推荐入口**：核验一条内容（图 + 文案 + 评论，三者可任意组合）。

        analyze_content(ContentItem(image_path="samples/base_neutral.jpg", text="7天根除痘印"))
        analyze_content(ContentItem(text="...", kind="copy"))        # 纯文案也支持

    之所以把"一条内容"而不是"一张图"作为入口：赛题要的是
    「从识别到决策的完整闭环」，而真实世界的一条种草内容是**多模态混合**的
    —— 图文一致性只有在同一条内容里才判得出来。
    """
    return GuardPipeline(**kw).run(item, outdir=outdir,
                                   tag=tag or item.meta.get("id") or "item", sheet=sheet)


def analyze_image(path: str | Path, outdir=None, tag: str | None = None,
                  sheet: bool = True, **kw) -> RiskReport:
    """一行调用：analyze_image('samples/base_neutral.jpg')

    sheet=False 时不生成证据图（批量跑/测试用）；其余 **kw 透传给 GuardPipeline。
    """
    p = Path(path)
    return GuardPipeline(**kw).run(ContentItem(image_path=str(p)),
                                   outdir=outdir, tag=tag or p.stem, sheet=sheet)


if __name__ == "__main__":                      # 方便单文件自测
    import sys
    r = analyze_image(sys.argv[1] if len(sys.argv) > 1 else "samples/base_neutral.jpg")
    print(ledger.summarize(r))