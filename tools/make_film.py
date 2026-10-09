# -*- coding: utf-8 -*-
"""E 组第一步：把各段成片**统一重编码**成全片母版 —— 一次转码、单一档位 —— 并跑前置与拼接后的硬判据。

用法：
    python tools/make_film.py                  # 10 镜 179s 版（默认）
    python tools/make_film.py --with-d5        # 追加第 11 镜（D5 观察栏）⇒ ≈192s
    python tools/make_film.py --verify-only    # 只复核已存在的母版

**为什么是「统一重编码」而不是「只拼接」**（2026-10-01 换版，起因与实测见 `docs/DEBT.md` D-43）：
  · 旧法是"逐段转 TS（`-c copy`）→ concat TS → 回 MP4（`-c copy`）"：母版与各段**共用同一份
    码流**，"全片没有新像素"曾是**结构事实**。但共用码流也意味着**共用一个解码器** —— 各段
    编码档一旦不同（现成的 `seg_shot06_pack.mp4` / `seg_shot07_abstain.mp4` 是
    `High 4:4:4 Predictive`，其余 8 段是 `High`），profile 跳变会在**解码**阶段污染画面：
    实测母版 5370 帧里 **810 帧**（S06 全部 330 ＋ S07 全部 480）被解错，成片镜 6 下半部
    于是出现"行行相同、列向缓变"的亮竖带。
  · 新法＝**一次 `libx264` 重编码，每个输入各自一个解码器实例**（N 个 `-i` ＋
    `-filter_complex "…concat=n=N:v=1:a=0"` ＋ 单次编码）：profile 跳变不再有机会污染解码。
  · ⚠️ **别退回 `-f concat` demuxer 做重编码**：它同样只给**一个**解码器，坏画面会被忠实
    转写一遍（2026-10-01 实测 t=102.5s 下半均值 98.62，与旧法同坏）。
  · 于是"全片没有新像素"这条性质**改挂在「段」上**：段仍按 `make_film_segment.py` 的档位出
    （含逐像素定格判据），母版是**中间物** —— 成片 E2 本来就要再重编码一次（烧字幕＋垫 BGM），
    母版自己无损没有意义；它只需保证"**解码后**与各段画面**转码级一致**"（下面的 ②）。

**两张表合一才是全集**（别猜文件名）：`S02–S11` 在 `make_film_segment.SEGMENTS`，
`S01`（从 `fig_star.png` 里逐条"长"出来的那一镜）在 `make_film_shots.shots()`。
两张表的字段名是**同一个隐藏接口**（`tag/out/shot/slot/narration/why/pieces`），
后者的 docstring 就写着"下游拼片脚本按名字取" —— 这里正是那个下游。

**前置判据**（见 `precheck()`）：
  · **硬闸门**（致命：不过就当场红、不产出）：各段 `codec / pix_fmt / 尺寸 / fps / time_base`
    必须**完全一致** —— 编码器真的要求这些一致，不一致还硬编会静默产出坏片。
  · **profile / level**：与本工具的编码档（`FILM_PROFILE` / `FILM_LEVEL`，即 `ENCODE` 的目标档）
    不一致 ⇒ **红字告警**，并对这些段**加跑**「解码路径自洽」判据（下面的 ③）。理由：profile
    跳变正是 D-43 的致病因子 —— 它在**共享解码器**下毁画面、在**逐段独立解码**下无害；所以它
    不该无脑拒绝干活，而该**触发更强的实测**，实测不过才真的不产出。
    ⚠️ 如实登记：现成的 10 段里 S06/S07 **就是** 4:4:4 ⇒ 这条会**如实命中**（告警档），
    这是被测事实、不是误报（round45 回执 §4 把两次自证伪的输出照登）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from make_film_segment import FILM, SEGMENTS, _pixel_diff, _ssim, _utf8_stdout  # noqa: E402
from make_film_shots import shots as factory_shots          # noqa: E402

MASTER_179 = FILM / "film_master_179s.mp4"
MASTER_D5 = FILM / "film_master_192s.mp4"
#: 母版不含第 11 镜（D5 观察栏是 §8.5 ② 的**追加块**，不进 179s 版）
CORE_TAGS = ("S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08", "S09", "S10")
D5_TAG = "S11"
PROBE = ("codec_name", "pix_fmt", "width", "height", "r_frame_rate", "time_base", "profile", "level")
#: 硬闸门比的就是这几个字段：**编码器真的要求**它们一致（不一致硬编 ⇒ 静默坏片）。
HARD_FIELDS = ("codec_name", "pix_fmt", "width", "height", "r_frame_rate", "time_base")
#: 片契约＝本工具统一重编码的目标档。`ENCODE` 里的 `-profile:v/-level` 必须与它**同源**
#: （改了这里就得同步改那里，否则判据会拿一个自己都不产的档去要求输入）。
FILM_PROFILE, FILM_LEVEL = "High", 40
#: 一次统一重编码的参数（单一来源）。`-r 30` 同时是"统一 time_base"的手段：
#: 定帧率输出 ⇒ 母版时间基 1/15360、逐帧 PTS 由输出侧重排，与各段无关。
ENCODE = ("-c:v", "libx264", "-crf", "18", "-preset", "medium",
          "-pix_fmt", "yuv420p", "-r", "30", "-profile:v", "high", "-level", "4.0")
#: ② 的下限。重编码**不再逐像素相同**（旧判据 `_pixel_diff == 0` 在这一版必然红），
#: 但转码级差异必须极小：2026-10-01 实测 20 个抽样点最差 0.998470（S02）。
SSIM_MIN = 0.99


def out_table() -> dict[str, str]:
    """`tag → 成片文件名`：两张段表合一（**单一来源**，不在这里重写一遍文件名）。"""
    return {s["tag"]: s["out"] for s in factory_shots()} | {s["tag"]: s["out"] for s in SEGMENTS}


def ffprobe(path: pathlib.Path, entries: str, stream: str | None = None) -> str:
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "csv=p=0", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe 失败：{path.name}\n{r.stderr[:300]}")
    return (r.stdout or "").strip()


def probe(path: pathlib.Path) -> dict:
    """一段视频的编码指纹（前置判据就比这些字段）。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=" + ",".join(PROBE), "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe 失败：{path.name}\n{r.stderr[:300]}")
    return {k: json.loads(r.stdout)["streams"][0].get(k) for k in PROBE}


