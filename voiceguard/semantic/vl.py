"""层2 语义层（第一块砖）：把图交给百炼 VL，**只描述、不判定**。

铁律（本项目最核心的设计决策）：
    VL 模型绝不回答"这张图是否伪造"。它只做两件事：
      1. 描述画面内容（是什么、有没有人脸/产品/文字）
      2. 解释**算法已经定位**的那个区域在语义上是什么
    原因：VLM 对压缩痕迹的敏感度远低于 ELA/噪声检测器，却极擅长编造"看起来合理"的结论。
    让模型当"判官"= 把项目最大的不确定性放在最不可控的环节。

用法：
    from voiceguard.semantic.vl import ask_vl
    print(ask_vl("output/p0/x_sheet.png", "描述这张图"))
"""

from __future__ import annotations

import base64
from pathlib import Path

from voiceguard import config

# 系统提示词：把"不判定"写死进每一次调用，不依赖调用方自觉
SYSTEM_PROMPT = (
    "你是图像取证流程里的『语义解释员』，不是判官。"
    "你只描述你能看到的内容，以及把局部区域的语义说清楚（例如：这是一张人脸的区域、"
    "这是一块纯色背景、这是一个文字标签）。"
    "严禁输出『伪造/篡改/真假』的结论，也不要猜测压缩历史、像素统计量这类你看不到的东西。"
    "如果画面信息不足以回答，就直接说『不足以判断』。回答用简体中文，简洁。"
)

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _client():
    from openai import OpenAI

    key = config.get_api_key()
    if not key:
        raise RuntimeError("未找到 API key（环境变量或本机 .env）")
    return OpenAI(api_key=key, base_url=config.BAILIAN_BASE_URL, timeout=config.API_TIMEOUT)


def ask_vl(image_path: str | Path, question: str, model: str | None = None,
           max_tokens: int = 1800) -> dict:
    """问 VL 一个问题。返回 {ok, answer, finish_reason, reasoning_tokens, error}。

    max_tokens 默认给足：qwen3-vl / deepseek 系是推理模型，token 会被思考过程吃掉，
    给 200 会出现"content 为空但没报错"的假成功（P0 实测踩过）。
    """
    p = Path(image_path)
    if not p.exists():
        return {"ok": False, "answer": "", "error": f"图片不存在：{p}"}
    mime = _MIME.get(p.suffix.lower(), "image/png")
    b64 = base64.b64encode(p.read_bytes()).decode()

    r = _client().chat.completions.create(
        model=model or config.MODEL_VL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": question},
            ]},
        ],
        max_tokens=max_tokens,
    )
    ch = r.choices[0]
    ans = (ch.message.content or "").strip()
    u = r.usage.model_dump() if r.usage else {}
    rtok = (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    return {"ok": bool(ans), "answer": ans, "finish_reason": ch.finish_reason,
            "reasoning_tokens": rtok, "prompt_tokens": u.get("prompt_tokens"),
            "error": None if ans else f"返回空内容（finish={ch.finish_reason}, reasoning={rtok}）"}