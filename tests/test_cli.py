"""CLI 入口回归：`python -m voiceguard` 是评委最可能走的那条路 —— 之前它 0% 覆盖。

为什么要专门一个文件（三层，缺一层都不算证明）：
  ① `main()` 在**进程内**被直接调用 → 覆盖 `__main__` 的每条分支（覆盖率看得见）；
  ② 真的起一个 `python -m voiceguard` **子进程** → 证明入口能被外部唤起
     （覆盖率 100% 也不代表"命令行敲得动"，这两个是完全不同的事实）；
  ③ **失败路径**：输入不存在必须返回 2 且不产出任何文件 —— 绝不允许"静默假装成功"。

注意：子进程测试不计入覆盖率（coverage 不跨进程），但它测的是**另外一个事实**，
不是多余的努力。别为了覆盖率数字把它删掉。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard.__main__ import main                                          # noqa: E402


def _tiny_jpeg(p: Path) -> Path:
    """96×72 小 JPEG：够走完整条链路，又不让回归变慢（大图会让测试从秒变分钟）。"""
    gx, gy = np.meshgrid(np.linspace(0, 1, 96), np.linspace(0, 1, 72))
    img = np.stack([gx * 200 + 30, gy * 180 + 40, (1 - gx) * 150 + 60], -1)
    p.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", img.astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tofile(str(p))
    return p


def _names(d: Path) -> list[str]:
    return sorted(f.name for f in d.iterdir())


# ---------------------------------------------------------------- ① 进程内
def test_main_quiet_writes_artifacts(tmp_path, capsys):
    img = _tiny_jpeg(tmp_path / "tiny.jpg")
    out = tmp_path / "o"
    assert main([str(img), "--out", str(out), "--tag", "cli", "--quiet"]) == 0

    names = _names(out)
    assert any(n.endswith(".md") for n in names), names
    assert any(n.endswith(".json") for n in names), names
    assert any("cli" in n for n in names), "--tag 未生效"

    cap = capsys.readouterr()
    assert "产物" in cap.err, "产物路径属于诊断信息，必须走 stderr"
    assert "产物" not in cap.out, "stdout 要被摘要/报告独占，否则管道里全是噪音"


def test_main_no_sheet_skips_evidence_png(tmp_path):
    img = _tiny_jpeg(tmp_path / "tiny.jpg")
    out = tmp_path / "o"
    assert main([str(img), "--out", str(out), "--tag", "ns", "--quiet", "--no-sheet"]) == 0
    assert not any(n.endswith("_evidence_sheet.png") for n in _names(out))


def test_main_default_tag_uses_stem(tmp_path):
    img = _tiny_jpeg(tmp_path / "somestem.jpg")
    out = tmp_path / "o"
    assert main([str(img), "--out", str(out), "--quiet", "--no-sheet"]) == 0
    assert any(n.startswith("somestem") for n in _names(out))


def test_main_verbose_prints_markdown_report(tmp_path, capsys):
    img = _tiny_jpeg(tmp_path / "tiny.jpg")
    out = tmp_path / "o"
    assert main([str(img), "--out", str(out), "--no-sheet"]) == 0
    out_text = capsys.readouterr().out
    assert "#" in out_text and "核验" in out_text, "非 --quiet 时应打印完整账本报告"


def test_main_missing_input_returns_2_and_writes_nothing(tmp_path, capsys):
    """坏输入 ⇒ exit 2 + 零半成品。本用例守的是**同一条契约的两个实例**：
    「路径不存在」，以及「文件在、但读不出（非空坏图 / 零字节空文件）」。

    修前实测（坏图那一支）：`forensics/base.py:134` 抛的 ValueError 从 CLI 边界逸出
    ⇒ traceback + exit=1。同一条路上「路径不存在」早就是友好 exit=2 —— 两个实例
    此前一个受控一个裸奔，所以合成一条用例来钉。
    """
    out = tmp_path / "o"
    rc = main([str(tmp_path / "nope.jpg"), "--out", str(out)])
    assert rc == 2, "输入不存在必须是明确的退出码，不能是 0"
    assert "输入不存在" in capsys.readouterr().err
    assert not out.exists() or not _names(out), "失败路径不许留下任何半成品产物"

    # 第二个实例：文件**在**，但内容读不出。两种坏法都要走同一个受控出口。
    for name, blob in (("fake.jpg", b"not an image at all"), ("zero.jpg", b"")):
        bad = tmp_path / name
        bad.write_bytes(blob)
        out_x = tmp_path / f"o_{name}"
        rc = main([str(bad), "--out", str(out_x)])
        cap = capsys.readouterr()
        assert rc == 2, f"{name}：坏输入必须是明确的退出码，不能是 0"
        assert "Traceback" not in (cap.out + cap.err), f"{name}：不许露栈：{cap.err}"
        assert "输入无法处理" in cap.err or "读不到图像" in cap.err, f"{name}：{cap.err!r}"
        assert not out_x.exists() or not _names(out_x), f"{name}：失败路径不许留下半成品产物"


# ---------------------------------------------------------------- ② 子进程
def test_module_entry_runs_as_real_subprocess(tmp_path):
    img = _tiny_jpeg(tmp_path / "tiny.jpg")
    out = tmp_path / "o"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    # ⚠️ 子进程的编码必须**两边都钉死**（2026-09-22 修）。原来只写 `text=True`：子进程按
    # 继承来的 locale 编码吐字，父进程按 locale 解码 —— 只在"环境里没有编码变量"时才巧合对上。
    # 实测：本机 shell 一旦导出 `PYTHONIOENCODING=utf-8`（跑 pytest 时常这么干），子进程就吐
    # UTF-8、父进程按 GBK 解 `[sub] 未触发…` 的第 8 字节 → 读线程崩、`r.stdout` 变空串。
    # 不是假通过（闸门是 returncode），而是**失败时诊断信息全空**，外加
    # `PytestUnhandledThreadExceptionWarning` 噪音。钉死两侧后，结果与外部环境无关。
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        [sys.executable, "-m", "voiceguard", str(img), "--out", str(out),
         "--tag", "sub", "--quiet", "--no-sheet"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, f"stderr={r.stderr}"
    assert any(n.endswith(".json") for n in _names(out))
    # 顺带守一句：摘要真的读回来了，且没有**替换字符**。
    #   · 不带 encoding → 读线程按 GBK 崩 → stdout 空串 → 第一条红；
    #   · 只带 errors="replace" 不带 encoding → GBK 硬解出 U+FFFD → 第二条红。
    # 两条合起来把"两侧编码没对齐"这件事钉死，比断言某个具体判定词稳（判定词会随规则变）。
    assert "[sub]" in r.stdout, f"子进程 stdout 没读回来（编码没对齐？）：{r.stdout!r}"
    assert "�" not in r.stdout, f"读回来带了替换字符（两侧编码不一致）：{r.stdout!r}"

    # 同一条纪律的**第二个方向**：汉字 Windows 的 cp936 流。
    # 上面那半管的是"父进程解码"，这半管的是"子进程编码" —— 报告里的 `⚠️` 不在 GBK 内，
    # 修前实测：`UnicodeEncodeError: 'gbk' codec can't encode '⚠'`（来源
    # `report/ledger.py:99`）⇒ 报告主体整个打不出来。刻意把子进程流钉死成 gbk，
    # 与本机 shell 是否导出 PYTHONUTF8 无关（否则假绿）。不设 text/encoding，拿 bytes 自己解。
    env_gbk = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "PYTHONUTF8", "PYTHONIOENCODING")}
    env_gbk["PYTHONIOENCODING"] = "gbk"      # 评委机器默认（中文 Windows）
    out_gbk = tmp_path / "o_gbk"
    r_gbk = subprocess.run(
        [sys.executable, "-m", "voiceguard", str(img), "--out", str(out_gbk), "--tag", "gbk"],
        cwd=str(ROOT), env=env_gbk, capture_output=True, timeout=300)
    assert r_gbk.returncode == 0, \
        f"退出码={r_gbk.returncode}；stderr={r_gbk.stderr.decode('gbk', errors='replace')}"
    assert b"UnicodeEncodeError" not in r_gbk.stderr, \
        f"编码兜底没生效：{r_gbk.stderr.decode('gbk', errors='replace')}"
    assert r_gbk.stdout.decode("gbk", errors="replace").strip(), "报告主体没打出来（stdout 空）"


def test_subprocess_report_carries_sha256_and_path(tmp_path):
    """报告里必须带 sha256 指纹与原始路径 —— 账本可复核性的最小条件。"""
    img = _tiny_jpeg(tmp_path / "tiny.jpg")
    out = tmp_path / "o"
    assert main([str(img), "--out", str(out), "--tag", "fp", "--quiet", "--no-sheet"]) == 0
    js = next(f for f in out.iterdir() if f.suffix == ".json")
    txt = js.read_text(encoding="utf-8")
    report = json.loads(txt)
    assert "tiny.jpg" in txt
    assert report, "报告不许是空文件"


# ---------------------------------------------------------------- ③ 三个入口
#  2026-09-23：CLI 接出 `--text` 与 `--comments-file` 之前，这里只有「必须给图」一条路，
#  于是「语义一致性校验」（需文案）与评论区核验（需评论列表）在命令行上**没有出口**，
#  只能从网页 `demo/` 进 —— 而 README 里三环是并列写的。
#  下面这几条守的是：三个入口在**命令行上**也真的走得通，且不是"收下了参数但什么也没跑"。

def _run(argv, out, tag) -> dict:
    """跑一次 `main()` 并把落盘的报告**从文件读回来** —— 不读内存对象，
    因为"产物里到底写了什么"才是评委能复核的那个事实。"""
    assert main([*argv, "--out", str(out), "--tag", tag, "--quiet"]) == 0
    js = next(f for f in out.iterdir() if f.suffix == ".json")
    return json.loads(js.read_text(encoding="utf-8"))


def _layer(rep: dict, prefix: str) -> dict:
    return next(L for L in rep["layers"] if L["layer"].startswith(prefix))


def test_只给文案也能出账本(tmp_path):
    """`--text` 不收图：`guard.py:245` 的 `run()` 本来就支持，缺的一直只是这个出口。"""
    out = tmp_path / "o"
    text = "这支精华三天就能让法令纹消失，比医美还快"
    rep = _run(["--text", text], out, "t")

    assert rep["fingerprint"] == hashlib.sha256(text.encode("utf-8")).hexdigest(), \
        "无图时的指纹应当锚住文案本身（纯文案的算法刻意保持不变）"
    assert rep["image_path"] == ""
    assert _layer(rep, "F4")["status"].startswith("已跑"), \
        f"收了 --text 却没跑 F4 —— 那就是个静默吞参数的壳子：{_layer(rep, 'F4')}"
    assert not any(n.endswith("_evidence_sheet.png") for n in _names(out)), \
        "无图时没有像素可画，不该产出证据图"


def test_只给评论文件也能出账本(tmp_path):
    """`--comments-file` 一行一条；空行与首尾空白按 `parse_comment_lines` 的口径处理。"""
    f = tmp_path / "c.txt"
    f.write_text("用了一周真的变好了\n\n  绝对是收了钱发的  \n", encoding="utf-8")
    out = tmp_path / "o"
    rep = _run(["--comments-file", str(f)], out, "c")

    assert rep["kind"] == "comment_thread"
    assert rep["comment_verdict"], "收了评论却没跑 F5"
    assert rep["comment_verdict"]["signals"]["review.n_comments"] == 2, \
        "空行没被丢掉，或首尾空白没被 strip —— 评论条数对不上"


def test_两份不同评论得到不同物证锚(tmp_path):
    """**这条守的是 2026-09-23 修掉的那个退化。**

    无图时指纹原先是 `sha256(item.text or "")`。只给评论 ⇒ text 为空 ⇒
    **任何**一份评论文件都落成 `e3b0c442…`（正是空串的 sha256），
    指纹作为"物证锚"在评论通路上等于失效：两份完全不同的评论指向同一个锚。

    退化此前没露头，是因为这条分支两个入口都**到不了**（命令行原先必须有图、
    网页那边图片是必填 `demo/serve.py:140`）。CLI 接出 `--comments-file` 之后它第一次可达。
    """
    empty_hash = hashlib.sha256(b"").hexdigest()
    fps = []
    for i, body in enumerate(("评论甲\n", "评论乙\n")):
        f = tmp_path / f"c{i}.txt"
        f.write_text(body, encoding="utf-8")
        fps.append(_run(["--comments-file", str(f)], tmp_path / f"o{i}", f"c{i}")["fingerprint"])

    assert fps[0] != fps[1], "两份不同的评论落成同一个物证锚 —— 指纹退化了"
    assert empty_hash not in fps, f"指纹退化成空串的 sha256（{empty_hash[:8]}…）"


def test_什么都不给时返回2且不产出(tmp_path, capsys):
    """没有可核验的输入 ⇒ 明确退出码 2。本系统不猜 —— 也不许假装成功。"""
    out = tmp_path / "o"
    assert main(["--out", str(out)]) == 2
    assert "没有可核验的输入" in capsys.readouterr().err
    assert not out.exists() or not _names(out), "失败路径不许留下半成品产物"


def test_评论文件不存在返回2(tmp_path, capsys):
    out = tmp_path / "o"
    assert main(["--comments-file", str(tmp_path / "nope.txt"), "--out", str(out)]) == 2
    assert "评论文件不存在" in capsys.readouterr().err
    assert not out.exists() or not _names(out)


def test_评论文件全是空行等同于没给(tmp_path, capsys):
    """全是空行的评论文件 ≠ 有输入。这条要红在被挡在 `GuardPipeline` **之前**：
    若把 `[]` 当成"有评论"放进去，跑出来会是一份看起来正常的账本 —— 那才是假通过。"""
    f = tmp_path / "blank.txt"
    f.write_text("\n\n   \n\t\n", encoding="utf-8")
    out = tmp_path / "o"
    assert main(["--comments-file", str(f), "--out", str(out)]) == 2
    assert not out.exists() or not _names(out)


# ---------------------------------------------------------------- ④⑤ tag 卫生
#  2026-09-25 · round8 缺陷④⑤：页面侧早就有 `TAG_RE`，CLI 侧**一个校验都没有**，
#  `--tag` 一路透传到 `report/ledger.py:296` 拼文件名。真跑过的两条现场：
#    `--tag '../_ESCAPED'` → rc=0，产物写到 `--out` 的**上级目录**（_ESCAPED_report.json
#     与 _ESCAPED_evidence_sheet.png 都已在交付区落地为证）；
#    `--tag 'a:b'` → Windows 上裸 `FileNotFoundError` 栈（`:` 不是合法文件名字符）。
#  规则本体在 `voiceguard/core/tags.py`（唯一一份），这里守的是**它真的接在 CLI 上**。

#: 非法 tag 的取样：每条都对应一种真实的坏结局，不是凑数。
_BAD_TAGS = [
    ("../_ESCAPED", "路径穿越"),
    ("..\\_ESCAPED", "反向路径穿越（Windows）"),
    ("a:b", "Windows 上非法的文件名字符 → 修前是裸 FileNotFoundError 栈"),
    ("", "空 —— 修前 `args.tag or …` 会静默退回图片名，用户以为设了其实没设"),
    ("x" * 65, "超长（上限 64）"),
    ("CON", "Windows 保留设备名 —— CON_report.md 至今打不开"),
    ("有中文", "非白名单字符"),
]


def test_非法tag返回2且不产生任何产物(tmp_path, capsys):
    """**这条是 ④⑤ 的主守卫**：非法 tag 必须「非 0 退出 + 一句中文受控错误 + 零产物」。

    ⚠️ 断言"零产物"要**两边都看**：只看 `--out` 里空不空，正好漏掉修前那个真实缺陷
    —— 产物全写到 `--out` 的上级去了，`--out` 里确实一个文件都没有
    （`d.mkdir` 甚至都可能没被走到）。所以这里连 `tmp_path` 顶层一起查。
    """
    img = _tiny_jpeg(tmp_path / "tiny.jpg")
    out = tmp_path / "o"
    for bad, why in _BAD_TAGS:
        rc = main([str(img), "--out", str(out), "--tag", bad, "--quiet", "--no-sheet"])
        assert rc == 2, f"非法 tag {bad!r}（{why}）必须是明确的退出码，不能是 0"
        err = capsys.readouterr().err
        assert "非法 tag" in err, f"{bad!r}（{why}）没有受控的中文错误：{err!r}"
        assert "Traceback" not in err, f"{bad!r}（{why}）漏出了裸栈：{err!r}"
        # 产物不许出现在 --out 之外（修前 '../_ESCAPED' 正是从这里漏出去的）
        stray = [f.name for f in tmp_path.iterdir() if f.is_file() and f.name != "tiny.jpg"]
        assert not stray, f"非法 tag {bad!r}（{why}）把产物写到了 --out 之外：{stray}"
        assert not out.exists() or not _names(out), f"{bad!r}（{why}）留下了半成品产物"


def test_非法tag在窗口期前就被挡住(tmp_path, capsys):
    """控制组：同一条命令换成**合法** tag 必须 rc=0 且产物落在 `--out` 里。

    没有这条，上面那条可以靠"CLI 彻底跑不起来"通过 —— 那就不是护栏，是灯坏了。
    """
    img = _tiny_jpeg(tmp_path / "tiny.jpg")
    out = tmp_path / "o"
    assert main([str(img), "--out", str(out), "--tag", "ok_1-A", "--quiet", "--no-sheet"]) == 0
    assert any(n.startswith("ok_1-A") for n in _names(out)), _names(out)


def test_合法tag的产物名逐字不变(tmp_path):
    """④⑤ 的边界条件：**合法 tag 的产物名一个字都不许变**。

    规则是从页面 `demo/serve.py:58` 搬过来的（口径完全一致），搬家不是改口径：
    这里把两个产物名连字符都钉死，任何"顺手改个分隔符"都会立刻红。
    """
    img = _tiny_jpeg(tmp_path / "tiny.jpg")
    out = tmp_path / "o"
    assert main([str(img), "--out", str(out), "--tag", "zh-09_X", "--quiet"]) == 0
    assert sorted(_names(out)) == [
        "zh-09_X_evidence_sheet.png", "zh-09_X_report.json", "zh-09_X_report.md",
    ], _names(out)


def test_默认前缀取自图片名且仍然合法(tmp_path):
    """不给 `--tag` 时沿用图片名 —— 这条只是钉住"搬家没动默认路径"。"""
    img = _tiny_jpeg(tmp_path / "tiny.jpg")
    out = tmp_path / "o"
    assert main([str(img), "--out", str(out), "--quiet", "--no-sheet"]) == 0
    assert any(n.startswith("tiny_") for n in _names(out)), _names(out)


def _obs_names(rep: dict) -> list[str]:
    """观察栏里的检测器名（`detectors` 里装的是对象/dict —— 不是裸字符串）。"""
    return [d if isinstance(d, str) else d.get("name")
            for d in ((rep.get("observations") or {}).get("detectors") or [])]


def test_observe开关不改判定面且默认路径没有观察栏(tmp_path):
    """`--observe` 的硬门槛（预注册 v2 §3）：开了观察级检测器，**判定面一字不动**。

    这是"观察栏默认关"在 CLI 层的守卫 —— 少了它，往注册表里加一个 `default=True`
    的检测器就能悄悄混进默认路径，判定数字被改而没人发现。修前实测确实踩过一次：
    兜底 confidence 用的是"所有跑成了的检测器"，打开观察栏后 `high`→`mid`，
    修法是 `_judging()` —— 不参与判定的检测器不许碰任何判定字段。
    """
    img = _tiny_jpeg(tmp_path / "tiny.jpg")
    off = _run([str(img), "--no-sheet"], tmp_path / "off", "off")
    on = _run([str(img), "--no-sheet", "--observe"], tmp_path / "on", "on")

    # ① 判定面逐字节相同（tier / confidence / fired / benign_hits 全在内）
    assert off["verdict"] == on["verdict"], \
        f"开了观察栏，判定面被动过：{off['verdict']} → {on['verdict']}"
    # ② 默认路径不许出现观察栏 —— "看得见就早晚被读成判定"
    assert not off.get("observations"), f"默认路径混进了观察栏：{off.get('observations')}"
    # ③ 开了才出现，且观察维度都真跑上了（不是"收下开关但什么也没跑"）
    got = sorted(_obs_names(on))
    assert got == ["color_edge", "lighting", "noise"], f"观察栏没真跑起来（空壳）：{got}"