def duration(path: pathlib.Path) -> float:
    return float(ffprobe(path, "format=duration") or 0)


def frames(path: pathlib.Path) -> int:
    """精确帧数。

    ⚠️ 必须带 `-count_frames`：不带时 ffprobe 给的是字面量 `N/A`，而它会一路传到 `int()`
    才炸 —— 于是"帧数对不上"这条判据会以**崩溃**的形式失败，看着像工具坏了而不是画面坏了
    （2026-09-26 首次拼 E1 实测踩到，记在这里）。
    """
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe 失败：{path.name}\n{r.stderr[:300]}")
    return int((r.stdout or "0").strip().split(",")[0] or 0)


def streams(path: pathlib.Path) -> list[str]:
    return [s for s in ffprobe(path, "stream=codec_type").splitlines() if s]


def grab(path: pathlib.Path, t: float, out_png: pathlib.Path, seek: str = "fast") -> None:
    """按**时间**抽一帧（`-ss` 定位，不依赖帧号）。

    `seek="fast"` ＝ `-ss` 在 `-i` **前**（快 seek：跳到最近关键帧再解）；
    `seek="out"`  ＝ `-ss` 在 `-i` **后**（从 0 顺序解到那一刻）。
    两种路径问的是同一句话：**"这一帧的画面，取决于解码器从哪儿接手吗？"**
    （D-43 实测：旧母版 102.5s 快 seek 11.03 ／ 输出侧 98.62 —— 同一份码流两个答）。
    只量一种路径，这类静默失败永远量不出来。

    ⚠️ 为什么不用 `make_film_segment._frame()`：它在长文件上会取错帧 —— 实测
    `_frame(master, "eq(n\\,2790)")` 抽出来的竟是**镜 9 的画面**（而镜 9 的 frame 0 落在 4110 帧），
    同一时刻改用 `-ss` 从母版与源段各抽一帧则逐像素差为 **0**。见 `docs/DEBT.md` D-24。
    """
    out_png.parent.mkdir(exist_ok=True)
    if seek == "fast":
        cmd = ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
               "-frames:v", "1", "-y", str(out_png)]
    elif seek == "out":
        cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-ss", f"{t:.3f}",
               "-frames:v", "1", "-y", str(out_png)]
    else:
        raise ValueError(f"seek 只能是 'fast' / 'out'，收到 {seek!r}")
    subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")


def off_contract(probed: list[tuple[str, dict]]) -> list[str]:
    """profile/level **不在片契约内**的段（＝共享解码器下的风险源，要加跑 ③）。"""
    return [t for t, fp in probed
            if (fp.get("profile"), fp.get("level")) != (FILM_PROFILE, FILM_LEVEL)]


