"""守卫：申诉包**不许印本机目录结构**，且主动承认必须在摘要表里。

round13 A-4（2026-09-25）修的是 Hermes 在真产物上抓到的两处瑕疵：

* **瑕疵① 路径泄露**：`APPEAL.md` 里印出了
  `D:\\LorealGuard\\output\\_appeal_probe\\inplace_q75.jpg`。这份材料是要交给
  平台 / 评审 / 对方创作者的 —— 印出本机目录等于连盘符与用户名一起交出去。
* **瑕疵② 承认埋太深**：`右栏有内容 ≠ 右栏解释了那个坐标` 当时只写在第二节的
  引用块里（真产物第 36 行附近）；读材料的人扫一眼摘要表就走了，看不到。

为什么单独开一个文件而不并进 `tests/test_appeal_pack.py`：那个文件是**纪律守卫**
（不许重算 / 不许润色 / 拒发判据），本文件是**交付面守卫**（交付物上印了什么）。
两条线将来各自的失效原因不一样，混在一起会让"红"指向错误的根因。

⚠️ **登记在案的边界（本文件钉住的当前态，不是已修）**：包内 `files/report.json`
是原报告的**逐字副本**，其中 `image_path` 字段仍是本机绝对路径。这一条**故意不改**：
它是"系统原始产出"的字节级副本，`SHA256SUMS.txt` 记的就是它的哈希 —— 改一个字节，
平台拿自己那份报告来核对时就会对不上号，代价大于收益。所以口径是：
**本工具自己写的两份（APPEAL.md / appeal.json）不印路径；系统原始报告的副本不动。**
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from samples import make_p2_suite as P2                                     # noqa: E402
from tools.make_appeal_pack import build_appeal_pack, display_path          # noqa: E402
from voiceguard.core.contract import ContentItem                            # noqa: E402
from voiceguard.guard import analyze_content                               # noqa: E402


# ---------------------------------------------------------------- 夹具

@pytest.fixture(scope="session")
def _real(tmp_path_factory) -> dict:
    """跑一份**真实**报告，工况取 ③ 记录在案的那条边界（尺寸不变的原位 q75）。

    与 `tests/test_appeal_pack.py` 同源同工况：左栏 3 条坐标级证据、右栏
    `NO_TEXT_PROVIDED` 且 `explains` 为空 ⇒ 摘要表那一行必须落在「否」分支。
    本文件不复用手搓字典 —— 路径字段的形态（绝对 / 相对）正是被测对象，
    手搓一个 `image_path` 会让"断言不含绝对路径"变成对着自己写的字符串断言。
    """
    out = tmp_path_factory.mktemp("appeal_paths")
    base = P2.load_base()
    img = out / "inplace_q75.jpg"
    Image.fromarray(np.asarray(P2.jpeg_roundtrip(base, 75)).astype(np.uint8)).save(
        img, "JPEG", quality=93)
    report = analyze_content(ContentItem(image_path=str(img), meta={"id": "appeal-paths"}),
                            outdir=out, tag="item", sheet=False)
    js = out / "item_report.json"
    assert js.exists(), "analyze_content 没落盘报告 JSON —— 夹具本身失效了"
    d = json.loads(js.read_text(encoding="utf-8"))
    #: 夹具自检：报告里必须**真的**有一个本机绝对路径，否则下面"不含路径"的断言
    #: 是在拿一个不存在的字符串做否定式断言 —— 永远不会红 = 不是检查。
    assert Path(d["image_path"]).is_absolute(), (
        f"夹具报告的 image_path 不是绝对路径（{d['image_path']}）—— 本文件的断言会空转")
    return {"report": d, "image": img, "out": out,
            "copies": {"files/report.json": js.read_bytes(),
                       "files/report.md": (out / "item_report.md").read_bytes()}}


def _build(_real, report: dict | None = None, image=None) -> dict[str, bytes]:
    return build_appeal_pack(report if report is not None else _real["report"],
                             copies=_real["copies"], image=image,
                             rules_fp="0" * 16)


# ---------------------------------------------------------------- ① 不印本机路径

def test_the_material_never_prints_a_local_absolute_path(_real):
    """`APPEAL.md` 与 `appeal.json` 里不许出现被核验图的本机目录。

    自证伪在夹具里（见上）：报告里那个路径确实是绝对的；而且这里**同时**断言
    原报告的逐字副本里它还在 —— 一条是"我们没印"，一条是"这个字符串真实存在"。
    少了后者，把夹具换成相对路径就能让本测试绿得毫无意义。
    """
    files = _build(_real)
    leak = str(Path(_real["report"]["image_path"]).parent)     # 本机目录
    name = Path(_real["report"]["image_path"]).name            # 文件名（可印）

    for rel in ("APPEAL.md", "appeal.json"):
        text = files[rel].decode("utf-8")
        assert leak not in text, (
            f"{rel} 里印出了本机绝对路径 `{leak}` —— 这是要交给第三方的材料")

    # 不是"把整行删了"：文件名这一级信息仍要在场，读材料的人才知道说的是哪张图。
    assert name in files["APPEAL.md"].decode("utf-8"), (
        f"APPEAL.md 里连文件名 `{name}` 都没有了 —— 修路径泄露不等于抹掉出处")

    # 反面对照（登记项）：逐字副本里的绝对路径**故意保留**，见本文件 docstring。
    # 注意比对方式：JSON 里的反斜杠是转义过的（`\\`），所以解析后再比 ——
    # 直接拿原字符串做字节搜索会永远搜不到，那样这条对照就变成了摆设。
    verbatim = json.loads(files["files/report.json"].decode("utf-8"))
    assert verbatim["image_path"] == _real["report"]["image_path"], (
        "`files/report.json` 不再是原报告的逐字副本了 —— 本工具改了系统原始产出？")


def test_original_image_path_is_filename_only(_real):
    """创作者附上的图，清单里只记文件名；但 sha256 核对必须照旧管用。

    两条一起断，是为了防"为了让断言过而把 path 设成 None"——
    那样上一条会绿，而"核对过原图"这件事就假了。
    """
    files = _build(_real, image=_real["image"])
    manifest = json.loads(files["appeal.json"].decode("utf-8"))
    got = manifest["original_image"]

    assert got["verified"] is True, "给对了图却记成没核对 —— 本测试失焦"
    assert got["sha256"] == _real["report"]["fingerprint"], "指纹核对被改动波及"
    assert got["path"] == _real["image"].name, (
        f"original_image.path 应该是文件名，实际是 {got['path']!r}")
    assert not Path(got["path"]).is_absolute() and got["path"] == Path(got["path"]).name


def test_display_path_keeps_relative_paths_untouched():
    """相对路径是**上下文**（`samples/x.jpg` 说得清是什么），绝对路径才是泄露面。

    分界线画在"是不是绝对路径"上，不画在"有没有斜杠"上 —— 后者会把
    `samples/base_neutral.jpg` 也砍成 `base_neutral.jpg`，白丢信息。
    """
    assert display_path(None) is None
    assert display_path("") == ""
    assert display_path("samples/base_neutral.jpg") == "samples/base_neutral.jpg"
    assert display_path(str(Path("samples") / "calib" / "a.jpg")) == str(
        Path("samples") / "calib" / "a.jpg")
    #: 探针**用本机仓库根拼**（跨平台都是绝对路径）—— 原先硬写 `D:/LorealGuard/…`，
    #: 而在 Linux 上那不是绝对路径 ⇒ 谓词落空、断言反而红（2026-10-09 GitHub Actions 实测）。
    assert display_path(ROOT / "output" / "_appeal_probe" / "inplace_q75.jpg") == "inplace_q75.jpg"
    if sys.platform.startswith("win"):      # 反斜杠形态只在 Windows 上算绝对路径
        assert display_path(r"D:\LorealGuard\output\inplace_q75.jpg") == "inplace_q75.jpg"


# ---------------------------------------------------------------- ② 承认提到摘要表

def test_the_coverage_caveat_is_a_summary_table_row(_real):
    """「右栏有内容 ≠ 右栏解释了那个坐标」必须是**摘要表的一行**，不是埋着的引用。

    判据落在"它在第一张表里"这件事上：取 md 里第一个 `---` 之前的头部，
    要求那一行以 `|` 开头（即表格行），而不是 `>` 开头的引用行。
    """
    md = _build(_real)["APPEAL.md"].decode("utf-8")
    head = md.split("\n---\n", 1)[0]
    rows = [ln for ln in head.splitlines()
            if ln.startswith("| 归因是否覆盖左栏坐标 ")]
    assert len(rows) == 1, (
        f"摘要表里没有「归因是否覆盖左栏坐标」这一行（找到 {len(rows)} 行）—— "
        "它必须与「系统给出的等级」同级，读者扫一眼表就要看到")
    assert "**否**" in rows[0], (
        f"本工况（右栏不覆盖左栏坐标）这一行没写「否」：{rows[0]}")
    assert "右栏有内容 ≠ 右栏解释了那个坐标" in rows[0], (
        f"摘要表这一行没把区别说透：{rows[0]}")


def test_the_summary_row_flips_when_attribution_really_covers(_real):
    """自证伪：让某条归因真的 `explains` 一条坐标 ⇒ 同一行必须改口成「是」。

    没有这一条，一个把"否"写死的实现也能骗过上面那条 —— 而那正是
    「一条不会红的检查等于没有检查」的镜像形态。
    """
    covered = json.loads(json.dumps(_real["report"]))
    covered["benign"][0]["explains"] = [covered["evidence"][0]["id"]]
    head = _build(_real, report=covered)["APPEAL.md"].decode("utf-8").split("\n---\n", 1)[0]
    row = [ln for ln in head.splitlines() if ln.startswith("| 归因是否覆盖左栏坐标 ")]
    assert len(row) == 1, "覆盖成立时摘要表反而没有这一行了"
    assert "**是**" in row[0], f"覆盖成立时这一行没改口：{row[0]}"
    assert "右栏有内容 ≠ 右栏解释了那个坐标" not in row[0], (
        "覆盖成立时仍印着「不等于」—— 那是在自贬，读者会以为系统没覆盖")
