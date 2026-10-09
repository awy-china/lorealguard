"""镜头工厂：把台本里**从一张静态图长出来**的那一镜做成可复核的成片段（本轮＝镜 1）。

为什么要有第二个剪辑工具（而不是往 `tools/make_film_segment.py` 里塞）
------------------------------------------------------------------------
`make_film_segment.py` 消费的是**已经录好的 mp4**，它的能力边界是「掐点 + 拼 + 叠卡片」。
镜 1 要的是**从一张 png 里逐条长出来**（擦除进入）—— 这是"造帧"，不是"剪帧"：
没有剪点、没有场景检测、没有 onset，几何全部来自**像素量测**而不是已有帧号。
硬塞进去会把那个工具四条已经跑绿的判据搅浑（铁律 10：无指令不动无关代码）。
⇒ 本文件独立成篇，但**沿用同一套约定**：槽位/命名、两段式验收、`_rejected/` 拒收、
   `_utf8_stdout()` 编码钉子、成功行 `ok_line()`。

三条比"好看"更重要的线
----------------------
① **不许另画一张图**。画面只能是 `D:/deliver/compete/loreal-ppt/img/fig_star.png` 本体。
   新画一张 = 屏幕上的数字来源说不清（CLAUDE.md 铁律 1/9：产物必须能从冻结语料重放）。
   本工具因此**一处 drawtext 都没有** —— 画面上每一个字都来自那张图。
② **数字不许硬编码**。5,667 / 327 / 17.33 全部由 `promises()` 从
   `05-同类作品与差异化定位.md` 解析，并断言"两数相除 == 比值"。
   源码里出现这三个字面量 ⇒ 变异测试 M61 会红。
③ **判定权不在这**。本工具只产画面，不产任何"真假"结论。

画面规格（对台本 `loreal-video-script.md` 镜 1 的三句原文）
----------------------------------------------------------
    原文：「黑场起 → `fig_star` 图表从左侧逐条"长"出来（动画：条形用擦除进入，不要弹跳）」
    原文：「首个长条 5,667 的红色条形压满画面」
    原文：「口播到"十七比一"时，图上 `1 : 17` 比值描边闪一次；不配音效」

    ① 黑场起 + 逐条擦除：7 条带（标题 / 副标题 / 5 根条形）各一个黑色遮罩，
       遮罩的 `x` 是**时间的表达式** ⇒ 从左往右擦出去。
       ⚠️ 擦除只能用 `overlay` 的 `x='…t…'`：本机 ffmpeg 8.1.2 上 `drawbox` 的
       时间相关表达式**静默不画**（实测 `x='100'` → 均值 243.93；`x='100*t'` →
       同刻 t=0 与 t=1s 均值都是 255.0，即**什么都没画且不报错**）。这条是踩出来的。
    ② 「压满画面」**降级实现**（台本允许降级，但必须写进本段的 `why=`）：
       不许另画图，而**尾帧必须同时读得出 5,667 / 327 / 17.33 三个承诺串** ——
       任何推近裁切都会把 327 与 17.33 挤出画面，自相矛盾。
       故按「整幅满宽」处理：裁内容框 → 缩放到满宽 1920 → 居中；5,667 那条红条是图里
       最长的一根，满宽后它就是画面主体。
    ③ 描边闪一次：在**量出来的** `1 : 17.33` 比值框上套一个 drawbox **描边**
       （thickness，不是填充），`enable='between(t,起,起+0.45)'`。
       描边用**白色不用风险红**：台本第六节把风险红预算定成「全片 ≤3，镜 2 两处 + 镜 9 一处」，
       镜 1 不得新增（那条红条是**素材自带**的，不是我们加的）。
       不配音效 —— 本工具根本不产出音轨（与 `make_film_segment.py` 一致）。

四项自证（缺一即"假通过"）
--------------------------
    ① 几何 / 时长 / 帧数：ffprobe 读数 == 1920×1080 / 30fps / 16.000s / 480 帧
    ② 装得下配音：段长 ≥ 该镜配音 mp3 的**实测** ffprobe 时长。装不下**拒收** ——
       不许拉长槽位、不许剪字。
    ③ **尾帧 VL 逐字读回**：把最后一帧交给 `voiceguard.semantic.vl.ask_vl`，读回的文本里
       必须**全部**命中 `promises()` 解析出的承诺串。读不回 = 拒收（没 key / 断网也拒收，
       这是刻意的：证明不了就不许冒充成片）。
    ④ 拒收路径**可证伪**：任一条不过 ⇒ mp4 挪进 `output/film/_rejected/` + 非 0 退出；
       `tests/test_film_shots.py` 里有一条用例专门证明"真的挪了 + 真的非 0"。

额外三条离线判据（不依赖网络，纯像素）
--------------------------------------
    ⑤ **首帧全黑**（= "黑场起"，第 0 帧最亮 ≤ `BLACK_MAX`=8）—— 有**几何版**先在排片时拦：
       `first_frame_is_covered()`：源图任何"不是背景"的行都必须落在某个遮罩内。
       实测（round17b）抓到的就是它：条带取法漏行 + Lanczos 晕染 ⇒ 4 个帧行露在黑场外。
    ⑥ **逐条揭示**：每条带在它自己的时刻之前**真的是黑的**、擦完之后**真的有字**，
       且次序严格递增 —— 把"动效"这件事变成像素读数，不是"我觉得像"。
    ⑦ **闪一次且尾帧干净**：比值框在闪的那 0.45s 内**变过**，而尾帧与"闪之前"的差必须
       **远小于闪本身**（`tail_is_clean`：≤ max(16, 闪幅×0.25)）。⚠️ **不是逐位相等** ——
       有损编码的量化漂移让静态帧之间也有 ≤14 的残留，写 `== 0` 的判据永远绿不了。

用法
----
    cd /d/LorealGuard && env -u PYTHONPATH -u PYTHONUTF8 -u PYTHONIOENCODING \\
        .venv/Scripts/python.exe tools/make_film_shots.py
    （只看量测不落盘：--probe ；不重建只复核：--verify-only ；只做某镜：--only S01）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voiceguard.semantic.vl import ask_vl  # noqa: E402

#: 成片导出规格（`loreal-video-script.md` 第六节）：1920×1080 / 30fps。
W, H, FPS = 1920, 1080, 30

FILM = ROOT / "output" / "film"
REJECTED = FILM / "_rejected"
WORK = FILM / "_shots"

#: 台本 / 调研 / 素材 / 配音 —— 全部**只读**（`D:/deliver/compete/` 是交付区，只读）。
SCRIPT = pathlib.Path("D:/deliver/compete/loreal-video-script.md")
RESEARCH = pathlib.Path("D:/deliver/compete/loreal-research/05-同类作品与差异化定位.md")
PPT_IMG = pathlib.Path("D:/deliver/compete/loreal-ppt/img")
VOICE = pathlib.Path("D:/deliver/compete/voice/out")
SRT = VOICE / "lorealguard-demo.srt"

#: 自证笔录落盘处（本轮要求：`output/_film_shots_round17.txt`）。
TRANSCRIPT = ROOT / "output" / "_film_shots_round17.txt"

QUESTION = (
    "这是一张视频截图的画面。请**逐字抄写**画面上的文字，"
    "不要解释、不要总结，尤其要准确抄下所有数字和百分号。看不清的字写『?』。"
)

# ---- 揭示动画的设计常量（**不是口径数字**：它们只决定"什么时候擦"，不决定画面内容）----
WIPE = 0.45          # 单条带擦除耗时（秒）
TITLE_AT = 0.35      # 标题开始擦：之前是**纯黑场**（"黑场起"）
SUB_GAP = 0.80       # 副标题相对标题收完后的间隔
FLASH_HOLD = 0.35    # 最后一条收完 → 比值闪，之间留的稳定观看时间
FLASH_LEN = 0.45     # 描边闪烁时长

#: 白描边。**不用风险红**：台本第六节「全片风险红 ≤3（镜 2 两处 + 镜 9 一处）」，镜 1 不占额度。
FLASH_COLOR = "white"
FLASH_THICK = 6
FLASH_PAD = 14       # 描边框相对比值文字框外扩的像素（缩放后）

#: 条带的**纵向范围**用"软阈值"（任何不是背景的像素）定，不用"前景像素够多"定。
#: 实测（round17b）：`fig_star.png` 第 164 行只有 18 个 `dist>24` 的像素（阈值写的是 `>20`），
#: 被切掉 —— 可它是副标题的真实内容（源图峰值 166）。切掉的后果不是"量测难看"，
#: 是那一行**没有遮罩** ⇒ 成片第 0 帧最亮 110（台本写的是『黑场起』）。
SOFT_THR = 6

#: 遮罩相对条带**上下各外扩**的帧行数。
#: 为什么必须有：`scale=…:flags=lanczos` 是**带旁瓣的重采样**，条带边缘的内容会晕染到
#: 相邻 1~2 个帧行上（实测：bar3/4/5 遮罩上沿之外那一行分别是 38 / 30 / 21）。
#: 只按条带边界摆遮罩 ⇒ 那几行永远露着。帧行间距最小 30（title→subtitle），±3 不打架。
MASK_PAD = 3

#: 『黑场起』的判据：成片**第 0 帧最亮像素 ≤ 8**（纯黑）。台本镜 1 原文就是"黑场起"。
BLACK_MAX = 8

#: 尾帧"没有描边残留"的容忍上限 ＝ **闪幅度 × 这个比例**（不是"逐位相等"，见 `tail_is_clean`）。
TAIL_RESIDUE_RATIO = 0.25

#: "这一镜是图表演示镜"的标记。台本里镜 1 没有源 mp4，画面＝一张图。
KIND_FIGBARS = "figbars"


def ok_line(checks: list[str]) -> str:
    """成功行。**只列真正跑过的判据** —— 把没跑的判据写进成功行就是假通过。"""
    return f"    ✅ 本轮实跑判据全部通过：{' / '.join(checks)}"


def _utf8_stdout() -> None:
    """把 stdout/stderr 钉成 UTF-8 —— 成功行带 `✅`，不钉就**自己崩**。

    ⚠️ `docs/DEBT.md` D-07 那类 bug（Windows 上 stdout 不是终端时按 locale 编码，
    本机 GBK，打印 U+2705 抛 `UnicodeEncodeError`）。最坏的不是崩，是**崩在最后一行**：
    判据全过、mp4 已落盘，却报非 0 退出 ⇒ 看的人以为片子坏了，去重剪一段本来没问题的片子。
    与 `tools/make_film_segment.py` 的 `_utf8_stdout()` 逐字同源。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass          # 已被包成非 TextIOWrapper：不拦，继续跑


