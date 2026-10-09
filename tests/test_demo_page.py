"""`demo/` 演示交互面的守卫。

这一层新增的是**产品形态**，不是判定能力 —— 所以守卫的重点不是"页面好不好看"，
而是三件会被答辩追问、也会被现场踩的事：

  ① **页面不自带结论**：所有数字从 `output/demo/*.json` 读；HTML 里不许嵌判定结果。
  ② **默认零网络**：拔网线（代理指向死端口）照出账本；`--explain` 只把图送出去，
     失败如实登记 `unavailable_*`，**判定字段逐字节不变**。
  ③ **只吃合成样本**：图片白名单在 `samples/`，`../`、绝对路径、越界后缀一律 400；
     产物只落 `output/demo/`。

每条都能变红；若哪天有人把数字写进 HTML、或让页面自己算等级，这里会先红。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import threading
import urllib.error
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")

_spec = importlib.util.spec_from_file_location("demo_serve", ROOT / "demo" / "serve.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

from voiceguard.core.contract import ContentItem          # noqa: E402
from voiceguard.guard import GuardPipeline                # noqa: E402

#: 判定字段：这些字段是「判定链」的全部可复算出口，演示层不许碰。
VERDICT_KEYS = ("fingerprint", "verdict", "evidence", "benign", "provenance",
                "detectors", "comment_verdict")

IMAGE = "samples/fw_splice.jpg"
TEXT = "7 天根除痘印，我用完就消失了，姐妹们亲测有效"


class Client:
    """本机 HTTP 客户端。**绕开环境代理** —— 测试里会故意把代理指向死端口。"""

    def __init__(self, base: str):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _open(self, req):
        try:
            r = self.opener.open(req)
            return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get(self, path: str):
        return self._open(urllib.request.Request(self.base + path))

    def get_json(self, path: str):
        st, body = self.get(path)
        return st, json.loads(body)

    def post(self, payload):
        req = urllib.request.Request(
            self.base + "/api/run", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        st, body = self._open(req)
        try:
            return st, json.loads(body)
        except json.JSONDecodeError:                       # 崩溃时把原文带出来
            return st, {"ok": False, "error": body[:400].decode("utf-8", "replace")}


@pytest.fixture()
def demo(tmp_path, monkeypatch):
    """起一个真服务（随机端口），产物落 tmp_path（不污染 output/demo）。"""
    monkeypatch.setattr(S, "DEMO_DIR", tmp_path)
    monkeypatch.setattr(S, "_ASK", None)
    httpd = S.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield Client(f"http://127.0.0.1:{httpd.server_address[1]}")
    finally:
        httpd.shutdown()
        httpd.server_close()


def run(client: Client, **kw) -> dict:
    payload = {"image": IMAGE, "text": TEXT, "comments": "", "explain": False}
    payload.update(kw)
    st, d = client.post(payload)
    assert st == 200, f"核验失败 {st}：{d}"
    assert d["ok"], d
    return d


# --------------------------------------------------------------- ① 页面不自带结论

def test_page_has_zero_external_requests():
    """零外链：没有绝对地址、没有外链脚本/样式/字体。现场断网也照开。"""
    assert "<link" not in HTML, "页面出现了 <link>（外链样式/字体）"
    assert not re.search(r"<script[^>]*\bsrc\s*=", HTML), "页面出现了外链 <script src>"
    assert not re.search(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""", HTML), "存在绝对地址"
    assert not re.search(r"@import|url\(\s*['\"]?https?:", HTML), "CSS 里有外链引用"
    for bad in ("googleapis", "cdn.", "unpkg", "jsdelivr"):
        assert bad not in HTML, f"引用了外部资源：{bad}"
    # fetch 只允许相对本站的路径
    for url in re.findall(r"""fetch\(\s*["']([^"']+)""", HTML):
        assert url.startswith("/"), f"页面请求了站外地址：{url}"


def test_page_never_bakes_verdict_values(demo):
    """判据③：页面上的数字必须从 JSON 读 —— HTML 里不许出现任何一次核验的结果。"""
    d = run(demo)
    rep = d["report"]
    baked = [rep["fingerprint"], rep["content_id"]]
    for h in rep["verdict"]["fired"]:
        baked += [h["rule_id"], h["desc"]]
    for e in rep["evidence"]:
        baked += [e["label"], e["detail"]]
        for v in e["metrics"].values():
            if isinstance(v, float):
                baked.append(f"{v:.4f}")
            elif isinstance(v, int) and abs(v) >= 100:
                baked.append(str(v))
    hits = [b for b in baked if b and b in HTML]
    assert not hits, f"HTML 里嵌进了核验结果（必须改成从 JSON 读）：{hits[:4]}"
    # 等级值来自规则引擎，页面只负责上色
    assert re.search(r"\bverdict\b", HTML) and re.search(r"\.tier\b", HTML), \
        "页面没有从报告 JSON 里取等级"
    assert "TIER_STYLE" in HTML, "等级色板不见了（色板只上色，不判级）"


