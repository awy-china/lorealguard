"""引用校验器：把交付文档里的「文件:行号」逐条回源码核（存在性 + 行范围 + 锚词）。

为什么需要它（2026-09-29 取证）：`loreal-agent-design.md` 自称引用"全部实测通过"，
但 2026-09-28 往 `voiceguard/__main__.py` 插入 11 行 UTF-8 兜底后，那 8 条引用**整体漂移 +11**
（行号仍在文件范围内 ⇒ 只查"越界"的校验器**抓不到**），文档却没人重跑 ——
铁律 9 的「烙死旧口径 = 真漂移」在文档层重演。本工具把「引用可核」变成一条**会红的检查**。

用法：
    python _check_refs.py                     # 默认校验三类面向评委的活文档（同目录）
    python _check_refs.py <md> [<md> ...]
    python _check_refs.py --self-check        # 自证伪：喂假引用，必须被抓到（不会红=无效检查）
      python D:/LorealGuard/tools/check_refs.py  # 仓库正本（**进源码包**）；与交付区 `_check_refs.py` 是
                                          # **同一份字节的两个副本** —— `tools/preflight.py` ⑥ 硬断言两者一致

判据（任一不满足即 exit 1）：
  1. 被引用的**文件必须存在**（依次在 仓库根 / 交付区根 / D: 盘根 下解析）；
  2. `起始行/结束行` 必须在 `1..文件总行数` 内，且 结束行 >= 起始行；
  3. 被引用的行**不能全是空行**；
  4. **锚词断言**：引用反引号之后、到最近的 `）` 或换行之前，若出现 `` `--flag` `` 这类可执行锚词（**新写引用时请把锚词写进同句括号，如 `` （`--text`） ``；不写锚词，本条对它就无能为力**），
     该锚词必须**真出现在被引用的行里** —— 这一条专抓「行号对、内容换人」的漂移；
  5. 只认**反引号内**的引用（`` `voiceguard/__main__.py:42-43` ``），避免把正文时间 `12:30` 当引用；
     反引号里只写 `:44-48` 的**续写形式**，沿用本文件内上一个带路径的引用（找不到前文路径即判失败）；
  6. 扫到的引用条数必须 > 0（防止「扫了个空、却报一切正常」，同 `check_number_consistency.py` 的设计）。
  7. **盲区（如实登记）**：没写锚词的引用（如「图」那一行）本工具只保证「文件在、行号在范围内」，
     **不能**保证语义未漂移 —— 这条防线靠人把锚词写进同句，不靠自觉。
  8. **续写形式的误伤防护**：冒号前紧邻词字符的（`127.0.0.1:8765`、`12:30`）、以及切片写法（`xs[:200]`）**不算**引用；
     代价是「中文裸文件名:行号」也会被跳过 ⇒ 面向评委的文档请写**全路径**（本工具会直接报「文件不存在」，不静默放过）。
     （锚词放宽：文档写 `_run()` 时，源码里的 `def _run(` 也算命中 —— 2026-10-05 起。）
  9. **盲区**：代码围栏（三反引号块）内的引用**不扫** —— 反引号成对规则的副作用；要核的引用请写在正文。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

DELIVER = Path("D:/deliver/compete")


def _repo_root() -> Path:
    """仓库根：本文件在仓库 `tools/` 下时取它；在交付区（`D:/deliver/compete/`）下时退回
    `D:/LorealGuard`。**同一份字节**在两处都要能解析相对引用（源码包解到别处也认得出自己的根）。"""
    here = Path(__file__).resolve()
    cands = ([here.parents[1]] if len(here.parents) > 1 else []) + [Path("D:/LorealGuard")]
    for cand in cands:
        if (cand / "voiceguard").is_dir():
            return cand
    return Path("D:/LorealGuard")


ROOTS = [_repo_root(), DELIVER, Path("D:/")]
DEFAULT_DOCS = [
    DELIVER / "loreal-agent-design.md",
    DELIVER / "RUN-GUIDE.md",
    DELIVER / "SUBMIT.md",
    DELIVER / "loreal-defense-qa.md",        # 现场要背的稿，引用错=当场讲错行号
    DELIVER / "loreal-preflight-checklist.md",  # 上场自检清单
    DELIVER / "README-demo.md",            # 交互面运行指南
    DELIVER / "README.md",                 # 交付面根说明（只读扫描，不改）
]

SPAN = re.compile(r"`([^`\n]+)`")
REF_IN_SPAN = re.compile(
    r"(?P<path>(?:[\w.-]+/)*[\w.-]+\.(?:py|md|ya?ml|html|json|txt|bat|lock))?"
    r"::?(?P<start>\d+)(?:-(?P<end>\d+))?"
)
# 只看「可执行锚词」：命令行开关（--x）或函数/方法名（x() 或 x( —— 后者是代码锚的常见写法，
# 2026-09-29 修正：原先只认空括号 x()，导致 RiskReport( / ValueError( 这类代码锚**静默失效**）。
# 别的词（中文短语、路径、含 = 的赋值）不做断言，避免误伤。
ANCHOR = re.compile(r"^(?:--[\w-]+|[\w_.]+\(\)?)$")
AFTER = 60


def resolve(rel: str) -> Path | None:
    for root in ROOTS:
        cand = root / rel
        if cand.is_file():
            return cand
    return None


def check(text: str, label: str) -> list[tuple[bool, str, str]]:
    rows: list[tuple[bool, str, str]] = []
    last_path: str | None = None
    for span in SPAN.finditer(text):
        for m in REF_IN_SPAN.finditer(span.group(1)):
            path = m.group("path")
            if not path:
                pre = span.group(1)[:m.start()]
                if pre and (pre[-1].isalnum() or pre[-1] in "._["):
                    continue  # 端口号/时间（127.0.0.1:8765、12:30）/ 切片（xs[:200]）不是引用
            start = int(m.group("start"))
            end = int(m.group("end")) if m.group("end") else start
            suc = f":{start}" + (f"-{end}" if m.group("end") else "")
            if path:
                last_path = path
                target = resolve(path)
                desc = f"{path}{suc}"
                if target is None:
                    rows.append((False, desc, "文件不存在（仓库根 / 交付区根 / D: 盘根都没有）"))
                    continue
            else:
                if not last_path:
                    rows.append((False, f"(续写){suc}", "续写形式但本文件内找不到上一个带路径的引用"))
                    continue
                target = resolve(last_path)
                desc = f"{last_path}{suc}"
                if target is None:
                    rows.append((False, desc, f"续写指向的文件不存在：{last_path}"))
                    continue
            lines = target.read_text(encoding="utf-8", errors="replace").split("\n")
            n = len(lines)
            if start < 1 or end < start or end > n:
                rows.append((False, desc, f"行范围越界（文件共 {n} 行）"))
                continue
            body = lines[start - 1:end]
            if all(not b.strip() for b in body):
                rows.append((False, desc, f"引用落在空行（{n} 行文件）"))
                continue
            tail = text[span.end():]
            for stop in ("）", "\n"):
                cut = tail.find(stop)
                if cut != -1:
                    tail = tail[:cut]
            tail = tail[:AFTER]
            toks = [t for t in SPAN.findall(tail) if not REF_IN_SPAN.search(t) and ANCHOR.match(t)]
            blob = "\n".join(body)
            # `x()` 也认源码里的 `x(`：文档惯写成 `_run()`，而源码是 `def _run(...):`（2026-10-05 放宽）
            miss = [t for t in toks
                    if t not in blob and not (t.endswith("()") and t[:-1] in blob)]
            if miss:
                rows.append((False, desc,
                             f"锚词断言失败：{'/'.join(miss)} 不在 {start}-{end} 行内（行号对、内容换人）"))
                continue
            extra = f"，锚词 {'/'.join(toks)} 命中" if toks else ""
            rows.append((True, desc, f"OK（文件 {n} 行{extra}）"))
    return rows


def report(rows: list[tuple[bool, str, str]], label: str) -> int:
    bad = [r for r in rows if not r[0]]
    print("=" * 10, label)
    for ok, desc, detail in rows:
        print(f"  [{'OK ' if ok else 'FAIL'}] {desc:<45} {detail}")
    print(f"  合计：引用 {len(rows)} 条 / 失败 {len(bad)} 条")
    if not rows:
        print("  注：本文件里没有反引号引用（不是失败；整轮合计为 0 才判失败）")
    return 1 if bad else 0


def self_check() -> int:
    """自证伪：喂假引用，必须红。红不了就说明这工具形同虚设。"""
    fake = ("看 `voiceguard/__main__.py:99999`（`--nope`）与 `voiceguard/no_such_file.py:1` 与 `:7-9`，"
            "再看不漂移但内容换人的 `voiceguard/main的假引用:1`。"
            "锚词断言专用：`voiceguard/__main__.py:26`（`--text`）"
            "端口号不算引用：`http://127.0.0.1:8765` 与时间 `12:30`。"
            "切片不算引用：`xs[:200]`。函数名放宽：`tools/make_redteam_delivery.py:90`（`main()`）")
    rows = check(fake, "<self-check 合成文本>")
    caught = [r for r in rows if not r[0]]
    print(f"[自证伪] 合成文本里 {len(rows)} 条引用，抓到 {len(caught)} 条失败")
    for ok, desc, detail in rows:
        print(f"  [{'OK ' if ok else 'FAIL'}] {desc:<45} {detail}")
    ports = [r for r in rows if "8765" in r[1]]
    if ports:
        print("[自证伪] **失败** —— 端口号/时间被误当成引用")
        return 1
    if any("200" in r[1] for r in rows):
        print("[自证伪] **失败** —— 切片 `xs[:200]` 被误当成引用")
        return 1
    relaxed = [r for r in rows if r[1].startswith("tools/make_redteam_delivery.py:90")]
    if not relaxed or not relaxed[0][0]:
        print("[自证伪] **失败** —— `x()` 未被源码里的 `def x(` 满足（放宽失效）")
        return 1
    kinds = " ".join(d for _, _, d in caught)
    need = ["越界", "文件不存在", "锚词断言失败"]
    if all(k in kinds for k in need):
        print("[自证伪] OK —— 越界 / 文件不存在 / 行号对但内容换人 三类假引用全被抓到")
        return 0
    print("[自证伪] **失败** —— 假引用没被抓全，本工具的判据不可信")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="_check_refs", description="交付文档「文件:行号」引用校验器")
    ap.add_argument("docs", nargs="*", help="要校验的 md 文件（默认 loreal-agent-design.md）")
    ap.add_argument("--self-check", action="store_true", help="自证伪：假引用必须被抓到")
    ap.add_argument("--list-docs", action="store_true",
                    help="只打印默认文档清单（每行一个绝对路径）后退出"
                         "（给 tools/preflight.py ⑥ 用：清单只在本文里写一份）")
    args = ap.parse_args(argv)
    if args.self_check:
        return self_check()
    if args.list_docs:
        for d in DEFAULT_DOCS:
            print(d)
        return 0
    docs = [Path(d) for d in args.docs] or DEFAULT_DOCS
    rc = 0
    total = 0
    for doc in docs:
        if not doc.is_file():
            print(f"[FAIL] 文档不存在：{doc}")
            rc = 1
            continue
        rows = check(doc.read_text(encoding="utf-8", errors="replace"), str(doc))
        total += len(rows)
        rc |= report(rows, str(doc))
    if total == 0:
        print("判失败：**整轮一条引用都没扫到**（扫描器空转 = 无效检查）")
        rc = 1
    print("=" * 10, f"结论：{'全部通过' if rc == 0 else '存在失败引用 ⇒ exit 1'}" f"（整轮引用 {total} 条）")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())