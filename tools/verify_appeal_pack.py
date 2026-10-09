#!/usr/bin/env python
"""申诉包**独立校验器** —— 拿到包的人不必有我们的仓库，也能自己算一遍。

为什么单独一个文件：`tools/make_appeal_pack.py` 产出的 `SHA256SUMS.txt` 只有回到
我们仓库跑 `tests/` 才能核对 —— 那等于「凭证」还是「我们的说法」。本文件是给
**第三方**（评审 / 平台 / 对方创作者）用的：把一个文件拷走，`python verify_appeal_pack.py <包目录>`
就能得到四道检查的结论与明细。

三条自我约束（构造上保证，不是口头承诺）：

1. **单文件、纯标准库**：只 import `argparse` / `ast` / `hashlib` / `json` / `re` / `sys` /
   `dataclasses` / `pathlib`。不 import 本项目任何模块，也不碰 `sys.path`。
2. **零网络**：源码里连 `socket` / `urllib` / `http` / `requests` / `ssl` 都不出现 ——
   第 4 道检查就是**扫自己**，把"我扫了哪些 import"打给读者看。
3. **只验不改**：全程只读，不写、不删包内任何字节。

四道检查：

1. **哈希复核**：逐条读 `SHA256SUMS.txt` 重算每个文件的 `sha256`；不一致 / 缺失 /
   目录里多出未登记文件，**分别点名**。
2. **内部引用一致**：`appeal.json` 里 `files_included` 引用的文件在包里都要在、
   且都已被 `SHA256SUMS.txt` 登记；逐字副本面的实际 sha 与登记值逐条对照。
3. **可提交性（隐私闸）**：包内文本产物里不许有本机绝对路径与临时目录名。
   ⚠️ 按「作者面 / 逐字副本面」**分档**，口径照 `tools/make_appeal_pack.py:display_path()`：
   * **作者面** = 我们撰写的 `APPEAL.md` / `appeal.json` / `SHA256SUMS.txt`：
     出现本机绝对路径 ⇒ **不通过**（这是我们的话，不该带本机路径）。
   * **逐字副本面** = `files/` 下的原报告副本：本职就是照抄，允许保留报告原有的
     绝对路径 ⇒ **只登记、不判负**。
   * **临时目录名**（如 `lorealguard_upload_`、`/tmp/`、`\\Temp\\`）⇒ 两面**一律不通过**：
     从临时目录跑出来的包根本没有可追溯性。
4. **零网络自证**：`ast` 解析本文件自己的源码，打印命中的网络模块（应为「无」）。

退出码：四道全过 `0`；任一「不通过」`1`；**用错**（目录不存在 / 不是目录）`2`。
不通过时一律点名「哪一道 + 哪个文件 + 差在哪」。

用法：

    python tools/verify_appeal_pack.py output/appeal/<包名>
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: 包根下**我们撰写的**产物（作者面）。出现本机绝对路径 ⇒ 不通过。
AUTHOR_FACE = ("APPEAL.md", "appeal.json", "SHA256SUMS.txt")

#: 逐字副本面前缀（原报告的字节级副本）。允许保留报告原有的绝对路径。
VERBATIM_PREFIX = "files/"

SUMS_NAME = "SHA256SUMS.txt"

#: 命中即算「网络相关」的顶层模块名（第 4 道检查的清单，也是给读者看的清单）。
NETWORK_MODULES = ("http", "requests", "socket", "ssl", "urllib")

#: 本机绝对路径。只认"盘符 / 家目录 / 系统目录"这类**根式**写法 ——
#: 相对路径（`samples/x.jpg`）是上下文、不是泄露面（同 `display_path()` 的分界线）。
#:
#: `(?<![0-9A-Za-z])` 这个前视边界不是装饰：没有它，`https://www.cac.gov.cn/...`
#: 里的 `s:/` 会被读成「盘符 s」—— 真产物上实测到的误报（法条 URL 就在报告里）。
#: 判据必须能区分"`D:\...` 是路径"与"`https://` 是网址"，否则每份带来源链接的包都红。
ABS_PATH_PATTERNS = (
    ("Windows 盘符绝对路径", re.compile(r"(?<![0-9A-Za-z])[A-Za-z]:[\\/]")),
    ("POSIX 家目录/系统目录绝对路径", re.compile(r"/(?:home|Users|root|opt|mnt)/")),
)

#: 临时目录痕迹。**两面都判负**：从临时目录跑出来的包不可追溯。
TEMP_PATTERNS = (
    ("临时目录名 lorealguard_upload_", re.compile(r"lorealguard_upload_", re.I)),
    ("POSIX 临时目录", re.compile(r"/(?:tmp|var/folders)/", re.I)),
    ("Windows 临时目录", re.compile(r"\\(?:Temp|AppData[\\/]Local[\\/]Temp)[\\/]", re.I)),
)

MAX_SNIPPET = 120


# ---------------------------------------------------------------- 结果容器

@dataclass
class Check:
    """一道检查的结果。`problems` 非空 ⇒ 这一道不通过（且每条都点名）。"""

    key: str
    ok: bool = True
    details: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.problems.append(msg)

    def note(self, msg: str) -> None:
        self.details.append(msg)


# ---------------------------------------------------------------- 判据（纯函数）

def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def parse_sums(text: str) -> tuple[dict[str, str], list[str]]:
    """解析 `SHA256SUMS.txt`。返回 `({相对路径: 登记 sha256}, [格式错误的行])`。

    宽容的地方：`sha256sum` 的二进制标记 `*` 与多余空白。不宽容的地方：
    格式不对的行**不静默丢弃** —— 返回给调用方点名（一条被悄悄跳过的行
    = 一个不受检查的文件）。
    """
    registered: dict[str, str] = {}
    malformed: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            malformed.append(f"第 {lineno} 行不是「sha256  路径」两段式：{raw[:MAX_SNIPPET]}")
            continue
        digest, rel = parts[0].strip().lower(), parts[1].strip().lstrip("*")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            malformed.append(f"第 {lineno} 行的 sha256 不是 64 位十六进制：{raw[:MAX_SNIPPET]}")
            continue
        if not rel:
            malformed.append(f"第 {lineno} 行只有哈希、没有路径：{raw[:MAX_SNIPPET]}")
            continue
        if rel in registered:
            malformed.append(f"第 {lineno} 行重复登记了 `{rel}`（前一条 sha 会被无声盖掉）")
            continue
        registered[rel] = digest
    return registered, malformed


def pack_files(pack: Path) -> list[str]:
    """包内所有文件的相对路径（posix 分隔符，排序后）。"""
    return sorted(p.relative_to(pack).as_posix() for p in pack.rglob("*") if p.is_file())


def face_of(rel: str) -> str:
    """`"author"` = 我们撰写的产物；`"verbatim"` = 逐字副本面（只登记不判负）。"""
    return "author" if rel in AUTHOR_FACE else "verbatim"


def text_of(path: Path) -> str | None:
    """读成文本；非 UTF-8（二进制）返回 `None`（由调用方登记，不静默略过）。"""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def scan_text_for_leaks(text: str) -> list[dict]:
    """扫一段文本里的泄露面。返回 `[{kind, label, match, line, snippet}]`。

    `kind` 取 `"temp_name"` / `"abs_path"`：前者两面都判负，后者只有作者面判负。
    按行报，是为了让读者能**直接跳到那一行**去核对，而不是只被告知"文件里有"。
    """
    out: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        for kind, patterns in (("temp_name", TEMP_PATTERNS), ("abs_path", ABS_PATH_PATTERNS)):
            for label, pat in patterns:
                m = pat.search(raw)
                if not m:
                    continue
                if (kind, m.group(0), lineno) in seen:
                    continue
                seen.add((kind, m.group(0), lineno))
                out.append({"kind": kind, "label": label, "match": m.group(0),
                            "line": lineno, "snippet": raw.strip()[:MAX_SNIPPET]})
    return out


def imported_modules(source: str) -> dict[str, list[int]]:
    """AST 解析源码 → `{顶层模块名: [行号…]}`（`from X import y` 记 `X`）。"""
    tree = ast.parse(source)
    found: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.setdefault(alias.name.split(".")[0], []).append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.setdefault(node.module.split(".")[0], []).append(node.lineno)
    return found


def network_imports(source: str) -> list[dict]:
    """源码里命中的网络 import。返回 `[{module, lines}]`（空 = 干净）。"""
    found = imported_modules(source)
    return [{"module": m, "lines": sorted(found[m])}
            for m in sorted(found) if m in NETWORK_MODULES]


# ---------------------------------------------------------------- ① 哈希复核

def check_hashes(pack: Path, registered: dict[str, str], malformed: list[str]) -> Check:
    chk = Check(key="哈希复核")
    present = pack_files(pack)
    recomputed = 0
    total_bytes = 0

    for line in malformed:
        chk.fail(f"{SUMS_NAME} {line}")

    for rel, want in registered.items():
        path = pack / rel
        if not path.is_file():
            chk.fail(f"缺失：`{rel}` 在 SHA256SUMS.txt 里登记了（sha256 {want[:12]}…），"
                     f"但包目录里没有这个文件 —— 包不完整")
            continue
        blob = path.read_bytes()
        got = sha256_bytes(blob)
        recomputed += 1
        total_bytes += len(blob)
        if got == want:
            chk.note(f"{rel}  {len(blob):,} B  实算 {got[:12]}…  登记 {want[:12]}…  [OK] 一致")
        else:
            chk.fail(f"对不上：`{rel}` 登记 {want}，实算 {got}"
                     f"（{len(blob):,} B）—— 这份文件在登记之后被改过，或登记的就是另一个字节")

    extras = [rel for rel in present if rel not in registered and rel != SUMS_NAME]
    for rel in extras:
        size = (pack / rel).stat().st_size
        chk.fail(f"多出未登记：`{rel}`（{size:,} B）在包里，但 SHA256SUMS.txt 里没有它 —— "
                 f"未登记的字节 = 不受核对的字节，第三方无从判断它该不该在包里")
    if not extras:
        chk.note(f"未登记的额外文件：无（`{SUMS_NAME}` 自己不登记自己 —— 自指：把自己的哈希"
                 f"写进自己，写进去的瞬间哈希就变了；口径同 make_appeal_pack.build_appeal_pack）")
    chk.note(f"本轮重算的字节：{recomputed} 个已登记文件 / {total_bytes:,} B"
             f"（整段读入后算 sha256，不解析内容、不改一个字节）")
    return chk


# ---------------------------------------------------------------- ② 内部引用一致

def check_references(pack: Path, registered: dict[str, str]) -> Check:
    chk = Check(key="内部引用一致")
    ap = pack / "appeal.json"
    if not ap.is_file():
        chk.fail("`appeal.json` 不在包里 —— 无法核对它引用的文件")
        return chk
    try:
        manifest = json.loads(ap.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        chk.fail(f"`appeal.json` 解析失败（{type(e).__name__}: {e}）—— 清单本身读不出来")
        return chk
    if not isinstance(manifest, dict):
        chk.fail(f"`appeal.json` 顶层不是对象（是 {type(manifest).__name__}）")
        return chk

    refs = manifest.get("files_included")
    if not isinstance(refs, list) or not refs:
        chk.fail("`appeal.json` 的 `files_included` 为空或不是列表 —— "
                 "清单没说自己含哪些文件，第三方就无从对照")
        return chk

    for rel in dict.fromkeys(str(r) for r in refs):        # 去重但保序
        path = pack / rel
        exists = path.is_file()
        is_registered = rel in registered
        tag = f"在包中：{'是' if exists else '否'}；{SUMS_NAME} 已登记：{'是' if is_registered else '否'}"
        if rel == SUMS_NAME:
            # 索引自己不登记自己（自指，同第 1 道的豁免）：要求它出现在自己的哈希表里
            # 是不可能满足的条件，那不叫检查、叫恒红。
            if not exists:
                chk.fail(f"引用落空：`appeal.json` 的 files_included 列了 `{SUMS_NAME}`，"
                         f"但包里没有它（{tag}）")
            else:
                chk.note(f"{SUMS_NAME}  {tag}；但它**不登记自己**（自指，口径同第 1 道）"
                         f"—— 引用它、核对它的哈希是两件事，前者这里已确认")
            continue
        if not exists:
            chk.fail(f"引用落空：`appeal.json` 的 files_included 列了 `{rel}`，但包里没有它（{tag}）")
            continue
        if not is_registered:
            chk.fail(f"引用未登记：`{rel}` 在包里也被 appeal.json 引用，但 SHA256SUMS.txt 里"
                     f"没有它的哈希（{tag}）—— 引用了却不可核对")
            continue
        blob = path.read_bytes()
        got = sha256_bytes(blob)
        if rel.startswith(VERBATIM_PREFIX):
            # 工作单点名的两条（files/report.json / files/report.md）走的就是这里。
            chk.note(f"{rel}  {len(blob):,} B  实算 {got[:12]}…  登记 {registered[rel][:12]}…  "
                     f"[OK] 逐字副本与登记值一致（{tag}）")
        else:
            chk.note(f"{rel}  {tag}（哈希对照见第 1 道）")
    return chk


# ---------------------------------------------------------------- ③ 可提交性（隐私闸）

def check_privacy(pack: Path) -> Check:
    chk = Check(key="可提交性（隐私闸）")
    scanned = 0
    for rel in pack_files(pack):
        text = text_of(pack / rel)
        if text is None:
            chk.note(f"{rel}：非 UTF-8 文本，跳过扫描（它是否该在包里由第 1 道回答）")
            continue
        scanned += 1
        leaks = scan_text_for_leaks(text)
        if not leaks:
            chk.note(f"{rel}：扫描 {len(text.splitlines())} 行 / {len(text.encode('utf-8')):,} B，"
                     f"未出现本机绝对路径与临时目录名")
            continue
        author = face_of(rel) == "author"
        for lk in leaks:
            where = f"`{rel}:{lk['line']}` 命中「{lk['label']}」（{lk['match']}）：{lk['snippet']}"
            if lk["kind"] == "temp_name":
                chk.fail(f"临时目录名：{where} —— 临时目录里跑出来的包不可追溯，"
                         f"作者面与逐字副本面**一律不通过**")
            elif author:
                chk.fail(f"本机绝对路径出现在**作者面**：{where} —— 这是我们自己写的产物，"
                         f"它要交给第三方，不该带本机目录结构（口径同 display_path()）")
            else:
                chk.note(f"登记（不判负）：{where} —— 逐字副本面照抄原报告，"
                         f"原报告本来就带本机路径（口径同 display_path()）")
        if not author:
            chk.note(f"{rel}：按**逐字副本面**处理（不判负；只登记上面这些命中）")
    chk.note(f"隐私闸：本次扫了 {scanned} 个文本产物；"
             f"作者面 = {', '.join(AUTHOR_FACE)}；逐字副本面 = `{VERBATIM_PREFIX}` 下文件")
    return chk


# ---------------------------------------------------------------- ④ 零网络自证

def check_no_network(source_path: Path) -> Check:
    """扫**自己的源码**，确认没有网络 import。

    这一条是给第三方看的，不是给我们看的：它把"凭什么信你不联网"从承诺变成
    可复现的一步。扫不到源码（比如被拷成一段字符串跑）时如实判不通过，不假装扫过。
    """
    chk = Check(key="零网络自证")
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as e:
        chk.fail(f"读不到自己的源码（{source_path}）：{e} —— 这一道无法自证，按不通过处理")
        return chk
    try:
        found = imported_modules(source)
    except SyntaxError as e:
        chk.fail(f"自己的源码 AST 解析失败（{e}）—— 扫不成，按不通过处理")
        return chk
    hits = network_imports(source)
    chk.note(f"我扫的是自己：{source_path}（{len(source.encode('utf-8')):,} B，"
             f"sha256 {sha256_bytes(source.encode('utf-8'))[:12]}…）")
    chk.note(f"AST 解析到的 import：{len(found)} 个顶层模块"
             f"（{', '.join(sorted(found)) or '无'}）")
    if hits:
        for h in hits:
            chk.fail(f"自己的源码里出现网络模块 `{h['module']}`（第 "
                     f"{', '.join(str(n) for n in h['lines'])} 行）—— 零网络自证不成立")
    else:
        chk.note(f"命中的网络模块清单 {list(NETWORK_MODULES)} 里，命中：无")
    return chk


# ---------------------------------------------------------------- 编排 + 输出

def verify(pack: Path, source_path: Path | None = None) -> list[Check]:
    """跑四道检查。`source_path` 只给测试用（默认 = 本文件自己）。"""
    sums = pack / SUMS_NAME
    if sums.is_file():
        registered, malformed = parse_sums(text_of(sums) or "")
    else:
        registered, malformed = {}, []
    c1 = check_hashes(pack, registered, malformed)
    if not sums.is_file():
        c1.fail(f"包根没有 `{SUMS_NAME}` —— 没有索引就没有可核对的东西，这一道不通过")
    return [c1, check_references(pack, registered), check_privacy(pack),
            check_no_network(source_path or Path(__file__))]


def _print_check(chk: Check, idx: int, total: int) -> None:
    print(f"\n[{idx}/{total}] {chk.key} —— {'[OK] 通过' if chk.ok else '[FAIL] 不通过'}")
    for line in chk.details:
        print(f"  · {line}")
    for line in chk.problems:
        print(f"  ! {line}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="verify_appeal_pack",
        description="独立核对一份申诉包（单文件、纯标准库、零网络）。"
                    "只读不写：不校验的内容一个字都不改。")
    ap.add_argument("pack", help="申诉包目录（含 SHA256SUMS.txt 的那一层）")
    ap.add_argument("--quiet", action="store_true", help="只打结论与失败项，不打逐文件明细")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):        # Windows 控制台默认码页打不出中文
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

    pack = Path(args.pack)
    if not pack.exists() or not pack.is_dir():
        print(f"[FAIL] 不是目录（或不存在）：{pack}")
        print("       用法：python verify_appeal_pack.py <含 SHA256SUMS.txt 的包目录>")
        return 2

    me = Path(__file__)
    self_sha = sha256_bytes(me.read_bytes())[:12] if me.is_file() else "（读不到自己）"
    print("== 申诉包独立校验器 ==")
    print("   零依赖（纯标准库）/ 零网络 / 只读不写")
    print(f"   校验器自身：{me.name}  sha256[:12] = {self_sha}  ← 记下它，就知道这份结论是哪一版算的")
    print(f"   包目录：{pack.resolve()}")

    checks = verify(pack)
    files = pack_files(pack)
    print(f"   包内文件 {len(files)} 个"
          f"（{sum((pack / r).stat().st_size for r in files):,} B）：{', '.join(files) or '空'}")

    for i, chk in enumerate(checks, 1):
        if args.quiet:
            print(f"[{'OK' if chk.ok else 'FAIL'}] {i}/{len(checks)} {chk.key}")
            for line in chk.problems:
                print(f"  ! {line}")
        else:
            _print_check(chk, i, len(checks))

    bad = [c for c in checks if not c.ok]
    print("\n== 结论 ==")
    for i, chk in enumerate(checks, 1):
        print(f"  [{'OK' if chk.ok else 'FAIL'}] {i}/{len(checks)} {chk.key}")
    if bad:
        print(f"\n[FAIL] 不通过 {len(bad)} 道：{', '.join(c.key for c in bad)}")
        for chk in bad:
            for line in chk.problems:
                print(f"  ✗ [{chk.key}] {line}")
        print("       → 退出码 1：**这份包与它的索引对不上**。在查清之前不要提交它。")
        return 1
    print("\n[OK] 四道全过：哈希与索引逐字节一致、内部引用齐、无路径泄露、校验器自身零网络。")
    print("     注意：本校验器核对的是「这份包是不是它自己声明的那些字节」，"
          "**不判断内容是否真实** —— 系统本身也不做真假终审。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
