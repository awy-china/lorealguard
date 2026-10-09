# -*- coding: utf-8 -*-
"""E 组第二步：给**母版**烧字幕、垫 BGM、混配音 —— 出最终成片 `LorealGuard-demo-3min.mp4`。

为什么是独立工具而不是塞进 `make_film.py`
------------------------------------------
`make_film.py` 的产物是**逐帧同一份码流**的母版（`-c copy`，判据 = `framemd5` 逐行相同）。
本步要烧字幕、要混音 ⇒ **必须重编码**，那套判据在这里失效。两者判据不同、失败模式不同、
基准也不同（母版是基准，本步是消费方）⇒ 分开两个工具，各自自洽。

重编码之后，判据靠三条**能红**的撑着
------------------------------------
  ① 帧数 / 时长与母版对齐（差 0 帧、≤1ms）；
  ② **字幕真上屏了**：在三个声明 cue 的中点抽帧，底部字幕带必须出现白像素；没烧上就是 0；
  ③ BGM 按台本 `:155`：-28 LUFS 垫底，且**镜 5–7 窗口内数字静音**（`volume=0` ⇒ ≤ -80 dB）
     ——「镜 5–7 录屏段只留键盘声 + 环境静音，不压音乐」这句原文的化身。

音轨是**另建**的（E1 实测：十一段成片全是纯视频轨）
---------------------------------------------------
全片音频 = 各段配音 mp3 按槽位对齐（`apad` 补满 / `atrim` 截齐）+ BGM。
配音只覆盖槽位前段 ⇒ 补静音；S11 无配音（§8.5 ②）⇒ 整段静音。时间轴因此与 SRT
（绝对时间轴，止于 179.0s）天然对齐，**不需要任何按镜平移**。

用法：
    python tools/make_film_final.py                # 179s 版
    python tools/make_film_final.py --with-d5      # 追加版（+S11）
    python tools/make_film_final.py --verify-only
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from make_film import CORE_TAGS, D5_TAG, MASTER_179, MASTER_D5, duration, frames, grab, out_table  # noqa: E402
from make_film_segment import FILM, SEGMENTS, VOICE, _utf8_stdout                            # noqa: E402
from make_film_shots import shots as factory_shots                                          # noqa: E402

OUT_179, OUT_D5 = FILM / "LorealGuard-demo-3min.mp4", FILM / "LorealGuard-demo-3min-d5.mp4"
SRT = VOICE / "lorealguard-demo.srt"
FONT, SIZE, ALPHA60 = "DengXian", 28, "&H99000000"   # 台本 `:153`：等线 28px 白字 + 60% 黑描边
SILENT_FROM, SILENT_TO = "S05", "S08"                # BGM 归零窗口：本镜起点 → 下一镜起点
SUB_BAND = (0, 950, 1920, 1080)                      # 字幕带（底部横排）
CUE_TS = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d+)\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d+)")


def ffmpeg(args: list[str], v: str = "error") -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-hide_banner", "-v", v, *args], cwd=str(FILM),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def secs(g: list[int] | tuple[int, ...], i: int = 0) -> float:
    return g[i] * 3600 + g[i + 1] * 60 + g[i + 2] + g[i + 3] / 1000


def cues(srt: pathlib.Path) -> list[tuple[float, float, str]]:
    """SRT → [(起, 止, 文本)]；顺手把 CRLF 归一，避免行尾影响解析。"""
    out = []
    for block in re.split(r"\n\s*\n", srt.read_text(encoding="utf-8").replace("\r\n", "\n").strip()):
        lines = block.strip().split("\n")
        m = CUE_TS.match(lines[1]) if len(lines) > 1 else None
        if m:
            g = [int(x) for x in m.groups()]
            out.append((secs(g), secs(g, 4), "\\N".join(lines[2:])))
    return out


def srt_to_ass(c: list[tuple[float, float, str]], ass: pathlib.Path) -> None:
    """字号 / 描边 / 对齐**只写在这一处**，与台本 `:153` 一一对应。"""
    def ts(t: float) -> str:
        return f"{int(t // 3600)}:{int(t // 60) % 60:02d}:{int(t) % 60:02d}.{int(round(t % 1 * 100)):02d}"
    head = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,{FONT},{SIZE},&H00FFFFFF,&H00FFFFFF,{ALPHA60},{ALPHA60},"
        "0,0,0,0,100,100,0,0,1,2,0,2,40,40,40,1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    body = [f"Dialogue: 0,{ts(a)},{ts(b)},Sub,,0,0,0,,{t}" for a, b, t in c]
    ass.write_text("\n".join(head + body) + "\n", encoding="utf-8")


def plan(tags: tuple[str, ...]) -> dict[str, dict]:
    """每段：配音文件名、起点、段长（**全部实测**，不拿槽位去凑）。"""
    narr = {s["tag"]: s.get("narration", "") for s in factory_shots()}
    narr |= {s["tag"]: s.get("narration", "") for s in SEGMENTS}
    table, acc, out = out_table(), 0.0, {}
    for t in tags:
        d = duration(FILM / table[t])
        out[t] = dict(mp3=(VOICE / narr[t] if narr.get(t) else None), start=acc, dur=d)
        acc += d
    return out


def voice_track(p: dict[str, dict], tags: tuple[str, ...], wav: pathlib.Path) -> float:
    """每段配音补到段长再顺序拼；无配音段（S11）用 `anullsrc` 填整段静音。"""
    args, parts = ["-y"], []
    for i, t in enumerate(tags):
        mp3, d = p[t]["mp3"], p[t]["dur"]
        args += ["-i", str(mp3)] if mp3 and mp3.exists() else \
                ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={d:.3f}"]
        parts.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"apad,atrim=0:{d:.3f},asetpts=N/SR/TB[p{i}]")
    filt = ";".join(parts) + ";" + "".join(f"[p{i}]" for i in range(len(tags))) + \
        f"concat=n={len(tags)}:v=0:a=1[out]"
    r = ffmpeg([*args, "-filter_complex", filt, "-map", "[out]", "-c:a", "pcm_s16le", str(wav)])
    if r.returncode != 0:
        raise RuntimeError(f"配音轨拼装失败：\n{r.stderr[:600]}")
    return duration(wav)


def bgm_wav(wav: pathlib.Path, seconds: float, silent: tuple[float, float]) -> None:
    """极简电子底噪（粉噪 → 高通 60 → 低通 400；无旋律、无鼓点），镜 5–7 归零，再归到 -28 LUFS。

    ⚠️ `seed` 是**必须**的（2026-10-05 补，见 `docs/DEBT.md` **D-52**）：lavfi 的 `anoisesrc`
    默认**无种子** ⇒ 每重拼一次就换一套粉噪 ⇒ 成片音轨与整体 sha **不可重放**（违反铁律 9），
    而且让人误以为「画面/配音也变了」。实证：加 seed 后**连拼两次 sha 完全相同**。
    seed 取值只是常量，无语义（口径不变：仍是 -28 LUFS 的 60–400 Hz 底噪）。
    """
    r = ffmpeg(["-y", "-f", "lavfi", "-i", "anoisesrc=c=pink:r=48000:a=0.05:seed=20261005",
                "-t", f"{seconds:.3f}",
                "-af", f"highpass=f=60,lowpass=f=400,"
                       f"volume=volume=0:enable='between(t,{silent[0]:.3f},{silent[1]:.3f})',"
                       f"loudnorm=I=-28:TP=-3:LRA=1", "-c:a", "pcm_s16le", str(wav)])
    if r.returncode != 0:
        raise RuntimeError(f"BGM 生成失败：\n{r.stderr[:600]}")


def lufs(path: pathlib.Path) -> float:
    """整段 integrated LUFS —— 量出来的，不是写死的。

    ⚠️ 只认 `Input Integrated`：链里的 `loudnorm` 会把**输出**重新归一到 -24，
    所以它后面 `volumedetect` 的读数（输出侧）不能当素材电平用 —— 那要用 `max_vol()`。
    """
    r = ffmpeg(["-i", str(path), "-af", "loudnorm=print_format=summary,volumedetect",
                "-f", "null", "-"], v="info")
    log = (r.stderr or "") + (r.stdout or "")
    m = re.search(r"Input Integrated:\s*(-?[\d.]+)", log)
    return float(m.group(1)) if m else float("nan")


def max_vol(path: pathlib.Path, a: float, b: float) -> float:
    """[a,b) 内的 max_volume（dB）—— 纯 `volumedetect`，不掺 `loudnorm`。

    ⚠️ 量"闸门窗口"必须**从窗口内侧**起量（调用方传 a+1 / b-1）：`volumedetect` 取区间
    **最大值**，窗口起点正好压在闸门边缘时，边缘前那点响信号就能把读数顶到 -18.5，
    于是"窗口是否静音"这条判据**永远不会绿**（2026-09-26 实测，见 `docs/DEBT.md` D-25）。
    """
    r = ffmpeg(["-i", str(path), "-af", f"atrim={a:.3f}:{b:.3f},volumedetect",
                "-f", "null", "-"], v="info")
    log = (r.stderr or "") + (r.stdout or "")
    m = re.search(r"max_volume:\s*(-?[\d.]+)", log)
    return float(m.group(1)) if m else float("nan")


def white_in_band(png: pathlib.Path) -> int:
    """底部字幕带里的近白像素数 —— 「字幕真上屏了」的可红判据。"""
    from PIL import Image
    return sum(1 for R, G, B in Image.open(png).convert("RGB").crop(SUB_BAND).getdata()
               if R > 200 and G > 200 and B > 200)


def main() -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-d5", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    a = ap.parse_args()

    tags = CORE_TAGS + ((D5_TAG,) if a.with_d5 else ())
    master, out = (MASTER_D5, OUT_D5) if a.with_d5 else (MASTER_179, OUT_179)
    bad, checks = [], []
    print(f"===== E2 字幕 + BGM + 混音｜{len(tags)} 段 → {out.name} =====")
    if not master.exists():
        print(f"❌ 母版不存在：{master.name} —— 先跑 tools/make_film.py")
        return 1

    c = cues(SRT)
    p = plan(tags)
    silent = (p[SILENT_FROM]["start"], p[SILENT_TO]["start"])       # 镜 5–7 = [S05起, S08起)
    ass, voice, bgm = FILM / "_film_subs.ass", FILM / "_voice_track.wav", FILM / "_bgm.wav"
    total = sum(v["dur"] for v in p.values())

    if not a.verify_only:
        srt_to_ass(c, ass)
        print(f"  字幕 {len(c)} 条 → {ass.name}（{FONT} {SIZE}px 白字 + 60% 黑描边，底部横排）")
        if len(c) != 75:
            bad.append(f"字幕条数 {len(c)} ≠ 75（SRT 变了 ⇒ 先跑 voice/make_srt.py 复核；"
                               f"2026-10-05 口径变更后为 75，见 docs/DEBT.md D-51）")
        vt = voice_track(p, tags, voice)
        bgm_wav(bgm, total, silent)
        print(f"  配音轨 {vt:.3f}s（片长 {total:.3f}s）／BGM 镜 5–7 窗口 {silent[0]:.3f}–{silent[1]:.3f}s 归零")
        r = ffmpeg(["-y", "-i", str(master), "-i", str(voice), "-i", str(bgm),
                    "-filter_complex", "[1:a][2:a]amix=inputs=2:duration=first:normalize=0[a]",
                    "-map", "0:v", "-map", "[a]", "-vf", f"ass={ass.name}",
                    "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out.name])
        if r.returncode != 0:
            print(f"  ❌ 烧字幕/混音失败：\n{r.stderr[:800]}")
            return 1
        print(f"  烧字幕 + 混音 → {out.name}")

    fm, ff, fd = frames(master), frames(out), duration(out)
    print(f"  帧数 母版 {fm} ／ 成片 {ff}；时长 母版 {duration(master):.3f}s ／ 成片 {fd:.3f}s")
    if fm != ff:
        bad.append(f"帧数变了：母版 {fm} vs 成片 {ff}")
    if abs(fd - duration(master)) > 1e-3:
        bad.append(f"时长变了：母版 {duration(master):.3f}s vs 成片 {fd:.3f}s")
    if not bad:
        checks.append("帧数/时长与母版逐帧对齐")

    probes = [c[0], c[len(c) // 2], c[-1]]
    for i, (s0, s1, txt) in enumerate(probes):
        t = (s0 + s1) / 2
        png = FILM / "_frames" / f"sub_probe{i}.png"
        grab(out, t, png)          # ⚠️ 用 `-ss` 按时间抽帧，不用 `_frame()`（见 D-24）
        w = white_in_band(png)
        print(f"  cue「{txt[:14]}」中点 {t:7.3f}s（第 {round(t * 30)} 帧）底部带白像素 {w}")
        if w == 0:
            bad.append(f"{t:.3f}s（第 {round(t * 30)} 帧）字幕带无白像素 ⇒ 字幕没烧上屏")
    if not bad:
        checks.append(f"字幕上屏（{len(probes)} 个 cue 中点采样，底部白像素 > 0）")

    lv = lufs(bgm)
    vol = max_vol(bgm, silent[0] + 1.0, silent[1] - 1.0)      # 从窗口内侧起量（见 max_vol 注释）
    print(f"  BGM 实测 {lv:.2f} LUFS（目标 -28）；镜 5–7 窗口 max_volume {vol:.1f} dB"
          f"（量 [{silent[0] + 1:.0f},{silent[1] - 1:.0f}) 内侧区间）")
    if abs(lv + 28) > 1.5:
        bad.append(f"BGM 响度 {lv:.2f} LUFS 偏离 -28（台本 `:155`）")
    if vol > -80:
        bad.append(f"镜 5–7 窗口 BGM 未静音（max_volume {vol:.1f} dB，应 ≤ -80）")
    if not bad:
        checks.extend(["BGM ≈ -28 LUFS", "镜 5–7 窗口 BGM 数字静音"])

    if bad:
        print("\n❌ E2 判定：FAIL")
        for b in bad:
            print(f"   - {b}")
        return 1
    print(f"\n✅ E2 判定：PASS（{' / '.join(checks)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())