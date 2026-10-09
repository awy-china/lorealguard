"""镜 1 出片（`tools/make_film_shots.py`）的常驻守卫 —— 把 round17b 那两条真坑钉住。

两条坑的共同点：**片子出了、30 秒时长对、看着像那么回事**，坏的是"第 0 帧"和"尾帧"。
  1) **首帧不是黑场（最亮 110）**，而台本镜 1 写的是『黑场起』。根因有两层：
     (a) 条带的纵向范围用"前景像素够多"（`fg.sum(axis=1) > 20`）取，`fig_star.png` 第 164 行
         只有 18 个前景像素（源图峰值 166）被切掉 ⇒ **那一行没有遮罩**；
     (b) `scale=…:flags=lanczos` 会把条带边缘晕染到边界外 1 行，遮罩按条带边界精确摆放时
         那几行永远露着（实测 bar3/4/5 上沿外 1 行 = 38 / 30 / 21）。
     现在：条带范围取"软阈值"（任何不是背景的像素）+ 遮罩上下各外扩 `MASK_PAD`。
  2) **尾帧还留着描边（差 6）**。根因不是 `enable` 多开了 —— 实测 t1 之后每一帧与"闪之前"
     的差**已经是 14、且不含任何 255 级像素**、相邻两帧差 0：那是 libx264 crf18 的量化漂移。
     坏的是判据 `d_last == 0`（有损编码下**永远绿不了**）。真残留是白框 ⇒ 差 200+，差一个量级。

本文件分四层，各管一件事：
  甲 **纯谓词 / 几何**（无 ffmpeg、无网络）—— 变异测试打的就是这层，秒级。
  乙 **产物实测**（`output/film/seg_shot01_hook.mp4` 在就跑，不在就 skip）—— 真实像素读数。
  丙 **自证伪**：合成片证明"甲"里那两条像素判据真能变红（不会红的检查等于没有检查）。
  丁 **变异**：只改 `tools/make_film_shots.py` 的**源码文本**，证明甲/乙不是装饰品。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import make_film_shots as mfs  # noqa: E402

#: 合成片 / 抽帧的落点。**必须在仓库内** —— 拒收路径要 `relative_to(ROOT)`。
WORK = ROOT / "output" / "_test_film_shots"

SRC = ROOT / "tools" / "make_film_shots.py"

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
need_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="没有 ffmpeg，出片与抽帧都跑不了")

FIG = mfs.PPT_IMG / "fig_star.png"


def _out() -> pathlib.Path:
    """段表声明的成片路径 —— **从 `shots()` 取**，不手抄文件名。"""
    return mfs.FILM / mfs.shots()[0]["out"]


def _fig_lay() -> tuple[dict, dict]:
    """量测 + 换算帧坐标。纯本地（PIL + numpy），不碰 ffmpeg、不碰网络。"""
    fig = mfs.measure_figure(FIG)
    return fig, mfs.layout(fig)


def _seg() -> dict:
    return mfs.shots()[0]


# ===================================================================== 甲 纯谓词


def test_成片路径由段表声明_不许在别处手抄():
    """① 路径与段表。段表字段名是隐藏接口（下游拼片脚本按名字取），拼错=静默错片。"""
    s = _seg()
    assert s["tag"] == "S01" and s["shot"] == 1
    assert s["slot"] == 16.0, "镜 1 槽位就是 16s（台本 00:00–00:16）"
    assert s["out"] == "seg_shot01_hook.mp4"
    assert mfs.FILM == ROOT / "output" / "film", "成片目录"
    assert mfs.REJECTED == mfs.FILM / "_rejected", "拒收要挪进成片的子目录"
    assert (mfs.VOICE / s["narration"]).exists(), f"配音不在：{mfs.VOICE / s['narration']}"


def test_三个承诺串全部进读回清单_且来自调研md不是手抄():
    """④ 尾帧读回清单 == 调研 md 里那三个承诺串（**一个不少、一个不手抄**）。

    为什么钉"清单等于全集"：只放一个串的清单会让"尾帧读回 1/1"看着也是成功行。
    数字从 `promises()` 现解析（它自己带算术自校：removal ÷ detector 必须等于 ratio）。
    """
    pr = mfs.promises()
    piece = _seg()["pieces"][0]
    assert len(pr) == 3, "三个承诺串：抹除侧星数 / 检测侧星数 / 比值"
    assert piece["expect"] == [p["text"] for p in pr], "读回清单必须是全集，不是子集"
    assert piece["promise_keys"] == [p["key"] for p in pr]
    assert len(set(piece["expect"])) == 3, "三个串不许互相重合"
    assert all(t.strip() for t in piece["expect"])
    #: 算术自洽（示例：5,667 ÷ 327 = 17.33）—— 手抄一个数就会在这里红。
    a, b, r = (int(t.replace(",", "")) if i < 2 else float(t)
               for i, t in enumerate(piece["expect"]))
    assert round(a / b, 2) == r, f"{a} ÷ {b} = {a / b:.4f}，而清单里写的是 {r}"


@need_ffmpeg
def test_首帧黑场的几何判据_真实素材上不报漏():
    """⑤-几何：源图里**任何不是背景的行**都必须落在某个遮罩内（排片时就拦，不用等出片）。

    另钉两件事：
      · 条带纵向范围必须比"前景像素够多"的硬阈值**更宽** —— round17b 丢的就是那种
        "几乎没像素、但确实有内容"的行（`fig_star.png` 第 164 行）；
      · 遮罩必须真的**上下各外扩**了行数（Lanczos 会晕染到边界外 1 行）。
    """
    import numpy as np
    from PIL import Image

    fig, lay = _fig_lay()
    leak = mfs.first_frame_is_covered(fig, lay)
    assert leak == [], f"这些帧行没被任何遮罩盖住 ⇒ 第 0 帧不会是纯黑：{leak[:12]}"

    # 硬阈值（工具曾经的取法）在这张图上会丢掉行 —— 丢了就不该再用它定条带范围。
    arr = np.asarray(Image.open(FIG).convert("RGB")).astype(int)
    dist = np.abs(arr - np.array(fig["bg"])).sum(axis=2)
    hard_rows = int(((dist > 24).sum(axis=1) > 20).sum())
    assert len(fig["ink_rows"]) > hard_rows, (
        f"非背景行数 {len(fig['ink_rows'])} 不大于硬阈值行数 {hard_rows} —— "
        f"条带纵向范围多半退回硬阈值了（round17b 第 164 行就是这么丢的）")

    # 遮罩 = 条带 + 上下各 MASK_PAD 行。取偶会再多 0~2 行，所以这里钉 +4 而不是 +6：
    # 钉的是"往外扩了"，不是精确值。MASK_PAD=0 在这里红。
    bb, py, s = fig["bbox"], lay["py"], lay["s"]
    for band, src in zip(lay["bands"], fig["bands"]):
        top = py + int(round((src["y0"] - bb["y0"]) * s))
        bot = py + int(round((src["y1"] - bb["y0"]) * s))
        assert band["fh"] >= (bot - top + 1) + 4, (
            f"{band['name']} 遮罩 h{band['fh']} 相对条带 {top}..{bot} 没有上下外扩 "
            f"—— 重采样晕染出来的那两行会一直露着（MASK_PAD={mfs.MASK_PAD}）")
    geo = sorted((b["fy"], b["fy"] + b["fh"] - 1) for b in lay["bands"])
    for (a0, a1), (b0, b1) in zip(geo, geo[1:]):
        assert b0 > a1, f"遮罩重叠：{a0}..{a1} 与 {b0}..{b1} —— 会把邻居提前擦出来"


def test_尾帧判据_闪幅与残影分得开():
    """⑥-谓词：**不是逐位相等**。有损编码下静态帧之间也有残留，而白框残留高一个量级。"""
    assert mfs.tail_is_clean(255, 0) and mfs.tail_is_clean(255, 6), "实测残留（6）必须放行"
    assert mfs.tail_is_clean(255, 14), "全帧范围的编码残留（14）必须放行"
    assert not mfs.tail_is_clean(255, 200), "真残留的白框（实测 200+）必须拦下"
    #: 边界：上限就是 `tail_is_clean` 自己算的那个数（写死 63 会在改比例时假红）。
    lim = max(mfs.BLACK_MAX * 2, int(mfs.TAIL_RESIDUE_RATIO * 255))
    assert mfs.tail_is_clean(255, lim) and not mfs.tail_is_clean(255, lim + 1)
    assert mfs.frame_is_black(mfs.BLACK_MAX) and not mfs.frame_is_black(mfs.BLACK_MAX + 1)


def test_降级决定_不许另画图_且理由写在段表里():
    """保住 round17 那条降级决定：**画面上每个字都来自素材图**，一处 drawtext 都没有。

    理由：尾帧要同时读回 5,667 / 327 / 17.33 —— 任何推近/另画都会让"读回"与"画面"自相矛盾。
    为什么连"理由"也机检：退化本身可以接受，**没人知道为什么退化**不行。
    """
    src = SRC.read_text(encoding="utf-8")
    assert "drawtext=" not in src and "textfile=" not in src, "又往画面上画字了"
    piece = _seg()["pieces"][0]
    assert set(piece) <= {"kind", "src", "why", "expect", "promise_keys", "flash_mark"}, \
        f"素材项多了字段 {sorted(set(piece))} —— 新字段要是『另画图』那类，先说清为什么"
    assert piece["kind"] == mfs.KIND_FIGBARS and piece["src"] == "fig_star.png"
    why = _seg()["why"]
    assert "降级实现" in why and "不许另画图" in why, "降级理由必须写在段表 why 里"


def test_降级决定_描边用白不用红():
    """保住第二条降级决定：闪用**白**描边 —— 全片风险红预算 ≤3 留给镜 2 / 镜 9。"""
    assert mfs.FLASH_COLOR == "white", f"闪色被改成 {mfs.FLASH_COLOR!r} 了"
    why = _seg()["why"]
    assert "风险红" in why, "为什么不用红的，必须写在段表 why 里"


def test_拒收路径_挪走文件并报非零(monkeypatch):
    """⑦ 判据不过时：**真的把 mp4 挪进 `_rejected/`**、留下 WHY、且退出码非 0。

    为什么三条都要：只挪不报非零 ⇒ 上层脚本以为成功（`| tail` 那个坑的另一副面孔）；
    只报非零不挪 ⇒ 下次 `--verify-only` 会拿这条坏片当"已复核"。
    """
    seg = _seg()
    film = ROOT / "output" / "_test_film_shots_film"
    if film.exists():
        shutil.rmtree(film)
    film.mkdir(parents=True)
    rej, work = film / "_rejected", film / "_shots"
    monkeypatch.setattr(mfs, "FILM", film)
    monkeypatch.setattr(mfs, "REJECTED", rej)
    monkeypatch.setattr(mfs, "WORK", work)
    out = film / seg["out"]
    out.write_bytes(b"not-a-real-mp4")
    monkeypatch.setattr(mfs, "verify",
                        lambda *a, **k: (False, ["首帧不是黑场（最亮 110）"], []))
    try:
        rc = mfs.run_shot(seg, verify_only=True, do_probe=False)
        assert rc != 0, "判据红了却报 0 —— 上层会当成功"
        assert not out.exists(), "坏片还留在成片目录里"
        assert (rej / seg["out"]).exists(), f"没挪进 {rej.name}/"
        why = (rej / f"{seg['tag']}_WHY.txt").read_text(encoding="utf-8")
        assert "首帧不是黑场" in why, "WHY 里要写清是哪条判据红的"
    finally:
        shutil.rmtree(film, ignore_errors=True)


# ===================================================================== 乙 产物实测
#: 没有成片就 skip：出片要 30 秒 + ffmpeg + 12 次抽帧，不是单测该干的事。
#: 成片一旦在（本轮就是 PASS 出来的那一条），下面这些就是**真实像素读数**。
needs_out = pytest.mark.skipif(not HAVE_FFMPEG or not _out().exists(),
                               reason="成片不在（先跑 tools/make_film_shots.py）")


@needs_out
def test_产物_几何时长帧数():
    """② 1920×1080 / 30fps / 16.000s / 480 帧 —— 四项都从 ffprobe 读，不由时长算。"""
    s = _seg()
    info = mfs.probe(_out())
    assert (int(info["width"]), int(info["height"])) == (mfs.W, mfs.H) == (1920, 1080)
    assert info["avg_frame_rate"] == f"{mfs.FPS}/1"
    assert abs(float(info["duration"]) - s["slot"]) < 1e-3
    assert int(info["nb_frames"]) == int(round(s["slot"] * mfs.FPS)) == 480, \
        "帧数由 ffmpeg 数出来，不许用时长×帧率顶替"


@needs_out
def test_产物_段长装得下实测配音():
    """③ 段长 ≥ 配音实测长 —— 不够只能改台本并留档，**不许拉长槽位、不许剪字**。"""
    s = _seg()
    mp3 = mfs.VOICE / s["narration"]
    mp3_dur = mfs.media_duration(mp3)
    dur = float(mfs.probe(_out())["duration"])
    assert mp3_dur > 0
    assert dur + 1e-9 >= mp3_dur, f"段长 {dur:.3f}s < 配音 {mp3_dur:.3f}s"


@needs_out
def test_产物_第0帧是黑场():
    """⑤-像素：第 0 帧最亮像素 ≤ `BLACK_MAX`（台本『黑场起』）。"""
    d = WORK / "guard"
    d.mkdir(parents=True, exist_ok=True)
    m = mfs.px_max(mfs.grab(_out(), 0, d / "f000.png"))
    assert mfs.frame_is_black(m), f"第 0 帧最亮 {m} > {mfs.BLACK_MAX} —— 不是黑场"


@needs_out
def test_产物_尾帧无描边残留():
    """⑥-像素：描边框只在闪的那 0.45s 内亮；尾帧与闪之前的差必须**远小于闪幅**。

    实测（PASS 那一版）：闪幅 255，尾帧残留 6。
    """
    seg, (fig, lay) = _seg(), _fig_lay()
    mp3 = mfs.VOICE / seg["narration"]
    anchor = mfs.narration_anchor(seg["shot"], seg["slot"])
    fl = mfs.plan(seg, fig, lay, anchor, mfs.media_duration(mp3))["flash"]
    d = WORK / "guard"
    d.mkdir(parents=True, exist_ok=True)
    rect = dict(w=fl["w"], h=fl["h"], x=fl["x"], y=fl["y"])
    out = _out()
    n_pre = int((fl["t0"] - 0.2) * mfs.FPS)
    n_mid = int((fl["t0"] + fl["t1"]) / 2 * mfs.FPS)
    pre = mfs.grab(out, n_pre, d / "flash_pre.png", rect)
    mid = mfs.grab(out, n_mid, d / "flash_mid.png", rect)
    last = mfs.grab(out, int(round(seg["slot"] * mfs.FPS)) - 1, d / "flash_last.png", rect)
    d_mid, d_last = mfs.px_diff(pre, mid), mfs.px_diff(pre, last)
    assert d_mid >= 200, f"描边没闪起来（差 {d_mid}）"
    assert mfs.tail_is_clean(d_mid, d_last), f"尾帧还留着描边（残留 {d_last}，闪幅 {d_mid}）"


# ===================================================================== 丙 自证伪
#: 黑框 + 白方块；`once` 只亮中段（0.05–0.15s），`stuck` 一直亮（= `enable` 没关掉）。
_BOX = "drawbox=x=20:y=20:w=40:h=24:color=white@1:t=fill"
RECT = dict(w=40, h=24, x=20, y=20)


def _clip(path: pathlib.Path, vf: str) -> pathlib.Path:
    """合成一条 6 帧（0.2s@30fps）的小片。**故意用有损编码**：判据要能扛真实编码残留。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c=black:s=160x120:r=30:d=0.2{vf}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
    return path


