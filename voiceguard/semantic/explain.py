"""F3 语义解释层：把**算法已经定位**的疑点翻成人话。永不参与判定。

为什么单独一个模块（而不是在 guard.py 里直接调 VL）：
    判定链（F1→F7）必须**与模型完全解耦** —— 同一个输入，开不开解释，
    结论必须逐字节一致。把解释隔到独立模块 + 独立字段，这条不变量才有物理保证，
    也才守得住「拔网线可跑」的演示（默认路径零模型、零网络）。

三条硬约束（每条都有测试与变异体守着，见 tests/test_semantic_explain.py）：
    ① **只读**：入参是算完的 report；本模块不写 report 的任何判定字段，
       只回一个独立 dict，由调用方挂到 report.explanation。
    ② **白名单**：只允许 config.OPEN_WEIGHT_MODELS 里的开源权重档
       —— 赛题硬要求「必须用开源大模型而非闭源模型」，这条以代码形式落地。
    ③ **不编造**：无 key / 无图 / 调用失败 → 如实返回 status=unavailable_*，
       绝不返回空串冒充成功，也绝不猜一个看起来合理的解释。

用法（CLI）：
    python -m voiceguard samples/base_neutral.jpg --explain
"""

from __future__ import annotations

from pathlib import Path

from voiceguard import config

#: F3 允许的模型（开源权重白名单）—— 从 config 取，单一来源
ALLOWED_MODELS: dict = dict(config.OPEN_WEIGHT_MODELS)

#: 提交给模型的提问模板：**只描述已定位的区域**，不提供任何"是否伪造"的问法
_TEMPLATE = (
    "下面是图像取证流程**已经用算法定位**的疑点（坐标与统计量都是实测值，不需要你判断）。\n"
    "{items}\n\n"
    "请只做两件事：\n"
    "1) 用一两句人话说清每个坐标区域**在画面里是什么**（例如：人脸皮肤区、纯色背景、"
    "文字标签、产品包装、发丝边缘）；\n"
    "2) 指出这些区域在语义上是否属于**容易被算法误伤的正常内容**"
    "（例如：磨皮后的皮肤、平滑渐变的背景、压缩后的文字边缘）。\n"
    "严禁输出任何『伪造/篡改/真假/拼接』的结论；不确定就写『不足以判断』。"
)


def build_question(report) -> str:
    """把 report 里**已定位**的证据拼成提问文本。纯函数、可单测。

    只用左半栏（可疑证据）的坐标与数字 —— 右半栏（免责归因）不进提问，
    避免把"为什么不怀疑你"当成"哪里可疑"喂给模型。
    """
    ev = list(getattr(report, "evidence", []) or [])
    if not ev:
        return ("本次算法**未定位到任何可疑区域**（左半栏为空）。"
                "请只描述这张图整体是什么内容（物体/场景），不要做任何真伪判断。")
    lines: list[str] = []
    for e in ev:
        bbox = e.bbox
        loc = f"坐标(x={bbox[0]},y={bbox[1]},w={bbox[2]},h={bbox[3]})" if bbox else "整图级（无坐标）"
        nums = "；".join(f"{k}={v}" for k, v in list(e.metrics.items())[:4])
        lines.append(f"- {e.id} {loc}｜算法结论：{e.label}"
                     + (f"｜实测值：{nums}" if nums else ""))
    return _TEMPLATE.format(items="\n".join(lines))


def _pick_image(report, sheet_path: str | Path | None) -> Path | None:
    """挑要送出去的那张图：**优先我们自己的证据图**，其次原图。

    证据图是本系统自产的产物（含算法叠图），比原图更能让模型对上"哪个区域"。
    """
    if sheet_path:
        p = Path(sheet_path)
        if p.exists() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return p
    ip = getattr(report, "image_path", "") or ""
    p = Path(ip) if ip else None
    return p if (p and p.exists()) else None


