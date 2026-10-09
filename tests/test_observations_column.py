# -*- coding: utf-8 -*-
"""观察栏（B 档）—— 预注册 v2 §3 的**机制**回归，附四渲染的守卫。

**口径（写于测量之前）**

观察栏的承诺是一条**机制**，不是一句声明：

  A. **默认路径逐字节不变**：默认遍历只有 `ela`，而 `ela` 的 `raw` 里**没有**自报边界的
     `measures` 键 ⇒ `build_observations()` 恒返回 `{}` ⇒ 报告 JSON 里**连 `observations`
     键都不出现**、md 里**连附四都不出现**（`guard.py:277-278`、`contract.py:424`、
     `ledger.py:277-278`）。三条冻结样张的判定 sha256 因此不受本轮影响。
  B. **开了也改不动判定**：显式点名 `lighting`/`color_edge` 之后，`evidence` / `benign` /
     `verdict` / `provenance` / `comment_verdict` **必须与默认路径逐字节相同**
     —— 这是"B 档不是物证、不进 tier"的**唯一硬证据**，不是靠 `rules.yaml` 里没写就默认成立。
  C. **附四只在该出现时出现**：非空时给出官方点名的两个维度（`loreal-topic2-details.md:48`
     光影/阴影一致性、`:50` 色彩过渡）**＋ 自建的 `noise`（噪声残差一致性，非官方点名）**，
     每一维**四件事都要说清**（测什么 / 本次值 / 这次是弃权还是没出坐标 / 为什么不在左栏）。
  D. **数字表里不许混散文**：`measures` / `not_measures` / `polarity_note` 是给人读的注解，
     不许进 `measurements`（`guard.py:264-265`）；`bbox` 给人看的那版必须是
     `"(x,y) 宽×高"`（`guard.py:267`）。

**自证伪（③）**：把 ELA 的 `raw` 手动塞一个 `measures` 键 → `build_observations` 必须
立刻把它当成观察栏检测器收进去（默认路径的"空"是**自报边界**这条机制的结果，
不是 `build_observations` 被写死成 `return {}`）。

⚠️ 测试函数名一律 ASCII（docstring 才写中文）：本文件要用 `tmp_path`，且要走
`samples/calib_lighting_color.py::_jpeg` 的 q92 读回路径（CJK 临时目录 ⇒ `cv2.imread`
返回 None，见 `test_ela.py:121`）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samples import calib_lighting_color as CAL                              # noqa: E402
from voiceguard import config                                                # noqa: E402
from voiceguard.core.contract import ContentItem                             # noqa: E402
from voiceguard.forensics import registry, run_all                           # noqa: E402
from voiceguard.forensics.ela import ela_detector                            # noqa: E402
from voiceguard.guard import GuardPipeline, _observe_names, build_observations  # noqa: E402
from voiceguard.report.ledger import render_markdown, render_json            # noqa: E402

SAMPLE = Path(config.SAMPLES_DIR) / "base_neutral.jpg"
OBS = ["ela", "lighting", "color_edge", "noise"]
VOLATILE = ("generated_at", "evidence_files", "content_id", "path", "image_path",
            "duration", "elapsed", "fingerprint")
#: 判定部分：这一组逐字节相同才算"观察栏不进判定"。
JUDGMENT = ("evidence", "benign", "verdict", "provenance", "comment_verdict", "explanation")


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


def _run(tmp_path: Path, tag: str, detectors=None):
    rep = GuardPipeline(detectors=detectors).run(
        ContentItem(image_path=str(SAMPLE)), outdir=tmp_path / tag, tag=tag, sheet=False)
    return rep


def _splice(tmp_path: Path) -> Path:
    """标定图②（跨源拼接）落盘：两个观察栏维度**都**会出坐标 —— C 组要的就是这个。"""
    img, _ = CAL.make_splice_cross_source()
    p = tmp_path / "calib_splice_cross_source.jpg"
    CAL._jpeg(img, p)
    assert p.exists(), "标定图没落盘"          # 落盘失败要立刻红，不许后面拿着 None 跑
    return p


def _payload(rep) -> str:
    d = rep.as_dict()
    return json.dumps(_norm({k: d[k] for k in JUDGMENT}), ensure_ascii=False,
                      sort_keys=True, indent=1)


# ---------------------------------------------------------------- A 默认路径

def test_default_path_has_an_empty_observation_column(tmp_path):
    """A：默认路径（只 ela）—— `observations` 是空 dict，JSON 里连键都不出现。"""
    rep = _run(tmp_path, "obs_default")
    assert rep.observations == {}
    assert "observations" not in rep.as_dict_with_appendix()
    assert '"observations"' not in render_json(rep), "默认报告多出了 observations 键 ⇒ 冻结 sha 会变"


def test_default_report_has_no_appendix_four(tmp_path):
    """A：md 里附四一整节都不许出现（否则冻结样张的 md sha 也会变）。"""
    md = render_markdown(_run(tmp_path, "obs_md"))
    assert "附四" not in md and "观察栏" not in md


def test_appendix_four_is_not_rendered_from_an_empty_dict(tmp_path):
    """A：`observations={"detectors": []}` 这种"有键无内容"也不许渲染出一节空壳。"""
    rep = _run(tmp_path, "obs_empty")
    rep.observations = {"detectors": []}
    assert "附四" not in render_markdown(rep)


# ---------------------------------------------------------------- B 不进判定

def test_observation_detectors_do_not_move_any_judgment_byte(tmp_path):
    """B：开了三个观察栏维度之后，判定部分必须与默认路径**逐字节相同**。"""
    a = _payload(_run(tmp_path, "obs_a"))
    b = _payload(_run(tmp_path, "obs_b", detectors=OBS))
    if a != b:
        import difflib
        diff = "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=1))
        pytest.fail("观察栏改动了判定 payload：\n" + diff)
    # D13（2026-10-01 round42）：**显式**钉住兜底 `confidence`（`guard.py:432` 的
    # `[r.confidence for r in _judging(results)]`）—— 观察栏的 `confidence="mid"` 不许被收进去。
    # `base_neutral` 的证据表为空 ⇒ 这条兜底路径**真的在跑**（不是被前面的 evidence 短路掉），
    # 所以这是一条有效断言：把 `_judging()` 改成收观察级 → `high` 掉 `mid` → 这里必红。
    assert (json.loads(a)["verdict"]["confidence"]
            == json.loads(b)["verdict"]["confidence"]), "观察栏动了兜底 confidence"


def test_observation_detectors_are_recorded_but_not_in_the_left_column(tmp_path):
    """B（对照）：它们**确实跑了**（`detectors` 里有），但名字在整个判定 payload 里一次不出现。"""
    rep = _run(tmp_path, "obs_c", detectors=OBS)
    names = {d["name"] for d in rep.detectors}   # 原始检测结果序列化后是 dict（contract.py:380）
    assert names == {"ela", "lighting", "color_edge", "noise"}, names
    obs_names = {d["name"] for d in rep.observations["detectors"]}
    assert obs_names == {"lighting", "color_edge", "noise"}, obs_names

    # D12（2026-10-01 round42）：ELA **无偏亮区域** ⇒ `noise` **如实弃权**，不许编一个 step。
    # 前置条件真的在跑：base_neutral 的 ELA 区域数为 **0**（任何极性都不出）⇒ 弃权是必走路径，
    # 与「把 region_z_min 调低就会出区域」无关（把 z 门槛调低也没区域可挑）。
    noise_obs = next(d for d in rep.observations["detectors"] if d["name"] == "noise")
    assert noise_obs["status"] == "unavailable", noise_obs["status"]
    assert "未给出偏亮区域" in noise_obs["reason"], noise_obs["reason"]
    assert noise_obs["score"] == 0.0, noise_obs["score"]
    assert noise_obs["regions"] == [], noise_obs["regions"]
    d = rep.as_dict()
    left = json.dumps(_norm({k: d[k] for k in ("evidence", "benign", "verdict", "provenance")}),
                      ensure_ascii=False)
    for n in ("lighting", "color_edge", "noise"):
        assert n not in left, f"{n} 混进了左栏物证/分级（B 档被当成了物证）"

    # 机制钉死（2026-10-01 round43）：观察级**确实进信号表**（`build_signals` 只认
    # `_ran()`，`guard.py:127-133`，只看 status=="ok"、不看 default ⇒ `forensics.<维度>.*`
    # 照样被写上，实测 `noise` 32 键）。挡住判定的真机制是 **rules.yaml 从不引用
    # `forensics.<观察维度>.`**；`_judging()` 另挡兜底 confidence 与证据表挑拣两处。
    # 于是"加一条 `forensics.noise.*` 的 when:"＝判定静默改变却无用例变红 —— 这条把它焊死。
    # 正面控制：ela 的引用必须 > 0，否则下面这条检查是空转（不会红的检查等于没有检查）。
    rules_text = (ROOT / "voiceguard" / "rules" / "rules.yaml").read_text(encoding="utf-8")
    assert "forensics.ela." in rules_text, "正面控制失败：规则表不按前缀引用 ⇒ 本检查空转"
    for _n in sorted(_observe_names()):          # 观察维度从注册表推导，不写死名字
        assert f"forensics.{_n}." not in rules_text, (
            f"观察维度 {_n} 被写进了 rules.yaml ⇒ 判定面会静默改变（B 档被接进了规则层）")


# ---------------------------------------------------------------- C 附四的内容

def test_appendix_four_carries_the_four_required_things(tmp_path):
    """C：附四每一维四件事都要说清 —— 测什么 / 本次值 / 弃权还是没出坐标 / 为什么不在左栏。"""
    rep = GuardPipeline(detectors=OBS).run(
        ContentItem(image_path=str(_splice(tmp_path))), outdir=tmp_path / "obs_md4",
        tag="obs_md4", sheet=False)
    md = render_markdown(rep)
    assert "## 附四：观察栏（层1 扩展维度 · 仅供人工复核）" in md
    assert "**不是物证**" in md, "附四必须自报'不进左栏、不进分级'"
    for name in ("lighting", "color_edge", "noise"):
        assert f"### 观察维度：`{name}`" in md, name
    for item in ("1. **它测什么**", "2. **本次测到的值**", "3. **本次**", "4. **为什么不在左栏**"):
        assert md.count(item) >= 2, f"附四缺「{item}」（每个维度各一条）"
    # 官方口径的出处必须写进报告（评审要对得上原文）
    assert "loreal-topic2-details.md:48/:50" in md
    # 附四不许混进"第一至四节"（前四节是判定）
    assert md.index("## 附四") > md.index("## 附三") if "## 附三" in md else True


# ---------------------------------------------------------------- D 结构契约

def test_observation_structure_matches_the_preregistered_shape(tmp_path):
    """D：结构 = 预注册 §3 的口径（Hermes 的验收脚本按键名取用，键名不许自作主张）。"""
    rep = _run(tmp_path, "obs_shape", detectors=OBS)
    top = rep.observations
    assert set(top) == {"detectors"}
    for d in top["detectors"]:
        assert set(d) == {"name", "status", "reason", "score", "measurements", "regions",
                          "what_it_measures", "what_it_does_not"}, set(d)
        assert d["status"] in ("ok", "unavailable")
        assert isinstance(d["measurements"], dict) and d["measurements"]
        assert d["what_it_measures"] and d["what_it_does_not"]
        for g in d["regions"]:
            assert set(g) == {"bbox", "polarity", "z_peak", "shape"}, set(g)
            assert g["bbox"] == "" or (g["bbox"].startswith("(") and "×" in g["bbox"])


def test_measurements_hold_numbers_not_prose(tmp_path):
    """D：注解键（measures / not_measures / polarity_note）不许混进数字表。"""
    rep = _run(tmp_path, "obs_num", detectors=OBS)
    for d in rep.observations["detectors"]:
        m = d["measurements"]
        assert not (set(m) & {"measures", "not_measures", "polarity_note"}), set(m)
        assert all(isinstance(v, (int, float, bool, str, type(None))) for v in m.values()), \
            "measurements 里出现了嵌套结构（注解/坐标被整块搬进来了）"
    light = next(d for d in rep.observations["detectors"] if d["name"] == "lighting")
    assert {"n_blocks", "r_consistency", "theta_main_deg", "n_regions", "median_grad",
            "brighter_z_peak_max", "darker_z_peak_max", "brighter_area_ratio",
            "darker_area_ratio", "reliable"} <= set(light["measurements"])


def test_regions_are_squeezed_to_the_four_published_fields(tmp_path):
    """D：region 只带 4 个字段 —— 检测器自己的判据量（ΔE / 纹理比 / split_mode）不进观察栏。"""
    rep = GuardPipeline(detectors=OBS).run(
        ContentItem(image_path=str(_splice(tmp_path))), outdir=tmp_path / "obs_reg",
        tag="obs_reg", sheet=False)
    shared = [g for d in rep.observations["detectors"] if d["name"] == "color_edge"
              for g in d["regions"]]
    assert shared, "跨源拼接缝在观察栏里必须有坐标"
    assert set(shared[0]) == {"bbox", "polarity", "z_peak", "shape"}


# ---------------------------------------------------------------- ③ 自证伪

def test_falsify_self_declaration_is_the_only_way_in(tmp_path):
    """③：给 ELA 的 raw 塞一个 `measures` → 观察栏必须立刻收下它。

    若 `build_observations` 被写成 `return {}`，或入选条件被改成"按名字白名单"，
    默认路径的"空"就变成了巧合 —— 这条会红。
    """
    img, meta = CAL.make_splice_cross_source()
    import numpy as np
    u8 = np.clip(img, 0, 255).astype(np.uint8)
    r = ela_detector(u8, meta=None)
    assert build_observations([r]) == {}, "ELA 没有自报边界却进了观察栏"

    r.raw = {**r.raw, "measures": "假装我自报了边界", "not_measures": "假装"}
    obs = build_observations([r])
    assert [d["name"] for d in obs["detectors"]] == ["ela"], (
        "自报边界之后仍进不了观察栏 ⇒ 入选条件不是 `raw['measures']`，默认路径的'空'不可信")