# --------------------------------------------------------------- ② 默认零网络

def test_runs_offline_with_dead_proxy(demo, monkeypatch):
    """拔网线照出账本：出口代理指向死端口后，核验仍必须跑完。"""
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(k, "http://127.0.0.1:9")
    d = run(demo, explain=False)
    assert d["report"]["explanation"] == {}, "默认路径不该产出任何模型产物"
    assert d["report"]["verdict"]["tier"], "断网时等级必须照出"
    assert d["files"]["sheet"], "断网时证据图必须照出"


def test_explain_defaults_off_and_degrades_honestly(demo, monkeypatch):
    """解释层：默认关；没有 key / 调用失败 → 如实登记，绝不假装成功。"""
    assert run(demo, image="samples/base_neutral.jpg", explain=False)["report"]["explanation"] == {}

    def no_key(*a, **k):
        raise RuntimeError("未找到 API key（环境变量或本机 .env）")

    monkeypatch.setattr(S, "_ASK", no_key)
    d = run(demo, image="samples/base_neutral.jpg", explain=True)
    ex = d["report"]["explanation"]
    assert ex["status"] == "unavailable_no_key", ex
    assert ex["answer"] == "", "拿不到解释时不许返回空串冒充成功"
    # 页面必须把这个状态显示出来（而不是藏起来）
    assert "renderExplain" in HTML and "ex.status" in HTML, "页面没有如实渲染解释层状态"


def test_demo_layer_leaves_verdict_byte_identical(demo, tmp_path):
    """演示层不许碰判定：同一输入，经页面跑 / 直接跑引擎，判定字段必须逐字节一致。"""
    comments = ["30ml 用了两周，混油皮夏天有点闷，但早上上妆不搓泥。"] * 6
    via_page = run(demo, comments="\n".join(comments))["report"]

    direct = GuardPipeline(explain=False).run(
        ContentItem(image_path=str(ROOT / IMAGE), text=TEXT, comments=comments,
                    kind="comment_thread"),
        outdir=tmp_path / "direct", tag="direct", sheet=False).as_dict()

    for k in VERDICT_KEYS:
        assert json.dumps(via_page[k], sort_keys=True, ensure_ascii=False) == \
               json.dumps(direct[k], sort_keys=True, ensure_ascii=False), \
               f"演示层改动了判定字段：{k}"


def test_explain_on_does_not_change_verdict(demo, monkeypatch):
    """开解释 ≠ 改判定：F3 只写 explanation，判定字段一格不动。"""
    monkeypatch.setattr(S, "_ASK", lambda p, q, m: {"ok": True, "answer": "这一块是产品包装与文字标签。"})
    off = run(demo, image="samples/base_neutral.jpg", explain=False)["report"]
    on = run(demo, image="samples/base_neutral.jpg", explain=True)["report"]
    assert on["explanation"]["status"] == "ok", on["explanation"]
    assert on["explanation"]["answer"], "假客户端返回了内容，这里应该是 ok"
    for k in VERDICT_KEYS:
        assert json.dumps(on[k], sort_keys=True, ensure_ascii=False) == \
               json.dumps(off[k], sort_keys=True, ensure_ascii=False), f"F3 影响了判定字段：{k}"


# --------------------------------------------------------------- ②b F9 Agent 编排（默认关）

def test_agent_defaults_off_and_page_cannot_opt_in_implicitly(demo):
    """F9 与 F3 同一条纪律：**默认关**。

    关着时报告 JSON 里**连 `agent_trace` 这个键都不该出现**（`as_dict_with_agent`
    只在真跑过时才多这个键）——「键在、值是空 dict」也是一种谎：它让人以为跑过了。
    """
    d = run(demo, image="samples/base_neutral.jpg")
    assert "agent_trace" not in d["report"], "没勾选却跑了 F9（默认必须是关）"
    # 请求体里没有 agent 字段时也必须是关（页面之外的人直接 POST 也一样）
    assert d["inputs"]["agent"] is False

    m = re.search(r'<input[^>]*id="agent-toggle"[^>]*>', HTML)
    assert m, "页面上没有 F9 勾选框"
    assert "checked" not in m.group(0), "F9 勾选框默认就是勾上的 —— 默认零网络这条就没了"
    assert 'agent: $("#agent-toggle").checked' in HTML, "勾选框的值没进请求体（勾了也不会跑）"
    assert "renderAgent" in HTML and 'id="agent-card"' in HTML, "页面没有渲染 F9 附三的出口"
    # ⚠️ 页面**不做** F9 的「未运行」占位：没跑就整块隐藏（见 renderAgent 的分支），
    #    因为 F9 的默认态是「本就不该跑」，而 F3 的默认态是「跑不了也要如实说」。


