"""守卫：M1「能用自己的图」—— 本地零上传通道（round13 B 段）。

为什么要有这一层：`demo/index.html` 之前**零 `input type="file"`**，陌生人只能从下拉里
挑 `samples/` 的合成样图 ⇒ 用品的第一动作「拿我自己的东西试」被堵死。B 段把这条路打开，
同时把三条纪律钉死在测试里：

  ① **零上传**：图只落到本机一个临时目录，跑完**立刻删**（①）、默认路径**零网络**（⑥）。
  ② **页面不篡改判定**：同一张图经上传通道与 samples 通道，判定面必须**逐字节相同**（②，最值钱）。
  ③ **输入边界**：畸形输入一律受控 400 + 中文原因，且服务不崩（③）。
  ④ **不回显路径**：响应体与落盘产物里不许出现本机临时目录名（④）。
  ⑤ 页面承诺句在场（⑤）—— 承诺是文案，但文案也是产品的一部分，必须能被断言。

⚠️ **登记在案的边界（本文件钉住当前态，不是已修）**：报告的 `evidence_files` 字段印的是
演示产物目录（`DEMO_DIR`）的**绝对路径**，这是两个通道**共有**的既有行为（与本次上传无关），
不在本单范围内 ⇒ 本文件不断言"报告里没有任何绝对路径"，只断言"没有**临时目录**"。
另：上传图不做 EXIF 方向归一化（与 CLI 通道同款行为，见第 ② 条的一致性保证）。
"""

from __future__ import annotations

import base64
import json
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo import serve as S                                                 # noqa: E402

HTML = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")

TEXT = "7 天根除痘印，我用完就消失了，姐妹们亲测有效"
SAMPLE = "samples/fw_splice.jpg"
OTHER = "samples/base_neutral.jpg"

#: 判定面：与 `tests/test_demo_page.py:37` 同一份清单（演示层不许碰的字段）。
VERDICT_KEYS = ("fingerprint", "verdict", "evidence", "benign", "provenance",
                "detectors", "comment_verdict")

#: 归一化时剥掉的易变键 —— 它们必然逐次不同（产物路径、时间戳），留着会让对拍永远红。
VOLATILE = ("generated_at", "evidence_files", "content_id", "path", "image_path",
            "duration", "elapsed")


# ---------------------------------------------------------------- 客户端 / 服务

class Client:
    """本机 HTTP 客户端（**绕开环境代理** —— ⑥ 会把代理指向死端口）。"""

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
    """真服务（随机端口）+ **可控临时区**。

    把 `tempfile.tempdir` 指到测试自己的目录，是 ① 的关键手法：这样"临时目录空了"
    这句话是**真读出来的**（面试级证据），而不是靠偷看实现里那个变量名。
    `mkdtemp` 上加的探针只为自证伪 —— 若上传通道**根本没落过盘**，"临时区是空的"
    就是一句空转的真话（一条不会红的检查等于没有检查）。
    """
    monkeypatch.setattr(S, "DEMO_DIR", tmp_path)
    monkeypatch.setattr(S, "_ASK", None)
    monkeypatch.setattr(S, "_AGENT_ASK", None)

    tmp_root = tmp_path / "tmp_zone"
    tmp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_root))
    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def spy(*a, **k):
        p = real_mkdtemp(*a, **k)
        created.append(Path(p))
        return p

    monkeypatch.setattr(tempfile, "mkdtemp", spy)

    httpd = S.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield {"client": Client(f"http://127.0.0.1:{httpd.server_address[1]}"),
               "tmp_root": tmp_root, "created": created}
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------- 载荷构造

def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _upload(raw: bytes | None = None, *, name: str = "my_photo.jpg", **kw) -> dict:
    """页面在上传通路里实际发出的请求体形态（`demo/index.html` 的 #run 处理器同款）。"""
    raw = raw if raw is not None else (ROOT / SAMPLE).read_bytes()
    p = {"image_b64": _b64(raw), "image_name": name,
         "text": TEXT, "comments": "", "explain": False}
    p.update(kw)
    return p


def _sample(rel: str = SAMPLE, **kw) -> dict:
    p = {"image": rel, "text": TEXT, "comments": "", "explain": False}
    p.update(kw)
    return p


def _norm(o):
    """剥易变键 —— 与 `tests/test_demo_page.py` / `_judgment_invariance.py:_norm` 同款口径。"""
    if isinstance(o, dict):
        return {k: _norm(v) for k, v in sorted(o.items())
                if not any(s in k.lower() for s in VOLATILE)}
    if isinstance(o, list):
        return [_norm(x) for x in o]
    return o


def _judge(report: dict) -> dict:
    return {k: _norm(report.get(k)) for k in VERDICT_KEYS}


