# -*- coding: utf-8 -*-
"""成片内容守卫：把"只亲跑过一次"的两条结论变成**能红的常驻断言**。

两条结论
--------
  ① **全片的风险红出现在哪里** —— 每条红都在预料之内（白名单四段 / 黑名单六段零红区）；
  ② **字幕贴住配音、镜尾不悬空**（2026-10-05 口径变更：旧的「83 条 / 覆盖整片 / 零空档」已作废，
     见 `docs/DEBT.md` **D-51**；现基线 75 条 / 覆盖 93.1%）。

为什么每条都能红
----------------
老规矩：「一条不会红的检查等于没有检查」。每个用例的 docstring 都写明**怎么改就会红**。

A 组合成素材上的断言：换机器 / `tests/test_rebuild_from_scratch.py` 把产物删光后照跑。
B 组真产物断言：`output/film/*.mp4` 或交付区 SRT **缺则 `pytest.skip`** ——
"产物不存在"不等于"判据坏了"，硬挂上去会在从零重造那条链上假红。

判色阈值与几何口径
------------------
口径单一来源 = `tools/check_film_content.py`（阈值出处 `loreal-video-script.md` §8.5⑤⑥ +
`docs/DEBT.md` **D-18**）。本文件**不重复定义阈值**，只 import 那个模块。

⚠️ 断言一律用 **960×540 口径的结构性事实**（`== 0` / `> 0` / `>= 2 块`）——
把一个全分辨率的像素数当阈值写进来，缩放口径不同 ⇒ 必假红。
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_film_content as cf   # noqa: E402

#: 合成素材落点（`tests/test_film.py` 同款手法：现造，不碰真产物）
WORK = ROOT / "output" / "_test_film_content"
WORK.mkdir(parents=True, exist_ok=True)


# =========================================================================== A. 合成素材


def _png(name: str, blocks) -> np.ndarray:
    """现画一张 200×200 黑底 PNG 并**读回**（走一趟 PNG 编解码，证明链路是通的）。

    `blocks` = `[((x0, y0, x1, y1), (r, g, b)), ...]`。
    """
    d = WORK / "mask"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.png"
    im = Image.new("RGB", (200, 200), (0, 0, 0))
    for box, color in blocks:
        im.paste(color, box)
    im.save(p)
    return np.asarray(Image.open(p).convert("RGB"))


def _stamp(t: float) -> str:
    """秒 → `HH:MM:SS,mmm`。"""
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _srt(name: str, spans) -> pathlib.Path:
    """现造 SRT（**CRLF 行尾** —— 真实 SRT 就是 CRLF 存的）。"""
    d = WORK / "srt"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.srt"
    blocks = [f"{i}\r\n{_stamp(a)} --> {_stamp(b)}\r\n第{i}条\r\n"
              for i, (a, b) in enumerate(spans, 1)]
    p.write_bytes(("\r\n".join(blocks) + "\r\n").encode("utf-8"))
    return p


def test_红掩膜能红能绿_且阈值是闭区间():
    """掩膜的三态 + 闭区间边界都得钉死 —— 一个只会说"没红"的判据是没有的判据。

    怎么改就会红：
      · 把 `RED` 改成 `(151, 90, 90)` ⇒ 纯红 (255,0,0) 仍绿，但边界用例
        **(150,90,90) 不再算红** ⇒ 第 ④ 段断言直接红；
      · 把 `<=` 写成 `<` ⇒ 同上（(150,90,90) 掉出去）；
      · 把 `AMBER_G_MIN` 丢掉、只留 3 元组 ⇒ (200,150,60) 会被判成琥珀，而
        (255,0,0) 也会落进琥珀 ⇒ 第 ② 段 `amber == 0` 红。
    """
    # ① 全黑 ⇒ 两个都得是 0
    black = _png("black", [])
    assert cf.count_colors(black) == (0, 0), "全黑图数出了颜色 ⇒ 掩膜条件写反了"

    # ② 纯红 (255,0,0)：红要 > 0，琥珀必须 0（G=0 低于琥珀下界）
    red = _png("red", [((60, 60, 140, 140), (255, 0, 0))])
    r_px, a_px = cf.count_colors(red)
    assert r_px == 80 * 80, f"红像素计数不对：{r_px} ≠ {80 * 80}"
    assert a_px == 0, f"纯红被判进了琥珀（{a_px} px）⇒ 阈值串了（D-18 的两条判据不能混）"

    # ③ 琥珀 (200,150,60)：琥珀 > 0，红必须 0（G=150 高于红的上界 90）
    amber = _png("amber", [((60, 60, 140, 140), (200, 150, 60))])
    r_px, a_px = cf.count_colors(amber)
    assert a_px == 80 * 80, f"琥珀像素计数不对：{a_px} ≠ {80 * 80}"
    assert r_px == 0, f"琥珀被判进了红（{r_px} px）⇒ 红的上界 G 放太宽了"

    # ④ 闭区间边界：只差 1 的像素必须落在正确的一侧
    on_edge = _png("edge_on", [((100, 100, 101, 101), (150, 90, 90))])
    assert cf.count_colors(on_edge) == (1, 0), (
        "(150,90,90) 是红的**闭区间边界**，必须算红 —— 改成开区间这条就红")
    below_r = _png("edge_r", [((100, 100, 101, 101), (149, 90, 90))])
    assert cf.count_colors(below_r)[0] == 0, "(149,90,90) 差 1 必须**不算红**（R 下界被放宽了？）"
    above_g = _png("edge_g", [((100, 100, 101, 101), (150, 91, 90))])
    assert cf.count_colors(above_g)[0] == 0, "(150,91,90) 差 1 必须**不算红**（G 上界被放宽了？）"


def test_连通块计数能区分两把红叉与一块红():
    """镜 2 的判据是"**两把**红叉" ⇒ 必须真在数连通性，不是数颜色块个数。

    怎么改就会红：
      · 把 `blobs()` 的 4 邻接改成 8 邻接 ⇒ 对角相邻的两块会被并成一块，
        但 ⑶ 那条"用细线连起来"仍读 1 ⇒ 这个变异不一定被抓；真正的钉子是把
        `blobs()` 换成"数掩膜里连通域个数"以外的任何东西（比如直接 `return 1`）⇒ ⑵ 红；
      · 把 `min_px` 默认值调到 1700 以上 ⇒ 40×40=1600 的方块不算块 ⇒ ⑵ 与 ⑶ 都红。
    """
    box = ((10, 10, 50, 50), (255, 0, 0))
    far = ((80, 10, 120, 50), (255, 0, 0))

    two = cf.blobs(cf.red_mask(_png("two", [box, far])))
    assert two == 2, f"两个分离的 40×40 红方块该数出 2 块，实得 {two}"

    one = cf.blobs(cf.red_mask(_png("one", [box])))
    assert one == 1, f"一个红方块该数出 1 块，实得 {one}"

    # 用一条 4px 宽的红线把两块缝起来 ⇒ 连通性变了，必须读成 1 块
    bridge = ((50, 28, 80, 32), (255, 0, 0))
    linked = cf.blobs(cf.red_mask(_png("linked", [box, far, bridge])))
    assert linked == 1, (
        f"两块被红线缝起来了却数出 {linked} 块 ⇒ 它没在数连通性，只在数颜色块个数")


def test_字幕覆盖统计能红能绿():
    """`coverage` / `max_gap` / `overlaps` 三个读数各自都要能红。

    口径（单一来源 `check_film_content.subtitle_stats` 的 docstring）：
      · `coverage` = 各条 `[起,止)` **并集** ∩ `[0, duration]` 的长度 / duration；
      · `max_gap`  = 相邻条 `起[i+1] - 止[i]` 的最大值（负值按 0 计），
                    且**末尾到 duration 的空档也算一个 gap**。

    ⚠️ 派单 §4.A.3 的 ① 写的是"两条 [0,1) [1,2)、duration=3.0 ⇒ max_gap == 0"，
    这句**自相矛盾**：末尾空档 3.0-2.0=1.0 也是 gap（定义第二句）。两个口径不可能同时成立
    （coverage 2/3 要求并集 2.0s，而"末尾接满 + 零空档"要求并集 3.0s）。这里按**定义**
    跑：绿例用 duration=2.0，把 2/3 与末尾空档 1.0 单列成 ①b。见回执。

    怎么改就会红：
      · 删掉 `max_gap = max(max_gap, max(0.0, duration - last))` ⇒ ①b 的 1.0 变 0.0 ⇒ 红；
      · `coverage` 把"并集"改成"各条长度之和" ⇒ ③ 的重叠场景会读到 4/3 > 1 ⇒ 红；
      · 解析不吃 CRLF ⇒ `n` 直接变 0，全部断言红。
    """
    # ① 首尾相接、且末条踩在片尾 ⇒ 覆盖满、零空档（绿）
    p = _srt("contig", [(0.0, 1.0), (1.0, 2.0)])
    st = cf.subtitle_stats(p, 2.0)
    assert st["n"] == 2, f"CRLF 解析出 {st['n']} 条，应为 2（LineEnding 归一丢了？）"
    assert (st["first"], st["last"]) == (0.0, 2.0)
    assert st["coverage"] == pytest.approx(1.0), f"覆盖应为 100%，实得 {st['coverage']:.4f}"
    assert st["max_gap"] == 0.0, f"首尾相接不该有空档，实得 {st['max_gap']:.3f}s"
    assert st["overlaps"] == 0

    # ①b 同两条、片长拉到 3.0 ⇒ 覆盖掉到 2/3，末尾那 1.0s 就是一种 gap
    st = cf.subtitle_stats(p, 3.0)
    assert st["coverage"] == pytest.approx(2 / 3), f"覆盖应为 2/3，实得 {st['coverage']:.4f}"
    assert st["max_gap"] == pytest.approx(1.0), (
        f"末尾到片尾的空档没算成 gap（实得 {st['max_gap']:.3f}s）⇒ 这条判据是摆设")

    # ② 中间断开 1s ⇒ max_gap 必须读到 1.0（这条就是能打红的那条）
    gap = _srt("gap", [(0.0, 1.0), (2.0, 3.0)])
    st = cf.subtitle_stats(gap, 3.0)
    assert st["max_gap"] == pytest.approx(1.0), f"1s 空档没被读到（实得 {st['max_gap']:.3f}s）"
    assert st["coverage"] == pytest.approx(2 / 3)
    assert st["overlaps"] == 0, "断开的字幕不该算重叠"

    # ③ 两条重叠 ⇒ overlaps == 1，且并集口径下 coverage 不虚高
    ov = _srt("overlap", [(0.0, 2.0), (1.0, 3.0)])
    st = cf.subtitle_stats(ov, 3.0)
    assert st["overlaps"] == 1, f"重叠没被数出来（实得 {st['overlaps']}）"
    assert st["coverage"] == pytest.approx(1.0), (
        f"重叠区间把覆盖率顶到了 {st['coverage']:.4f} ⇒ 用的是「各条长度之和」而不是并集")

    # ④ 删掉中间一条 ⇒ n 少 1 且 coverage 变小（证明它数的是真实内容，不是常量）
    full = _srt("three", [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)])
    before = cf.subtitle_stats(full, 3.0)
    assert (before["n"], before["coverage"]) == (3, pytest.approx(1.0))

    fewer = _srt("three_minus_mid", [(0.0, 1.0), (2.0, 3.0)])
    after = cf.subtitle_stats(fewer, 3.0)
    assert after["n"] == before["n"] - 1, "删掉一条后条数没减 ⇒ 解析在数常量"
    assert after["coverage"] < before["coverage"], (
        f"删掉一条后覆盖率没降（{after['coverage']:.4f} vs {before['coverage']:.4f}）"
        " ⇒ 覆盖率没真读内容")


# =========================================================================== B. 真产物

#: 允许出现风险红的段 —— **每行都是答辩要念的口径**，别把新段偷偷塞进来
ALLOWED_RED = {
    "seg_shot01_hook": "镜1 图表自带红（D-18 白名单：图表的红不是风险红）",
    "seg_shot02_creator": "设计红：两把红叉，且峰值帧必须 blobs >= 2",
    "seg_shot04_product": "产品页自带 #ff6b6b 等级标签（D-18 白名单，demo/index.html:336）",
    "seg_shot09_boundary": "设计红：「开卷考试」红字",
}

#: **不得出现风险红**的段（红像素 < 一个连通块口径的 100 px 且无 >=100px 连通块）
FORBIDDEN_RED = (
    "seg_shot03_answer",
    "seg_shot05_bench",
    "seg_shot06_pack",
    "seg_shot07_abstain",
    "seg_shot08_product",
    "seg_shot10_close",
)

#: 一段"可称为风险红的红色区域"的最小面积（px，960×540 口径）。
#: 同一常量两处用：连通块 `min_px`，以及红像素总数的上界 ——
#: 实测噪声底是 **1~17 px**（单帧总共，全是散点），100 px 是它一个数量级以上的天花板。
RISK_PX = 100


@pytest.fixture(scope="module")
def film_scan():
    """逐段扫一遍真产物（缺则 skip）。每段给：红峰值 / 红帧数 / 琥珀峰值 / 峰值帧红连通块数。"""
    segs = cf.segments()
    if not segs:
        pytest.skip("output/film/ 下没有 seg_shot*.mp4 ⇒ 跳过真产物断言（不清产物照跑）")
    out = {}
    for p in segs:
        rows, peak = cf.scan_peak(p)
        out[p.stem] = {
            "red_peak": max((r for _, r, _ in rows), default=0),
            "amber_peak": max((a for _, _, a in rows), default=0),
            "red_frames": sum(1 for _, r, _ in rows if r > 0),
            "red_blobs": cf.blobs(cf.red_mask(peak), min_px=RISK_PX) if peak is not None else -1,
        }
    return out


def test_成片段数恰为10_且排除D5追加块():
    """`segments()` 必须排除 `seg_shot11_*`（S11 是 D5 追加块，**不在** 179s 成片链里）。

    怎么改就会红：把 `EXCLUDED_PREFIX` 过滤去掉 ⇒ 有 S11 时读到 11 段；
    去掉 `sorted()` ⇒ 段序变成文件系统序，`film_scan` 的段名对不上白/黑名单。
    """
    segs = cf.segments()
    if not segs:
        pytest.skip("output/film/ 下没有 seg_shot*.mp4 ⇒ 跳过")
    names = [p.stem for p in segs]
    assert len(segs) == cf.FILM_SEGMENTS, (
        f"成片链该是 {cf.FILM_SEGMENTS} 段，实得 {len(segs)}：{names}"
        f"（`seg_shot11_*` 是 D5 追加块，必须排除）")
    assert not any(n.startswith(cf.EXCLUDED_PREFIX) for n in names), f"S11 混进来了：{names}"
    assert names == sorted(names), f"段序没排：{names}"


def test_成片风险红只出现在白名单段(film_scan):
    """**全片的风险红都在预料之内** —— 这是"成片没有意外红"的唯一证据。

    判据用 960×540 口径的**结构性事实**：
      · 白名单段：`red_peak > 0`（那段确实自带红，白名单项不是过期的）；
        其中镜 2 还要 `blobs(峰值帧) >= 2`（**两把**红叉，不是一块红）；
      · 黑名单段：峰值帧 `blobs == 0`（没有 >=100px 的红色区域）且 `red_peak < 100`
        （一个数量级以上的噪声天花板；实测噪声底 1~17 px，见回执）。
    不写全分辨率的 32,431 / 6,417 这类数字当阈值 —— 缩放口径不同，写进来必假红。

    怎么改就会红：
      · 黑名单段里插一块任何 >=100px 的红（比如给镜 6 加个红标）⇒ blobs 或 red_peak 红；
      · 白名单段把红去掉（镜 2 的红叉改绿）⇒ `red_peak > 0` 红；
      · 镜 2 的两把红叉并成一把，或把它俩缝起来 ⇒ `blobs >= 2` 红；
      · 新增一个段又不进任何名单 ⇒ 完整性断言红。
    """
    assert set(film_scan) == set(ALLOWED_RED) | set(FORBIDDEN_RED), (
        "有段落既不在白名单也不在黑名单 —— 新增段必须显式做一次判定："
        f"{sorted(set(film_scan) ^ (set(ALLOWED_RED) | set(FORBIDDEN_RED)))}")

    for name, why in ALLOWED_RED.items():
        got = film_scan[name]
        assert got["red_peak"] > 0, (
            f"{name} 在白名单里（{why}），却一个红像素都没有 ⇒ 白名单项过期了")

    assert film_scan["seg_shot02_creator"]["red_blobs"] >= 2, (
        f"镜2 峰值帧只数出 {film_scan['seg_shot02_creator']['red_blobs']} 块红，"
        "设计要求**两把**红叉（被并成一块 / 只剩一把？）")

    for name in FORBIDDEN_RED:
        got = film_scan[name]
        assert got["red_blobs"] == 0, (
            f"{name} 不在白名单里，却在峰值帧出现了 {got['red_blobs']} 块 >= {RISK_PX}px 的红色区域"
            f"（红峰 {got['red_peak']} px）⇒ 成片里有了计划外的红")
        assert got["red_peak"] < RISK_PX, (
            f"{name} 红峰 {got['red_peak']} px 顶破了噪声天花板 {RISK_PX}px ⇒ 有红在长")


def test_镜5第二行是琥珀色_不是红(film_scan):
    """§8.5⑤「第二行改琥珀」那处要真落地 —— 镜 5 必须有琥珀。

    怎么改就会红：把镜 5 的第二行改回红色 / 改回白色 ⇒ `amber_peak == 0`；
    顺带那条 `red_blobs == 0`（在上一用例里）会把"改成红色"这条变异也抓住。
    """
    got = film_scan["seg_shot05_bench"]
    assert got["amber_peak"] > 0, (
        "镜5 的琥珀峰为 0 ⇒ §8.5⑤「第二行改琥珀」没上屏（或改回了红/白）")
    assert got["red_blobs"] == 0, (
        "镜5 出现了红色区域 ⇒ 第二行可能是红的（§8.5⑤ 要求琥珀）")


def test_字幕贴住配音_不悬空():
    """字幕末条必须**贴住配音实测终点**（镜尾只留静音留白），不许铺满槽位。

    口径（2026-10-05 二次修，见 `docs/DEBT.md` D-51）：旧口径按字数**铺满槽位**，
    于是「83 条 / 覆盖 100% / 零空档」被当成目标 —— 但槽位是**排片窗口、比配音长**
    （十镜合计留白 10.9s）⇒ 字幕天然比配音慢，镜尾最多悬空 2.12s（镜 8）。旧音色离
    槽位近、看不出来；2026-10-05 换云希 +12% 后留白变大，用户实看即成片报了
    「视频字幕和配音不对应」。现口径锚在 edge-tts 真实句界上：
    **75 条 / 覆盖 93.1% / 空档只落镜尾 0.20–2.17s / 交叠 0 / 最长 3.47s**。

    怎么改就会红：拿旧「铺满槽位」的 SRT 喂进来 ⇒ `last_off` = 2.12s；删掉任意一条 ⇒
    `n`；让两条交叠 ⇒ `overlaps`；单条拖到 >3.5s ⇒ `max_dur`；把某镜字幕拖到槽位末 ⇒
    `last_off` 与 `gap_in_tail` 同时红。
    ⚠️ 实测与基线不符时**不许改断言迁就** —— 报差异（铁律：口径写于测量之前）。
    """
    srt = cf.find_srt()
    if srt is None or not srt.is_file():
        pytest.skip(f"交付区没有字幕（找过 {[str(p) for p in cf.SRT_CANDIDATES]}）⇒ 跳过")

    st = cf.subtitle_stats(srt, cf.FILM_DURATION)
    assert st["n"] == 75, f"字幕条数 {st['n']} ≠ 75（口径见 docs/DEBT.md D-51）"
    assert st["overlaps"] == 0, f"有 {st['overlaps']} 处字幕重叠（基线 0）"
    assert st["max_dur"] <= 3.5 + 1e-6, f"最长一条 {st['max_dur']:.2f}s > 3.5s（台本规则）"
    assert 0.88 <= st["coverage"] <= 0.96, (
        f"覆盖率 {st['coverage'] * 100:.1f}% 不在 88–96%（现口径=只覆盖真实语音；"
        f"旧口径「100%」已作废）")
    assert st["max_gap"] <= 2.5, (
        f"最大空档 {st['max_gap']:.2f}s > 2.5s ⇒ 镜尾留白远超配音缺口（配音换过速？）")

    # 核心判据：字幕末条贴住**配音实测终点**，且空档只落在镜尾
    script = cf.find_script()
    if script is None or not script.is_file():
        pytest.skip(f"交付区没有台本（找过 {[str(p) for p in cf.SCRIPT_CANDIDATES]}）⇒ 跳过贴配音检查")
    al = cf.subtitle_voice_alignment(srt, script)
    if not al.get("ok"):
        pytest.skip(f"贴配音检查不可判：{al.get('why')}")
    assert al["last_off"] <= 0.35, (
        f"镜内末条字幕与配音实测终点最大差 {al['last_off']:.2f}s（基线 ≤0.35s）"
        f" ⇒ 字幕没贴住配音（旧口径实测 2.12s）")
    assert al["gap_in_tail"], "某镜**镜内**出现 >0.05s 字幕空洞 ⇒ 字幕段没按真实句界铺"