def test_agent_on_does_not_change_verdict(demo, monkeypatch):
    """开 F9 ≠ 改判定：它只写 agent_trace，判定字段一格不动。

    假客户端只回一步 `read_ledger` —— 连"追加取证"都不做，就是为了把
    「编排层跑过了」与「判定变了没有」彻底分开。
    """
    calls = []

    def fake(prompt, model=None):
        calls.append(prompt)
        acts = [{"action": "read_ledger", "args": {}},
                {"action": "finish", "args": {"summary": "账本已读，无需追加取证。"}}]
        return {"ok": True, "answer": json.dumps(acts[min(len(calls), 2) - 1])}

    monkeypatch.setattr(S, "_AGENT_ASK", fake)
    off = run(demo, image="samples/base_neutral.jpg", agent=False)["report"]
    on = run(demo, image="samples/base_neutral.jpg", agent=True)["report"]
    assert calls, "勾了 F9 却一次都没问模型"
    assert on["agent_trace"]["status"] == "ok", on["agent_trace"]
    assert on["agent_trace"]["steps"], "跑过了却没有步骤轨迹"
    for k in VERDICT_KEYS:
        assert json.dumps(on[k], sort_keys=True, ensure_ascii=False) == \
               json.dumps(off[k], sort_keys=True, ensure_ascii=False), f"F9 影响了判定字段：{k}"


# --------------------------------------------------------------- ②c 附四观察栏（默认关）

def test_observe_defaults_off_and_page_cannot_opt_in_implicitly(demo):
    """观察栏与 F3/F9 同一条纪律：**默认关**。

    关着时报告 JSON 里**连 `observations` 这个键都不该出现**（`contract.py:424`
    只在它非空时才写）—— 理由同 F9：「键在、值是空 dict」也是一种谎。
    """
    d = run(demo, image="samples/base_neutral.jpg")
    assert "observations" not in d["report"], "没勾选却跑了观察栏（默认必须是关）"
    assert d["inputs"]["observe"] is False          # 请求体里没这个字段时也是关（直接 POST 的人也一样）

    m = re.search(r'<input[^>]*id="observe-toggle"[^>]*>', HTML)
    assert m, "页面上没有观察栏勾选框"
    assert "checked" not in m.group(0), "观察栏勾选框默认就是勾上的"
    assert 'observe: $("#observe-toggle").checked' in HTML, "勾选框的值没进请求体（勾了也不会跑）"
    assert "renderObservations" in HTML and 'id="observe-card"' in HTML, \
        "页面没有渲染附四观察栏的出口"


def test_observe_on_does_not_change_verdict(demo):
    """开观察栏 ≠ 改判定：它只写 `observations`（附四），判定字段一格不动。

    ⚠️ **一处例外要写清楚**：`detectors` 是"这一轮跑了哪些检测器"的**运行清单**，不是判定 ——
    开了观察栏它**本就应该**多三项（同 `tests/test_observations_column.py:127` 的口径）。
    所以这里断言的是：`detectors` 恰好 +3，而**其余**判定字段逐字节不动。
    """
    off = run(demo, image="samples/base_neutral.jpg", observe=False)["report"]
    on = run(demo, image="samples/base_neutral.jpg", observe=True)["report"]

    names = sorted(d["name"] for d in (on.get("observations") or {}).get("detectors") or [])
    assert names == ["color_edge", "lighting", "noise"], f"观察栏没真跑起来（空壳）：{names}"
    assert len(on["detectors"]) == len(off["detectors"]) + 3, \
        f"观察级检测器没真进运行清单：{off['detectors']} → {on['detectors']}"
    for k in [x for x in VERDICT_KEYS if x != "detectors"]:
        assert json.dumps(on[k], sort_keys=True, ensure_ascii=False) == \
               json.dumps(off[k], sort_keys=True, ensure_ascii=False), f"观察栏影响了判定字段：{k}"


# --------------------------------------------------------------- ③ 只吃合成样本

@pytest.mark.parametrize("bad", [
    "../voiceguard/config.py", "samples/../CLAUDE.md", "samples/..\\CLAUDE.md",
    "/etc/passwd", "C:/Windows/win.ini", "voiceguard/config.py",
    "samples/nope.jpg", "samples/base_neutral.exe", "samples/", "", None,
    # 2026-09-25 为 `samples/calib/` 放开"一层子目录"**之后**新增的越界样本：
    # 放宽的只能是**层数** —— `..` / 空段 / 隐藏段 / 三级路径一律还得拒。
    "samples/calib/../base_neutral.jpg", "samples/calib/..", "samples/a/b/c.jpg",
    "samples/calib/", "samples//base_neutral.jpg", "samples/.hidden/x.jpg",
])
def test_api_rejects_out_of_scope_images(demo, tmp_path, bad):
    st, d = demo.post({"image": bad, "text": "", "comments": ""})
    assert st == 400, f"{bad!r} 本该被拒绝，却返回 {st}"
    assert not d["ok"]
    assert list(tmp_path.iterdir()) == [], "被拒绝的请求不该留下任何产物"


