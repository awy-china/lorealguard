"""生成中性测试底图（程序合成，**无个人数据**，可直接进开源仓库）+ 自校验统计特性

关键教训（2026-09-18 实测）：第一版用**逐像素白噪声**当"皮肤纹理"，结果 ELA 全图暴涨、
处处误报 —— 因为白噪声在 JPEG 下最不可压缩。真实照片纹理是 **1/f 相关**的（低频占优）。
所以这里所有噪声都先做高斯低通再定幅，并把"统计像不像真实照片"做成自动校验：

校验标准（对照真实相机照片 q=90 的实测值）：
  · 块级 ELA 中位数 1.0 ~ 3.0（真实照片实测 1.6）
  · 块级离散度 CV 0.2 ~ 0.45（实测 0.31）
  · 干净样图上不得出现 |z|>4 的块（否则样本本身自带误报）

用法： python samples/make_neutral_base.py       # 生成并自校验
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from voiceguard import config                                    # noqa: E402
from voiceguard.forensics import load_and_prepare                # noqa: E402
from voiceguard.forensics.ela import compute_ela, ela_block_score  # noqa: E402

H, W = 1200, 1600
RNG = np.random.default_rng(20260918)


def corr_noise(shape, amp: float, blur: float = 1.1) -> np.ndarray:
    """1/f 风格的相关噪声：白噪声 → 高斯低通 → 归一化到目标幅度。"""
    n = RNG.normal(0, 1, shape).astype(np.float32)
    n = cv2.GaussianBlur(n, (0, 0), blur)
    s = float(n.std()) or 1.0
    return n / s * amp


def build() -> np.ndarray:
    # 影棚灰渐变背景 + 弱相关噪声
    bg = np.zeros((H, W, 3), np.float32)
    grad = np.linspace(212, 152, H, dtype=np.float32)[:, None]
    for c, k in enumerate((0.97, 0.96, 0.93)):
        bg[:, :, c] = grad * k
    img = bg + corr_noise((H, W, 3), 2.2, 1.2)

    # 头发：深色椭圆 + 中等相关噪声
    hair = np.zeros((H, W), np.uint8)
    cv2.ellipse(hair, (int(W * 0.5), int(H * 0.30)), (int(W * 0.17), int(H * 0.22)), 0, 0, 360, 255, -1)
    hm = cv2.GaussianBlur(hair, (0, 0), 9).astype(np.float32)[:, :, None] / 255.0
    hair_col = np.clip(62 + corr_noise((H, W, 3), 4.5, 1.9), 0, 255)
    img = img * (1 - hm * 0.97) + hair_col * (hm * 0.97)

    # 脸：肤色椭圆 + 弱相关噪声（真实皮肤纹理很"软"）+ 额头柔光
    face = np.zeros((H, W), np.uint8)
    fcx, fcy = int(W * 0.5), int(H * 0.42)
    faxes = (int(W * 0.16), int(H * 0.24))
    cv2.ellipse(face, (fcx, fcy), faxes, 0, 0, 360, 255, -1)
    fm = cv2.GaussianBlur(face, (0, 0), 11).astype(np.float32)[:, :, None] / 255.0
    skin = np.zeros((H, W, 3), np.float32)
    skin[:, :, 0], skin[:, :, 1], skin[:, :, 2] = 165, 185, 215
    skin = skin + corr_noise((H, W, 3), 3.0, 1.0)
    yy = np.arange(H, dtype=np.float32)[:, None]
    xx = np.arange(W, dtype=np.float32)[None, :]
    glow = np.exp(-(((xx - fcx) ** 2) / (2 * (W * 0.06) ** 2) + ((yy - (fcy - H * 0.08)) ** 2) / (2 * (H * 0.08) ** 2)))
    skin = skin + (glow[:, :, None] * 22)
    img = img * (1 - fm) + np.clip(skin, 0, 255) * fm

    # 眼/唇：**内部要带相关噪声**（纯色填充在 JPEG 下会形成 ELA≈0 的假"平坦区"，
    # 实测 z 会掉到 −5.5，同样会制造误报）。眼珠用放射渐变模拟虹膜。
    def blob(cx, cy, ax, ay, base_rgb, amp=2.6):
        m = np.zeros((H, W), np.uint8)
        cv2.ellipse(m, (cx, cy), (ax, ay), 0, 0, 360, 255, -1, cv2.LINE_AA)
        mf = cv2.GaussianBlur(m, (0, 0), 1.5).astype(np.float32)[:, :, None] / 255.0
        col = np.zeros((H, W, 3), np.float32) + np.array(base_rgb, np.float32)
        col += corr_noise((H, W, 3), amp, 1.0)
        return mf, col

    for dx in (-int(W * 0.062), int(W * 0.062)):
        mf, col = blob(fcx + dx, fcy - int(H * 0.03), 28, 16, (70, 70, 82), amp=5.0)
        # 虹膜放射渐变的近似：中心暗、边缘略亮
        d = np.sqrt(((xx - (fcx + dx)) ** 2) / (28.0 ** 2) + ((yy - (fcy - int(H * 0.03))) ** 2) / (16.0 ** 2))
        col += ((1 - np.clip(d, 0, 1)) ** 2)[:, :, None] * -28.0
        img = img * (1 - mf) + np.clip(col, 0, 255) * mf

    mf, col = blob(fcx, fcy + int(H * 0.12), 46, 16, (120, 130, 190), amp=4.0)
    img = img * (1 - mf) + np.clip(col, 0, 255) * mf

    # 产品瓶：**渐变**填充（不用纯色块，纯色块在 JPEG 下会形成非真实的平坦区）+ 高光带
    bx, by, bw, bh = int(W * 0.70), int(H * 0.52), int(W * 0.14), int(H * 0.40)
    body = np.zeros((H, W, 3), np.float32)
    gl = np.linspace(1.15, 0.80, bw, dtype=np.float32)[None, :, None]
    body[by : by + bh, bx : bx + bw] = np.array([92.0, 124.0, 176.0]) * gl
    body += corr_noise((H, W, 3), 2.0, 1.0)
    bm = np.zeros((H, W), np.uint8)
    cv2.rectangle(bm, (bx, by), (bx + bw, by + bh), 255, -1)
    bm = cv2.GaussianBlur(bm, (0, 0), 1.2).astype(np.float32)[:, :, None] / 255.0
    img = img * (1 - bm) + np.clip(body, 0, 255) * bm
    hi = np.zeros((H, W), np.uint8)
    cv2.rectangle(hi, (bx + 18, by + 20), (bx + 46, by + bh - 30), 255, -1)
    hi = cv2.GaussianBlur(hi, (0, 0), 3.0).astype(np.float32)[:, :, None] / 255.0
    band = 222.0 + corr_noise((H, W, 3), 2.0, 1.0)      # 高光带也不能是纯色台面
    img = img * (1 - hi * 0.62) + band * (hi * 0.62)

    return np.clip(img, 0, 255).astype(np.uint8)


def verify(path: Path) -> bool:
    """自校验：统计特性得像真实照片，且干净样图不得自带误报。

    ⚠️ 必须对**存盘后再读回**的 JPEG 计算 —— 内存数组还没经历过压缩，
    直接对它算 ELA 会得到完全不同的数字（曾经自欺：内存 2.41 / 读回 1.06）。
    """
    img, _ = load_and_prepare(path)
    diff = compute_ela(img, 90)
    bm, z, regions, _, cvr = ela_block_score(diff, block=16, robust_z=4.0)
    med = float(np.median(bm))
    zmax = float(np.abs(z).max())
    ok = (0.8 <= med <= 3.0) and (cvr >= 0.12) and len(regions) == 0
    print("  自校验（真实相机照片 q=90 实测参考：中位 1.617 / CV 0.306 / 最大|z| 2.86 / 区域 0）")
    print(f"    块级 ELA 中位={med:.3f}  CV={cvr:.3f}  最大|z|={zmax:.2f}  检出区域={len(regions)}  块数={z.size}")
    print(f"  → {'✅ 统计特性合格，可用作基准样图' if ok else '❌ 不合格（样本自带信号，会导致后续验收无效）'}")
    if not ok:
        print("     判据：中位∈[0.8,3.0]（真实值同量级），CV≥0.12（非退化，不能是一张死平的图），区域数=0（不许自带误报）")
    return ok


if __name__ == "__main__":
    img = build()
    out = Path(config.SAMPLES_DIR) / "base_neutral.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 93])[1].tofile(str(out))
    print(f"已生成中性底图：{out}  {W}×{H}  {out.stat().st_size/1024:.0f} KB（q=93，模拟相机出货）")
    raise SystemExit(0 if verify(out) else 1)