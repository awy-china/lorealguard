"""P0 步骤⑤：验证百炼（阿里云 DashScope 国内站）通路 —— 文本 + 视觉各一次

验证点：
  1. key 能从环境/Hermes .env 读到（不打印明文）
  2. Agent 模型（deepseek-v4.1-flash）能出话
  3. VL 模型（qwen3-vl-plus）能读我们自己的**证据图**并说人话
     —— 这是层2 的命门：模型只能解释已定位的疑点，不能自己找坐标
产物：output/p0/api_check.json
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard import config  # noqa: E402

OUT = Path(config.OUTPUT_DIR) / "p0"
OUT.mkdir(parents=True, exist_ok=True)

key = config.get_api_key()
print("=" * 60)
if not key:
    print("❌ 未找到 DASHSCOPE_API_KEY（环境变量与 Hermes .env 都没有）")
    raise SystemExit(2)
print(f"✅ key 已读到：{key[:6]}…{key[-4:]}（长度 {len(key)}，不落盘）")
print(f"   base_url = {config.BAILIAN_BASE_URL}")

from openai import OpenAI  # noqa: E402

client = OpenAI(api_key=key, base_url=config.BAILIAN_BASE_URL, timeout=config.API_TIMEOUT)
report: dict = {"base_url": config.BAILIAN_BASE_URL, "key_len": len(key)}


def chat(model: str, messages: list, max_tokens: int = 1800) -> dict:
    """统一调用 + **把空回答判为失败**。

    实测坑（2026-09-18）：deepseek-v4.1-flash / qwen3-* 是推理模型，
    completion_tokens 里含 reasoning_tokens，会把 max_tokens 全部烧在思考上 →
    content 为空字符串而 finish_reason=length。若只看"没抛异常"就会误判为可用。
    """
    r = client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens)
    ch = r.choices[0]
    txt = (ch.message.content or "").strip()
    u = r.usage.model_dump() if r.usage else {}
    rtok = (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    return {"model": model, "ok": bool(txt), "reply": txt,
            "finish_reason": ch.finish_reason, "reasoning_tokens": rtok,
            "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
            "error": None if txt else
            f"返回空内容（finish_reason={ch.finish_reason}，reasoning_tokens={rtok}）"
            "——多数是 max_tokens 被思考过程吃光"}


def call_text() -> dict:
    print("\n[1] 文本通路 " + config.MODEL_AGENT)
    res = chat(config.MODEL_AGENT,
               [{"role": "user",
                 "content": "用一句中文说明：在图像取证里，ELA 检测到的高残差区域能证明什么、不能证明什么？"}])
    print(f"   finish={res['finish_reason']} reasoning_tokens={res['reasoning_tokens']}")
    print("   →", (res["reply"] or res["error"])[:300])
    return res


def call_vision() -> dict:
    model = config.MODEL_VL
    sheet = OUT / "B_拼接_sheet.png"
    if not sheet.exists():
        sheet = next(iter(sorted(OUT.glob("*_sheet.png"))), None)
    print(f"\n[2] 视觉通路 {model}  证据图={sheet.name}")
    b64 = base64.b64encode(sheet.read_bytes()).decode()
    res = chat(model, [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        {"type": "text", "text":
            "这是图像取证的四联证据图（左起：原图 / ELA差值放大 / 热力图叠加 / 异常区域框）。"
            "请只描述你**看到**的画面内容与那张热力图/框大致出现在什么位置，"
            "一句话，不要下『是否伪造』的结论。"},
    ]}])
    res["image"] = sheet.name
    print(f"   finish={res['finish_reason']} reasoning_tokens={res['reasoning_tokens']}")
    print("   →", (res["reply"] or res["error"])[:400])
    return res


for name, fn in (("text", call_text), ("vision", call_vision)):
    try:
        report[name] = fn()
    except Exception as e:                      # 网络/额度都可能出错，如实记录
        report[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        print(f"   ❌ {type(e).__name__}: {str(e)[:300]}")

(OUT / "api_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
ok = all(v.get("ok") for k, v in report.items() if isinstance(v, dict) and "ok" in v)
print("\n" + "=" * 60)
print("百炼通路：" + ("✅ 文本+视觉均可用" if ok else "⚠️ 见上方错误，已记录到 api_check.json"))