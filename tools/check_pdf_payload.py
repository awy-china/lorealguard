#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`tools/check_pdf_payload.py` —— 交付 PDF 的「图像负载在场」判据（纯标准库，零依赖，只读）。

**为什么存在（真伤现场）**：2026-09-29 重出 deck 时，`export_pdf.py` **exit 0**、
文件也正常写出来了，体积只比历史小 36%（2,398,482 B vs 3,739,128 B）——
看着"正常"，实测**图像对象从 1,908 个掉到 34 个、图像字节从 3.09 MB 掉到 22.6 KB**
（PowerPoint COM 挂到了上一个进程遗留的实例上）。⇒ 退出码、体积、页数**都判不出**这种坏件，
只有数"PDF 里到底嵌了几个图像对象、多少图像字节"才判得出。

用法：
    python tools/check_pdf_payload.py <pdf> [--min-image-objects 200] [--min-image-bytes 1000000]
    python tools/check_pdf_payload.py --self-check      # 合成两个 PDF，证明本判据会绿也会红

退出码：0 = 达标；2 = 疑似丢图像 / 文件不可读；3 = 用法错。

⚠️ 判据是**下限**（floor），不是等值断言 —— 它守的是"图像负载还在场"这件事；
   逐字节冻结另由 `tools/preflight.py` 的 ⑤ 用 sha16 完成。两者是**两层**，缺一不可：
   只有 sha 相等才叫冻结，只有负载在场才叫"这份 PDF 还能用"。