# ---------------------------------------------------------------- ① 跑完即删

def test_upload_leaves_no_temp_dir_behind(demo):
    """图落本机临时目录 → 跑完**立刻删**。这是"图不出本机"里最实的一条。

    自证伪（怎么改会红）：把 `run_once` 里 `finally: shutil.rmtree(...)` 那一行注释掉
    ⇒ `left` 非空 ⇒ 红。
    """
    st, d = demo["client"].post(_upload())
    assert st == 200 and d["ok"], f"上传核验失败 {st}：{d}"

    assert demo["created"], (
        "上传通道一个临时目录都没落过 —— 本测试的'空'是空转，"
        "说明请求根本没走上传分支（或实现换了别的落盘方式）")
    left = [p for p in demo["created"] if p.exists()]
    assert not left, f"临时目录/文件没删干净：{left}"
    assert list(demo["tmp_root"].iterdir()) == [], (
        f"临时区还有残留：{[p.name for p in demo['tmp_root'].iterdir()]}")


def test_a_failed_run_also_cleans_up(demo):
    """**跑挂了也要删**（`finally` 而不是顺序语句）。

    怎么改会红：把删除从 `finally` 挪到 `return` 之前 ⇒ 本测试里引擎抛异常那条路
    就不会删 ⇒ 红。上传的是**真图但引擎不认的输入**走不到这里，所以用"文件名合法、
    内容是乱码"的图触发一次失败——它在 ③ 里被 400 拦下，而这里要的是**跑起来了但失败**：
    用一个 `text` 超长的请求（在 build_item 阶段就抛 BadRequest）即可。
    """
    st, d = demo["client"].post(_upload(text="x" * (S.MAX_TEXT + 10)))
    assert st == 400, f"超长文案没有受控拒绝：{st} {d}"
    left = [p for p in demo["created"] if p.exists()]
    assert not left, f"请求被拒后临时目录没删：{left}"


# ---------------------------------------------------------------- ② ⭐ 判定面逐字节相同

def test_upload_channel_judges_exactly_like_the_sample_channel(demo):
    """⭐ **同一张图：上传通道的判定面 == samples 通道的判定面**。

    这条是 B 段最值钱的一条：它证明"页面不篡改判定"。上传来的是**同一份字节**，
    所以连指纹都必须一样；判定面（tier / 证据 / 归因 / 检测器实测值）必须逐字节相同。

    怎么改会红：让上传通路多传或少传一个开关（例如上传时顺手把 `observe` 打开、
    或把 `explain` 默认成 True）⇒ 判定面出现差异 ⇒ 红。

    非空转的两个自证伪：
      * 归一化确实在干活 —— 两份**原始** JSON 必须**不同**（内容是同一张图，
        差异只该来自路径/时间戳这类易变键）；
      * 判定哈希确实对判定敏感 —— 换一张图（真拼接 → 良性底图）必须得到**不同**的 sha，
        否则"相同"可能只是因为哈希对判定瞎。
    """
    raw = (ROOT / SAMPLE).read_bytes()
    st_a, a = demo["client"].post(_sample())
    st_b, b = demo["client"].post(_upload(raw=raw, name="fw_splice.jpg"))
    assert (st_a, st_b) == (200, 200), f"某一个通道没跑通：{st_a} {st_b}"
    assert a["report"]["fingerprint"] == b["report"]["fingerprint"], (
        "两个通道算出的物证指纹不同 —— 它们说的不是同一张图")

    ja, jb = _judge(a["report"]), _judge(b["report"])
    assert json.dumps(ja, sort_keys=True, ensure_ascii=False) == \
           json.dumps(jb, sort_keys=True, ensure_ascii=False), (
        "上传通道的判定面与 samples 通道不一致 —— 页面/服务动了判定链（铁律 3）")

    # 非空转①：归一化在干活。
    assert json.dumps(a["report"], sort_keys=True) != json.dumps(b["report"], sort_keys=True), (
        "两份原始报告一模一样 —— 归一化没在剥任何东西，上面的相等是「拿同一份比同一份」")
    # 非空转②：哈希对判定敏感（换张图必须换 sha）。
    st_c, c = demo["client"].post(_upload(raw=(ROOT / OTHER).read_bytes(), name="other.jpg"))
    assert st_c == 200, c
    assert json.dumps(_judge(c["report"]), sort_keys=True) != \
           json.dumps(jb, sort_keys=True), (
        "换了一张图（真拼接 → 良性底图）判定面却没变 —— 这个哈希对判定是瞎的")
    # 判定面确实有值（不是拿空比空）。
    assert jb["verdict"].get("tier"), "上传通道的判定面里没有等级 ⇒ 上面比的是空值"