def test_api_accepts_the_calibration_image_one_level_deep(demo):
    """`samples/calib/` 那四张标定图必须能跑 —— 观察栏的演示**只有它们量得出数字**。

    这是放开"一层子目录"的**唯一用途**：`samples/` 根目录三张图上 `lighting`
    实测全部 `unavailable`（2026-09-25 逐图跑 `--observe`）。
    这里不是"只要 200 就算过"—— 要真的把观察栏跑起来（三个维度都进了
    `observations`），否则放宽白名单一点意义都没有。
    """
    d = run(demo, image="samples/calib/calib_splice_cross_source.jpg", observe=True)
    names = sorted(x["name"] for x in (d["report"].get("observations") or {}).get("detectors") or [])
    assert names == ["color_edge", "lighting", "noise"], f"标定图上观察栏没跑起来：{names}"
    assert d["inputs"]["observe"] is True


def test_report_endpoint_rejects_traversal(demo):
    st, body = demo.get("/api/report?tag=../../voiceguard/config")
    assert st == 400, f"越界 tag 本该 400，实际 {st}"
    assert b"OPEN_WEIGHT_MODELS" not in body, "把仓库源码读出来了"
    st, _ = demo.get("/api/report?tag=%2e%2e%2fCLAUDE.md")
    assert st == 400
    st, d = demo.get_json("/api/report?tag=nonexistent_tag")
    assert st == 404, d


def test_oversized_inputs_rejected(demo):
    assert demo.post({"image": IMAGE, "text": "啊" * (S.MAX_TEXT + 1)})[0] == 400
    too_many = "\n".join(f"评论 {i}" for i in range(S.MAX_COMMENT_LINES + 1))
    assert demo.post({"image": IMAGE, "comments": too_many})[0] == 400
    assert demo.post({"image": IMAGE, "comments": "啊" * (S.MAX_COMMENT_LINE + 1)})[0] == 400
    st, _ = demo.post({"image": IMAGE, "tag": "../../evil"})
    assert st == 400


def test_non_loopback_peer_is_refused():
    """纵深防御：判断来源是否回环（绑定 127.0.0.1 之外的第二道闸）。"""
    class Fake:
        client_address = ("10.0.0.7", 51000)
    assert S.Handler._loopback_only(Fake()) is False
    Fake.client_address = ("127.0.0.1", 51000)
    assert S.Handler._loopback_only(Fake()) is True


# --------------------------------------------------------------- 三环都要有出口

def test_comment_scenario_is_reachable_from_the_page(demo):
    """评论区核验必须能从页面上跑通。

    （2026-09-23 订正：本条原写「评论区核验在 CLI 上没有出口（`__main__.py:39` 只收图）
    —— 本页必须能跑通它」。CLI 现已接出 `--comments-file`，所以"页面是唯一出口"这句
    不再成立；但页面上的可达性本身仍是必须守的事实，故本条保留。）
    """
    seed = "这个真的好用，需要的私信我，链接在评论区，姐妹们冲！"
    d = run(demo, comments="\n".join([seed] * 8))
    cv = d["report"]["comment_verdict"]
    assert cv.get("tier"), "评论区层没有出结论"
    assert cv.get("hits") and cv.get("attributions"), "评论区层缺少命中或归因"
    assert d["inputs"]["n_comments"] == 8
    assert "renderComment" in HTML, "页面没有渲染评论区层"


def test_text_image_consistency_scenario_is_reachable(demo):
    """图文一致性（F4-C）需要文案入参 —— 本页必须能把它跑出来。"""
    d = run(demo, text="7 天根除痘印")
    states = [x.get("kind") for x in d["report"]["evidence"]]
    assert "efficacy_timeline" in states, f"没跑到图文一致性/生理尺度这条通路：{states}"


def test_评论切分口径与命令行同源():
    """网页与命令行共用**同一份**「一行一条评论」的定义：`core/contract.parse_comment_lines`。

    这条守的是「只有一处定义」这句话本身。两边各长一套分毫不差的实现，
    迟早会出现「网页上算出来是高风险、命令行跑出来不是」——
    同一条内容因为入口不同而得到不同判定，是本系统最不该有的那种不一致。

    做法：把同一批**刁钻输入**同时喂给两边，要求逐条相同。
    """
    from voiceguard.core.contract import parse_comment_lines
    for raw in (None, "", "\n\n\n", "   \n\t\n",
                "单条", "甲\n\n  乙  \n\t丙\t\n",
                "带\x1f分隔符的行\n第二行", "  前后都有空白  \n末行"):
        assert S._parse_comments(raw) == parse_comment_lines(raw), f"两边口径不一致：{raw!r}"