class _Tee:
    """把 stdout 同时写进自证笔录。`reconfigure` 要转发 —— 否则 `_utf8_stdout()` 白钉。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()

    def reconfigure(self, **kw):
        for st in self.streams:
            try:
                st.reconfigure(**kw)
            except (AttributeError, ValueError, OSError):
                pass


# ===================================================================== 数字：只许解析
def promises() -> list[dict]:
    """从调研 md 解析三个**承诺串**（会被 VL 逐字读回的那些），并按算术自校。

    ⚠️ **一个字都不许手抄**（台本交接的硬要求）：手抄的数字与画面会各自漂移，
    而漂移了没人知道。这里解析不到就**抛错退出**（宁红不静默）。
    """
    text = RESEARCH.read_text(encoding="utf-8")
    lines = text.splitlines()

    def _last_bold_num(needle: str) -> str:
        """取**最后一次**提到这个仓库的那一行的**最后一个**加粗数字。

        ⚠️ 必须取最后一行：这份 md 是**流水账**，同一仓库在 09-17 / 09-18 / 09-25
        各出现一次（5,580 → 5,597 → 5,667）。取第一行会静静拿到过期数字 ——
        而"画面上的数与调研对不上"正是这一轮要防的那件事。
        """
        found = ""
        for ln in lines:
            if needle in ln:
                hits = re.findall(r"\*\*([\d,]+)\*\*", ln)
                if hits:
                    found = hits[-1]
        if not found:
            raise RuntimeError(f"调研 md 里找不到 `{needle}` 那一行的加粗数字：{RESEARCH}")
        return found

    removal = _last_bold_num("wiltodelta/remove-ai-watermarks")
    detector = _last_bold_num("lynote-ai/ai-image-detector")

    # 比值不许另抄：从 `A ÷ B = **R : 1**` 里按**分母**取，顺带证明"17.33 真的是 5667÷327"。
    ratios: dict[str, str] = {}
    for m in re.finditer(r"([\d,]+)\s*÷\s*([\d,]+)\s*=\s*\*\*([\d.]+)\s*:\s*1\*\*", text):
        ratios[m.group(2).replace(",", "")] = m.group(3)
    den = detector.replace(",", "")
    if den not in ratios:
        raise RuntimeError(f"调研 md 里没有 `… ÷ {den} = **R : 1**` 这条比值（口径更正还没落？）")
    ratio = ratios[den]

    # 算术自校：解析出来的三个串必须自洽，否则后面所有"读回"都建立在流沙上。
    num, den_i, rat = int(removal.replace(",", "")), int(den), float(ratio)
    if round(num / den_i, 2) != rat:
        raise RuntimeError(f"调研 md 自相矛盾：{num} ÷ {den_i} = {num / den_i:.4f}，"
                           f"而文中写的是 {rat}")
    return [
        dict(key="removal", text=removal, why="抹除侧最高星（攻击侧）"),
        dict(key="detector", text=detector, why="检测侧最高星（防守侧）"),
        dict(key="ratio", text=ratio, why=f"{removal} ÷ {detector}"),
    ]


def script_shot(shot: int) -> dict:
    """读台本里 `### 镜 N｜…` 那一块的 画面 / 口播 / 后期 三行原文。"""
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(rf"^### 镜 {shot}｜", ln):
            start = i
            break
    if start is None:
        raise RuntimeError(f"台本里找不到镜 {shot}：{SCRIPT}")
    block = []
    for ln in lines[start + 1:]:
        if ln.startswith("### ") or ln.startswith("## "):
            break
        block.append(ln)
    body = "\n".join(block)

    def _grab(field: str) -> str:
        m = re.search(rf"^-\s*\*\*{field}\*\*：(.*)$", body, re.M)
        if not m:
            raise RuntimeError(f"台本镜 {shot} 里找不到 `- **{field}**：` 这一行")
        return m.group(1).strip()

    head = lines[start]
    m = re.match(r"^### 镜 \d+｜(\d+):(\d+)–(\d+):(\d+)（([\d.]+)s", head)
    if not m:
        raise RuntimeError(f"台本镜 {shot} 的小标题格式变了：{head!r}")
    slot = float(m.group(5))
    return dict(slot=slot, head=head, 画面=_grab("画面"), 口播=_grab("口播"), 后期=_grab("后期"))


def srt_cues() -> list[dict]:
    """读 SRT。台词时间轴的**唯一来源** —— 不另猜一个时刻表。"""
    raw = SRT.read_text(encoding="utf-8").splitlines()
    cues, i = [], 0
    pat = re.compile(r"^(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)\s*$")
    while i < len(raw):
        m = pat.match(raw[i].strip())
        if not m:
            i += 1
            continue
        g = [int(x) for x in m.groups()]

        def _t(h, mi, s, ms):
            return h * 3600 + mi * 60 + s + ms / 1000.0

        start, end = _t(*g[:4]), _t(*g[4:])
        txt = []
        i += 1
        while i < len(raw) and raw[i].strip():
            txt.append(raw[i].strip())
            i += 1
        cues.append(dict(start=start, end=end, text=" ".join(txt)))
    if not cues:
        raise RuntimeError(f"没从 SRT 里读出任何一条台词：{SRT}")
    return cues


# ===================================================================== 量测：像素说了算
def _bg_color(arr) -> tuple[int, int, int]:
    """背景色 = 出现最多的那个颜色。**不许假设是白底**：这张图实测是黑底。"""
    flat = (arr[:, :, 0].astype(int) << 16) | (arr[:, :, 1].astype(int) << 8) | arr[:, :, 2].astype(int)
    vals, counts = __import__("numpy").unique(flat, return_counts=True)
    v = int(vals[counts.argmax()])
    return (v >> 16) & 255, (v >> 8) & 255, v & 255


def _runs(mask_1d, min_len: int) -> list[tuple[int, int]]:
    out, s = [], None
    for i, v in enumerate(mask_1d):
        if v and s is None:
            s = i
        elif not v and s is not None:
            if i - s >= min_len:
                out.append((s, i - 1))
            s = None
    if s is not None and len(mask_1d) - s >= min_len:
        out.append((s, len(mask_1d) - 1))
    return out


def measure_figure(png: pathlib.Path) -> dict:
    """把 `fig_star.png` 的**几何**量出来：内容框、7 条带、5 根条形的左右边与颜色、比值框。

    为什么要量：任何一处手抄的坐标都会在素材更新后静静错位，而错位只表现为"动画看起来怪"，
    不会报错。量出来的坐标会跟着素材走 —— 素材换了、这里跟着换，或者当场报错。
    """
    import numpy as np
    from PIL import Image

    img = Image.open(png).convert("RGB")
    arr = np.asarray(img).astype(int)
    bg = _bg_color(arr)
    if sum(bg) > 90:
        raise RuntimeError(f"{png.name} 的背景色 {bg} 不是深色 —— 本工具的黑场擦除假设不成立，"
                           f"先来读一遍这张图再改代码")
    dist = np.abs(arr - np.array(bg)).sum(axis=2)
    fg = dist > 24

    rows = fg.sum(axis=1) > 20
    hard = _runs(rows, 8)
    if len(hard) != 7:
        raise RuntimeError(f"{png.name} 量出 {len(hard)} 条横带，期望 7 条"
                           f"（标题/副标题/5 根条形）—— 素材变了，先重新量一遍")

    # 条带的**纵向范围**取"视觉上连续非黑"的行段（软阈值），不取"前景像素够多"的行段。
    # 两段必须一一对应、且硬段被软段包住 —— 对不上就说明这张图的构成变了，先来读一遍。
    soft_rows = (dist > SOFT_THR).sum(axis=1) > 0
    sof = _runs(soft_rows, 1)
    if len(sof) != len(hard):
        raise RuntimeError(f"{png.name} 软轮廓量出 {len(sof)} 段、硬阈值量出 {len(hard)} 段 —— "
                           f"不一致，多半是图里多了/少了独立元素")
    bands = []
    for (ha, hb), (sa, sb) in zip(hard, sof):
        if not (sa <= ha and hb <= sb):
            raise RuntimeError(f"硬条带 y{ha}..{hb} 不在软段 y{sa}..{sb} 里 —— 量错了")
        bands.append(dict(y0=sa, y1=sb))
    #: 任何"不是背景"的源图行 —— 帧坐标下它们**必须**全被某个遮罩盖住，否则第 0 帧不是黑场。
    #: 判据在 `first_frame_is_covered()`（有了 `lay` 才能换算帧坐标）。
    ink_rows = [int(y) for y in range(len(soft_rows)) if soft_rows[y]]
    ys, xs = np.where(fg)
    bbox = dict(x0=int(xs.min()), y0=int(ys.min()), x1=int(xs.max()), y1=int(ys.max()))

    bars = []
    for b in bands[2:]:
        h = b["y1"] - b["y0"] + 1
        col = fg[b["y0"]:b["y1"] + 1, :].sum(axis=0)
        solid = _runs(col > 0.5 * h, 20)          # 实心条：整条带高度都被填满
        if len(solid) != 1:
            raise RuntimeError(f"条带 y{b['y0']}..{b['y1']} 里量出 {len(solid)} 段实心条，期望 1 段")
        x0, x1 = solid[0]
        patch = arr[b["y0"]:b["y1"] + 1, x0:x1 + 1].reshape(-1, 3)
        keep = np.abs(patch - np.array(bg)).sum(axis=1) > 24
        patch = patch[keep]
        flat = (patch[:, 0] << 16) | (patch[:, 1] << 8) | patch[:, 2]
        vals, counts = np.unique(flat, return_counts=True)
        v = int(vals[counts.argmax()])
        color = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        bars.append(dict(y0=b["y0"], y1=b["y1"], x0=int(x0), x1=int(x1),
                         w=int(x1 - x0 + 1), color=color, warm=(color[0] - color[2] > 40)))

    # 几何自校：左端对齐 + 宽度严格递减 + 第一根最宽且是暖色（"红色条形"）。
    lefts = [b["x0"] for b in bars]
    if max(lefts) - min(lefts) > 4:
        raise RuntimeError(f"量出的条形左端不对齐：{lefts} —— 台本说的『从左侧长出来』是同一根线")
    for a, b in zip(bars, bars[1:]):
        if b["w"] >= a["w"]:
            raise RuntimeError(f"条形宽度没有严格递减：{[x['w'] for x in bars]}")
    if not bars[0]["warm"]:
        raise RuntimeError(f"第一根条形的颜色 {bars[0]['color']} 不是暖色（台本：『红色条形』）")

    # 比值框：取"最高星检测器"那一行（第三根条）右侧、**最后一「词」**。
    # ⚠️ 不能直接取"最后一段连续前景"：文字是**逐字**画的，字与字之间有 5~20px 空隙，
    #    按"连续段"切会把 `1 : 17.33` 切成 7 个字形块，最后一块只剩一个 `3`（宽 16px）。
    #    实测踩过：那样量出来的框在帧坐标 x1957 —— **已经在画面外**，描边会闪在屏幕外面，
    #    而四条几何判据全是绿的（因为判据只看"闪没闪"）。所以这里按**词间隙**（≥60px）分组。
    #    另外，条形本身与星数之间的间隙只有 ~30px（比词间隙小），所以必须**先把条形整段切掉**
    #    再从剩下的列里分词组 —— 否则条形会和星数粘成一个词，比值就变成"右侧唯一一个词"。
    rb = bars[2]
    col = fg[rb["y0"]:rb["y1"] + 1, :].sum(axis=0)
    off = rb["x1"] + 1
    words: list[list[int]] = []
    for a, b in _runs(col[off:] > 0, 2):
        a, b = a + off, b + off
        if words and a - words[-1][1] < 60:
            words[-1][1] = b
        else:
            words.append([a, b])
    right = words
    if len(right) < 2:
        raise RuntimeError(f"检测器那一行右侧量出 {len(right)} 个词块，期望 ≥2（星数 + 比值）")
    rx0, rx1 = right[-1]
    if rx1 > bbox["x1"]:
        raise RuntimeError(f"量出的比值框右缘 {rx1} 超出内容框右缘 {bbox['x1']} —— 量错了")
    sub = fg[rb["y0"]:rb["y1"] + 1, rx0:rx1 + 1]
    rys = np.where(sub.any(axis=1))[0]
    ratio = dict(x0=int(rx0), x1=int(rx1),
                 y0=int(rb["y0"] + rys.min()), y1=int(rb["y0"] + rys.max()))
    ratio["w"] = ratio["x1"] - ratio["x0"] + 1
    ratio["h"] = ratio["y1"] - ratio["y0"] + 1
    if not (ratio["w"] < 0.25 * (bbox["x1"] - bbox["x0"])):
        raise RuntimeError(f"量出的比值框宽 {ratio['w']}px 占了整行的一大块，"
                           f"多半是把星数或整行文字当成了比值 —— 先来读一遍这张图")
    return dict(src=str(png), size=[img.width, img.height], bg=bg, bbox=bbox, ink_rows=ink_rows,
                bands=[dict(name=n, y0=b["y0"], y1=b["y1"]) for n, b in
                       zip(["title", "subtitle", "bar1", "bar2", "bar3", "bar4", "bar5"], bands)],
                bars=bars, ratio=ratio)


def layout(fig: dict) -> dict:
    """把量出来的源图坐标换算成**帧坐标**：裁内容框 → 满宽 1920 → 居中。

    「满宽」是台本那句"压满画面"的降级实现：不推近、不裁掉右侧的 327 / 17.33
    （尾帧读回要用），只把内容框的宽度顶满 1920。缩放系数 s 与 ffmpeg 的
    `scale=1920:{ph}` 必须**逐位一致** —— 否则遮罩框会错开几像素，擦除时会留边。
    """
    bb = fig["bbox"]
    bw, bh = bb["x1"] - bb["x0"] + 1, bb["y1"] - bb["y0"] + 1
    ph = int(round(bh * W / bw / 2)) * 2          # 与 ffmpeg `-2` 同样的"就近取偶"
    if ph > H:
        raise RuntimeError(f"内容框按满宽缩放后高 {ph} > {H} —— 会裁到画面，需要换实现")
    py = ((H - ph) // 2 // 2) * 2                 # y 取偶：yuv420p 的色度不许错位
    s = ph / bh

    # ⚠️ 横向与纵向是**两个**变换，别混用：纵向还要加上居中留边 `py`，而它的原点是内容框的
    #    `y0` 不是 `x0`。混用的后果实测过：描边框算到 x1842、宽 78 —— **右缘出画**，
    #    而"闪没闪""闪几次"这些判据**全绿**（它们只看像素差不看坐标）。
    def fx(x: int) -> int:
        return int(round((x - bb["x0"]) * s))

    def fy(y: int) -> int:
        return py + int(round((y - bb["y0"]) * s))

    out = dict(crop=dict(x=bb["x0"], y=bb["y0"], w=bw, h=bh), ph=ph, py=py, s=s, bands=[])
    for b in fig["bands"]:
        # 遮罩 = 条带 + 上下各 `MASK_PAD` 帧行：重采样会把条带边缘晕染到遮罩之外的那一行上。
        y0, y1 = fy(b["y0"]) - MASK_PAD, fy(b["y1"]) + MASK_PAD
        y0 = (y0 // 2) * 2                        # 向下取偶（**向外**扩）
        y1 = ((y1 + 2) // 2) * 2 - 1              # 向上取偶（**向外**扩）
        out["bands"].append(dict(name=b["name"], fy=(y0 // 2) * 2,
                                 fh=max(2, ((y1 - y0 + 1 + 1) // 2) * 2)))
    # 外扩之后必须**互不重叠**：重叠的遮罩会把邻居在它的时刻之前擦出来（判据 ⑥ 会红，
    # 但那时已经出了 30 秒的片）。间距缩小或 MASK_PAD 变大时，这里先红。
    geo = sorted((b["fy"], b["fy"] + b["fh"] - 1, b["name"]) for b in out["bands"])
    for (y0a, y1a, na), (y0b, y1b, nb) in zip(geo, geo[1:]):
        if y0b <= y1a:
            raise RuntimeError(f"遮罩重叠：{na} y{y0a}..{y1a} 与 {nb} y{y0b}..{y1b} —— "
                               f"MASK_PAD={MASK_PAD} 相对条带间距太大了")
    out["ratio"] = dict(x0=fx(fig["ratio"]["x0"]), y0=fy(fig["ratio"]["y0"]),
                        x1=fx(fig["ratio"]["x1"]), y1=fy(fig["ratio"]["y1"]))
    out["ratio_w"] = out["ratio"]["x1"] - out["ratio"]["x0"] + 1
    out["ratio_h"] = out["ratio"]["y1"] - out["ratio"]["y0"] + 1
    return out


def first_frame_is_covered(fig: dict, lay: dict) -> list[int]:
    """『第 0 帧是黑场』的**几何版**：源图里任何"不是背景"的行，在帧坐标下都必须落在
    某个遮罩的 `[fy, fy+fh-1]` 之内。返回**没被盖住**的帧行（空 = 通过）。

    为什么要有它（而不是只看像素）：像素判据要**出片之后**才知道，而这条在**排片时**就拦得住；
    更关键的是它会红在"改 MASK_PAD / 改条带取法"的那一刻，不用等 30 秒出片 + 抽帧。
    实测它抓的就是 round17b 那条缺陷：源图 164 行（→ 帧 312）与 563/673/783 行曾是漏的。
    """
    src_y0, s, py = lay["crop"]["y"], lay["s"], lay["py"]
    covered = [(b["fy"], b["fy"] + b["fh"] - 1) for b in lay["bands"]]
    miss = {py + int(round((y - src_y0) * s)) for y in fig.get("ink_rows", [])}
    return sorted(f for f in miss if not any(a <= f <= b for a, b in covered))


def frame_is_black(maxval: int) -> bool:
    """『黑场起』的像素判据：第 0 帧最亮像素 ≤ `BLACK_MAX`。"""
    return maxval <= BLACK_MAX


def tail_is_clean(d_mid: int, d_last: int) -> bool:
    """『尾帧没有描边残留』的判据 —— **不是"逐位相等"**。

    为什么不能写 `d_last == 0`：成片是 libx264 crf 18 **有损**编码。闪一下（255 级、跨约
    3900 像素）之后编码器的量化步长会漂移，随后"静态"帧与闪之前那一帧实测差 **≤14**
    （纯编码残留，全帧范围、不含任何 255 级像素；相邻两帧之间差 0）。逐位相等的判据在
    有损编码上**永远绿不了** —— 那不是片子坏了，是判据写错了。
    真残留的描边是白色实框，实测差 **200+**，与残留相差一个量级 ⇒ 用比例阈值分辨得开。

    实测（round17b，`seg_shot01_hook.mp4`）：d_mid=255，d_last=6（描边框内）/14（全帧）。
    """
    return d_last <= max(BLACK_MAX * 2, int(TAIL_RESIDUE_RATIO * d_mid))


# ===================================================================== 排片：时刻由锚定
def narration_anchor(shot: int, slot: float) -> dict:
    """定出两个**口播锚点**：5,667 那句、和"十七比一"那句（**真实播放时刻**）。

    ⚠️ 2026-10-05 二次修（`docs/DEBT.md` D-51）：本函数给的就是 **SRT 里的真实句界** 时刻。
    `plan()` 早先要按 `实测配音长 / 槽位` 等比压缩（因为旧 SRT 是按槽位比例分的），**现在不压缩**
    —— SRT 已锚在 edge-tts 真实句界上。实测镜 1：「十七比一」= **13.504s**（与口播一致）；
    若再压缩会落到 12.842s（早 0.66s），而上一版（压缩到 14.310s）是**晚 0.81s**。
    """
    sc = script_shot(shot)
    narr = sc["口播"]
    pr = {p["key"]: p["text"] for p in promises()}
    for needle, label in ((pr["removal"], "抹除侧星数"), (pr["detector"], "检测侧星数")):
        if needle not in narr:
            raise RuntimeError(f"台本镜 {shot} 的口播里没有 {label} `{needle}`：{narr!r}")
    if "十七比一" not in narr:
        raise RuntimeError(f"台本镜 {shot} 的口播里没有『十七比一』（描边闪的锚点没了）：{narr!r}")
    if "擦除" not in sc["画面"]:
        raise RuntimeError(f"台本镜 {shot} 的画面行不再写『擦除』，本工具的动效实现要对账：{sc['画面']!r}")
    if "描边" not in sc["后期"]:
        raise RuntimeError(f"台本镜 {shot} 的后期行不再写『描边』，本工具的闪法要对账：{sc['后期']!r}")

    cues = srt_cues()
    hit = [c for c in cues if "十七比一" in c["text"]]
    if len(hit) != 1:
        raise RuntimeError(f"SRT 里含『十七比一』的台词有 {len(hit)} 条，期望恰好 1 条")
    flash_cue = hit[0]
    hit = [c for c in cues if pr["removal"] in c["text"]]
    if len(hit) != 1:
        raise RuntimeError(f"SRT 里含 `{pr['removal']}` 的台词有 {len(hit)} 条，期望恰好 1 条")
    bar1_cue = hit[0]
    if not (bar1_cue["start"] < flash_cue["start"]):
        raise RuntimeError("SRT 的台词次序变了：5,667 那句应当排在『十七比一』之前")

    return dict(script=sc, narr=narr, bar1_srt=bar1_cue["start"], flash_srt=flash_cue["start"])


def plan(shot: dict, fig: dict, lay: dict, anchor: dict, mp3_dur: float) -> dict:
    """把锚点 + 设计常量排成**每一条带什么时候擦**，并把全部次序断言写在这里。

    ⚠️ 2026-10-05 二次修（`docs/DEBT.md` D-51）：**锚点不再乘 k**。旧 SRT 是按槽位比例分
    的，所以要乘 `mp3长/槽位` 压回真实播放位置；新 SRT 锚在 edge-tts **真实句界**上（见
    `voice/make_srt.py`）⇒ 锚点本身就是真实播放时刻。实测镜 1：若仍乘 k(0.9510)，
    「十七比一」的描边闪会落在 14.310s，而口播在 **13.504s** —— 闪晚 0.81s（守卫
    `tests/test_film_shots.py::test_产物_尾帧无描边残留` 因此变红：d_mid = 0）。
    """
    k = mp3_dur / shot["slot"]                    # 只留作次序校验与信息栏
    t_bar1 = anchor["bar1_srt"]
    t_flash = anchor["flash_srt"]
    if k >= 1:
        raise RuntimeError(f"配音 {mp3_dur:.3f}s 装不下槽位 {shot['slot']:.3f}s（k={k:.4f}）")
    if not (0 < t_bar1 < t_flash < mp3_dur < shot["slot"]):
        raise RuntimeError(f"锚点次序不成立：bar1={t_bar1:.3f} flash={t_flash:.3f} "
                           f"配音={mp3_dur:.3f} 槽位={shot['slot']:.3f}")

    t_sub = TITLE_AT + WIPE + SUB_GAP
    t_last = t_flash - FLASH_HOLD - WIPE          # 最后一根条必须在这之前收完
    span = t_last - t_bar1
    if span <= 0:
        raise RuntimeError(f"5,667 那句（{t_bar1:.3f}s）到最后一根收完（{t_last:.3f}s）之间没有余量，"
                           f"五根条排不下 —— 要么挪 WIPE/FLASH_HOLD，要么这一镜就不该做逐条揭示")
    step = span / 4                               # 5 根条：第 1 根锚在 5,667，后 4 根均匀铺开
    if step <= 0:
        raise RuntimeError(f"逐条揭示的间隔 {step:.3f}s ≤ 0")
    if not (TITLE_AT + WIPE < t_sub < t_bar1):
        raise RuntimeError(f"标题 / 副标题 / 第一根条的次序不成立："
                           f"{TITLE_AT + WIPE:.3f} / {t_sub:.3f} / {t_bar1:.3f}")

    starts = {"title": TITLE_AT, "subtitle": t_sub}
    for i in range(5):
        starts[f"bar{i + 1}"] = t_bar1 + i * step

    bands = []
    for b in lay["bands"]:
        t0 = starts[b["name"]]
        bands.append(dict(name=b["name"], fy=b["fy"], fh=b["fh"],
                          t_start=round(t0, 6), t_end=round(t0 + WIPE, 6)))
    fb = lay["ratio"]
    flash = dict(x=max(0, fb["x0"] - FLASH_PAD), y=max(0, fb["y0"] - FLASH_PAD),
                 w=min(W, lay["ratio_w"] + 2 * FLASH_PAD), h=lay["ratio_h"] + 2 * FLASH_PAD,
                 t0=round(t_flash, 6), t1=round(t_flash + FLASH_LEN, 6))
    if not (flash["x"] >= 0 and flash["y"] >= 0
            and flash["x"] + flash["w"] <= W and flash["y"] + flash["h"] <= H):
        raise RuntimeError(
            f"描边框 x{flash['x']} y{flash['y']} {flash['w']}×{flash['h']} 超出 {W}×{H} —— "
            f"闪在画面外了。最常见的原因是**横坐标走错了变换**（用了带 py 留边的纵向变换）。")
    if flash["y"] + flash["h"] > lay["py"] + lay["ph"]:
        raise RuntimeError(
            f"描边框底 y{flash['y'] + flash['h']} 越过了图像底 y{lay['py'] + lay['ph']} —— "
            f"量到的比值框不在它该在的那条带里")

    # 『黑场起』的几何版：任何有内容的源图行都必须被某个遮罩盖住（**排片时就红**）。
    leak = first_frame_is_covered(fig, lay)
    if leak:
        raise RuntimeError(
            f"『黑场起』不成立：源图有 {len(leak)} 个帧行落在所有遮罩之外（前几个 {leak[:8]}）"
            f"—— 第 0 帧不会是纯黑。查条带纵向范围（SOFT_THR）与 MASK_PAD={MASK_PAD}")
    return dict(k=k, t_bar1=round(t_bar1, 6), t_flash=round(t_flash, 6), step=round(step, 6),
                bands=bands, flash=flash)


# ===================================================================== 出片
def mask_color_source(band: dict, dur: float) -> str:
    """一条带的黑色遮罩输入。高度 = 条带高（取偶），**每条带一个独立输入** —— 因为每条带
    的擦除时刻不同，共用一条就会一起擦（那就成了"整幅左到右擦一次"，台本要的是逐条）。"""
    return f"color=c=black:s={W}x{band['fh']}:r={FPS}:d={dur:.3f},format=yuv420p"


def wipe_filter(idx: int, band: dict) -> str:
    """一条带的擦除：遮罩的 `x` 是时间的表达式 ⇒ 遮罩从左滑出，底下的画面就露出来。

    ⚠️ `x` 取偶（`2*trunc(...)`）：yuv420p 下奇数横向偏移会让色度面错半像素。
    ⚠️ 只能用 overlay：`drawbox` 的时间表达式在本机 ffmpeg 8.1.2 上**静默不画**（见文件头）。
    """
    half = W / 2
    # `shortest=1` 是**必须的**：overlay 默认 shortest=0，成片长度会被**最长的那路输入**决定。
    # 实测踩过：遮罩给到 dur+1.0s ⇒ 出片 510 帧 / 17.000s（槽位是 480 帧 / 16.000s），
    # 而画面看着完全正常 —— 多出来的 1 秒是最后一条带擦完后的静止画面。
    return (f"[v{idx - 1}][{idx}:v]overlay="
            f"x='2*trunc({half:g}*clip((t-{band['t_start']:.6f})/{WIPE},0,1))':y={band['fy']}"
            f":eof_action=repeat:shortest=1[v{idx}]")


def stage_chain(lay: dict, dur: float) -> str:
    """把静态图铺成舞台：裁内容框 → 满宽 1920 → 补黑到 1080 居中 → 定帧率、卡死帧数。"""
    c = lay["crop"]
    return (f"[0:v]crop={c['w']}:{c['h']}:{c['x']}:{c['y']},"
            f"scale={W}:{lay['ph']}:flags=lanczos,"
            f"pad={W}:{H}:0:{lay['py']}:color=black,"
            f"fps={FPS},trim=end_frame={int(round(dur * FPS))},setpts=PTS-STARTPTS,"
            f"setsar=1,format=yuv420p[v0]")     # `[v0]` 是 overlay 链的起点标签，别改名


def flash_filter(fl: dict) -> str:
    """比值描边闪一次。`enable=` 是**滤镜自带的时间轴开关**（这条在本机是好的）。"""
    return (f"drawbox=x={fl['x']}:y={fl['y']}:w={fl['w']}:h={fl['h']}"
            f":color={FLASH_COLOR}@{1}:t={FLASH_THICK}"
            f":enable='between(t,{fl['t0']:.6f},{fl['t1']:.6f})'")


def build(seg: dict, fig: dict, lay: dict, pl: dict) -> pathlib.Path:
    """出片：**一帧不多一帧不少**由 `trim=end_frame` 卡死，不由时长算。"""
    out = FILM / seg["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    dur = seg["slot"]
    parts = [stage_chain(lay, dur)]
    for i, band in enumerate(pl["bands"], start=1):
        parts.append(wipe_filter(i, band))
    last = f"v{len(pl['bands'])}"
    parts.append(f"[{last}]{flash_filter(pl['flash'])},format=yuv420p[outv]")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-loop", "1", "-framerate", str(FPS), "-i", str(fig["src"])]
    for band in pl["bands"]:
        cmd += ["-f", "lavfi", "-i", mask_color_source(band, dur + 1.0)]
    cmd += ["-filter_complex", ";".join(parts), "-map", "[outv]",
            "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"出片失败：{(r.stderr or '').strip()[:400]}")
    return out


# ===================================================================== 自证
def probe(clip: pathlib.Path) -> dict:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,nb_frames,avg_frame_rate",
         "-show_entries", "format=duration", "-of", "default=nw=1", str(clip)],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",   # D-07：ffmpeg 的日志是 UTF-8，按 locale 解会崩
    )
    d = {}
    for line in (p.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v
    return d


def media_duration(path: pathlib.Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(path)],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    try:
        return float((p.stdout or "").strip())
    except ValueError:
        raise RuntimeError(f"读不出时长：{path}（{(p.stderr or '').strip()[:200]}）")


def grab(video: pathlib.Path, frame: int, out: pathlib.Path, crop: dict | None = None) -> pathlib.Path:
    """按**帧号**抽一帧（`select=eq(n\\,K)`，不用 `-ss`：`-ss` 会在边界上静默差一帧）。

    `crop` 只裁出要看的那条带 ⇒ 文件极小、判据只看该看的像素。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"select=eq(n\\,{frame})"
    if crop:
        vf += f",crop={crop['w']}:{crop['h']}:{crop['x']}:{crop['y']}"
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(video), "-vf", vf, "-frames:v", "1",
                        "-fps_mode", "passthrough", str(out)],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"抽帧失败（第 {frame} 帧）：{(r.stderr or '').strip()[:200]}")
    return out