# ---------------------------------------------------------------- ③ 畸形输入受控

def _bad_cases():
    good_jpeg = (ROOT / SAMPLE).read_bytes()
    return [
        ("非 base64", {"image_b64": "!!!这不是 base64!!!", "image_name": "a.jpg"}),
        ("零字节", {"image_b64": "", "image_name": "a.jpg"}),
        ("超大", {"image_b64": "A" * (S.MAX_IMAGE_B64 + 8), "image_name": "a.jpg"}),
        ("非图像", {"image_b64": _b64("<html>我不是图片</html>".encode("utf-8")), "image_name": "a.jpg"}),
        ("后缀不在白名单", {"image_b64": _b64(good_jpeg), "image_name": "payload.exe"}),
        ("文件名带 ../", {"image_b64": _b64(good_jpeg), "image_name": "../../evil.jpg"}),
        ("文件名带反斜杠", {"image_b64": _b64(good_jpeg), "image_name": "..\\evil.jpg"}),
        ("文件名过长", {"image_b64": _b64(good_jpeg),
                    "image_name": "x" * (S.MAX_IMAGE_NAME + 5) + ".jpg"}),
    ]


@pytest.mark.parametrize("why,payload", _bad_cases(), ids=[c[0] for c in _bad_cases()])
def test_malformed_uploads_are_controlled_400s(demo, why, payload):
    """畸形输入 → 受控 400 + **中文原因**；且服务**不崩**（后面还能正常跑）。

    怎么改会红：摘掉大小闸（`MAX_IMAGE_B64`）⇒"超大"那条会往下走，或抛 500、或真去
    解一个 8 MB 的假图 ⇒ 红；把文件名当路径用 ⇒"文件名带 ../"那条会越出临时目录。

    ⚠️ 只看 400 是不够的：一个"任何输入都 400"的实现也能过 —— 所以最后必须再跑一次
    正常上传（绿），证明这一串 400 是**判据**而不是"坏掉了"。
    """
    st, d = demo["client"].post(_upload(**payload) if False else payload)
    assert st == 400, f"{why}：期望受控 400，实际 {st} {d}"
    assert d.get("ok") is False, d
    msg = str(d.get("error") or "")
    assert re.search(r"[一-鿿]", msg), f"{why}：错误原因不是中文人话：{msg!r}"
    assert "Traceback" not in msg and "Error" not in msg, (
        f"{why}：把 Python 异常原文回显给用户了：{msg!r}")

    # 不崩：紧接着一次正常上传必须照常出账本。
    st2, d2 = demo["client"].post(_upload())
    assert st2 == 200 and d2["ok"], f"{why} 之后服务不能正常工作了：{st2} {d2}"


@pytest.mark.parametrize("why,raw", [
    ("坏 JSON", b"{bad json"),
    ("不是 UTF-8", b'{"image_b64": "\xff\xfe \x80\x81"}'),
], ids=["bad-json", "not-utf8"])
def test_unparsable_body_is_a_client_error_not_a_server_fault(demo, why, raw):
    """请求体**解不开**（坏 JSON / 非 UTF-8）⇒ 400，不是 500（round14b 问题 1）。

    为什么要在这一层钉死：请求体是**客户端**发错的，归 500 等于把"用户输入不合法"登记成
    "服务端故障" —— 页面会显示"核验过程中出错了，这一跑没有出账本"，把人的注意力引向引擎，
    而引擎**根本没跑**；终端还会打出一段误导性堆栈。

    怎么改会红：把 `do_POST` 里新加的那条 `except (json.JSONDecodeError, UnicodeDecodeError)`
    去掉（其余不动）⇒ 坏 JSON 落进兜底分支 ⇒ 实测 500 ⇒ 红。

    ⚠️ 只看 400 不够（"任何输入都 400"的实现也能过）：末尾必须再跑一次**正常上传**（绿）。
    """
    req = urllib.request.Request(
        demo["client"].base + "/api/run", data=raw,
        headers={"Content-Type": "application/json"})
    st, body = demo["client"]._open(req)
    assert st == 400, f"{why}：期望受控 400，实际 {st} {body[:200]!r}"
    try:
        d = json.loads(body)
    except json.JSONDecodeError:                            # 崩溃时把原文带出来
        raise AssertionError(f"{why}：400 的响应体不是 JSON：{body[:200]!r}") from None
    assert d.get("ok") is False, d
    msg = str(d.get("error") or "")
    assert re.search(r"[一-鿿]", msg), f"{why}：错误原因不是中文人话：{msg!r}"
    assert "Traceback" not in msg and "Error" not in msg, (
        f"{why}：把 Python 异常原文回显给用户了：{msg!r}")

    # 不崩：紧接着一次正常上传必须照常出账本。
    st2, d2 = demo["client"].post(_upload())
    assert st2 == 200 and d2["ok"], f"{why} 之后服务不能正常工作了：{st2} {d2}"


