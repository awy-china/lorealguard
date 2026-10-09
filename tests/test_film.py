# -*- coding: utf-8 -*-
"""成片链（E 组）两把工具的守卫 —— 只做**合成素材**上的断言，不碰 `output/film/` 的真实产物。

为什么不用真实产物当素材
------------------------
`tests/test_rebuild_from_scratch.py` 会把生成物删光再从零重造 ⇒ 任何把守卫挂在
`output/film/*.mp4` 上的写法，都会在那条链上假红（"产物不存在"不等于"判据坏了"）。
所以这里现造：噪声 wav、两色纯色片、合成 SRT、自画 PNG —— 换台机器 / 清空产物照跑。

三条判据各自钉住 2026-09-26 踩过的真坑
--------------------------------------
  ① `bgm_wav()` 的归零**必须真的落在闸门里**，且窗口外仍有声 —— 否则「镜 5–7 环境静音」
     这句话就是空话（整段静音也能让"窗口静音"绿）。
  ② 判据窗口**必须从闸门内侧起量**：`volumedetect` 取区间**最大值**，起点压在闸门边缘时，
     边缘前那点响信号就把读数顶起来 ⇒ 这条判据**永远不可能绿**（`docs/DEBT.md` D-25）。
     量法不改（它没错），钉的是**量的位置**（调用点带 1s 守护带）。
  ③ 抽帧一律**按时间**（`grab()`：`-ss`），不按帧号 —— `eq(n\\,N)` 在长文件上取错帧，
     实测把母版 2790 帧抽成了镜 9 的文字（`docs/DEBT.md` D-24）。
  ④ 定格段判据必须是**全帧扫描**（`_hold_scan()`）而不是三帧抽检 —— 只把未采样区间涂黑的
     样本能让抽检整段 PASS（3 个采样点全落在干净区间），而画面里实有 22 帧是黑的
     （`docs/DEBT.md` D-22）。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import make_film as mf          # noqa: E402
import make_film_final as mff     # noqa: E402
import make_film_segment as mfs   # noqa: E402

WORK = ROOT / "output" / "_test_film_pipeline"
#: `make_film_final.ffmpeg()` 按工具契约用 `cwd=FILM` ⇒ 测试必须保证这个目录在（否则 FileNotFoundError）。
(ROOT / "output" / "film").mkdir(parents=True, exist_ok=True)

#: 合成 BGM 的片长与归零闸门 —— 故意不用成片那套（179s / [75,120)）：跑得快，且与产物解耦。
BGM_SECS, GATE = 6.0, (2.0, 4.0)
#: 从闸门**内侧**起量的窗口（对应 `make_film_final.py` 里的 `silent[0] + 1.0`）。
INNER = (GATE[0] + 1.3, GATE[1] - 0.1)


def _work(name: str) -> pathlib.Path:
    d = WORK / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _brightness(png: pathlib.Path) -> float:
    px = list(Image.open(png).convert("L").getdata())
    return sum(px) / len(px)


_CACHED_BGM: pathlib.Path | None = None


def _bgm() -> pathlib.Path:
    """6s 合成 BGM（真跑 `bgm_wav()`，不是另写一条链）—— 三条用例共用，只算一次。"""
    global _CACHED_BGM
    if _CACHED_BGM is None:
        w = _work("bgm") / "bgm.wav"
        mff.bgm_wav(w, BGM_SECS, GATE)
        _CACHED_BGM = w
    return _CACHED_BGM


def _synth_cut(path: pathlib.Path) -> pathlib.Path:
    """0–1s 黑、1–2s 白的硬切片（`-qp 0` 无损 ⇒ 颜色是纯的，没有编码噪声）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=1",
         "-f", "lavfi", "-i", "color=c=white:s=320x240:r=30:d=1",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
         "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return path


# --------------------------------------------------------------------------- ① BGM 归零


