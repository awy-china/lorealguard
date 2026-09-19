"""一次性工具：给交付 PDF 做排版质检（走百炼 VL）。

用途：vision_analyze 兜底模型额度已耗尽，改用项目自身的百炼通路检查 PDF 页面：
      中文乱码/缺字体、表格被裁切、元素重叠、整页空白。
注意：这是 QA 工具，不是取证流程，因此自带质检提示词，不用 vl.SYSTEM_PROMPT。
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, r"D:/LorealGuard")

from openai import OpenAI  # noqa: E402

from voiceguard import config  # noqa: E402

OUT = Path(r"D:/tmp")

SYS = (
    "你是排版质检员。只看版面，不复述内容、不做内容评价。"
    "对每个检查项给一句结论，最后给一句总评。用简体中文。"
)
Q = (
    "这是一份中文 PDF 的页面截图，用于排版质检。请逐项检查并简短回答：\n"
    "1) 中文是否乱码或缺字（方框/豆腐块）；\n"
    "2) 表格或文字是否被右边缘裁切、横向溢出；\n"
    "3) 是否有元素重叠、遮挡、明显错位；\n"
    "4) 是否存在大段异常空白（页面下半部空掉一半以上）；\n"
    "5) 一句话总评（合格/有问题+问题在哪）。"
)


def main() -> int:
    key = config.get_api_key()
    if not key:
        print("NO_KEY")
        return 2
    client = OpenAI(api_key=key, base_url=config.BAILIAN_BASE_URL, timeout=150)
    files = sorted(OUT.glob("qa_p*.png"), key=lambda p: int(p.stem.split("_p")[1]))
    print(f"pages_to_check={len(files)}")
    bad = []
    for fn in files:
        n = int(fn.stem.split("_p")[1])
        b64 = base64.b64encode(fn.read_bytes()).decode()
        r = client.chat.completions.create(
            model=config.MODEL_VL,
            messages=[
                {"role": "system", "content": SYS},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": Q},
                ]},
            ],
            max_tokens=1200,
        )
        ans = (r.choices[0].message.content or "").strip()
        flag = "OK" if ("合格" in ans and "问题" not in ans[:60]) else "CHECK"
        if flag == "CHECK":
            bad.append(n)
        print(f"--- p{n} [{flag}] ---\n{ans}\n")
    print("FLAGGED_PAGES:", bad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())