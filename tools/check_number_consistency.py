"""口径检查器：交付物里的「回归项数」必须等于当前真实套件数。

为什么需要它（2026-09-20 取证发现）：本轮加了 26 条测试（181 → 207），但 181 这个数字
还留在 **12 处交付物**里（master-plan / 教学卡 09–13 / PROJECT-HANDOFF / build_deck.py）。
这就是「改了代码没改数字」——评委读到两个不同的回归数，会怀疑全部数字。

铁律对应：**口径同步必须带命中次数断言**。所以本脚本不只报差异，还报**它到底扫到了几处
命中**；命中数为 0 会直接判失败（防止"扫描器扫了个空、却报一切正常"）。

用法：
    python tools/check_number_consistency.py            # 扫描并报告（有漂移则 exit 1）
    python tools/check_number_consistency.py --self-check   # 自证伪：故意喂错数必须被抓到
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DELIVER = Path("D:/deliver/compete")

# 要扫的"回归项数"声称形态。只认「数字 + 项回归 / passed」这类明确写法，避免误伤
# 190 个文件、90% 覆盖、sha 片段等别的数字。
CLAIM = re.compile(r"(?<![\d.])(?P<n>\d{2,4})\s*(?:项回归|项\s*回归|passed|项\b)")

# 紧跟数字后面的这些单位说明它是别的量（百分比 / 体积 / 文件数 / 条数），不是"回归项数"。
# 注意：判噪必须**按匹配点判**，不能"整行含 % 就跳过" —— 那样一句"207 项回归 + 90% 覆盖"
# 会被整行丢掉，扫描器就空转了（本工具的自证伪当场抓过这个 bug）。
NOISE_TAIL = re.compile(r"\s*(?:%|‰|MB|KB|GB|个文件|条|张|份|秒|s\b|字\b)")

SCAN_FILES = [
    ROOT / "README.md",
    ROOT / "docs" / "DEBT.md",
]
SCAN_GLOBS = [
    (DELIVER, "*.md"),
    (DELIVER / "loreal-ppt", "*.py"),
]

# 数字附近必须出现这些词之一，才算"在讲回归项数"（收窄命中面，减少误报）
CONTEXT = ("回归", "passed", "pytest", "覆盖率")


def actual_test_count() -> int:
    """当前真实套件数：pytest --collect-only（只收集、不执行，无副作用）。"""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    for pat in (r"(\d+)\s+tests?\s+collected", r"collected\s+(\d+)\s+items?"):
        m = re.search(pat, out)
        if m:
            return int(m.group(1))
    # 本机 pytest 的 -q --collect-only 只输出逐文件计数（tests/x.py: 8）→ 求和
    # 本机 pytest 的 -q --collect-only 只输出逐文件计数（tests/x.py: 8）→ 求和
    # ⚠️ 2026-09-27 修（D-30）：先切掉 warnings summary 段再求和 —— 它里面那行「路径:行号」
    # （实测 tools\make_film_segment.py:775）会被下面的正则当成「该文件有 775 个测试项」，
    # 于是活口径 = 515 + 775 = 1290 ⇒ 口径闸假红。只在源文件刚被写过（.pyc 失效 ⇒ pytest
    # 重新导入并打印 DeprecationWarning）之后的那一次 collect 出现 ⇒ 看运气的假红（见 D-29）。
    out = out.split("warnings summary", 1)[0]
    per_file = re.findall(r"^\S+\.py:\s*(\d+)\s*$", out, flags=re.M)
    if per_file:
        return sum(int(x) for x in per_file)
    raise SystemExit("❌ 数不出当前套件数（pytest --collect-only 输出里既没有总数，也没有逐文件计数）。")


def targets() -> list[Path]:
    files = [p for p in SCAN_FILES if p.exists()]
    for d, pat in SCAN_GLOBS:
        if d.exists():
            files.extend(sorted(d.glob(pat)))
    return files


def scan(files: list[Path], truth: int):
    """→ (漂移, 引用类, 历史类, 命中总数, 漂移明细)。

    明细是 (文件, 行号, 起始列, 结束列, 旧值) —— 供 `--fix` **按列精确**回填，
    避免整行替换误伤同一行里的其它数字。
    """
    drift, quoted, historical, hits, recs = [], [], [], 0, []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if not any(k in line for k in CONTEXT):
                continue
            for m in CLAIM.finditer(line):
                n = int(m.group("n"))
                if n == truth:
                    hits += 1
                    continue
                # 期数/年份/百分比等噪声：只收 100–999 且不是 sha/大小/百分比的
                if not (100 <= n <= 999):
                    continue
                if NOISE_TAIL.match(line[m.end():m.end() + 6]):
                    continue  # 紧跟的是 %/MB/条 等 → 这是别的量，不是回归项数
                hits += 1
                # 引用类：这一行同时给出了**证据文件名**（如 output/f4/_verify.txt（105 passed）），
                # 那这个数字属于**那份文件的内容**，不是当前态 —— 改它就是伪造证据。
                # 单独列出来供人核对"那份文件里到底写着几"，不算漂移。
                if any(c in line for c in ("当轮", "当时", "该轮", "本轮")):
                    historical.append(f"{f} :{i}: {n}（已标为当轮/历史，属当时的真实读数）→ {line.strip()[:78]}")
                    continue
                if re.search(r"output/[^\s）)]*\.(?:txt|md|json)|_[a-z0-9_]*\.txt", line):
                    quoted.append(f"{f} :{i}: {n}（引用证据文件，请核对指向的产物）→ {line.strip()[:80]}")
                    continue
                drift.append(f"{f} :{i}: 写着 {n}，真实 {truth} → {line.strip()[:88]}")
                recs.append((f, i, m.start("n"), m.end("n"), n))
    return drift, quoted, historical, hits, recs


def apply_fix(recs: list, truth: int) -> int:
    """按列精确回填漂移数字（同一文件倒序改，避免行内列号漂移）。**只**动漂移明细里的位置。

    ⚠️ **行尾必须原样保留**（2026-09-26 实测的真伤）：这里原来是 `read_text()` + `write_text()`，
    Windows 的文本模式写会把 `\n` 变成 `\r\n` ⇒ 一份**纯 LF** 的交付区稿子被回填 3 位数字的同时
    被整体换了行尾（实测 `loreal-defense-qa.md`：8,304 B / CR=0 → 8,428 B / CR=124，多出来的
    字节数**正好等于行数**）。交付区在仓库外、没有 git 兜底，而 `--fix` 的打印里一个字都不会提
    行尾 —— 谁也看不出来改过。
    ⇒ 一律走 bytes：`read_bytes()` → 解码 → 改 → 编码 → `write_bytes()`（行尾字节原样带过）。
    守卫：`tests/test_number_consistency.py` 的 ⑦（+ ⑧ 自证伪）。
    """
    by_file: dict = {}
    for f, ln, a, b, _old in recs:
        by_file.setdefault(f, []).append((ln, a, b))
    n = 0
    for f, items in by_file.items():
        raw = f.read_bytes()
        lines = raw.decode("utf-8", errors="replace").splitlines(keepends=True)
        for ln, a, b in sorted(items, reverse=True):
            s = lines[ln - 1]
            lines[ln - 1] = s[:a] + str(truth) + s[b:]
            n += 1
        f.write_bytes("".join(lines).encode("utf-8"))
    return n


def _utf8_stdout() -> None:
    """把 stdout 钉成 UTF-8 —— 否则**重定向到文件**时本工具会自己崩掉，报出一个假的 exit 1。

    为什么必须有（2026-09-22 实测）：本工具的输出带 `✅`/`❌`/`🔧`。Windows 上 stdout 一旦不是
    终端（`> output/x.txt`），Python 就按 locale（本机 GBK）编码 → 打印 `✅` 抛
    `UnicodeEncodeError: 'gbk' codec can't encode character '\\u2705'` → **exit 1**。
    而 CLAUDE.md §5 明令别用 `| tail`（会吃掉上游退出码）、推荐的就是重定向到文件 ——
    于是"唯一推荐的跑法"恰恰是这条崩溃路径，看的人会以为**数字漂移了**。
    一条永远会红的检查与一条永远不红的检查同样坏：假的红会让人去改本来没错的数字。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass          # 已被包成非 TextIOWrapper：不拦，继续跑