def test_bgm_归零窗口是数字静音_窗口外仍有声():
    """归零要在**闸门里**、且不能把整段都压掉 —— 两头都能红。

    一个只会说"静"的判据是没有的判据：`loudnorm` 归一到 -28 之后，若整段真被静音，
    "窗口内 ≤ -80"照样绿 ⇒ 所以必须同时断言窗口外（闸门前 / 闸门后）都还有声。
    """
    w = _bgm()
    inner = mff.max_vol(w, *INNER)
    before = mff.max_vol(w, 0.2, GATE[0] - 0.8)
    after = mff.max_vol(w, GATE[1] + 0.2, BGM_SECS - 0.2)
    lv = mff.lufs(w)
    assert inner <= -80, f"闸门内不是数字静音：{inner:.1f} dB（本 build 静音底约 -91）"
    assert before > -80, f"闸门前就没了声：{before:.1f} dB ⇒ 整段静音，判据成了空话"
    assert after > -80, f"闸门后就没了声：{after:.1f} dB ⇒ 归零窗口盖住了整段"
    assert -40 < lv < -20, f"整段 LUFS 读到 {lv} —— 不像 -28 目标（断链 / NaN 会落在这里）"


def test_静音判据从闸门内侧起量_不压在边缘():
    """(D-25) 判据窗口的起点**不许压在闸门边缘上**。

    实测：179s 成片上用 `atrim=75.000:120.000` 量那个闸门，读到 -18.5 dB（判据要求 ≤ -80）
    ⇒ 永远红；而逐秒扫描显示 [76,77) 起是 -91.0。分界线落在**量的位置**上，不在闸门本身。
    这里把"位置"钉在源码上（变异体 M63 会把它翻回边缘，本用例必须变红）。
    """
    src = (ROOT / "tools" / "make_film_final.py").read_text(encoding="utf-8")
    assert "vol = max_vol(bgm, silent[0] + 1.0, silent[1] - 1.0)" in src, (
        "判据窗口没带守护带 ⇒ 起点压在闸门边缘，这条判据会永远红（D-25）")

    edge = mff.max_vol(_bgm(), GATE[0], GATE[1])
    inner = mff.max_vol(_bgm(), *INNER)
    assert edge >= inner, "超集窗口的最大值不可能小于子集 —— 量法本身出了问题"


# --------------------------------------------------------------------------- ② 字幕


def test_字幕样式与台本一致():
    """字号 / 描边 / 对齐只写在 `srt_to_ass()` 一处，与台本 `:153` 一一对应。"""
    ass = _work("ass") / "s.ass"
    mff.srt_to_ass([(0.0, 1.5, "甲"), (61.0, 62.5, "乙\\N丙")], ass)
    txt = ass.read_text(encoding="utf-8")
    assert "Style: Sub,DengXian,28,&H00FFFFFF,&H00FFFFFF,&H99000000,&H99000000," in txt, \
        "样式行偏离台本 `:153`（等线 28px 白字 + 60% 黑描边）"
    assert ",1,2,0,2,40,40,40,1" in txt, "BorderStyle/Outline/Alignment（底部居中）变了"
    assert "0:00:00.00,0:00:01.50,Sub,,0,0,0,,甲" in txt, "第 1 条时间轴不对"
    assert "0:01:01.00,0:01:02.50,Sub,,0,0,0,,乙\\N丙" in txt, "第 2 条时间轴/换行不对"


def test_srt_解析起止时间与文本_且吃_CRLF():
    """解析必须给出**终点**（不是起终点同值），且 CRLF 要归一 —— SRT 是 CRLF 存的。"""
    srt = _work("srt") / "t.srt"
    srt.write_bytes(
        ("1\r\n00:00:01,000 --> 00:00:02,500\r\n甲\r\n\r\n"
         "2\r\n00:01:00,000 --> 00:01:01,250\r\n乙\r\n丙\r\n").encode("utf-8"))
    cs = mff.cues(srt)
    assert len(cs) == 2, f"该解出 2 条，实得 {len(cs)}"
    assert [round(a, 3) for a, _, _ in cs] == [1.0, 60.0]
    assert [round(b, 3) for _, b, _ in cs] == [2.5, 61.25], "终点解析错（等于起点？）"
    assert [t for _, _, t in cs] == ["甲", "乙\\N丙"], "多行文本的换行拼接错了"


