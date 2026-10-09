# -*- coding: utf-8 -*-
"""② 逐检测器隔离：**一个检测器抛异常，不许拖垮整轮**。

**口径（写于测量之前）**

背景：层 1 的检测器还会继续加（noise / double_jpeg / lighting / color_edge / texture，
见 `voiceguard/forensics/registry.py:64`）。加检测器的前置条件是：**任何一个检测器在这张图上
吃不下，只能让它自己变成「不适用（原因：…）」，其余检测器照跑、判定照出**。

判据（四条，任一不成立即红）：
  A. 注册一个必然抛异常的假检测器 → 跑一张正常图，**整轮不许崩**；
  B. 该检测器必须**显式登记**为「不适用（原因：异常：<一行>）」—— 报告 `detectors` 里
     有 status=unavailable + reason，`layers` 的 F1 状态明说"N 个不适用"；
     **绝不许静默缺席**（三态：ok / 不适用+原因 / 无）；
  C. 假检测器**不许影响真检测器的结论**：把假的那条从报告里摘掉之后，**判定 payload**
     与"不注册假检测器"那一次**逐字节一致**（含 verdict.confidence —— 若"不适用"的 low
     混进 confidence 兜底，这条会红）。`layers` 一栏**必须**变（它的职责就是登记谁没跑成），
     所以单独比：只许多出"1 个不适用"与假检测器自己那条 brief，其余层一字节不许动；
  D. **未注册的检测器名仍然抛 KeyError**（那是调用方写错了名字＝程序员错误，不许被吞成
     "不适用" —— 吞掉它等于静默少跑一层物证）。

**自证伪**：临时把 `voiceguard/forensics/registry.py` 里那圈 try/except 去掉，A/B/C 立刻红
（回执 §3.3① 有原始输出）。D 与"status 只在非默认时输出"两条钉住另外两个方向：
前者防吞错，后者防默认路径多出 JSON 键（那会让三条冻结样张的判定 sha256 改变）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voiceguard import config
from voiceguard.forensics import load_and_prepare, registry, run_all
from voiceguard.guard import analyze_image, build_signals

SAMPLE = Path(config.SAMPLES_DIR) / "base_neutral.jpg"

#: 假检测器的名字：故意取一个不可能与真检测器重名的前缀。
FAKE = "zz_probe_boom"

VOLATILE = ("generated_at", "evidence_files", "content_id", "path", "image_path", "duration", "elapsed")


def _boom(img, cfg=None, meta=None):
    raise RuntimeError("本检测器在这张图上不适用（故意抛异常）")


class _fake:
    """临时注册假检测器（用完必摘，registry 是全局状态）。"""

    def __enter__(self):
        registry.register(_boom, FAKE)
        return FAKE

    def __exit__(self, *exc):
        registry.unregister(FAKE)
        return False


def _norm(o, *dirs):
    """与 `D:/deliver/compete/_freeze_judgment.py` 同款归一化（剥离易变键 + 路径）。"""
    if isinstance(o, dict):
        return {k: _norm(v, *dirs) for k, v in sorted(o.items())
                if not any(s in k.lower() for s in VOLATILE)}
    if isinstance(o, list):
        return [_norm(x, *dirs) for x in o]
    if isinstance(o, str):
        for d in dirs:
            o = o.replace(str(d), "<DIR>")
        return o.replace("\\", "/")
    return o


def _judge(tmp_path, tag: str, name: str) -> tuple[dict, list]:
    """跑一次完整链路，读回判定 JSON（检测器清单摘掉假检测器）。

    返回 (判定 payload, layers)。**layers 单独拿出去比**：那一栏的职责就是如实登记
    "这轮谁没跑成"，所以它**必须**随不适用的检测器变化（这正是三态可见）；
    而 judgments（verdict / evidence / benign / provenance / 各检测器数字）不许变。
    """
    out = tmp_path / name
    analyze_image(SAMPLE, outdir=out, tag=tag, sheet=False)
    d = json.loads((out / f"{tag}_report.json").read_text(encoding="utf-8"))
    d["detectors"] = [x for x in d["detectors"] if x["name"] != FAKE]
    return d, d.pop("layers")


# ---------------------------------------------------------------- 判据 A / B

def test_a_raising_detector_does_not_kill_the_round(tmp_path):
    """A：假检测器一抛，整轮必须照跑完（修前：RuntimeError 直接穿透整条链路）。"""
    with _fake():
        rep = analyze_image(SAMPLE, outdir=tmp_path / "a", tag="iso", sheet=False)
    assert rep is not None
    assert rep.fingerprint, "报告没落完（指纹为空）"


def test_b_unavailable_is_registered_with_a_one_line_reason(tmp_path):
    """B：必须显式登记为「不适用（原因：异常：…）」，不许静默缺席。"""
    with _fake():
        rep = analyze_image(SAMPLE, outdir=tmp_path / "b", tag="iso", sheet=False)

    entries = {d["name"]: d for d in rep.detectors}
    assert FAKE in entries, "抛异常的检测器在 detectors 里整个消失了 —— 静默缺席"
    e = entries[FAKE]
    assert e["status"] == "unavailable"
    assert e["reason"].startswith("异常："), e["reason"]
    assert "\n" not in e["reason"], "原因必须是一行"
    assert e["notes"] and e["notes"][0] == f"不适用（原因：{e['reason']}）"

    f1 = next(l for l in rep.layers if l["layer"].startswith("F1"))
    assert "1 个不适用" in f1["status"], f1["status"]
    assert "不适用（原因：异常：" in f1["notes"], f1["notes"]
    # 真检测器照跑：ELA 仍在清单里，且是 ok（ok 不带 status 键 —— 见 B2）
    assert "status" not in entries["ela"]
    assert entries["ela"]["confidence"] == "high"


def test_b2_no_status_key_on_the_default_path(tmp_path):
    """B2：status/reason **只在非默认时**输出 —— 默认路径多一个键＝冻结样张的 sha256 会变。"""
    with _fake():
        rep = analyze_image(SAMPLE, outdir=tmp_path / "b2", tag="iso", sheet=False)
    entries = {d["name"]: d for d in rep.detectors}
    assert "status" not in entries["ela"], "ok 的检测器也带了 status 键 → 冻结口径被破坏"
    assert "reason" not in entries["ela"]


# ---------------------------------------------------------------- 判据 C

def test_c_broken_detector_does_not_change_the_real_conclusions(tmp_path):
    """C：摘掉假检测器后，判定 payload 与"没注册假检测器"那次完全一致（layers 除外）。"""
    clean, layers_clean = _judge(tmp_path, "iso", "c_clean")
    with _fake():
        dirty, layers_dirty = _judge(tmp_path, "iso", "c_dirty")
    assert len(dirty["detectors"]) == len(clean["detectors"]), "真检测器数量变了"

    a = json.dumps(_norm(clean, tmp_path), ensure_ascii=False, sort_keys=True, indent=1)
    b = json.dumps(_norm(dirty, tmp_path), ensure_ascii=False, sort_keys=True, indent=1)
    if a != b:
        import difflib
        pytest.fail("假检测器改变了真结论：\n" + "\n".join(difflib.unified_diff(
            a.splitlines(), b.splitlines(), "没假检测器", "有假检测器", lineterm="", n=1)))

    # 明写一条：confidence 兜底不许被"不适用"的 low 拉低
    assert clean["verdict"]["confidence"] != "low" and dirty["verdict"]["confidence"] != "low"

    # layers 只许在"多登记了一个不适用者"这一个方向上不同
    f1c = next(l for l in layers_clean if l["layer"].startswith("F1"))
    f1d = next(l for l in layers_dirty if l["layer"].startswith("F1"))
    assert f1c["status"] == "已跑（1 个检测器）"
    assert f1d["status"] == "已跑（1 个检测器；1 个不适用）"
    assert f1d["notes"].startswith(f1c["notes"]), "真检测器的 brief 被改动了"
    assert "zz_probe_boom" in f1d["notes"][len(f1c["notes"]):]
    others_c = [l for l in layers_clean if not l["layer"].startswith("F1")]
    others_d = [l for l in layers_dirty if not l["layer"].startswith("F1")]
    assert json.dumps(others_c, ensure_ascii=False, sort_keys=True) == \
        json.dumps(others_d, ensure_ascii=False, sort_keys=True), "其余层被不适用者影响了"


def test_c2_unavailable_detector_contributes_no_signal(tmp_path):
    """C2：不适用于的检测器在信号表里**一个键都不留**（不留 score=0 让人误读成"没异常"）。"""
    img, meta = load_and_prepare(SAMPLE)
    sig_clean = build_signals(run_all(img, None, meta), meta)
    with _fake():
        sig_dirty = build_signals(run_all(img, None, meta), meta)
    assert not [k for k in sig_dirty if "zz_probe_boom" in k], "不适用者仍在信号表里留了键"
    assert sig_dirty == sig_clean, "信号表被不适用者改动了"
    assert sig_clean["forensics.ela.score"] == sig_dirty["forensics.ela.score"]


# ---------------------------------------------------------------- 判据 D

def test_d_unknown_detector_name_still_raises_keyerror():
    """D：名字没注册 ≠ 跑的时候抛异常。前者是程序员错误，保持 KeyError。"""
    img, meta = load_and_prepare(SAMPLE)
    with pytest.raises(KeyError) as ei:
        run_all(img, only=["no_such_detector"])
    assert "未注册的检测器" in str(ei.value)


def test_d2_registry_untouched_by_the_probe():
    """收尾：假检测器用完必须摘干净（否则会污染同进程的其它测试）。"""
    assert FAKE not in registry.names()
    assert "ela" in registry.names()