def px_max(png: pathlib.Path) -> int:
    from PIL import Image
    import numpy as np
    return int(np.asarray(Image.open(png).convert("RGB")).max())


def px_diff(a: pathlib.Path, b: pathlib.Path) -> int:
    from PIL import Image
    import numpy as np
    x = np.asarray(Image.open(a).convert("RGB")).astype(int)
    y = np.asarray(Image.open(b).convert("RGB")).astype(int)
    return int(np.abs(x - y).max())


def readback(png: pathlib.Path, needles: list[str]) -> tuple[bool, str, list[str]]:
    """尾帧逐字读回。返回 (是否全中, 模型原话, 漏掉的串)。**调用失败也算漏**（回空串）。"""
    try:
        res = ask_vl(png, QUESTION)
    except Exception as exc:                       # 没 key / 断网 —— 证明不了就是没证明
        return False, f"<VL 调用失败：{type(exc).__name__}: {exc}>", list(needles)
    ans = res.get("answer") or ""
    if not res.get("ok"):
        return False, f"<VL 未返回内容：{res.get('error')}>", list(needles)
    norm = re.sub(r"[\s,，]", "", ans)
    miss = [n for n in needles if n.replace(",", "") not in norm]
    return (not miss), ans, miss


def verify(seg: dict, fig: dict, lay: dict, pl: dict, out: pathlib.Path,
           mp3: pathlib.Path, mp3_dur: float, asks: list[str]) -> tuple[bool, list[str], list[str]]:
    """两段式验收。`checks` **只收真正跑过的判据** —— 没跑的不许出现在成功行里。"""
    bad: list[str] = []
    checks: list[str] = []
    info = probe(out)

    # ① 几何 / 时长 / 帧数
    w, h = int(info.get("width", 0)), int(info.get("height", 0))
    fps = info.get("avg_frame_rate", "")
    dur = float(info.get("duration") or 0)
    frames = int(info.get("nb_frames") or 0)
    if (w, h) == (W, H):
        checks.append(f"几何 {w}×{h}")
    else:
        bad.append(f"几何 {w}×{h} ≠ {W}×{H}")
    if fps == f"{FPS}/1":
        checks.append(f"{FPS}fps")
    else:
        bad.append(f"帧率 {fps!r} ≠ {FPS}/1")
    if abs(dur - seg["slot"]) < 1e-3:
        checks.append(f"时长 {dur:.3f}s")
    else:
        bad.append(f"时长 {dur:.3f}s ≠ 槽位 {seg['slot']:.3f}s")
    want_frames = int(round(seg["slot"] * FPS))
    if frames == want_frames:
        checks.append(f"帧数 {frames}")
    else:
        bad.append(f"帧数 {frames} ≠ {want_frames}（等于时长×帧率，但那不是 ffmpeg 真给的数）")

    # ② 装得下配音
    if dur + 1e-9 >= mp3_dur:
        checks.append(f"装得下配音 {mp3_dur:.3f}s（余量 {dur - mp3_dur:+.3f}s）")
    else:
        bad.append(f"装不下配音：段长 {dur:.3f}s < 实测配音 {mp3_dur:.3f}s"
                   f"（不许拉长槽位、不许剪字 —— 改台本并留档）")

    # ⑤ 首帧全黑（"黑场起"）
    f0 = grab(out, 0, WORK / "f_000.png")
    m0 = px_max(f0)
    if frame_is_black(m0):
        checks.append(f"首帧全黑（最亮 {m0} ≤ {BLACK_MAX}）")
    else:
        bad.append(f"首帧不是黑场（最亮 {m0} > {BLACK_MAX}）—— 台本写的是『黑场起』")

    # ⑥ 逐条揭示：每条带"擦之前是黑的、擦完之后有字"，且次序严格递增
    ends = [b["t_end"] for b in pl["bands"]]
    if ends == sorted(ends) and len(set(ends)) == len(ends):
        checks.append(f"逐条揭示 {len(pl['bands'])} 条带次序严格递增")
    else:
        bad.append(f"揭示次序不是严格递增：{ends}")
    for b in pl["bands"]:
        n_before = max(0, int(round(b["t_start"] * FPS)) - 1)
        n_after = min(want_frames - 1, int((b["t_end"] * FPS) + 0.5) + 1)
        rect = dict(w=W, h=b["fh"], x=0, y=b["fy"])
        pre = grab(out, n_before, WORK / f"b_{b['name']}_pre.png", rect)
        post = grab(out, n_after, WORK / f"b_{b['name']}_post.png", rect)
        if px_max(pre) > 12:
            bad.append(f"条带 {b['name']} 在 {b['t_start']:.3f}s 之前就亮了（最亮 {px_max(pre)}）"
                       f"—— 擦除没生效或者遮罩高度错了")
        if px_max(post) <= 60:
            bad.append(f"条带 {b['name']} 在 {b['t_end']:.3f}s 之后还是黑的（最亮 {px_max(post)}）"
                       f"—— 遮罩没让开，或者这条带本来就没内容")
    if not any("条带" in b for b in bad):
        checks.append("每条带先黑后亮")

    # ⑦ 闪一次、且尾帧干净
    fl = pl["flash"]
    rect = dict(w=fl["w"], h=fl["h"], x=fl["x"], y=fl["y"])
    n_pre = int((fl["t0"] - 0.2) * FPS)
    n_mid = int((fl["t0"] + fl["t1"]) / 2 * FPS)
    n_last = want_frames - 1
    a_pre = grab(out, n_pre, WORK / "flash_pre.png", rect)
    a_mid = grab(out, n_mid, WORK / "flash_mid.png", rect)
    a_end = grab(out, n_last, WORK / "flash_last.png", rect)
    d_mid, d_last = px_diff(a_pre, a_mid), px_diff(a_pre, a_end)
    if d_mid >= 200:
        checks.append(f"比值描边闪一次（峰值差 {d_mid}）")
    else:
        bad.append(f"描边没闪起来：flash 时段与之前的差只有 {d_mid}（{fl['t0']:.3f}s 起 {FLASH_LEN}s）")
    # ⚠️ 判据**不是** `d_last == 0`：有损编码下静态帧之间也有 ≤14 的残留（见 `tail_is_clean`）。
    #    真残留的描边是白色实框 ⇒ 差 200+。丢人脸的不是残留，是把残留当残影。
    if tail_is_clean(d_mid, d_last):
        checks.append(f"尾帧无描边残留（残留 {d_last} ≤ {max(BLACK_MAX * 2, int(TAIL_RESIDUE_RATIO * d_mid))}"
                      f"，闪幅 {d_mid}）")
    else:
        bad.append(f"尾帧还留着描边（差 {d_last}，闪幅 {d_mid}）—— "
                   f"`enable` 只该开一次、且要在片尾之前关掉")

    # ③ 尾帧 VL 逐字读回
    tail = grab(out, n_last, WORK / "tail.png")
    ok, answer, miss = readback(tail, asks)
    if ok:
        checks.append(f"尾帧 VL 逐字读回 {len(asks)}/{len(asks)} 个承诺串")
    else:
        bad.append(f"尾帧读不回 {len(miss)}/{len(asks)} 个承诺串 {miss}（模型原话：{answer[:180]!r}）")
    return (not bad), bad, checks


