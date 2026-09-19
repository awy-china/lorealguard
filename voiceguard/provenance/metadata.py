"""F2 标识核验层：读**文件元数据**里的隐式标识 → 判定三态。

法定依据（《人工智能生成合成内容标识办法》第五条，原文已核准）：
    隐式标识「在生成合成内容的**文件元数据**中添加」，包含生成合成内容属性信息、
    服务提供者名称或者编码、内容编号等制作要素信息；并**鼓励**添加数字水印。

所以这一层要找的东西是**枚举好的**，不是"看模型心情"：
    ① C2PA / Content Credentials（JPEG APP11 / PNG 私有块，JUMBF 容器）—— 行业标准
    ② SD WebUI / ComfyUI 的生成参数（PNG tEXt 里的 `parameters` / `workflow`）
    ③ 生成器软件签名（EXIF `Software`、XMP `xmp:CreatorTool`、IPTC）
    ④ 内容编号 / 服务提供者编码（第五条要求的"制作要素信息"）

⚠️ 本层最重要的工程结论（写进答辩，也写进代码注释）：
    **元数据核验必须「不对称使用」**。检出 AI 标识 = 强结论；
    检不出**不能**推断为真实 —— 平台（小红书/抖音）上传时会重新编码，把
    EXIF/XMP/tEXt 整体丢掉，这是**常态而不是作弊**。
    因此「元数据缺失」单独**永远不足以**判定第十条的"恶意删除"，
    必须由别的层（像素级生成痕迹）补上"这本来是个 AI 图"这个前提要件。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.contract import ProvenanceResult, ProvenanceState, law_ref

NAME = "provenance"

# ---------------------------------------------------------------- 生成器签名表

#: 生成器/工具签名 → 正则。命中即写入 ai_labels，**不做真假判断**，只报告"元数据自称是谁造的"。
AI_TOOL_PATTERNS: dict[str, str] = {
    "Midjourney": r"midjourney",
    "Stable Diffusion": r"stable[\s_\-]?diffusion|sdxl|sd[-_]?1\.5|\bsd[-_]?1\.\d",
    "SD WebUI / Automatic1111": r"automatic1111|a1111|stable-diffusion-webui|negative prompt",
    "ComfyUI": r"comfyui",
    "NovelAI": r"novelai",
    "Adobe Firefly": r"firefly",
    "OpenAI / DALL·E": r"dall[\s·\-]?e|openai",
    "Flux": r"\bflux\b|black forest labs",
    "Ideogram": r"ideogram",
    "Leonardo.ai": r"leonardo\.ai",
    "Google Imagen / Gemini": r"\bimagen\b|\bgemini\b",
    "通义万相": r"通义万相|wanx|tongyi",
    "文心一格": r"文心一格|\bern?\s?ie\b",
    "即梦 / Seedream": r"即梦|seedream|jimeng",
    "可灵 / Kling": r"可灵|kling",
    "海螺 / Hailuo": r"海螺|hailuo",
    "腾讯混元": r"混元|hunyuan",
    "豆包": r"豆包|doubao",
}

#: 强签名：光出现这个词就基本可以确认是生成工具写的元数据（比 "openai" 这类弱词可靠）
STRONG_KEYS = ("parameters", "workflow", "negative prompt", "prompt")

#: C2PA / JUMBF 容器特征串（不验签，只声明"容器在"）
C2PA_MARKERS = (b"jumbf", b"c2pa", b"contentcredentials", b"c2pa.claim")

#: 提取元数据时只看前若干 MB，避免超大文件把内存吃满
_SCAN_LIMIT = 4 * 1024 * 1024


def _extract(p: Path, raw: bytes) -> dict:
    """抽取元数据三件套（EXIF / XMP / PNG 文本块）+ C2PA 容器特征。全部容错。"""
    out = {"fmt": p.suffix.lower().lstrip(".") or "bin", "exif": {}, "xmp": "",
           "png_text": {}, "c2pa": [], "error": None}
    exif, xmp, png_text = {}, "", {}

    try:
        from PIL import Image, ExifTags

        with Image.open(p) as im:
            try:
                ex = im.getexif()
                for tag, val in ex.items():
                    exif[str(ExifTags.TAGS.get(tag, tag))] = _s(val)
                for tag, val in (ex.get_ifd(0x8769) or {}).items():   # Exif IFD：UserComment 在这
                    exif[f"Exif.{ExifTags.TAGS.get(tag, tag)}"] = _s(val)
            except Exception:  # noqa: BLE001  EXIF 坏了不影响其它证据
                pass
            try:
                xd = im.getxmp()                                   # Pillow>=8.3，JPEG/PNG/WebP
                xmp = _xmp_to_text(xd) if xd else ""
            except Exception:  # noqa: BLE001
                pass
            try:
                png_text = {str(k): _s(v) for k, v in (getattr(im, "text", None) or {}).items()}
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"

    head = raw[:_SCAN_LIMIT]
    out["c2pa"] = [m.decode() for m in C2PA_MARKERS if m in head]
    if not xmp:                                    # 兜底：直接从字节里找 XMP 包（PIL 读不到时）
        m = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", head, re.S)
        if m:
            xmp = m.group(0).decode("utf-8", "ignore")
    out["exif"], out["xmp"], out["png_text"] = exif, xmp, png_text
    return out


def _s(v) -> str:
    """EXIF 值 → 字符串。bytes 走 latin-1（UserComment 常带 UTF-16 头）。"""
    if isinstance(v, bytes):
        try:
            return v.decode("utf-16", "ignore").strip("\x00") or v.decode("latin-1", "ignore")
        except Exception:  # noqa: BLE001
            return v.decode("latin-1", "ignore")
    return str(v)


def _xmp_to_text(xd) -> str:
    """getxmp() 的嵌套 dict → 一段可搜索的文本（我们只做关键词匹配，不需要结构）。"""
    buf: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                buf.append(str(k))
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        else:
            buf.append(str(o))

    walk(xd)
    return " ".join(buf)


#: PNG 文本块的**键名**本身就是生成器家族签名 —— 几乎只有 AI 生成链路会写这些键，
#: 而键名往往比键值更可靠（用户可能把 prompt 清空，但不会把键删掉）。
PNG_KEY_SIGNATURES: dict[str, str] = {
    "parameters": "Stable Diffusion WebUI（parameters 文本块）",
    "workflow": "ComfyUI（workflow 工作流 JSON）",
    "prompt": "Stable Diffusion 家族（prompt 文本块）",
    "negative_prompt": "Stable Diffusion 家族（negative_prompt）",
    "sd-metadata": "InvokeAI（sd-metadata）",
    "invokeai_metadata": "InvokeAI",
    "ai_info": "AI 生成信息块",
}


def _find_ai_labels(exif: dict, xmp: str, png_text: dict) -> list[dict]:
    """在元数据里找生成器签名。返回 [{"field","tool","value"}]。

    两条通路，缺一不可：
        ① **键名**签名（PNG_KEY_SIGNATURES）：最容易漏、也最容易认的标志；
        ② **值/文本**正则（AI_TOOL_PATTERNS）：EXIF Software、XMP CreatorTool、各类文本块。
    """
    labels: list[dict] = []
    seen: set[str] = set()

    for k, v in (png_text or {}).items():
        tool = PNG_KEY_SIGNATURES.get(str(k).strip().lower())
        if tool and tool not in seen:
            labels.append({"field": f"PNG.{k}", "tool": tool, "value": str(v or "")[:180]})
            seen.add(tool)

    hay: list[tuple[str, str]] = []
    hay += [(f"EXIF.{k}", v) for k, v in exif.items()]
    hay += [(f"PNG.{k}", v) for k, v in png_text.items()]
    if xmp:
        hay.append(("XMP", xmp))

    for field, value in hay:
        text = (value or "").strip()
        if not text:
            continue
        low = text.lower()
        for tool, pat in AI_TOOL_PATTERNS.items():
            if tool in seen:
                continue
            if re.search(pat, low):
                labels.append({"field": field, "tool": tool, "value": text[:180]})
                seen.add(tool)
    return labels


def _has_c2pa(raw: bytes) -> list[str]:
    head = raw[:_SCAN_LIMIT]
    return [m.decode() for m in C2PA_MARKERS if m in head]


# ---------------------------------------------------------------- 主入口

def read_provenance(path: str | Path) -> ProvenanceResult:
    """读文件元数据，产出**未定案**的三态候选（定案交给 finalize_state）。

    分级：MARKED 可以单独定案（检出即强结论）；其余都要跨层证据。
    """
    p = Path(path)
    raw = p.read_bytes()
    ex = _extract(p, raw)

    exif, xmp, png_text, c2pa = ex["exif"], ex["xmp"], ex["png_text"], ex["c2pa"]
    ai_labels = _find_ai_labels(exif, xmp, png_text)

    n_exif = sum(1 for v in exif.values() if str(v).strip())
    n_text = sum(1 for k, v in png_text.items() if str(v).strip())
    has_xmp = bool(xmp.strip())

    counters = {
        "format": ex["fmt"], "n_exif_fields": n_exif, "n_png_text": n_text,
        "has_xmp": has_xmp, "c2pa_markers": c2pa, "has_c2pa_container": bool(c2pa),
        "n_ai_labels": len(ai_labels), "bytes_scanned": min(len(raw), _SCAN_LIMIT),
        "extract_error": ex["error"],
    }

    prov = ProvenanceResult(state=ProvenanceState.UNKNOWN, ai_labels=ai_labels,
                            counters=counters, confidence="low")
    _add_signals(prov, exif, xmp, png_text, c2pa, counters)

    if ai_labels:
        # 检出即强结论：元数据自称是 AI 生成的（第六条（一）的情形）
        prov.state = ProvenanceState.MARKED
        prov.confidence = "high"
        prov.sources = [law_ref("MARK_ART5_META"), law_ref("MARK_ART6_1")]
        tools = "、".join(sorted({a["tool"] for a in ai_labels}))
        prov.signals.insert(0, f"命中生成器/工具签名：{tools}（{ai_labels[0]['field']}）")
        prov.notes.append("检出 AI 标识属『已声明为生成合成内容』——这不是伪造，"
                          "而是第六条（一）的『应加显著提示标识』情形，请按提示级处理。")
    elif c2pa:
        # 有 C2PA 容器但没匹配到生成器名 → 内容凭证存在，交给 c2pa 库验签（P1 待接）
        prov.state = ProvenanceState.COMPLETE_UNMARKED
        prov.confidence = "mid"
        prov.sources = [law_ref("MARK_ART5_META")]
        prov.signals.append("检出 C2PA/JUMBF 容器特征（未验签）→ 元数据完整，建议用 c2pa 库核对声明")
        prov.notes.append("本层只做容器特征检测，**不做签名验证**；c2pa-python 验签通道见 P1 待办。")
    elif counters["format"] in ("jpg", "jpeg") and n_exif == 0 and not has_xmp:
        # JPEG 连 EXIF 都没有 → "元数据被剥离"的候选，但绝不单独定案
        prov.state = ProvenanceState.UNKNOWN
        prov.sources = [law_ref("MARK_ART10_TAMPER")]
        prov.signals.append("JPEG 无 EXIF 且无 XMP → 元数据容器为空（可能是剥离，"
                            "也可能是平台重编码 / 截图转存）")
    elif n_exif >= 3 or has_xmp or n_text:
        prov.state = ProvenanceState.COMPLETE_UNMARKED
        prov.confidence = "mid"
        prov.signals.append(f"元数据完整（EXIF {n_exif} 项 / PNG 文本 {n_text} 块 / XMP={has_xmp}），"
                            "其中未发现任何 AI 标识")
        prov.notes.append("元数据完整且无 AI 标识，**不能**据此推断内容为真实拍摄 —— "
                          "非 AI 工具生成的图片同样不会带标识。")
    else:
        prov.signals.append(f"该格式（{counters['format']}）普遍不含元数据（PNG/截图常见），"
                            "元数据缺失在此**不构成**任何证据")
        prov.notes.append("PNG/截图天然无元数据，缺失既不是剥离证据、也不是真实证据。")

    prov.notes.append("⚠️ 不对称使用：检不出 AI 标识**不等于**内容为真实 —— "
                      "平台二次压缩会整体剥离元数据（05-实测：平台上传图几乎抓不到 C2PA）。")
    return prov


def finalize_state(prov: ProvenanceResult, ai_trace_score: float | None = None,
                   platform_recompressed: bool = False, trace_threshold: float = 0.7) -> ProvenanceResult:
    """跨层定案：把「元数据缺失」升级为 STRIPPED 需要**同时**满足两个要件。

    这是本层最重要的设计（也是答辩应主动讲的一点）：
        第十条罚的是「**恶意**删除、篡改、伪造、隐匿标识」，要件有两个：
            ① 它本来是有标识的（= 像素层能证明"这是 AI 生成的"）
            ② 标识是被抹掉的（= 元数据异常缺失，且不是平台压缩的锅）
        只有 ① ② 同时成立，才叫"抹标识"。缺一，就只能诚实输出 UNKNOWN。

    现状（P0/框架阶段）：② 有检测（本层），① 缺检测器（P1 待做像素级生成痕迹检测）→
    ai_trace_score 传 None 时，**永远不输出 STRIPPED**，而是输出 UNKNOWN + 明确说明缺什么。
    宁可弃权，也不把"平台压缩"和"恶意的"混为一谈 —— 那正是对真实创作者最大的伤害。
    """
    if prov.state in (ProvenanceState.MARKED,):
        return prov

    strip_candidate = any("元数据容器为空" in s for s in prov.signals)
    if not strip_candidate:
        return prov

    if platform_recompressed:
        prov.state = ProvenanceState.UNKNOWN
        prov.confidence = "low"
        prov.signals.append("该文件疑似经平台二次压缩 → 元数据缺失可由平台重编码解释，"
                            "**不得**归因『恶意抹除』（第十条要件不成立）")
        return prov

    if ai_trace_score is None:
        prov.state = ProvenanceState.UNKNOWN
        prov.confidence = "low"
        prov.notes.append("缺少像素级『生成痕迹』检测器（P1 待接）→ 无法证明该图曾带 AI 标识，"
                          "故第十条的两个要件只满足一个，本层**弃权**（输出 UNKNOWN，不判 STRIPPED）。")
        return prov

    if ai_trace_score >= trace_threshold:
        prov.state = ProvenanceState.STRIPPED
        prov.confidence = "mid"
        prov.sources = [law_ref("MARK_ART10_TAMPER")]
        prov.signals.append(f"元数据容器为空 + 像素层检出生成痕迹（ai_trace_score="
                            f"{ai_trace_score:.2f} ≥ {trace_threshold}）→ 两个要件同时成立")
        prov.notes.append("据此判为『标识被剥离』：建议按第十条提示平台复核该内容的标识缺失。")
    else:
        prov.state = ProvenanceState.UNKNOWN
        prov.confidence = "low"
        prov.signals.append(f"像素层未检出生成痕迹（ai_trace_score={ai_trace_score:.2f} < "
                            f"{trace_threshold}）→ 元数据缺失无 AI 前提要件，弃权")
    return prov


def _add_signals(prov: ProvenanceResult, exif, xmp, png_text, c2pa, counters) -> None:
    """把"我们到底看到了什么"逐条记下来 —— 报告要能让人自己复算。"""
    prov.signals.append(
        f"文件格式 {counters['format']}；EXIF 有效字段 {counters['n_exif_fields']} 项；"
        f"PNG 文本块 {counters['n_png_text']} 个；XMP={'有' if counters['has_xmp'] else '无'}"
    )
    for k in ("Software", "Exif.UserComment", "ImageDescription"):
        if exif.get(k):
            prov.signals.append(f"EXIF[{k}] = {str(exif[k])[:120]}")
    for k in ("parameters", "workflow", "prompt", "Comment", "Description"):
        if png_text.get(k):
            prov.signals.append(f"PNG[{k}] = {str(png_text[k])[:120]}")
    if c2pa:
        prov.signals.append(f"C2PA 容器特征命中：{', '.join(c2pa)}")


# ---------------------------------------------------------------- CLI

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m voiceguard.provenance.metadata <图片路径>")
        raise SystemExit(2)
    r = read_provenance(sys.argv[1])
    print(f"state = {r.state.value}  (confidence={r.confidence})")
    for s in r.signals:
        print("  ·", s)
    for n in r.notes:
        print("  !", n)