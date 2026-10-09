"""录「**用产品**」的那一镜：演示页在浏览器里真跑一次。

为什么需要单独一个工具
----------------------
`tools/record_all_demo.py` 录的 A/B/C 三段全是**命令行**：跑分、打包、读报告。
`output/video_evidence/README.md:8-10` 自认「没有一镜是使用产品」，而初赛评分
第 1 位要的是「**可运行、可展示的完整项目形态，而非仅停留在模型/算法演示层面**」
（`D:/deliver/compete/loreal-topic2-details.md:33`）→ 视频里必须有一段
**真在页面上点出来的账本**，不是终端里跑出来的数字。

流程（一步都不许省）
--------------------
  1) 起 `demo/serve.py`（纯标准库本地服务，只绑 127.0.0.1，零外网）
  2) 开 Edge 的 `--app` 窗口 → 页面地址带 `?autorun=1`：自动填预置语料 + 点一次
     「核验」。这一步与人工点选**完全等价**（同一个 `/api/run`、同一份产物），
     见 `demo/index.html` 里的 `autorun()`；之所以要自动，是因为录制窗口会抢前台
     焦点，人没法在录屏进行中点击。
  3) 按窗口标题找到它 → 区域抓屏（复用 `record_demo.record_window`）
  4) 判「跑完了没有」：`output/demo/*_report.json` 里出现 mtime 晚于开录时刻的
     那一份 —— 报告是页面的最后一步，它落盘即"这一跑结束"，页面不需要为录制改
     任何行为。（**不能**拿"目录里出现任何新文件"当信号：证据图在 +1.0s 就落盘，
     报告要到 +18.6s，实测见 `record_demo._done_seen`。）
  5) 抽帧 → 百炼 VL **逐字读回** → 与**同一次运行的真实产物**比对：
     期望值不是这里手写的，是从**本次运行**写出的 `<tag>_report.json` 里读出来的
     （按开录时刻筛，不认"目录里最新那份"——上一镜的报告也在同一个目录里）。
     默认抽**片尾一帧**；**摇镜镜（`scroll=obs` + `step`）抽两帧并合并读回**，
     因为附四两块物理上不可能同屏（视口 592 CSS px vs 卡片 ≈1580），
     片尾那一帧里第一块已经被推出视野 —— 只抽一帧 = 自造假失败，详见 `verify()`。
  6) 读不回来 → 挪进 `_rejected_page/` 并以非 0 退出：废片不许留在证据目录里冒充素材。
  7) 镜 D4「拔网线」：换一个**模型出口被掐断**的演示服务进程（黑洞代理 + 清 NO_PROXY，
     见 `BLACKHOLE_PROXY`），跑同一份预置语料 → F3 如实登记连不上，账本与判定不变。

用法
----
    cd /d/LorealGuard && env -u PYTHONPATH .venv/Scripts/python.exe tools/record_demo_page.py
    （只录一镜：--only D2 ；只抽帧复核已有 mp4：--verify-only）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import record_demo as rd  # noqa: E402
from voiceguard.semantic.vl import ask_vl  # noqa: E402

EV = ROOT / "output" / "video_evidence"
DEMO_OUT = ROOT / "output" / "demo"
#: 收尾产物：报告是页面的**最后一步**（F3 跑完才写），它落盘 = 这一跑结束。
#: 不能用"目录里有新文件"代替 —— 证据图在 +1.0s 就落盘（见 `_done_seen`）。
REPORTS = DEMO_OUT / "*_report.json"
#: 浏览器 profile 放**产物目录之外**：它一启动就在产物目录里建一堆文件，
#: 混进"新产物"里只会让完成信号更难判。
PROFILE_DIR = ROOT / "output" / "_edgeprofile"
FRAMES = EV / "_vlframes"
REJECTED = EV / "_rejected_page"

#: 录出来的窗口客户区（物理像素，16:9）。与 A/B/C 三段的 1578×888 同量级，
#: 成片按 1600×900 缩放即可；150% 缩放下 1600 物理像素 ≈ 1067 CSS 像素，
#: 页面在这个宽度下是**两栏账本**（index.html 的断点是 900 CSS px）。
WIN_W, WIN_H = 1600, 900
WIN_X, WIN_Y = 40, 40

#: 每一镜：用哪条预置语料（按 **id** 取，不写序号 —— 序号会随语料增删漂移）、
#: 是否勾 F3、以及这一镜在视频里要证明什么。
PAGE_DEMO = [
    dict(tag="D1", out="recD1_page_text.mp4", preset_id="t34m", explain=0, seconds=60,
         why="图 + 文案 → 语义一致性链（功效时间线落回规则，出两栏账本）"),
    dict(tag="D2", out="recD2_page_comments.mp4", preset_id="c5-m01", explain=0, seconds=60,
         why="图 + 评论线程 → F5 评论区核验（同一页面上第二条判定链）"),
    dict(tag="D3", out="recD3_page_f3.mp4", preset_id="t34m", explain=1, seconds=120,
         scroll="f3",
         why="勾上 F3：开源权重档（qwen3.8-27b）把**算法已定位**的疑点翻成人话，判定不变"),
    dict(tag="D4", out="recD4_page_offline.mp4", preset_id="t34m", explain=1, seconds=90,
             scroll="f3", offline=True,
             why="拔网线：把通往模型的那条路**真的掐断**，F3 如实登记连不上，账本与判定一个字没变"),
        # D5 用**标定集那张图**，不是 D1 的默认图 —— 这不是随手换图：观察栏那两个维度
        # 只有 `samples/calib/` 这四张量得出数字（`samples/` 根目录三张图上 `lighting`
        # 实测全部 `unavailable`，2026-09-25 逐图跑 `--observe`）。
        # 记的是官方点名的**另两个**物证维度（拼接痕迹已由 ela 在账本里覆盖）。
        # ⚠️ `step=4000`：附四**一屏装不下两块**（整卡 ≈1580 CSS px，捕获视口 592 CSS px，
        # 见 `demo/index.html` 里 `second()` 的算术），所以这一镜改成**摇镜** ——
        # 页面在首次落点后 4.0s 硬切到第二块。**刻意不设 `scale`**：摇镜之后
        # "一屏塞更多"不再是约束，缩放档留着会白白把字缩小（片源 1578×888 → 成片
        # 1920×1080 本来就是上采样，字越大越清楚）。
        # `tail=11.0` 也是这一镜专有：默认 6.0s 会让整片 ~8.8s 就收尾（round18 实测），
        # 第二块只来得及停 4s 左右 —— 够不到"第二块在画面里停留 ≥6s / 整片 ≥13s"。
        # 时序（由录像自身可反推）：报告落盘 = 视频第 `duration - tail` 秒 = **首落点**，
        # 硬切在 `+step` ⇒ 第二块在画面里停 `tail - step/1000` = 11.0 - 4.0 = **7.0s** ≥ 6s，
        # 整片 ≈ `落盘(约2.8s) + 11.0` ≈ 13.8s ≥ 13s。`verify()` 的两帧时刻正是
        # 从这里推的（见 `obs_verify_ts`），不是手写的常数。
        dict(tag="D5", out="recD5_page_observe.mp4", preset_id="t34m", explain=0, observe=1,
             image="samples/calib/calib_splice_cross_source.jpg", seconds=90, scroll="obs",
             step=4000, tail=11.0,
             why="勾上观察栏：官方点名的另两个维度（光影一致性 / 色彩过渡）**真的量出了数字**，"
                 "而它们只写附四 —— 不进左栏、不进规则表、不改风险等级"),
    ]

#: 「拔网线」怎么拔：给**演示服务进程**一个黑洞代理，再把 NO_PROXY 白名单清掉。
#: 本机网线不能真拔（会连当前这个会话一起断，而且拔了也没法把边跑边录做完），
#: 所以掐的是**F3 唯一那条出口**：`openai` SDK 走 httpx，httpx 默认 `trust_env=True`
#: → 代理环境变量就是它唯一认的路。实测（2026-09-22）：`status=error` /
#: `调用失败：APIConnectionError: Connection error.`，而同一次运行的账本是
#: `base_neutral_7a4ab438` / 指纹 `1a9f812fd314` / 关注级 / E1 —— **与联网那一镜逐字段相同**。
#: 这不是"页面上写死一个 error"，是真的连不出去。
BLACKHOLE_PROXY = {
    "HTTPS_PROXY": "http://127.0.0.1:9",     # 9 = discard，本机没人听 → 立刻 Connection refused
    "HTTP_PROXY": "http://127.0.0.1:9",
    "ALL_PROXY": "http://127.0.0.1:9",
}

#: 判定链本体（F3 只许写 explanation，这些字段一个都不许动）。
#: 用"勾 F3 / 不勾 F3 跑同一份输入"的两份产物**比出来** —— 这是本项目的核心主张，
#: 不能只写在 PPT 里（`CLAUDE.md` 铁律 3：判定权归算法，模型永不参与判定）。
VERDICT_KEYS = ("content_id", "shape", "fingerprint", "detectors", "verdict", "evidence",
                "benign", "layers", "comment_verdict", "provenance")


def verdict_only(rep: dict) -> dict:
    """报告里属于**判定链**的那部分（剔除 explanation 与任何模型产物）。

    `layers` 要削掉 **F3 那一行**：它记的是"语义解释层跑没跑"（`status` 里带模型名），
    勾/不勾 F3 本来就不同 —— 那是**跑没跑**变了，不是**判定**变了。实测：不削这一行，
    第一次比对就红在 `['layers']` 上，等于把"模型没参与判定"这句话验成了假的。
    其余各行（F1/F2/F4–F8）照比，一个字都不许差。
    """
    out = {}
    for k in VERDICT_KEYS:
        v = rep.get(k)
        if k == "layers" and isinstance(v, list):
            v = [r for r in v if not str((r or {}).get("layer") or "").startswith("F3")]
        out[k] = v
    return out


def verdict_unchanged(a: dict, b: dict) -> tuple[bool, str]:
    """同一份输入、勾/不勾 F3 各跑一次 → 判定链字段必须完全相同。

    这就是"模型不参与判定"的**可执行版本**：只要有一个字段变了，本镜按废片拒收。
    """
    ka, kb = verdict_only(a), verdict_only(b)
    diff = [k for k in VERDICT_KEYS if ka[k] != kb[k]]
    if diff:
        return False, f"勾上 F3 之后判定链字段变了：{diff}（F3 只许写 explanation）"
    return True, "ok"


#: 页面截图的读回提示词。与终端版（record_all_demo.QUESTION）分开写：
#: 网页是深底 + 多栏，要求逐字抄标题与等级字样，**不要它总结**。
QUESTION = (
    "这是一个网页界面截图。请**逐字抄写**画面上出现的文字（标题、等级、分栏名、表格里的短词），"
    "不要解释、不要总结、不要推测没显示的内容。看不清的字写『?』。"
)


def edge_exe() -> str:
    for c in (shutil.which("msedge"),
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if c and pathlib.Path(c).exists():
            return c
    raise RuntimeError("找不到 msedge.exe（演示页录制需要 Edge 的 --app 窗口）")


def _get_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:      # noqa: S310 只连本机回环
        return json.loads(r.read().decode("utf-8"))


def start_server(port: int, offline: bool = False) -> subprocess.Popen:
    env = None
    if offline:
        env = {**os.environ, **BLACKHOLE_PROXY}
        for k in ("NO_PROXY", "no_proxy"):
            env.pop(k, None)     # 别让白名单把百炼放行（那这一镜就成了假的"拔网线"）
    p = subprocess.Popen([sys.executable, str(ROOT / "demo" / "serve.py"),
                          "--port", str(port), "--no-browser"],
                         cwd=str(ROOT), env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    t0 = time.time()
    while time.time() - t0 < 20:
        try:
            _get_json(f"http://127.0.0.1:{port}/api/samples", timeout=2.0)
            return p
        except Exception:                       # noqa: BLE001  还没起来，接着等
            time.sleep(0.4)
    p.kill()
    raise RuntimeError(f"演示服务起不来（127.0.0.1:{port}）")


def preset_index(port: int, preset_id: str) -> int:
    """把预置语料的 id 换成页面下拉框里的序号（序号由服务端给，不由本文件猜）。"""
    d = _get_json(f"http://127.0.0.1:{port}/api/samples")
    for i, p in enumerate(d.get("presets") or []):
        if p.get("id") == preset_id:
            return i
    ids = [p.get("id") for p in (d.get("presets") or [])][:8]
    raise RuntimeError(f"预置语料里没有 {preset_id}（前 8 个：{ids}）")


#: 开跑前让页面先等这么久（毫秒），留给录制端"量窗口 + 挂 ffmpeg"。
#: 不给这个前置量，页面会在 t0+1.01s 就把产物落盘（实测三镜都是这个数）——
#: 片头只剩 0.2s 余量，一旦机器忙一点，录到的就是"已经跑完的静态页"，
#: 视频里那一镜根本没有"用产品"的过程。
LEAD_IN_MS = 3500


def _cmp_key(case: dict, explain: bool | None = None) -> tuple:
    """「判定不变」的比对键 = **输入的身份**（预置语料 + 图 + 是否开观察栏）＋ 是否勾 F3。

    必须带上 image / observe：不然镜 D5（标定集 + 观察栏）会落到 D1 的键上，
    **把 D1 留的基准覆盖掉** → 随后 D3 拿 D5 的报告当基准比 → 凭空报一个"判定变了"。
    （2026-09-25 加 D5 时发现：这是**任何新增镜**都会踩的坑，所以修在键上，不修在某镜上。）
    """
    return (case["preset_id"], case.get("image", ""), bool(case.get("observe", 0)),
            bool(case["explain"]) if explain is None else explain)


def open_page(port: int, idx: int, explain: int, profile: pathlib.Path,
              scroll: str = "", lead_ms: int = LEAD_IN_MS,
              observe: int = 0, image: str = "", scale: str = "",
              step: int = 0) -> subprocess.Popen:
    url = (f"http://127.0.0.1:{port}/?autorun=1&preset={idx}"
           + ("&explain=1" if explain else "")
           + ("&observe=1" if observe else "")
           + (f"&image={image}" if image else "")
           + (f"&scroll={scroll}" if scroll else "")
           # `step=<毫秒>`：只在页面侧 `scroll=obs` 时才会被认（摇镜的第二落点）。
           # 不给就一个字节都不加到 URL 上 —— 其余镜的 URL 与从前逐字相同。
           + (f"&step={step}" if step else "")
           + (f"&lead={lead_ms}" if lead_ms else ""))
    return subprocess.Popen(
        [edge_exe(), f"--app={url}", f"--window-size={WIN_W},{WIN_H}",
         f"--window-position={WIN_X},{WIN_Y}", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", "--disable-sync",
         "--disable-features=msEdgeFirstRunExperience"]
        # 只为**这一镜**降内容渲染尺度（`scale=""` = 不开这个开关）。
        # 铁律：窗口尺寸与捕获分辨率不许变（`WIN_W×WIN_H` → 1578×888），
        # 所以只能动 devicePixelRatio，让同样的物理窗口里多装几行 CSS 内容。
        + ([f"--force-device-scale-factor={scale}"] if scale else []),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def kill_tree(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                   capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def page_title(port: int) -> str:
    """窗口标题 = 页面 <title>（Edge 的 --app 窗口不带浏览器后缀）。"""
    html = pathlib.Path(ROOT / "demo" / "index.html").read_text(encoding="utf-8")
    i, j = html.index("<title>") + len("<title>"), html.index("</title>")
    return html[i:j].strip()


def report_since(t0: float | None) -> pathlib.Path | None:
    """**本次运行**写出的报告：mtime 必须晚于开录时刻。

    不能拿"目录里最新那份报告"充数 —— 上一镜的报告就躺在同一个目录里，
    2026-09-21 的 D3 就是这么把 D2 的报告当成了自己的期望值来源（读回 0/4）。
    """
    if t0 is None:
        return None
    fs = [p for p in DEMO_OUT.glob("*_report.json") if p.stat().st_mtime >= t0]
    return max(fs, key=lambda p: p.stat().st_mtime) if fs else None


def report_before(video: pathlib.Path) -> pathlib.Path | None:
    """`--verify-only` 用：找回**当初录这一镜时**写出的那份报告（`report_since` 的逆）。

    为什么必须有它：复核一条已录成的片，重录会换文件、sha 就断了（D5 已按 sha 锚定）。
    而 `verify()` 的期望值**只许来自真产物**、不许硬编，所以复核模式也得把那份报告找回来 ——
    不然复核只会永远回一句"本次运行没写出报告"，等于这条通路根本没通。
    判据：报告一定写在**录像结束之前**（录制过程就是"等 `*_report.json` 落盘再收尾"）
    ⇒ 取 mtime ≤ mp4 mtime 的最新一份。

    这是**启发式**、不是证明：若在"报告落盘 → 录像收尾"之间又跑了别的镜，取到的就是别人的
    报告。方向是**失败即暴露**（取错 ⇒ 期望串读不回 ⇒ FAIL），不会静默放过；真要严格，
    重录那一镜即可（`--only <tag>`，不带 `--verify-only`）。
    """
    if not video.exists():
        return None
    cut = video.stat().st_mtime
    fs = [p for p in DEMO_OUT.glob("*_report.json") if p.stat().st_mtime <= cut]
    return max(fs, key=lambda p: p.stat().st_mtime) if fs else None


def _jsnum(val) -> str:
    """按 JS 的 `String(v)` 打数字：`1.0` 上屏是 `1`，不是 `1.0`。

    附四的测量值是页面直接 `String(v)` 塞进表格的。用 Python 的 `str()` 会写成
    `1.0`，而屏幕上根本没有那个字 ⇒ 期望值永远读不回来 = **自造假失败**。
    """
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val)


def expectations(rep: dict, case: dict) -> list:
    """**承诺字样来自同一次运行的真实产物**，不是本文件里手写的常量。

    挑的是"**片尾那一屏上真的看得到**、而且能证明这是这一次运行"的东西：
      · 等级字样、两栏的栏名  → 画面确实是账本那一段
      · 物证指纹前 12 位      → 画面确实是**这一次**的运行，不是上一次留下的静态页
        （这是加进来最要紧的一条：前三个词在别的运行里也都成立）
      · 勾了 F3 的那一镜改成读 F3 卡片上的东西（模型名/许可/状态/它引用的证据 id）：
        卡片在账本**下面**，而这一镜滚的就是卡片（`scroll=f3`）—— 期望值必须跟着
        那一屏走，否则要求读回屏幕上根本没有的字，等于自造假失败。
      · 勾了观察栏的那一镜（`scroll=obs`）读**那两块各自的 h4 与表里前几行**：那张卡比
        一屏高（≈1580 CSS px vs 视口 592 CSS px），所以这一镜靠**摇镜**让两块各自出现 ——
        摇镜只有**两个落点**（页面 `second()`：卡首 + 第二块标题）⇒ 能上屏的只有前两块，
        期望串即取前两块，并加哨兵要求这俩**恰好**是官方点名的两维度（round18 的成片只出现
        `color_edge`、`lighting` 一次都没上屏，而那时这条期望串是绿的 —— 漂移的代价）。
    """
    v = rep.get("verdict") or {}
    ev = rep.get("evidence") or []
    ev0 = str((ev[0].get("id") if ev else "") or "")
    if case.get("scroll") == "obs":
        ds = (rep.get("observations") or {}).get("detectors") or []
        # 门槛是**两块**，不是"非空"。为什么：这一镜要证明的是官方点名的
        # **两个**维度（光影一致性 / 色彩过渡）都真的量出了数字。只有一块时，
        # 片子看起来完全正常（照滚、照有数字），但证明的只是其中一半 ——
        # round18 的成片正是如此：`lighting` 一次都没出现，而守卫是绿的。
        # 哨兵串在屏幕上不会出现 ⇒ VL 读不回来 ⇒ 明确 FAIL。
        # 这里**不能**返回 `[]`：`verify` 的 `miss` 也会是空的 → 假通过。
        if len(ds) < 2:
            return [f"观察栏只产出 {len(ds)} 块（这一镜要两个维度都出现）"]
        # 2026-10-03：附四现在**三块**（`noise` 是 10-01 加的自建观察维度），而摇镜只有
        # **两个落点**（页面 `second()`：卡首 + 第二块标题）⇒ 能上屏的只有前两块。故期望
        # 串只取前两块，并加一条哨兵：这两块必须**恰好**是官方点名的 `lighting` /
        # `color_edge`，否则报错（哨兵串不在屏上 ⇒ FAIL）——多出/换名的维度不许静默挤掉
        # 官方维度：那正是 round18 与 2026-10-03 两次「守卫绿、画面缺维度」的形态。
        official = {"lighting", "color_edge"}
        names = [str(d.get("name") or "") for d in ds[:2]]
        if set(names) != official:
            return [f"摇镜两落点覆盖的是 {names}，不是官方点名的 {sorted(official)}"]
        out = []
        for d in ds[:2]:
            out.append(f"观察维度 {d.get('name')}（{d.get('status')}）")
            for k, val in list((d.get("measurements") or {}).items())[:4]:
                out.append(str(k))
                if isinstance(val, float) and not val.is_integer():
                    out.append(_jsnum(val))
        return [s for s in out if s]
    if case.get("scroll") == "f3":
        ex = rep.get("explanation") or {}
        return [str(x) for x in (ex.get("model"), ex.get("license"),
                                 ex.get("status"), ev0) if x]
    exp = [str(v.get("tier") or ""), "可疑证据", "免责"]
    fp = str(rep.get("fingerprint") or "")
    if fp:
        exp.append(fp[:12])
    if ev0:
        exp.append(ev0)
    return [e for e in exp if e]


def grab_frame(video: pathlib.Path, ts: float, tag: str) -> pathlib.Path:
    FRAMES.mkdir(parents=True, exist_ok=True)
    png = FRAMES / f"page_{tag}_{ts:.0f}s.png"
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", str(ts), "-i", str(video), "-frames:v", "1", str(png)],
                       # 2026-09-22 实测：ffmpeg 的日志是 **UTF-8**（「不存在的文件.mp4」= e4 b8 8d…），
                       # 不给 encoding 就按 locale（GBK）解 → 抽帧路径含非 ASCII 时读线程崩，
                       # `r.stderr` 变成 **None**（不是空串）→ 下面那行 `r.stderr.strip()` 直接
                       # 抛 AttributeError，ffmpeg 说的"打不开这个文件"一个字都看不到。
                       # 变异体 M49/M50 守着这条，见 docs/DEBT.md D-07。
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not png.exists():
        raise RuntimeError(f"抽帧失败：{r.stderr.strip()[:200]}")
    return png


#: 摇镜镜第一块的采样：**首落点后**再等这么久（秒）才抽第一帧。
#: 首落点 ≈ 报告落盘时刻（渲染完 → `data-rendered` → 滚过去），而报告落盘时刻
#: 可以从录像自身算出：`tail` 只在落盘信号之后才计，所以 **落盘 = 视频第 `duration - tail` 秒**。
#: 取 1.5s 是"等它真的滚过去"与"别撞上 step 硬切"之间的折中（D5 的 step=4.0s）。
OBS_FIRST_DELAY = 1.5


def obs_verify_ts(case: dict, dur: float) -> list:
    """摇镜镜的两个抽帧时刻（秒；`None` = 片尾）。**从录像自身推**，不写死。

    第一块的可读窗口 = [落盘, 落盘 + step] = [`dur - tail`, `dur - tail + step/1000`]，
    第一帧取窗口内靠前 `OBS_FIRST_DELAY` 秒处；第二帧取片尾（那时画面停在第二块）。
    `case["verify_ts"]` 显式给定时以它为准（现场调时不用改代码）。
    """
    if case.get("verify_ts"):
        return list(case["verify_ts"])
    tail = float(case.get("tail", 6.0) or 0.0)
    step = float(case.get("step", 0) or 0) / 1000.0
    if not step:                     # 不摇镜：一个落点，片尾一帧就够
        return [None]
    land = max(0.0, dur - tail)      # 报告落盘 = 首落点
    t1 = land + OBS_FIRST_DELAY
    if step:                         # 不许越过硬切：留 1.0s 余量
        t1 = min(t1, land + step - 1.0)
    return [max(0.5, t1), None]


#: 全角标点/数字 → 半角；全角空格 → 半角。（**写法照抄** `tools/check_shot_visibility.py`
#: 的 `_FW`/`_SKIP`/`norm()` —— 那份是 Hermes 的验收工具、按 sha 锚定，不许改，
#: 这里只是把同一套口径复制过来，让录屏器与验收工具的读回判据同源。）
_FW = {c: chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)}
_FW[0x3000] = 0x20
_SKIP = "()[]{}<>（）［］｛｝【】〈〉《》"


def norm(s: str) -> str:
    """归一化：全角→半角、去掉所有空白与括号、去 BOM。

    为什么非归一化不可（round18c 实测，两个方向都翻过车）：
      · 页面模板 `demo/index.html:517` 用的是**全角** `（${d.status}）`，而云端 VL
        读回成半角 `(ok)` ⇒ 同一带**时对时错**（flaky）；
      · 本机 OCR 把 `（ok）` 读成 `。幻`、把小数点读成 `．`（`177．58`）。
    两条读回通道都会在标点上翻车 ⇒ 判据不能建在标点上。归一化后
    `177．58` ↔ `177.58`、`lighting (ok)` ↔ `lighting（ok）` 都命中。
    这是**判据的归一化，不是承诺的降级**：两块 h4 仍然都必须在期望串里。
    """
    t = str(s).translate(_FW).replace("﻿", "")
    return "".join(ch for ch in t if ch not in _SKIP and not ch.isspace())


def unmet(exp: list[str], readback: str) -> list[str]:
    """归一化后仍未读回的期望串（返回**原串**，便于人读/打印）。

    单独抽出来是为了可被守卫直接调用：拿同一份期望串喂进半角/全角/带空格三种
    读回形态，都必须回空；故意写错的串必须**不**回空。
    """
    hay = norm(readback)
    out = []
    for e in exp:
        n = norm(e)
        if not n:                      # 归一化后为空 ⇒ 这条期望挡不住任何东西
            out.append(e)
            continue
        if n not in hay:
            out.append(e)
    return out


def verify(case: dict, rep: dict | None) -> tuple[bool, str]:
    """抽帧 → VL 逐字读回 → 与真实产物机械比对。

    **为什么摇镜那一镜（`scroll=obs` + `step`）要抽两帧**
    --------------------------------------------------
    附四整卡实测 ≈1580 CSS px，而这一镜的捕获视口只有 **592 CSS px**
    （1600×900 物理窗口 ÷ dpr 1.5，round18 探针实测）⇒ **两块在物理上不可能同屏**。
    这一镜靠页面在首落点后 4.0s **硬切**到第二块（`demo/index.html:787-809` 的
    `second()`）。于是：片尾那一帧里只剩**第二块**，第一块的字（`观察维度 color_edge`）
    早被推出视野上沿 —— 只抽片尾一帧，期望串里属于第一块的那几条**永远读不回来**，
    这是**自造假失败**（round18 就是这么判的，而更早的版本把期望串降回一块、把
    假的判成绿的，本文件的历史里两种错都犯过）。

    所以：摇镜镜抽**两帧**（摇镜前 + 片尾），两帧的读回**合并**后再比对 ——
    某个串在**任一帧**读回即算命中。时刻由 `obs_verify_ts()` 从录像自身推出来
    （或 `case["verify_ts"]` 显式指定），不写死在函数里。
    其余镜不给 `step`/`verify_ts` ⇒ 仍然只抽片尾一帧，行为与从前一字不差。
    """
    video = EV / case["out"]
    if not video.exists():
        return False, f"缺文件 {video}"
    if rep is None:
        return False, ("本次运行没写出报告（开录时刻之后 output/demo/ 里没有 *_report.json）—— "
                       "期望值来自产物，取不到就不许硬编")
    exp = expectations(rep, case)
    dur = float(rd.probe(video).get("duration", 0) or 0)
    end = max(0.5, dur - 0.4)
    ts_list = obs_verify_ts(case, dur)
    if len(ts_list) > 1:
        print(f"      摇镜镜：抽两帧（附四两块同屏不可能 —— 视口 592 CSS px vs 卡片 ≈1580；"
              f"摇镜窗口 = [{dur - float(case.get('tail', 6.0) or 0.0):.1f}s 落盘, "
              f"+{float(case.get('step', 0) or 0) / 1000.0:.1f}s 硬切]）")
    # 逐帧抽 + 逐帧 VL 读回；`ans` 是**合并**后的整段文字，用于机械比对。
    ans, per_frame = "", []
    for i, ts in enumerate(ts_list):
        t = end if ts is None else max(0.0, min(float(ts), end))
        tag = case["tag"] if len(ts_list) == 1 else f"{case['tag']}_{i + 1}"
        png = grab_frame(video, t, tag)
        res = ask_vl(png, QUESTION)
        a = res.get("answer") or ""
        # 比对走 `unmet()`（两侧归一化：全角/半角、空白、括号、BOM 全部抹平）。
        # `hit` 只作**本帧**的进度显示，判据在下面的 `miss`。
        hit = [e for e in exp if e not in ans and norm(e) and norm(e) in norm(a)]
        per_frame.append((png, t, a, res, hit))
        ans += ("\n" if ans else "") + a
    miss = unmet(exp, ans)
    for png, t, _a, res, hit in per_frame:
        print(f"      帧 {png.name}（片内 {t:.1f}s / 共 {dur:.1f}s，{png.stat().st_size} B）"
              f"| VL ok={res.get('ok')} | 本帧新命中 {len(hit)}/{len(exp)}")
        if hit:
            print(f"        └ {hit}")
    print(f"      合并两帧后命中 {len(exp) - len(miss)}/{len(exp)}")
    if miss:
        print(f"      ❌ 归一化后未读回：{miss}")
        print("      --- VL 逐字读回原文 ---")
        for png, t, a, res, _ in per_frame:
            print(f"      | === 片内 {t:.1f}s（{png.name}）===")
            for ln in (a or f"(空) err={res.get('error')}").splitlines():
                print(f"      | {ln}")
        return False, f"期望串未读回 {miss}"
    print("      ✅ 承诺字样全部命中（判据=归一化后包含，期望值取自本次运行的 report.json）")
    return True, "ok"


def reject(case: dict, why: str) -> None:
    REJECTED.mkdir(parents=True, exist_ok=True)
    src = EV / case["out"]
    if src.exists():
        shutil.move(str(src), str(REJECTED / f"{case['tag']}_{case['out']}"))
        (REJECTED / f"{case['tag']}_WHY.txt").write_text(
            f"{case['out']} 被拒收（{why}）\n预置语料: {case['preset_id']}｜F3: {case['explain']}\n"
            "废片特征：文件在、非黑帧、时长合格，但屏幕内容不是承诺的那几个字样。\n",
            encoding="utf-8")


def main() -> int:
    # 控制台编码兜底：本机 cmd 是 GBK，判定已经 PASS 却会在打印「✅」那一刻
    # UnicodeEncodeError 崩掉（实测踩到，退出码 1 —— 明明录成了却报失败）。
    # 用 errors="replace" 而不是硬转 UTF-8：保留控制台自己的编码，让它在用户
    # 的 cmd/Windows Terminal 里仍是正常中文，编不出的字符降级成 '?' 而不是崩。
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:                       # noqa: BLE001  老环境没有 reconfigure
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只处理某一镜（D1/D2/D3/D4/D5）")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--verify-only", action="store_true", help="不重录，只抽帧复核已有 mp4")
    a = ap.parse_args()

    cases = [c for c in PAGE_DEMO if not a.only or c["tag"] == a.only]
    if not cases:
        print(f"[X] 没有匹配的镜：--only {a.only}")
        return 2

    bad = []
    seen: dict = {}          # _cmp_key(c) → 本次运行的报告，供"判定不变"比对
    srv = edge = None
    srv_offline = None       # 当前这个服务进程的出口档：模型可达 / 出口被掐断
    try:
        title = page_title(a.port)
        for c in cases:
            out = EV / c["out"]
            print(f"\n===== 镜 {c['tag']}：{c['out']} =====")
            # 离线那一镜要换一个**出口被掐断**的服务进程（判定链在进程内，环境变量
            # 就是它唯一的路），所以按镜切换时重启服务 —— 出口档变了才有必要重启。
            if srv is None or bool(c.get("offline")) != srv_offline:
                kill_tree(srv)
                if srv is not None:
                    time.sleep(1.0)      # 等旧进程真的让出端口，别对着 TIME_WAIT 起新的
                srv = start_server(a.port, offline=bool(c.get("offline")))
                srv_offline = bool(c.get("offline"))
                print(f"  演示服务已起：http://127.0.0.1:{a.port}/（只绑回环）"
                      + ("｜**模型出口已掐断**（黑洞代理，NO_PROXY 白名单已清）→ "
                         "F3 这一层够不到百炼，账本照出" if srv_offline else "｜模型出口正常"))
            print(f"  录的是一件事：{c['why']}")
            rep_path = None
            if a.verify_only:
                # 复核（不重录）：期望值必须来自**这片录成时**的那份报告，见 `report_before`。
                rep_path = report_before(out)
                print("  复核模式（--verify-only）：不重录、不换文件"
                      + (f"；期望值取自 {rep_path.name}（mtime ≤ 片子 mtime）"
                         if rep_path is not None else
                         "；❌ 找不到这片录成时的报告 ⇒ 期望值无从谈起，只能判 FAIL"))
            if not a.verify_only:
                idx = preset_index(a.port, c["preset_id"])
                profile = PROFILE_DIR / c["tag"]
                # 开页之前就起钟：产物是页面写的，钟从"它开始存在"那一刻才不漏。
                t_open = time.time()
                edge = open_page(a.port, idx, c["explain"], profile, c.get("scroll", ""),
                                 observe=c.get("observe", 0), image=c.get("image", ""),
                                 scale=c.get("scale", ""), step=int(c.get("step", 0) or 0))
                print(f"  页面：?autorun=1&preset={idx}（{c['preset_id']}）"
                      f"{'&explain=1' if c['explain'] else ''}"
                      f"{'&observe=1' if c.get('observe') else ''}"
                      f"{'&image=' + c['image'] if c.get('image') else ''}"
                      f"{'&scroll=' + c['scroll'] if c.get('scroll') else ''}"
                      f"{'&step=' + str(c['step']) if c.get('step') else ''}"
                      f"&lead={LEAD_IN_MS}"
                      f"{'｜dpr=' + c['scale'] if c.get('scale') else ''}"
                      f"｜窗口标题 '{title}'")
                r = rd.record_window(title, out, c["seconds"], done_target=REPORTS,
                                     tail=float(c.get("tail", 6.0)),
                                     size=(WIN_W, WIN_H), t0=t_open)
                if r.get("rc") != 0 or not out.exists():
                    bad.append((c["tag"], f"录制失败 rc={r.get('rc')} {r.get('stderr')}"))
                    print(f"  ❌ 录制失败：{r}")
                    kill_tree(edge)
                    edge = None
                    continue
                print(f"  录制完成：{r['rect'][2]}x{r['rect'][3]} 窗口在 {r['rect'][:2]} "
                      f"抢焦点={r['stole_focus']} 产物落盘于 {r['finished_after']}s "
                      f"（总 {r['elapsed']}s）")
                if r.get("covered_by"):
                    print(f"  ⚠️ 录制期间抓取区域被别的窗口盖过（时刻,标题）：{r['covered_by']}")
                if r.get("covered_at_end"):
                    print(f"  ❌ **片尾留帧那几秒**被盖住：{r['covered_at_end']}"
                          f" —— 复核抽的正是那几帧，这镜基本必废（重录，或换台安静的机器）")
                kill_tree(edge)
                edge = None
                time.sleep(0.5)
                # 没等到产物落盘 = 退回了"按秒数猜跑完没有"（2026-09-20 事故 3 的同一类错）：
                # 那种片子照样"文件在、非黑帧、时长合格"，但片尾那一屏**不保证**是跑完的那一屏
                # （实测踩过：录满 91s 上限才收尾，页面的结果早就停在那儿了，看着像过了）。
                if r.get("finished_after") is None:
                    why = ("没等到 output/demo/*_report.json 的落盘信号，整段按上限秒数截断 —— "
                           "无法证明片尾是'跑完那一屏'")
                    bad.append((c["tag"], why))
                    print(f"  ❌ {why}")
                    reject(c, why)
                    continue
                rep_path = report_since(r.get("t0"))
            rep = None
            if rep_path is not None:
                rep = json.loads(rep_path.read_text(encoding="utf-8"))
                print(f"  本次产物：{rep_path.name}（等级 {(rep.get('verdict') or {}).get('tier')}）")
            ok, why = verify(c, rep)
            if not ok:
                bad.append((c["tag"], why))
                reject(c, why)
            if rep is not None:
                # 同一份**输入**（预置语料 + 图 + 观察栏档）、勾/不勾 F3 各跑一次
                # → 判定链字段必须逐字段相同。先跑的那一镜（D1）留在这里当基准，
                # 等同一输入的、勾了 F3 的那一镜来比（键的定义见 `_cmp_key`）。
                key = _cmp_key(c)
                base = seen.get(_cmp_key(c, explain=not bool(c["explain"])))
                if base is not None:
                    same, why2 = verdict_unchanged(base, rep)
                    print(f"  判定不变（与'{'勾' if not c['explain'] else '不勾'} F3'"
                          f"的同预置镜比对 {len(VERDICT_KEYS)} 个判定链字段）："
                          f"{'✅ 逐字段相同' if same else '❌ ' + why2}")
                    if not same:
                        bad.append((c["tag"], why2))
                        reject(c, why2)
                seen[key] = rep
    finally:
        kill_tree(edge)
        kill_tree(srv)

    # 勾了 F3 的镜，必须真的跟"不勾 F3"的同预置镜比过 —— 没比过就要说出来，
    # 不能让人以为"判定不变"已经验过了（`--only D3` 就属于这种：基准镜没跑）。
    for c in cases:
        if c["explain"] and _cmp_key(c, explain=False) not in seen:
            print(f"\n⚠️ 镜 {c['tag']} 勾了 F3，但本次没跑同预置的不勾 F3 那镜"
                  f"（{c['preset_id']}）→ **判定不变这一条本次没有比对**。"
                  f"成片用的那一次请整轮跑（不带 --only）。")

    print("\n" + "=" * 48)
    if bad:
        print(f"页面录屏判定：FAIL（{len(bad)} 镜）—— 废片已挪进 {REJECTED.relative_to(ROOT)}/")
        for t, w in bad:
            print(f"   - {t}: {w}")
        return 1
    print("页面录屏判定：PASS —— 每一镜片尾那一屏，都逐字对上了同一次运行的产物")
    return 0


if __name__ == "__main__":
    sys.exit(main())