def test_decision_card_covers_both_sides(demo):
    """赛题细则②要的「风险预警 + 处理建议」：平台侧与创作者侧两条都不能少。"""
    d = run(demo)
    dec = d["decision"]
    assert dec["tier"] and dec["alert"], "决策卡缺等级或预警"
    owners = {a["owner"] for a in dec["actions"]}
    assert {"平台", "创作者"} <= owners, f"建议只给了一边：{owners}"
    assert all(isinstance(a["requires_human"], bool) for a in dec["actions"])
    assert dec["human_in_the_loop"] and dec["disclaimer"], "缺「人在环上」或免责声明"


def test_artifacts_land_in_demo_dir_only(demo, tmp_path):
    """产物只落 output/demo/：不许污染 output/guard/（那是既有评测口径的数据源）。"""
    guard = ROOT / "output" / "guard"
    before = {p.name for p in guard.glob("*")} if guard.is_dir() else set()
    d = run(demo)
    tag = d["tag"]
    assert (tmp_path / f"{tag}_report.json").is_file()
    assert (tmp_path / f"{tag}_report.md").is_file()
    assert (tmp_path / f"{tag}_decision.json").is_file()
    assert (tmp_path / f"{tag}_evidence_sheet.png").is_file()
    after = {p.name for p in guard.glob("*")} if guard.is_dir() else set()
    assert after == before, "演示跑动了 output/guard/（会污染既有口径）"


def test_page_data_endpoints_serve_the_files_verbatim(demo):
    """判据③的另一半：/api/report 必须原样吐出磁盘上的文件，不许二次加工。

    比的是**字节**：磁盘上的报告按 CRLF 写盘，用文本模式读回来会掩盖换行差异。
    """
    d = run(demo)
    st, body = demo.get(d["files"]["report"])
    assert st == 200
    on_disk = (S.DEMO_DIR / f"{d['tag']}_report.json").read_bytes()
    assert body == on_disk, "接口对报告做了加工 —— 页面看到的必须是产物本身"
    st, dec = demo.get_json(d["files"]["decision"])
    assert st == 200 and dec["tier"] == d["decision"]["tier"]


def test_same_input_same_tag(demo):
    """同输入 → 同 tag → 同文件名：演示也可以复算，不是一次性的。"""
    a = run(demo)
    b = run(demo)
    assert a["tag"] == b["tag"]
    assert a["report"]["fingerprint"] == b["report"]["fingerprint"]


# ------------------------------------------------------------------ 自动演示模式
# 录屏要用的那一层：录制窗口会抢前台焦点，人没法在录屏进行中点击，
# 所以页面需要一种**与人工点选完全等价**的自动跑法（demo/index.html 的 autorun()）。

def test_autorun_is_opt_in_and_touches_nothing_but_the_form():
    """自动演示只在 URL 明确写了 autorun=1 时才动，且只做「填表 + 点核验」。"""
    assert 'q.get("autorun") !== "1"' in HTML, "autorun 不是显式开关（可能默认就跑）"
    body = HTML.split("async function autorun()", 1)[1].split("\nboot()", 1)[0]
    # 只许：填预置语料、勾 explain、点核验 —— 不许绕过 /api/run 自己造结果
    assert "$(\"#fill\").click()" in body and "btn.click()" in body
    assert "fetch(" not in body, "autorun 里出现了 fetch：那是绕过按钮自己发请求"
    for leak in ("innerHTML", "fetch(", "/api/report?", "decision", "tier"):
        assert leak not in body, f"autorun 里出现了 {leak}：自动跑不许碰判定结果"


def test_autorun_waits_for_the_sample_list():
    """样图列表是异步来的：没就绪就点会发出空 image（服务端 400，看着像页面坏了）。"""
    body = HTML.split("async function autorun()", 1)[1].split("\nboot()", 1)[0]
    assert "btn.disabled" in body, "autorun 没检查按钮是否已就绪"
    assert "boot().then(autorun)" in HTML, "autorun 必须在 boot() 之后跑"


def test_autorun_waits_for_the_evidence_image_before_scrolling():
    """滚动必须等证据图加载完。

    图没加载时文档比最终矮，`scrollIntoView` 会被夹到文档底部 ——
    实测：滚到的位置比 `#result` 低约 500px（正好是那张证据图的高度），
    画面于是从表格中间开始，两栏的栏名全在视野之外，录出来的片子读不回承诺字样。
    """
    assert "img.complete" in HTML, "滚动前没等证据图加载完"
    assert 'addEventListener("load", go)' in HTML, "没在证据图 load 之后再滚"
    assert "window.scrollTo" in HTML, "滚动目标必须是算出来的位置，不能靠 scrollIntoView 的夹取行为"