def shots() -> list[dict]:
    """段表。**字段名是隐藏接口**：`tag / out / shot / slot / narration / why / pieces`，
    与 `tools/make_film_segment.py` 的 `SEGMENTS` 同一套 —— 下游拼片脚本按名字取。"""
    pr = promises()
    return [
        dict(
            tag="S01", out="seg_shot01_hook.mp4", shot=1, slot=16.0,
            narration="shot1_zh-CN-YunxiNeural.mp3",
            why=("镜 1 槽位 16s（钩子）：黑场起 → fig_star 图表逐条从左『擦』出来 → "
                 "口播到『十七比一』时 1 : 17.33 比值描边闪一次。"
                 "⚠️ 台本『首个长条 5,667 的红色条形压满画面』**降级实现**：不许另画图，"
                 "而尾帧必须同时读回 5,667 / 327 / 17.33 三个承诺串 —— 任何推近裁切都会把 "
                 "327 与 17.33 挤出画面（自相矛盾）。故按『整幅满宽』处理：裁内容框 → "
                 "缩放到满宽 1920 居中，5,667 那条红条（图里最长）即画面主体。"
                 "描边用白色不用风险红：全片风险红预算 ≤3 已给镜 2 / 镜 9。"),
            pieces=[
                dict(kind=KIND_FIGBARS, src="fig_star.png",
                     why="唯一素材：PPT 里那张星数对比图本体。一处 drawtext 都没有 —— "
                         "画面上每个字都来自这张图，数字来源可查。",
                     expect=[p["text"] for p in pr],
                     promise_keys=[p["key"] for p in pr],
                     flash_mark="十七比一"),
            ],
        ),
    ]


