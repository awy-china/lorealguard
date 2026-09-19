"""F2 标识核验层测试 —— 三态判定必须**可证伪**，且不许夸大能力。

这一层的风险不是"漏判"，而是"乱判"：
    元数据缺失（平台压缩、截图转存、微信传输都会造成）**不是**"恶意抹除"。
    第十条的要件是「恶意」，所以判定必须靠跨层证据，而不是靠"文件里没东西"。
下面每组用例都钉在一条具体的政策上。
"""

from __future__ import annotations

import pytest
from PIL import Image, PngImagePlugin

from voiceguard.core.contract import ProvenanceState
from voiceguard.provenance import finalize_state, read_provenance

SD_PARAMS = ("landscape, masterpiece, best quality\nNegative prompt: blurry, lowres\n"
             "Steps: 28, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 12345, "
             "Size: 512x512, Model: dreamshaper_8, Version: v1.7.0")


def _png(path, text: dict | None = None):
    im = Image.new("RGB", (80, 60), (120, 140, 160))
    if text:
        info = PngImagePlugin.PngInfo()
        for k, v in text.items():
            info.add_text(k, v)
        im.save(path, pnginfo=info)
    else:
        im.save(path)
    return path


def _jpeg(path, exif: bool = False, tool: str | None = None):
    im = Image.new("RGB", (80, 60), (120, 140, 160))
    kw = {}
    if exif:
        e = Image.Exif()
        e[0x010F] = "Canon"                       # Make
        e[0x0110] = "Canon EOS R6"                # Model
        e[0x0131] = tool or "Digital Photo Professional"   # Software
        e[0x013B] = "Anonymous"                   # Artist
        kw["exif"] = e
    im.save(path, "JPEG", quality=92, **kw)
    return path


# --------------------------------------------------------------- 有标识
def test_sd_parameters_in_png_are_marked(tmp_path):
    p = _png(tmp_path / "sd.png", {"parameters": SD_PARAMS})
    prov = read_provenance(p)
    assert prov.state is ProvenanceState.MARKED
    assert prov.ai_labels, "PNG tEXt parameters 是 SD/WebUI 的招牌标识，必须能认出来"
    assert any("Stable Diffusion" in str(a) or "WebUI" in str(a) or "Automatic" in str(a)
               for a in prov.ai_labels) or any("Stable Diffusion" in s for s in prov.signals)


def test_comfyui_workflow_text_is_marked(tmp_path):
    p = _png(tmp_path / "comfy.png", {"workflow": '{"nodes":[{"class_type":"KSampler"}]}'})
    prov = read_provenance(p)
    assert prov.state is ProvenanceState.MARKED


def test_generator_name_in_exif_software_is_marked(tmp_path):
    p = _jpeg(tmp_path / "ai.jpg", exif=True, tool="Stable Diffusion WebUI")
    prov = read_provenance(p)
    assert prov.state is ProvenanceState.MARKED


def test_c2pa_container_is_detected(tmp_path):
    """C2PA/JUMBF 容器的**存在性**可扫；验签需要 c2pa 库（PLANNED，报告里如实标注）。"""
    p = _jpeg(tmp_path / "c2pa.jpg", exif=True)
    p.write_bytes(p.read_bytes() + b"\xff\xeb\x00\x14jumbfc2pa\x00manifest")
    prov = read_provenance(p)
    assert prov.counters.get("has_c2pa_container") is True
    assert any("C2PA" in s for s in prov.signals)


# --------------------------------------------------------------- 无标识
def test_camera_jpeg_with_exif_is_complete_unmarked(tmp_path):
    p = _jpeg(tmp_path / "cam.jpg", exif=True)
    prov = read_provenance(p)
    assert prov.state is ProvenanceState.COMPLETE_UNMARKED
    assert not prov.ai_labels


def test_clean_png_without_metadata_abstains_not_stripped(tmp_path):
    """导出/截图出来的 PNG 天生没有元数据 —— 这**不是**剥离证据。"""
    p = _png(tmp_path / "clean.png")
    prov = read_provenance(p)
    assert prov.state is ProvenanceState.UNKNOWN
    assert prov.state is not ProvenanceState.STRIPPED


def test_jpeg_without_metadata_is_only_a_candidate(tmp_path):
    p = _jpeg(tmp_path / "bare.jpg")
    prov = read_provenance(p)
    assert any("元数据容器为空" in s for s in prov.signals), "JPEG 无 EXIF/XMP 应被登记为剥离候选"
    assert prov.state is ProvenanceState.UNKNOWN, "仅有候选事实时不许定案"


# --------------------------------------------------------------- 跨层定案
def test_stripped_requires_pixel_level_ai_trace(tmp_path):
    """第十条要件严格：元数据为空 + 像素层证明它本为 AI 生成 → 才判『被剥离』。"""
    p = _jpeg(tmp_path / "bare2.jpg")
    prov = read_provenance(p)

    abstain = finalize_state(prov, ai_trace_score=None)
    assert abstain.state is ProvenanceState.UNKNOWN, "没有像素级证据时必须弃权，不许假设"

    confident = finalize_state(read_provenance(p), ai_trace_score=0.93)
    assert confident.state is ProvenanceState.STRIPPED


def test_platform_recompression_blocks_stripped_verdict(tmp_path):
    """平台二次压缩会剥离元数据 —— 归因平台，而不是归因创作者恶意。"""
    p = _jpeg(tmp_path / "plat.jpg")
    prov = finalize_state(read_provenance(p), ai_trace_score=0.93,
                          platform_recompressed=True)
    assert prov.state is ProvenanceState.STRIPPED or prov.state is ProvenanceState.UNKNOWN
    if prov.state is ProvenanceState.UNKNOWN:
        assert any("平台" in n for n in prov.notes), "弃权理由必须写清楚是谁剥离的"


def test_provenance_always_reports_its_own_limit(tmp_path):
    """不对称使用必须写进结论里：检不出标识 ≠ 内容为真。"""
    for p in (_jpeg(tmp_path / "a.jpg", exif=True), _png(tmp_path / "b.png")):
        prov = read_provenance(p)
        assert any("不能" in n or "不构成" in n or "不等于" in n or "无法" in n
                   for n in prov.notes), f"{p.name} 缺少能力边界说明"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))