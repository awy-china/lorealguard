"""LorealGuard · 本地演示服务 —— 把**已经存在的**核验引擎接到一个能点的页面上。

    python demo/serve.py                    # 打开 http://127.0.0.1:8765
    python demo/serve.py --port 9000
    python demo/serve.py --explain-default  # 勾选框默认打开（见下方纪律②）

为什么要有这一层（而不是继续用 CLI 录屏）：
    赛题评分第 1 位原文要「**可运行、可展示的完整项目形态，而非仅停留在模型/算法演示层面**」，
    而 `python -m voiceguard <图>` 只有一条命令、一个输入，演示不出「交互流程」——
    本文件给的就是这个交互面：图 + 文案 + 评论三个输入，一个页面，点一下出账本。

    ⚠️ 2026-09-23 订正：本段原写「CLI 把 `ContentItem` 写死成只带图
    （`voiceguard/__main__.py:39`），「语义一致性校验」（需文案）与评论区核验
    （需评论列表）在 CLI 上**没有出口**」—— 这半句**当天已不再成立**：
    CLI 现已接出 `--text` 与 `--comments-file` 两个入口（见 `voiceguard/__main__.py`）。
    页面的不可替代性因此收敛为一条：它给的是**交互**（可点、可看、可现场改输入），
    而不再是"唯一能走进文案/评论通路的门"。

三条纪律（每条都由 `tests/test_demo_page.py` 里**会红**的检查钉住）：

  ① **不动判定**：本文件不写阈值、不重算等级，只调用 `GuardPipeline`；
     页面上的每个数字都从 `output/demo/*.json` **读回来**渲染，不在 HTML 里嵌死。
  ② **默认零网络**：`--explain` 默认关闭 → 默认路径零模型、零网络（拔网线照出账本）。
     勾选后才把图送往百炼（F3 语义解释层，开源权重档）；失败如实登记为
     `unavailable_*` / `error`，账本照出（`voiceguard/semantic/explain.py:101-123`）。
  ③ **图不出本机**：两条图片通路，各有各的闸。
     下拉里只列 `samples/` 下的程序合成样图（白名单 + realpath 双重校验）；
     使用者自己的图（拖入 / 选择本地图片）只落进本机一个临时目录，跑完**立刻删**
     （`finally` —— 跑挂了也删），并且**不写进任何产物**：报告里印的是展示用文件名，
     不是那个路径。守卫：`tests/test_demo_page.py`（samples 通路）+ `tests/test_demo_upload.py`（上传通路）。

     ⚠️ 铁律「只用合成样本」的产品边界随上传通路移动了一格，如实登记在这里：
     上传通路收的是**使用者自己的图**，它只在本机（内存 + 临时目录）过一遍，服务端不外发、
     不留存；默认路径依然零模型零网络（见②）。合成样本仍是**唯一默认**通路。

只用标准库：现场演示不依赖任何额外安装，也不需要联网。
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import shutil
import sys
import tempfile
import traceback
import webbrowser
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:            # 允许 `python demo/serve.py` 直接跑
    sys.path.insert(0, str(ROOT))

from voiceguard.agent import advise                                   # noqa: E402
from voiceguard.core.contract import ContentItem, parse_comment_lines  # noqa: E402
from voiceguard.core.tags import TAG_RE, tag_error                     # noqa: E402
from voiceguard.guard import GuardPipeline                            # noqa: E402
from voiceguard.report import ledger                                  # noqa: E402

DEMO_DIR = ROOT / "output" / "demo"       # 演示产物（output/ 已被 .gitignore 排除）
SAMPLES_DIR = ROOT / "samples"
INDEX = Path(__file__).resolve().parent / "index.html"

ALLOWED_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
#: tag 的规则**不在本文件** —— 唯一一份住在 `voiceguard/core/tags.py`，
#: 网页与命令行（`voiceguard/__main__.py`）共用它。本文件只 `from` 过来用，
#: **绝不在这里抄一份**：抄一份就意味着哪天规则变了，只有一边会变。
#: （2026-09-25 round8 缺陷④⑤：CLI 侧原先零校验，`--tag '../_ESCAPED'` 能写到 --out 外面。）
MAX_TEXT = 4000

#: 上传通路（B 段）的两道闸。上限与前端 `demo/index.html` 的 `LOCAL_MAX_MB = 8` 对齐：
#: 8 MiB 字节的 base64 是 11,184,812 字符，这里留一档余量。
#: ⚠️ `MAX_BODY` 必须比 `MAX_IMAGE_B64` 大 —— 否则请求会先被 body 闸拦下，
#: `MAX_IMAGE_B64` 这道闸永远轮不到（"看着有、其实没有"的闸比没有闸更坏）。
MAX_IMAGE_B64 = 12 * 1024 * 1024
MAX_IMAGE_NAME = 128
MAX_BODY = 13 * 1024 * 1024
MAX_COMMENTS_CHARS = 20000
MAX_COMMENT_LINES = 200
MAX_COMMENT_LINE = 500

#: 测试注入口（与 `GuardPipeline.explain_ask` 同一套做法，见 voiceguard/guard.py:241）：
#: 传了就用它问模型 —— 跑测试时塞假客户端 → **零网络**。
_ASK = None
#: F9 同一条路：Agent 编排层也只在**勾选后**才出网，测试同样靠注入假客户端做到零网络。
_AGENT_ASK = None


class BadRequest(ValueError):
    """输入不合法 —— 一律 400，绝不带着非法输入往下跑。"""


# --------------------------------------------------------------------- 校验

def _pick_image(rel: str) -> Path:
    """只允许 `samples/<名字>.<图后缀>`，且 realpath 必须真的落在 samples/ 里。

    两道闸门缺一不可：正则挡 `../`；realpath 挡符号链接与 Windows 的大小写/短名把戏。
    """
    if not isinstance(rel, str) or not rel:
        raise BadRequest("image 必填：形如 samples/base_neutral.jpg")
    if not rel.startswith("samples/") or "\\" in rel:
        raise BadRequest("image 只能是 samples/ 下的文件（本页只吃程序合成的样图）")
    name = rel[len("samples/"):]
    # 只允许 `samples/<文件>` 或 `samples/<一层子目录>/<文件>`。
    # 为什么要放子目录：`samples/calib/` 那四张是**专门为观察栏的光影/色彩过渡两个维度生成的标定集**
    # （`tools/calib_lighting_color.py`），而 `samples/` 根目录三张图上 `lighting`
    # 实测**全部 `unavailable`**（2026-09-25 逐图跑 `--observe`）→ 观察栏在页面上
    # 演示不出真数字。放宽的只是**层数**，护栏一条没少：`..` / 空段 / 隐藏文件一律拒；
    # `resolve()` 的包含性检查仍是第二道（不是唯一一道）。
    parts = name.split("/")
    if len(parts) > 2 or "" in parts or ".." in parts or any(x.startswith(".") for x in parts):
        raise BadRequest("image 路径不合法（只允许 samples/ 下一层子目录）")
    if Path(name).suffix.lower() not in ALLOWED_IMG_EXT:
        raise BadRequest(f"image 后缀只允许 {', '.join(ALLOWED_IMG_EXT)}")
    p = SAMPLES_DIR / name
    if not p.is_file():
        raise BadRequest(f"samples/ 下没有这个文件：{name}")
    try:
        p.resolve().relative_to(SAMPLES_DIR.resolve())
    except ValueError:
        raise BadRequest("image 越出了 samples/（拒绝）") from None
    return p


# ------------------------------------------------------- 上传通路（使用者自己的图）

#: 落到临时目录里的那份拷贝固定用 ASCII 名：`cv2`/`PIL` 在 Windows 中文路径下有坑
#: （`voiceguard/forensics/base.py:120` 那条注释）。**叫什么名字无所谓** ——
#: 给使用者看的名字单独走 `image_name`，报告里印的也是它。
#: 但**后缀必须保留**：`load_and_prepare` 是用后缀判 `is_jpeg` 的（`base.py:139`），
#: 后缀一变，上传通路与 samples 通路的判定面就不一样了（守卫：`tests/test_demo_upload.py` ②）。
_TMP_STEM = "upload"


def _sniff_image(data: bytes) -> str | None:
    """按**内容**认格式 —— 只干两件事：判「这是不是图」，以及没给文件名时补一个后缀。"""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _upload_name(raw, data: bytes) -> str:
    """展示用文件名：只用来显示与算 tag，**绝不参与任何路径拼接**（磁盘上叫 `_TMP_STEM`）。

    与 `_pick_image` 同一套口径：后缀白名单 + 拒绝一切路径成分（`/`、`\\`、`..`、前导点）。
    上传通路走的是「用户给的名字」，与命令行通路信任文件路径上的后缀是一致的。
    """
    if raw in (None, ""):
        ext = _sniff_image(data)
        if ext is None:
            raise BadRequest("这不是图像（只接受 JPEG / PNG / WebP）")
        return f"local_image{ext}"
    if not isinstance(raw, str):
        raise BadRequest("image_name 必须是字符串（纯文件名）")
    name = raw.strip()
    if not name:
        raise BadRequest("image_name 不能是空白")
    if len(name) > MAX_IMAGE_NAME:
        raise BadRequest(f"文件名太长（上限 {MAX_IMAGE_NAME} 字符）")
    if "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise BadRequest("文件名不合法：只接受纯文件名，不能带路径分隔符或 ..")
    if Path(name).suffix.lower() not in ALLOWED_IMG_EXT:
        raise BadRequest(f"图片后缀只允许 {', '.join(ALLOWED_IMG_EXT)}")
    return name


def _decode_upload(raw) -> bytes:
    """`image_b64` → 字节。空 ⇒ `b""`（= 没上传，走 samples 下拉那条路）。"""
    if raw in (None, ""):
        return b""
    if not isinstance(raw, str):
        raise BadRequest("image_b64 必须是 base64 字符串")
    s = raw.strip()
    if s.startswith("data:"):     # 浏览器 `FileReader.readAsDataURL` 给的就是 data URL
        head, sep, rest = s.partition(",")
        if not sep or "base64" not in head:
            raise BadRequest("image_b64 的 data URL 形式不合法")
        s = rest.strip()
    if not s:
        return b""                # `data:...;base64,` 后面是空的 == 没给图
    if len(s) > MAX_IMAGE_B64:
        raise BadRequest(f"图片太大（base64 上限 {MAX_IMAGE_B64} 字符，"
                         f"约 {MAX_IMAGE_B64 * 3 // 4 // 1024 // 1024} MB 原图）")
    try:
        data = base64.b64decode(s, validate=True)
    except (ValueError, TypeError):          # binascii.Error 是 ValueError 的子类
        raise BadRequest("image_b64 不是合法的 base64 编码") from None
    if not data:
        raise BadRequest("图片内容为空（0 字节）")
    return data


def _materialize_upload(payload: dict) -> tuple:
    """上传的图 → `(临时文件, 展示用文件名)`；压根没上传则 `(None, "")`。

    ⚠️ **落盘这一步只在这里做，删除是调用方的事** —— `run_once` 把删除放在 `finally`，
    所以"请求被拒/引擎跑挂"也要删得掉。校验一律排在建目录**之前**：
    畸形输入一个临时目录都不会留下。
    """
    data = _decode_upload(payload.get("image_b64"))
    if not data:
        return None, ""
    if _sniff_image(data) is None:
        raise BadRequest("这不是图像（只接受 JPEG / PNG / WebP）")
    name = _upload_name(payload.get("image_name"), data)
    tmpdir = Path(tempfile.mkdtemp(prefix="lorealguard_upload_"))
    f = tmpdir / f"{_TMP_STEM}{Path(name).suffix.lower()}"
    f.write_bytes(data)
    return f, name


def _parse_comments(raw) -> list:
    """评论列表 = 多行文本。空行不算；行数/单行长度都有上限（防注入、防手滑）。

    **切分口径不在这里** —— 它住在 `core/contract.py::parse_comment_lines`
    （网页与命令行共用的唯一一份；两边各长一套，就会出现"网页上算出来是高风险、
    命令行跑出来不是"这类最不该有的不一致）。
    这里只加三道**上限闸**：网页读的是陌生人提交的请求体，是本系统唯一需要
    防注入的入口；命令行读的是使用者自己的文件，走 `parse_comment_lines`
    不过这三道闸（见该函数 docstring）。
    """
    if raw in (None, ""):
        return []
    if not isinstance(raw, str):
        raise BadRequest("comments 必须是字符串（一行一条评论）")
    if len(raw) > MAX_COMMENTS_CHARS:
        raise BadRequest(f"comments 太长（上限 {MAX_COMMENTS_CHARS} 字符）")
    lines = parse_comment_lines(raw)
    if len(lines) > MAX_COMMENT_LINES:
        raise BadRequest(f"评论条数过多（上限 {MAX_COMMENT_LINES} 条）")
    for ln in lines:
        if len(ln) > MAX_COMMENT_LINE:
            raise BadRequest(f"单条评论过长（上限 {MAX_COMMENT_LINE} 字符）")
    return lines


def _tag_stem(name: str) -> str:
    """tag 的可见部分：把**展示用文件名**压成 ASCII 安全的短串。

    为什么要压：tag 是产物文件名前缀，规则住在 `voiceguard/core/tags.py`
    （`^[A-Za-z0-9_\\-]{1,64}$`，网页与命令行共用）。使用者上传的图可以叫「我的自拍.jpg」——
    直接拿它当 tag，产物**写得出去**（Windows 文件名允许中文），但**读不回来**：
    `/api/report?tag=我的自拍_xxxx` 会被同一份白名单拒成 400 ⇒
    "报告跑出来了"和"页面上点得开报告"就不再是一回事（④ 后半段正是点开它）。
    非 ASCII 一个不留（中文 / 空格 / 点号），全被去掉就退回 `local`；再截到 40 字符，
    保证 `stem + '_' + 8 位哈希` 不超过 `tags.TAG_MAX = 64`。
    """
    stem = "".join(ch for ch in Path(name).stem
                   if ch.isascii() and (ch.isalnum() or ch in "_-"))
    return stem.strip("_-")[:40] or "local"


def _pick_tag(payload: dict, name: str, text: str, comments: list) -> str:
    """tag = 产物文件名前缀。不传就按**输入内容**算一个确定性 tag（同输入 → 同文件名，可复算）。

    ⚠️ 2026-09-25（round8 缺陷③）：修前哈希只吃了 `len(comments)` —— **条数一样、内容不同**
    的两份输入会算出同一个 tag，后一次**静默覆盖**前一次的产物（`output/demo/` 里只剩后者，
    而页面还告诉用户"这就是你刚才那次的结果"）。现在哈希吃的是**文案全文 + 评论全文**：

        sha256(name + "\\x1f" + text + "\\x1f" + "\\n".join(comments))[:8]

    `\\x1f`（ASCII 单元分隔符）不可能出现在正常文案/评论里，用来排除歧义拼接 ——
    否则 ("ab","c") 与 ("a","bc") 会撞到同一个串上。仍然**确定性**：同输入 → 同 tag；
    仍**不含**随机数与时间戳（否则"可复算"这条就没了）。

    `name` 是**展示用**文件名（上传通路给 `image_name`，samples 通路给文件名）：
    这样同一张图不管是拖进来还是从下拉里选，算出的 tag 都一样；若用临时目录里那个
    ASCII 名，同一张图两个入口就会落成两份产物。
    """
    tag = payload.get("tag")
    if tag in (None, ""):
        blob = f"{name}\x1f{text}\x1f" + "\n".join(comments)
        return f"{_tag_stem(name)}_{sha256(blob.encode('utf-8')).hexdigest()[:8]}"
    err = tag_error(tag)
    if err:
        raise BadRequest(err)
    return tag


def build_item(payload: dict, tmp_file: Path | None = None, upload_name: str = "") -> tuple:
    """请求体 → (ContentItem, tag, explain, agent, observe)。纯函数，可单测。

    `tmp_file` / `upload_name` 是**上传通路**的两件东西，由 `run_once` 先一步备好
    （落盘与删除是同一条生命周期，拆开就会有一边忘了删）。默认 `(None, "")`
    ⇒ 走 samples 下拉那条老路，行为与从前一模一样。
    """
    img = tmp_file if tmp_file is not None else _pick_image(payload.get("image"))
    name = upload_name or img.name            # 展示用名字：算 tag 用它，**绝不**参与路径拼接
    text = payload.get("text") or ""
    if not isinstance(text, str):
        raise BadRequest("text 必须是字符串")
    if len(text) > MAX_TEXT:
        raise BadRequest(f"text 太长（上限 {MAX_TEXT} 字符）")
    comments = _parse_comments(payload.get("comments"))
    tag = _pick_tag(payload, name, text, comments)
    item = ContentItem(image_path=str(img), text=text, comments=comments,
                       kind="comment_thread" if comments else "image")
    #: F9 / F3 / 观察栏同一条纪律：**默认关**。前两个勾上 = 明示同意联网；
    #: 观察栏勾上只多跑三个纯 CPU 检测器（光影 / 色彩过渡 / 噪声残差，零模型、零网络），且它们**不进判定**。
    return (item, tag, bool(payload.get("explain")), bool(payload.get("agent")),
            bool(payload.get("observe")))


def _scrub_upload_path(tag: str, tmp_file: Path, display: str) -> None:
    """最后一道闸：产物里**任何**残留的临时路径，一律改写成展示用文件名。

    `report.image_path` 已经在内存里改过并按同一对象重写了产物，但报告里还有别的地方
    会印"这次用的是哪个文件"—— F3 的 `explanation["image_sent"]`
    （`voiceguard/semantic/explain.py:108` 记的是真送出去的那个文件）。
    临时目录名对使用者毫无意义，还会把他本机的目录结构印进要发出去的产物里，
    所以落盘之后统一抹一遍：这一层**不猜字段名**，只认那三个串（文件全路径 / 目录全路径 /
    目录名），连 JSON 里的转义写法一起换（`\\` 在 JSON 里是 `\\\\`）。
    """
    tmpdir = tmp_file.parent
    pairs: list = []
    for needle in (str(tmp_file), str(tmpdir), tmpdir.name):     # 长的在前，先换掉再换短的
        pairs.append((needle, display))
        pairs.append((json.dumps(needle)[1:-1], display))        # JSON 里的转义形态
    for suffix in ("_report.md", "_report.json"):
        p = DEMO_DIR / f"{tag}{suffix}"
        if not p.is_file():
            continue
        t = p.read_text(encoding="utf-8")
        for old, new in pairs:
            t = t.replace(old, new)
        p.write_text(t, encoding="utf-8")


# --------------------------------------------------------------------- 跑一次

def run_once(payload: dict, *, explain_ask=None, agent_ask=None) -> dict:
    """跑一条内容 → 落盘产物 → **把产物读回来**作为返回值（页面的数据源就是这些文件）。

    ⚠️ 上传通路（B 段）的临时目录在**这里**才有生命周期：`_materialize_upload` 先落盘，
    删除放在 `finally` —— 请求被拒（输入不合法）也一样删得掉，因为那条 `BadRequest`
    是在 `try` 里从 `build_item` 抛出来的（守卫 `tests/test_demo_upload.py` ①①②）。
    """
    if not isinstance(payload, dict):
        raise BadRequest("请求体必须是 JSON 对象")
    tmp_file, upload_name = _materialize_upload(payload)
    try:
        item, tag, explain, agent, observe = build_item(payload, tmp_file, upload_name)
        pipe = GuardPipeline(explain=explain, explain_ask=explain_ask,
                             agent=agent, agent_ask=agent_ask, observe=observe)
        report = pipe.run(item, outdir=DEMO_DIR, tag=tag, sheet=True)
        if upload_name:
            # 「图不出本机」在报告里也要成立：印**展示用文件名**，不印临时路径。
            # `pipe.run` 已经把产物落盘了（`guard.py:517-519`），所以改完要按同一个对象
            # 重写一遍 —— 只改内存不改盘，页面上读到的（从文件读）就还是临时路径。
            report.image_path = upload_name
            ledger.write_report(report, DEMO_DIR, tag)
            _scrub_upload_path(tag, tmp_file, upload_name)

        #: 决策卡不属于判定链（它只做「等级 → 措辞」映射，措辞在 rules.yaml），
        #: 所以不走 GuardPipeline 的产物，单独落一份，页面同样**从文件读**。
        decision = advise(report).to_dict()
        dec_path = DEMO_DIR / f"{tag}_decision.json"
        dec_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")

        rep_path = DEMO_DIR / f"{tag}_report.json"
        return {
            "ok": True,
            "tag": tag,
            "inputs": {"image": upload_name or f"samples/{Path(item.image_path).name}",
                       "text_chars": len(item.text), "n_comments": len(item.comments),
                       "explain": explain, "agent": agent, "observe": observe},
            "report": json.loads(rep_path.read_text(encoding="utf-8")),
            "decision": json.loads(dec_path.read_text(encoding="utf-8")),
            "files": {"report": f"/api/report?tag={tag}", "decision": f"/api/decision?tag={tag}",
                      "markdown": f"/api/markdown?tag={tag}",
                      "sheet": f"/evidence?tag={tag}" if _sheet(tag) else None},
        }
    finally:
        #: 删在 `finally`：跑挂了、输入不合法、被 400 拦下 —— 一条都不留。**上传通路独有**，
        #: 没上传（`tmp_file is None`）时什么都不做 —— samples 下拉里的图是仓库里的文件，
        #: 碰它就是删用户的仓库（铁律：不删用户文件）。
        if tmp_file is not None:
            shutil.rmtree(tmp_file.parent, ignore_errors=True)


def _sheet(tag: str) -> Path | None:
    p = DEMO_DIR / f"{tag}_evidence_sheet.png"
    return p if p.is_file() else None


def _safe_demo_file(tag: str, suffix: str) -> Path:
    """读产物：tag 先过白名单（与 CLI 共用的那一份），再 realpath 校验落在 output/demo/ 内。

    两道闸门缺一不可：白名单挡 `../` 与保留名，realpath 挡符号链接与 Windows 的短名把戏。
    """
    err = tag_error(tag)
    if err:
        raise BadRequest(err)
    p = DEMO_DIR / f"{tag}{suffix}"
    if not p.is_file():
        raise KeyError(tag)
    try:
        p.resolve().relative_to(DEMO_DIR.resolve())
    except ValueError:
        raise BadRequest("tag 越出了 output/demo/（拒绝）") from None
    return p


# --------------------------------------------------------------------- 语料

def sample_list() -> dict:
    """页面下拉框的内容：**只列仓库里真实存在的**合成样图与自造语料，不编内容。"""
    def _imgs(d: Path, prefix: str) -> list:
        return [{"rel": f"{prefix}/{p.name}", "name": p.name,
                 "kb": round(p.stat().st_size / 1024)}
                for p in sorted(d.iterdir())
                if p.is_file() and p.suffix.lower() in ALLOWED_IMG_EXT]

    images = _imgs(SAMPLES_DIR, "samples")
    # 标定集（`tools/calib_lighting_color.py` 生成）也要能选：
    # 观察栏（附四）光影/色彩过渡两个维度的演示**只有这四张量得出数字**（`noise` 要求 ELA 先出偏亮区域，演示它请选根目录的 `fw_splice.jpg`）—— 根目录三张图上
    # `lighting` 实测全是 `unavailable`（2026-09-25）。路径仍在 `samples/` 之内 ⇒
    # 服务端白名单（`_pick_image`）照样放行；多出来的**只有一层子目录**。
    calib = SAMPLES_DIR / "calib"
    if calib.is_dir():
        images += _imgs(calib, "samples/calib")
    presets: list = []

    tp = ROOT / "output" / "p2" / "text_pairs.json"
    if tp.is_file():
        for it in (json.loads(tp.read_text(encoding="utf-8")).get("items") or [])[:40]:
            presets.append({"kind": "文案", "id": it.get("id", ""), "source": "output/p2/text_pairs.json",
                            "text": it.get("text", ""), "expect": str(it.get("expect", ""))})

    cp = ROOT / "output" / "p5" / "comment_pairs.json"
    if cp.is_file():
        items = json.loads(cp.read_text(encoding="utf-8")).get("items") or []
        # 风险线程排在前面：演示时最该点到的是「能触发 F5 的那条」。
        # 只列前 20 条会把良性线程排满整个下拉框 → 这一层在页面上看起来永远无事发生。
        risk = [it for it in items if (it.get("expect") or {}).get("fire_risk")]
        rest = [it for it in items if not (it.get("expect") or {}).get("fire_risk")]
        for it in risk[:12] + rest[:8]:
            presets.append({"kind": "评论线程", "id": it.get("id", ""), "source": "output/p5/comment_pairs.json",
                            "text": "\n".join(it.get("comments") or []),
                            "expect": str(it.get("expect", ""))})
    return {"images": images, "presets": presets}


# --------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "LorealGuardDemo/1.0"

    # -- 工具 ----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def log_message(self, fmt, *a):        # 演示时别把终端刷满
        pass

    def _loopback_only(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    # -- GET -----------------------------------------------------------
    def do_GET(self):                       # noqa: N802
        if not self._loopback_only():
            return self._json(403, {"ok": False, "error": "只接受本机访问"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/samples":
                return self._json(200, sample_list())
            if u.path == "/api/report":
                p = _safe_demo_file((q.get("tag") or [""])[0], "_report.json")
                return self._send(200, p.read_bytes(), "application/json; charset=utf-8")
            if u.path == "/api/decision":
                p = _safe_demo_file((q.get("tag") or [""])[0], "_decision.json")
                return self._send(200, p.read_bytes(), "application/json; charset=utf-8")
            if u.path == "/api/markdown":
                p = _safe_demo_file((q.get("tag") or [""])[0], "_report.md")
                return self._send(200, p.read_bytes(), "text/markdown; charset=utf-8")
            if u.path == "/evidence":
                p = _safe_demo_file((q.get("tag") or [""])[0], "_evidence_sheet.png")
                return self._send(200, p.read_bytes(), "image/png")
            return self._json(404, {"ok": False, "error": f"没有这个路径：{u.path}"})
        except BadRequest as e:
            return self._json(400, {"ok": False, "error": str(e)})
        except KeyError:
            return self._json(404, {"ok": False, "error": "产物不存在（先跑一次核验）"})

    # -- POST ----------------------------------------------------------
    def do_POST(self):                      # noqa: N802
        if not self._loopback_only():
            return self._json(403, {"ok": False, "error": "只接受本机访问"})
        if urlparse(self.path).path != "/api/run":
            return self._json(404, {"ok": False, "error": "只有 /api/run"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json(400, {"ok": False, "error": "Content-Length 不合法"})
        if n <= 0 or n > MAX_BODY:
            # ⚠️ 登记在案的边界（2026-09-25 round14b 问题 2，**只登记未改**）：这一支**不读体**就回 400
            # 并关连接 ⇒ 仍在发送的客户端读到 `ConnectionAbortedError [WinError 10053]`，那条 400 读不到。
            # **不是产品缺陷**：页面侧有前置闸（`demo/index.html` 的 `LOCAL_MAX_MB = 8`），
            # 正常用户路径到不了这里 —— 只有手工打服务端的人才会碰上。
            # 要修就是"回 400/413 前把体读掉丢弃"，**不许**为此放宽 `MAX_BODY` / `MAX_IMAGE_B64`（第 78-79 行那条注释是对的）。
            return self._json(400, {"ok": False, "error": f"请求体大小必须在 1–{MAX_BODY} 字节"})
        try:
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
            return self._json(200, run_once(payload, explain_ask=_ASK, agent_ask=_AGENT_ASK))
        except BadRequest as e:
            return self._json(400, {"ok": False, "error": str(e)})
        except (json.JSONDecodeError, UnicodeDecodeError):
            # 2026-09-25 round14b 问题 1：请求体不是合法 JSON / 不是 UTF-8，是**客户端**发错了
            # ⇒ 必须 400。原先落到下面的兜底 500，等于把"用户输入不合法"登记成"服务端故障"，
            # 还会在终端打出一段误导性堆栈（堆栈会让人去查引擎，而引擎根本没跑）。
            return self._json(400, {"ok": False, "error": "请求体不是合法 JSON"})
        except Exception:                   # noqa: BLE001  引擎异常也要回一个页面能显示的说法
            # ⚠️ 纪律③：回给页面的只能是**人话** —— 异常类型名、异常原文、路径一律不外传
            # （`load_and_prepare` 抛的 `ValueError` 里就带着临时文件的全路径，见
            # `base.py:141`）。细节不丢：打在本机终端上，开发者照样看得到。
            traceback.print_exc()
            return self._json(500, {"ok": False,
                                    "error": "核验过程中出错了，这一跑没有出账本。"
                                             "详细堆栈已打在本服务终端里（本页不回显）。"})


def serve(port: int = 8765, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="demo/serve.py", description="LorealGuard 本地演示服务")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="不自动开浏览器")
    args = ap.parse_args(argv)

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    httpd = serve(args.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"LorealGuard 演示服务：{url}")
    print("  默认零模型零网络（拔网线照出账本）；"
          "勾选「语义解释」才把图送往百炼，勾选「Agent 编排」才把账本摘要送往百炼。")
    print("  产物落在 output/demo/；Ctrl+C 结束。")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:                   # noqa: BLE001  开不了浏览器不影响服务
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