# ---------------------------------------------------------------------------
# 通道 B：对外稿里的「误报低」必须**条件化**（HANDOFF-to-cc.md §2 的 ④，2026-09-24）
#
# 为什么需要它：对外稿里单讲「误报 0 / 上界 7.22%」，是**开卷考试**的分数。同一套阈值下把
# 规则表全文交给对手，陷害侧误报 **69.7%**、规避侧绕过 **100%**、红线被击穿 **3 条** ——
# 这三笔 `README.md:192-194` 已如实登记。宣称与反例必须**同段**出现，否则读者只拿到
# 对自己有利的那一半。在此之前这条纪律**只写在散文里**（CLAUDE.md 的口径纪律），谁来检查
# 都靠人眼 —— 而人眼在这一轮之前，连续三轮都没看出来。
#
# 为什么按「段」判、不按「行距 ±N」判：PPT 源里同一页的卡片数字在 `build_deck.py:306`、
# 页脚在 `:322` —— 差 16 行，但它们**同属第 6 页**，人的读法就是"同一页"。按行距判会把
# 这一页判成无条件（假红），放宽到 ±20 行又会把**别的页**的对抗数字借过来（假绿）。
# 「备注」（NOTES）单独成段：它是**讲稿**，现场/录屏都会念出来，不许蹭正文的对抗数字。
# ---------------------------------------------------------------------------
FPR_TARGETS = [
    DELIVER / "loreal-ppt" / "build_deck.py",
    DELIVER / "loreal-ppt" / "figs.py",
    DELIVER / "loreal-video-script.md",
    ROOT / "demo" / "index.html",
    ROOT / "demo" / "serve.py",
]
#: 「误报低」的声称形态。**同一件事在这几个文件里有四种写法**，只认其中一种，这条检查对
#: 其余写法就永远绿（本轮实测：口播稿写 `7.2%/8.7%`，PPT 正文写 `7.22%/8.68%`，
#: 而第 7 页的关键行 `("0 / 40", …)` `("≤ 7.22%", …)` 里**「误报」两个字根本不在这行**）：
#:   ① 带「误报」二字的——`误报 0/40`、`误报率 0.0%`、`零误报`；
#:   ② 只写 `0 / 40` 的（数字与「误报」隔着一列）；
#:   ③ 只写 `≤ 7.22%` 的（上界形态）；
#:   ④ 不带任何数字的口号式——`谁不冤枉人`（把"不冤枉人"当可测指标来讲，同样是宣称）。
FPR_CLAIM = re.compile(
    r"零误报"
    r"|误报\s*0(?!\d)"
    r"|误报率[^\n]{0,14}?0\.0\s*%"
    r"|谁不冤枉"
    r"|(?<!\d)0\s*/\s*(?:33|40)(?!\d)"
    r"|≤\s*7\.22\s*%|≤\s*8\.68\s*%"
)
#: 对抗条件下的三个反例数字（任一出现即算"同一段里讲清楚了"）。
ADVERSARIAL = re.compile(r"69\.7|绕过\s*100|击穿\s*3\s*条")


