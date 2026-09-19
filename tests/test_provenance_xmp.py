"""元数据通路（EXIF / XMP / PNG 文本块 / C2PA）的回归 —— 之前 XMP 解析 44 行没被测过。

为什么 XMP 值得单独测：**AI 工具名最常出现的地方就是 XMP 的 `xmp:CreatorTool`**，
而它是"已声明 AI"三态判定的头号证据来源。之前只测了 EXIF 与 PNG 键名，等于把
最可能命中的那条通路放在测试之外 —— 覆盖率报告里那 44 行未覆盖，就是它。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard.provenance.metadata import (                                    # noqa: E402
    _extract,
    _xmp_to_text,
    read_provenance,
)


# ------------------------------------------------------------------ 纯函数
def test_xmp_to_text_flattens_nested_dict():
    txt = _xmp_to_text({"xmp": {"CreatorTool": "Midjourney v6",
                                "dc": {"title": "夏日种草"}, "n": 1}})
    for token in ("CreatorTool", "Midjourney v6", "夏日种草", "xmp", "1"):
        assert token in txt, f"嵌套 dict 展平后丢了 {token!r}"


def test_xmp_to_text_handles_lists_and_scalars():
    assert _xmp_to_text(["a", {"b": ["c"]}]) == "a b c"
    assert _xmp_to_text(None) == "None"


# ------------------------------------------------------------------ 兜底通路
def test_extract_reads_xmp_packet_from_raw_bytes(tmp_path):
    """PIL 读不到 XMP 时，必须能从**原始字节**里捞出 XMP 包（这是兜底，不是装饰）。"""
    p = tmp_path / "fake.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"JFIF\x00"
                  + b"<x:xmpmeta xmlns:x='adobe:ns:meta/'>"
                  + b"<xmp:CreatorTool>Midjourney</xmp:CreatorTool>"
                  + b"</x:xmpmeta>" + b"\x00" * 32)

    ex = _extract(p, p.read_bytes())
    assert "xmp:CreatorTool" in ex["xmp"], ex["xmp"][:200]
    assert ex["error"] is not None, "这不是有效图片，必须如实记录解析错误（而不是装作没发生）"


def test_read_provenance_flags_ai_tool_inside_xmp(tmp_path):
    p = tmp_path / "fake.jpg"
    p.write_bytes(b"JFIF\x00"
                  + b"<x:xmpmeta xmlns:x='adobe:ns:meta/'>"
                  + b"<xmp:CreatorTool>Midjourney</xmp:CreatorTool>"
                  + b"</x:xmpmeta>" + b"\x00" * 32)

    prov = read_provenance(p)
    assert prov.counters["has_xmp"] is True
    assert prov.ai_labels, "XMP 里写着 Midjourney，必须被捞出来"
    assert any("Midjourney" in str(lab) for lab in prov.ai_labels), prov.ai_labels
    assert prov.state != "unknown" or prov.signals, "有标识时必须给出人话信号"


# ------------------------------------------------------------------ PNG 键名
def test_png_text_block_key_signature_detected(tmp_path):
    """PNG 文本块的**键名**本身就是生成器签名（键名比键值可靠：用户会清 prompt，不会删键）。"""
    p = tmp_path / "sd.png"
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "a photo of a serum bottle, steps: 30, sampler: DPM++")
    Image.new("RGB", (16, 16), (10, 20, 30)).save(p, pnginfo=info)

    prov = read_provenance(p)
    assert prov.ai_labels, "parameters 键名 = SD WebUI 家族签名，必须命中"
    assert "Stable Diffusion" in str(prov.ai_labels)


def test_clean_png_has_no_ai_labels(tmp_path):
    """反向断言：干净的 PNG 不许被冤枉 —— 元数据通路的误报护栏就在这里。"""
    p = tmp_path / "clean.png"
    Image.new("RGB", (16, 16), (200, 200, 200)).save(p)
    prov = read_provenance(p)
    assert prov.ai_labels == [], f"干净 PNG 被误判：{prov.ai_labels}"


# ------------------------------------------------------------------ C2PA
def test_c2pa_marker_in_bytes_is_detected(tmp_path):
    p = tmp_path / "c2pa.jpg"
    p.write_bytes(b"\x00" * 8 + b"c2pa.claim" + b"\x00" * 8)
    prov = read_provenance(p)
    assert prov.counters["has_c2pa_container"] is True
    assert prov.counters["c2pa_markers"], "命中的标记原文要留档，便于人工复核"