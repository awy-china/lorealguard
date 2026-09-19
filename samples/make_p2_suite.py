"""P2 误报压力测试集 · 生成器（数据集 = 生成器 + 固定种子 + sha256 清单）

## 为什么要有这个集子

我们的第一指标是「冤枉率」而不是「抓假率」。要证明不冤枉人，唯一的办法是
**把所有会诱发误报的正常编辑都造出来打自己** —— 而不是拿几张干净图说"我们很好"。

它同时是「作品升级为标准」的那一半：
    别人不需要相信我们的话，只需要跑同一个生成器（固定种子）
    → 比对 manifest 里每一张的 sha256 → 得到与公告完全一致的数据集 → 自己跑协议。

**为什么数据集不随仓库分发**：48 张合成图 ≈ 二十 MB，而"生成器 + 种子"唯一确定它们，
且可被 sha256 校验 —— 这比分发一堆文件更难偷改、也更好复现。

## 两类样本

| 类 | 含义 | 对待方式 |
|---|---|---|
| `benign` | 正常编辑/正常内容（美颜、平台重压缩、截图、滤镜、合法拼版、已声明 AI…），
          **以及多步叠加的真实发布链路**（磨皮→平台压缩→裁切…） | **必须被善待**：判定不得超过「关注级」 |
| `malicious` | 篡改/违规形态（跨源拼接、抹标识、克隆遮盖…） | 漏判可接受但要记录；**已知盲区写进 manifest，不藏起来** |

隐私：全部程序合成，不含任何个人数据。

用法：  py.bat samples/make_p2_suite.py
产物：  output/p2/suite/*.jpg|png  +  output/p2/manifest.json
"""

from __future__ import annotations

import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samples.make_splice_sample import build_splice            # noqa: E402

BASE = ROOT / "samples" / "base_neutral.jpg"
OUT = ROOT / "output" / "p2" / "suite"
MANIFEST = ROOT / "output" / "p2" / "manifest.json"
SEED = 20260920


# ------------------------------------------------------------------ 工具

def load_base() -> np.ndarray:
    if not BASE.exists():
        raise FileNotFoundError(f"缺少底图 {BASE}（先跑 samples/make_neutral_base.py）")
    return np.asarray(Image.open(BASE).convert("RGB")).copy()


