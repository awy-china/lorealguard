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

VERSION = "0.3.0+f4"

#: 尚未接入的信号 + 它归哪一层负责。写在这里而不是藏在注释里：
#: 报告要如实说出"我们还没做这一块"，这比含糊带过更能建立可信度。
PLANNED_SIGNALS: dict[str, str] = {
    "forensics.ai_trace_score": "像素级生成痕迹检测器（P1 待接）",
    "review.bot_score": "F5 评论区真实性层",
    "scene.before_after": "F3 语义层（VL，仅描述）",
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
    """
    raw = res.raw or {}
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


# ---------------------------------------------------------------- 信号构造

def build_signals(results: list, meta: dict, prov=None) -> dict:
    """各层输出 → 扁平信号表（规则表靠它判定）。

    约定：键名 `层.检测器.字段`；未接入的信号显式登记为 None。
    阈值不在这里出现 —— 阈值属于规则表（rules.yaml）。
    """
    s: dict = {}
    for r in results:
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
    """检测器输出 → 统一 Evidence（左半栏）。带坐标、极性、形态、数字。"""
    out: list = []
    n = 0
    for r in results:
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


# ---------------------------------------------------------------- 主流程

class GuardPipeline:
    """统一入口。默认只跑**确定性、可复算**的层（F1/F2/F6/F7/F8）。"""

    def __init__(self, rules: dict | None = None, rules_path: str | Path | None = None,
                 detectors: list | None = None, trace_score: float | None = None):
        self.rules = rules if rules is not None else load_rules(rules_path)
        self.detectors = detectors
        #: 像素级生成痕迹分（P1 接入前为 None → 第十条判定自动弃权，不假装能判）
        self.trace_score = trace_score
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

        # --- 物证两侧
        benign = build_benign(benign_hits, evidence, prov)

        # --- 判定
        tier = max_tier([h.tier for h in risk_hits])
        top = max(risk_hits, key=lambda h: h.tier.rank) if risk_hits else None
        confs = [e.confidence for e in evidence] or [r.confidence for r in results] or ["low"]
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
            try:
                files.append(make_evidence_sheet(img, results[0],
                                                 out / f"{tag}_evidence_sheet.png",
                                                 title=f"LorealGuard {VERSION}"))
            except Exception as e:  # noqa: BLE001  证据图失败不该让报告失败
                files.append(f"（证据图生成失败：{type(e).__name__}: {e}）")
        elif sheet:
            files.append("（无图像输入 → 不生成证据图；本报告的证据是**文案原文 + 生理尺度对照**）")

        fp = (sha256_file(path) if has_img
              else hashlib.sha256((item.text or "").encode("utf-8")).hexdigest())
        report = RiskReport(
            content_id=item.meta.get("id") or tag, kind=item.kind,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            fingerprint=fp, image_path=str(path) if has_img else "",
            shape=meta.get("shape"), evidence=evidence, benign=benign,
            provenance=prov,
            detectors=[r.as_dict() for r in results],
            verdict=verdict, evidence_files=files,
            boundaries=list(DEFAULT_BOUNDARIES), version=VERSION,
            layers=self._layers(results, prov, risk_hits, benign_hits, meta, sem),
        )
        rep_files = ledger.write_report(report, out, tag)
        report.evidence_files = files + [rep_files["markdown"], rep_files["json"]]
        ledger.write_report(report, out, tag)      # 二次落盘：让 md 里也含产物清单
        return report

    # -- 层运行情况 ------------------------------------------------------
    def _layers(self, results, prov, risk_hits, benign_hits, meta, sem=None) -> list:
        info = describe(self.rules)
        f4_status = f"已跑（{sem.state}）" if sem is not None else "未接入（PLANNED）"
        f4_notes = ("；".join(sem.notes) if sem is not None
                    else "文本与图像是否自相矛盾；提供 text.* 信号")
        return [
            {"layer": "F1 像素取证层",
             "status": (f"已跑（{len(results)} 个检测器）" if results else "不适用（无图像输入）"),
             "notes": ("；".join(r.brief() for r in results) if results
                       else "纯文案输入 → 像素层不适用；不适用 ≠ 无异常")},
            {"layer": "F2 标识核验层",
             "status": (f"已跑（{prov.state.value}）" if prov is not None
                        else "不适用（无图像输入）"),
             "notes": "元数据核验＝不对称使用：检不出标识不能推断为真实"},
            {"layer": "F3 语义解释层", "status": "未接入（PLANNED）",
             "notes": "需 VL；按设计只做描述与解释，永不参与判定（semantic/vl.py）"},
            {"layer": "F4 文案/语义一致性层", "status": f4_status, "notes": f4_notes},
            {"layer": "F5 评论区真实性层", "status": "未接入（PLANNED）",
             "notes": "纯规则，不用大模型；提供 review.bot_score"},
            {"layer": "F6 免责归因层", "status": f"已跑（{len(benign_hits)} 条命中）",
             "notes": "报告右半栏的唯一来源"},
            {"layer": "F7 分级判定层", "status": f"已跑（规则表 v{info.get('version')}，"
                                                  f"命中 {len(risk_hits)} 条）",
             "notes": f"风险规则 {info.get('n_risk')} 条 / 免责规则 {info.get('n_benign')} 条；"
                      f"已核准法条 {info.get('n_clauses_verified')} 条，待复核 {len(info.get('pending_sources') or [])} 项"},
            {"layer": "F8 报告输出层", "status": "已跑", "notes": "双向账本（md）+ 结构化（json）"},
        ]

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