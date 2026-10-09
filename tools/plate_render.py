"""**无源片段（卡片镜）的逐帧绘制器** —— `make_film_segment.py` 的 `kind="plate"` 片种。

为什么单独一个模块：plate 片的"源"不是一份录屏，而是**一段程序**。把它写进剪辑工具里
会让那个工具同时承担"剪"与"画"两件事，而这两件事的证据链完全不同 ——
剪的判据是帧数 / 剪点 / 定格逐像素，画的判据是**红像素窗口**与**文字墨量窗口**。

三条不变量（与 `tools/make_appeal_pack.py` 的四条同一性质：都是"不许做"的清单）：

1. **零时钟**：帧号是唯一的时间源（`n / FPS`），不读 `time.time()`、不读文件 mtime、
   不读环境。同一 `(pid, frames)` 两次绘制必须**逐字节相同** —— 这是成片段
   sha256 可复造的前提，也是本模块不写 PNG 任何元数据的原因。
2. **零外部素材**：进入画面的图像素材**只有** `samples/base_neutral.jpg`（合成样图）。
   头像剪影由本模块用几何图形画（`_silhouette`），**没有任何真人照片**。
   ⚠️ 这是铁律 4 在绘制链上的落点：这里不接受任何"换一张更真的头像"的改动。
3. **声明即判据**：`PLATE_SPECS[pid]` 里声明的红叉时刻 / 文字窗口 / 卡片推入时刻，
   就是 `make_film_segment.py --only SXX` 里 `verify` 拿**实测像素**去对的那一份数。
   声明与实测**两边都能红**：实测与声明不符 ⇒ 拒收；声明写成 `%` 之外的东西 ⇒ 当场红。
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 画面里**唯一**允许出现的图像素材（铁律 4：只用合成样本）。
SAMPLE = ROOT / "samples" / "base_neutral.jpg"

#: 字体。与 `make_film_segment.py` 的 drawtext 用同一族（msyh），保证两处中文字形一致。
FACE = "C:/Windows/Fonts/msyh.ttc"
FACE_BOLD = "C:/Windows/Fonts/msyhbd.ttc"

W, H, FPS = 1920, 1080, 30

BG = (11, 16, 32)          # 与 `_card_filter` 里 `color=c=0x0B1020` 同色
PANEL = (26, 34, 51)
EDGE = (70, 84, 112)
INK = (236, 240, 248)
MUTE = (150, 160, 180)
AMBER = (217, 138, 0)      # 与镜 5 旁注同色（0xD98A00）
#: 红叉。**故意选带一点暗的红**：H.264 会把边缘拉亮，纯 0xFF0000 在高码率下
#: 更易被振铃推过判色阈值（R>200 且 G<60），而 (230,32,32) 留了 60 的余量。
RED = (230, 32, 32)
SILH = (150, 158, 176)     # 头像剪影（中性灰蓝，**不是**任何肤色）

#: 判色阈值的**单一来源**：ffmpeg 的 `lutrgb` 条件串与 PIL 端的检查都由它生成，
#: 两边不可能各自漂。语义＝**严格不等**（lo 走 `gt`、hi 走 `lt`）。
#:   red   纯红：R>200 且 G<60 且 B<60    —— 红叉 (230,32,32) 落在这里
#:   amber 琥珀：R>180 且 100<G<190 且 B<60 —— 镜 5 旁注 / S03 小标 (217,138,0) 落这里
#: 两个口径**互斥**（红叉 G=32 不 >100；琥珀 G=138 不 <60）—— 这正是
#: 「S03 满屏琥珀却必须零红」这条判据能成立、且能红的依据。
RGB_RULES: dict[str, dict[str, tuple[int | None, int | None]]] = {
    "red": {"r": (200, None), "g": (None, 60), "b": (None, 60)},
    "amber": {"r": (180, None), "g": (100, 190), "b": (None, 60)},
}

#: 「亮」判据：有限范围 luma > 200（swscale 的 RGB→gray 给出 16..235，
#: 所以 200 ≈ 亮度的 84%）。白字 235 稳过、卡片底色/琥珀字(≈146)稳不过。
GRAY_BRIGHT = 200

THICK = 22      # 红叉笔画宽度（px）。红叉的**形状**判据（非色块）就是拿它算出来的。

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FACE_BOLD if bold else FACE
    if not pathlib.Path(path).exists():
        raise RuntimeError(
            f"字体缺失：{path} —— 缺字体时 PIL 会回退到一个画不出中文的默认字体，"
            f"画面看起来只是有点小，而所有文字判据会绿得没有依据。")
    key = (path, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(path, size)
    return _FONT_CACHE[key]


def _text_ink(text: str, size: int, bold: bool = False) -> int:
    """数一串字**画出来有多少像素不是空的**（0 ⇒ 这串字一个字都没画出来）。

    为什么绘制期就要有这个数：静默空字（缺字形 / 括号写错 / 编码不对）在成片里
    表现为"那块地方是空的"，而帧数、时长、几何**全部照常为真** —— 本项目最恨的
    那种静默。这里当场量一遍，量到 0 就直接红。
    """
    f = _font(size, bold)
    box = f.getbbox(text)
    if not box:
        return 0
    pad = 8
    im = Image.new("L", (box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad), 0)
    ImageDraw.Draw(im).text((pad - box[0], pad - box[1]), text, font=f, fill=255)
    return sum(1 for v in im.getdata() if v > 128)


# ----------------------------------------------------------------------------- S02 画面


def _silhouette(d: ImageDraw.ImageDraw, cx: int, top: int, bottom: int) -> None:
    """创作者头像**剪影** —— 头 + 肩两块椭圆，纯几何，**不含任何真人照片**。"""
    head_r = int((bottom - top) * 0.24)
    cy = top + head_r + int((bottom - top) * 0.06)
    d.ellipse([cx - head_r, cy - head_r, cx + head_r, cy + head_r], fill=SILH)
    sh_w, sh_h = int((bottom - top) * 0.56), int((bottom - top) * 0.46)
    d.ellipse([cx - sh_w, bottom - sh_h, cx + sh_w, bottom + int(sh_h * 0.35)], fill=SILH)


def _x_layer(box: tuple[int, int, int, int]) -> Image.Image:
    """一把红叉画在**独立透明层**上 —— 落下的动画靠裁这层，不动底层像素。"""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = box
    d.line([x0, y0, x1, y1], fill=RED + (255,), width=THICK)
    d.line([x0, y1, x1, y0], fill=RED + (255,), width=THICK)
    return layer


def _reveal(im: Image.Image, layer: Image.Image, box: tuple[int, int, int, int],
            prog: float) -> Image.Image:
    """把红叉**从上往下**露出一部分（`prog`∈(0,1]）＝「落下」。

    ⚠️ `prog` 由**帧号**算（`(n - onset + 1) / ramp`），不读时钟 —— 见模块头 ①。
    """
    y0, y1 = box[1], box[3]
    top, total = y0 - THICK, (y1 - y0) + 2 * THICK
    h = max(1, min(total, int(round(total * prog))))
    part = layer.crop((0, top, W, top + h))
    im.paste(part, (0, top), part)
    return im


def _s02_base() -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # 左半屏：**真实的种草图** —— 画面里唯一允许的图像素材（合成样图）。
    photo = Image.open(SAMPLE).convert("RGB").resize((800, 600), Image.Resampling.LANCZOS)
    im.paste(photo, (120, 190))
    d.rectangle([120, 190, 919, 789], outline=EDGE, width=2)
    # 右半屏：创作者头像**剪影**（程序画，无真人照片）。
    d.rectangle([1000, 190, 1799, 789], fill=PANEL, outline=EDGE, width=2)
    _silhouette(d, cx=1400, top=250, bottom=760)
    lf = _font(30)
    for cx, txt in ((520, "种草图（合成样图）"), (1400, "创作者头像（剪影）")):
        d.text((cx, 810), txt, font=lf, fill=MUTE, anchor="ma")
    return im


def _s02_render(out_dir: pathlib.Path, sp: dict) -> None:
    base = _s02_base()
    layers = [_x_layer(c["box"]) for c in sp["red_cues"]]
    z = sp["zoom"]
    for n in range(sp["frames"]):
        im = base.copy()
        for cue, layer in zip(sp["red_cues"], layers):
            if n < cue["onset"]:
                continue
            prog = min(1.0, (n - cue["onset"] + 1) / cue["ramp"])
            im = _reveal(im, layer, cue["box"], prog)
        big = z["first"] <= n <= z["last"]
        ImageDraw.Draw(im).text((W // 2, z["y"]), z["text"],
                                font=_font(z["size_big"] if big else z["size"]),
                                fill=INK, anchor="mm")
        im.save(out_dir / f"s02_{sp['frames']:04d}_{n:05d}.png")


# ----------------------------------------------------------------------------- S03 画面


def _draw_card(im: Image.Image, x: int, c: dict) -> None:
    """一张卡片 + 右侧**可交付小标**。`x` 是当前左缘（推入时为负向偏移）。"""
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([x, c["y"], x + c["w"], c["y"] + c["h"]], radius=18,
                        fill=PANEL, outline=EDGE, width=2)
    d.text((x + 56, c["y"] + c["h"] // 2), c["title"], font=_font(46), fill=INK, anchor="lm")
    d.text((x + c["w"] - 56, c["y"] + c["h"] // 2), c["tag"], font=_font(40), fill=AMBER,
           anchor="rm")


def _s03_render(out_dir: pathlib.Path, sp: dict) -> None:
    hl = sp["headline"]
    hf = _font(hl["size"])
    kf = _font(sp["keywords"][0]["size"])
    for n in range(sp["frames"]):
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        if n <= hl["last"]:
            d.text((W // 2, hl["y"]), hl["text"], font=hf, fill=INK, anchor="mm")
        for c in sp["cards"]:
            if n < c["onset"]:
                continue
            p = min(1.0, (n - c["onset"] + 1) / c["ramp"])
            _draw_card(im, int(round(c["x"] + (W - c["x"]) * (1 - p))), c)
        for k in sp["keywords"]:
            if k["first"] <= n <= k["last"]:
                d.text((W // 2, k["y"]), k["text"], font=kf, fill=INK, anchor="mm")
        im.save(out_dir / f"s03_{sp['frames']:04d}_{n:05d}.png")


# ----------------------------------------------------------------------------- S09 画面


def _s09_lines() -> list[dict]:
    """镜 9 五条「不做」—— 字号 / 纵坐标 / 上屏帧号都是**裁定值**（台本 `:119-121`），
    写成一张表而不是散在绘制代码里。

    ⚠️ 五条的**大小不是一刀齐**：正文三条（口播 `:121`）字号 80、行距 140；
    末尾两条（画面 `:120` —— 我们自己先捅的那一刀）字号 75 / 60、行距 133 / 75。
    末两行比前一行**小 20% 且更靠下**，这是刻意的不对称：被拒的旧版正是一样大一样齐
    （见 `output/film/_rejected/`）。这两条数字来自 round20e2 实测的版式，不是估的。
    """
    return [
        dict(text="不做真假终审", size=80, y=90, onset=0, red=None),
        dict(text="不做 AIGC 二分类", size=80, y=230, onset=150, red=None),
        dict(text="不做自动处罚", size=80, y=370, onset=300, red=None),
        dict(text="数据集自造自判 = 开卷考试", size=75, y=503, onset=450, red="开卷考试"),
        dict(text="把规则表全文交给对手 → 红线被击穿 3 条（登记在案，未改阈值）",
             size=60, y=578, onset=600, red="击穿 3 条"),
    ]


_S09_LINES = _s09_lines()
_S09_FRAMES = 780                        # 26.0s × 30fps（槽位裁定值）


def _plate_x0(text: str, size: int) -> int:
    """一行字的左缘 x —— 整行按 `getlength` 居中（与 `anchor="lm"` 的绘制逐点同源）。"""
    return (W - int(_font(size).getlength(text))) // 2


def _plate_ink_box(text: str, size: int, y: int,
                   seg: str | None = None) -> tuple[int, int, int, int]:
    """量一串字（或其中的一段）在**画面坐标**里真的落在哪几行像素上，外扩 4px 吸收编码振铃。

    为什么绘制期就要量：判据的取景框必须与画面**同源**。手估一个框，一旦偏到空白处，
    那条判据会**恒绿** —— 与没有判据相比它更坏，会在报告里多出一行没人质疑的 ✅
    （`_validate_check` 的说明写了同一件事）。所以这里不估：用与绘制完全相同的字体 /
    取整 / `anchor="lm"`，把一个整帧大小的 L 图当量尺。

    镜 9 / 镜 10 共用这一份 —— 各写一份就等于放了两套"同源"定义，迟早会分叉。
    """
    f = _font(size)
    x = _plate_x0(text, size)
    if seg is not None:
        x += int(f.getlength(text.split(seg)[0]))
        text = seg
    im = Image.new("L", (W, H), 0)
    ImageDraw.Draw(im).text((x, y), text, font=f, fill=255, anchor="lm")
    bb = im.getbbox()
    if bb is None:
        raise RuntimeError(f"plate：『{text}』量不到墨迹 —— 判据不许建在空框上。")
    return (bb[0] - 4, bb[1] - 4, bb[2] + 4, bb[3] + 4)


def _rect(b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """墨迹框 `(x0, y0, x1, y1)` → `REGIONS` 的口径 **`(x, y, w, h)`**（`crop={w}:{h}:{x}:{y}`）。

    ⚠️ 这两个口径在同一个文件里并存，是 round20e4 现场被守卫抓出来的**真错**：取景框最早按
    `(x0, y0, x1, y1)` 写进 `REGIONS`，`_mask_counts` 却按 `crop={w}:{h}:{x}:{y}` 取景
    ⇒ `s09L2` 本应只盖 y 164–304 的横条实际盖成 **y 164–468**（`h` 位被当成 `x1`），
    判据随即在第 300 帧报出 10,399 px「窗口外亮着」——**画面是对的，框的口径是错的**
    （被拒版与收下版 sha256 逐字节相同，见 `docs/DEBT.md` D-21）。凡把墨迹框交给 `REGIONS`
    一律过这里，别直接塞。
    """
    x0, y0, x1, y1 = b
    return (x0, y0, x1 - x0, y1 - y0)


def _s09_x0(L: dict) -> int:
    """镜 9 一行的左缘 x（转调 `_plate_x0`）。"""
    return _plate_x0(L["text"], L["size"])


def _s09_ink_box(L: dict, seg: str | None = None) -> tuple[int, int, int, int]:
    """镜 9 一行（或行内一段）的墨迹框（转调 `_plate_ink_box`）。"""
    return _plate_ink_box(L["text"], L["size"], L["y"], seg)


def _s09_draw_line(im: Image.Image, L: dict, bright: bool) -> None:
    """画一行：正文段取 `INK`（正在讲的那条）/ `MUTE`（已经讲过的），**风险红段恒为 `RED`**。

    「前一条变灰」是台本 `:119` 的裁定。两处风险红（`开卷考试` / `击穿 3 条`）**不跟着变灰**：
    它们是这一镜的落点，跟着变灰等于把话收回半句，台本 `:123` 也没有「红只亮五秒」这层意思。
    灰用的是 `MUTE=(150,160,180)`，luma≈159 < `GRAY_BRIGHT=200` ⇒ 灰字**不会**被亮判据数进去，
    「出现后即转灰」才是逐帧可测的。
    """
    d = ImageDraw.Draw(im)
    f = _font(L["size"])
    base = INK if bright else MUTE
    red = L["red"]
    parts = ([(L["text"], base)] if red is None else
             [(L["text"].split(red)[0], base), (red, RED), (L["text"].split(red)[1], base)])
    x = _s09_x0(L)
    for seg, col in parts:
        if not seg:
            continue
        d.text((x, L["y"]), seg, font=f, fill=col, anchor="lm")
        x += int(f.getlength(seg))


def _s09_render(out_dir: pathlib.Path, sp: dict) -> None:
    lines = sp["lines"]
    onsets = [L["onset"] for L in lines]
    for n in range(sp["frames"]):
        im = Image.new("RGB", (W, H), BG)
        for i, L in enumerate(lines):
            if n < L["onset"]:
                continue
            nxt = onsets[i + 1] if i + 1 < len(onsets) else None
            _s09_draw_line(im, L, bright=(nxt is None or n < nxt))
        im.save(out_dir / f"s09_{sp['frames']:04d}_{n:05d}.png")


def _s09_region_table() -> dict[str, tuple[int, int, int, int]]:
    """镜 9 的取景框 —— 由 `_s09_ink_box` **实测**得到，框与框之间取中点 ⇒ 互不重叠。

    前三条各一个独立横条（判「出现后即转灰」）；末两行**共用一个**横条：它们字号 75/60、
    行距只有 75px，两条墨迹上下相接 ⇒ 拆不成两个不重叠的框（硬拆会让一条的框里混进
    另一条的墨，判据就变了味）。两处风险红各一个窄条（x 只盖那四个字 / 五个字）。
    """
    boxes = [_s09_ink_box(L) for L in _S09_LINES]
    out: dict[str, tuple[int, int, int, int]] = {}
    for i in range(3):
        top = 0 if i == 0 else (boxes[i - 1][3] + boxes[i][1]) // 2
        bot = (boxes[i + 1][1] + boxes[i][3]) // 2
        out[f"s09L{i + 1}"] = _rect((0, top, W, bot))
    out["s09tail"] = _rect((0, (boxes[2][3] + boxes[3][1]) // 2, W, min(H, boxes[4][3] + 8)))
    out["s09redL4"] = _rect(_s09_ink_box(_S09_LINES[3], "开卷考试"))
    out["s09redL5"] = _rect(_s09_ink_box(_S09_LINES[4], "击穿 3 条"))
    return out


# ----------------------------------------------------------------------------- S10 画面


def _s10_lines() -> list[dict]:
    """镜 10 收尾：封面版式三行 + 一行琥珀小字（台本 `:127-129`）。

    ⚠️ 台本写「**第四行**小字」，但原文 54 字，1920px 宽下要读得清就得 38 号以上
    ⇒ **一行放不下**，这里折成两行（语义一字不改，只改断行）。
    ⚠️ 台本写「定格时下方浮出第四行」「最后一行出现时全片结束定格 2 秒」
    ⇒ 第 375 帧（12.5s）浮出，375–434 共 60 帧**逐帧相同**（定格），435 起黑场 1.5s。
    ⚠️ 第四行**必须走琥珀**：§8.5 ⑥ 裁定「全片风险红预算 = 3 处，只给镜 2 与镜 9」
    ⇒ 本镜一个风险红像素都不许有（判据里那条 `zero/red` 就是这条裁定的化身）。
    色值直接用 `AMBER`（＝镜 5 旁注同色 0xD98A00），不另造一个"差不多"的琥珀。
    """
    return [
        dict(text="判定权归算法", size=80, y=380, onset=0, amber=False),
        dict(text="解释权归语言", size=80, y=520, onset=0, amber=False),
        dict(text="免责权归创作者", size=80, y=660, onset=0, amber=False),
        dict(text="以上指标均为自造集口径；把规则表全文交给对手后，",
             size=38, y=880, onset=375, amber=True),
        dict(text="白盒陷害侧误报 69.7%、红线被击穿 3 条（见镜 9）",
             size=38, y=940, onset=375, amber=True),
    ]


_S10_LINES = _s10_lines()
_S10_FRAMES = 480                        # 16.0s × 30fps（台本「镜 10｜02:43–02:59（16s / 82 字）」）
_S10_HOLD_FROM = 375                     # 12.5s：琥珀小字浮出 ⇒ 定格 60 帧（2.0s）
_S10_BLACK_FROM = 435                    # 14.5s：黑场 45 帧（1.5s），台本 `:132`
_S10_TITLE = "LorealGuard"               # 封面版式的标题（台本 `:127`「标题淡出」）
_S10_TITLE_Y = 200
_S10_TITLE_FADE = 45                     # 标题淡出占 45 帧（1.5s）


def _s10_title_color(n: int) -> tuple[int, int, int]:
    """标题淡出用的颜色：第 0 帧是 `INK`，到第 45 帧退成背景色。**帧号驱动，不读时钟**。

    这样"淡出"就成了可测的东西：标题带的亮像素**只在 0–44 帧**有，之后恒 0
    —— 判据 `windows(bright, s10title, [(0, 44)])` 一旦画面忘了退，当场红。
    """
    k = min(1.0, n / float(_S10_TITLE_FADE))
    return (int(round(INK[0] + (BG[0] - INK[0]) * k)),
            int(round(INK[1] + (BG[1] - INK[1]) * k)),
            int(round(INK[2] + (BG[2] - INK[2]) * k)))


def _s10_frame(n: int) -> Image.Image:
    """第 n 帧：封面版式（标题 + 三行）＋到点浮出的琥珀小字。黑场与定格由 `_s10_render` 管。"""
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((W // 2, _S10_TITLE_Y), _S10_TITLE, font=_font(100),
           fill=_s10_title_color(n), anchor="mm")
    for L in _S10_LINES:
        if n < L["onset"]:
            continue
        d.text((_plate_x0(L["text"], L["size"]), L["y"]), L["text"],
               font=_font(L["size"]), fill=AMBER if L["amber"] else INK, anchor="lm")
    return im


def _s10_render(out_dir: pathlib.Path, sp: dict) -> None:
    """逐帧画。

    ⚠️ **定格段用的是同一个 `Image` 对象**：`hold` 只画一次，375–434 那 60 帧存的是同一份
    位图 ⇒ 60 个 PNG 逐字节相同。这是"定格"在本工具里的口径（复制真实末帧，禁插黑、
    禁循环、禁造新帧 —— §8.5 ③）；解码侧的逐帧相同另属常驻守卫，见 `docs/DEBT.md`。
    """
    hold: Image.Image | None = None
    for n in range(sp["frames"]):
        if n >= _S10_BLACK_FROM:
            im = Image.new("RGB", (W, H), (0, 0, 0))       # 台本 `:132`：结尾黑场 1.5s
        elif n >= _S10_HOLD_FROM:
            if hold is None:
                hold = _s10_frame(n)                       # 只画这一次 ⇒ 后面的帧与它同源
            im = hold
        else:
            im = _s10_frame(n)
        im.save(out_dir / f"s10_{sp['frames']:04d}_{n:05d}.png")


def _s10_region_table() -> dict[str, tuple[int, int, int, int]]:
    """镜 10 的三个取景框（实测所得）。

    ⚠️ 口径是 `(x, y, w, h)` —— 与 S09 那次被守卫抓出来的**真错**同一处（`_s09_region_table`
    里记了全过程：写成 `(x0, y0, x1, y1)` 会让 `crop=` 把 `h` 位当成 `x1`，判据就在窗口外
    凭空看见亮像素）。所以这里也一律过一遍 `rect()`。
    """
    three = [_plate_ink_box(L["text"], L["size"], L["y"]) for L in _S10_LINES[:3]]
    note = [_plate_ink_box(L["text"], L["size"], L["y"]) for L in _S10_LINES[3:]]
    title = _plate_ink_box(_S10_TITLE, 100, _S10_TITLE_Y)
    mid_title = (title[3] + three[0][1]) // 2          # 标题墨迹底 ↔ 第一行墨迹顶 的中点
    mid_note = (three[2][3] + note[0][1]) // 2         # 第三行墨迹底 ↔ 琥珀小字顶 的中点
    return {
        "s10title": _rect((0, max(0, title[1] - 8), W, mid_title)),
        "s10three": _rect((0, mid_title, W, mid_note)),
        #: 琥珀小字两行合一个框：38 号字、60px 行距 ⇒ 两行墨迹相接，拆不出两个不重叠的框
        "s10note": _rect((max(0, min(note[0][0], note[1][0]) - 8), note[0][1] - 8,
                         W, min(H, note[1][3] + 8))),
        #: 整帧框：给「末 45 帧是黑场」那条判据用。比传 `region=None` 更显式 ——
        #: 免得某个调用点忘了 `None` 的语义，判据悄悄量成了别的东西而看着仍然 ✅。
        "s10full": (0, 0, W, H),
    }


# ----------------------------------------------------------------------------- 声明表


def _cards_and_keywords() -> tuple[list[dict], list[dict]]:
    """三张卡片 / 三条关键词 —— 时刻是**裁定值**，写成一张表而不是散在绘制代码里。"""
    cards = [
        dict(title="账本可复跑", tag="可复跑", onset=72, ramp=9, y=320, h=150),
        dict(title="解释可读成话", tag="可读成话", onset=168, ramp=9, y=500, h=150),
        dict(title="归因可追溯", tag="可追溯", onset=264, ramp=9, y=680, h=150),
    ]
    for c in cards:
        c["x"] = 200
        c["w"] = 1520
    #: 每条关键词**停留 1.2s** ＝ 36 帧（@30fps），条目之间留 0.2s(6 帧) 的间隔 ——
    #: 有了间隔，「逐条上屏」在像素上才是三段可分辨的窗口，而不是一段连续的亮。
    kws = [("判定权归算法", 360), ("解释权归语言", 402), ("免责权归创作者", 444)]
    keywords = [dict(text=t, first=f, last=f + 35, y=950, size=64) for t, f in kws]
    return cards, keywords


_CARDS, _KEYWORDS = _cards_and_keywords()

#: 判据用的**取景框**（x, y, w, h）。为什么要写在这里：框=布局事实，画面画在哪，
#: 就该量在哪 —— 让剪辑工具自己猜框，等于让它猜一个它不拥有的知识。
#: 逐条的"框里除了要量的东西还有没有别的"都由绘制代码保证（见上面各 `_render`）。
REGIONS: dict[str, tuple[int, int, int, int]] = {
    "caption": (0, 850, 1920, 200),      # S02 底部六个字（放大前后都在框内）
    "headline": (0, 90, 1920, 250),      # S03 开场大字
    "cardband": (0, 300, 1920, 560),     # S03 三段卡片带
    "tagstrip": (1300, 300, 460, 560),   # S03 卡片右侧小标那一竖条（只有琥珀字进来）
    "keyword": (0, 880, 1920, 170),      # S03 三条关键词那一横条
}

#: 镜 9 的取景框（实测所得，见 `_s09_region_table`）—— 并进来而不是写进上面那张表，
#: 因为它的边界是**量出来的**（取决于字体渲染的实际墨迹），不是排片时就能写死的整数。
REGIONS.update(_s09_region_table())
REGIONS.update(_s10_region_table())

_S02_RED = [
    dict(name="种草图红叉", onset=90, ramp=6, box=(200, 270, 840, 710)),
    dict(name="头像剪影红叉", onset=186, ramp=6, box=(1080, 260, 1720, 710)),
]

#: ⚠️ 这些 `first/last/onset` 是**声明**，`verify` 拿实测像素去对的就是它们。
#: 两边都能红：实测与声明不符 ⇒ 拒收；声明本身写错（窗口长度≠36、时刻越界）⇒ 当场红。
#:
#: `checks` 是**要拿实测像素去对的那几条**（判据的"检查项"由本模块声明，怎么量由
#: `make_film_segment.py` 定 —— 画与量分开，但"量什么"归画面这一侧）：
#:   steps   规则色像素随时间**只跳声明的那几次**，其余时段平台；跳的落点必须落在
#:           声明的 `[onset, onset+ramp]` 里，越窗即红。⇒「两把红叉先后落下」可测。
#:   zero    整片该规则色**恒为 0**。⇒ S03「满屏琥珀却零红」可测（红预算守卫）。
#:   windows 该色像素**恰好**只在声明的窗口里有。⇒「每条停留 1.2s」是逐帧可测的。
#:   zoom    某框内亮像素在 0.4s 窗口里涨到 `min_gain` 倍、之后回落到原位。⇒「放大后回落」。
#:   blob    末帧该色像素的**填充率**（占自身外接框）< max_fill 且占全屏 < max_share。
#:           ⇒「红叉是笔画、不是一块纯红异常块」可测。
PLATE_SPECS: dict[str, dict] = {
    "s02": dict(
        id="s02",
        frames=570,                     # 槽位 19.0s × 30fps
        why="镜 2：两把红叉先后落下（种草图 / 头像剪影各一），底部六个字放大 0.4s 后回落",
        #: 红叉时刻：onset 停在这一帧**第一次有红**，ramp 帧内长满。
        red_cues=_S02_RED,
        zoom=dict(text="误伤补不回来", first=270, last=281, y=950, size=56, size_big=78),
        checks=[
            dict(kind="steps", rule="red", what="两把红叉先后落下（且全片只有这两处红）",
                 cues=[(c["onset"], c["ramp"]) for c in _S02_RED],
                 min_delta=400, plateau_tol=0.02),
            dict(kind="blob", rule="red", what="红叉是笔画，不是一块纯红异常块",
                 max_fill=0.25, max_share=0.08),
            dict(kind="zoom", rule="bright", region="caption", what="六个字放大 0.4s 后回落",
                 first=270, last=281, min_gain=1.3, flat_tol=0.15),
        ],
    ),
    "s03": dict(
        id="s03",
        frames=570,
        why="镜 3：三张卡片逐张推入（右侧各出一个可交付小标），三条关键词各停留 1.2s",
        red_cues=[],                    # ← 本镜**零红**（红预算只有 S02 的两处 + S09 的一处）
        headline=dict(text="我们不判真假 —— 我们出一本可复算的账",
                      first=0, last=65, y=180, size=64),   # 0.0–2.2s ＝ 66 帧
        cards=_CARDS,
        keywords=_KEYWORDS,
        checks=[
            dict(kind="zero", rule="red", what="本镜零红（红预算只有 S02 两处 + S09 一处）"),
            dict(kind="steps", rule="bright", region="cardband", what="三张卡片逐张推入",
                 cues=[(c["onset"], c["ramp"]) for c in _CARDS],
                 min_delta=150, plateau_tol=0.02),
            dict(kind="steps", rule="amber", region="tagstrip", what="三张卡各出一个可交付小标",
                 cues=[(c["onset"], c["ramp"]) for c in _CARDS],
                 min_delta=50, plateau_tol=0.03),
            dict(kind="windows", rule="bright", region="headline",
                 what="开场大字只在 0.0–2.2s",
                 windows=[(0, 65)]),
            dict(kind="windows", rule="bright", region="keyword", what="三条关键词各停留 1.2s",
                 windows=[(k["first"], k["last"]) for k in _KEYWORDS]),
        ],
    ),
    "s09": dict(
        id="s09",
        frames=_S09_FRAMES,             # 780 ＝ 槽位 26.0s × 30fps
        why="镜 9：五条「不做」逐条上屏（讲过的转灰），末两行是我们自己先捅的那一刀；"
            "两处风险红（开卷考试 / 击穿 3 条）—— 全片红预算的第三处就在这里",
        #: 两处风险红：`box` 由 `_s09_ink_box` 实测（与 REGIONS 里那两条窄条同源）。
        #: 它们**不是**「落下」的红叉，而是字里的红 ⇒ 绘制侧不走 `_reveal`，也没有 ramp 动画。
        red_cues=[
            dict(name="开卷考试", onset=450, ramp=1,
                 box=_s09_ink_box(_S09_LINES[3], "开卷考试")),
            dict(name="击穿 3 条", onset=600, ramp=1,
                 box=_s09_ink_box(_S09_LINES[4], "击穿 3 条")),
        ],
        lines=_S09_LINES,
        #: ⚠️ 这里**故意没有** steps 判据：五条里的灰字与亮字像素量几乎相等，
        #: 「上屏」在总量上是一个平台而不是一次跳变 —— 拿 steps 判它必然假红（那是误报，
        #: 不是判据）。改成逐条独立的 windows：某条的亮像素**恰好**只在它自己那几秒里，
        #: 超出窗口即红 —— 那恰好就是「讲过的转灰」在像素上的样子，而且比 steps 更硬。
        checks=[
            dict(kind="windows", rule="red", region="s09redL4",
                 what="`开卷考试` 的红在第四行上屏后一直亮到片尾", windows=[(450, 779)]),
            dict(kind="windows", rule="red", region="s09redL5",
                 what="`击穿 3 条` 的红在第五行上屏后亮到片尾", windows=[(600, 779)]),
            dict(kind="blob", rule="red", what="红是字，不是一块纯红异常块",
                 max_fill=0.55, max_share=0.08),
            dict(kind="windows", rule="bright", region="s09L1",
                 what="第一条只在 0.0–5.0s 亮，第二条上屏后转灰", windows=[(0, 149)]),
            dict(kind="windows", rule="bright", region="s09L2",
                 what="第二条只在 5.0–10.0s 亮，第三条上屏后转灰", windows=[(150, 299)]),
            dict(kind="windows", rule="bright", region="s09L3",
                 what="第三条只在 10.0–15.0s 亮，第四条上屏后转灰", windows=[(300, 449)]),
            dict(kind="windows", rule="bright", region="s09tail",
                 what="末两行上屏后亮字一直在（到片尾）", windows=[(450, 779)]),
        ],
    ),
    "s10": dict(
        id="s10",
        frames=_S10_FRAMES,             # 480 ＝ 16.0s × 30fps
        why="镜 10 收尾：封面版式三行 + 琥珀小字（自造集口径 / 69.7% / 击穿 3 条）+ 定格 2s + 黑场 1.5s",
        #: 本镜**零风险红**（§8.5 ⑥：全片红预算只给镜 2 两把红叉 + 镜 9 一处）
        #: ⇒ 下面第一条判据就是这条裁定的化身，不需要任何 `red_cues`。
        red_cues=[],
        lines=_S10_LINES,
        checks=[
            #: ⚠️ 这条是**全片红预算**的一部分，口径来自 §8.5 ⑥，阈值一字不改：
            #: `zero` ＝ 整片该规则色恒为 0 ⇒ 「本镜一个风险红像素都不许有」。
            dict(kind="zero", rule="red",
                 what="全片零风险红（§8.5 ⑥：红预算只给镜 2 两把红叉 + 镜 9 一处）"),
            #: 窗口 (0, 11) 是**量出来的**不是算的：线性退色算式给 n>7.9，但 ffmpeg 的
            #: `format=gray` 是**有限范围**亮度（黑 16/白 235），阈值 200 落在它上面
            #: ⇒ 实测第 8 帧仍 4576 px、9→141、10→17、11→2（抗锯齿边缘的趋零尾巴）
            #: ⇒ **非零到第 11 帧**，窗口取 (0, 11)。
            #: 这条判据证的是「标题**没有**亮到黑场」：一旦忘了退色，第 12 帧起仍有亮像素 ⇒ 当场红。
            dict(kind="windows", rule="bright", region="s10title",
                 what="封面标题只在头 12 帧（0–0.4s）亮过，之后亮度降到 `GRAY_BRIGHT` 以下",
                 windows=[(0, 11)]),
            dict(kind="windows", rule="bright", region="s10three",
                 what="三行字一直在（到黑场为止）", windows=[(0, 434)]),
            #: 琥珀字不算 bright（亮度 ≈146 < `GRAY_BRIGHT` 200）⇒ 必须用 amber 判色。
            dict(kind="windows", rule="amber", region="s10note",
                 what="琥珀小字只在定格那 2s 在（窗口外恒 0）", windows=[(375, 434)]),
            dict(kind="windows", rule="bright", region="s10full",
                 what="全屏亮像素到第 434 帧为止 ⇒ 末 45 帧是黑场", windows=[(0, 434)]),
        ],
    ),
}


def spec(pid: str) -> dict:
    """取声明表。**未知 pid 当场红** —— 不许静默回落到某一段画面。"""
    if pid not in PLATE_SPECS:
        raise RuntimeError(
            f"plate={pid!r} 不认识（已知：{sorted(PLATE_SPECS)}）—— "
            f"plate 名写错不许静默回落到别的画面：那样出来的是**另一镜的内容**，"
            f"而帧数、时长、几何全部照常为真。")
    return PLATE_SPECS[pid]


def _validate(sp: dict) -> None:
    """声明表自身的结构性判据（与 `verify` 的像素判据**分开**：这一层不看不看片就能红）。"""
    pid = sp["id"]
    n = sp["frames"]
    for cue in sp["red_cues"]:
        if not (0 <= cue["onset"] < n) or cue["ramp"] < 1:
            raise RuntimeError(f"{pid}：红叉声明 {cue['name']} 的 onset={cue['onset']} / "
                               f"ramp={cue['ramp']} 越界（帧数 {n}）—— 声明本身不成立。")
        if not (cue["box"][0] < cue["box"][2] and cue["box"][1] < cue["box"][3]):
            raise RuntimeError(f"{pid}：红叉 {cue['name']} 的 box 不是矩形。")
    if "zoom" in sp:
        z = sp["zoom"]
        if not (0 < z["first"] <= z["last"] < n):
            raise RuntimeError(f"{pid}：放大窗口 {z['first']}..{z['last']} 越界（帧数 {n}）。")
        if z["last"] - z["first"] + 1 != int(round(0.4 * FPS)):
            raise RuntimeError(
                f"{pid}：放大窗口 {z['first']}..{z['last']} 是 "
                f"{z['last'] - z['first'] + 1} 帧 ≠ 0.4s×30fps=12 帧 —— "
                f"「放大 0.4 秒」是裁定值，声明表与控制不了它的改动不一致时当场红。")
        if z["last"] + 1 >= n:
            raise RuntimeError(f"{pid}：放大窗口贴着片尾，没有「回落之后」的帧可比。")
    if "headline" in sp:
        hl = sp["headline"]
        if hl["first"] != 0 or hl["last"] != int(round(2.2 * FPS)) - 1:
            raise RuntimeError(f"{pid}：开场大字声明 {hl['first']}..{hl['last']} ≠ "
                               f"0.0–2.2s（0..65）—— 这是裁定窗口，不许在这里挪。")
    for c in sp.get("cards", []):
        if c["onset"] + c["ramp"] >= n:
            raise RuntimeError(f"{pid}：卡『{c['title']}』推入到 {c['onset'] + c['ramp']} 帧，"
                               f"已越过片尾 {n} —— 推入还没完片子就结束了。")
    _validate_keywords(pid, sp.get("keywords", []), n)
    for ck in sp["checks"]:
        _validate_check(pid, ck, n)


def _validate_check(pid: str, ck: dict, n: int) -> None:
    """判据声明自身的结构 —— 这一层**不看不看片**就能红：判据写错了就不该开跑。

    为什么要有：一条"量不出来/量错地方"的判据在成片上只会表现为**绿**（比如框写到了
    没有字的空白处，那里恒为 0，"恒为 0"与"符合声明"长得一模一样）。这类判据比没有
    判据更坏 —— 它会让报告里多出一行没人质疑的 ✅。
    """
    kind = ck.get("kind")
    if kind not in ("steps", "zero", "windows", "zoom", "blob"):
        raise RuntimeError(f"{pid}：判据 kind={kind!r} 不认识"
                           f"（只认 steps / zero / windows / zoom / blob）。")
    if ck.get("rule") not in ("red", "amber", "bright"):
        raise RuntimeError(f"{pid}：判据 rule={ck.get('rule')!r} 不认识（只认 red / amber / bright）。")
    if "region" in ck and ck["region"] not in REGIONS:
        raise RuntimeError(f"{pid}：判据『{ck.get('what')}』要量的框 {ck['region']!r} 不在 REGIONS 里"
                           f"（已知 {sorted(REGIONS)}）—— 框名写错时若不报错，就会在某个空框上恒绿。")
    if not ck.get("what"):
        raise RuntimeError(f"{pid}：有条判据没有 what —— «量的是什么» 必须写出来，"
                           f"否则红了也没人知道是哪一条不成立。")
    #: cues 是 (落点, 渐变帧数)、windows 是 (首帧, 末帧) —— 两者的第二项语义不同，
    #: 所以分开判，不能共用 `a <= b` 那一条。
    for onset, ramp in ck.get("cues") or []:
        if not (0 <= onset < n) or ramp < 1 or onset + ramp >= n:
            raise RuntimeError(f"{pid}：判据『{ck['what']}』的 cue=({onset},{ramp}) 越界"
                               f"（帧数 {n}）—— 落点或渐变长度不成立。")
    for first, last in ck.get("windows") or []:
        if not (0 <= first <= last < n):
            raise RuntimeError(f"{pid}：判据『{ck['what']}』的 window=({first},{last}) 越界"
                               f"（帧数 {n}）。")
    if kind == "steps" and not ck.get("cues"):
        raise RuntimeError(f"{pid}：steps 判据『{ck['what']}』没有 cues —— 那它就该写成 zero。")


def _validate_keywords(pid: str, kws: list[dict], n: int) -> None:
    want = int(round(1.2 * FPS))
    prev_last = -1
    for k in kws:
        span = k["last"] - k["first"] + 1
        if span != want:
            raise RuntimeError(
                f"{pid}：关键词『{k['text']}』停留 {span} 帧 ≠ 1.2s×30fps={want} 帧 —— "
                f"「每条停留 1.2s」是裁定值，声明与画面不符时当场红。")
        if k["first"] <= prev_last or k["last"] >= n:
            raise RuntimeError(f"{pid}：关键词『{k['text']}』的窗口 {k['first']}..{k['last']} "
                               f"与前一条重叠或越界 —— 窗口必须两两分开才能逐条判。")
        prev_last = k["last"]


def render(pid: str, out_dir: pathlib.Path, frames: int) -> int:
    """把一段画成 `frames` 张 PNG。返回落盘张数（调用方拿它去对 Σ片长）。"""
    sp = spec(pid)
    _validate(sp)
    if frames != sp["frames"]:
        raise RuntimeError(
            f"{pid}：声明表按 {sp['frames']} 帧写死（时刻都是帧号），调用方却给了 {frames} 帧 —— "
            f"时刻表与排片对不上时不许按比例缩放：那会让每一个声明时刻都变成假的。")
    out_dir.mkdir(parents=True, exist_ok=True)
    if "zoom" in sp:
        if _text_ink(sp["zoom"]["text"], sp["zoom"]["size_big"]) == 0:
            raise RuntimeError(f"{pid}：『{sp['zoom']['text']}』画出来是空的（0 个墨点）。")
    if "headline" in sp:
        if _text_ink(sp["headline"]["text"], sp["headline"]["size"]) == 0:
            raise RuntimeError(f"{pid}：开场大字画出来是空的（0 个墨点）。")
    for k in sp.get("keywords", []):
        if _text_ink(k["text"], k["size"]) == 0:
            raise RuntimeError(f"{pid}：关键词『{k['text']}』画出来是空的（0 个墨点）。")
    for c in sp.get("cards", []):
        if _text_ink(c["title"], 46) == 0 or _text_ink(c["tag"], 40) == 0:
            raise RuntimeError(f"{pid}：卡『{c['title']}』/ 小标『{c['tag']}』画出来是空的。")
    #: 镜 9 / 镜 10 的每一行 + 每一处风险红段都要**逐段量墨**：缺字形（如 `→` / 全角括号）
    #: 或编码不对时，那一段在画面里是空的，而帧数、时长、几何**全部照常为真** —— 本项目最恨的静默。
    #: ⚠️ 取 `L.get("red")`：镜 10 的行没有 `red` 键（它整片零红），写死 `L["red"]` 会 KeyError。
    for L in sp.get("lines", []):
        for seg in ([L["text"]] if L.get("red") is None else [L["text"], L["red"]]):
            if _text_ink(seg, L["size"]) == 0:
                raise RuntimeError(f"{pid}：『{seg}』画出来是空的（0 个墨点）—— "
                                   f"缺字形或编码不对时画面是空的，而帧数与时长全部照常为真。")

    {"s02": _s02_render, "s03": _s03_render, "s09": _s09_render, "s10": _s10_render}[pid](out_dir, sp)

    # 结构性判据：落盘文件名必须**恰好**是 `pid_frames_00000..frames-1`。
    # 多出来的是上一版残留（换了帧数就会露出来，且会被 `%05d` 图案静默吃进片尾），
    # 少了的就是"片尾短了"而 fps 与时长看起来仍然对。两个方向都要红。
    want = [f"{pid}_{frames:04d}_{i:05d}.png" for i in range(frames)]
    have = sorted(p.name for p in out_dir.glob(f"{pid}_{frames:04d}_*.png"))
    if have != want:
        extra = sorted(set(have) - set(want))[:5]
        miss = len(want) - len(set(have) & set(want))
        raise RuntimeError(
            f"{pid}：落盘 {len(have)} 张 ≠ 应有 {frames} 张（缺 {miss}，多 {extra}）—— "
            f"多出来的残帧会被图像序列图案静默吃进片尾，不敢继续。")
    return len(have)