"""
import argparse
import re
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: `/Subtype /Image` 的容差写法（PowerPoint 写 "/Subtype /Image"，别的写 "/Subtype/Image"）
IMG_RE = re.compile(rb"/Subtype\s*/Image")
#: `/Type /Page`（注意排除 `/Type /Pages` 的页树根）
PAGE_RE = re.compile(rb"/Type\s*/Page(?![s])")
#: 对象字典里各字段（限在同一对象内探测，见 `_dict_window`）
FIELD_RE = {
    "width": re.compile(rb"/Width\s+(\d+)"),
    "height": re.compile(rb"/Height\s+(\d+)"),
    "length": re.compile(rb"/Length\s+(\d+)"),
    "filter": re.compile(rb"/Filter\s*/(\w+)"),
}

#: 我们的 19 页 deck 实测：1,908 个图像对象 / 3,086,157 B 图像字节（2026-09-29）。
#: 历史 18 页：1,556 / 2,848,919 B。坏件：34 个 / 22,633 B。
#: ⇒ 下限取历史值的 ~1/3（3 倍余量），坏件在任何一侧都被判红。
DEFAULT_MIN_OBJECTS = 200
DEFAULT_MIN_BYTES = 1_000_000


def _dict_window(raw: bytes, at: int, span: int = 600) -> bytes:
    """取该对象字典的近似窗口：从匹配点向后到 `stream`/`endobj` 为止（限 span 字节）。"""
    tail = raw[at:at + span]
    for stop in (b"stream", b"endobj"):
        i = tail.find(stop)
        if i != -1:
            tail = tail[:i]
    return tail


def scan_pdf(path: Path) -> dict:
    """扫一份 PDF 字节流：图像对象数 / 图像字节数 / 页数 / 尺寸清单。只读，零依赖。"""
    raw = path.read_bytes()
    objs = []
    for m in IMG_RE.finditer(raw):
        win = _dict_window(raw, m.end())
        length = FIELD_RE["length"].search(win)
        width = FIELD_RE["width"].search(win)
        height = FIELD_RE["height"].search(win)
        flt = FIELD_RE["filter"].search(win)
        objs.append({
            "bytes": int(length.group(1)) if length else 0,
            "w": int(width.group(1)) if width else 0,
            "h": int(height.group(1)) if height else 0,
            "filter": flt.group(1).decode("ascii", "replace") if flt else "?",
        })
    return {
        "path": str(path),
        "file_bytes": len(raw),
        "image_objects": len(objs),
        "image_bytes": sum(o["bytes"] for o in objs),
        "pages": len(PAGE_RE.findall(raw)),
        "biggest": sorted(objs, key=lambda o: -o["bytes"])[:5],
    }


def check(path: Path, min_objects: int = DEFAULT_MIN_OBJECTS,
          min_bytes: int = DEFAULT_MIN_BYTES) -> tuple[bool, str, dict]:
    """下限判据。返回 (是否达标, 人话结论, 读数)。"""
    try:
        got = scan_pdf(path)
    except OSError as exc:
        return False, "读不出这份 PDF：" + str(exc), {}
    head = ("pages=" + str(got["pages"]) + " image_objects=" + str(got["image_objects"])
            + " image_bytes=" + str(got["image_bytes"]) + " file_bytes=" + str(got["file_bytes"]))
    if got["image_objects"] < min_objects or got["image_bytes"] < min_bytes:
        return False, ("疑似**丢图像**的坏 PDF：实测 [" + head + "] < 下限 [image_objects≥"
                       + str(min_objects) + " image_bytes≥" + str(min_bytes) + "] "
                       "—— 退出码与体积都判不出这类坏件，必须看负载本身"), got
    return True, head, got


# ---------------------------------------------------------------- 自证伪（--self-check）

def _synth_pdf(with_image: bool) -> bytes:
    """造一份最小 PDF：`with_image=True` 时多挂一个 8x8 FlateDecode 图像 XObject。"""
    img_obj = b""
    if with_image:
        payload = b"\x78\x9c\x63\x60\x60\x60\x00\x00\x00\x04\x00\x01"  # 一小段合法 zlib 流
        img_obj = (b"5 0 obj\n<< /Type /XObject /Subtype /Image /Width 8 /Height 8 "
                   b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
                   b"/Length " + str(len(payload)).encode() + b" >>\nstream\n" + payload
                   + b"\nendstream\nendobj\n")
    return (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] "
            b"/Contents 4 0 R /Resources << >> >>\nendobj\n"
            b"4 0 obj\n<< /Length 0 >>\nstream\n\nendstream\nendobj\n"
            + img_obj + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n")


def self_check() -> int:
    """两个合成 PDF：带图的必须判绿、纯文字的必须判红（下限放到 1 才能逼出红）。"""
    print("=== 自证伪：这条判据会不会红 ===")
    tmp = Path(tempfile.mkdtemp(prefix="lorealguard_pdf_payload_"))
    ok_cases = []
    with_img = tmp / "with_image.pdf"
    with_img.write_bytes(_synth_pdf(True))
    plain = tmp / "text_only.pdf"
    plain.write_bytes(_synth_pdf(False))

    green, msg, _ = check(with_img, min_objects=1, min_bytes=1)
    print(("  ✅ " if green else "  ❌ ") + "带图 PDF（下限 1/1）应判绿 -> " + msg)
    ok_cases.append(green)

    red, msg, _ = check(plain, min_objects=1, min_bytes=1)
    print(("  ✅ " if not red else "  ❌ ") + "纯文字 PDF 应判红 -> " + msg)
    ok_cases.append(not red)

    # 坏件的真实量级对照：把带图 PDF 的下限抬到历史水平 ⇒ 必须红（合成图只有几十字节）
    red2, msg2, _ = check(with_img, min_objects=DEFAULT_MIN_OBJECTS,
                          min_bytes=DEFAULT_MIN_BYTES)
    print(("  ✅ " if not red2 else "  ❌ ") + "按历史下限（200/1MB）判合成小图应判红 -> " + msg2)
    ok_cases.append(not red2)

    for p in (with_img, plain):
        p.unlink(missing_ok=True)
    try:
        tmp.rmdir()
    except OSError:
        pass
    good = all(ok_cases)
    print("自证伪结论：" + ("✅ 三种情形全部符合预期（这条检查会红）" if good else "❌ 不符合预期"))
    return 0 if good else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools/check_pdf_payload.py",
                                 description="交付 PDF 的图像负载下限判据（只读，零依赖）")
    ap.add_argument("pdf", nargs="?", help="要检查的 PDF")
    ap.add_argument("--min-image-objects", type=int, default=DEFAULT_MIN_OBJECTS)
    ap.add_argument("--min-image-bytes", type=int, default=DEFAULT_MIN_BYTES)
    ap.add_argument("--self-check", action="store_true", help="用合成 PDF 证明本判据会红也会绿")
    args = ap.parse_args(argv)

    if args.self_check:
        return self_check()
    if not args.pdf:
        ap.error("要么给一个 PDF，要么 --self-check")

    path = Path(args.pdf)
    if not path.is_file():
        print("找不到 " + str(path))
        return 3
    ok, msg, got = check(path, args.min_image_objects, args.min_image_bytes)
    print(("✅ " if ok else "❌ ") + path.name + " -> " + msg)
    for o in got.get("biggest", []):
        print("     " + str(o["w"]) + "x" + str(o["h"]) + "  " + o["filter"]
              + "  " + str(o["bytes"]) + " B")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())