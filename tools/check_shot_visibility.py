#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_shot_visibility.py —— 独立可见性核对（只核对，不改任何判定）

用途：给一段录像，回答"某个字串**到底**在第几秒出现在画面里"。
- 抽帧 → Windows 本机 OCR（不依赖云端 vision，本机 OCR 无额度问题）→ 逐串报首次出现的时刻。
- **不采信任何生成器的自述**：这是一条只吃产物的核对路径。

用法：
  python tools/check_shot_visibility.py <video.mp4> \
      --expect "观察维度 color_edge" --expect "观察维度 lighting" \
      [--times 1,3,5,7,9,11,13] [--out output/_vis] [--ocr-script <ps1>]

退出码：0 = 所有期望串都在某一帧里出现；4 = 有串一次都没出现；2 = 用法/依赖/工具自身出错。

⚠️ 三条实测坑（都踩过）：
  1. **本机 OCR 会在 CJK 字符之间插空格**（"观 察 维 度 color_edge"）⇒ 比对前两侧都去掉
     全部空白再匹配；不做这一点，任何中文串都永远匹配不上。
  2. **给 Windows OCR 的路径必须绝对 + 反斜杠**。传相对路径时 OCR 不报错、只是读不出字
     （输出只剩一个 BOM）⇒ 旧版据此对**明明在画面里的**串全报 MISS（假红）。
  3. 因此 `main()` 里有一条自检：**抽帧成功但所有帧一个字都没读出来**时，明确报"工具坏"，
     以 exit 2 退出，**绝不报 MISS** —— 假红比不测更害人。
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

DEFAULT_OCR = (r"C:\Users\AWY\AppData\Local\hermes\skills\productivity"
               r"\windows-screenshot-ocr\scripts\ocr_screenshot.ps1")
MIN_TEXT = 20          # 一帧 OCR 少于这么多字 ⇒ 视为"没读出来"


_FW = {c: chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)}   # 全角标点/数字 → 半角
_FW[0x3000] = 0x20                                          # 全角空格 → 半角
_SKIP = "()[]{}<>（）［］｛｝【】〈〉《》"


def norm(s: str) -> str:
    """归一化：全角→半角、去掉所有空白与括号、去 BOM。

    为什么必须这么狠：本机 OCR 与云端 VL 都会**在两个方向**上读歪标点 ——
      · CJK 字符之间被插空格（"观 察 维 度 color_edge"）；
      · 全角 `（）` 被读成半角 `()`，小数点 `．` 被读成 `.`，反之亦然（实测
        `177.58` 与 `22.418` 在 OCR 原文里是 `177．58` / `22．418`）。
    只去空格不够：实测同一带里 `观察维度 color_edge（ok）` 读回吻合、
    `观察维度 lighting（ok）` 却读成半角括号 ⇒ 期望串**时对时错**（flaky），
    而页面模板 `demo/index.html:517` 用的确实是全角 `（`。⇒ 两边都归一化才对得上。
    """
    t = str(s).translate(_FW).replace("\ufeff", "")
    return "".join(ch for ch in t if ch not in _SKIP and not ch.isspace())


def extract(video: pathlib.Path, times: list[float], outdir: pathlib.Path) -> list[pathlib.Path]:
    outdir = outdir.resolve()          # 坑 2：绝对路径
    outdir.mkdir(parents=True, exist_ok=True)
    frames = []
    for t in times:
        png = outdir / f"vis_{t:g}s.png"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{t:g}", "-i", str(video.resolve()),
                        "-frames:v", "1", str(png)], check=True)
        frames.append(png)
    return frames


def ocr(png: pathlib.Path, ocr_script: pathlib.Path, outdir: pathlib.Path) -> str:
    """调 Windows 本机 OCR。路径必须**绝对 + 反斜杠**（WinRT API 不认正斜杠）。"""
    txt = (outdir / (png.stem + ".txt")).resolve()
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(ocr_script),
                    "-ImagePath", str(png.resolve()).replace("/", "\\"),
                    "-OutFile", str(txt).replace("/", "\\")],
                   check=True, capture_output=True)
    raw = txt.read_bytes()
    for enc in ("utf-8", "utf-16", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--expect", action="append", required=True,
                    help="必须出现在画面里的字串；可重复")
    ap.add_argument("--times", default="1,3,5,7,9,11,13")
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("output/_vis"))
    ap.add_argument("--ocr-script", type=pathlib.Path, default=pathlib.Path(DEFAULT_OCR))
    a = ap.parse_args()

    if not a.video.exists():
        print(f"[vis] 视频不存在：{a.video}")
        return 2
    if not a.ocr_script.exists():
        print(f"[vis] OCR 脚本不存在（--ocr-script 可覆盖）：{a.ocr_script}")
        return 2

    times = [float(x) for x in str(a.times).split(",") if x.strip()]
    outdir = a.out.resolve()
    frames = extract(a.video, times, outdir)
    texts = {t: ocr(p, a.ocr_script, outdir) for t, p in zip(times, frames)}

    # 自检（坑 3）：抽帧成功但一个字都没读出来 ⇒ 是工具/路径问题，不是画面问题。
    if all(len(norm(x)) < MIN_TEXT for x in texts.values()):
        print("[vis] 工具自身出错：抽帧成功，但 OCR 在所有帧上都没读出文字（路径/引擎问题）")
        print("[vis] ⇒ 不要据此判 MISS。请检查 --ocr-script 与抽帧产物：")
        for t, p in zip(times, frames):
            print(f"     t={t}s {p} ({p.stat().st_size if p.exists() else 'MISSING'} B)")
        return 2

    lines = [f"[vis] 视频：{a.video}（抽 {len(times)} 帧：{a.times}）", ""]
    missing = []
    for want in a.expect:
        w = norm(want)
        hit = next((t for t in times if w in norm(texts[t])), None)
        mark = "OK " if hit is not None else "MISS"
        lines.append(f"  [{mark}] 首次出现 t={hit if hit is not None else '-'}s | {want}")
        if hit is None:
            missing.append(want)

    lines.append("")
    if missing:
        lines.append(f"[vis] FAIL：{len(missing)}/{len(a.expect)} 个串**一次都没出现**")
        for m in missing:
            lines.append(f"  MISS: {m}")
        # 缺串时把各帧 OCR 原文落盘，便于人判"是没出现还是没读清"
        for t in times:
            lines.append(f"  --- t={t}s 原文 ---")
            lines.append(norm(texts[t])[:600])
    else:
        lines.append(f"[vis] PASS：{len(a.expect)}/{len(a.expect)} 个串都在画面里出现过")

    outdir.mkdir(parents=True, exist_ok=True)
    report = outdir / "visibility_report.txt"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[vis] 报告：{report}")
    return 4 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())