def precheck(probed: list[tuple[str, dict]]) -> tuple[list[str], list[str], list[str]]:
    """前置判据（**纯函数**：可以只喂一个文件给它做自证伪）。

    返回 `(致命, 红字告警, 通过项)`。
      · 致命 ⇒ 调用方拒绝产出；
      · 告警 ⇒ 打印 ＋ 记入 checks，**不**拒绝，但调用方要对这些段加跑 ③「解码路径自洽」。
    """
    fatal: list[str] = []
    warn: list[str] = []
    ok: list[str] = []
    ref_tag, ref = probed[0]
    for tag, fp in probed[1:]:
        diff = [k for k in HARD_FIELDS if fp.get(k) != ref.get(k)]
        if diff:
            got = "／".join(f"{k}={fp.get(k)}" for k in diff)
            want = "／".join(f"{k}={ref.get(k)}" for k in diff)
            fatal.append(f"{tag} 编码参数与 {ref_tag} 不一致：{got} ≠ {want}"
                         " —— 拒绝统一重编码（会出坏片）")
    if not fatal:
        ok.append(f"硬闸门：{'/'.join(HARD_FIELDS)} 全等")
    off = off_contract(probed)
    if off:
        warn.append(f"profile/level 不在片契约 {FILM_PROFILE}/{FILM_LEVEL} 内："
                    + "；".join(f"{t}={dict(probed)[t].get('profile')}/{dict(probed)[t].get('level')}"
                                for t in off)
                    + " ⇒ 这些段加跑『解码路径自洽』判据（D-43 的致病因子在共享解码器）")
    else:
        ok.append(f"profile/level 全部＝{FILM_PROFILE}/{FILM_LEVEL}")
    return fatal, warn, ok