def save(arr: np.ndarray, path: Path, quality: int = 93, exif: dict | None = None,
         png_text: dict | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.fromarray(arr.astype(np.uint8))
    if path.suffix.lower() == ".png":
        kw = {}
        if png_text:
            info = PngImagePlugin.PngInfo()
            for k, v in png_text.items():
                info.add_text(k, v)
            kw["pnginfo"] = info
        im.save(path, **kw)
    else:
        kw = {"quality": quality}
        if exif:
            e = Image.Exif()
            for tag, val in exif.items():
                e[tag] = val
            kw["exif"] = e
        im.save(path, "JPEG", **kw)
    return path


def resample_short_side(arr: np.ndarray, short: int) -> np.ndarray:
    h, w = arr.shape[:2]
    s = short / min(h, w)
    return cv2.resize(arr, (max(1, int(round(w * s))), max(1, int(round(h * s)))),
                      interpolation=cv2.INTER_AREA)


def bilateral(arr: np.ndarray, d: int, sigma: float) -> np.ndarray:
    """磨皮：双边滤波（保边平滑）—— 真实美颜 SDK 的核心算子之一。"""
    return cv2.bilateralFilter(arr.astype(np.uint8), d, sigma, sigma)


def blend(a: np.ndarray, b: np.ndarray, m: np.ndarray) -> np.ndarray:
    """m 为 [0,1] 浮点掩膜（H,W,1 或 H,W）。"""
    if m.ndim == 2:
        m = m[:, :, None]
    return np.clip(a.astype(np.float32) * (1 - m) + b.astype(np.float32) * m, 0, 255).astype(np.uint8)


def face_mask(shape) -> np.ndarray:
    """脸区软掩膜（美颜通常只作用在人脸）：用底图的椭圆脸位置。"""
    h, w = shape[:2]
    m = np.zeros((h, w), np.uint8)
    cv2.ellipse(m, (int(w * 0.5), int(h * 0.42)), (int(w * 0.17), int(h * 0.25)), 0, 0, 360, 255, -1)
    return cv2.GaussianBlur(m, (0, 0), 21).astype(np.float32)[:, :, None] / 255.0


# ------------------------------------------------------------------ 工况

def case_base(a, rng):
    return a, {}


def case_platform_1080_q75(a, rng):
    return resample_short_side(a, 1080), {}


def case_platform_1080_q60(a, rng):
    return resample_short_side(a, 1080), {"quality": 60}


def case_resave_same_quality(a, rng):
    return a, {}


def case_beauty_light(a, rng):
    return blend(a, bilateral(a, 7, 25), face_mask(a.shape) * 0.85), {}


def case_beauty_medium(a, rng):
    return blend(a, bilateral(a, 11, 45), face_mask(a.shape) * 0.95), {}


def case_beauty_heavy(a, rng):
    b = bilateral(a, 15, 75)
    return blend(a, b, face_mask(a.shape)), {}


def case_global_warm(a, rng):
    x = a.astype(np.float32) * np.array([1.06, 1.00, 0.92], np.float32)
    return np.clip(x, 0, 255), {}


def case_global_contrast(a, rng):
    x = (a.astype(np.float32) - 128.0) * 1.18 + 128.0
    return np.clip(x, 0, 255), {}


def case_global_brighten(a, rng):
    return np.clip(a.astype(np.float32) * 1.12, 0, 255), {}


def case_legal_layout(a, rng):
    """合法拼版：左「使用前」右「使用后」，中间分隔线。这是种草内容最常见的正常排版。"""
    right = np.clip(a.astype(np.float32) * 1.20, 0, 255).astype(np.uint8)
    out = np.hstack([a, np.full((a.shape[0], 6, 3), 255, np.uint8), right])
    return out, {}


def case_watermark_bar(a, rng):
    h, w = a.shape[:2]
    m = np.zeros((h, w), np.uint8)
    cv2.rectangle(m, (0, int(h * 0.86)), (w, h), 255, -1)
    mf = cv2.GaussianBlur(m, (0, 0), 5).astype(np.float32)[:, :, None] / 255.0
    bar = np.full_like(a, 245, np.uint8)
    return blend(a, bar, mf * 0.55), {}


def case_crop_rescale(a, rng):
    h, w = a.shape[:2]
    c = a[int(h * 0.1):int(h * 0.9), int(w * 0.1):int(w * 0.9)]
    return cv2.resize(c, (w, h), interpolation=cv2.INTER_CUBIC), {}


def case_rotate_slight(a, rng):
    h, w = a.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), 1.5, 1.0)
    return cv2.warpAffine(a, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE), {}


def case_high_iso(a, rng):
    return np.clip(a.astype(np.float32) + rng.normal(0, 9, a.shape), 0, 255), {}


def case_low_texture(a, rng):
    return cv2.GaussianBlur(a, (0, 0), 4.0), {}


def case_local_concealer(a, rng):
    """局部遮瑕（去痘涂抹）：合法美颜。用邻近区域的中值滤波结果覆盖一小块。"""
    h, w = a.shape[:2]
    x0, y0 = int(w * 0.42), int(h * 0.30)
    bw, bh = int(w * 0.06), int(h * 0.06)
    patch = cv2.medianBlur(a[y0:y0 + bh, x0:x0 + bw], 9)
    out = a.copy()
    out[y0:y0 + bh, x0:x0 + bw] = patch
    return out, {"bbox_xywh": [x0, y0, bw, bh]}


def case_declared_ai_png(a, rng):
    return a, {"ext": ".png", "png_text": {
        "parameters": "a portrait, best quality, ultra detailed\nNegative prompt: blurry\n"
                      "Steps: 30, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 20260920, "
                      "Size: 1600x1200, Model: sdxl_base_1.0, Version: v1.9.4"}}


def case_declared_ai_exif(a, rng):
    return a, {"exif": {0x010F: "Midjourney", 0x0110: "Midjourney v6",
                        0x0131: "Midjourney", 0x013B: "anonymous"}}


def case_camera_exif(a, rng):
    return a, {"exif": {0x010F: "Canon", 0x0110: "Canon EOS R6", 0x0131: "Digital Photo Professional",
                        0x013B: "Studio Sample", 0x8298: "(c) synthetic sample", 0x0112: 1}}