def _blocks(path: Path, text: str):
    """把一份对外稿切成"段"：PPT 源 = 每页函数 + 每则备注；口播稿 = 每个 `#` 标题块；页面 = 整份文件。

    返回 [(段名, [(行号, 行内容), …]), …]。**解析不出来就抛 ValueError** ——
    宁可让守卫红，也不许它因为"没解析到"而静默变绿。
    """
    lines = text.splitlines()
    tagged = [(i, ln) for i, ln in enumerate(lines, 1)]
    blocks: list[tuple[str, list[tuple[int, str]]]] = []

    if path.name == "build_deck.py":
        pages = [(i, m.group(1)) for i, ln in tagged
                 if (m := re.match(r"def (p\d+)\(prs\):", ln))]
        if not pages:
            raise ValueError("一页都没解析到（`def pNN(prs):` 的写法变了？）")
        for k, (ln0, pname) in enumerate(pages):
            end = pages[k + 1][0] - 1 if k + 1 < len(pages) else len(lines)
            blocks.append((pname, [(i, l) for i, l in tagged if ln0 <= i <= end]))
        start = next((i for i, ln in tagged if ln.startswith("NOTES = [")), None)
        if start is None:
            raise ValueError("找不到 `NOTES = [` —— 备注段没了，映射失效")
        elem: list[tuple[int, str]] = []
        idx = 0
        for i, ln in tagged:
            if i <= start:                 # start 那一行是 `NOTES = [` 本身，不能当备注内容
                continue
            if not ln.startswith("    "):
                break                      # 缩进回到 0 → NOTES 列表结束
            elem.append((i, ln))
            if ln.rstrip().endswith('",'):  # 每个备注元素以 `",` 收尾
                blocks.append((f"备注[{idx}]（= 第 {idx + 1} 页的口播）", elem))
                elem, idx = [], idx + 1
        if elem:
            raise ValueError("最后一个备注元素没有收尾的 `\",`")
        if idx != len(pages):
            raise ValueError(f"备注段数 {idx} ≠ 页数 {len(pages)} —— 备注↔页映射已失效")
        return blocks

    if path.suffix == ".md":
        marks = [i for i, ln in tagged if ln.startswith("#")]
        if not marks:
            return [(path.name, tagged)]
        for k, ln0 in enumerate(marks):
            end = marks[k + 1] - 1 if k + 1 < len(marks) else len(lines)
            blocks.append((lines[ln0 - 1].strip()[:44],
                           [(i, l) for i, l in tagged if ln0 <= i <= end]))
        return blocks

    return [(path.name, tagged)]