def test_底部字幕带的白像素计数能红能绿():
    """这条判据是"字幕真上屏"的唯一证据 ⇒ 它自己必须能红：黑图 → 0，带内画白 → 正数。"""
    d = _work("band")
    black = d / "black.png"
    Image.new("RGB", (1920, 1080), (0, 0, 0)).save(black)
    assert mff.white_in_band(black) == 0, "全黑图不该数出白像素"

    white = d / "white.png"
    im = Image.new("RGB", (1920, 1080), (0, 0, 0))
    im.paste((255, 255, 255), (600, 990, 1320, 1050))     # 画在字幕带（y 950–1080）里
    im.save(white)
    n = mff.white_in_band(white)
    assert n == 720 * 60, f"白像素计数不对：{n} ≠ {720 * 60}（裁区或阈值变了？）"


# --------------------------------------------------------------------------- ③ 抽帧


def test_抽帧按时间定位_不用帧号():
    """`grab()` 必须按时间取到**那一刻的画面** —— 钉住 D-24（帧号寻址在长文件上取错帧）。"""
    d = _work("grab")
    clip = _synth_cut(d / "cut.mp4")
    a, b = d / "at05.png", d / "at15.png"
    mf.grab(clip, 0.5, a)
    mf.grab(clip, 1.5, b)
    assert _brightness(b) > _brightness(a) + 100, (
        f"1.5s 处应是白场、0.5s 处是黑场，实得 {_brightness(b):.1f} vs {_brightness(a):.1f}"
        " ⇒ 抽帧没按时间定位（退回帧号寻址？）")


# --------------------------------------------------------------------------- ④ 定格全帧扫描


def test_定格全帧扫描_能红能绿():
    """`_hold_scan()` 自己必须**能红** —— 一个只会说「相同」的判据就是没有的判据。

    绿片：`testsrc` 2s（第 0~59 帧各异）＋ `tpad=stop_mode=clone:stop_duration=1.0`
    ⇒ 第 60~89 帧是第 59 帧的克隆 ⇒ 扫第 59→89 帧应当**零差异**、扫过 30 帧。
    红片：把第 75 帧涂黑后**无损**重编码 ⇒ 差异帧表必须**恰好** `[75]`（多一个少一个都算错）。
    ⚠️ 两条都必须 `-qp 0`：默认有损编码下同一张图逐帧重建仍有 ±1 差异 ⇒ 绿片会永远红
    （Hermes 实测踩过这一脚）。尺寸必须 1920×1080：`_hold_scan` 用模块级 `W/H` 算每帧字节数。
    已有的三帧抽检点 (186, 332, 479) **不含 300 / 75** ⇒ 这类中段涂黑正是抽检的盲区。
    """
    d = _work("hold_scan")
    green = d / "synth_hold.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=2",
         "-vf", "tpad=stop_mode=clone:stop_duration=1.0",
         "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(green)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    got = mfs._hold_scan(green, 59, 89)
    assert got == ([], 30), (
        f"克隆段被判出差异（实得 {got}）⇒ 绿片不绿 —— `-qp 0` 丢了？这条判据会永远红")

    red = d / "synth_hold_mut75.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(green),
         "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black@1:t=fill:enable='eq(n\\,75)'",
         "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(red)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    got = mfs._hold_scan(red, 59, 89)
    assert got == ([75], 30), (
        f"涂黑的第 75 帧没被全帧扫描抓到（实得 {got}）⇒ 这条判据是摆设")


def test_定格判据走全帧扫描_不用三帧抽检():
    """`verify()` 的定格分支必须**调**全帧扫描 —— 退回三帧抽检就是 D-22 的盲区重开。

    实测（2026-09-26）：只把未采样区间涂黑（成片第 210~220 / 280~290 帧）的样本，
    三个采样点（202 / 265 / 329）全落在干净区间 ⇒ 抽检判 PASS，而画面里实有 22 帧是黑的。
    本用例把「接了几处」钉在**源码文本**上：定义 1 处 ＋ `verify()` 里的调用 1 处 ＝ **2 处**
    （`verify()` 里判 PASS/FAIL 的只有全帧扫描，三帧数字降级为打印）。
    """
    src = (ROOT / "tools" / "make_film_segment.py").read_text(encoding="utf-8")
    n = src.count("_hold_scan(")
    assert n == 2, (
        f"`_hold_scan(` 在 make_film_segment.py 里出现 {n} 次，要 **2** 次"
        "（定义 1 ＋ verify() 里的调用 1）⇒ 定格判据退回三帧抽检，D-22 的盲区重开")
