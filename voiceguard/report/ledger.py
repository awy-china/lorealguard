"""F8 报告层：**双向账本**。

这份报告的形状本身就是我们的产品主张，所以它不是"输出格式"，而是核心交付物：

    ┌───────────────────────┬──────────────────────────────┐
    │ 一、可疑证据（左半）    │ 二、免责与良性归因（右半）      │
    │ 坐标 / 极性 / 形态 / 数字│ 为什么这些现象不该怀疑你        │
    └───────────────────────┴──────────────────────────────┘

行业报告普遍只有左半栏 —— 那等于把"美颜导致的正常低残差"丢给平台去脑补成伪造。
右半栏是本项目的差异点，也是误伤真实创作者的第一道护栏。

另外三条固定内容（缺一不可）：
    ③ 元数据/标识核验（三态，对齐《标识办法》第六条与第十条）
    ④ 分级依据（命中哪条规则、引用哪条法条**原文**、哪些条文标着"待复核"）
    ⑤ 能力边界（我们做了什么、明确不做什么 —— 主动声明局限是可信度的一部分）
    ⑥ 申诉指引（创作者侧：被误标了怎么办）
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.contract import BENIGN_CODES, LAW_MARK_VERIFIED_AT, RiskTier

_SEP = "─" * 72


def _src(s) -> str:
    return f"{s.display()}" if s else "（未标注出处）"


def _gen(report) -> str:
    if not report.generated_at:
        return ""
    return f" · 生成于 {report.generated_at}"


def _input_line(report) -> str:
    """「- 输入：」那一行 —— 如实写清本次核验的**物证是什么形态**。

    ⚠️ 2026-09-23：这一行原先是
        `a(f"- 输入：`{report.image_path}`" + (尺寸...))`
    无图输入时 `image_path` 是空串，于是渲染成一个**空的 code span**
    ——页面上就是「输入：」后面跟一对空反引号，看着像渲染坏了。
    无图通路（纯文案 / 纯评论）在 CLI 接出 `--text` / `--comments-file`
    之前两个入口都**到不了**，所以这个空壳一直没人看见：
    不是它没问题，是没人走到。
    """
    if report.image_path:
        wh = f"（{report.shape[1]}×{report.shape[0]}）" if report.shape else ""
        return f"- 输入：`{report.image_path}`{wh}"
    if report.comment_verdict:
        n = (report.comment_verdict.get("signals") or {}).get("review.n_comments")
        return f"- 输入：纯文本输入（无图像）· 评论 {n} 条" if n else "- 输入：纯文本输入（无图像）· 评论"
    return "- 输入：纯文本输入（无图像）· 文案"


def render_markdown(report) -> str:
    """报告 = 双向账本。任何人拿到这份 md 都能自己复核每一条结论。"""
    v = report.verdict
    L: list[str] = []
    a = L.append

    a(f"# LorealGuard 核验报告 · {report.content_id}")
    a("")
    a(f"**判定：{v.tier.value}** · 置信度 {v.confidence}{_gen(report)}")
    a("")
    if v.headline:
        a(f"> {v.headline}")
        a("")
    a(_input_line(report))
    a(f"- 物证指纹 sha256：`{report.fingerprint}`")
    a(f"- 报告版本：LorealGuard {report.version}")
    a("")
    a(_SEP)

    # ---------------------------------------------- 一、可疑证据
    a("")
    a("## 一、可疑证据（左半栏）")
    a("")
    if report.evidence:
        a("| # | 层 | 结论 | 关键数字 | 置信度 | 坐标 | 出处 |")
        a("|---|---|---|---|---|---|---|")
        for e in report.evidence:
            m = e.metrics
            nums = "；".join(f"{k}={_num(m[k])}" for k in list(m)[:5])
            bbox = f"({e.bbox[0]},{e.bbox[1]}) {e.bbox[2]}×{e.bbox[3]}" if e.bbox else "—"
            srcs = "；".join(_src(s) for s in e.sources) or "—"
            a(f"| {e.id} | {e.layer} | {e.label} | {nums} | {e.confidence} | {bbox} | {srcs} |")
        a("")
        for e in report.evidence:
            if e.detail:
                a(f"- **{e.id}** {e.detail}")
    else:
        a("本次**未发现可疑证据**。")
        a("")
        a("> ⚠️ 这不等于「已确认为真实」：本系统不做真假终审；"
          "且平台重压缩、全局滤镜会抹平像素级对比（检测器不适用 ≠ 无异常）。")
    a("")
    a(_SEP)

    # ---------------------------------------------- 二、免责与良性归因
    a("")
    a("## 二、免责与良性归因（右半栏）")
    a("")
    a("这一栏回答的是：**为什么不怀疑你**。它和左半栏同为一级输出，不允许被省略。")
    a("")
    if report.benign:
        a("| 代码 | 归因 | 置信度 | 它解释了哪些证据 |")
        a("|---|---|---|---|")
        for b in report.benign:
            exp = "、".join(b.explains) if b.explains else "（整图级，无对应坐标）"
            a(f"| `{b.code}` | {b.label or BENIGN_CODES.get(b.code, '')} | {b.confidence} | {exp} |")
        a("")
        for b in report.benign:
            if b.detail:
                a(f"- **`{b.code}`** {b.detail}")
    else:
        a("本次**未触发任何已知良性归因**。")
        a("")
        a("> 这一栏为空时请谨慎解读：它意味着「当前输入没有可归因的良性解释」，"
          "**不意味着**「已被确认为伪造」。请以左半栏有无坐标级证据为准。")
    a("")
    a(_SEP)

    # ---------------------------------------------- 三、元数据与标识核验
    a("")
    a("## 三、元数据与标识核验（三态，对齐《标识办法》）")
    a("")
    p = report.provenance
    if p:
        a(f"**判定：{p.state.value}**（置信度 {p.confidence}）")
        a("")
        a("我们实际看到的东西（可自行复算）：")
        for s in p.signals:
            a(f"- {s}")
        if p.counters:
            a("")
            a("| 计数 | 值 |")
            a("|---|---|")
            for k, val in p.counters.items():
                a(f"| {k} | {_num(val)} |")
        if p.ai_labels:
            a("")
            a("检出的 AI 标识：")
            for lab in p.ai_labels:
                a(f"- `{lab.get('field')}` → 签名 **{lab.get('tool')}**：`{str(lab.get('value'))[:120]}`")
        if p.notes:
            a("")
            for n in p.notes:
                a(f"> {n}")
    else:
        a("本项未运行（该输入不含文件元数据）。")
    a("")
    a(f"> 法条原文核准时间：{LAW_MARK_VERIFIED_AT}。已核准条文 = 可引用；"
      "标【依据待复核】的条文**不得**直接作为结论依据，需按 04-法规文档第二节复核后再引用。")
    a("")
    a(_SEP)

    # ---------------------------------------------- 四、分级依据
    a("")
    a("## 四、分级依据（命中规则与法条原文）")
    a("")
    if v.fired:
        for h in v.fired:
            a(f"### `{h.rule_id}` → {h.tier.value}")
            a("")
            a(f"- 规则：{h.desc}")
            if h.source:
                a(f"- 依据：{h.source.display()}")
                if h.source.quote:
                    a(f"- 原文：> {h.source.quote}")
                if h.source.url:
                    a(f"- 来源：{h.source.url}")
            used = {k: val for k, val in h.signals_used.items() if not str(k).startswith("_")}
            if used:
                a(f"- 触发时实测值：`{json.dumps(used, ensure_ascii=False)}`")
            if h.advice_platform:
                a(f"- 给平台：{h.advice_platform}")
            if h.advice_creator:
                a(f"- 给创作者：{h.advice_creator}")
            a("")
    else:
        a("**未命中任何风险规则。**")
        a("")
        a("> 这不等于「内容为真」：本系统只在证据足以支撑某个等级时才升级判定，"
          "未命中就是未命中 —— 但它是「没判」，不是「判了清白」。")
    a("")
    if v.benign_hits:
        a("<details><summary>免责规则命中（展开）</summary>")
        a("")
        for h in v.benign_hits:
            a(f"- `{h.rule_id}`（{h.code}）：{h.desc}")
        a("")
        a("</details>")
        a("")
    a(_SEP)

    # ---------------------------------------------- 五、能力边界
    a("")
    a("## 五、能力边界（主动声明局限）")
    a("")
    for b in report.boundaries:
        a(f"- {b}")
    a("")
    a(_SEP)

    # ---------------------------------------------- 六、申诉指引
    a("")
    a("## 六、被误标了怎么办（创作者申诉指引）")
    a("")
    a("本系统把「创作者侧」和「平台侧」的结论分开写，就是为了这一步能走通：")
    a("")
    a("1. **先看第二节（免责栏）**：若已有良性归因，把这一节原文附在申诉材料里 ——"
      "它本身就是「为什么不该怀疑你」的证据。")
    a("2. **再看第一节的坐标**：任何可疑结论都带 `(x,y) 宽×高`、极性与形态，"
      "可回到原图逐点核对；如果算法指的位置是纯色背景或文字边缘，提出异议。")
    a("3. **提交原始文件**：带 EXIF 的原始拍摄文件（或 AI 生成工具的原始输出）是最强证据。"
      "注意平台上传后元数据会被剥离，所以**必须提交本地原图**，不是平台上的图。")
    a("4. **要求按第十条复核**：若系统判「标识被剥离」而你又确实没加过标识，"
      "指出本报告第三节的计数（EXIF 项数等），要求平台用 `c2pa` 验签通道复核。")
    a("")
    a("> 📦 以上第 1–4 步中，「把右栏原文 + 坐标 + 哈希锚整理成可提交文件」这一步"
      "**已经自动化**：本报告可用 `python tools/make_appeal_pack.py --report <本报告的 json> "
      "--image <原图> --outdir <输出目录>` 一键导出申诉材料（自证包）。"
      "⚠️ 该工具**只消费本报告**，不重算、不调模型；且当左栏非空而右栏为空时会**拒绝导出**"
      "（只有物证、没有归因的申诉材料是反效果）。"
      "第 2、3 步仍必须人工完成 —— 逐点核对坐标、提交本地原图，这两件工具替不了你。")
    a("")
    a(_SEP)

    # ---------------------------------------------- 附
    a("")
    # ---------------------------------------------- 附一：评论区核验（F5）
    cv = getattr(report, "comment_verdict", None) or {}
    if cv:
        a(_SEP)
        a("")
        a("## 附一、评论区核验（F5 评论区真实性层）")
        a("")
        a(f"- 本层判定：**{cv.get('tier')}**（映射到全局等级：{cv.get('contract_tier')}）")
        a("- 口径：只用**给定评论列表的文本**（零大模型、零账号数据）；单条评论永不判。")
        a("")
        if cv.get("hits"):
            a("| 命中的规则 | 等级 | 触发时的实测数字 | 建议（平台） | 建议（创作者） |")
            a("|---|---|---|---|---|")
            for h in cv["hits"]:
                nums = "；".join(f"{k}={_num(v)}" for k, v in (h.get("signals_used") or {}).items())
                a(f"| `{h['id']}` | {h['tier']} | {nums} | "
                  f"{h.get('advice_platform', '')} | {h.get('advice_creator', '')} |")
            a("")
        a("**归因（为什么不判得更重 / 为什么弃权）**：")
        a("")
        for x in (cv.get("attributions") or []):
            a(f"- `{x.get('code')}` {x.get('detail', '')}")
        a("")
        a("> 本层出口只有「建议人工复核」，**没有「这是水军」的终审**；"
          "账号维度数据（注册时长/发帖频率/IP/设备）不在本系统手里 —— 凡是需要它才能定的结论，一律不判。")
        a("")

    # ---------------------------------------------- 附二：语义解释（F3，非判定）
    from ..semantic.explain import render_section   # 延迟导入：避免 report ↔ semantic 循环
    L += render_section(getattr(report, "explanation", None) or {})

    # ---------------------------------------------- 附三：Agent 编排（F9，非判定）
    #  与附二同一条纪律：这是**附录**，不进第一至四节。
    #  ⚠️ 只在 F9 **真跑过**时才渲染（默认关闭 → 既没有这一节、`layers` 里也没有 F9 行）：
    #  验收① 要求默认路径的报告与今天**逐字节相同**，`_layers()` 的实测漂移见回执。
    agent_trace = getattr(report, "agent_trace", None) or {}
    if agent_trace:
        from ..agent.planner import render_section as render_agent   # 同上：延迟导入
        L += render_agent(agent_trace)

    # ---------------------------------------------- 附四：观察栏（层1 扩展，B 档，非判定）
    #  官方点名但此前**未实现**的两个维度（`loreal-topic2-details.md:48` 光影/阴影一致性、
    #  `:50` 色彩过渡）＋ 自建的 `noise`（噪声残差一致性，非官方点名）在这里如实交账：
#  **有数字、有坐标，但不是物证、不进分级**。
    #  ⚠️ 与附三同一条纪律：只在观察栏**非空**时渲染 —— 默认路径（只 ela）连这一节都不出现，
    #  报告因此与冻结样张逐字节相同。
    observations = getattr(report, "observations", None) or {}
    if observations.get("detectors"):
        a("## 附四：观察栏（层1 扩展维度 · 仅供人工复核）")
        a("")
        a("> 这一栏**不是物证**：它不进左栏、不进分级、不参与任何规则触发。"
          "本系统的判定链只由 ELA（物证）+ 文案/评论（语义）+ 标识核验（来源）三段构成，"
          "下面这些维度是**给人看的第二双眼睛**。")
        a("")
        # 出处**逐维**区分（2026-10-01 round42）：`lighting`/`color_edge` 是官方点名的两个维度；
        # `noise` 是**本队自建**（`noise.py` 自述 / `docs/DEBT.md` D-42）—— 此前一律写
        # 「官方口径见 loreal-topic2-details.md:48/:50」会把自建的那个也挂到官方原文上（口径错）。
        _OFFICIAL_DIMS = {"lighting", "color_edge"}
        for d in observations["detectors"]:
            _nm = d.get("name")
            _prov = ("官方口径见 `loreal-topic2-details.md:48/:50`" if _nm in _OFFICIAL_DIMS
                     else "**本队自建，非官方点名** · 只作观察（见 `noise.py` 自述 / `docs/DEBT.md` D-42）")
            a(f"### 观察维度：`{_nm}`（{_prov}）")
            a("")
            a(f"1. **它测什么**：{d.get('what_it_measures', '')}")
            a(f"2. **本次测到的值**：{_num(d.get('measurements') or {})}"
              f"　（本次得分 {d.get('score')}）")
            if d.get("status") != "ok":
                a(f"3. **本次**：**不适用**（原因：{d.get('reason', '')}）"
                  " —— 不适用≠正常，只是这次测不出可复核的位置。")
            elif not d.get("regions"):
                a("3. **本次**：检测器跑了，但**没有给出可复核的位置**"
                  "（未过触发线 / 判据未同时满足）—— 同样≠正常。")
            else:
                a(f"3. **本次**：给出 {len(d['regions'])} 处可复核位置 —— "
                  + "；".join(f"{g.get('bbox', '')}（{g.get('polarity', '')}，"
                              f"z_peak {g.get('z_peak')}，{g.get('shape', '')}）"
                              for g in d["regions"][:5]))
            a(f"4. **为什么不在左栏**：{d.get('what_it_does_not', '')}"
              "　左栏物证的准入是「能指向编辑行为、且可靠性可控」；"
              "本维度的背离在真实拍摄里也有大量合理解释，按「宁可漏判、不冤枉人」的口径"
              "只作复核线索，**不得**用它升级风险等级。")
            a("")

    a("## 附：各层运行情况")
    a("")
    a("| 层 | 状态 | 说明 |")
    a("|---|---|---|")
    for l in report.layers:
        a(f"| {l.get('layer')} | {l.get('status')} | {l.get('notes', '')} |")
    if report.evidence_files:
        a("")
        a("产物：")
        for f in report.evidence_files:
            a(f"- `{f}`")
    a("")
    return "\n".join(L)


def _num(x) -> str:
    if isinstance(x, float):
        return f"{x:.4g}"
    if isinstance(x, list):
        return "[" + ", ".join(_num(i) for i in x) + "]"
    if isinstance(x, dict):
        return "{" + ", ".join(f"{k}: {_num(v)}" for k, v in list(x.items())[:4]) + "}"
    return str(x)


def render_json(report) -> str:
    #: 逐级降级（鸭子类型，保持既有写法，不要求 report 一定是 RiskReport）：
    #:   `as_dict_with_appendix()` = `as_dict()`（判定部分逐字节不变）
    #:     + **F9 真跑过时的** agent_trace + **观察栏非空时的** observations。
    #: 三者默认都空 ⇒ 与 `as_dict()` 返回完全相同的字典（验收①）。
    #: ⚠️ 链子必须**从长到短**写：漏掉 appendix 这一级，附三/附四就都落不了盘。
    fn = (getattr(report, "as_dict_with_appendix", None)
          or getattr(report, "as_dict_with_agent", None) or report.as_dict)
    return json.dumps(fn(), ensure_ascii=False, indent=2)


def write_report(report, outdir: str | Path, tag: str = "item") -> dict:
    """落盘 md + json。返回文件清单（供报告自身引用）。"""
    d = Path(outdir)
    d.mkdir(parents=True, exist_ok=True)
    md, js = d / f"{tag}_report.md", d / f"{tag}_report.json"
    md.write_text(render_markdown(report), encoding="utf-8")
    js.write_text(render_json(report), encoding="utf-8")
    return {"markdown": str(md), "json": str(js)}


def summarize(report) -> str:
    """一行摘要（终端用）。带指纹前缀 —— 便于人工对号，也便于别人复算验证。"""
    v = report.verdict
    return (f"[{report.content_id}] {v.tier.value} (conf={v.confidence}) "
            f"fp={str(report.fingerprint)[:8]} "
            f"可疑证据={len(report.evidence)} 免责归因={len(report.benign)} "
            f"标识={report.provenance.state.value if report.provenance else 'n/a'}")


__all__ = ["render_markdown", "render_json", "write_report", "summarize", "RiskTier"]