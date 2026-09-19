"""pytest 夹具：统一提供"中性样图"与几类受控篡改样本。

样本一律来自 samples/base_neutral.jpg（程序合成，无个人数据）。
所有样本落盘为真实 JPEG —— ELA 必须对**有压缩史的文件**工作，
对内存数组直接算会得到完全不同的数字（P0 踩过：内存 2.41 / 读回 1.06）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard import config                                                  # noqa: E402
from voiceguard.forensics import load_and_prepare                              # noqa: E402

BASE = Path(config.SAMPLES_DIR) / "base_neutral.jpg"
PASTE = (928, 360, 320, 240)          # x, y, w, h
COPY_MOVE = (300, 300, 320, 240)


def _write(img: np.ndarray, p: Path, q: int = 95) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tofile(str(p))
    return p


@pytest.fixture(scope="session")
def base_img() -> np.ndarray:
    if not BASE.exists():
        pytest.skip(f"缺少中性样图 {BASE}（先跑 samples/make_neutral_base.py）")
    img, _ = load_and_prepare(BASE)
    return img


@pytest.fixture(scope="session")
def env_meta() -> dict:
    _, meta = load_and_prepare(BASE)
    return meta


@pytest.fixture
def splice(tmp_path: Path, base_img: np.ndarray):
    """外源像素拼接：生成式图块（无 JPEG 压缩史）贴进底图 → ELA 应报偏亮。"""
    rng = np.random.default_rng(7)
    gx, gy = np.meshgrid(np.linspace(0, 1, PASTE[2]), np.linspace(0, 1, PASTE[3]))
    patch = np.stack([gx * 180 + 40, gy * 150 + 60, (1 - gx) * 120 + 90], -1)
    patch = np.clip(patch + rng.normal(0, 6, patch.shape), 0, 255).astype(np.uint8)
    img = base_img.copy()
    x, y, w, h = PASTE
    img[y : y + h, x : x + w] = patch
    p = _write(img, tmp_path / "splice.jpg")
    return load_and_prepare(p) + (PASTE,)


@pytest.fixture
def copy_move(tmp_path: Path, base_img: np.ndarray):
    """同图缩放裁切再贴：压缩史与周围一致 → ELA 原理上看不见（P1 copy-move 的活）。"""
    img = base_img.copy()
    x, y, w, h = COPY_MOVE
    img[y : y + h, x : x + w] = cv2.resize(base_img[700:940, 900:1220], (w, h),
                                           interpolation=cv2.INTER_LINEAR)
    p = _write(img, tmp_path / "copymove.jpg")
    return load_and_prepare(p)


def beatify(img: np.ndarray, op: str) -> np.ndarray:
    """三种"美颜磨皮"代理算子（保边 / 去块效应 / 典型美颜链）。"""
    m = np.zeros(img.shape[:2], np.uint8)
    h, w = img.shape[:2]
    cv2.ellipse(m, (w // 2, h // 2), (int(w * 0.17), int(h * 0.22)), 0, 0, 360, 255, -1, cv2.LINE_AA)
    m = cv2.GaussianBlur(m, (0, 0), 12).astype(np.float32)[:, :, None] / 255.0
    if op == "bilateral":
        sm = cv2.bilateralFilter(img, 25, 80, 80)
    elif op == "median":
        sm = cv2.medianBlur(img, 15)
    else:  # beauty
        sm = cv2.bilateralFilter(cv2.medianBlur(img, 15), 15, 60, 60)
    return (img * (1 - m) + sm * m).astype(np.uint8)