def test_autorun_waits_for_render_to_finish_not_just_the_panel():
    """「渲染完了没有」要靠 render() 结尾挂的标记，不能只看 `#result` 是否隐藏。

    实测（2026-09-21，镜 D1）：`#result` 在 render() 的**第一行**就揭开，
    表格与证据图要到最后一行才填完 —— 那一瞬间文档还矮，滚过去会被夹到文档底部
    （画面停在元数据段，两栏栏名全在视野外）。D2/D3 只是碰巧躲过：同一个 autorun，
    时好时坏，正是"一条不会红的检查等于没有检查"的反面教材。
    """
    assert 'document.documentElement.dataset.rendered = "1"' in HTML, (
        "render() 结尾没挂「渲染完了」标记"
    )
    assert "delete document.documentElement.dataset.rendered" in HTML, (
        "render() 开头没撤标记 —— 第二次跑会读到上一次的旧值，等于不等"
    )
    body = HTML.split("async function autorun()", 1)[1].split("\nboot()", 1)[0]
    assert 'dataset.rendered !== "1"' in body, "autorun 没等「渲染完了」标记（只看 #result 会滚到半成品上）"
    # 标记必须真的在 render() 的头尾，而不是写在别处看着像
    r = HTML.split("function render(data)", 1)[1].split("\nfunction ", 1)[0]
    assert r.index("delete document.documentElement.dataset.rendered") \
        < r.index('document.documentElement.dataset.rendered = "1"'), (
        "标记没夹住 render() 全体：撤标记必须在函数开头、挂标记必须在最后"
    )


def test_autorun_scroll_target_is_selectable():
    """滚到哪一屏要能由 URL 指定：勾 F3 那一镜要看的是 F3 卡片，不是账本。

    卡片在账本**下面**。写死滚账本 → 录出来的那一屏里根本没有模型名，
    而"开源权重档真的跑了"恰恰只能从卡片上读出来。
    """
    assert 'q.get("scroll") === "f3"' in HTML, "滚动目标被写死（F3 卡片永远进不了画面）"
    assert '$("#explain-card")' in HTML, "scroll=f3 没指向 F3 卡片本体"


def test_no_duplicate_element_ids():
    """页面里的 id 必须唯一 —— 重复 id 是**静默**的产出错位，不看渲染结果抓不到。

    实测（2026-09-21，镜 D3）：复选框与 F3 卡片的内容框都叫 `explain`，
    `$("#explain")` 只命中文档里靠前的那个（复选框）→ `clear()` 清的是复选框、
    卡片内容被 append 进 <input>（void 元素，不渲染）→ F3 卡片永远只有标题、
    正文空白，而报告里 explanation 是满的（answer 436 字）。
    "文件在、非黑帧、时长够"三条全绿，只有把画面读回来才看得见。
    """
    import re
    assert 'id="explain-toggle"' in HTML, (
        "F3 勾选框又叫回了 explain（会和卡片内容框撞 id）"
    )
    ids = re.findall(r'\bid="([^"]+)"', HTML)
    dup = sorted({i for i in ids if ids.count(i) > 1})
    assert not dup, f"页面里有重复 id：{dup}（$('#x') 只会命中靠前那个，产出会静默错位）"


def test_autorun_pins_the_viewport_against_stray_input():
    """录制模式下视口必须**钉在目标那一屏**上，而不只是"滚一次"。

    2026-09-21 实测（探针 `experiments/_probe_page_scroll_headful.py`，有头 Edge）：
    置顶的录制窗口照样收得到鼠标/键盘，画面被**平滑滚到文档最底部**
    —— `scrollY` 从 631 一路缓动到 `docH - vh`（= 文档最后一行），
    而页面自己的 `window.scrollTo` **一次都没被调用**（不是本页 JS 干的，所以改 JS 抓不到）。
    后果：D1/D2 的片尾停在「Agent 处置建议」段，承诺的账本字样被推出视野 → 废片。
    pin 只改滚动位置，不碰渲染与判定；且只在 `autorun=1` 下装（人工使用照旧不自己跳）。
    """
    body = HTML.split("async function autorun()", 1)[1].split("\nboot()", 1)[0]
    assert 'addEventListener("scroll"' in body, (
        "autorun 没装滚动 pin：只滚一次挡不住落在置顶窗口上的外部输入"
    )
    assert "Math.abs(window.scrollY - target())" in body, (
        "pin 没有把视口拉回目标位（只是又滚了一次），等于没钉"
    )


def test_autorun_lead_in_is_opt_in_and_off_by_default():
    """`lead=<毫秒>`：开跑前先等一会儿，给录制端留出"量窗口 + 挂 ffmpeg"的时间。

    不给这个前置量，页面会在 t0+1.01s 就把产物落盘（实测三镜一致），片头余量不到
    0.2s —— 机器稍忙，那一镜里就没有"用产品"的过程，只剩一张已经跑完的静态页。
    **默认必须是 0**：人工打开页面时，行为与从前一模一样。
    """
    body = HTML.split("async function autorun()", 1)[1].split("\nboot()", 1)[0]
    assert 'Number(q.get("lead") || "0")' in body, (
        "lead 不是可选参数（默认值被改掉了 → 人工打开页面也会白等）"
    )
    assert "if (lead) await new Promise" in body, "前置等待没有真的等"


