"""造一张「拼接样本」用于端到端演示与验收。

做法：在中性合成底图（base_neutral.jpg，程序生成、无个人数据）上，
把一块矩形**提亮并叠加噪声**后贴回，以与底图**相同**的 JPEG 质量存盘。

⚠️ 为什么必须叠加噪声（实测出来的，不是想当然）：
    experiments/_diag_splice_sensitivity.py 扫过 gain×噪声 全表，结论是——
      · **纯提亮**：gain 从 1.00 到 2.00 **全部检不出**。同源像素提亮不改变噪声场，
        ELA 看不到压缩历史的断裂。
      · **纹理/噪声差异 σ≥4**：稳定检出，z≈9.5~10.7、IoU 0.85~0.89。
    这也正是真实跨图拼接的物理本质：贴进来的那块来自**另一台设备/另一张图**，
    它带来的是「不匹配的噪声场」，而不只是「亮度不同」。
    （口径提示：P0 在**真实相机照片**上测到 z=+42.3 / IoU 0.79；本样本在**合成底图**上，
      两者口径不同，不可直接比较。）

同时落一份 ground truth（真实粘贴坐标），供验收时算定位精度：
    只有知道「真在哪」，才谈得上「IoU 多少」，否则就是自说自话。

用法：  py.bat samples/make_splice_sample.py
产物：  samples/fw_splice.jpg  +  samples/fw_splice_gt.json
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
SRC = HERE / "base_neutral.jpg"
OUT = HERE / "fw_splice.jpg"
GT = HERE / "fw_splice_gt.json"

BRIGHTEN = 1.18           # 亮度增益（弱 —— 因为有效信号不是亮度）
NOISE_SIGMA = 8.0         # 叠加噪声 σ（有效信号：来自异源的不匹配噪声场）
SEED = 20260919           # 固定种子 = 可复现
QUALITY = 95              # 与底图一致，避免压缩质量差成为线索

#: 粘贴窗（相对坐标 x0, y0, w, h）
BOX = (0.55, 0.30, 0.30, 0.28)


def build_splice(dst: str | Path = OUT, gt_path: str | Path | None = GT,
                 src: str | Path = SRC, sigma: float = NOISE_SIGMA,
                 brighten: float = BRIGHTEN, box: tuple = BOX,
                 seed: int = SEED, quality: int = QUALITY) -> dict:
    """构造跨源拼接样本，返回 ground truth dict（含真实粘贴框 xywh）。

    测试、演示、P2 压力测试集共用**同一份实现**（参数化）—— 免得"演示用一张、
    测试用另一张"，导致验收数字对不上真实样本。
    """
    src, dst = Path(src), Path(dst)
    if not src.exists():
        raise FileNotFoundError(f"缺少底图：{src}（先跑 samples/make_neutral_base.py）")

    a = np.asarray(Image.open(src).convert("RGB")).copy()
    h, w = a.shape[:2]
    x0, y0 = int(w * box[0]), int(h * box[1])
    bw, bh = int(w * box[2]), int(h * box[3])

    patch = a[y0:y0 + bh, x0:x0 + bw].astype(np.float32) * brighten
    rng = np.random.default_rng(seed)
    if sigma > 0:
        patch = patch + rng.normal(0, sigma, patch.shape)
    a[y0:y0 + bh, x0:x0 + bw] = np.clip(patch, 0, 255).astype(np.uint8)

    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(a).save(dst, "JPEG", quality=quality)

    gt = {
        "image": dst.name, "src": src.name, "shape": [h, w],
        "bbox_xywh": [x0, y0, bw, bh],
        "operation": f"亮度 x{brighten} + 异源噪声 σ={sigma}（seed={seed}）",
        "note": "程序合成的跨源拼接，用于验收 ELA 的定位能力；不含任何个人数据。"
                "有效信号是噪声场不匹配，不是亮度差（见 experiments/_diag_splice_sensitivity.py）。",
    }
    if gt_path:
        Path(gt_path).write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")
    return gt


def main() -> int:
    gt = build_splice()
    print(f"OK  {OUT}  (真实粘贴框 xywh={gt['bbox_xywh']})")
    print(f"OK  {GT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())