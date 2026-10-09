"""端到端测试 —— 框架必须**真跑一遍**，不是"接口看起来对"。

这一组是全项目最重要的一组：
    ① 同输入 → 同指纹（可复现，这是"作品能被别人验证"的前提）；
    ② 报告三件套真的落盘；
    ③ 没跑的层必须**如实登记**（PLANNED / 默认关闭 / 不适用），不许假装已跑；
    ④ 对 ELA 不适用的输入（PNG）不许给出高风险结论（误报护栏的端到端形态）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from voiceguard import config
from voiceguard.core.contract import ContentItem, RiskTier
from voiceguard.guard import GuardPipeline, analyze_image, sha256_file

SAMPLE = Path(config.SAMPLES_DIR) / "base_neutral.jpg"


def test_sample_exists():
    assert SAMPLE.exists(), f"缺少中性合成样图：{SAMPLE}"


def test_end_to_end_on_neutral_sample(tmp_path):
    r = analyze_image(SAMPLE, outdir=tmp_path / "out", tag="e2e")
    # ① 指纹 = 输入文件的 sha256（别人拿同一份输入能复算）
    assert r.fingerprint == hashlib.sha256(SAMPLE.read_bytes()).hexdigest() == sha256_file(SAMPLE)
    # ② 判定落在合法等级上，且带理由
    assert r.verdict.tier in set(RiskTier)
    assert r.verdict.headline
    # ③ 三件套落盘
    names = [Path(f).name for f in r.evidence_files if not f.startswith("（")]
    assert any(n.endswith(".md") for n in names), f"缺 md 报告：{r.evidence_files}"
    assert any(n.endswith(".json") for n in names), f"缺 json 报告：{r.evidence_files}"
    for f in r.evidence_files:
        if f.startswith("（"):
            continue
        assert Path(f).exists() and Path(f).stat().st_size > 0, f"产物为空：{f}"
    # ④ md 里要有双向账本 + 指纹 + 边界
    md = next(Path(f).read_text(encoding="utf-8") for f in r.evidence_files if f.endswith(".md"))
    assert "可疑证据" in md and "免责" in md
    assert r.fingerprint in md
    assert "能力边界" in md


#: 层状态的合法措辞（不可解释的措辞 = 口径漂移，直接判红）
STATUS_VOCAB = ("已跑", "已接入", "默认关闭", "未接入", "不适用", "未产出", "PLANNED")
#: "这层本次没真跑 / 不适用"的措辞 —— 至少要有一层这么说，否则八层全绿反而可疑
NOT_RUNNING = ("未接入", "不适用", "未产出", "PLANNED", "默认关闭")


def assert_layers_disclose_honestly(layered: dict) -> None:
    """守卫：层状态必须可解释 + 没跑的必须如实登记（变异体见 test_semantic_explain_falsifiability）。"""
    assert len(layered) >= 8, f"F1–F8 八层都要登记，实际 {len(layered)} 层"
    bad = [k for k, v in layered.items() if not any(t in v for t in STATUS_VOCAB)]
    assert not bad, f"层状态用了不可解释的措辞：{bad}"
    not_run = [k for k, v in layered.items() if any(t in v for t in NOT_RUNNING)]
    assert not_run, "没有任何层声明未跑/不适用/默认关闭 → 八层全写『已跑』是可疑的"
    f3 = layered.get("F3 语义解释层", "")
    assert f3, "F3 必须登记"
    # F3 现在已接入但**默认关闭**：状态里必须明写开关名，不许写成"已跑"
    assert "默认关闭" in f3 and "--explain" in f3, \
        f"F3 默认关闭时必须写明开启方式，实际：{f3}"


def test_planned_layers_are_disclosed_not_faked(tmp_path):
    r = analyze_image(SAMPLE, outdir=tmp_path / "out2", tag="planned", sheet=False)
    assert_layers_disclose_honestly({l["layer"]: l["status"] for l in r.layers})


def test_determinism_same_input_same_result(tmp_path):
    a = analyze_image(SAMPLE, outdir=tmp_path / "d1", tag="x", sheet=False)
    b = analyze_image(SAMPLE, outdir=tmp_path / "d2", tag="x", sheet=False)
    assert a.fingerprint == b.fingerprint
    assert a.verdict.tier is b.verdict.tier
    assert len(a.evidence) == len(b.evidence)
    assert [e.metrics for e in a.evidence] == [e.metrics for e in b.evidence]


def test_png_without_jpeg_history_never_gets_high_risk(tmp_path):
    """ELA 对无 JPEG 史的输入不适用 → 必须登记免责归因并把置信度打到最低。"""
    p = tmp_path / "flat.png"
    Image.new("RGB", (640, 480), (110, 130, 150)).save(p)
    r = analyze_image(p, outdir=tmp_path / "out3", tag="png", sheet=False)
    codes = {b.code for b in r.benign}
    assert "NO_JPEG_HISTORY" in codes, f"应命中「无 JPEG 史」免责归因，实际：{codes}"
    assert r.verdict.tier is not RiskTier.HIGH
    assert r.verdict.confidence == "low"


def test_report_is_json_serializable_from_real_run(tmp_path):
    import json
    r = analyze_image(SAMPLE, outdir=tmp_path / "out4", tag="json", sheet=False)
    d = json.loads(json.dumps(r.as_dict(), ensure_ascii=False))
    assert d["fingerprint"] == r.fingerprint
    assert "layers" in d and "evidence" in d and "benign" in d


def test_missing_input_raises_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        GuardPipeline().run(ContentItem(image_path=str(tmp_path / "nope.jpg")))
    with pytest.raises(ValueError):
        GuardPipeline().run(ContentItem())


def test_rules_table_selfcheck_passes():
    from voiceguard.rules import describe, load_rules
    info = describe(load_rules())
    assert info["errors"] == [], f"规则表体检失败：{info['errors']}"
    assert info["n_rules_checked"] >= 12


def test_evidence_json_does_not_embed_pixel_arrays(tmp_path):
    """证据 JSON 不许内嵌像素数组。

    实测过的事故：base._plain() 曾经对 ndarray 做 tolist()，
    一个 ELA 结果 9.8 MB、一份报告 JSON 27~35 MB。这条测试就是那次事故的护栏。
    """
    import json
    r = analyze_image(SAMPLE, outdir=tmp_path / "out5", tag="slim", sheet=False)
    js = next(Path(f) for f in r.evidence_files if f.endswith(".json"))
    size = js.stat().st_size
    assert size < 300_000, f"报告 JSON {size} 字节，疑似把像素数组写进去了"
    d = json.loads(js.read_text(encoding="utf-8"))
    for det in d.get("detectors", []):
        for k, v in (det.get("raw") or {}).items():
            assert not (isinstance(v, list) and len(v) > 1000), f"raw.{k} 疑似像素数组"
            if isinstance(v, dict) and "__ndarray__" in v:
                assert set(v) >= {"__ndarray__", "dtype", "min", "max", "mean"}


def test_left_column_implies_a_risk_rule_fires(tmp_path):
    """框架级不变量：报告出现坐标级可疑证据 ⇒ 必须至少命中一条风险规则。

    否则报告自相矛盾：左栏写着「这里有异常」，判定栏写着「未触发任何风险规则」。
    真实踩到过：触发条件挂在面积加权分上，8.8% 面积的跨源拼接被稀释到 0.586，
    刚好卡在 0.6 阈值下 → 有证据却判「未触发」。这条测试锁死那个坑。
    """
    import importlib.util

    path = Path(config.SAMPLES_DIR) / "make_splice_sample.py"
    spec = importlib.util.spec_from_file_location("mk_splice", path)
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)

    img = tmp_path / "sp.jpg"
    gt = mk.build_splice(img, gt_path=None, src=SAMPLE)
    r = analyze_image(img, outdir=tmp_path / "out6", tag="sp", sheet=False)

    bright = [e for e in r.evidence if e.metrics.get("polarity") == "brighter"]
    assert bright, "跨源拼接样本未被检出 —— ELA 能力回退了"
    assert r.verdict.fired, "有坐标级证据却没命中任何风险规则：报告自相矛盾"
    assert r.verdict.tier is RiskTier.WATCH, "仅像素层证据只应到「关注级」，不得直接判高风险"

    def iou(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        x0, y0 = max(ax, bx), max(ay, by)
        x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        inter = max(0, x1 - x0) * max(0, y1 - y0)
        union = aw * ah + bw * bh - inter
        return inter / union if union else 0.0

    best = max(iou(e.bbox, gt["bbox_xywh"]) for e in bright)
    assert best >= 0.5, f"检出区域与真实粘贴框 IoU={best:.2f} 过低，定位不可用"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))