@need_ffmpeg
@pytest.mark.parametrize("box,clean", [("none", True), ("once", True), ("stuck", False)])
def test_自证伪_尾帧残留判据能变红(box, clean):
    """合成片：`stuck`（白框一直亮）**必须**被判成有残留 —— 否则 ⑥ 是装饰品。

    ⚠️ 参考帧必须取**纯黑片**那一帧，不能取本片第 0 帧：`stuck` 的第 0 帧本身就带白框，
    拿它当基准的话 `d_mid = d_last = 0`，坏片会被判成"干净"（这个坑当场踩过一次）。
    """
    d = WORK / "synth"
    vf = {"none": "", "once": f",{_BOX}:enable='between(t,0.05,0.15)'", "stuck": f",{_BOX}"}[box]
    src = _clip(d / f"{box}.mp4", vf)
    ref = mfs.grab(_clip(d / "black.mp4", ""), 0, d / "black_0.png", RECT)
    mid = mfs.grab(src, 3, d / f"{box}_3.png", RECT)
    last = mfs.grab(src, 5, d / f"{box}_5.png", RECT)
    d_mid, d_last = mfs.px_diff(ref, mid), mfs.px_diff(ref, last)
    if box == "once":
        assert d_mid >= 200, f"闪没闪起来（差 {d_mid}）—— 前面的判据不算数"
    assert mfs.tail_is_clean(d_mid, d_last) is clean, (
        f"{box}：d_mid={d_mid} d_last={d_last}，判成 {'有残留' if clean else '干净'} 是错的")


