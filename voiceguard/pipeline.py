"""DEPRECATED（P0 遗留骨架）—— 现行入口在 `voiceguard/guard.py::GuardPipeline`。

本文件是 P0 阶段的"一根线"草稿：只跑检测器并把结果倒成一个裸 dict，**没有**账本
契约（LedgerReport）、没有免责归因、没有风险评估 —— 也就是没有本项目的核心逻辑。
保留原因：`analyze_forensics()` 是一个方便的直接调用口（测试与诊断脚本用），
且删除会让 `tests/test_contract.py` 的兼容性断言失效。

⚠️ 新代码一律走 `guard.py`。本文件的去留已登记在 `docs/DEBT.md`；
在它被删掉之前，测试必须继续覆盖它 —— 否则就是"没人用的代码慢慢腐烂"。
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