def test_upload_wins_over_the_dropdown_when_both_are_present(demo):
    """两个都给了（页面在下拉里选了样图、又拖了本地图）⇒ **以本地图为准**，且如实回报来源。

    怎么改会红：把上传分支改成 `if payload.get("image")` 优先 ⇒ 本测试红（指纹会是样图的）。
    """
    raw = (ROOT / OTHER).read_bytes()
    st, d = demo["client"].post(_upload(raw=raw, name="mine.jpg", image=SAMPLE))
    assert st == 200 and d["ok"], d
    assert d["report"]["fingerprint"] == __import__("hashlib").sha256(raw).hexdigest(), (
        "给了本地图却按下拉里的样图判 —— 用户会以为看到的是自己的图")
    assert d["inputs"]["image"] == "mine.jpg", d["inputs"]


# ---------------------------------------------------------------- ④ 不回显路径

def test_neither_response_nor_artifacts_echo_the_temp_path(demo):
    """响应体与落盘产物里都不许出现**本机临时目录**（页面上的「报告 JSON」链接就读它）。

    怎么改会红：在 `run_once` 的返回里加一行 `"tmp": str(tmpdir)`（或干脆不脱敏，
    把引擎算出来的 `image_path` 原样回显）⇒ 红。
    """
    st, d = demo["client"].post(_upload(name="我的自拍.jpg"))
    assert st == 200 and d["ok"], d

    assert demo["created"], "没落过临时目录 ⇒ 本测试没有可检查的对象（空转）"
    tmp_dir = demo["created"][0]
    body = json.dumps(d, ensure_ascii=False)
    assert str(tmp_dir) not in body, "响应体里回显了临时目录的绝对路径"
    assert tmp_dir.name not in body, "响应体里回显了临时目录名"
    assert str(demo["tmp_root"]) not in body, "响应体里回显了本机临时区路径"

    rep = d["report"]
    assert rep.get("image_path") == "我的自拍.jpg", (
        f"报告记录的 image_path 应当是展示用文件名，实际是 {rep.get('image_path')!r}")

    for path in (f"/api/report?tag={d['tag']}", f"/api/markdown?tag={d['tag']}"):
        st2, raw = demo["client"].get(path)
        assert st2 == 200, f"{path} 取不到：{st2}"
        text = raw.decode("utf-8")
        assert str(tmp_dir) not in text and tmp_dir.name not in text, (
            f"{path} 的产物里印着临时目录：{tmp_dir}")


# ---------------------------------------------------------------- ⑤ 页面承诺句

def test_the_page_promises_the_image_never_leaves_the_machine():
    """页面必须明写这句承诺，并且真的给出入口（承诺句 + 拖入区 + 下拉并存）。

    怎么改会红：删掉「你的图片不会离开你的机器」那句 ⇒ 红。
    """
    assert "不离开你的机器" in HTML, "页面没有「图不出本机」的承诺句"
    assert "不上传、不联网" in HTML, "承诺句没有把「不上传、不联网」写出来"
    assert 'type="file"' in HTML and 'id="local-img"' in HTML, "页面没有本地图片选择入口"
    assert 'id="drop"' in HTML, "页面没有拖入区"
    assert re.search(r'accept="[^"]*\.jpe?g[^"]*"', HTML), "file 输入没有限制可选后缀"
    assert 'id="samples"' in HTML, "samples/ 下拉不见了（无图也要能演示）"
    # 承诺句与首屏口径一致（A-1 的主张层就是这句的同义改写）。
    assert "不判真假 · 图不出本机 · 结论可复算" in HTML, "首屏主张层与承诺句不同源"


# ---------------------------------------------------------------- ⑥ 默认零网络

def test_upload_run_needs_no_network_by_default(demo, monkeypatch):
    """拔网线照出账本：上传通道的默认路径同样**零模型、零网络**。

    怎么改会红：在上传分支里顺手把 `explain` 打开（"反正图都传上来了"）⇒
    出口代理指向死端口时这一步会失败 ⇒ 报告里 explanation 非空/状态不对 ⇒ 红。
    """
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(k, "http://127.0.0.1:9")
    st, d = demo["client"].post(_upload())
    assert st == 200 and d["ok"], f"断网时上传通道跑不完：{st} {d}"
    assert d["report"]["explanation"] == {}, "默认路径不该产出任何模型产物"
    assert d["report"]["verdict"]["tier"], "断网时等级必须照出"
    assert d["files"]["sheet"], "断网时证据图必须照出"
    assert d["inputs"]["explain"] is False and d["inputs"]["agent"] is False
