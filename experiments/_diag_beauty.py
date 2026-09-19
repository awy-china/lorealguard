"""诊断2：磨皮的 ELA 信号在什么条件下才可见？（换算子 × 换重存质量）

代码里的 lambda 写错了，这里重写成正经函数，并加两组新变量：
  算子: bilateral(保边) / median(去块效应) / median+bilateral(典型美颜)
  重存质量 q: 95 / 85 / 80 / 75
读法: 椭圆内 z 越负 = 磨皮越可见；|z|>4 才可能被检出。
"""
from __future__ import annotations
import sys
from pathlib import Path
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from voiceguard.forensics.ela import compute_ela, ela_block_score
from voiceguard.forensics import load_and_prepare

OUT = ROOT / "output" / "p0"
BASE = Path.home() / "Pictures/Camera Roll/27188d6b8dc949d8bc6138c6948ec181.jpg"
img, meta = load_and_prepare(BASE)
h, w = img.shape[:2]
cx, cy = int(w * 0.5), int(h * 0.42)
axes = (int(w * 0.16), int(h * 0.24))
ellipse = np.zeros((h, w), np.uint8)
cv2.ellipse(ellipse, (cx, cy), axes, 0, 0, 360, 255, -1)
inside = ellipse > 0
feather = cv2.GaussianBlur(ellipse, (0, 0), 21).astype(np.float32)[:, :, None] / 255.0


def beauty(im: np.ndarray, kind: str, gain: float = 0.0) -> np.ndarray:
    if kind == "bilateral":
        sm = cv2.bilateralFilter(im, 41, 120, 120)
    elif kind == "median":
        sm = cv2.medianBlur(im, 31)
    else:  # 典型美颜：先中值去块 + 再保边平滑
        sm = cv2.bilateralFilter(cv2.medianBlur(im, 15), 25, 90, 90)
    out = im.astype(np.float32) * (1 - feather) + sm.astype(np.float32) * feather
    if gain:
        out = out * (1 + gain * feather)
    return np.clip(out, 0, 255).astype(np.uint8)


def report(tag: str, im: np.ndarray, q: int):
    diff = compute_ela(im, q)
    bm, z, regions, area_ratio, cvr = ela_block_score(diff, block=16, robust_z=4.0)
    bh, bw = bm.shape
    m = cv2.resize(inside[: bh * 16, : bw * 16].astype(np.uint8), (bw, bh),
                   interpolation=cv2.INTER_AREA) > 0.5
    sigma = 1.4826 * np.median(np.abs(bm - np.median(bm)))
    print(f"  {tag:<34} q={q:<3} 内/外 ELA={bm[m].mean():5.3f}/{bm[~m].mean():5.3f} "
          f"z内均值={z[m].mean():+6.2f} z内最小={z[m].min():+6.2f} 检出区域={len(regions):<2} σ̂={sigma:.3f}")


print("=== 基线：未磨皮的原图 ===")
for q in (95, 90, 85, 80, 75):
    report("原始照片(不磨皮)", img, q)

for kind in ("bilateral", "median", "beauty"):
    print(f"\n=== 算子: {kind} ===")
    im = beauty(img, kind, gain=0.0)
    for q in (95, 90, 85, 80, 75):
        report(f"{kind}(gain=0)", im, q)
    im2 = beauty(img, kind, gain=0.08)
    for q in (90, 80):
        report(f"{kind}(gain=8%提亮)", im2, q)