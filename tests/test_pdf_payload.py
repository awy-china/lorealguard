# -*- coding: utf-8 -*-
"""`tools/check_pdf_payload.py` 的守卫 —— 钉住"这条判据**会红**"。

为什么补（2026-09-29 真伤现场）
------------------------------
重出 deck 时 `export_pdf.py` **exit 0**、文件也正常写出来了、页数 19 页没错、
体积只比历史小 36%（2,398,482 B vs 3,739,128 B）—— 看着能用。实测**图像对象
1,908 → 34 个、图像字节 3.09 MB → 22.6 KB**（真因＝挂到上一个进程遗留的
PowerPoint COM 实例）。⇒ 退出码 / 体积 / 页数**都判不出这类坏件**，
判据只能落在"图像负载本身"；而按老规矩**一条不会红的检查等于没有检查**。

三个用例（各自 docstring 写明**怎么改就会红**）
---------------------------------------------
  ① 判据本体两个方向：合成纯文字 PDF ⇒ 必红；合成带图 PDF ⇒ 必绿（下限放到 1/1）。
  ② 下限真的在下限位：用交付基线的下限（200 个 / 1 MB）判那张合成小图 ⇒ 必红。
     把 `DEFAULT_MIN_OBJECTS/BYTES` 改成 0 ⇒ ②红（下限成了摆设）。
  ③ 真产物 + 接线（opt-in）：交付区那份 19 页 PDF 在 ⇒ 必须 ≥ 基线读数；
     `tools/preflight.py` 里必须**真的调**了本判据（只 import 不调 = 装饰品）。
     PDF 不在（换机器 / 从零重造链）⇒ skip —— "产物不存在"不等于"判据坏了"。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_pdf_payload as cp   # noqa: E402

#: 交付区（不在本仓库内 —— 与 tests/test_number_consistency.py 同一种写死法）
DECK_PDF = pathlib.Path("D:/deliver/compete/loreal-ppt/LorealGuard-Deck-v1.pdf")
PREFLIGHT = ROOT / "tools" / "preflight.py"


def _write_pdf(with_image: bool) -> pathlib.Path:
    """把合成 PDF 落到**系统临时目录**（不写仓库 output/ —— 免得被"已删文件"复查反复点名）。"""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="lg_pdf_payload_test_"))
    p = tmp / ("with_image.pdf" if with_image else "text_only.pdf")
    p.write_bytes(cp._synth_pdf(with_image))
    return p


def test_text_only_pdf_is_flagged_and_image_pdf_passes():
    """① 两个方向都要对：**纯文字 PDF 必须判红**、带图 PDF 必须判绿（下限 1/1）。

    怎么改就会红：让 `check()` 恒返回 True（判据失去可证伪性）⇒ 第一个断言红；
    让 `IMG_RE` 写错（比如要求 `/Subtype/Image` 无空格）⇒ 第二个断言红。
    """
    plain = _write_pdf(False)
    ok, msg, got = cp.check(plain, min_objects=1, min_bytes=1)
    assert not ok, f"纯文字 PDF 竟然判绿 —— 这条判据不会红，等于没检查：{msg}"
    assert got["image_objects"] == 0, f"合成纯文字 PDF 里数出了图像对象：{got}"

    with_img = _write_pdf(True)
    ok2, msg2, got2 = cp.check(with_img, min_objects=1, min_bytes=1)
    assert ok2, f"带图 PDF 判红 —— 判据把好件也拦了，会被当成噪音关掉：{msg2}"
    assert got2["image_objects"] == 1 and got2["image_bytes"] == 12, (
        f"带图 PDF 的读数不对（图像对象/字节没数准）：{got2}")


def test_default_floor_rejects_a_tiny_image():
    """② 交付基线那对下限（200 个 / 1 MB）必须**真的拦得住**小图。

    怎么改就会红：把 `cp.DEFAULT_MIN_OBJECTS` / `DEFAULT_MIN_BYTES` 改成 0 或 1
    ⇒ 合成小图（1 个对象 / 12 B）也能过 ⇒ 这条红。
    """
    tiny = _write_pdf(True)
    ok, msg, _ = cp.check(tiny)          # 用模块默认下限
    assert not ok, (
        "按默认下限（" + str(cp.DEFAULT_MIN_OBJECTS) + " 个 / "
        + str(cp.DEFAULT_MIN_BYTES) + " B）判一张合成小图竟然判绿 —— 下限已经不在下限位："
        + msg)


def test_real_delivery_pdf_payload_and_wiring():
    """③ opt-in：真产物读数 ≥ 基线，且 preflight ⑤ **真的调**了这条判据。

    怎么改就会红：把 preflight 里 `check_pdf_payload.check(` 那几行删掉（只留 import）
    ⇒ 接线断言红；交付区那份 PDF 被换回"图像全丢"的坏件 ⇒ 读数断言红。
    """
    src = PREFLIGHT.read_text(encoding="utf-8")
    assert "check_pdf_payload.check(" in src, (
        "tools/preflight.py 里只剩 import、没有真的调用 —— ⑤ 会静默退回「只看字节+sha」那一层")

    if not DECK_PDF.is_file():
        pytest.skip("交付区那份 deck PDF 不在盘上（换机器 / 从零重造链）—— 不是判据坏了")
    ok, msg, got = cp.check(DECK_PDF)
    assert ok, f"交付区 deck PDF 的图像负载低于下限（很可能是坏件）：{msg}"
    assert got["pages"] >= 19, f"页数比 19 还少：{got}"
    assert got["image_objects"] >= 1500, (
        f"图像对象少于历史量级（19 页实测 1,908；坏件 34）—— 疑似又出了坏件：{got}")