def case_copy_move(a, rng):
    """同图复制粘贴（ELA 已知盲区）：从同一张图另一处搬一块过来。"""
    h, w = a.shape[:2]
    src = a[int(h * 0.15):int(h * 0.15) + int(h * 0.12), int(w * 0.12):int(w * 0.12) + int(w * 0.12)]
    x0, y0 = int(w * 0.60), int(h * 0.62)
    out = a.copy()
    out[y0:y0 + src.shape[0], x0:x0 + src.shape[1]] = src
    return out, {"bbox_xywh": [x0, y0, src.shape[1], src.shape[0]]}


def case_clone_cover(a, rng):
    """克隆遮盖：把一块内容用邻近区域盖掉（伪造/隐去信息的形态）。"""
    h, w = a.shape[:2]
    x0, y0 = int(w * 0.72), int(h * 0.14)
    bw, bh = int(w * 0.10), int(h * 0.10)
    src = a[y0 + bh + 20:y0 + bh + 20 + bh, x0:x0 + bw]
    out = a.copy()
    out[y0:y0 + bh, x0:x0 + bw] = src
    return out, {"bbox_xywh": [x0, y0, bw, bh]}


def jpeg_roundtrip(arr: np.ndarray, quality: int = 75) -> np.ndarray:
    """真走一次 JPEG 编解码（不经磁盘）—— 平台转码/二次保存的本质。"""
    buf = BytesIO()
    Image.fromarray(arr.astype(np.uint8)).save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return np.asarray(Image.open(buf).convert("RGB")).copy()


# ---- 叠加工况：真实创作链路几乎都是多步串联，而不是单算子 ----


def case_stack_beauty_medium_q75(a, rng):
    """磨皮（中度）→ 平台缩图压缩：最典型的种草图发布路径。"""
    x, _ = case_beauty_medium(a, rng)
    return resample_short_side(x, 1080), {}


def case_stack_beauty_heavy_q60(a, rng):
    """重磨皮 → 强压（美容类内容被平台反复缩压的形态）。"""
    x, _ = case_beauty_heavy(a, rng)
    return resample_short_side(x, 1080), {"quality": 60}


def case_stack_warm_q75(a, rng):
    x, _ = case_global_warm(a, rng)
    return resample_short_side(x, 1080), {"quality": 75}


def case_stack_brighten_crop(a, rng):
    x, _ = case_global_brighten(a, rng)
    return case_crop_rescale(x, rng)


def case_stack_contrast_rotate(a, rng):
    x, _ = case_global_contrast(a, rng)
    return case_rotate_slight(x, rng)


def case_stack_iso_q60(a, rng):
    """暗光高 ISO 实拍 → 平台强压。"""
    x, _ = case_high_iso(a, rng)
    return resample_short_side(x, 1080), {"quality": 60}


def case_stack_blur_brighten(a, rng):
    x, _ = case_low_texture(a, rng)
    x, _ = case_global_brighten(x, rng)
    return x, {}


def case_stack_concealer_q75(a, rng):
    """遮瑕 → 平台压缩。

    注意：缩放会改坐标，GT 必须同步换算 —— 否则定位口径就是假的（宁可算对，不图省事）。
    """
    x, meta = case_local_concealer(a, rng)
    s = 1080 / min(a.shape[:2])
    x = resample_short_side(x, 1080)
    bx, by, bw, bh = meta["bbox_xywh"]
    return x, {"quality": 75,
               "bbox_xywh": [int(round(bx * s)), int(round(by * s)),
                             int(round(bw * s)), int(round(bh * s))]}


def case_stack_watermark_q60(a, rng):
    x, _ = case_watermark_bar(a, rng)
    return resample_short_side(x, 1080), {"quality": 60}


# ---- 平台链路 ----


def case_small_720_q60(a, rng):
    """短边只留 720 + q60：聊天工具/朋友圈转发后的真实画质。"""
    return resample_short_side(a, 720), {"quality": 60}


def case_double_compress(a, rng):
    """反复转存：同一质量压两遍（在线相册、聊天转发）。"""
    x = resample_short_side(a, 1080)
    return jpeg_roundtrip(jpeg_roundtrip(x, 75), 75), {"quality": 75}


def case_webp_roundtrip(a, rng):
    """平台转码：JPEG → WebP → 回 JPEG（输出仍是 JPEG，保证取证管线可读）。"""
    buf = BytesIO()
    Image.fromarray(a.astype(np.uint8)).save(buf, "WEBP", quality=80)
    buf.seek(0)
    x = np.asarray(Image.open(buf).convert("RGB")).copy()
    return jpeg_roundtrip(x, 90), {}


