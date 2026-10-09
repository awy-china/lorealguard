# -*- coding: utf-8 -*-
"""成片内容审计：把两条"只亲跑过一次"的结论变成**可复算的读数**。

这工具回答两个答辩要念的问题
------------------------------
  ① **全片的风险红出现在哪里、有多大**（每条红都在预料之内 ⇒ 没有意外的红）
     —— 逐段扫 `output/film/seg_shot*.mp4`，数红/琥珀掩膜像素。
  ② **字幕有没有贴住配音、镜尾有没有悬空**（2026-10-05 口径变更：旧的「83 条 / 覆盖整片 /
     零空档」基线随 `docs/DEBT.md` **D-51** 作废；现基线 75 条 / 覆盖 93.1% / 空档只落镜尾）

判色阈值（口径单一来源，勿改）
------------------------------
出处：`D:/deliver/compete/loreal-video-script.md` §8.5⑤⑥ + `docs/DEBT.md` **D-18**。
不在这里调阈值 —— 阈值改了，"全片红在哪"这句话就换了口径（铁律 1）。

D-18 铁律：不许全球数像素
--------------------------
画面里有两处**自带红**不是风险红，必须白名单化（见 `tests/test_film_content.py` §B）：
镜 1 的图表自带红、镜 4 的产品页等级标签（`demo/index.html:336` 的 `#ff6b6b`）。
本工具只负责**如实数**，谁算"风险红"由调用方的白名单决定。

抽帧口径（D-24）
----------------
一次 `ffmpeg` 直通取全部帧（`fps=2,scale=960:540`，rawvideo 从 stdout 分块读）。
**禁止**逐帧 `-ss` / `-vf select='eq(n,N)'` —— 帧号寻址在长文件上会取错帧（D-24）。

本工具**只读不写**：不往盘上落任何审计产物，结果只打印到 stdout。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

import numpy as np

# --------------------------------------------------------------------------- 判色阈值

#: 风险红：`R >= 150 and G <= 90 and B <= 90`（闭区间）
#: 出处 `loreal-video-script.md` §8.5⑥ + `docs/DEBT.md` D-18。
RED = (150, 90, 90)
#: 琥珀：`R >= 170 and 100 <= G <= 205 and B <= 95`（闭区间）
#: 出处同上 §8.5⑤。G 的**下界**单列成常量 —— 3 元组放不下它，别把它丢进掩膜里硬编码。
AMBER = (170, 205, 95)
AMBER_G_MIN = 100

#: 抽帧与判色的统一几何：960×540（缩放口径不同 ⇒ 别拿全分辨率的数当阈值）
FRAME_W, FRAME_H = 960, 540
FRAME_BYTES = FRAME_W * FRAME_H * 3

#: 成片段目录（工具自己的位置往上找仓库根）
FILM_DIR = pathlib.Path(__file__).resolve().parents[1] / "output" / "film"

#: 成片链里的段数：10 段（`seg_shot11_*` 是 D5 追加块，**不在** 179s 成片链里）
FILM_SEGMENTS = 10
#: S11 前缀（排除用）
EXCLUDED_PREFIX = "seg_shot11_"

#: 成片时长（秒）—— `loreal-video-script.md` 的口径
FILM_DURATION = 179.000000

#: 字幕候选路径：口径出处写的是 `voice/lorealguard-demo.srt`，实际产物落在 `voice/out/`。
#: 两处都看，命中即用（缺则 skip，不造假红）。
SRT_CANDIDATES = (
    pathlib.Path("D:/deliver/compete/voice/lorealguard-demo.srt"),
    pathlib.Path("D:/deliver/compete/voice/out/lorealguard-demo.srt"),
)

#: 台本候选路径（镜标题行 = 槽位；口径变更后用来判「空档只许落在镜尾」）
SCRIPT_CANDIDATES = (
    pathlib.Path("D:/deliver/compete/loreal-video-script.md"),
)

#: edge-tts 真实句界 SRT 所在目录 / 音色（`make_voice.py --all` 落的）——
#: 字幕末条必须**贴住**它给的配音实测终点（2026-10-05 口径变更，见 DEBT D-51）
ANCHOR_DIR = pathlib.Path("D:/deliver/compete/voice/out")
ANCHOR_VOICE = "zh-CN-YunxiNeural"

_TS = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


# --------------------------------------------------------------------------- 目录 / 素材


def segments(film_dir: pathlib.Path = FILM_DIR) -> list[pathlib.Path]:
    """成片链的段：`seg_shot*.mp4` 里**排除** `seg_shot11_*`，按文件名排序 ⇒ 恰好 10 段。

    改哪儿会变：把 `EXCLUDED_PREFIX` 去掉 ⇒ 变成 11 段；不排序 ⇒ 段序随机。
    """
    d = pathlib.Path(film_dir)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("seg_shot*.mp4")
                  if not p.name.startswith(EXCLUDED_PREFIX))


def find_srt() -> pathlib.Path | None:
    """第一个真实存在的字幕文件；都不在 ⇒ `None`（调用方 skip，不造假红）。"""
    for p in SRT_CANDIDATES:
        if p.is_file():
            return p
    return None


def find_script() -> pathlib.Path | None:
    """台本（镜标题行给槽位）；不在 ⇒ `None`。"""
    for p in SCRIPT_CANDIDATES:
        if p.is_file():
            return p
    return None


def shot_starts(script: pathlib.Path) -> list:
    """台本镜标题行 → [(镜号, 镜起点秒, 槽位秒)]（与 `voice/make_srt.py` 同一条正则）。"""
    out = []
    for ln in script.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^### 镜 (\d+)｜(\d+):(\d+)–(\d+):(\d+)（(\d+)s / (\d+) 字）", ln)
        if m:
            out.append((int(m.group(1)), int(m.group(2)) * 60 + int(m.group(3)), int(m.group(6))))
    return out


def subtitle_voice_alignment(srt: pathlib.Path, script: pathlib.Path,
                             anchor_dir: pathlib.Path = ANCHOR_DIR,
                             voice: str = ANCHOR_VOICE) -> dict:
    """**字幕是否贴住配音** —— 2026-10-05 口径变更后的核心判据（DEBT D-51）。

    旧口径（**已作废**）：字幕按字数铺满槽位 ⇒「覆盖 100% / 零空档」被当成目标。
    但槽位是**排片窗口，比配音长**（十镜合计留白 10.9s）⇒ 字幕天然比配音慢：
    镜尾悬空最多 2.12s（镜 8）。旧音色离槽位近、看不出来；2026-10-05 云希 +12%
    之后留白变大，用户实看即成片报「视频字幕和配音不对应」。

    现口径：字幕锚在 edge-tts 真实句界上 ⇒ 判据换成：
      - `last_off`  = max|镜内末条终点 − 该镜配音实测终点|（秒）—— 就是用户看到的那件事；
      - `gap_in_tail` = 空档是否**只**落在镜尾（镜内不得有 >0.05s 空洞）；
      - `rows[*].slack` = 末条到槽位末的留白（信息栏，供报告）。

    改哪儿会变：拿旧「铺满槽位」的 SRT 喂进来 ⇒ `last_off` = 2.12s（红）；
    把 `gap_in_tail` 的判据改成「只看向邻条」⇒ 镜内空洞漏网。
    缺台本/缺边界文件 ⇒ `{"ok": False, ...}`（调用方 skip，不造假红）。
    """
    st = shot_starts(script)
    cs = cues(srt)
    if not st or not cs:
        return {"ok": False, "why": "台本或字幕读不到"}
    rows, last_off, gap_in_tail = [], 0.0, True
    for no, a, slot in st:
        b = a + slot
        own = sorted((s, e) for s, e in cs if a - 1e-6 <= s < b - 1e-6)
        if not own:
            return {"ok": False, "why": f"镜 {no} 没有字幕"}
        ap_ = pathlib.Path(anchor_dir) / f"shot{no}_{voice}.srt"
        if not ap_.is_file():
            return {"ok": False, "why": f"缺边界文件 {ap_.name}（先跑 voice/make_voice.py --all）"}
        voice_end = a + max(e for _, e in cues(ap_))
        cue_last = max(e for _, e in own)
        last_off = max(last_off, abs(cue_last - voice_end))
        for i in range(1, len(own)):
            if own[i][0] - own[i - 1][1] > 0.05:
                gap_in_tail = False
        rows.append({"shot": no, "slot": slot, "cue_last": cue_last,
                     "voice_end": voice_end, "slack": b - cue_last})
    return {"ok": True, "rows": rows, "last_off": last_off, "gap_in_tail": gap_in_tail}


# --------------------------------------------------------------------------- 取帧


def decode(path: pathlib.Path, fps: int = 2):
    """一次 ffmpeg 直通取帧，按 `FRAME_BYTES` 分块 yield `(540, 960, 3)` uint8 帧。

    为什么不是逐帧 `-ss`：又慢，又踩 D-24（帧号寻址在长文件上取错帧）。
    """
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path),
        "-vf", f"fps={fps},scale={FRAME_W}:{FRAME_H}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(FRAME_BYTES)
            if len(buf) < FRAME_BYTES:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(FRAME_H, FRAME_W, 3)
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        proc.wait()


# --------------------------------------------------------------------------- 判色


def red_mask(frame: np.ndarray) -> np.ndarray:
    """风险红掩膜（2D bool）。闭区间：`R >= 150 and G <= 90 and B <= 90`。"""
    r = frame[:, :, 0].astype(np.int16)
    g = frame[:, :, 1].astype(np.int16)
    b = frame[:, :, 2].astype(np.int16)
    return (r >= RED[0]) & (g <= RED[1]) & (b <= RED[2])


def amber_mask(frame: np.ndarray) -> np.ndarray:
    """琥珀掩膜（2D bool）。闭区间：`R >= 170 and 100 <= G <= 205 and B <= 95`。"""
    r = frame[:, :, 0].astype(np.int16)
    g = frame[:, :, 1].astype(np.int16)
    b = frame[:, :, 2].astype(np.int16)
    return (r >= AMBER[0]) & (g >= AMBER_G_MIN) & (g <= AMBER[1]) & (b <= AMBER[2])


def count_colors(frame: np.ndarray) -> tuple[int, int]:
    """返回 `(red_px, amber_px)` —— 纯 numpy 布尔掩膜求和。"""
    return int(red_mask(frame).sum()), int(amber_mask(frame).sum())


def blobs(mask: np.ndarray, min_px: int = 100) -> int:
    """4 连通块计数，只数 `>= min_px` 的块（`min_px=100` ⇒ 噪点不算一块）。

    改哪儿会变：把 4 邻接换成 8 邻接 ⇒ 对角相邻的两块会被并成一块，镜 2 的两把红叉读成 1。
    """
    m = np.asarray(mask, dtype=bool)
    if m.ndim != 2:
        raise ValueError(f"blobs() 要 2D 掩膜，实得 {m.shape}")
    alive = {(int(y), int(x)) for y, x in np.argwhere(m)}
    n = 0
    while alive:
        stack = [alive.pop()]
        size = 1
        while stack:
            y, x = stack.pop()
            for nb in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if nb in alive:
                    alive.discard(nb)
                    stack.append(nb)
                    size += 1
        if size >= min_px:
            n += 1
    return n


def scan_peak(path: pathlib.Path, fps: int = 2):
    """**单次解码**返回 `(rows, peak_frame)`。

    `rows` 同 `scan()`；`peak_frame` 是红像素最多的那一帧（要跑 `blobs()` 自检时用）。
    合在一起是因为"再解一遍找峰值帧"要重跑一次 ffmpeg —— 慢，且两次解码结果必须一致。
    """
    rows: list[tuple[float, int, int]] = []
    best_red, peak_frame = -1, None
    for i, f in enumerate(decode(path, fps)):
        r, a = count_colors(f)
        rows.append((i / fps, r, a))
        if r > best_red:
            best_red, peak_frame = r, f
    return rows, peak_frame


def scan(path: pathlib.Path, fps: int = 2) -> list[tuple[float, int, int]]:
    """逐帧 `(时刻秒, red_px, amber_px)`；时刻 = 帧序 / fps（第 0 帧 = 0.0s）。"""
    rows, _ = scan_peak(path, fps)
    return rows


# --------------------------------------------------------------------------- 字幕


def _secs(stamp: str) -> float:
    m = _TS.search(stamp)
    if m is None:
        raise ValueError(f"时间戳解不动：{stamp!r}")
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def cues(srt: pathlib.Path) -> list[tuple[float, float]]:
    """解出 `[(起, 止)]` 并排序。CRLF / BOM 都归一后再解析（SRT 是 CRLF 存的）。"""
    raw = pathlib.Path(srt).read_bytes().decode("utf-8-sig")
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    out: list[tuple[float, float]] = []
    for block in text.strip().split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        arrow = [ln for ln in lines if "-->" in ln]
        if not arrow:
            continue
        a, b = arrow[0].split("-->")
        out.append((_secs(a), _secs(b)))
    return sorted(out)


def subtitle_stats(srt: pathlib.Path, duration: float) -> dict:
    """字幕覆盖统计。口径（**别改**，改了答辩念的数字就换口径）：

    - `n`        = 条数；
    - `first`    = 最早起点；`last` = 最晚终点；
    - `max_dur`  = 最长一条的时长（台本规则 ≤3.5s）；
    - `coverage` = **各条 `[起,止)` 区间的并集 ∩ [0, duration] 的长度 / duration**
                   （并集 ⇒ 重叠只算一次；半开区间 ⇒ 首尾相接的两条不重叠）；
    - `max_gap`  = 相邻条之间 `起[i+1] - 止[i]` 的最大值（**负值按 0 计**），
                   且**末尾到 duration 的空档也算一个 gap**；
    - `overlaps` = `起[i+1] < 止[i]` 的次数。

    ⚠️ 2026-10-05 口径变更（DEBT D-51）：本函数**没改**，但**基线换了** ——
    旧基线「83 条 / 覆盖 100.0% / 最大空档 0.0」属于「字幕铺满槽位」那个作废口径；
    现口径锚在真实语音上 ⇒ 75 条 / 覆盖 93.1% / 最大空档 2.17s（空档只落镜尾）。
    「覆盖率高」不再是目标，「贴住配音」才是 —— 见 `subtitle_voice_alignment`。

    改哪儿会变：`coverage` 改成"各条长度之和" ⇒ 有重叠时虚高 > 1；
    `max_gap` 不把负值按 0 计 ⇒ 重叠处会把最大值拉低。
    """
    cs = cues(srt)
    if not cs:
        return {"n": 0, "first": 0.0, "last": 0.0, "max_dur": 0.0, "coverage": 0.0,
                "max_gap": float(duration), "overlaps": 0}

    first, last = cs[0][0], max(b for _, b in cs)

    overlaps = sum(1 for i in range(1, len(cs)) if cs[i][0] < cs[i - 1][1])
    max_gap = max(0.0, cs[0][0])
    for i in range(1, len(cs)):
        max_gap = max(max_gap, max(0.0, cs[i][0] - cs[i - 1][1]))
    max_gap = max(max_gap, max(0.0, duration - last))

    # 区间并集 ∩ [0, duration]：clip 到窗口后合并
    span = 0.0
    cur_a = cur_b = None
    for a, b in cs:
        a, b = max(a, 0.0), min(b, float(duration))
        if b <= a:
            continue
        if cur_a is None:
            cur_a, cur_b = a, b
        elif a <= cur_b:
            cur_b = max(cur_b, b)
        else:
            span += cur_b - cur_a
            cur_a, cur_b = a, b
    if cur_a is not None:
        span += cur_b - cur_a

    return {"n": len(cs), "first": first, "last": last,
            "max_dur": max(b - a for a, b in cs),
            "coverage": span / duration if duration > 0 else 0.0,
            "max_gap": max_gap, "overlaps": overlaps}


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 必须在任何 print 之前
    ap = argparse.ArgumentParser(description="成片内容审计（只读，不落盘）")
    ap.add_argument("--film-dir", default=str(FILM_DIR))
    ap.add_argument("--srt", default=None, help="默认自动在 SRT_CANDIDATES 里找")
    ap.add_argument("--duration", type=float, default=FILM_DURATION)
    args = ap.parse_args(argv)

    segs = segments(pathlib.Path(args.film_dir))
    if not segs:
        print(f"skip: {args.film_dir} 下没有 seg_shot*.mp4")
    else:
        print(f"成片段：{len(segs)} 段（期望 {FILM_SEGMENTS}）")
        print(f"{'段名':<24}{'红帧数':>8}{'红峰值':>10}{'琥珀峰值':>10}")
        for p in segs:
            rows = scan(p)
            if not rows:
                print(f"skip: {p.name} 解不出帧")
                continue
            red_frames = sum(1 for _, r, _ in rows if r > 0)
            red_peak = max(r for _, r, _ in rows)
            amber_peak = max(a for _, _, a in rows)
            print(f"{p.stem:<24}{red_frames:>8}{red_peak:>10}{amber_peak:>10}")

    srt = pathlib.Path(args.srt) if args.srt else find_srt()
    if srt is None or not srt.is_file():
        print(f"skip: 字幕不在 {[str(p) for p in SRT_CANDIDATES]}")
    else:
        st = subtitle_stats(srt, args.duration)
        print(f"字幕：{srt}")
        print(f"  条数={st['n']} 首={st['first']:.3f}s 末={st['last']:.3f}s "
              f"覆盖={st['coverage'] * 100:.1f}% 最大空档={st['max_gap']:.3f}s "
              f"重叠={st['overlaps']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