def run_shot(seg: dict, verify_only: bool, do_probe: bool) -> int:
    fig_path = PPT_IMG / seg["pieces"][0]["src"]
    print(f"\n===== {seg['tag']}｜镜 {seg['shot']}｜槽位 {seg['slot']}s｜{seg['why']} =====")
    print(f"    素材 {fig_path}")
    fig = measure_figure(fig_path)
    lay = layout(fig)
    print(f"    量测：源图 {fig['size'][0]}×{fig['size'][1]}，背景 {fig['bg']}，"
          f"内容框 x{fig['bbox']['x0']}..{fig['bbox']['x1']} y{fig['bbox']['y0']}..{fig['bbox']['y1']}")
    print(f"          {len(fig['bands'])} 条带；5 根条形宽度 {[b['w'] for b in fig['bars']]}，"
          f"首根色 {fig['bars'][0]['color']}（暖）")
    print(f"          比值框（源图）x{fig['ratio']['x0']}..{fig['ratio']['x1']} "
          f"y{fig['ratio']['y0']}..{fig['ratio']['y1']}")
    print(f"    铺陈：满宽 {lay['ph']}px 居中于 y={lay['py']}（缩放系数 {lay['s']:.5f}）")

    mp3 = VOICE / seg["narration"]
    if not mp3.exists():
        print(f"    ❌ 找不到配音 {mp3}")
        return 1
    mp3_dur = media_duration(mp3)
    anchor = narration_anchor(seg["shot"], seg["slot"])
    pl = plan(seg, fig, lay, anchor, mp3_dur)
    print(f"    口播锚：`{promises()[0]['text']}` 那句 {anchor['bar1_srt']:.3f}s（SRT）"
          f"→ {pl['t_bar1']:.3f}s（实播，压缩系数 {pl['k']:.4f}）")
    print(f"            『十七比一』 {anchor['flash_srt']:.3f}s（SRT）→ {pl['t_flash']:.3f}s（实播）"
          f"；配音实测 {mp3_dur:.3f}s / 槽位 {seg['slot']:.3f}s")
    for b in pl["bands"]:
        print(f"            {b['name']:8s} 擦除 {b['t_start']:.3f}s → {b['t_end']:.3f}s "
              f"（帧 y{b['fy']} h{b['fh']}）")
    print(f"            描边闪 {pl['flash']['t0']:.3f}s → {pl['flash']['t1']:.3f}s，"
          f"框 x{pl['flash']['x']} y{pl['flash']['y']} {pl['flash']['w']}×{pl['flash']['h']}")
    print(f"            逐条间隔 {pl['step']:.3f}s")

    if do_probe:
        WORK.mkdir(parents=True, exist_ok=True)
        (WORK / f"{seg['tag']}_plan.json").write_text(
            json.dumps(dict(figure=fig, layout=lay, plan=pl,
                            promises=promises(), narration=anchor["narr"]),
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    （--probe：量测与排片写入 {WORK.relative_to(ROOT)}/{seg['tag']}_plan.json，未出片）")
        return 0

    out = FILM / seg["out"]
    if not verify_only:
        out = build(seg, fig, lay, pl)
        print(f"    出片 {out.relative_to(ROOT)}（{out.stat().st_size:,} B）")
    if not out.exists():
        print(f"    ❌ 缺文件 {out}")
        return 1

    asks = seg["pieces"][0]["expect"]
    ok, bad, checks = verify(seg, fig, lay, pl, out, mp3, mp3_dur, asks)
    if not ok:
        REJECTED.mkdir(parents=True, exist_ok=True)
        dst = REJECTED / seg["out"]
        shutil.move(str(out), str(dst))
        (REJECTED / f"{seg['tag']}_WHY.txt").write_text(
            f"{seg['out']} 被拒收：\n" + "\n".join(f"  - {b}" for b in bad) + "\n",
            encoding="utf-8")
        print(f"    ❌ 拒收 → {dst.relative_to(ROOT)}")
        for b in bad:
            print(f"       - {b}")
        return 1
    print(ok_line(checks))
    return 0


def main() -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只做某一镜（S01）")
    ap.add_argument("--verify-only", action="store_true", help="不重建，只复核已有成段")
    ap.add_argument("--probe", action="store_true", help="只量测 + 排片并落 JSON，不出片")
    ap.add_argument("--self-check-utf8", action="store_true",
                    help="自证伪：只走成功行那条路径（打印 ✅ 后退出），给编码守卫当探针")
    a = ap.parse_args()

    if a.self_check_utf8:
        # 成功行是本工具唯一带非 GBK 字符的出口，也正是"判据全过、片子已落盘、
        # 却报非 0 退出"那个坑的现场。单独走一遍，好让守卫秒级证伪。
        print(ok_line(["几何", "时长", "帧数"]))
        return 0

    segs = [s for s in shots() if not a.only or s["tag"] == a.only]
    if not segs:
        print(f"[X] 没有匹配的段：--only {a.only}")
        return 2

    TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
    fh = open(TRANSCRIPT, "w", encoding="utf-8", errors="replace")
    sys.stdout = _Tee(sys.stdout, fh)
    try:
        print(f"镜头工厂 · 笔录（{TRANSCRIPT.name}）｜规格 {W}×{H} / {FPS}fps")
        rc = 0
        for seg in segs:
            rc |= run_shot(seg, a.verify_only, a.probe)
        print(f"\n成片段判定：{'PASS' if rc == 0 else 'FAIL'}")
        return rc
    finally:
        sys.stdout = sys.__stdout__
        fh.close()


if __name__ == "__main__":
    sys.exit(main())