@need_ffmpeg
def test_自证伪_首帧黑场判据能变红():
    """合成片：整帧亮 ⇒ 必须判成『不是黑场』（这正是 round17b 最亮 110 的样子）。"""
    d = WORK / "synth"
    white = _clip(d / "white.mp4", ",drawbox=x=0:y=0:w=160:h=120:color=white@1:t=fill")
    black = _clip(d / "black2.mp4", "")
    mw = mfs.px_max(mfs.grab(white, 0, d / "w0.png"))
    mb = mfs.px_max(mfs.grab(black, 0, d / "b0.png"))
    assert mw > 200, f"合成白片最亮只有 {mw} —— 素材没造对"
    assert not mfs.frame_is_black(mw), f"整帧亮（{mw}）却判成黑场 —— 判据是装饰品"
    assert mfs.frame_is_black(mb), f"纯黑（{mb}）却判成不是黑场"


# ===================================================================== 丁 变异
#: 只改**源码文本**，不重建、不重跑出片（出片要 30 秒 + 网络）。
#: 每条都对应一个真实踩过的写法，不是等价改写。
MUTANTS = [
    ("M61 条带纵向范围退回『前景像素够多』（丢掉只有 18 个像素的那一行 ⇒ 第 0 帧漏光）",
     "soft_rows = (dist > SOFT_THR).sum(axis=1) > 0",
     "soft_rows = fg.sum(axis=1) > 20",
     "test_首帧黑场的几何判据_真实素材上不报漏"),

    ("M62 遮罩不外扩（Lanczos 晕染出来的那一行露在黑场外）",
     "MASK_PAD = 3", "MASK_PAD = 0",
     "test_首帧黑场的几何判据_真实素材上不报漏"),

    ("M63 读回清单只放第一个承诺串（『尾帧读回 1/1』也成了成功行）",
     'expect=[p["text"] for p in pr]',
     'expect=[p["text"] for p in pr[:1]]',
     "test_三个承诺串全部进读回清单_且来自调研md不是手抄"),

    ("M64 尾帧判据放得比实框还宽（残留白框被判成『干净』）",
     "TAIL_RESIDUE_RATIO = 0.25", "TAIL_RESIDUE_RATIO = 4.0",
     "test_尾帧判据_闪幅与残影分得开"),

    ("M65 描边换成风险红（全片红预算被镜 1 吃掉）",
     'FLASH_COLOR = "white"', 'FLASH_COLOR = "red"',
     "test_降级决定_描边用白不用红"),

    ("M66 拒收时报 0（判据红、片子坏了，上层却当成功）",
     '            print(f"       - {b}")\n        return 1',
     '            print(f"       - {b}")\n        return 0',
     "test_拒收路径_挪走文件并报非零"),

    ("M67 拒收挪到别的名字（下次 --verify-only 还会捡起这条坏片）",
     'dst = REJECTED / seg["out"]',
     'dst = REJECTED / ("x_" + seg["out"])',
     "test_拒收路径_挪走文件并报非零"),

    ("M68 降级理由从段表抹掉（退化了，但没人知道为什么）",
     "红色条形压满画面』**降级实现**", "红色条形压满画面』实现",
     "test_降级决定_不许另画图_且理由写在段表里"),
]


