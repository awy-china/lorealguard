"""守卫：申诉包**独立校验器**（`tools/verify_appeal_pack.py`）。

一句话概括这个校验器：**把"凭证"从"我们的说法"里分出来** —— 拿到申诉包的人
（评委 / 平台 / 对方创作者）不需要我们的仓库、不装任何东西、不联网，就能自己
把 `SHA256SUMS.txt` 逐字节算一遍。

本文件守的不是功能，是**它的四道检查各自真的会红**。逐条对应：

    ① 真产物四道全过（exit 0）                → test_the_real_pack_passes_all_four_checks
    ② 改一个字节 → 1，且点名文件与两个哈希    → test_one_changed_byte_fails_and_names_the_file
    ③ 缺文件 → 1，且点名"缺失"                → test_a_deleted_file_fails_and_names_it
    ④ 多出未登记文件 → 1；且不冤枉索引自身     → test_an_extra_unregistered_file_fails_and_names_it
    ⑤ 作者面出现 `D:\\` 绝对路径 → 1            → test_absolute_path_on_the_author_face_fails
    ⑥ 同一路径在逐字副本面 → **仍 0**          → test_the_same_absolute_path_on_the_verbatim_face_still_passes
    ⑦ 临时目录名两面都判负                     → test_a_temp_dir_name_fails_on_both_faces
    ⑧ 源码里出现网络 import → 第 4 道红        → test_the_self_scan_goes_red_on_a_planted_network_import
    ⑨ 判据本身有区分力（不会恒红/恒绿）        → test_the_network_detector_is_not_vacuous
    ⑩ 单文件、零项目依赖、不碰 sys.path        → test_the_tool_is_a_lone_file_with_no_project_imports
    ⑪ 拷到别处、仓库不在 sys.path 上也能跑      → test_it_runs_copied_away_without_the_repo
    ⑫ 坏行点名而不是静默丢                     → test_parse_sums_names_bad_lines_instead_of_dropping_them
    ⑬ 目录不存在是用法错（2），不是"包不合格"  → test_a_missing_directory_is_a_usage_error
    ⑭ 没有索引 ⇒ 第 1 道点名，不静默全绿        → test_a_pack_without_the_index_fails_loudly

⚠️ **夹具口径**：优先用**真产物** `output/_appeal_r13/r9page_on/`（工单第 3 节），
但 `output/` 不进仓库（`.gitignore:33`），干净检出里没有它 —— 所以夹具在真产物缺席时
**合成一份同构的包**（同样五件套、同样"作者面干净 / 副本面带本机路径"），绝不因此
让任何一条守卫空转。`test_the_real_pack_passes_all_four_checks` 里有一句断言钉住
"真产物在盘上时，跑的必须就是它"。

写盘变异一律在 `tmp_path` 的**副本**上做，真产物只读（工单：不许删、不许改）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.verify_appeal_pack import (                                     # noqa: E402
    AUTHOR_FACE, NETWORK_MODULES, SUMS_NAME, imported_modules, main,
    network_imports, parse_sums, scan_text_for_leaks, verify,
)

TOOL = ROOT / "tools" / "verify_appeal_pack.py"

#: 工单第 3 节点名的**已生成真实申诉包**（`output/` 不进仓库，缺席时用合成夹具）。
REAL_PACK = ROOT / "output" / "_appeal_r13" / "r9page_on"

#: 一条货真价实的本机绝对路径（作者面不许有、副本面允许有 —— 判据的分界线）。
LOCAL_ABS = r"D:\LorealGuard\output\demo\leak.jpg"

#: 临时目录痕迹（两面都判负）。
TEMP_MARK = "lorealguard_upload_7f3a91"


# ---------------------------------------------------------------- 夹具

def _reindex(pack: Path) -> dict[str, str]:
    """按 `make_appeal_pack` 的格式重写 `SHA256SUMS.txt`（不含自己，自指）。

    变异用例必须重算索引，否则**第 1 道永远先红** —— 那样第 3 道（隐私闸）就算
    完全失灵也照样 exit 1，等于没测。重算之后，唯一可能让包红的就是被测的那一道。
    """
    reg = {}
    for p in sorted(pack.rglob("*")):
        if p.is_file() and p.name != SUMS_NAME:
            reg[p.relative_to(pack).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    (pack / SUMS_NAME).write_text(
        "".join(f"{v}  {k}\n" for k, v in reg.items()), encoding="utf-8")
    return reg


def _synth_pack(dst: Path) -> Path:
    """真产物缺席时的**同构**替身：同样是五件套 + 同样的两面形态。"""
    (dst / "files").mkdir(parents=True, exist_ok=True)
    report = {
        "content_id": "synth", "kind": "image", "version": "0.0.0",
        "generated_at": "2026-01-01 00:00:00",
        "fingerprint": "0" * 64,
        # 逐字副本面**故意**带本机绝对路径（与真产物同形：报告本来就有）。
        "image_path": r"D:\LorealGuard\samples\base_neutral.jpg",
        "evidence": [], "benign": [], "verdict": {"tier": "未触发", "fired": []},
        "evidence_files": [r"D:\LorealGuard\output\demo\synth_report.md"],
    }
    (dst / "files" / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (dst / "files" / "report.md").write_text(
        "# 原报告（合成替身）\n\n- 输入：`D:\\LorealGuard\\samples\\base_neutral.jpg`\n",
        encoding="utf-8")
    # 作者面**一个本机路径都没有**（这是真产物的形态，也是判据要保的东西）。
    (dst / "APPEAL.md").write_text(
        "# 申诉材料（自证包）· 内容 synth\n\n> 本材料不主张内容为真。\n", encoding="utf-8")
    (dst / "appeal.json").write_text(json.dumps({
        "material": "申诉材料（自证包）",
        "files_included": ["APPEAL.md", "appeal.json", SUMS_NAME,
                           "files/report.json", "files/report.md"],
        "contains_image_bytes": False,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    _reindex(dst)
    return dst


@pytest.fixture(scope="session")
def pack_source() -> Path:
    """真产物在盘上就用它；不在（干净检出）才退回合成替身。"""
    return REAL_PACK if (REAL_PACK / SUMS_NAME).is_file() else Path("<synth>")


@pytest.fixture
def pack(tmp_path, pack_source) -> Path:
    """每个用例一份**可改写**的副本 —— 真产物全程只读。"""
    dst = tmp_path / "pack"
    if pack_source.is_dir():
        shutil.copytree(pack_source, dst)
    else:
        _synth_pack(dst)

    # 夹具自检：副本面必须**真的**带本机绝对路径。否则下面"副本面带路径仍绿"那条
    # 是在对一个不存在的字符串做否定式断言 —— 永远不会红 = 不是检查。
    verbatim = (dst / "files" / "report.json").read_text(encoding="utf-8")
    assert scan_text_for_leaks(verbatim), (
        "夹具的逐字副本面里没有任何本机绝对路径 —— 第 ⑥ 条守卫会空转")
    return dst


def _run(capsys, pack: Path, *extra: str) -> tuple[int, str]:
    """跑 CLI（走 `main`，不是绕过它调内部函数），返回 `(退出码, 输出)`。"""
    rc = main([str(pack), *extra])
    return rc, capsys.readouterr().out


# ---------------------------------------------------------------- ① 真产物全过

def test_the_real_pack_passes_all_four_checks(capsys, pack, pack_source):
    """四道全过 ⇒ exit 0，且输出里四道各自的结论都在（不是"没报错就算过"）。"""
    if REAL_PACK.is_dir():
        # 真产物在盘上时，跑的**必须**是它 —— 否则这条守卫可能一直在测合成替身。
        assert pack_source == REAL_PACK, "真产物在盘上却没用它当夹具"

    rc, out = _run(capsys, pack)
    assert rc == 0, f"真产物没通过自证（exit {rc}）：\n{out}"
    for key in ("哈希复核", "内部引用一致", "可提交性（隐私闸）", "零网络自证"):
        assert f"[OK] " in out and key in out, f"输出里没有「{key}」这一道的结果：\n{out}"
    assert "四道全过" in out, f"结论段没说明四道全过：\n{out}"
    # 明细要求（工单 §2.1）：要能说清"我算了哪些字节"。
    assert "本轮重算的字节" in out, f"没交代重算了哪些字节：\n{out}"

    checks = verify(pack)
    assert [c.ok for c in checks] == [True] * 4, (
        f"逐道复跑时有道不通过：{[(c.key, c.problems) for c in checks if not c.ok]}")


# ---------------------------------------------------------------- ② 改一个字节

def test_one_changed_byte_fails_and_names_the_file(capsys, pack):
    """改 `APPEAL.md` 一个字节 ⇒ exit 1，点名它，并**同时印出登记值与实算值**。

    只断言"名字出现了"是不够的：那样一个"随便报个错"的实现也能过。这里要求
    输出里**同时**有旧哈希与它点名的文件 —— 读者能拿这两样自己去复核。
    """
    old = hashlib.sha256((pack / "APPEAL.md").read_bytes()).hexdigest()
    with (pack / "APPEAL.md").open("ab") as f:
        f.write(b"x")

    rc, out = _run(capsys, pack)
    assert rc == 1, f"改了一个字节却仍然 exit {rc}"
    assert "哈希复核" in out, f"没点出是哪一道：\n{out}"
    assert "APPEAL.md" in out, f"没点名被改的文件：\n{out}"
    assert old in out, f"没印出登记值 {old[:12]}… —— 读者无从自己复核：\n{out}"
    assert "对不上" in out, f"没说清是「对不上」还是「缺失」：\n{out}"


# ---------------------------------------------------------------- ③ 缺文件

def test_a_deleted_file_fails_and_names_it(capsys, pack):
    (pack / "files" / "report.md").unlink()
    rc, out = _run(capsys, pack)
    assert rc == 1, f"删了已登记的文件却仍然 exit {rc}"
    assert "缺失" in out and "files/report.md" in out, (
        f"没点名缺失的文件：\n{out}")


# ---------------------------------------------------------------- ④ 多出未登记文件

def test_an_extra_unregistered_file_fails_and_names_it(capsys, pack):
    """多出一个未登记文件 ⇒ exit 1 并点名；**索引自身不算多出**（自指，不能恒红）。"""
    (pack / "notes.txt").write_text("我是一份没被索引的文件\n", encoding="utf-8")

    rc, out = _run(capsys, pack)
    assert rc == 1, f"多出未登记文件却仍然 exit {rc}"
    assert "多出未登记" in out and "notes.txt" in out, f"没点名多出的文件：\n{out}"
    # 反面对照：`SHA256SUMS.txt` 不能因为"没登记自己"被判成多出 —— 那是一条恒红的判据。
    assert f"多出未登记：`{SUMS_NAME}`" not in out, (
        f"把索引自身当成了多出文件（自指）—— 这条判据会恒红：\n{out}")


# ---------------------------------------------------------------- ⑤⑥ 两面分档

def test_absolute_path_on_the_author_face_fails(capsys, pack):
    """作者面（我们撰写的产物）出现本机绝对路径 ⇒ 不通过。

    先重算索引，把第 1 道摘干净：这条红必须**由隐私闸自己**给出。
    """
    with (pack / "APPEAL.md").open("a", encoding="utf-8") as f:
        f.write(f"\n- 复算用的原图：`{LOCAL_ABS}`\n")
    _reindex(pack)
    checks = {c.key: c for c in verify(pack)}
    assert checks["哈希复核"].ok, f"重算索引后第 1 道仍红（用例自身没搭好）：{checks['哈希复核'].problems}"

    rc, out = _run(capsys, pack)
    assert rc == 1, f"作者面带本机绝对路径却仍然 exit {rc}：\n{out}"
    assert "可提交性（隐私闸）" in out and "作者面" in out, f"没点出是哪一道、哪一面：\n{out}"
    assert "APPEAL.md" in out and LOCAL_ABS[:24] in out, (
        f"没点名文件与命中的路径片段：\n{out}")


def test_the_same_absolute_path_on_the_verbatim_face_still_passes(capsys, pack):
    """**同一条**绝对路径落在逐字副本面 ⇒ 只登记、不判负（仍 exit 0）。

    这是本文件最关键的一条：它把"分档"与"一律放行"区分开 —— ⑤ 与 ⑥ 一起才有意义。
    两个形态都测：真产物里**本来就有**的（report.json 的 image_path），以及新注入的。
    """
    existing = (pack / "files" / "report.json").read_text(encoding="utf-8")
    assert "D:\\" in existing, "真产物的副本面里本机路径不见了 —— 夹具与前提都对不上"

    with (pack / "files" / "report.json").open("a", encoding="utf-8") as f:
        f.write(f"\n# 新注入的一条：{LOCAL_ABS}\n")
    _reindex(pack)

    rc, out = _run(capsys, pack)
    assert rc == 0, f"副本面保留本机路径（口径允许）却 exit {rc}：\n{out}"
    assert "登记（不判负）" in out and LOCAL_ABS[:24] in out, (
        f"副本面的命中既没被登记、也没印出来 —— 那就是在静默略过：\n{out}")


# ---------------------------------------------------------------- ⑦ 临时目录名

@pytest.mark.parametrize("rel", ["APPEAL.md", "files/report.md"])
def test_a_temp_dir_name_fails_on_both_faces(capsys, pack, rel):
    """临时目录名**两面都判负**（参数化覆盖作者面与副本面）。

    副本面那一条是刻意的：它证明"分档"不是一张对所有命中都生效的免罪牌。
    """
    with (pack / rel).open("a", encoding="utf-8") as f:
        f.write(f"\n- 从临时目录跑的：C:\\Users\\x\\AppData\\Local\\Temp\\{TEMP_MARK}\\item_report.json\n")
    _reindex(pack)

    rc, out = _run(capsys, pack)
    assert rc == 1, f"{rel} 出现临时目录名却仍然 exit {rc}：\n{out}"
    assert "临时目录名" in out and rel in out, f"没点名文件与原因：\n{out}"


# ---------------------------------------------------------------- ⑧ 自证伪：网络

def test_the_self_scan_goes_red_on_a_planted_network_import(capsys, pack, tmp_path):
    """把工具源码拷一份、塞进 `import urllib.request` ⇒ 第 4 道必须红、点名模块。

    为什么用"改过的真源码"而不是手写一段假源码：手写假源码只能证明 AST 判据存在，
    证明不了**第 4 道真的在扫自己的文件**（一个把检查写死成 `ok=True` 的实现也能过）。
    """
    planted = tmp_path / "planted_tool.py"
    source = TOOL.read_text(encoding="utf-8")
    assert network_imports(source) == [], "被扫的原始源码里已经有网络 import —— 前提不成立"
    planted.write_text(source + "\nimport urllib.request  # planted\n", encoding="utf-8")

    checks = verify(pack, source_path=planted)
    net = [c for c in checks if c.key == "零网络自证"][0]
    assert not net.ok, "源码里塞了网络 import，第 4 道却是绿的 —— 这道自证是摆设"
    assert any("urllib" in p for p in net.problems), (
        f"红了但没说清是哪个模块：{net.problems}")

    # 正面对照：真源码这一道必须绿（否则上面那句"红了"毫无信息量）。
    real = [c for c in verify(pack) if c.key == "零网络自证"][0]
    assert real.ok, f"工具自己的源码没通过零网络自证：{real.problems}"
    assert "命中：无" in "\n".join(real.details), (
        f"没有把「命中的网络模块：无」打给读者看（工单 §2.1 第 4 道）：{real.details}")


def test_the_network_detector_is_not_vacuous():
    """判据本身要有区分力：该命中的命中、不该命中的不命中。"""
    hits = network_imports(
        "import socket\n"
        "from urllib import request\n"
        "import ssl\n"
        "from http import client\n"
        "import requests\n"
    )
    assert sorted(h["module"] for h in hits) == sorted(NETWORK_MODULES), (
        f"五个网络模块没全扫出来：{hits}")

    clean = network_imports(
        "import json\nfrom pathlib import Path\n"
        "from voiceguard.guard import sha256_file\n"      # 项目内模块不是网络模块
    )
    assert clean == [], f"把非网络模块误判成网络模块：{clean}"

    # 行号也要给对：第三方要能自己跳过去看那一行。
    assert network_imports("import json\nimport socket\n")[0]["lines"] == [2]


def test_text_leak_scanner_does_not_mistake_a_url_for_a_drive_letter():
    """`https://…` 不是「盘符 s」—— 真产物里的法条 URL 实测触发过这条误报。

    没有这条守卫，"盘符"判据会宽到把每份带来源链接的申诉包都判红，
    而那正是把守卫变成噪声的典型死法。
    """
    url = '  "url": "https://www.cac.gov.cn/2025-03/14/c_1743654684782215.htm",'
    assert scan_text_for_leaks(url) == [], f"把网址读成了本机路径：{scan_text_for_leaks(url)}"
    assert scan_text_for_leaks(r'  "image_path": "D:\LorealGuard\samples\a.jpg",'), \
        "真正的盘符路径反而扫不出来 —— 判据被收紧过头了"


# ---------------------------------------------------------------- ⑩ 单文件零依赖

def _touches_sys_path(source: str) -> bool:
    """源码里有没有**真的**碰 `sys.path`（AST 级，不是子串）。

    子串判据在这里是错的：工具的 docstring 里必须能写「不碰 `sys.path`」这句承诺 ——
    拿子串去扫，一句自我声明就把它自己判红（本文件第一版就是这么红的）。
    """
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Attribute) and node.attr == "path"
                and isinstance(node.value, ast.Name) and node.value.id == "sys"):
            return True
    return False


def test_the_tool_is_a_lone_file_with_no_project_imports():
    """结构性检查：单文件、纯标准库、不碰 `sys.path`。

    为什么扫源码而不是"跑一遍看结果"：`sys.path.insert` 只在**本仓库里**才危险
    （把它拷到别处就跑不起来了），本机跑一遍永远看不到 —— 本机有仓库。
    """
    source = TOOL.read_text(encoding="utf-8")
    mods = set(imported_modules(source))
    assert "voiceguard" not in mods and "tools" not in mods, (
        f"校验器 import 了项目内模块 {sorted(mods)} —— 拷到别处就跑不起来，"
        "而「拷到别处能跑」正是这个文件存在的理由")

    # 自证伪：判据得先能认出一次真的 sys.path 改动，否则下面那句是空转。
    assert _touches_sys_path("import sys\nsys.path.insert(0, 'x')\n"), \
        "判据认不出真的 sys.path 改动 —— 这条检查不会红"
    assert not _touches_sys_path(source), (
        "校验器动了 sys.path —— 它必须能在任何 cwd 下跑（仓库不在路径上时也一样）")

    stdlib_only = {"__future__", "argparse", "ast", "dataclasses", "hashlib",
                   "json", "pathlib", "re", "sys"}
    assert mods <= stdlib_only, f"出现非标准库 import：{sorted(mods - stdlib_only)}"
    for banned in NETWORK_MODULES:
        assert banned not in mods, f"校验器自己的源码里出现网络模块 {banned}"


def test_it_runs_copied_away_without_the_repo():
    """拷到 tmp、仓库不在 `sys.path` 上、cwd 也不是仓库 ⇒ 仍然 exit 0。

    这条是"第三方能不能真的跑起来"的**行为**证据：⑩ 只是源码结构，
    结构对但缺个文件（比如依赖同目录的兄弟模块）照样跑不起来。
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        shutil.copy2(TOOL, home / "verify_appeal_pack.py")
        dst = home / "pack"
        if REAL_PACK.is_dir():
            shutil.copytree(REAL_PACK, dst)
        else:
            _synth_pack(dst)

        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONIOENCODING"] = "utf-8"
        #: `-I` = 隔离模式：忽略环境变量与用户 site-packages，最贴近"第三方裸跑"。
        p = subprocess.run([sys.executable, "-I", "verify_appeal_pack.py", "pack"],
                           cwd=str(home), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert p.returncode == 0, (
            f"拷到别处跑不动（exit {p.returncode}）：\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
        assert "命中的网络模块" in p.stdout, f"没打出自证那一段：\n{p.stdout}"
        assert "坏" not in p.stderr, f"stderr 里有异常：\n{p.stderr}"


# ---------------------------------------------------------------- ⑫⑬⑭ 结构边界

def test_parse_sums_names_bad_lines_instead_of_dropping_them():
    """坏行必须被点名 —— 一条被静默跳过的行，就是一个不受核对的文件。"""
    good = "a" * 64
    reg, bad = parse_sums(
        f"{good}  files/report.json\n"
        f"{good} *files/report.md\n"          # sha256sum 的二进制标记，宽容
        "\n"
        "不是哈希  x.txt\n"
        f"{'b' * 32}  short.txt\n"
        f"{good}\n"
        f"{good}  files/report.json\n"        # 重复登记：后一条会被无声盖掉
    )
    assert set(reg) == {"files/report.json", "files/report.md"}, f"合法行没解析全：{reg}"
    assert len(bad) == 4, f"坏行数不对（应 4 条：非哈希 / 短哈希 / 只有哈希 / 重复）：{bad}"
    assert any("重复登记" in b for b in bad), f"重复登记没被点名：{bad}"


def test_a_missing_directory_is_a_usage_error(capsys, tmp_path):
    """目录不存在 ⇒ 2（用法错），与"包不合格 ⇒ 1"分开。

    分开的理由：第三方拿到 1 会以为"包被改过"，实际只是路径写错了。
    """
    rc = main([str(tmp_path / "根本没有这个目录")])
    out = capsys.readouterr().out
    assert rc == 2, f"用法错却返回 {rc}"
    assert "不是目录" in out, f"没告诉使用者是路径问题：\n{out}"


def test_a_pack_without_the_index_fails_loudly(capsys, pack):
    """没有 `SHA256SUMS.txt` ⇒ exit 1，且第 1 道**点名**它，不静默全绿。"""
    (pack / SUMS_NAME).unlink()
    rc, out = _run(capsys, pack)
    assert rc == 1, f"包没有索引却 exit {rc}"
    assert SUMS_NAME in out and "没有" in out, f"没点名缺索引：\n{out}"


def test_it_only_reads_never_writes_the_pack(capsys, pack):
    """校验器**只验不改**：跑完之后包内每个文件的字节与文件清单都不许变。

    "我不会写坏你的包"是承诺；这条把它变成可复跑的测量 —— 顺带保证第三方
    可以在**只读介质**（光盘、只读挂载）上跑它。
    """
    def snapshot() -> dict[str, str]:
        return {p.relative_to(pack).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in pack.rglob("*") if p.is_file()}

    before = snapshot()
    rc, _ = _run(capsys, pack)
    assert rc == 0, "夹具包没先通过自证 —— 这条守卫的前提不成立"
    assert snapshot() == before, (
        "跑一遍校验器之后包变了 —— 它必须是纯读的（第三方可能在只读介质上跑它）")


def test_it_never_claims_a_verdict_of_its_own():
    """校验器不下真假结论 —— 它只能谈"字节对不对"（铁律 3 同样管核对工具）。

    两向都断：**不许**出现肯定式断言的字符串字面量；**必须**出现"不判断内容是否真实"
    这句免责。只断前一半的话，把这句话整段删掉也能绿。
    """
    source = TOOL.read_text(encoding="utf-8")
    for banned in ("内容为真", "判定为伪造", "确认为伪造", "认定为伪造", "已确认违规"):
        assert f'"{banned}"' not in source, (
            f"校验器里有肯定式断言的字符串字面量 `{banned}` —— 它只核对字节，不下结论")
    assert "不判断内容是否真实" in source, (
        "结论段没有把「只核字节、不判真假」说清楚 —— 读者会以为四道全过就等于内容可信")
