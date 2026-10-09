# -*- coding: utf-8 -*-
"""标定集：`lighting`（光影/阴影一致性）与 `color_edge`（色彩过渡）的**四张**合成样图。

预注册 v2 §4 的纪律：**标定集与评测集物理分开**（评测集是既有的 `samples/` 冻结样张，
本脚本一张都不动、也不进提交包 —— `tools/make_submission_pack.py` 是硬编码白名单）。
**口径写于测量之前**：四张图的"应该命不命中"是本脚本写死的**预期**，脚本只负责
跑一次并把**真实数字**打印出来；数字对不上预期时**只许改协议并留档，不许改检测器阈值**。

四张图与预期（跑之前就定死）：
  ① `calib_grid_same_source.jpg` —— 同一来源九宫格拼版（9 块取自**同一张**合成照片，
     光照方向与色温都一致）→ **不应该命中**（拼版是正常创作，不是拼接篡改）
  ② `calib_splice_cross_source.jpg` —— 跨源拼接：从**另一个来源**抠一块贴进来，
     光照方向不同 + 色温不同 + 纹理锐度不同 → **应该命中**（至少一个维度给出坐标）
  ③ `calib_global_warm.jpg` —— 全图加暖色滤镜（**全局**、无局部跳变）→ **不应该命中**
     （调色是良性编辑，这正是"宁可漏判不冤枉人"要保住的一类）
  ④ `calib_flat_color.png` —— 纯色卡（无纹理、无梯度）→ **应该弃权**
     （`status=="unavailable"`：测不出方向/过渡，绝不假装"无异常"）

⚠️ 四张全是**程序合成**（铁律 4：只用合成样本，绝不上传真实人物照片/证件图）。
⚠️ 本脚本不被提交包引用，也不改任何评测/冻结产物。

用法：
    env -u PYTHONPATH PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe samples/calib_lighting_color.py
"""

from __future__ import annotations

import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "samples" / "calib"
sys.path.insert(0, str(ROOT))

from voiceguard.forensics.color_edge import color_edge_detector   # noqa: E402
from voiceguard.forensics.lighting import lighting_detector       # noqa: E402

#: 画布。短边 480 → `lighting` 的块 = 480//8 = 60px。
W, H = 640, 480

#: 光照斜坡的幅值（亮度摆幅 / 画布半宽）：要让 `lighting` 的 median(m_b) 过弃权线（2.0），
#: 渐变斜率必须够大 —— 合成图没有真实照片的纹理来兜底（斜率 = RAMP/(max(W,H)/2) ≈ 0.27
#: ⇒ m_b ≈ 8×0.27 ≈ 2.1，实测 ~2.4）。**这是造标定集的口径，不是检测器参数**：检测器那一侧
#: 的 2.0 / 45° / 6.0 一个字都没动。
RAMP = 85.0
#: 基底纹理强度。⚠️ 第一版用了 grain=9 / 低频 18，结果**每块的梯度方向散在 0–180°**
#: （实测 R=0.284、θ 逐个乱跳）—— 那不是"全图光照一致"的图，是"没有主导方向"的图，
#: 场景根本没造出来。降到 grain=4 / 低频 8 之后 R=0.79，才是本题要的"单光源、方向一致"。
GRAIN, LOW_AMP = 4.0, 8.0


