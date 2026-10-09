"""输入鲁棒性（round5）：畸形输入只许出「报告」或「友好中文错误」，不许出后端崩溃。

三条症状（题面 round5_hermes_to_cc.md §1）：
  A 窄高图 → 证据图面板只按宽度归一（无高度上限）→ 峰值 2.3GB / 9.6s / 71MB PNG；
  B 极小图（短边 < block）→ ela_block_score 切出的窗口比 reshape 小 → ValueError，无报告；
  C 0 字节文件 → cv2.imdecode 抛 cv2.error，绕过已有的中文 ValueError。

为什么这三条值得单开一份测试：凡"判定链"以外的崩溃，都会在**判定发生之前**把人劝退
（图没判、报错先出）。误报护栏是第一优先级，而"后端异常直接甩给创作者"是比误报更早
发生的一类伤害。所以本文件同时钉住 ④：修 A/B 不许动判决 —— 8×8 与常规样本的
ELA 判定字段必须与修前逐字相同（任何尺寸 ≥ block 的图都不受影响，这是硬约束）。

样本一律现造（确定性噪声，合成、无个人数据），不依赖 D:/deliver/compete/_stress_imgs。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard.forensics.base import (                                   # noqa: E402
    _fit_panel, load_and_prepare, make_evidence_sheet)
from voiceguard.forensics.ela import ela_block_score, ela_detector        # noqa: E402
from voiceguard.guard import analyze_image                                # noqa: E402

SAMPLES = ROOT / "samples"
BASE = SAMPLES / "base_neutral.jpg"

#: ④ 冻结点：round5 改动**之前**实测的 ELA 判定字段（as_dict + 稳定哈希）。
#: 常规样本 = samples/base_neutral.jpg（1200×1600，短边 ≥600 → block=16）。
FP_BASE_NEUTRAL = "18e21d2cd8c07bfa0b2d2970b2ec278bcfb12f0432891033d250df6d6fd190ea"
FP_NOISE_8X8 = "53daad4c42ad1d8830da1aa863f89bf9f10e92f05571d4275413acdf97b92321"


def _noise_png(dirpath: Path, w: int, h: int, name: str, seed: int = 1234) -> Path:
    """确定性噪声样图（合成）。噪声保证 ELA 有残差可看，不至于全平。"""
    img = np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)
    p = dirpath / name
    cv2.imencode(".png", img)[1].tofile(str(p))          # 中文路径安全
    return p


def _fp(d: dict) -> str:
    """判定字段的稳定哈希（与 _freeze_judgment.py 同口径：sort_keys + ensure_ascii=False）。"""
    return hashlib.sha256(json.dumps(d, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _ela_of(report_dict: dict) -> dict:
    return next(d for d in report_dict["detectors"] if d["name"] == "ela")


# ===================================================================== ① 极小图
# 症状（修前）：rc=1，ValueError: cannot reshape array of size 1 into shape (1,8,1,8)，无报告。


@pytest.mark.parametrize("side", [1, 2, 4])
def test_极小图不崩且产出结构化报告(tmp_path, side):
    """1×1 / 2×2 / 4×4（短边 < block=8）必须走完全流程、落一份报告，并声明 ELA 不适用。"""
    p = _noise_png(tmp_path, side, side, f"tiny_{side}x{side}.png")
    out = tmp_path / f"out{side}"
    rep = analyze_image(p, outdir=out, tag=f"tiny{side}")

    js = json.loads((out / f"tiny{side}_report.json").read_text(encoding="utf-8"))
    ela = _ela_of(js)

    # 不崩 + 报告结构完整（"产出报告"不是"没报错"）
    assert "verdict" in js and "detectors" in js and "layers" in js
    assert rep.fingerprint, "物证锚不能为空"
    # ELA 必须自报"这次不适用"，而不是沉默地给个 0 分当"无异常"
    assert ela["confidence"] == "low"
    assert ela["raw"].get("too_small") is True
    assert any("太小" in n for n in ela["notes"]), ela["notes"]
    assert any("不适用" in n for n in ela["notes"]), ela["notes"]
    # 证据图也要落得下来（极小图不该只有报告没有物证）
    assert any(Path(f).exists() for f in rep.evidence_files)


def test_修_极小图护栏可证伪_不缩块就崩(monkeypatch):
    """证明上面的护栏是承重的：把 effective_block 还原成"原样返回"，4×4 立刻重演 reshape 崩溃。

    同时记下调用参数 —— 否则哪天 ela_block_score 干脆**不再调用**缩块（函数还在、调用点没了），
    这条会照样绿着过去：它证的只是"block=8 切 4×4 会崩"这个 numpy 常识，不是我们的护栏。
    """
    import voiceguard.forensics.ela as ela

    seen: list = []
    monkeypatch.setattr(ela, "effective_block", lambda b, h, w: (seen.append((b, h, w)), b)[1])
    with pytest.raises(ValueError, match="reshape"):
        ela.ela_block_score(np.zeros((4, 4), np.float32), block=8)
    assert seen == [(8, 4, 4)], "ela_block_score 必须真的拿 (block, H, W) 去问过缩块"


# ===================================================================== ② 坏文件
# 症状（修前）：0 字节 → cv2.error（!buf.empty()）；文本改名 / 截断 → 已是友好 ValueError。


@pytest.mark.parametrize("bad,desc", [
    ("empty", "0 字节文件"),
    ("text", "文本改名成 .jpg"),
    ("truncated", "截断的 JPEG"),
])
def test_坏文件给友好中文错误而不是_cv2_异常(tmp_path, bad, desc):
    p = tmp_path / f"{bad}.jpg"
    if bad == "empty":
        p.write_bytes(b"")
    elif bad == "text":
        p.write_bytes("这不是图片，只是一段文字。".encode("utf-8"))
    else:
        p.write_bytes(BASE.read_bytes()[:1024])         # 只有文件头

    with pytest.raises(ValueError, match="读不到图像") as ei:
        load_and_prepare(p)
    # 必须**恰好**是 ValueError —— cv2.error 不是它的子类，一旦回归这里就会红
    assert type(ei.value) is ValueError, f"{desc} 抛出了 {type(ei.value).__name__}"
    assert not isinstance(ei.value, cv2.error), f"{desc} 又漏成了 cv2.error"


def test_坏文件在整条链路上也是友好的(tmp_path):
    """CLI/页面用的是 analyze_image —— 同一份坏文件在这里也必须给同一条中文错误。"""
    p = tmp_path / "empty.jpg"
    p.write_bytes(b"")
    with pytest.raises(ValueError, match="读不到图像"):
        analyze_image(p, outdir=tmp_path / "out", tag="empty")


def test_修_坏文件护栏可证伪_空缓冲确实会被cv2拒():
    """证明"友好 ValueError"不是白拿的：同样的空缓冲直喂 cv2 就是 cv2.error。

    哪天 OpenCV 改成返回 None，这条会红 —— 也就提醒我们那个 try/except 已不再承重。
    """
    assert not issubclass(cv2.error, ValueError)
    with pytest.raises(cv2.error):
        cv2.imdecode(np.array([], np.uint8), cv2.IMREAD_COLOR)


# ===================================================================== ③ 窄高图
# 症状（修前）：9×400 的面板被按宽归一到 900 → 单面板 900×40000px → 2.4GB / 71MB PNG。


def test_面板尺寸双向限幅():
    """宽 ≤900 且 高 ≤1400，且常规样本的缩放结果与旧的"只按宽归一"逐像素同尺寸。"""
    assert _fit_panel(np.zeros((400, 9, 3), np.uint8)).shape[:2] == (1400, 31)     # 窄高：高度顶上限
    assert _fit_panel(np.zeros((9, 400, 3), np.uint8)).shape[:2] == (20, 900)      # 扁宽：宽度顶上限
    assert _fit_panel(np.zeros((1200, 1600, 3), np.uint8)).shape[:2] == (675, 900)  # 常规：不变


def test_修_窄高图护栏可证伪_只按宽归一必然爆高():
    """证明高度上限是承重的：旧的"按宽归一"口径会把 9×400 拉成 900×40000（内存爆炸的直接原因）。"""
    h, w = 400, 9
    assert int(h * 900 / w) == 40000, "旧口径（宽度归一）算出的面板高度"
    assert _fit_panel(np.zeros((h, w, 3), np.uint8)).shape[0] == 1400, "新口径必须被高度上限截住"


def test_窄高图证据图不超限(tmp_path):
    """9×400 的证据图：高度不许超过 标题58 + 面板1400 + 说明62，文件 < 5MB（别在测试里跑真内存）。"""
    p = _noise_png(tmp_path, 9, 400, "thin_9x400.png")
    img, meta = load_and_prepare(p)
    out = tmp_path / "sheet.png"
    make_evidence_sheet(img, ela_detector(img, meta=meta), out)

    sheet = cv2.imdecode(np.fromfile(str(out), np.uint8), cv2.IMREAD_COLOR)
    assert sheet.shape[0] <= 58 + 1400 + 62, f"证据图高 {sheet.shape[0]}px 超限"
    assert sheet.shape[1] <= 4 * 900 + 3 * 10, f"证据图宽 {sheet.shape[1]}px 超限"
    assert out.stat().st_size < 5_000_000, f"证据图 {out.stat().st_size / 1e6:.1f}MB 超限"


def test_窄高图转90度也不反向超限(tmp_path):
    """400×9（扁宽）：宽度顶到 900，高度按比例 —— 与窄高图对称地被限幅。"""
    p = _noise_png(tmp_path, 400, 9, "thin_400x9.png")
    img, meta = load_and_prepare(p)
    out = tmp_path / "sheet.png"
    make_evidence_sheet(img, ela_detector(img, meta=meta), out)

    sheet = cv2.imdecode(np.fromfile(str(out), np.uint8), cv2.IMREAD_COLOR)
    assert sheet.shape[1] <= 4 * 900 + 3 * 10
    assert sheet.shape[0] <= 58 + 1400 + 62
    assert out.stat().st_size < 5_000_000


# ===================================================================== ④ 回归护栏
# 硬约束：任何尺寸 ≥ block 的图，块尺寸 / 分数 / z / regions / notes 必须逐字不变。


def test_回归_极小边界8x8的判定字段逐字不变(tmp_path):
    """8×8 正好等于降块后的 block 边界 —— 它属于"尺寸 ≥ block"，一个字都不许变。"""
    p = _noise_png(tmp_path, 8, 8, "tiny_8x8.png")
    img, meta = load_and_prepare(p)
    d = ela_detector(img, meta=meta)
    assert _fp(d.as_dict()) == FP_NOISE_8X8
    # 顺带把关键字段写死，免得哈希一变只看到"哈希不同"却不知哪一项漂了
    assert d.raw["block"] == 8
    assert d.raw["median"] == 70.984375
    assert d.raw["sigma_hat"] == 0.0
    assert d.confidence == "low"
    assert d.regions == []
    assert "too_small" not in d.raw, "8×8 不属于『太小』，不该多出这个字段"


def test_回归_常规样本的判定字段逐字不变():
    """samples/base_neutral.jpg（1200×1600）：block=16，判定字段与修前一致。"""
    img, meta = load_and_prepare(BASE)
    d = ela_detector(img, meta=meta)
    assert _fp(d.as_dict()) == FP_BASE_NEUTRAL
    assert d.raw["block"] == 16
    assert d.score == 0.0
    assert d.confidence == "high"
    assert d.regions == []
    assert "too_small" not in d.raw


def test_回归护栏可证伪_块尺寸被改必红(monkeypatch):
    """上面两条"逐字不变"是不是不会红的检查？把块尺寸搅乱，它们必须红。"""
    import voiceguard.forensics.ela as ela

    monkeypatch.setattr(ela, "effective_block", lambda b, h, w: 1)
    img, meta = load_and_prepare(BASE)
    assert _fp(ela.ela_detector(img, meta=meta).as_dict()) != FP_BASE_NEUTRAL