def scan_fpr(paths=None):
    """→ (未条件化的声称, 「误报低」命中总数, 结构告警)。

    命中总数是**断言用**的：一处都没扫到 = 等于没检查（与通道 A 同一条纪律）。
    """
    problems: list[str] = []
    warns: list[str] = []
    hits = 0
    for path in (FPR_TARGETS if paths is None else paths):
        if not path.exists():
            warns.append(f"目标文件不存在，**没扫**：{path}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            blocks = _blocks(path, text)
        except ValueError as e:
            problems.append(f"{path}：（结构解析失败：{e}）→ 守卫本身已失效，必须修")
            continue
        for name, body in blocks:
            claimed = [(i, l) for i, l in body if FPR_CLAIM.search(l)]
            if not claimed:
                continue
            hits += len(claimed)
            if any(ADVERSARIAL.search(l) for _i, l in body):
                continue                   # 这一段里已经给了反例 → 合规
            for i, l in claimed:
                problems.append(f"{path} :{i}【{name}】无条件宣称误报低 → {l.strip()[:86]}")
    return problems, hits, warns


def fpr_self_check() -> bool:
    """通道 B 的自证伪。四段假稿，必须**只抓前两段的第 1、3 段**：

    ① 无条件 → 必须抓；② 同段有对抗数字 → 不许抓（否则守卫是"一律红"，没信息量）；
    ③ 对抗数字在**别的段** → 必须抓（这一条才证明"段"边界是真的，不是整文件一锅粥）。
    """
    tmp = ROOT / "output" / "_selfcheck_fpr_conditional.md"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(
        "# 假稿（自证伪）\n\n"
        "### 镜 A｜无条件（必须抓）\n"
        "误报 0/40（上界 7.22%）。\n\n"
        "### 镜 B｜同段已给反例（不许抓）\n"
        "误报 0/40（上界 7.22%）；把规则表全文交给对手，陷害侧误报 69.7%。\n\n"
        "### 镜 C｜反例在别的段（必须抓）\n"
        "误报率 0.0%（上界 7.2%）。\n\n"
        "### 镜 D｜这段只有对抗数字，没有误报声称（不参与计数）\n"
        "红线被击穿 3 条。\n",
        encoding="utf-8")
    problems, hits, _w = scan_fpr([tmp])
    tmp.unlink()
    # 段名带 `###` / `def pNN` 这类前缀（`_blocks` 就是这么命名的），比对前统一剥掉 ——
    # 不剥的话期望集合永远对不上，这条自证伪会**恒红**（等于没检查，2026-09-24 实测抓到）。
    flagged = {p.split("【")[1].split("】")[0].lstrip("# ").strip() for p in problems}
    print(f"自证伪（通道 B）：命中 {hits} 处，判红 {len(problems)} 处，红的段={sorted(flagged)}")
    ok = hits == 3 and flagged == {"镜 A｜无条件（必须抓）", "镜 C｜反例在别的段（必须抓）"}
    print("自证伪结论（通道 B）：" + ("无条件宣称真的会被抓、条件化的不会被冤 ✅" if ok
                               else "❌ 抓错了（多抓=假红，少抓=装饰品）"))
    return ok


def _run_numbers(args) -> int:
    """通道 A：回归项数漂移。"""
    truth = actual_test_count()
    print(f"当前真实套件数（pytest --collect-only）：{truth} 项")

    if args.self_check:
        tmp = ROOT / "output" / "_selfcheck_number.md"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(f"全量 {truth + 7} passed，覆盖率 90%。\n", encoding="utf-8")
        drift, _q, _h, hits, recs = scan([tmp], truth)
        fixed = apply_fix(recs, truth)
        after = tmp.read_text(encoding="utf-8")
        tmp.unlink()
        print(f"自证伪：注入错误数字（{truth + 7}）→ 检出 {len(drift)} 处，命中 {hits} 处")
        print(f"自证伪：--fix 回填 {fixed} 处，回填后文件内容={after.strip()!r}")
        ok = len(drift) == 1 and hits == 1 and fixed == 1 and str(truth) in after
        print("自证伪结论：" + ("扫描器真的会红、且能精确回填 ✅" if ok
                           else "❌ 扫描器抓不到错数或回填不准（等于没有检查）"))
        return 0 if ok else 1

    files = targets()
    drift, quoted, historical, hits, recs = scan(files, truth)
    if args.fix and recs:
        n_fixed = apply_fix(recs, truth)
        print(f"🔧 --fix：按列精确回填 {n_fixed} 处（引用证据文件/历史读数一律不碰）")
        drift, quoted, historical, hits, recs = scan(files, truth)
    print(f"扫描 {len(files)} 个文件，命中「回归数」声称 {hits} 处")
    if hits == 0:
        print("❌ 一处都没扫到 —— 等于没检查（文件路径变了？）。判失败。")
        return 1
    if quoted:
        print(f"\n引用证据文件的数字 {len(quoted)} 处（不是漂移，但要核对指向的产物里确实这么写）：")
        for q in quoted:
            print("  " + q)
    if historical:
        print(f"\n历史/当轮读数 {len(historical)} 处（保留原数字是对的，已标明是当轮）：")
        for h in historical:
            print("  " + h)
    if not drift:
        print("✅ 当前态数字全部一致，无漂移。")
        return 0
    print(f"\n漂移 {len(drift)} 处：")
    for d in drift:
        print("  " + d)
    print(f"\n修法：把上面每处的数字改成 {truth}（并核对同句里的派生数字，如「原 164 → +17」）。")
    return 1


def _run_fpr(args) -> int:
    """通道 B：对外稿的误报宣称是否条件化。"""
    paths = None
    if args.fpr_targets:
        paths = []
        for raw in args.fpr_targets:
            p = Path(raw)
            if p.is_dir():                 # 目录 → 按同名文件找（供测试注入变异体）
                paths.extend(p / t.name for t in FPR_TARGETS)
            else:
                paths.append(p)
    print("通道 B：对外稿（PPT 源 / 口播稿 / 页面文案）里的「误报低」是否与对抗数字同段")
    problems, hits, warns = scan_fpr(paths)
    for w in warns:
        print("  ⚠️ " + w)
    print(f"命中「误报低」声称 {hits} 处")
    if args.self_check:
        return 0 if fpr_self_check() else 1
    if hits == 0:
        print("❌ 一处「误报低」都没扫到 —— 等于没检查（路径变了？写法变了？）。判失败。")
        return 1
    if not problems:
        print("✅ 每一处「误报低」都在同一段里给了对抗数字（陷害侧 69.7% / 绕过 100% / 红线击穿 3 条），已条件化。")
        return 0
    print(f"\n无条件宣称误报低 {len(problems)} 处（宣称与反例必须同段）：")
    for p in problems:
        print("  " + p)
    print("\n修法：在该段内补上对抗条件下的数字并指明出处（照 `README.md:192-194` 的三笔）。"
          "**不许靠改检测器阈值把反例抹平**（铁律 1/2）。")
    return 1


def main() -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-check", action="store_true", help="自证伪：两个通道各喂一份假文件")
    ap.add_argument("--fix", action="store_true",
                    help="幂等回填：只改\"当前态漂移\"那几处（按列精确），引用/历史行一律不碰")
    ap.add_argument("--fpr-conditional", action="store_true",
                    help="只跑通道 B（对外稿的误报宣称是否条件化，= HANDOFF 的 ④）")
    ap.add_argument("--numbers-only", action="store_true",
                    help="只跑通道 A（回归项数漂移）—— 两个通道的红不该互相污染，别让看的人查错地方")
    ap.add_argument("--fpr-targets", action="append", default=None, metavar="PATH",
                    help="通道 B 的替代扫描目标（文件或目录，可重复）—— 供测试注入变异体，别对人用")
    args = ap.parse_args()

    rc = 0
    if not args.fpr_conditional:
        rc |= _run_numbers(args)
    if not args.numbers_only:
        rc |= _run_fpr(args)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())