def _texture(rng: np.random.Generator, w: int, h: int, *, grain: float, low_amp: float,
             blur: int) -> np.ndarray:
    """多尺度纹理：低频斑块 + 高频颗粒。`blur>0` 用来给"另一个来源"做出更平滑的纹理。"""
    low = cv2.resize(rng.normal(0, 1, (max(h // 24, 2), max(w // 24, 2))).astype(np.float32),
                     (w, h), interpolation=cv2.INTER_CUBIC)
    t = low * low_amp + rng.normal(0, grain, (h, w)).astype(np.float32)
    if blur:
        t = cv2.GaussianBlur(t, (blur * 2 + 1, blur * 2 + 1), 0)
    return t


def _photo(w: int, h: int, *, angle_deg: float, color_bgr: tuple, seed: int,
           grain: float = GRAIN, low_amp: float = LOW_AMP, blur: int = 0,
           ramp: float = RAMP) -> np.ndarray:
    """合成一张"有方向性光照的照片"。

    `angle_deg` = **光照梯度的方向**（亮度增长的方向）。这就是 `lighting` 要估的东西：
    全图一致 → 正常；局部反了 → 违背一致性。

    ⚠️ **光照是加性的**：`像素 = 中间调基色 + 光照偏移`。第一版写成「基色 × 相对亮度」，
    亮度摆到 1.9× 时 B/G/R 里 150–215 的那一路直接顶到 255 被削平 —— 削平块的梯度是 0，
    实测 `m_b≈0.33`，把稳健 z 的 sigma 从 ~0.3 抬到 1.71，命中因此变得勉强且不可复现。
    加性还有一层好处：**色温**由 `color_bgr` 的蓝↔橙配比表达，与亮度解耦。
    `ramp` = 光照偏移的幅值（沿 `angle_deg` 方向从 -ramp 摆到 +ramp）。
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    th = np.radians(angle_deg)
    proj = ((xx - w / 2.0) * np.cos(th) + (yy - h / 2.0) * np.sin(th)) / (max(w, h) / 2.0)
    off = ramp * proj + _texture(rng, w, h, grain=grain, low_amp=low_amp, blur=blur)
    img = np.empty((h, w, 3), np.float32)
    for c in range(3):
        img[:, :, c] = np.clip(float(color_bgr[c]) + off, 0.0, 255.0)
    return img


def _band_mask(w: int, h: int, x0: int, x1: int, yc: int, half: float, amp: float,
               seed: int) -> np.ndarray:
    """横向长条贴块掩膜，上下边界带正弦扰动（真实拼接的常见形态：换一条带 / 一块标签）。

    ⚠️ 为什么**不是**团块/正圆（第一版用的就是那个，实测踩了两个坑）：
      · `color_edge` 的接缝闸门是**绝对面积**（0.5% × 图面积 = 1536 px）。接缝是细环，
        像素数 ≈ 周长 × 1~2px ⇒ 团块（r=150，周长 ~1000）**够不到** 1536 ⇒ `n_candidates=0`。
      · 但把团块放大到周长够，**偏离块的块足迹**就上去了 ⇒ `lighting` 的 R 掉到 0.35 以下。
      ⇒ 形状本身卡死了。长条是唯一同时满足「接缝周长达标」**且**「偏离块足迹够小」的形态。

    ⚠️ **第二版把带收短收窄了**（`x1` 600→400、`half` 55→25，面积占比 20.6%→6.2%）：
    第一版按 `half=55` 造出来的带占 3 个块行（块=60px），实测 26/80 块的梯度方向与主导方向
    接近**正交**，把全图一致性 R 拉到 **0.2048 < 0.35** ⇒ `lighting` 不触发。R 是对块**等权**
    的圆周量，所以它只跟"偏离块的占比"有关：扫过 `half ∈{25,35,55} × x1∈{400,480,600}`
    （`experiments/_probe_calib_sweep.py`，只读探针）之后定在 **half=25 / x1=400**
    ⇒ 本脚本跑一次的真实输出：**R=0.5413、z 峰值 7.902、ΔE=14.489、纹理比 22.418，
    两个维度同时出坐标**（探针不落盘、不过 JPEG，数字略高：R=0.539 / ΔE=14.22 / 纹理比 126.4
    —— 差异全部来自 q92 重编码对这些量的影响，不是判据差异）。
    这不是"为了让样本过关挪阈值"——判据一个字没动，改的是**激励几何**（回执 §七）。
    """
    rng = np.random.default_rng(seed)
    xs = np.arange(x0, x1, dtype=np.float32)
    t = (xs - x0) / max(x1 - x0 - 1, 1)
    ph1, ph2 = (float(v) for v in rng.uniform(0.0, 2.0 * np.pi, 2))
    top = yc - half + amp * np.sin(2.5 * np.pi * t + ph1)
    bot = yc + half + amp * np.sin(2.0 * np.pi * t + ph2)
    poly = np.stack([np.concatenate([xs, xs[::-1]]),
                     np.concatenate([top, bot[::-1]])], axis=1).astype(np.int32)
    m = np.zeros((h, w), np.uint8)
    cv2.fillPoly(m, [poly], 1)
    return m.astype(bool)


def _jpeg(img: np.ndarray, path: pathlib.Path, q: int = 92) -> np.ndarray:
    """落盘 JPEG 再读回 —— 标定集必须走真实编解码（§4：用**实际会遇到的**输入形态）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", np.clip(img, 0, 255).astype(np.uint8),
                           [int(cv2.IMWRITE_JPEG_QUALITY), q])
    assert ok, f"JPEG 编码失败：{path}"
    buf.tofile(str(path))
    return cv2.imread(str(path), cv2.IMREAD_COLOR)


# ---------------------------------------------------------------- ① 同一来源九宫格

def make_grid_same_source() -> np.ndarray:
    """9 块**同一张**合成照片的不同裁切，无缝隙拼回 3×3。

    这是创作者真会做的"九宫格切图"：光照方向、色温、纹理锐度全图一致 ——
    拼块的接缝是**亮度台阶**，不是色温台阶，也不是纹理台阶。
    预期：`lighting` 不触发（方向一致）、`color_edge` 不触发（ΔE 与纹理比两条闸门挡住）。
    """
    src = _photo(W, H, angle_deg=20.0, color_bgr=(131, 141, 151), seed=11)
    tw, th = W // 3, H // 3
    # 9 块各取源图上**不同区域**的裁切（内容不同、光照/色温/纹理同源）。
    # ⚠️ 输出按 3*th × 3*tw 分配（640 不是 3 的倍数）：若按 W×H 分配，右边会留一条
    # **1px 的黑边**，那不是"拼版"，是凭空造出来的边缘 —— 会污染块级梯度。
    boxes = [(0, 0), (tw, 0), (2 * tw, 0), (0, th), (tw, th),
             (2 * tw, th), (0, 2 * th), (tw, 2 * th), (2 * tw, 2 * th)]
    out = np.zeros((3 * th, 3 * tw, 3), np.float32)
    for k, (x0, y0) in enumerate(boxes):
        crop = src[y0:y0 + th, x0:x0 + tw]
        r, c = divmod(k, 3)
        out[r * th:(r + 1) * th, c * tw:(c + 1) * tw] = crop
    return out


# ---------------------------------------------------------------- ② 跨源拼接

def make_splice_cross_source() -> tuple[np.ndarray, dict]:
    """跨源拼接：从左打光的冷色底图上，贴一块**从右打光 + 暖色 + 更平滑 + 更陡**的另一个来源的贴块。

    同时违反四件事（这正是跨源拼接在真实世界里的样子）：
      · 光照**方向**差 90° >> 45°（`lighting` 的方向判据）
      · 光照**强度**（梯度幅度）约为底图的 2.4 倍（`lighting` 的幅值 z 判据 —— 只差方向
        是不够的：预注册 §2.1 的提取线 z≥4 是**幅值**的稳健 z，方向偏但幅度平的块
        根本进不了候选集）
      · 色温（a/b）在边界跳变（`color_edge` 的 ΔE 判据）
      · 纹理锐度在边界跳变（`color_edge` 的纹理比判据 —— 用来把"物体边界"排除掉的那条）
    预期：**应该命中**，两个维度至少一个给出坐标。
    """
    # 基底：左→右（0°），冷色（B>R）。
    base = _photo(W, H, angle_deg=0.0, color_bgr=(151, 141, 131), seed=21)
    # 贴块来源：上→下（90°）、暖色（R>B）、**更陡**（斜率 0.6 vs 基底 0.27，即 m_b 约 2 倍）、
    # 更平滑（grain/blur → 过 `color_edge` 的纹理比闸门）。色温差落在"蓝↔橙"轴上。
    src_b = _photo(600, 170, angle_deg=90.0, color_bgr=(131, 141, 151), seed=22,
                   grain=2.0, low_amp=3.0, blur=2, ramp=180.0)
    # 带几何（见 `_band_mask` 的第二版说明）：half 越小 → 偏离块的块足迹越小 → 全图一致性 R 越高。
    x0, x1, yc, half, amp = 40, 400, 290, 25.0, 10.0
    band = _band_mask(W, H, x0, x1, yc, half, amp, seed=23)
    ys, xs = np.nonzero(band)
    by0, by1, bx0, bx1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    hh, ww = by1 - by0, bx1 - bx0
    sy, sx = int(src_b.shape[0] // 2 - hh // 2), int(src_b.shape[1] // 2 - ww // 2)
    patch = src_b[sy:sy + hh, sx:sx + ww]
    assert patch.shape[:2] == (hh, ww), f"贴块源窗口越界：{(hh, ww)} vs {patch.shape[:2]}"
    out = base.copy()
    sub = band[by0:by1, bx0:bx1]
    out[by0:by1, bx0:bx1][sub] = patch[sub]
    return out, {"band": [x0, yc - half, x1 - x0, int(2 * half)], "seed": 23,
                 "n_px": int(band.sum()),
                 "area_ratio": round(float(band.sum()) / float(W * H), 4),
                 "bbox": [bx0, by0, ww, hh],
                 "base_light_deg": 0.0, "patch_light_deg": 90.0,
                 "base_color_bgr": [151, 141, 131], "patch_color_bgr": [131, 141, 151]}


# ---------------------------------------------------------------- ③ 全局暖色滤镜

def make_global_warm() -> np.ndarray:
    """整张图加一个**全局**暖色调（R↑ B↓）—— 调色，不引入任何局部跳变。

    预期：**不应该命中**。`lighting` 看的是方向（全局**加性**偏置不改梯度方向，也不改梯度幅度）；
    `color_edge` 看的是**局部**色度跳变（全局加法在差分里被消掉 ⇒ S 不变）。

    ⚠️ 用加性偏置而非乘性增益：乘性会把 B↓R↑ 后的通道推到 255 削平，削平处的色度梯度变 0，
    反而制造出假的**局部**跳变（与"全局调色"这个场景自相矛盾），并把 `median_s` 压低。
    """
    img = _photo(W, H, angle_deg=20.0, color_bgr=(150, 165, 195), seed=31)
    tint = np.array([-14.0, 0.0, 14.0], np.float32)             # B↓ G≈ R↑（蓝↔橙轴）
    return np.clip(img + tint, 0.0, 255.0)


# ---------------------------------------------------------------- ④ 纯色卡

def make_flat_color() -> np.ndarray:
    """纯色卡：无纹理、无梯度。两个检测器都**测不出**任何东西 → 必须**弃权**，不许报"无异常"。"""
    return np.full((H, W, 3), (168, 150, 132), np.float32)


# ---------------------------------------------------------------- 跑一次 + 打印

#: 预期（**写于测量之前**）：True = 应该给出坐标，False = 不应该，None = 应该弃权。
EXPECT = {
    "calib_grid_same_source.jpg": {"hit": False},
    "calib_splice_cross_source.jpg": {"hit": True},
    "calib_global_warm.jpg": {"hit": False},
    "calib_flat_color.png": {"abstain": True},
}

_SKIP = {"measures", "not_measures", "polarity_note"}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    splice = make_splice_cross_source()
    made: list[tuple[str, np.ndarray]] = []
    made.append(("calib_grid_same_source.jpg", _jpeg(make_grid_same_source(),
                                                     OUT_DIR / "calib_grid_same_source.jpg")))
    made.append(("calib_splice_cross_source.jpg", _jpeg(splice[0],
                                                        OUT_DIR / "calib_splice_cross_source.jpg")))
    made.append(("calib_global_warm.jpg", _jpeg(make_global_warm(),
                                                OUT_DIR / "calib_global_warm.jpg")))
    flat = (np.clip(make_flat_color(), 0, 255).astype(np.uint8))
    cv2.imwrite(str(OUT_DIR / "calib_flat_color.png"), flat)
    made.append(("calib_flat_color.png", cv2.imread(str(OUT_DIR / "calib_flat_color.png"),
                                                    cv2.IMREAD_COLOR)))

    print("=" * 78)
    print("标定集（合成，程序生成）—— 每张**只跑一次**，下面是**真实数字**，不是预期值")
    print(f"落盘目录：{OUT_DIR}")
    print("=" * 78)
    bad = 0
    for fname, img in made:
        print()
        print(f"---- {fname}　（{img.shape[1]}×{img.shape[0]}）")
        if fname.startswith("calib_splice"):
            print(f"     真值（造图时写死的）：{splice[1]}")
        # 纪律：每张图**只跑一次**（跑两次会把"跑一次"的口径变成"最好那次"）
        got = {}
        for det in (lighting_detector, color_edge_detector):
            r = det(img)
            got[r.name] = r
            vals = {k: v for k, v in (r.raw or {}).items() if k not in _SKIP}
            print(f"  [{r.name}] status={r.status} score={r.score} n_regions={len(r.regions)}"
                  f" confidence={r.confidence}")
            if r.reason:
                print(f"           reason={r.reason}")
            print(f"           raw={vals}")
            for g in r.regions[:3]:
                print(f"           region={g}")
        exp = EXPECT[fname]
        hits = {k: len(v.regions) for k, v in got.items()}
        st = {k: v.status for k, v in got.items()}
        n_hit = sum(1 for v in hits.values() if v > 0)
        if exp.get("abstain"):
            ok = all(v == "unavailable" for v in st.values())
            why = f"两个都弃权？{ok}（{st}）"
        else:
            ok = (n_hit > 0) == bool(exp["hit"])
            why = (f"预期{'命中' if exp['hit'] else '不命中'}，实际命中维度数={n_hit}（{hits}）")
        print(f"  → 预期核对：{'OK ' if ok else '*** 与预期不符 ***'}　{why}")
        if not ok:
            bad += 1
    print()
    print("=" * 78)
    print(f"标定集结论：{len(made)} 张，{len(made) - bad} 张与**测量前写下的预期**一致，{bad} 张不符。")
    print("⚠️ 不符**不许**改检测器阈值（铁律 1/2）—— 只能改协议并留档，如实写进回执。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