# ---------------------------------------------------------------- 镜 D5：两块都要
# 这一镜的**承诺**是"官方点名的另两个维度（光影一致性 / 色彩过渡）都真的量出了数字"。
# round18 的成片正好反证了"期望串只读第一块"有多危险：8.8s 的片子里 `lighting`
# 一次都没出现（同帧比对：t=4s 与 t=7s 完全一样，画面里只有 color_edge 那张表），
# 而当时守卫是**绿的** —— 因为期望串只要第一块出现就算数。
# 附四整卡 ≈1580 CSS px 而捕获视口只有 592 CSS px（见 `demo/index.html` 里 `second()`
# 的算术），一块一屏装不下两块 ⇒ 只能靠**摇镜**让两块各自出现，期望串也必须两块都要。

def _rdp():
    """懒加载页面录制器（`tools/` 不在默认 import 路径上）。"""
    import sys as _sys
    if str(ROOT / "tools") not in _sys.path:
        _sys.path.insert(0, str(ROOT / "tools"))
    import record_demo_page
    return record_demo_page


def _two_block_report() -> dict:
    """合成一份"两块都在"的观察栏产物，形状照抄镜 D5 真读的那份报告。

    刻意在 `color_edge` 里塞了一个 `abstain_median_s_min`（真报告里就有）：
    `"median_s"` 是它的**子串**，按裸子串数唯一性会误判成"命中两处"。
    """
    return {"observations": {"detectors": [
        {"name": "color_edge", "status": "ok", "score": 1.0,
         "measurements": {"n_seams": 1, "median_s": 2.0, "max_seam_de": 14.489,
                          "texture_ratio_max": 22.418, "area_ratio_max": 0.012354,
                          "reliable": True, "abstain_median_s_min": 1.5}},
        {"name": "lighting", "status": "ok", "score": 0.6585,
         "measurements": {"n_blocks": 80, "r_consistency": 0.5413,
                          "theta_main_deg": 177.58, "n_regions": 1,
                          "median_grad": 2.3018, "reliable": True}},
    ]}}


def _token_count(text: str, s: str) -> int:
    """按**独立词元**数出现次数：`abstain_median_s_min` 里的那截 `median_s` 不算。"""
    return len(re.findall(r"(?<![0-9A-Za-z_])" + re.escape(s) + r"(?![0-9A-Za-z_])", text))


def test_d5_expectations_demand_both_observation_blocks():
    """镜 D5 的期望串必须**两块都要**，且每块前 4 项测量都在里面。

    直接调被守卫的函数、不复制字符串 —— 复制的字符串会在函数改动后继续"通过"。
    """
    rdp = _rdp()
    case = next(c for c in rdp.PAGE_DEMO if c["tag"] == "D5")
    rep = _two_block_report()
    exp = rdp.expectations(rep, case)

    for name in ("color_edge", "lighting"):
        assert f"观察维度 {name}（ok）" in exp, (
            f"期望串里没有 {name} 的标题 —— 这一镜承诺的两个维度都量出数字，"
            f"缺一块就只剩一半：{exp}")
    for d in rep["observations"]["detectors"]:
        for k in list(d["measurements"])[:4]:
            assert k in exp, f"{d['name']} 的测量项 {k} 没进期望串：{exp}"
    assert "14.489" in exp and "0.5413" in exp, f"非整浮点值没进期望串：{exp}"

    # 唯一性：数据派生的期望串在**观察栏子树**里只该命中一处 ——
    # 命中多处意味着读回时可能读到别的地方（假命中），要立刻点名，不许静默放过。
    sub = json.dumps(rep["observations"], ensure_ascii=False, sort_keys=True)
    for s in [x for x in exp if not x.startswith("观察维度 ")]:
        n = _token_count(sub, s)
        assert n == 1, f"期望串 {s!r} 在观察栏产物里命中 {n} 处（不是唯一）—— 会假命中"


def test_d5_expectations_refuse_a_single_block():
    """只跑出一块时必须返回**哨兵串**，绝不能返回空列表。

    空列表是这一层最危险的失败形态：`verify` 比的是"期望串是否都被读回"，
    期望为空 ⇒ 没有 miss ⇒ **假通过**（片子照样生成、照样非黑帧、照样有时长）。
    """
    rdp = _rdp()
    case = next(c for c in rdp.PAGE_DEMO if c["tag"] == "D5")
    full = _two_block_report()
    for n in (1, 0):
        one = {"observations": {"detectors": full["observations"]["detectors"][:n]}}
        sent = rdp.expectations(one, case)
        assert sent, f"只跑出 {n} 块时返回了空列表 → verify 会静默通过"
        assert all("观察维度" not in s for s in sent), (
            f"只跑出 {n} 块时仍然点名了某一块的标题 → 读回来就算过，等于没守住：{sent}")


