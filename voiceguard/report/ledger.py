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
    a(f"- 输入：`{report.image_path}`" + (f"（{report.shape[1]}×{report.shape[0]}）" if report.shape else ""))
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
    a("> ⏳ 以上第 1–4 步目前需要人工执行；**一键生成申诉材料（自证包）**是待建的下一步能力"
      "（对应产品方案里「守护创作者」的另一半）。")
    a("")
    a(_SEP)

    # ---------------------------------------------- 附
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
    return json.dumps(report.as_dict(), ensure_ascii=False, indent=2)


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