def case_png_downscale(a, rng):
    """降分辨率后存 PNG（无损但已缩图，且无 EXIF）。"""
    return resample_short_side(a, 1080), {"ext": ".png"}


# ---- 合法拼版：几何上是「拼接」，语义上完全合法 ----


def case_grid_2x2(a, rng):
    """四宫格：同一张图裁四块拼回一张。

    这是护栏的关键压力点 —— 内容真实，但几何上确实经过拼版；任何只认「有拼接痕迹」
    的判定都会在这里冤枉人。
    """
    h, w = a.shape[:2]
    hh, ww = h // 2, w // 2
    tiles = [a[:hh, :ww], a[:hh, ww:ww * 2], a[hh:hh * 2, :ww], a[hh:hh * 2, ww:ww * 2]]
    gap = np.full((hh, 5, 3), 255, np.uint8)
    top = np.hstack([tiles[0], gap, tiles[1]])
    bot = np.hstack([tiles[2], gap, tiles[3]])
    gapv = np.full((5, top.shape[1], 3), 255, np.uint8)
    return np.vstack([top, gapv, bot]), {}


def case_before_after_vertical(a, rng):
    """上下「使用前后」竖版（与 b12 的左右版并存：两种最常见排版都要善待）。"""
    bottom = np.clip(a.astype(np.float32) * 1.22, 0, 255).astype(np.uint8)
    h, w = a.shape[:2]
    band = np.full((6, w, 3), 255, np.uint8)
    return np.vstack([a, band, bottom]), {}


def case_border_pad(a, rng):
    """白边留白（社交平台常见的一圈留白，减少被裁）。"""
    h, w = a.shape[:2]
    pad = 40
    out = np.full((h + pad * 2, w + pad * 2, 3), 250, np.uint8)
    out[pad:pad + h, pad:pad + w] = a
    return out, {}


def case_cool_correct(a, rng):
    """色温校正（偏冷）—— 与 b09 暖色相对的另一种合法调色。"""
    x = a.astype(np.float32) * np.array([0.95, 1.00, 1.07], np.float32)
    return np.clip(x, 0, 255), {}


def case_rotate_crop_q75(a, rng):
    """旋转 → 裁剪 → 平台压缩：三步叠加。"""
    x, _ = case_rotate_slight(a, rng)
    x, _ = case_crop_rescale(x, rng)
    return resample_short_side(x, 1080), {"quality": 75}