def _pytest(node: str) -> int:
    """跑一条用例，返回退出码。**摘掉 PYTHONPATH / PYTHONIOENCODING**（同其它变异守卫）。"""
    log = ROOT / "output" / "_film_shots_mutant.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONIOENCODING")}
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        return subprocess.run([sys.executable, "-m", "pytest",
                               f"tests/test_film_shots.py::{node}", "-q", "--no-header"],
                              cwd=str(ROOT), env=env,
                              stdout=fh, stderr=subprocess.STDOUT).returncode


def test_every_film_shots_mutant_makes_its_guard_red():
    original = SRC.read_text(encoding="utf-8")
    try:
        for label, needle, repl, node in MUTANTS:
            n_hit = original.count(needle)
            assert n_hit == 1, (
                f"{label}：变异目标串在源码里出现 {n_hit} 次 —— `replace(…, 1)` 只会改第一处，"
                f"落在注释/文档串上时源码行为没变、守卫当然不红 ⇒ **这条变异是假的、会假装通过**。"
                f"把 needle 加长到唯一（本轮 M68 就是这么被抓出来的）。")
            SRC.write_text(original.replace(needle, repl, 1), encoding="utf-8")
            try:
                rc = _pytest(node)
            finally:
                SRC.write_text(original, encoding="utf-8")
            assert rc != 0, f"变异体没被抓到：{label} → {node} 仍然是绿的"
    finally:
        SRC.write_text(original, encoding="utf-8")
        assert SRC.read_text(encoding="utf-8") == original, "还原失败"


def test_real_source_keeps_all_film_shots_guards_green():
    """反向断言：真源码下这些守卫必须全绿。没有这条，把守卫写成永远红也能骗过上面。"""
    for node in dict.fromkeys(m[3] for m in MUTANTS):
        assert _pytest(node) == 0, f"真源码下守卫没绿：{node}"
