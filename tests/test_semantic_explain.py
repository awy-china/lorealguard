"""F3 语义解释层守卫：**判定不被解释污染** + 开源权重白名单 + 不编造。

守的核心不变量（答辩会被追问的那条）：
    同一个输入，开不开 --explain，**判定部分必须逐字节一致**。
    解释可以千变万化，判定一个字都不能动 —— 这是「大模型不碰真假」的代码化落地。

另一条同样硬：模型白名单。赛题要求「必须用开源大模型而非闭源模型」，
    所以交付运行时可调的档只能来自 config.OPEN_WEIGHT_MODELS（Apache-2.0）。

全部测试**零网络**：注入假客户端（FakeAsk），不碰百炼。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from voiceguard import config
from voiceguard.core.contract import ContentItem, Evidence, RiskReport
from voiceguard.guard import GuardPipeline
from voiceguard.semantic import explain as sem

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "samples" / "base_neutral.jpg"

#: 判定核心字段：这些一旦被解释层改动，就是作弊，测试必须红
JUDGMENT_KEYS = ("content_id", "kind", "fingerprint", "shape", "comment_verdict",
                 "evidence", "benign", "provenance", "detectors", "verdict", "boundaries")

ANSWER = ("坐标 (x=310,y=180,w=96,h=96) 落在人脸皮肤区：磨皮后亮度极平滑、缺少高频纹理，"
          "属于算法容易误伤的正常内容；不足以判断是否伪造。")


# ------------------------------------------------------------------ 守卫函数（变异体直接复用）
def judgment_json(report) -> str:
    d = report.as_dict()
    return json.dumps({k: d[k] for k in JUDGMENT_KEYS}, ensure_ascii=False, sort_keys=True)


def assert_judgment_identical(a, b) -> None:
    """守卫①：开/关解释两份报告的判定部分必须逐字节相同。"""
    ja, jb = judgment_json(a), judgment_json(b)
    if ja != jb:
        i = next((i for i, (x, y) in enumerate(zip(ja, jb)) if x != y), min(len(ja), len(jb)))
        raise AssertionError(
            f"判定被解释层污染：开/关解释的判定字节不一致（首个差异 @{i}）\n"
            f"  关：{ja[max(0, i - 40):i + 40]}\n  开：{jb[max(0, i - 40):i + 40]}")


def assert_allowed(model: str, whitelist=None) -> None:
    """守卫②：模型必须在开源权重白名单里（闭源档一律拒绝）。"""
    wl = config.OPEN_WEIGHT_MODELS if whitelist is None else whitelist
    if model not in wl:
        raise AssertionError(
            f"模型 {model} 不在开源权重白名单内；赛题要求必须用开源大模型 → 拒绝调用")


#: **冻结集合**：只有已在 HF 官方权重页核实为 Apache-2.0 的档才准进白名单。
#: 想加新档？先去核实许可，再改这个集合 —— 改集合 = 显式声明"我核实过了"。
KNOWN_OPEN_WEIGHT = {"qwen3.8-27b"}


def assert_whitelist_is_open_weight(whitelist=None) -> None:
    """守卫②b：白名单**本身**不许被悄悄放宽（加了没核实过的档必须红）。"""
    wl = set(config.OPEN_WEIGHT_MODELS if whitelist is None else whitelist)
    if not wl:
        raise AssertionError("白名单为空 → 交付运行时没有任何合规档可用")
    extra = wl - KNOWN_OPEN_WEIGHT
    if extra:
        raise AssertionError(
            f"白名单出现**未经核实**为开源权重的档：{sorted(extra)}；"
            f"必须先在 HF 官方权重页核实许可，再显式改 KNOWN_OPEN_WEIGHT")


def assert_explanation_honest(ex: dict) -> None:
    """守卫③：status=ok 必须真有答案；不许空串/占位符冒充成功。"""
    if ex.get("status") == "ok" and not (ex.get("answer") or "").strip():
        raise AssertionError(f"解释层自称 ok 却没有答案 → 属于编造：{ex}")
    if ex.get("status") == "ok" and len(ex.get("answer", "")) < 4:
        raise AssertionError(f"解释层答案短到不可能是真解释：{ex.get('answer')!r}")


def assert_explain_default_off(src: str) -> None:
    """守卫④：F3 默认必须是关闭的（默认路径零模型零网络 → 拔网线可跑）。"""
    m = re.search(r"explain:\s*bool\s*=\s*(\w+)", src)
    if not m:
        raise AssertionError("找不到 explain 开关的默认值签名")
    if m.group(1) != "False":
        raise AssertionError(f"F3 解释层不再是默认关闭（explain 默认 = {m.group(1)}）→ 默认路径会联网")


NET_RE = re.compile(r"^\s*(?:from|import)\s+(openai|requests|urllib|httpx|http\.client|socket)\b",
                    re.M)


def assert_no_network_imports(name: str, src: str) -> None:
    """守卫⑤：交付判定链不得有对外通信 import（唯一例外是 semantic/vl.py 的网络出口）。"""
    if NET_RE.search(src):
        raise AssertionError(f"{name} 出现对外通信 import；判定链必须零网络："
                             f"{NET_RE.search(src).group(0).strip()}")


# ------------------------------------------------------------------ 假客户端
class FakeAsk:
    """假模型客户端：记录调用、可注入异常。用它跑测试 = 零网络、零 token。"""

    def __init__(self, answer: str = ANSWER, raises: Exception | None = None, ok: bool = True):
        self.answer, self.raises, self.ok = answer, raises, ok
        self.calls: list = []

    def __call__(self, img, question, model):
        self.calls.append((str(img), question, model))
        if self.raises:
            raise self.raises
        return {"ok": self.ok, "answer": self.answer if self.ok else "",
                "prompt_tokens": 512, "reasoning_tokens": 0}


@pytest.fixture()
def img_path() -> str:
    if not IMG.exists():
        pytest.skip(f"缺合成样图 {IMG}")
    return str(IMG)


def run(img: str, tmp_path: Path, explain: bool = False, ask=None, tag: str = "t"):
    pipe = GuardPipeline(explain=explain, explain_ask=ask)
    return pipe.run(ContentItem(image_path=img), outdir=tmp_path, tag=tag)


def artifact(report, ext: str) -> Path:
    """产物真实落盘路径（别猜目录结构，从产物清单里取）。"""
    p = next((f for f in report.evidence_files
              if isinstance(f, str) and f.lower().endswith(ext)), None)
    assert p, f"产物清单里没有 {ext}：{report.evidence_files}"
    return Path(p)


# ------------------------------------------------------------------ ① 默认关闭
def test_default_off_no_model_no_network(img_path, tmp_path):
    rep = run(img_path, tmp_path)
    assert rep.explanation == {}, "默认路径不该产出解释"
    f3 = next(l for l in rep.layers if l["layer"].startswith("F3"))
    assert "默认关闭" in f3["status"] and "--explain" in f3["status"]
    md = artifact(rep, ".md").read_text(encoding="utf-8")
    assert "附二、语义解释" in md and "**非判定**" in md
    assert "未运行（默认关闭）" in md


# ------------------------------------------------------------------ ② ok 路径
def test_explain_ok_path(img_path, tmp_path):
    fake = FakeAsk()
    rep = run(img_path, tmp_path, explain=True, ask=fake, tag="t2")
    ex = rep.explanation
    assert ex["status"] == "ok" and ex["answer"] == ANSWER
    assert ex["model"] == config.MODEL_SEMANTIC and ex["license"].startswith("Apache-2.0")
    assert ex["used_evidence"] == [e.id for e in rep.evidence] or not rep.evidence
    assert len(fake.calls) == 1, "解释层只该发起一次调用"
    assert fake.calls[0][2] == config.MODEL_SEMANTIC
    assert_explanation_honest(ex)
    assert_allowed(ex["model"])
    f3 = next(l for l in rep.layers if l["layer"].startswith("F3"))
    assert "已跑" in f3["status"] and "仅描述" in f3["status"]
    md = artifact(rep, ".md").read_text(encoding="utf-8")
    assert "磨皮后亮度极平滑" in md, "解释须落进报告 md"
    assert "永不参与判定" in md or "不参与判定" in md
    js = json.loads(artifact(rep, ".json").read_text(encoding="utf-8"))
    assert js["explanation"]["status"] == "ok"


# ------------------------------------------------------------------ ③ ★判定不变性（核心）
_TS = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


def test_judgment_identical_on_off(img_path, tmp_path):
    off = run(img_path, tmp_path / "a", explain=False, tag="j")
    on = run(img_path, tmp_path / "b", explain=True, ask=FakeAsk(), tag="j")
    assert_judgment_identical(off, on)
    # 报告正文第一至四节（判定部分）也必须逐字相同；差异只能出现在附二之后。
    # 生成时刻是墙钟字段，两次跑必然差几秒 → 归一化掉（否则守卫会伪红）。
    md_off = _TS.sub("<TS>", artifact(off, ".md").read_text(encoding="utf-8").split("## 附二")[0])
    md_on = _TS.sub("<TS>", artifact(on, ".md").read_text(encoding="utf-8").split("## 附二")[0])
    assert md_off == md_on, "开解释后判定正文被改写了"


# ------------------------------------------------------------------ ④ 无 key / 失败 → 如实降级
@pytest.mark.parametrize("exc,expect", [
    (RuntimeError("未找到 API key：请配置 DASHSCOPE_API_KEY"), "unavailable_no_key"),
    (RuntimeError("HTTPSConnectionPool: connection refused"), "error"),
    (TimeoutError("timed out"), "error"),
])
def test_failure_degrades_honestly(img_path, tmp_path, exc, expect):
    off = run(img_path, tmp_path / "off", explain=False, tag="k")
    on = run(img_path, tmp_path / "on", explain=True, ask=FakeAsk(raises=exc), tag="k")
    assert on.explanation["status"] == expect
    assert not on.explanation["answer"], "失败时不许产出任何解释文本"
    assert_judgment_identical(off, on)
    f3 = next(l for l in on.layers if l["layer"].startswith("F3"))
    assert "未产出" in f3["status"]


def test_model_returning_empty_answer_is_not_ok(img_path, tmp_path):
    """模型返回空 → 不能冒充成功。"""
    on = run(img_path, tmp_path, explain=True, ask=FakeAsk(ok=False), tag="e")
    assert on.explanation["status"] == "error" and not on.explanation["answer"]


# ------------------------------------------------------------------ ⑤ 白名单守卫
def test_refuse_closed_model_without_calling(img_path, tmp_path):
    rep = run(img_path, tmp_path, explain=False, tag="w")
    fake = FakeAsk()
    ex = sem.explain(rep, model="qwen3.8-max", ask=fake)
    assert ex["status"] == "refused_closed_model"
    assert fake.calls == [], "闭源档必须拒绝，且**不得发起任何调用**"
    ex2 = sem.explain(rep, model="deepseek-v4.1-flash", ask=fake)
    assert ex2["status"] == "refused_closed_model" and fake.calls == []


def test_whitelist_and_config_consistency():
    assert_allowed(config.MODEL_AGENT) and assert_allowed(config.MODEL_SEMANTIC)
    assert_whitelist_is_open_weight()
    assert set(config.OPEN_WEIGHT_MODELS) & set(config.RESEARCH_ONLY_MODELS) == set(), \
        "白名单与离线研究档不许重叠"
    assert not hasattr(config, "MODEL_AGENT_SAFE"), \
        "错的安全网 MODEL_AGENT_SAFE（闭源 Max 档）必须已删除"
    for m, why in config.RESEARCH_ONLY_MODELS.items():
        with pytest.raises(AssertionError):
            assert_allowed(m)


# ------------------------------------------------------------------ ⑥ 无图 / 提问构造
def test_no_image_degrades(tmp_path):
    rep = RiskReport(content_id="x", image_path=str(tmp_path / "nope.jpg"))
    ex = sem.explain(rep, sheet_path=None, ask=FakeAsk())
    assert ex["status"] == "unavailable_no_image" and not ex["answer"]


def test_question_uses_only_located_evidence(img_path, tmp_path):
    rep = run(img_path, tmp_path, explain=False, tag="q")
    q = sem.build_question(rep)
    if rep.evidence:
        assert "严禁输出任何" in q
        for e in rep.evidence:
            assert e.id in q
    else:
        # 我们自己的合成样图**本来就干净**（左栏为空）→ 走"没有可疑区域"的提问
        assert "未定位到任何可疑区域" in q and "坐标(x=" not in q
    # 构造一条有定位证据的报告：提问必须带上坐标与实测值，且禁判定句仍在
    rep2 = RiskReport(content_id="z")
    rep2.evidence = [Evidence(id="E1", layer="forensics", kind="ela_region",
                              label="局部压缩异常", bbox=[10, 20, 30, 40],
                              metrics={"peak": 0.71})]
    q2 = sem.build_question(rep2)
    assert "E1" in q2 and "x=10" in q2 and "peak=0.71" in q2 and "严禁输出任何" in q2


# ------------------------------------------------------------------ ⑦ 静态守卫：默认关闭 + 判定链零网络
def test_guard_module_explain_default_off():
    src = (ROOT / "voiceguard" / "guard.py").read_text(encoding="utf-8")
    assert_explain_default_off(src)


def test_delivery_judgment_chain_has_no_network_imports():
    """判定链（含报告层）零网络：唯一允许联网的模块是 semantic/vl.py。"""
    allowed = {"semantic\\vl.py", "semantic/vl.py"}
    checked = 0
    for p in sorted((ROOT / "voiceguard").rglob("*.py")):
        rel = str(p.relative_to(ROOT / "voiceguard"))
        if rel in allowed:
            continue
        assert_no_network_imports(rel, p.read_text(encoding="utf-8"))
        checked += 1
    assert checked >= 15, f"扫描到的模块太少（{checked}），守卫可能失效"