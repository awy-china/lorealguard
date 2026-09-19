"""一根线串起来：ContentItem（图 + 文案）→ Report（结构化风险报告）

P0 只实装层1；层2/层3 留接口占位，P3/P4 填肉。这样上层调用方式现在就是最终形态，
后面加层不用改调用方。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .forensics import DetectionResult, load_and_prepare, make_evidence_sheet, registry, save_result
from . import config
from .core.contract import ContentItem   # 契约单一来源：输入结构定义在 core/contract.py


def analyze_forensics(img: np.ndarray, meta: dict, dets: list[str] | None = None) -> list[DetectionResult]:
    """层1：全部检测器跑一遍。"""
    return registry.run_all(img, cfg=None, meta=meta, only=dets)


def analyze(item: ContentItem, dets: list[str] | None = None, outdir: str | Path | None = None,
            tag: str = "item") -> dict:
    """层1 → 报告骨架。层2(VLM 区域解释) / 层3(Agent 分级判定) 见 semantic/ agent/。"""
    if not item.image_path:
        raise ValueError("P0 暂只支持图像侧核验；文案侧 checkers 见 semantic/text_check.py（P2）")

    img, meta = load_and_prepare(item.image_path)
    results = analyze_forensics(img, meta, dets)
    outdir = Path(outdir or config.OUTPUT_DIR / tag)
    outdir.mkdir(parents=True, exist_ok=True)

    report = {
        "item": {"image_path": str(item.image_path), "source": item.source,
                 "text_len": len(item.text), "shape": meta["shape"]},
        "meta": {"is_jpeg": meta["is_jpeg"], "exif_present": meta["exif_present"]},
        "detectors": [],
        "evidence_files": [],
        "todo": ["层2 semantic/region_explain.py（VLM 解释已定位区域）",
                 "层3 agent/fusion.py + verdict.py + advise.py（分级判定与建议）"],
    }
    for r in results:
        report["detectors"].append(r.as_dict())
        files = save_result(r, outdir, tag)
        report["evidence_files"].append(files)
        if r.name == "ela":
            report["evidence_files"].append(
                {"sheet": make_evidence_sheet(img, r, outdir / f"{tag}_ela_sheet.png")})
    outdir.joinpath(f"{tag}_report.json").write_text(
        __import__("json").dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report