def main() -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-d5", action="store_true", help="追加第 11 镜（D5 观察栏）")
    ap.add_argument("--verify-only", action="store_true", help="只复核已有母版，不重拼")
    a = ap.parse_args()

    tags = CORE_TAGS + ((D5_TAG,) if a.with_d5 else ())
    table = out_table()
    missing = [t for t in tags if t not in table]
    if missing:
        print(f"❌ 段表里没有 {missing} —— 别猜文件名，先补表。")
        return 1
    segs = [(t, FILM / table[t]) for t in tags]
    out = MASTER_D5 if a.with_d5 else MASTER_179
    bad: list[str] = []
    checks: list[str] = []

    print(f"===== E1 统一重编码｜{len(tags)} 段 → {out.name} =====")
    absent = [t for t, p in segs if not p.exists()]
    if absent:
        for t in absent:
            print(f"  ❌ {t} 成片不存在：{table[t]}")
        return 1
    probed = [(t, probe(p)) for t, p in segs]

    if not a.verify_only:
        for t, fp in probed:
            print(f"  {t:4s} {table[t]:28s} {fp['codec_name']}/{fp['pix_fmt']} "
                  f"{fp['width']}×{fp['height']} {fp['r_frame_rate']} tb={fp['time_base']} "
                  f"{fp['profile']}/{fp['level']}")

    fatal, warn, ok = precheck(probed)
    checks.extend(ok)
    for w in warn:
        print(f"  ⚠️（告警档·不拒绝产出·改触发③）{w}")
    if fatal:
        for b in fatal:
            print(f"  ❌ {b}")
        return 1

    if not a.verify_only:
        # 一次统一重编码：N 个输入 → `-filter_complex concat` → 单次 libx264。
        # ⚠️ **每个输入各自一个解码器实例**是本步的要害（D-43）：`-f concat` demuxer 只给
        # **一个**解码器，profile 跳变在解码阶段就污染了画面，重编码只是把坏画面忠实转写一遍。
        cmd = ["ffmpeg", "-v", "error"]
        for _, p in segs:
            cmd += ["-i", str(p)]
        chain = "".join(f"[{i}:v]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[v]"
        cmd += ["-filter_complex", chain, "-map", "[v]", "-an", *ENCODE,
                "-movflags", "+faststart", str(out), "-y"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"  ❌ 统一重编码失败：\n{r.stderr[:600]}")
            return 1
        print(f"  统一重编码（{len(segs)} 个输入 → concat → 单次 libx264 crf18）→ {out.name}")

    # ② 重编码后：时长 / 帧数 / 与源段**转码级一致** / 解码路径自洽。
    want_s = sum(duration(p) for _, p in segs)
    got_s = duration(out)
    want_n = sum(frames(p) for _, p in segs)
    got_n = frames(out)
    print(f"  时长 段和 {want_s:.3f}s ／ 母版 {got_s:.3f}s（差 {abs(got_s - want_s):.3f}s）")
    print(f"  帧数 段和 {want_n} ／ 母版 {got_n}")
    if abs(got_s - want_s) > 1e-3:
        bad.append(f"时长对不上：段和 {want_s:.3f}s vs 母版 {got_s:.3f}s")
    if got_n != want_n:
        bad.append(f"帧数对不上：段和 {want_n} vs 母版 {got_n}")
    if not bad:
        checks.extend(["时长＝各段之和（≤1ms）", "帧数＝各段之和"])

    #: 各段是**纯视频轨**（实测量到）⇒ 全片音轨由 E2 用配音 mp3 + BGM 另建，
    #: 所以这里必须断言母版也只有视频轨 —— 一旦哪段悄悄带了音轨，E2 的混音基准就变了。
    if all(streams(p) == ["video"] for _, p in segs):
        checks.append("各段纯视频轨（⇒ 音轨由 E2 另建）")
        if streams(out) != ["video"]:
            bad.append(f"母版出现了额外轨：{streams(out)} —— 与各段不一致")

    #: ⚠️ 为什么不是 `framemd5` 逐行比（2026-09-26 实测踩到，见 `docs/DEBT.md` D-23）：
    #: `framemd5` 按**解码序**输出，而解码序 ≠ 呈现序（B 帧），接缝处重排会让整份清单错位
    #: ⇒ 报出"第 2790 帧不同"这类**假红**。判据要能红，但不能因为**工具的输出顺序**而红。
    #: ⚠️ 为什么也不是"逐像素差 0"（2026-10-01 换版）：统一重编码后母版与源段**不再逐位相同**，
    #: 但转码级差异必须极小 ⇒ 改比 SSIM（实测最差 0.998470，阈值见 `SSIM_MIN`）。
    starts: dict[str, float] = {}
    off = 0.0
    for tag, p in segs:
        starts[tag] = off
        off += duration(p)
    worst_t, worst_s = "", 1.0
    for tag, p in segs:
        d = duration(p)
        for frac in (0.25, 0.75):
            t = frac * d
            a = FILM / "_frames" / f"e1_{tag}_{int(frac * 100)}_m.png"
            b = FILM / "_frames" / f"e1_{tag}_{int(frac * 100)}_s.png"
            grab(out, starts[tag] + t, a)
            grab(p, t, b)
            s = _ssim(a, b)
            print(f"  {tag} 段内 {t:6.3f}s（母版 {starts[tag] + t:7.3f}s）与源段 SSIM {s:.6f}")
            if s < worst_s:
                worst_t, worst_s = f"{tag}@{t:.3f}s", s
    if worst_s < SSIM_MIN:
        bad.append(f"母版与源段在 {worst_t} 处 SSIM {worst_s:.6f} < {SSIM_MIN} ⇒ 画面被改动")
    else:
        checks.append(f"抽样 {len(segs) * 2} 个时刻与源段 SSIM ≥ {SSIM_MIN}"
                      f"（最差 {worst_s:.6f} @ {worst_t}）")

    # ③ 解码路径自洽：母版同一时刻，快 seek 与输出侧 seek 必须给**同一帧**（逐像素差 0）。
    # 只对 profile/level 偏离片契约的段跑（它们才是共享解码器的风险源，D-43）—— 全片跑一遍
    # 每个时刻都要从头解一次，成本不划算；而且**每个输入各自一个解码器**之后，本来就不该有差。
    risky = off_contract(probed)
    if risky:
        print(f"  ③ 解码路径自洽：对 {len(risky)} 个偏离片契约的段加跑（{'/'.join(risky)}）")
        path_bad = 0
        for tag in risky:
            p = dict(segs)[tag]
            for frac in (0.25, 0.75):
                t = frac * duration(p)
                a = FILM / "_frames" / f"e1_{tag}_{int(frac * 100)}_m.png"
                c = FILM / "_frames" / f"e1_{tag}_{int(frac * 100)}_out.png"
                grab(out, starts[tag] + t, c, seek="out")
                n = _pixel_diff(a, c)
                print(f"  {tag} @ {t:6.3f}s 快 seek vs 输出侧 seek 逐像素差 {n}")
                if n:
                    path_bad += 1
                    bad.append(f"{tag}@{t:.3f}s 解码路径不一致（差 {n} 像素）⇒ 母版码流不自洽"
                               "（画面取决于解码器从哪儿接手，D-43 原形）")
        if not path_bad:
            checks.append(f"解码路径自洽：{len(risky)} 个偏离契约段 × 2 时刻逐像素相同")

    if bad:
        print("\n❌ E1 判定：FAIL")
        for b in bad:
            print(f"   - {b}")
        return 1
    print(f"\n✅ E1 判定：PASS（{' / '.join(checks)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
