# -*- coding: utf-8 -*-
"""③ 右半栏良性编辑不变性 —— HANDOFF-to-cc.md §2 的 ③。

**口径（写于测量之前，跑之前就写在这段注释里）**

守护对象是创作者 ⇒ 良性编辑**不得**升到「关注级及以上」。
对 `samples/base_neutral.jpg`（程序合成中性底图，无个人数据）施加**四类单算子编辑**：

    ① 裁剪 80% 回原尺寸    ② 尺寸不变重压缩 q75
    ③ 调色（暖色温）        ④ 加字幕（底部叠加条）

判据 A（等级）：任一类升到「关注级（WATCH）及以上」＝ 红。
判据 B（右半栏）：左半栏有物证时，右半栏必须非空，且每条 `code ∈ BENIGN_CODES`。

**实测结论（2026-09-24，先跑后记，未改任何阈值）**：①③④ 与未编辑对照均为「未触发」；
**② 尺寸不变的重压缩 q75 升到关注级**，命中 `R-SPLICE-SUSPECT-REVIEW`，而右半栏只有
`NO_TEXT_PROVIDED`（「没给文案」，与本次编辑无关）—— 即**该升级没有被任何免责码归因**。
按判据 A，③ 是**红**的。

**这是能力边界，不是待修的阈值**（铁律 1/2 + HANDOFF §3.1：测出红＝登记，不是改检测器）。
缺口的确切成因已由 `test_inplace_recompression_is_pinned_to_the_registered_boundary` 钉死：
免责闸门 `forensics.ela.reliable`（定义见 `voiceguard/guard.py:77-93`）只在
`jpeg_history is False` 或 `platform_recompressed is True` 时放行免责，而
**尺寸不变的低质重编码两条都不满足** —— 于是 ELA 被放大出来的、图中本就存在的良性硬边
（产品瓶矩形边界与高光带，`samples/make_neutral_base.py:97`、`:101`）被当成拼接证据升级。

**本文件不是"合格证"**：它把当前的红钉成会红的断言。缺口被修好时第③条会红 → 强制回来
更新登记；①③④ 任一类退化成误报时第①条会红。第②条自证伪证明第①条不是永远绿的装饰品。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samples import make_p2_suite as P2                                    # noqa: E402
from voiceguard.core.contract import (                                     # noqa: E402
    BENIGN_CODES, ContentItem, RiskTier,
)
from voiceguard.forensics import load_and_prepare, run_all                  # noqa: E402
from voiceguard.guard import analyze_content, build_signals                 # noqa: E402

#: 判据 A 的红线：升到这个等级即红。
RED_LINE = RiskTier.WATCH

#: 真拼接阳性对照（既有合成样本，见 samples/make_splice_sample.py）。
SPLICE = ROOT / "samples" / "fw_splice.jpg"


def _green_cases() -> list[tuple[str, object]]:
    """四类编辑里的**三类 + 未编辑对照** —— 它们是本文件的绿断言。

    算子一律复用 `samples/make_p2_suite.py` 的 `case_*`（同一口径，不另写第二套），
    约定与调用点一致：返回 `(arr, save_kwargs)`，存盘质量取 `meta.get("quality", 93)`。
    """
    return [
        ("对照·未编辑", lambda a: (a, {})),
        ("① 裁剪 80% 回原尺寸", lambda a: P2.case_crop_rescale(a, None)),
        ("③ 调色·暖色温", lambda a: P2.case_global_warm(a, None)),
        ("④ 加字幕·底部叠加条", lambda a: P2.case_watermark_bar(a, None)),
    ]


def _write_jpeg(arr, path: Path, quality: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(arr).astype(np.uint8)).save(path, "JPEG", quality=quality)
    return path


@pytest.fixture(scope="session")
def _reports(tmp_path_factory) -> dict:
    """全部工况各跑一次 `analyze_content`（session 级缓存 —— 每张图检测约十秒级）。

    `sheet=False`：不落证据图，测试不留副作用。
    """
    out = tmp_path_factory.mktemp("benign_edit")
    base = P2.load_base()
    reports = {}

    for name, fn in _green_cases():
        arr, meta = fn(base)
        p = _write_jpeg(arr, out / f"{name[:2].strip()}.jpg", int(meta.get("quality", 93)))
        reports[name] = analyze_content(
            ContentItem(image_path=str(p), meta={"id": name}), outdir=out, tag="g", sheet=False)

    # ② 尺寸不变的重压缩 q75 —— 即本轮测出的那条红。
    p = _write_jpeg(P2.jpeg_roundtrip(base, 75), out / "inplace_q75.jpg", 93)
    reports["② 重压缩 q75·尺寸不变"] = analyze_content(
        ContentItem(image_path=str(p), meta={"id": "inplace_q75"}), outdir=out, tag="r", sheet=False)
    reports["_inplace_path"] = str(p)

    # 对照工况：同质量、只多一步改尺寸（P2 b22 形态）—— 证明缺口与"改尺寸"有关。
    p = _write_jpeg(P2.resample_short_side(base, 1080), out / "resized_q75.jpg", 75)
    reports["⑤ 对照·改尺寸 1080 + q75"] = analyze_content(
        ContentItem(image_path=str(p), meta={"id": "resized_q75"}), outdir=out, tag="c", sheet=False)

    # 阳性对照：真拼接必须升级。
    reports["_splice"] = analyze_content(
        ContentItem(image_path=str(SPLICE), meta={"id": "splice"}), outdir=out, tag="p", sheet=False)
    return reports


def _offenders(reports: dict, names) -> list:
    """判据 A 的**单一实现** —— 绿断言与它的自证伪共用这一段代码。

    分成两份写会让自证伪证的变成"另一段判据"，而不是守卫本身。
    """
    return [(n, reports[n].verdict.tier) for n in names
            if reports[n].verdict.tier.rank >= RED_LINE.rank]


def test_three_edits_stay_below_watch(_reports):
    """判据 A：裁剪 / 调色 / 加字幕 / 未编辑对照 —— 四者都不得升到关注级及以上。

    这一条**会红**：任何一类良性编辑退化成都市传说级的误报源，或者检测器改动引入了
    新的误报，它立刻红。红了不许改阈值（铁律 1/2）—— 先看是哪一类、再决定是登记边界
    还是修检测器，并把结论写回本文件顶部的实测结论段。
    """
    offenders = _offenders(_reports, [n for n, _ in _green_cases()])
    assert not offenders, (
        f"良性编辑升到了「{RED_LINE.value}」及以上：{offenders}\n"
        "—— 误报护栏是第一优先级，这是 ③ 的红，不是调阈值的理由。"
    )


def test_the_level_guard_would_catch_an_edit_that_escalates(_reports):
    """自证伪②：把**已知会升级**的 ② 混进那份名单 → 判据必须抓到它、且只抓它。

    这一条直接证明上面那条守卫不是永远绿的装饰品 —— 它用的是同一段 `_offenders` 代码，
    只是换了一份输入。若这条红了，说明判据本身失去灵敏度，上面那条的"绿"毫无意义。
    """
    known_red = "② 重压缩 q75·尺寸不变"
    names = [n for n, _ in _green_cases()] + [known_red]
    got = [n for n, _ in _offenders(_reports, names)]
    assert got == [known_red], (
        f"判据对已知会升级的工况抓到的是 {got}（应恰好是 ['{known_red}']）—— "
        "判据本身不灵敏，`test_three_edits_stay_below_watch` 的绿是假绿"
    )


def test_the_green_assertion_is_not_vacuous(_reports):
    """自证伪：真拼接样本**必须**升级。

    若连外源像素拼接都不升级，上面那条「良性编辑不升级」就是永远绿的废话 ——
    一条不会红的检查等于没有检查。这条把它锚住：判定链还有牙齿。
    """
    rep = _reports["_splice"]
    assert rep.verdict.tier.rank >= RED_LINE.rank, (
        f"真拼接样本只判到「{rep.verdict.tier.value}」—— 判定链失去灵敏度，"
        "上面那条「良性编辑不升级」的断言因此变得毫无意义"
    )


def test_inplace_recompression_is_pinned_to_the_registered_boundary(_reports):
    """已登记的能力边界（**不是合格线**）：尺寸不变的低质重压缩 → 关注级。

    钉死两件事，缺口被修好时本用例必须变红，强制回来更新登记：

      1. 现状：② 判到关注级，且命中的就是真拼接用的同一条规则 `R-SPLICE-SUSPECT-REVIEW`；
      2. 成因：`forensics.ela.reliable` 仍为 True（`platform_recompressed` 认不出
         「尺寸不变的重编码」，见 `voiceguard/guard.py:89-92`），所以免责闸门没拦住 ——
         而对照组「改尺寸 1080 + q75」的 z 峰值更高（25.2 vs 15.8）却因
         `reliable=False` 被拦下。**同质量、只差一步 resize，结论就翻了。**
    """
    rep = _reports["② 重压缩 q75·尺寸不变"]
    assert rep.verdict.tier is RED_LINE, (
        f"② 的等级变成了「{rep.verdict.tier.value}」（登记时是「{RED_LINE.value}」）——\n"
        "  变绿：缺口被修好了，请更新 tests/test_benign_edit_invariance.py 与 CLAUDE.md 的能力边界登记；\n"
        "  变得更红：请先把新形态登记下来，再判断是否属于检测器回归。"
    )
    assert "R-SPLICE-SUSPECT-REVIEW" in [h.rule_id for h in rep.verdict.fired], (
        "升到了关注级，但不是 R-SPLICE-SUSPECT-REVIEW 触发的 —— 缺口成因变了，"
        "本用例钉死的那条链已经不成立，必须重新定位后再写"
    )

    img, meta = load_and_prepare(_reports["_inplace_path"])
    sig = build_signals(run_all(img, None, meta), meta)
    assert sig["forensics.ela.reliable"] is True, (
        "ELA 已自认不可靠 —— 免责闸门本可以拦住它，说明闸门行为已变，请重新定位缺口"
    )
    assert sig["forensics.ela.n_brighter_regions"] >= 1 and \
        sig["forensics.ela.brighter_z_peak_max"] >= 6, (
        "规则的三条闸门（reliable / n_brighter_regions>=1 / z_peak_max>=6）不再同时成立 —— "
        "本用例钉死的是「闸门全过、且可靠」这一形态，变了就要重写"
    )

    resized = _reports["⑤ 对照·改尺寸 1080 + q75"]
    assert resized.verdict.tier.rank < RED_LINE.rank and \
        "PLATFORM_RECODE" in [b.code for b in resized.benign], (
        "对照组「改尺寸 + 同质量」没被平台重编码免责掉 —— 缺口的分界线（几何是否变化）"
        "不再成立，② 的成因描述需要重写"
    )


def test_right_column_is_attributed_when_left_column_is_not_empty(_reports):
    """判据 B：左半栏有物证时，右半栏必须非空、且只能用法定免责码。

    口径的稳妥读法（写于测量之前）：**条件式**断言，而不是无条件要求右半栏非空 ——
    对照工况的 `explains=[]` 是正常形态（没物证可解释），无条件断言会造假红。
    这里同时钉住"码必须在册"：右半栏不许各层自己造句（`contract.py:181` 的纪律）。
    """
    for name, rep in _reports.items():
        if name.startswith("_"):
            continue
        if not rep.evidence:
            continue
        assert rep.verdict.benign_hits, (
            f"{name}：左半栏有 {len(rep.evidence)} 条物证，右半栏却一条免责归因都没有 —— "
            "「宁可漏判也不冤枉人」的两栏账本在这条工况上塌了半边"
        )
        unknown = [b.code for b in rep.benign if b.code not in BENIGN_CODES]
        assert not unknown, f"{name}：右半栏出现未登记的免责码 {unknown}（不许各层自己造句）"
