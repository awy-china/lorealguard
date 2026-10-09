"""成片剪辑工具（`tools/make_film_segment.py`）的守卫 —— 把 2026-09-22 踩的两个真坑钉住。

两个坑的共同点：**产物在、时长对、看着没问题**，坏的是"帧数"和"剪点"。
  1) 帧数用 `时长 × 30` **算**出来的：D4 算出 178，ffmpeg 实际只给 **176**
     （素材真实帧率是 715/48，不是 15）→ 整段短 2 帧 → 尾帧读回抽到**不存在的 509 帧**，
     工具在最后一步抛异常，而 mp4 已经落盘。
     现在帧数由 ffmpeg `frame=` **数**出来，卡片去吸收余量；
     另加 `trim=end_frame=<槽位帧数>` 把"段长 = 槽位"变成结构性保证。
  2) 剪点用秒 + 硬编 15fps 换算：`round(onset × 15)` 对 2440/163 与 715/48 都是错帧号。
     现在剪点保留 showinfo 的**精确 PTS 有理数**（`405504/15360`），
     由 `select=gte(t,<有理数>)` 选帧 —— 取整根本不进这条链。

本文件只做**合成素材**上的断言：不联网、不依赖 `output/video_evidence/` 里的真实录屏，
所以换台机器/清空产物也照跑。真实素材上的端到端判定由工具自己当场做（它带 VL 逐字读回）。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
from fractions import Fraction

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import make_film_segment as mfs  # noqa: E402

#: 合成素材的帧率 —— **故意不是 15**：当初把帧数算错，根因就是硬编了 15。
SRC_FPS = "2440/163"

#: 工作目录必须在仓库内：`_card_filter` 要 `relative_to(ROOT)` 取 drawtext 的 textfile 路径。
WORK = ROOT / "output" / "_test_film"

MISSING_FONT = not pathlib.Path("C:/Windows/Fonts/msyh.ttc").exists()


def _synth(path: pathlib.Path, pieces: list[tuple[str, float]]) -> pathlib.Path:
    """拼出 `[(颜色, 秒), …]` 的合成片；相邻两段之间是**硬切**。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for color, secs in pieces:
        cmd += ["-f", "lavfi", "-i", f"color=c={color}:s=320x240:r={SRC_FPS}:d={secs}"]
    fc = "".join(f"[{i}:v]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=0[v]"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
    return path


def _work(name: str) -> pathlib.Path:
    d = WORK / name
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- 剪点


def test_场景检测_恰好一次才算数():
    """一次硬切 ⇒ 认；零次或两次 ⇒ **拒片**（不是"取第一个"）。

    为什么拒而不是挑一个：多次变化是废片的典型特征（录制期间别的窗口压上来、页面中途重绘）。
    猜一个剪点等于把"这条链可复核"这句话作废。
    """
    d = _work("scene")
    one = _synth(d / "one.mp4", [("0x102030", 1.0), ("0xD0A020", 1.0)])
    assert len(mfs.scene_changes(one)) == 1, "一次硬切该被认出来"
    assert mfs.result_onset(one)[0].count("/") == 1

    flat = _synth(d / "flat.mp4", [("0x102030", 2.0)])
    assert mfs.scene_changes(flat) == [], "纯色片不该报出场景变化"
    with pytest.raises(RuntimeError, match="恰好一次"):
        mfs.result_onset(flat)

    two = _synth(d / "two.mp4", [("0x102030", 1.0), ("0xD0A020", 1.0), ("0x102030", 1.0)])
    assert len(mfs.scene_changes(two)) == 2
    with pytest.raises(RuntimeError, match="恰好一次"):
        mfs.result_onset(two)


def test_剪点是精确有理数_且落在帧边界上():
    """剪点必须是**素材自己的帧时刻**，不是四舍五入到小数的秒。

    判据：把有理数乘素材帧率必须是**整数** —— 即它正好是某一帧的 PTS。
    若哪天有人改回 `round(pts_time, 3)`，这条会红（0.001s 的偏差在 14.97fps 下足以
    跨到相邻帧，那正是"掐头差一帧"的成因）。
    """
    d = _work("rational")
    clip = _synth(d / "cut.mp4", [("0x102030", 1.0), ("0xD0A020", 1.0)])
    cur, sec, prev, nxt = mfs.result_onset(clip)
    for label, rat in (("onset", cur), ("前一帧", prev), ("后一帧", nxt)):
        n = Fraction(rat) * Fraction(SRC_FPS)
        assert n.denominator == 1, f"{label} 的有理数 {rat} 不落在帧边界上（{float(n)} 帧）"
    assert Fraction(cur) - Fraction(prev) == Fraction(nxt) - Fraction(cur), (
        "前后帧间隔不相等 ⇒ 这三个有理数不是同一帧序列上的相邻三帧")
    assert Fraction(cur) > Fraction(prev), "onset 必须晚于它的前一帧"
    assert abs(sec - float(Fraction(cur))) < 0.01, f"秒读数 {sec} 与有理数对不上"


def test_clip_chain_合成规则():
    """`clip_vf` 的两半（数帧 / 出片）必须共用同一个函数，规则钉在这里。

    实测踩过：漏 `select=` 前缀 → ffmpeg 直接 `No such filter: 'gte(t,…)'`；
    keep 片（`sel=""`）只顾拼前缀、把 `fps=/scale=` 丢了 → `-vf` 成空串。
    """
    keep = mfs.clip_chain("", None)
    assert "select=" not in keep and "trim=" not in keep
    assert "fps=30" in keep and "scale=1920:1080" in keep, f"keep 片丢了 fps/scale：{keep}"

    onset = mfs.clip_chain("gte(t\\,405504/15360)", None)
    assert onset.startswith("select=gte(t\\,405504/15360),"), f"select 前缀没拼对：{onset}"
    assert "trim=" not in onset, "数帧那一半不该带 trim（带了数的就不是真帧数）"

    built = mfs.clip_chain("gte(t\\,405504/15360)", 186)
    assert "trim=end_frame=186" in built and "setpts=PTS-STARTPTS" in built


# --------------------------------------------------------------------------- 帧数


def _synth_segment(d: pathlib.Path, slot: float) -> tuple[dict, dict]:
    """造一个「合成 onset 片 + 一张补时卡片」的段，并把模块的输出目录指到临时目录。"""
    clip = _synth(d / "src" / "cut.mp4", [("0x102030", 1.0), ("0xD0A020", 1.0)])
    mfs.EV, mfs.FILM, mfs.CARDS = clip.parent, d / "film", d / "cards"
    onsets = {"cut.mp4": mfs.result_onset(clip)}
    seg = dict(tag="T1", out="t1.mp4", shot=0, slot=slot, narration="",
               why="合成段", pieces=[
                   dict(kind="clip", src="cut.mp4", trim="onset", expect=[]),
                   dict(kind="card", dur="fill", title="合成卡", sub="守卫用", expect=[]),
               ])
    return seg, onsets


def test_plan_帧数正好等于槽位():
    """素材片吃**数出来的**帧数，卡片吸收余量 ⇒ 排完必须正好等于槽位帧数。

    这条是"装进槽位"这句话的可执行版本。注意它单独**不足以**抓住当初那个 bug
    （旧算法下卡片照样能把总长凑到槽位）—— 抓它的是下面那条出片帧数断言。
    """
    seg, onsets = _synth_segment(_work("plan"), slot=3.0)
    planned = mfs.plan(seg, onsets)
    want = int(round(3.0 * mfs.FPS))
    assert sum(p["frames"] for p in planned) == want, (
        f"排片总帧数 {sum(p['frames'] for p in planned)} ≠ 槽位 {want}")
    assert all(p["frames"] > 0 for p in planned), "不该有 0 帧的片"


@pytest.mark.skipif(MISSING_FONT, reason="drawtext 需要中文字体（Windows 交付机有）")
def test_出片帧数等于槽位_且末帧抽得出来():
    """出片后 ffprobe 的帧数必须等于槽位帧数，**且最后一帧抽得出来**。

    后半句是当初崩溃的原话：段短 2 帧时，`_frame(最后一帧)` 抽到不存在的 509 帧 →
    `RuntimeError`，而 mp4 已经落盘。所以"帧数对"与"末帧在"要一起断言。
    """
    seg, onsets = _synth_segment(_work("build"), slot=3.0)
    planned = mfs.plan(seg, onsets)
    out = mfs.build(seg, planned)
    want = int(round(3.0 * mfs.FPS))
    got = int(mfs.probe(out).get("nb_frames", 0) or 0)
    assert got == want, f"出片 {got} 帧 ≠ 槽位 {want} 帧（素材片帧数没数准？）"
    last = sum(p["frames"] for p in planned) - 1
    assert mfs._frame(out, f"eq(n\\,{last})", WORK / "_f" / "last.png").exists(), (
        f"最后一帧（第 {last} 帧）抽不出来 ⇒ 段比排片短")


@pytest.mark.skipif(MISSING_FONT, reason="drawtext 需要中文字体（Windows 交付机有）")
def test_剪点落在onset那一帧_不是前一帧():
    """出片第 0 帧必须**更像**源片的 onset 帧，而不是它前一帧 —— 即"掐头掐在最紧处"。

    这是 ④ 那条判据的**离线版**（工具里那版走 SSIM 也是同一套），不联网就能跑。
    素材是硬切，所以 onset 与前一帧的差异极大，判据有真实分辨力。
    """
    seg, onsets = _synth_segment(_work("cutpos"), slot=3.0)
    planned = mfs.plan(seg, onsets)
    out = mfs.build(seg, planned)
    _, _, prev, _ = onsets["cut.mp4"]
    cur = onsets["cut.mp4"][0]
    first = mfs._frame(out, "eq(n\\,0)", WORK / "_f" / "first.png")
    like_cur = mfs._ssim(mfs._frame(mfs.EV / "cut.mp4", f"gte(t\\,{cur})",
                                    WORK / "_f" / "src_cur.png", scale=True), first)
    like_prev = mfs._ssim(mfs._frame(mfs.EV / "cut.mp4", f"gte(t\\,{prev})",
                                     WORK / "_f" / "src_prev.png", scale=True), first)
    assert like_cur > like_prev + 0.05, (
        f"出片首帧更像 onset 的前一帧（onset {like_cur:.4f} vs 前一帧 {like_prev:.4f}）"
        f"—— 掐头晚/早了一帧")