def explain(report, sheet_path: str | Path | None = None, model: str | None = None,
            client=None, ask=None) -> dict:
    """对一条**已判定**的报告做语义解释。返回独立 dict（含 status），绝不改 report。

    参数 client / ask 可注入：测试用假客户端，跑测试时**零网络**。
    """
    m = model or config.MODEL_SEMANTIC
    base = {"model": m, "license": ALLOWED_MODELS.get(m, ""),
            "question": build_question(report), "answer": "", "used_evidence": []}

    # —— 约束②：白名单。不在开源权重白名单里的档，一律拒绝（且不发起任何调用）
    if m not in ALLOWED_MODELS:
        why = config.RESEARCH_ONLY_MODELS.get(m, "未知档")
        return {**base, "status": "refused_closed_model",
                "note": (f"模型 {m} 不在开源权重白名单内（{why}）；"
                         f"赛题要求必须用开源大模型 → F3 拒绝调用。"
                         f"可用档：{', '.join(sorted(ALLOWED_MODELS))}")}

    img = _pick_image(report, sheet_path)

    # —— 约束③：拿不到图 / 没 key → 如实降级
    if img is None:
        return {**base, "status": "unavailable_no_image",
                "note": "没有可送出的图像（证据图缺失且原图不存在）→ F3 不产出解释"}

    if ask is None:
        from voiceguard.semantic.vl import ask_vl

        def ask(p, q, m_):        # noqa: E306  局部适配，保持 vl.py 为唯一网络出口
            return ask_vl(p, q, model=m_)

    base["used_evidence"] = [e.id for e in (getattr(report, "evidence", []) or [])]
    base["image_sent"] = str(img)
    try:
        res = ask(img, base["question"], m)
    except Exception as e:                      # noqa: BLE001  网络/额度失败不该让报告失败
        msg = f"{type(e).__name__}: {e}"
        if "key" in msg.lower() or "未找到 API key" in msg:
            return {**base, "status": "unavailable_no_key",
                    "note": "未配置 API key → F3 不产出解释（判定链不受影响）"}
        return {**base, "status": "error", "note": f"调用失败：{msg[:200]}"}

    if not res or not res.get("ok"):
        err = (res or {}).get("error") or "返回空内容"
        if "key" in str(err).lower():
            return {**base, "status": "unavailable_no_key",
                    "note": "未配置 API key → F3 不产出解释（判定链不受影响）"}
        return {**base, "status": "error", "note": f"模型未给出可用回答：{err}"}

    return {**base, "status": "ok", "answer": res.get("answer", ""),
            "prompt_tokens": res.get("prompt_tokens"),
            "reasoning_tokens": res.get("reasoning_tokens"),
            "note": "本层只描述与解释，**不参与判定**；判定结果由 F1–F7 独立给出。"}


def render_section(ex: dict) -> list[str]:
    """把解释 dict 渲染成报告 md 的一节（附二）。返回行列表。"""
    L: list[str] = []
    a = L.append
    a("## 附二、语义解释（F3，**非判定**）")
    a("")
    a("这一节是**解释**，不是**判定**：它由模型产出，只回答「算法定位的那块区域在画面里是什么」，"
      "永远不改变上面第一至四节的结论。开启方式：`--explain`。")
    a("")
    if not ex:
        a("本项未运行（默认关闭）。判定链 F1–F7 为纯算法，**零模型、零网络**即可复算。")
        a("")
        return L
    st = ex.get("status")
    a(f"- 状态：**{st}**")
    a(f"- 模型：`{ex.get('model')}`" + (f"（许可：{ex['license']}）" if ex.get("license") else ""))
    if ex.get("image_sent"):
        a(f"- 解释所用图：`{ex['image_sent']}`")
    if ex.get("used_evidence"):
        a(f"- 提问中引用的已定位证据：{', '.join(ex['used_evidence'])}")
    if ex.get("note"):
        a(f"- 说明：{ex['note']}")
    a("")
    if ex.get("status") == "ok" and ex.get("answer"):
        a("> " + ex["answer"].replace("\n", "\n> "))
    else:
        a("（本层本次未产出解释，原因见上；这不影响第一至四节的判定。）")
    a("")
    return L


__all__ = ["ALLOWED_MODELS", "build_question", "explain", "render_section"]