def test_readback_judgement_is_normalized_and_still_falsifiable():
    """读回判据必须**在标点上不敏感**，但**不能**因此变成什么都能过。

    为什么非归一化不可（round18c）：D5 那一带被拒收的**唯一**原因是括号宽度 ——
    页面模板 `demo/index.html:517` 用的是**全角** `（${d.status}）`，而 VL 读回成
    半角 `(ok)` ⇒ 期望串**时对时错**（flaky）。同一带本机 OCR 又把 `（ok）` 读成
    `。幻`、把小数点读成 `．`（`177．58`）⇒ **两条读回通道都会在标点上翻车**。
    判据建在标点上，就是建在沙子上。

    但归一化**不是**放水 —— 这条测试的后半段就是自证伪：改一个数、少一块、
    整屏都不对，都必须立刻红。
    """
    rdp = _rdp()
    case = next(c for c in rdp.PAGE_DEMO if c["tag"] == "D5")
    exp = rdp.expectations(_two_block_report(), case)
    joined = "\n".join(exp)

    # ① 三种读回形态都必须被接住：全角（页面原样）/ 半角（VL）/ CJK 间插空格（OCR）
    #    再加上"半角 + 括号旁有空格"这一种。
    heads = [s for s in exp if s.startswith("观察维度 ")]
    assert heads, f"期望串里没有观察维度标题，这条守卫就守不住东西：{exp}"
    variants = [joined]                    # ① 页面原样（全角括号）
    for s in heads:                        # 形如 `观察维度 color_edge（ok）`
        head, status = s.split("（", 1)
        status = status.rstrip("）")
        for form in (f"{head}({status})",            # ② 半角括号（VL 读回）
                     " ".join(s),                    # ③ 每字间插空格（OCR）
                     s.replace("（", "( ").replace("）", " )")):   # ④ 半角 + 括号旁空格
            variants.append(joined.replace(s, form))
    for v in variants:
        assert rdp.unmet(exp, v) == [], f"读回形态被归一化漏掉：{v!r}"
    # 两个实测过的"读歪"实例：全角小数点 / 半角括号
    assert rdp.unmet(["177.58"], "177．58") == []
    assert rdp.unmet(["lighting（ok）"], "lighting (ok)") == []

    # ② 自证伪（一条不会红的检查等于没有检查）
    assert rdp.unmet(exp, joined) == [], "期望串原样读回却报缺失 —— 判据自相矛盾"
    assert rdp.unmet(exp, joined.replace("lighting", "")) != [], "少了第二块的 h4 还判过"
    assert rdp.unmet(exp, joined.replace("0.5413", "0.5414")) != [], "测量值被改一位还判过"
    assert rdp.unmet(exp, "完全无关的一屏字") != [], "整屏都不对还判过"
    assert rdp.unmet(["（ok）"], "任意读回") != [], "归一化后为空的期望串被静默放过"


def test_autorun_pan_is_opt_in_and_hard_cut():
    """摇镜（`step=<ms>`）必须：**默认不摇**、**硬切**、且只在 `scroll=obs` 时生效。

    三条都不是风格问题：
      · 默认不摇 —— D1–D4 已录成，落点一变那四镜的片尾就换了地方（要重录）；
      · 硬切 —— `:701-703` 的 pin 会在平滑动画中途按**旧**落点把视口拽回，
        摇镜在物理上不可能（round18 实测）；
      · 只在 obs —— 其余镜没有第二块可看，换落点纯属把画面搅乱。
    """
    body = HTML.split("async function autorun()", 1)[1].split("\nboot()", 1)[0]
    assert 'const step = q.get("scroll") === "obs"' in body, (
        "step 不是只在 scroll=obs 时生效的（其余镜会被无端摇一次）"
    )
    assert 'Number(q.get("step") || "0")' in body, "step 不是可选参数（默认值被改掉了）"
    assert 'behavior: "auto"' in body, "落点不是硬切 —— 会被滚动 pin 拽回，摇镜做不到"
    assert "hs[1]" in body, (
        "第二落点没钉在**第二块**（`hs[1]`）的标题上（只滚到卡底会把标题推出视野上沿）"
    )
    assert "return Math.max(0, hs[1].getBoundingClientRect().top" in body, (
        "第二落点没钉死成「第二块」的**表达式**：写回「最后一块」就会跳过中间的 `lighting` —— "
        "附四现在有**三块**（`color_edge` / `lighting` / `noise`），三块时「最后一块」只剩"
        "官方点名两维度里的一个（2026-10-03 修的正是这处漂移：旧断言钉的是 `hs[hs.length - 1]`，"
        "只有两块时才等价于「第二块」，注释里出现同样的串还会把它**喂绿**）"
    )