def case_portrait_9_16(a, rng):
    """竖版裁切（9:16 封面）：裁掉两侧、不留黑边 —— 常见且合法。"""
    h, w = a.shape[:2]
    target_w = int(round(h * 9 / 16))
    x0 = max(0, (w - target_w) // 2)
    return np.ascontiguousarray(a[:, x0:x0 + target_w]), {}


# ------------------------------------------------------------------ 集子定义
#: 每条 = 一个工况。`expect` 是**我们对产品的承诺**，由 P2 逐条校验：
#:   max_tier      —— 判定等级上限（良性样本的护栏）
#:   blind_spot    —— 已知盲区（漏判是预期结果，写下来而不是藏起来）
#:   needs_layer   —— 依赖尚未接入的层（F4/F5/P1），当前必然达不到，须如实登记
CASES: list[dict] = [
    # ---------------- 良性：正常拍摄 / 正常传输 ----------------
    dict(id="b01", label="benign", cat="正常拍摄", desc="合成底图原样（模拟相机出货 q93）",
         fn=case_base, expect={"max_tier": "关注级"}),
    dict(id="b02", label="benign", cat="平台二次压缩", desc="短边缩到 1080 + q75",
         fn=case_platform_1080_q75, expect={"max_tier": "关注级"}),
    dict(id="b03", label="benign", cat="平台二次压缩", desc="短边缩到 1080 + q60（元数据基本被剥光）",
         fn=case_platform_1080_q60, expect={"max_tier": "关注级"}),
    dict(id="b04", label="benign", cat="截图转存", desc="存成 PNG（无 EXIF，ELA 不适用）",
         fn=lambda a, rng: (a, {"ext": ".png"}), expect={"max_tier": "关注级", "must_benign": "NO_JPEG_HISTORY"}),
    dict(id="b05", label="benign", cat="正常传输", desc="同质量二次保存（q93 → q93）",
         fn=case_resave_same_quality, expect={"max_tier": "关注级"}),
    dict(id="b20", label="benign", cat="正常拍摄", desc="带完整相机 EXIF（模拟真实拍摄）",
         fn=case_camera_exif, expect={"max_tier": "关注级", "must_provenance": "元数据完整但无AI标识"}),
    # ---------------- 良性：美颜（头号误报风险） ----------------
    dict(id="b06", label="benign", cat="美颜磨皮", desc="轻度双边滤波（脸区 85% 混合）",
         fn=case_beauty_light, expect={"max_tier": "关注级"}),
    dict(id="b07", label="benign", cat="美颜磨皮", desc="中度双边滤波（脸区 95% 混合）",
         fn=case_beauty_medium, expect={"max_tier": "关注级"}),
    dict(id="b08", label="benign", cat="美颜磨皮", desc="重度双边滤波（脸区全混合）—— 压力点",
         fn=case_beauty_heavy, expect={"max_tier": "关注级"}),
    dict(id="b21", label="benign", cat="美颜遮瑕", desc="局部去痘涂抹（中值滤波小块）",
         fn=case_local_concealer, expect={"max_tier": "关注级"}),
    # ---------------- 良性：全局调整 ----------------
    dict(id="b09", label="benign", cat="全局滤镜", desc="暖色通道增益",
         fn=case_global_warm, expect={"max_tier": "关注级"}),
    dict(id="b10", label="benign", cat="全局滤镜", desc="对比度 +18%",
         fn=case_global_contrast, expect={"max_tier": "关注级"}),
    dict(id="b11", label="benign", cat="全局滤镜", desc="全图提亮 +12%",
         fn=case_global_brighten, expect={"max_tier": "关注级"}),
    # ---------------- 良性：排版与叠加 ----------------
    dict(id="b12", label="benign", cat="合法拼版", desc="左右「使用前后」对比排版（右侧整图提亮 20%）",
         fn=case_legal_layout, expect={"max_tier": "关注级"}),
    dict(id="b13", label="benign", cat="水印叠加", desc="底部半透明信息条",
         fn=case_watermark_bar, expect={"max_tier": "关注级"}),
    dict(id="b14", label="benign", cat="裁剪缩放", desc="裁 80% 后拉回原尺寸",
         fn=case_crop_rescale, expect={"max_tier": "关注级"}),
    dict(id="b15", label="benign", cat="旋转校正", desc="轻微旋转 1.5°",
         fn=case_rotate_slight, expect={"max_tier": "关注级"}),
    # ---------------- 良性：成像条件 ----------------
    dict(id="b16", label="benign", cat="高 ISO", desc="叠加传感器噪声 σ=9（暗光拍摄模拟）",
         fn=case_high_iso, expect={"max_tier": "关注级"}),
    dict(id="b17", label="benign", cat="低纹理", desc="重高斯模糊（纹理退化，ELA 敏感度下降）",
         fn=case_low_texture, expect={"max_tier": "关注级"}),
    # ---------------- 良性：已声明 AI（第六条（一）） ----------------
    dict(id="b18", label="benign", cat="已声明AI", desc="PNG 带 Stable Diffusion parameters 文本块",
         fn=case_declared_ai_png, expect={"max_tier": "提示级", "must_fire": "R-AI-LABEL-DECLARED"}),
    dict(id="b19", label="benign", cat="已声明AI", desc="EXIF Software=Midjourney",
         fn=case_declared_ai_exif, expect={"max_tier": "提示级", "must_fire": "R-AI-LABEL-DECLARED"}),

    # ---------------- 良性：真实叠加工况与平台链路（v1 扩充 → 良性样本 21 → 40） ----------------
    # 目的：把「良性样本的多样性来自我们自己的想象」这个结构性问题，用**真实发布链路**
    # 顶上去；同时让「误报率<10%」的统计承诺（需良性 n≥29）真正成立。
    dict(id="b22", label="benign", cat="叠加工况", desc="磨皮（中度）→ 1080 q75",
         fn=case_stack_beauty_medium_q75, expect={"max_tier": "关注级"}),
    dict(id="b23", label="benign", cat="叠加工况", desc="重磨皮 → 1080 q60",
         fn=case_stack_beauty_heavy_q60, expect={"max_tier": "关注级"}),
    dict(id="b24", label="benign", cat="叠加工况", desc="暖色滤镜 → 1080 q75",
         fn=case_stack_warm_q75, expect={"max_tier": "关注级"}),
    dict(id="b25", label="benign", cat="叠加工况", desc="提亮 +12% → 裁 80% 回原尺寸",
         fn=case_stack_brighten_crop, expect={"max_tier": "关注级"}),
    dict(id="b26", label="benign", cat="叠加工况", desc="对比度 +18% → 旋转 1.5°",
         fn=case_stack_contrast_rotate, expect={"max_tier": "关注级"}),
    dict(id="b27", label="benign", cat="叠加工况", desc="高 ISO σ=9 → 1080 q60（暗光实拍转存）",
         fn=case_stack_iso_q60, expect={"max_tier": "关注级"}),
    dict(id="b28", label="benign", cat="叠加工况", desc="重模糊 → 提亮 +12%",
         fn=case_stack_blur_brighten, expect={"max_tier": "关注级"}),
    dict(id="b29", label="benign", cat="叠加工况", desc="局部遮瑕 → 1080 q75（GT 坐标同步换算）",
         fn=case_stack_concealer_q75, expect={"max_tier": "关注级"}),
    dict(id="b30", label="benign", cat="叠加工况", desc="水印条 → 1080 q60",
         fn=case_stack_watermark_q60, expect={"max_tier": "关注级"}),
    dict(id="b31", label="benign", cat="平台链路", desc="短边 720 + q60（聊天工具转发画质）",
         fn=case_small_720_q60, expect={"max_tier": "关注级"}),
    dict(id="b32", label="benign", cat="平台链路", desc="同质量压缩两次（反复转存）",
         fn=case_double_compress, expect={"max_tier": "关注级"}),
    dict(id="b33", label="benign", cat="平台链路", desc="JPEG→WebP→JPEG（平台转码）",
         fn=case_webp_roundtrip, expect={"max_tier": "关注级"}),
    dict(id="b34", label="benign", cat="平台链路", desc="降分辨率后存 PNG（无 EXIF）",
         fn=case_png_downscale, expect={"max_tier": "关注级", "must_benign": "NO_JPEG_HISTORY"}),
    dict(id="b35", label="benign", cat="合法拼版", desc="四宫格：同图裁四块拼回（几何拼版但语义合法）",
         fn=case_grid_2x2, expect={"max_tier": "关注级",
                                   "note": "护栏关键压力点：内容真实、几何上确为拼版。"}),
    dict(id="b36", label="benign", cat="合法拼版", desc="上下「使用前后」竖版",
         fn=case_before_after_vertical, expect={"max_tier": "关注级"}),
    dict(id="b37", label="benign", cat="合法排版", desc="白边留白（社交平台防裁）",
         fn=case_border_pad, expect={"max_tier": "关注级"}),
    dict(id="b38", label="benign", cat="全局滤镜", desc="色温校正（偏冷）",
         fn=case_cool_correct, expect={"max_tier": "关注级"}),
    dict(id="b39", label="benign", cat="叠加工况", desc="旋转 → 裁剪 → 1080 q75（三步叠加）",
         fn=case_rotate_crop_q75, expect={"max_tier": "关注级"}),
    dict(id="b40", label="benign", cat="构图裁切", desc="竖版 9:16 裁切（不留黑边）",
         fn=case_portrait_9_16, expect={"max_tier": "关注级"}),

    # ---------------- 恶意：拼接（可检出） ----------------
    dict(id="m01", label="malicious", cat="跨源拼接", desc="异源噪声 σ=4 小块拼接（检出边界）",
         fn=lambda a, rng: (a, {"splice": dict(sigma=4.0, seed=1)}),
         expect={"min_tier": "关注级", "borderline": True,
                 "note": "σ=4 处于检出边界：是否检出取决于随机种子与存盘质量（扫描表用 q95/另一组种子时检出 z≈9.7；"
                         "本集 q93/seed=1 则未检出）。登记为边界样本，不计入承诺 —— 不用调阈值来迎合它。"}),
    dict(id="m02", label="malicious", cat="跨源拼接", desc="异源噪声 σ=8 小块拼接",
         fn=lambda a, rng: (a, {"splice": dict(sigma=8.0, seed=2)}), expect={"min_tier": "关注级"}),
    dict(id="m03", label="malicious", cat="跨源拼接", desc="异源噪声 σ=16 小块拼接",
         fn=lambda a, rng: (a, {"splice": dict(sigma=16.0, seed=3)}), expect={"min_tier": "关注级"}),
    # ---------------- 恶意：已知盲区（漏判是预期，必须写下来） ----------------
    dict(id="m04", label="malicious", cat="同源提亮", desc="拼块只提亮不改噪声场",
         fn=lambda a, rng: (a, {"splice": dict(sigma=0.0, brighten=1.35, seed=4)}),
         expect={"blind_spot": "ELA 对同源提亮无感（下界扫描：gain 1.0→2.0 恒不检出）",
                 "known_undetectable": True}),
    dict(id="m05", label="malicious", cat="同图复制", desc="同图 copy-move（ELA 原理上看不见）",
         fn=case_copy_move, expect={"blind_spot": "copy-move 压缩史与周围一致 → ELA 无差异",
                                    "known_undetectable": True}),
    dict(id="m06", label="malicious", cat="克隆遮盖", desc="用邻近区域克隆盖掉一块内容",
         fn=case_clone_cover, expect={"blind_spot": "同源克隆 → ELA 无差异", "known_undetectable": True}),
    dict(id="m07", label="malicious", cat="功效益假", desc="拼接 + 功效对比语境（需要 F4 文本层）",
         fn=lambda a, rng: (a, {"splice": dict(sigma=12.0, seed=7)}),
         expect={"min_tier": "关注级", "needs_layer": "F4 图文一致性层（text.efficacy_claim）→ 当前只能到关注级"}),
    dict(id="m08", label="malicious", cat="抹除标识", desc="SD 参数 PNG → 剥掉元数据存 JPEG（第十条）",
         fn=lambda a, rng: (a, {"strip_ai": True}),
         expect={"needs_layer": "P1 像素级生成痕迹检测器（ai_trace_score）→ 当前必须弃权，不许判高风险"}),
]


def _build_manifest() -> list[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    base = load_base()
    rows: list[dict] = []

    for c in CASES:
        rng = np.random.default_rng(SEED)
        arr, meta = c["fn"](base, rng)
        ext = meta.get("ext", ".jpg")
        path = OUT / f"{c['id']}_{c['cat']}{ext}"

        if meta.get("splice"):
            # 与演示样本共用同一实现（DRY）
            sp = meta["splice"]
            gt = build_splice(path, gt_path=None, src=BASE, quality=93, **sp)
            bbox = gt["bbox_xywh"]
            op = gt["operation"]
        elif meta.get("strip_ai"):
            # 先造一张"带过 AI 标识"的图，再把元数据剥离 —— 模拟第十条形态
            tmp = OUT / "_tmp_ai.png"
            save(arr, tmp, png_text={"parameters": "a portrait, Steps: 30, "
                                                  "Model: sdxl_base_1.0, Version: v1.9.4"})
            strip = np.asarray(Image.open(tmp).convert("RGB")).copy()
            tmp.unlink(missing_ok=True)
            save(strip, path, quality=93)                 # 只写像素，不带任何元数据
            bbox, op = None, "先给 AI 图加标识 → 剥离元数据 → 存 JPEG"
        else:
            save(arr, path, quality=meta.get("quality", 93), exif=meta.get("exif"),
                 png_text=meta.get("png_text"))
            bbox, op = meta.get("bbox_xywh"), c["desc"]

        rows.append({
            "id": c["id"], "label": c["label"], "category": c["cat"], "desc": c["desc"],
            "operation": op, "file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size, "gt_bbox_xywh": bbox, "expect": c["expect"],
        })

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({
        "name": "LorealGuard 误报压力测试集", "version": "v0",
        "generated": "2026-09-19", "seed": SEED, "base": BASE.name,
        "count": len(rows),
        "counts": {"benign": sum(1 for r in rows if r["label"] == "benign"),
                   "malicious": sum(1 for r in rows if r["label"] == "malicious")},
        "how_to_reproduce": "env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p2_suite.py"
                            "  → 比对每条的 sha256",
        "privacy": "全部程序合成，不含任何个人数据。",
        "items": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def main() -> int:
    rows = _build_manifest()
    for r in rows:
        print(f"{r['id']}  {r['label']:9} {r['category']:8} {r['bytes']//1024:>5} KB  "
              f"{r['sha256'][:12]}  {r['file']}")
    print(f"\n{len(rows)} 条 → {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())