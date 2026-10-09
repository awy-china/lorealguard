# -*- coding: utf-8 -*-
"""把 P2 系列样本导出成**官方要求的提交格式**测试数据包。

官方原文（赛题2）：测试数据包 zip，**每样本一文件夹**：
    文案 `.txt`  +  图 `.jpg`  +  `README.txt`（注明 来源 / 类型 / 是否伪造 / 伪造方式）

本脚本产出的目录结构：
    dist/submission_pack/lorealguard_testset_<ver>/
        samples/<id>/
            image.jpg        ← 图像（合成图或篡改图）
            text.txt         ← 该样本配的文案（无文案时写明"本样本仅图像通路"）
            README.txt       ← 来源 / 类型 / 是否伪造 / 伪造方式 / 期望等级 / 期望规则 / sha256
        MANIFEST.json        ← 机器可读全量清单（含逐条 sha256、gt 框、expect）
        SHA256SUMS.txt       ← 逐文件哈希（校验完整性）
        PRIVACY.txt          ← 隐私与合规声明
        README.md            ← 人读说明 + 官方格式对照 + 单行复现命令
        generators/          ← 生成器脚本（可复现性：别人能重造出一模一样的样本）
        EVIDENCE/            ← 证据附件：红队报告 + 协议原文 + 冻结语料 + 红线登记表 + 跑分器
                               （包内 README 承诺"第三方可用跑分器复算我们的数字"，
                                 仓库未发布时这句是空头承诺 —— 附件让它可以兑现）
    dist/submission_pack/lorealguard_testset_<ver>.zip   ← 提交用压缩包
    dist/submission_pack/lorealguard_testset_<ver>.zip.sha256

用法（单行）：
    env -u PYTHONPATH .venv/Scripts/python.exe tools/make_submission_pack.py
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE_DIR = ROOT / "output" / "p2" / "suite"
MAIN_MANIFEST = ROOT / "output" / "p2" / "manifest.json"
TEXT_MANIFEST = ROOT / "output" / "p2" / "text_pairs.json"
P5_MANIFEST = ROOT / "output" / "p5" / "comment_pairs.json"   # 场景②：评论区配对集
EDGE_DIR = ROOT / "output" / "edge" / "suite"                 # 边缘场景（误报护栏的证据）
EDGE_MANIFEST = ROOT / "output" / "edge" / "manifest.json"
BENCH_REPORT = ROOT / "output" / "p2" / "bench_report.md"     # P2 跑分报告（附录摘录的唯一出处）
OUT_DIR = ROOT / "dist" / "submission_pack"

#: 包版本。⚠️ 由本文件**显式**持有，不再从 `main_m` 的 version 推 —— `output/p2/` 那 60 条
#: 逐字节未变（它的 manifest 仍是 v1，那是实话），变的是**这个包**：
#:   v1 → v2：多了 9 条边缘场景（原先 10 条，其中 `edge_no_exif` 与主集同 sha 已删，理由见
#:            `samples/make_edge_suite.py` docstring）；
#:   v2 → v3：**修文本、不动测量** —— ① README 内部数字自相矛盾改正（"共 60 个" ⇒ 主集 + 边缘
#:            分列 69 个；"这 10 条" ⇒ 9 条）；② 补红队物证的白名单口径说明（研究档生成、不进
#:            交付运行时）；③ 补生成器 `samples/make_red_team.py`（此前生成器与冻结物证对不上）；
#:            ④ 新增 `EVIDENCE/bench_excerpt.md`（跑分报告摘录，带出处行号）。
#:            **样本一个字节都没变**（p2 60 条 / 边缘 9 条逐字节同 v2）—— 但仍按
#:            `tests/test_rebuild_from_scratch.py:11-17` 的规矩「改包内容 = 换版本号 = 重新取证」
#:            换名。**不许静默漂移**：换它是为了让下游（GOLDEN / pptx / 交付区引用）被迫显式跟改。
#:   v3 → v4：**新增 `EVIDENCE/generation_params.md`**（实话版生成参数：逐条算子 + 固定种子，
#:            由打包器从源码**实时抽出**、抽不全就打包失败；并主动说明包内 `sdxl_base_1.0`
#:            字样是伪造 AI 签名用例、不是 SDXL 生成证据）。**样本仍一个字节没变**
#:            （p2 60 / 边缘 9 逐字节同 v3）—— 照旧「改包内容 = 换版本号 = 重新取证」。
PACK_VERSION = "v4"

GENERATORS = [
    "samples/make_neutral_base.py",
    "samples/make_p2_suite.py",
    "samples/make_p2_text_pairs.py",
    "samples/make_p5_comment_pairs.py",
    "samples/make_edge_suite.py",
]

#: 随包分发的**来路脚本**：不参与打包、包内永不重跑，只让第三方看得见冻结物证是怎么来的。
#: ⚠️ v2 及以前**整条漏了**：包内有冻结物证 `EVIDENCE/red_team.json`，生成它的脚本却不在包内
#: ⇒ 第三方无法知道那份 JSON 怎么来的（生成器与物证对不上，是一处真实的来源断链）。
#:
#: 与上面 `GENERATORS` **必须分开**，理由不是分类洁癖：
#:   ① 它要网络 + API key，而本包承诺「离线可复算」；
#:   ② 它的产物写 `generated_at`（挂钟时间）—— LLM 输出本就不逐字节可复现，故语料是**冻结**的；
#:   ③ 若混进 `GENERATORS`，`tests/test_submission_pack.py::test_包内生成器不得含挂钟时间`
#:      会立刻红。届时只剩两条路：阉掉那行时间戳（= 改物证来源）、或放宽确定性守卫（= 拆护栏）。
#:      **两条都是坏选择** —— 所以显式分组，让两个事实各自留着、各自被守卫。
PROVENANCE_GENERATORS = [
    "samples/make_red_team.py",
]

# 固定时间戳 → zip 逐字节可复现（同一份输入永远同一 sha256）
FIXED_DATE = (2026, 9, 19, 0, 0, 0)


#: 证据附件（逐字节拷贝自仓库，保证与仓库同源）。包是数据包，不含源码；
#: 跑分器需要仓库内的规则表，故附件只在"能独立做到什么"范围内做承诺。
EVIDENCE_FILES = [
    ("docs/REDTEAM-PROTOCOL.md", "REDTEAM-PROTOCOL.md", "红队协议原文（口径写于测量之前，测完判据零修订）"),
    ("samples/red_team/red_team.json", "red_team.json", "冻结语料：99 条线程（规避 33 / 陷害 33 / 黑盒对照 33）"),
    ("samples/red_team/known_breaches.json", "known_breaches.json", "红线突破登记表（多一条红、少一条也红）"),
    ("experiments/red_team_bench.py", "red_team_bench.py", "跑分器（离线、不调 API，约 2 秒）"),
]

EVIDENCE_README = """LorealGuard 提交包 —— 证据附件（EVIDENCE/）
==========================================

这一层只回答一个问题：**报告里的数字，凭什么信？**

附件清单
--------
  redteam-report.md     红队实测报告（交付版 = 抬头 + 正文；正文由跑分器的纯函数 render() 生成，
                        因此报告可从下面这份冻结语料**逐字节重放**）
  REDTEAM-PROTOCOL.md   协议原文 —— 写在测量之前，测完判据零修订（"改口径补漏"在本项目是禁止动作）
  red_team.json         冻结语料：99 条线程（规避 33 / 陷害 33 / 黑盒对照 33），一次性生成后封存；
                        跑分器只读它、不重跑大模型（LLM 输出不可复现，故不重生成）
  known_breaches.json   红线突破登记表：实测被击穿 3 条，逐条写明机制与"为何暂不修"
  red_team_bench.py     跑分器：读上面那份语料 → 重算报告全文
  bench_excerpt.md      P2 跑分报告摘录（六项指标 / 承诺兑现 / 弃权分解），
                        带 `文件:行号` 出处；只搬运不改写，完整报告随源码包交付
  generation_params.md  生成参数（实话版）：逐条列程序合成算子与固定种子，并主动说明包内
                        `sdxl_base_1.0` 字样是**伪造 AI 签名用例**、不是 SDXL 生成证据

第三方**不需要我们的仓库**就能核的三件事
----------------------------------------
  1. 语料自洽：数 red_team.json 的 items —— 应为 99 条、分臂 33/33/33；
  2. 靶子一致：known_breaches.json 的 target_fingerprint 必须等于 red_team.json 的
     rules_yaml_sha256_16（不等 ⇒ 这份报告测的不是当前规则表，整份作废）；
  3. 报告同源：按报告里的口径表把语料逐条归类，核对三个关键数
     （陷害侧条件误报 23/33、黑盒对照 0/33、规避侧绕过 33/33）与逐条归因码是否对得上。

关于 red_team.json 的模型口径（如实说明，别误读）
--------------------------------------------------
  `red_team.json` 里记着 `"model": "deepseek-v4.1-flash"` —— 这是**当时实测用的模型**，
  是**物证内容**，不修饰、不改写。它属于**研究档**（`voiceguard/config.py:44-48` 的
  `RESEARCH_ONLY_MODELS`），只用于**离线研究 / 内部质检**。

  交付运行时**唯一允许的档**是 `qwen3.8-27b`（Apache-2.0 开源权重，白名单见
  `voiceguard/config.py:37-38`）；非白名单档**一调用即拒、且不发起任何请求**
  （`voiceguard/semantic/explain.py:86-90`）。因此这份红队物证
  **不参与交付运行时的任何调用**：它是"我们内部先拿研究档把自家规则表打了一顿"的
  质检记录，不是产品运行时依赖。交付运行时里模型只出现在**判定完成之后**的解释环节。

我们**不**声称的
----------------
  不声称"包内可原地复算全部数字"：重跑 red_team_bench.py 需要仓库内的规则表，
  而本包是**数据包**（不含源码）。一行复现命令写在报告抬头里。
  这条边界写在这里，是为了让"可复算"这个词只覆盖它真正覆盖的范围。
"""


#: 生成参数说明的**单一来源**：用例表逐条从 `samples/make_p2_suite.py` 实时抽出，
#: 不手抄 ⇒ 生成器改了参数，这份文件跟着变，不会出现"文档与源码漂移"（铁律 9）。
_CASE_RE = re.compile(
    r'dict\(id="(?P<id>[^"]+)", label="(?P<label>[^"]+)", '
    r'cat="(?P<cat>[^"]+)", desc="(?P<desc>[^"]+)"')
_SEED_RE = re.compile(r"(?:^SEED\s*=\s*|default_rng\()(\d+)", re.M)


def _seed_of(rel: str, note: str = "") -> str:
    p = ROOT / rel
    if not p.exists():
        return "（生成器不在仓库内）"
    m = _SEED_RE.search(p.read_text(encoding="utf-8"))
    return m.group(1) if m else note


def generation_params_md() -> str:
    """包内样本的"生成参数" —— 老实回答"你的 AI 样本怎么造的"。

    本包**没有用过任何扩散模型**（0 处 SD / SDXL / Flux 调用）⇒ 不存在"SDXL 生成参数"。
    真正的生成参数 = 固定种子 + 程序化编辑算子，原件是随包的 `generators/`。
    """
    src = (ROOT / "samples" / "make_p2_suite.py").read_text(encoding="utf-8")
    cases = _CASE_RE.findall(src)
    manifest = json.loads(MAIN_MANIFEST.read_text(encoding="utf-8"))
    n_b = manifest["counts"]["benign"]
    n_m = manifest["counts"]["malicious"]
    # 自证伪：抽不全就不出这份文件（宁可打包失败，也不出一个"看着齐全其实缺行"的参数表）
    if len(cases) != n_b + n_m:
        raise RuntimeError(
            f"生成参数表抽取不全：抽到 {len(cases)} 条，manifest 记 {n_b + n_m} 条 "
            f"⇒ 用例表格式变了，先修 _CASE_RE")
    rows = "\n".join(f"| `{cid}` | {lab} | {cat} | {desc} |"
                     for cid, lab, cat, desc in cases)
    return f"""# 生成参数（实话版）

> 要回答的是："**AI 伪造样本是怎么造出来的？生成参数是什么？**"
> 直答：**本包没有用过任何扩散模型** ⇒ **不存在 Stable Diffusion / SDXL 生成参数**。
> 真正的"生成参数"＝下面这些 **固定种子 + 程序化编辑算子**，原件是随包的 `generators/`
> 六个脚本 —— 图像与文案这一半，第三方拿生成器能重造出与本包**逐字节相同**的样本。

## 0. 一句实话

| 问 | 答 |
|---|---|
| 用了哪个生成模型？ | **0 处**。全包无 SD / SDXL / Flux / Midjourney 调用，无 API 生图 |
| 那"AI 伪造"样本从哪来？ | **程序合成 + 合成编辑算子**：磨皮 / 调色 / 重压缩 / 跨源拼接 / 抹元数据 / 贴标识 / 伪造 AI 签名 |
| 参数写在哪？ | 写死在 `generators/*.py` 的固定种子里；下表**由打包器从源码实时抽出**，不是手抄 |
| 第三方能重造吗？ | 图像与文案**能**（离线、逐字节、`sha256sum -c SHA256SUMS.txt` 可验）；红队语料**不能**（LLM 一次性产物，故冻结交付） |

## 1. 固定种子（从各生成器源码抽，出处是各自的 `SEED = …` / `default_rng(…)` 一行）

| 生成器 | 造什么 | 固定种子 |
|---|---|---|
| `generators/make_neutral_base.py` | 合成底图（所有样本的公共前提） | {_seed_of("samples/make_neutral_base.py")} |
| `generators/make_p2_suite.py` | 主集 60 条（场景①图像通路） | {_seed_of("samples/make_p2_suite.py")} |
| `generators/make_p2_text_pairs.py` | 文案配对集（同图两套文案） | {_seed_of("samples/make_p2_text_pairs.py", "（源码未声明固定种子：确定性模板路径）")} |
| `generators/make_p5_comment_pairs.py` | 评论配对集 69 条（场景②评论区） | {_seed_of("samples/make_p5_comment_pairs.py", "（源码未声明固定种子：模板/批量两条确定性路径）")} |
| `generators/make_edge_suite.py` | 边缘场景 9 条（误报护栏的证据） | {_seed_of("samples/make_edge_suite.py")} |
| `generators/make_splice_sample.py` | 跨源拼接算子（被主集调用） | {_seed_of("samples/make_splice_sample.py")} |
| `generators/make_red_team.py` | 红队语料 99 条线程（**要网络 + key ⇒ 冻结不重跑**） | {_seed_of("samples/make_red_team.py", "（无固定种子：LLM 输出不可复现）")} |

"未声明固定种子"的两集，其逐字节可复现性由**从零重造链**守卫
（`tests/test_rebuild_from_scratch.py`：删光生成物 → 重跑全部生成器 → 重打包必须回到同一 sha256）。

## 2. 主集：{n_b} 条良性 + {n_m} 条恶意 = {n_b + n_m} 条，逐条算子

下表由打包器从 `samples/make_p2_suite.py` 的用例表现抽，`desc` 一列就是该条用的算子参数（照抄、未改写）：

| 编号 | 真值 | 族 | 算子 / 参数 |
|---|---|---|---|
{rows}

写出质量档（照抄源码）：JPEG 主路径与"抹元数据"样本 `quality=93`、WebP 伪装 `quality=80`
（`generators/make_p2_suite.py:326 / :593 / :603`）；平台二次压缩族另用 q75 / q60（见上表 `desc`）。

## 3. 其余两族

- **文案配对集**（`make_p2_text_pairs.py`）：同一张图配两套文案（含 / 不含性能宣称）—— 造"同图两种说法"的对照。
- **评论配对集 69 条**（`make_p5_comment_pairs.py`）：评论区三类线程（良性 / 误伤探针 / 注水），逐条期望与理由在 `comments/README.txt`。
- **边缘场景 9 条**（`make_edge_suite.py`，种子 {_seed_of("samples/make_edge_suite.py")}）：**全部良性**，是误报护栏的证据；
  每条"应该判到哪一级"写在生成器的 `expect` 里（**挂牌于测量之前**，铁律 1）。

## 4. 红队语料 99 条：为什么不在"可复现"之列

`samples/red_team/red_team.json` 是一次性生成的**冻结语料**（99 条线程＝规避 33 / 陷害 33 / 黑盒对照 33）。
生成器 `generators/make_red_team.py` **要网络 + API key**，且 LLM 输出本就不逐字节可复现
⇒ 本包**不重跑它**，把当时那份语料连同生成器一起交出（作用是"让第三方看得见这份物证怎么来的"）。
其中的模型口径如实写在 `EVIDENCE/README.txt`：语料内记的 `"model"` 属**研究档**，不参与交付运行时任何调用。

## 5. 必须主动说清的一件事：包内**有**"SDXL"字样，但它是**伪造的测试用例**

包内能搜到 `sdxl_base_1.0` —— 出处是一处**故意注入的伪造 AI 签名**（"已声明 AI"良性族）：

    generators/make_p2_suite.py:202-206  case_declared_ai_png  → PNG 文本块写
        "Steps: 30, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 20260920, Size: 1600x1200,
         Model: sdxl_base_1.0, Version: v1.9.4"
    generators/make_p2_suite.py:209-211  case_declared_ai_exif → EXIF 写 "Midjourney v6"

它验证的是**另一条护栏**：作品已声明 AI 时，判定链要把它当**良性**对待（不得因为"带 AI 签名"就判违规）。
**它不是本包使用 SDXL 的证据** —— 那两串文字是手写字符串，与任何生成模型无关。
一行自查：`grep -rn "sdxl_base" generators/` 只命中这一处，且落在 `case_declared_ai_png` 里。

## 6. 复算（在**源码仓库**里跑，不是在数据包内）

    py.bat samples/make_p2_suite.py      # 主集 → output/p2/suite/
    py.bat samples/make_edge_suite.py    # 边缘 9 条 → output/edge/
    sha256sum -c SHA256SUMS.txt          # 与本包逐文件比对

⚠️ 边界（与 `generators/README.txt` 一致）：**本包是数据包、不含源码**，上面这些生成器要依赖仓库
`voiceguard/` 才能跑判定 ⇒ "重造样本并复核判定"这一步在包内做不到；完整可跑形态见决赛源码包。
"""


GENERATORS_README = """LorealGuard 测试数据包 —— 生成器（generators/）
================================================

这里放的是**造出本包样本的那几个脚本**，逐字节拷贝自仓库 `samples/`，内容一字未改。
装好依赖（`requirements.txt`）后按顺序跑，就能重造出与本包逐字节相同的样本：

  1. make_neutral_base.py      底图（所有样本的公共前提）
  2. make_p2_suite.py          场景①图像通路：60 条（良性 40 + 恶意 20）
  3. make_p2_text_pairs.py     场景①文案通路：同图配两套文案的配对集
  4. make_p5_comment_pairs.py  场景②评论区通路：良性 / 误伤探针 / 注水三类线程
  5. make_edge_suite.py        边缘场景 9 条（误报护栏的证据，全部良性）
  6. make_red_team.py          红队语料 99 条线程（冻结物证 `EVIDENCE/red_team.json` 的生成器）

⚠️ 两条边界，说清楚免得第三方白跑
----------------------------------
  1. **本包是数据包，不含源码** —— 上面第 2~5 个脚本依赖仓库 `voiceguard/` 才能跑判定，
     本包不带它。所以「重造样本」这一步在本包内做不到；包内 `README.md` 的复现命令写的是
     **在源码仓库里**怎么跑。**完整可跑形态见决赛源码包（含 `voiceguard/` 全量源码）。**
  2. `make_red_team.py` **要网络 + API key**，且只允许白名单档模型（默认 `qwen3.8-27b`）。
     它的产物 `EVIDENCE/red_team.json` **是一次性生成的冻结语料**：LLM 输出不可复现，
     所以本包**不重跑它**，只把当时那份语料连同生成器一起交出 —— 生成器在这里的作用是
     「让第三方看得见这份物证是怎么来的」，不是「请他重跑一遍」。
     该脚本直连的是 `output/p2/` 之外的 `samples/red_team/`，与其它 5 个生成器**互不依赖**。
"""


def _slice_section(lines: list[str], start_prefix: str,
                   stop_prefixes: tuple[str, ...]) -> tuple[int, list[str]]:
    """按标题前缀切一节，返回 (节标题所在行号 1-based, 含标题的正文行)。

    找不到就抛 —— **不许静默摘出一个空附录**（那会让"有出处"变成假象）。
    """
    start = next((i for i, ln in enumerate(lines) if ln.startswith(start_prefix)), None)
    if start is None:
        raise SystemExit(f"[FAIL] 跑分报告里找不到小节：{start_prefix}")
    stop = len(lines)
    for j in range(start + 1, len(lines)):
        if any(lines[j].startswith(p) for p in stop_prefixes):
            stop = j
            break
    return start + 1, lines[start:stop]


def bench_excerpt(rep: Path) -> str:
    """把跑分报告的『一、六项指标』『二、承诺违背清单』『七、弃权分解』摘成一份附录。

    ⚠️ **只做搬运 + 标注出处行号，一个新数字都不写**（铁律 8：数字口径单一来源）。
    行号是**算出来的**，不是手写死的 —— 报告一改行号就跟着变，不会悄悄指错地方。
    """
    lines = rep.read_text(encoding="utf-8").splitlines()
    head = lines[:4]                                   # 标题 + 数据集 + 判定配置
    n1, sec1 = _slice_section(lines, "## 一、六项指标", ("## 三、",))
    n7, sec7 = _slice_section(lines, "## 七、弃权分解", ("## 八、",))
    body = "\n".join

    return f"""# 附录 · P2 跑分报告摘录（数字出处）
====================================

本附录回答一个具体问题：**数据包里印的那些跑分数字，出自哪里？**
下面是 `output/p2/bench_report.md`（157 行）第 1~4 行与两个小节的**原文摘录**，
**逐字未改**——没有新增任何未经测量的数字，也没有重排、改写、换算。

**完整报告随源码包交付**（`output/p2/bench_report.md`，可由源码包内 `experiments/p2_bench.py`
重跑产出）。本包是**数据包**，故只摘与本包直接相关的三块，不重复收录全文。

--------------------------------------------------------------------------------
## 报告抬头（出处：`output/p2/bench_report.md:1-4`）

{body(head)}

## 摘录 A · 六项指标 + 承诺违背清单（出处：`output/p2/bench_report.md:{n1}-{n1 + len(sec1) - 1}`）

{body(sec1).rstrip()}

## 摘录 B · 弃权分解（出处：`output/p2/bench_report.md:{n7}-{n7 + len(sec7) - 1}`）

{body(sec7).rstrip()}
--------------------------------------------------------------------------------

**怎么核这份摘录**：打开源码包里的 `output/p2/bench_report.md`，按上面每个小节的
`文件:行号` 对一遍 —— 行号对得上，摘录就是搬运而不是改写。数字口径的单一来源是
`tools/check_number_consistency.py`（它会把对外稿里的回归项数与实跑数对齐）。
"""


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def build_text_index(tp: dict) -> dict[str, list[dict]]:
    """image_id → [该图上配的每套文案]"""
    idx: dict[str, list[dict]] = {}
    for item in tp.get("items", []):
        idx.setdefault(item["image_id"], []).append(item)
    return idx


def readme_txt(item: dict, texts: list[dict]) -> str:
    """官方要求的四要素：来源 / 类型 / 是否伪造 / 伪造方式。"""
    is_fake = item["label"] == "malicious"
    lines = [
        "LorealGuard 测试数据包 —— 单样本说明",
        "=" * 46,
        f"样本编号 : {item['id']}",
        f"来源     : 程序合成（生成器见 ../generators/，无任何个人数据、无网络素材）",
        f"类型     : {'篡改/伪造图' if is_fake else '正常拍摄图（良性对照）'}",
        f"是否伪造 : {'是' if is_fake else '否'}",
        f"伪造方式 : {item['operation'] if is_fake else '不适用（良性样本，未做任何篡改）'}",
        f"样本说明 : {item['desc']}",
        f"期望等级 : {item['expect'].get('max_tier', '（未标注）')}   ← LorealGuard 对它的期望判定上限",
        f"期望规则 : {item['expect'].get('must_fire') or '（本样本期望不命中具体规则，见 MANIFEST.json）'}",
    ]
    if item.get("gt_bbox_xywh"):
        lines.append(f"真值框   : {item['gt_bbox_xywh']}  (x, y, w, h)  ← 用于评估定位精度 IoU")
    if item.get("blind_spot"):
        lines.append(f"公告盲区 : {item['blind_spot']}  ← 我们**主动声明**这条属于已知盲区")
    lines += [
        f"sha256   : {item.get('sha256', '（见 MANIFEST.json）')}",
        f"字节数   : {item.get('bytes', '（见 MANIFEST.json）')}",
        "",
        "文案（如有）：",
    ]
    if texts:
        for t in texts:
            lines.append(f"  [{t['variant']}] {t['text']}")
            lines.append(f"      期望上限 {t['expect'].get('max_tier')}  理由：{t.get('why', '')}")
    else:
        lines.append("  （本样本仅图像通路，未配文案）")
    lines += [
        "",
        "-" * 46,
        "复现：env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p2_suite.py",
        "校验：sha256sum -c SHA256SUMS.txt（在数据包根目录执行）",
        "说明：本包只用于评测「LorealGuard」的判定能力，样本均为程序合成，不含真实人物/个人信息。",
    ]
    return "\n".join(lines) + "\n"


PRIVACY = """LorealGuard 测试数据包 —— 隐私与合规声明
================================================

1. 数据来源
   本包内**全部图像与文案均由程序合成**（生成器脚本随包提供于 generators/）。
   不包含任何真实人物的照片、不包含任何个人信息（无姓名/证件/联系方式/地理位置），
   也不包含任何从互联网抓取的素材。底图为程序生成的合成人像，非真实拍摄。

2. 为什么不直接分发"真实内容"
   真实美妆内容（种草笔记、评论）属于创作者的个人作品与可能的个人数据；
   我们**不抓取、不存储、不再分发**任何真实创作者内容。
   我们分发的对象是**我们自己造的评测样本 + 造它的生成器** ——
   任何人都能用生成器重造出一模一样的样本（逐字节一致，见 SHA256SUMS.txt）。

3. 可复现性
   同一份生成器 + 同一 seed  →  逐字节相同的样本集。
   第三方可以：① 校验我们提交的样本 ② 自行加样本 ③ 用我们公开的跑分器复算我们的每一个数字。

4. 关于 AI 生成内容标识
   部分样本模拟的是"平台对上传图片做过处理"的情形，用于测试标识核验通路。
   我们**不对样本的真假做终审**：LorealGuard 输出的是物证+可解释依据+免责说明，
   最终判定权交由平台与创作者，我们的"不做"清单见项目 README。

5. 免责
   本包仅用于技术评测。LorealGuard 的目标是**降低对真实创作者的误伤**，
   任何单条结论都必须附带"我们不能据此判定你，因为 X"的免责说明。
"""


def write_zip(src_dir: Path, zip_path: Path) -> None:
    """确定性打包：固定时间戳 + 排序 → 同一输入永远同一 sha256。"""
    files = sorted(p for p in src_dir.rglob("*") if p.is_file())
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in files:
            rel = p.relative_to(src_dir.parent).as_posix()
            info = zipfile.ZipInfo(rel, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes())


def comment_readme(p5: dict) -> str:
    """评论区配对集的人读说明（含**先写后测**的承诺原文）。"""
    items = p5["items"]
    L = ["LorealGuard 测试数据包 —— 场景②评论区配对集",
         "=" * 46, "",
         "设计：每条内容下给两份**配对**的评论列表（良性 vs 注水），",
         "      变量只有一个 —— 评论区的真实/注水程度，所以等级差异只能归因评论通路。",
         f"规模：良性 {p5['counts']['benign']} + 误伤探针 {p5['counts']['probe']} + "
         f"注水 {p5['counts']['bot']}（其中三闸门形态 {p5['counts']['bot_seedbatch']}）"
         f" = {len(items)} 条线程",
         "口径：只用给定评论列表的**文本**；零大模型、零账号数据。", "",
         "承诺（写在看结果之前，原样引用）："]
    L += [f"  · {s}" for s in p5["promise"]["statements"]]
    L += ["", "逐条清单（id / 类型 / 形态 / 期望 / 为什么这条重要）：", "-" * 46]
    for it in items:
        L.append(f"{it['id']}  [{it['kind']}/{it.get('mode') or '—'}]  期望 {it['expect']}")
        L.append(f"    商品/场景：{it['context']}")
        for c in it["comments"]:
            L.append(f"      - {c}")
        L.append(f"    为什么：{it['why']}")
        L.append("")
    L += ["复现：env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p5_comment_pairs.py",
          "跑分：env -u PYTHONPATH .venv/Scripts/python.exe experiments/p5_comment_bench.py",
          f"归因纪律：任何弃权都必须带归因码；本层永不出「这是水军」的终审。", ""]
    return "\n".join(L)


def main() -> int:
    for p in (MAIN_MANIFEST, TEXT_MANIFEST, P5_MANIFEST, EDGE_MANIFEST):
        if not p.exists():
            print(f"[FAIL] 缺少清单 {p} —— 请先跑 samples/ 下的四个生成器"
                  f"（含 samples/make_edge_suite.py）")
            return 2
    for d in (SUITE_DIR, EDGE_DIR):
        if not d.exists():
            print(f"[FAIL] 缺少样本目录 {d}")
            return 2

    main_m = load_json(MAIN_MANIFEST)
    text_m = load_json(TEXT_MANIFEST)
    p5_m = load_json(P5_MANIFEST)
    edge_m = load_json(EDGE_MANIFEST)
    text_idx = build_text_index(text_m)
    ver = PACK_VERSION
    pack_name = f"lorealguard_testset_{ver}"
    pack_root = OUT_DIR / pack_name
    if pack_root.exists():
        shutil.rmtree(pack_root)
    (pack_root / "samples").mkdir(parents=True, exist_ok=True)
    (pack_root / "generators").mkdir(parents=True, exist_ok=True)

    n_img = 0
    for item in main_m["items"]:
        img = SUITE_DIR / item["file"]
        if not img.exists():
            print(f"[WARN] 样本文件缺失，跳过：{img.name}")
            continue
        sdir = pack_root / "samples" / item["id"]
        sdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(img, sdir / "image.jpg")
        texts = text_idx.get(item["id"], [])
        (sdir / "text.txt").write_text(
            texts[0]["text"] + "\n" if len(texts) == 1 else
            "".join(f"[{t['variant']}] {t['text']}\n" for t in texts) if texts
            else "（本样本仅图像通路，未配文案）\n",
            encoding="utf-8",
        )
        (sdir / "README.txt").write_text(readme_txt(item, texts), encoding="utf-8")
        n_img += 1

    # ---- 边缘场景（误报护栏的证据）：与 p2 循环同形，三条文件一份不少 ----
    # README 由**生成器**落盘（`output/edge/suite/<id>.README.txt`，与样本同批产出），
    # 这里逐字节拷贝进包 —— 单一模板、单一口径，且第三方拿 generators/ 也能重造出同样的 README。
    n_edge = 0
    for item in edge_m["items"]:
        img = EDGE_DIR / item["file"]
        rd = EDGE_DIR / f"{item['id']}.README.txt"
        if not img.exists() or not rd.exists():
            print(f"[FAIL] 边缘样本或其 README 缺失：{img.name} / {rd.name}"
                  f"（缺了不静默少样本 —— 请重跑 samples/make_edge_suite.py）")
            return 2
        sdir = pack_root / "samples" / item["id"]
        sdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(img, sdir / "image.jpg")
        (sdir / "text.txt").write_text("（本样本仅图像通路，未配文案）\n", encoding="utf-8")
        shutil.copy2(rd, sdir / "README.txt")
        n_edge += 1

    # 场景②评论区：配对集作为一个 JSON + 一份人读说明进包
    (pack_root / "comments").mkdir(parents=True, exist_ok=True)
    (pack_root / "comments" / "comment_threads.json").write_text(
        json.dumps(p5_m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (pack_root / "comments" / "README.txt").write_text(comment_readme(p5_m), encoding="utf-8")

    # 生成器：**缺一个就拒发**。原先写的是 `if src.exists()` 静默跳过 —— 那正是 v2 漏掉
    # make_red_team.py 却没被发现的原因（生成器与冻结物证对不上，包本身不报错）。
    for g in GENERATORS + PROVENANCE_GENERATORS:
        src = ROOT / g
        if not src.exists():
            print(f"[FAIL] 生成器缺失：{g}（生成器与包内物证对不上则不打包，避免出一个'来路不明'的包）")
            return 2
        shutil.copy2(src, pack_root / "generators" / Path(g).name)
    (pack_root / "generators" / "README.txt").write_text(GENERATORS_README, encoding="utf-8")

    # ---- 证据附件：报告 + 协议 + 冻结语料 + 登记表 + 跑分器 ----
    ev = pack_root / "EVIDENCE"
    ev.mkdir(parents=True, exist_ok=True)
    # 报告用交付版的**生成函数**产出（抬头是常量、正文是仓库报告原文），
    # 这样包内这份与交付目录那份逐字节同源，且都能从冻结语料重放。
    import importlib.util
    _spec = importlib.util.spec_from_file_location("_rt_delivery", ROOT / "tools" / "make_redteam_delivery.py")
    _rt = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_rt)
    (ev / "redteam-report.md").write_bytes(_rt.build())
    for src_rel, dst_name, _desc in EVIDENCE_FILES:
        src = ROOT / src_rel
        if not src.exists():
            print(f"[FAIL] 证据附件缺失：{src_rel}（报告/语料不齐则不打包，避免出一个证据不完整的包）")
            return 2
        shutil.copy2(src, ev / dst_name)
    (ev / "README.txt").write_text(EVIDENCE_README, encoding="utf-8")
    # 跑分报告摘录：从报告原文切片生成（带算出来的出处行号），**不新写数字**
    if not BENCH_REPORT.exists():
        print(f"[FAIL] 跑分报告缺失：{BENCH_REPORT}（附录要从它摘，缺了不打包）")
        return 2
    excerpt = bench_excerpt(BENCH_REPORT)
    (ev / "bench_excerpt.md").write_text(excerpt, encoding="utf-8")
    # 生成参数说明（实话版）：由 generation_params_md() 从源码实时抽出，不手抄 ⇒ 不会漂移
    (ev / "generation_params.md").write_text(generation_params_md(), encoding="utf-8")

    (pack_root / "MANIFEST.json").write_text(
        json.dumps({"image_channel": main_m, "text_channel": text_m,
                    "comment_channel": p5_m, "edge_channel": edge_m,
                    "evidence": {
                        "note": "EVIDENCE/ 附件登记。附录类文件只做搬运 + 标注出处，不含新数字。",
                        "red_team_corpus": {
                            "file": "EVIDENCE/red_team.json",
                            "generator": "generators/make_red_team.py",
                            "model_tier": "research_only（非交付运行时白名单档）",
                            "runtime_use": "none —— 不参与交付运行时任何调用",
                        },
                        "bench_excerpt": {
                            "file": "EVIDENCE/bench_excerpt.md",
                            "source": "output/p2/bench_report.md",
                            "sections": ["报告抬头 1-4", "一、六项指标 + 二、承诺违背清单",
                                         "七、弃权分解"],
                            "full_report": "随决赛源码包交付（源码包内 experiments/p2_bench.py 可重跑产出）",
                        },
                        "generation_params": {
                            "file": "EVIDENCE/generation_params.md",
                            "source": "由打包器从 samples/make_p2_suite.py 用例表 + 各生成器 SEED 实时抽出",
                            "note": "本包未使用任何扩散模型 ⇒ 不存在 SDXL 生成参数；该文件为实话版口径说明",
                        },
                    }},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (pack_root / "PRIVACY.txt").write_text(PRIVACY, encoding="utf-8")

    # 边缘场景明细表（从清单生成 —— 措辞与逐条 desc 单一来源，不在这里另写一套）
    edge_rows = "\n".join(
        f"| `{it['id']}` | {it['category']} | {it['operation']} | {it['desc']} |"
        for it in edge_m["items"])

    # 人读说明
    readme = f"""# {pack_name} —— LorealGuard 测试数据包

> 提交通道：天池·欧莱雅美妆科技黑客松 2026 · 赛题2「信任守护师」

## 官方格式对照

| 官方要求 | 本包对应文件 |
|---|---|
| 每样本一文件夹 | `samples/<样本编号>/`：主集 {n_img} 个 + 边缘场景 {n_edge} 个，**共 {n_img + n_edge} 个样本目录** |
| 数据涵盖**边缘场景** | 边缘场景 {n_edge} 个（在主集 {n_img} 个**之外**，见下节），逐条 README 写明来源/类型/是否伪造/伪造方式 |
| 文案 `.txt` | `samples/<编号>/text.txt` |
| 图 `.jpg` | `samples/<编号>/image.jpg` |
| `README.txt` 注明 来源/类型/是否伪造/伪造方式 | `samples/<编号>/README.txt`（四要素逐条写明） |

## 包里有什么

| 文件 | 说明 |
|---|---|
| `MANIFEST.json` | 机器可读全量清单：逐条 sha256、真值框、**期望等级与期望规则**（四条通路：图像/文案/评论/边缘） |
| `SHA256SUMS.txt` | 逐文件哈希，`sha256sum -c` 校验 |
| `PRIVACY.txt` | 隐私与合规声明（**全部程序合成、无个人数据**） |
| `generators/` | 生成器脚本：任何人可重造出逐字节相同的样本（含边缘场景集） |
| `comments/comment_threads.json` | 场景②评论区配对集（机器可读，含逐条期望与理由） |
| `comments/README.txt` | 场景②人读说明（含承诺原文与逐条清单） |
| `EVIDENCE/` | **证据附件**：红队报告 + 协议原文 + 冻结语料 + 红线登记表 + 跑分器 + 跑分报告摘录 + **生成参数说明**（见 `EVIDENCE/README.txt`、`EVIDENCE/bench_excerpt.md`、`EVIDENCE/generation_params.md`） |

## 三条通路的样本（对齐赛题点名的三个场景）

- **场景①图像通路**：{main_m['counts']['benign']} 良性 + {main_m['counts']['malicious']} 恶意
  （恶意样本覆盖多种篡改几何，用于测「定位」与「漏报」）。
- **场景①文案通路**：{text_m['counts'].get('benign_text', '?')} 合规话术 + {text_m['counts'].get('malicious_text', '?')} 夸大话术，
  **配对设计**：同一张良性图配两套文案 → 等级差异只能归因文案通路。
- **场景②评论区通路**：{p5_m['counts']['benign']} 良性线程 + {p5_m['counts']['probe']} 误伤探针 + {p5_m['counts']['bot']} 注水线程，
  **配对设计 + 三种注水形态**（逐字复制 / 纯模板 / 纯引流）→ 每条风险规则都要能独立命中。
  明细见 `comments/README.txt`。

## 边缘场景（误报护栏的证据）

官方评分第 2 位问「测试数据质量是否满足提交格式，**数据是否涵盖边缘场景**」
（赛题原文 `loreal-topic2-details.md:21`，原词）。本包用 **{edge_m['count']} 条**边缘样本正面回答它 ——
它们**全是良性**（`是否伪造：否`，逐条 README 写明），**不新增任何检出靶**：

| 样本 | 类型 | 变换 | 说明 |
|---|---|---|---|
{edge_rows}

这 {n_edge} 条的回答方式不是"我们能检出更多"，而是**"这些输入我们不冤枉人"**：
每条的 `expect.max_tier` 在看结果之前就写死为**关注级**（关注级 = rank 2 ⇒ **不得判违规**），
跑分器逐条比对，不符合期望的条目如实在案。其中 `edge_inplace_q75` 是我们**主动交代**的能力边界：
原位降质重压缩（几何未变）会被推上关注级 —— 那是**复核提示，不是判违规**（登记于 `docs/DEBT.md` D-12）。
`edge_resized_q75` 是同质量、只多一步改尺寸的对照：它信号更强反而不触发，说明这条分界线落在
「几何是否变化」上，**不是**阈值松紧问题。

## `EVIDENCE/` 里的红队物证：模型口径如实说明

`EVIDENCE/red_team.json` 里记着 `"model": "deepseek-v4.1-flash"`。**这是物证内容，我们不改写它** ——
但必须把口径讲清楚，否则容易被误读成"你们的产品依赖这个模型"：

1. **它是内部质检产物**：红队语料是我们在**离线研究 / 内部质检**阶段，用**研究档模型**
   （非交付运行时白名单档）生成的，用途是拿自家规则表做对抗测试。
2. **它不参与交付运行时的任何调用**。交付运行时**唯一允许的档**是 `qwen3.8-27b`
   （Apache-2.0 开源权重，白名单见 `voiceguard/config.py:37-38`）；非白名单档
   **一调用即拒，且不发起任何请求**（`voiceguard/semantic/explain.py:86-90`）。
3. 交付运行时里，模型只出现在**判定完成之后**的解释环节 —— **判定链零模型调用**
   （坐标由算法给、语义由模型给，模型从不输出"伪造 / 篡改 / 真假 / 拼接"结论）。
4. `EVIDENCE/` 还附了跑分报告摘录 `bench_excerpt.md`（六项指标 / 承诺兑现 / 弃权分解），
   **带 `文件:行号` 出处、只搬运不改写**；完整报告随**源码包**交付。

## 一个数字请特别看

本数据包是**承诺制**的：每一条的期望等级**在看结果之前就写死在清单里**，
跑分器只做逐条比对。所以「承诺兑现率」是我们愿意被复查的指标 ——
不合期望的条目**如实在案**，不删、不挪阈值。

## 复现（单行）

```bash
# 1. 重造样本（与包内逐字节一致）
env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p2_suite.py
env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p2_text_pairs.py
env -u PYTHONPATH .venv/Scripts/python.exe samples/make_p5_comment_pairs.py
env -u PYTHONPATH .venv/Scripts/python.exe samples/make_edge_suite.py

# 2. 复算我们的每个数字（含误报上界与检出的置信下界）
env -u PYTHONPATH .venv/Scripts/python.exe experiments/p2_bench.py
env -u PYTHONPATH .venv/Scripts/python.exe experiments/p2_text_bench.py
env -u PYTHONPATH .venv/Scripts/python.exe experiments/p5_comment_bench.py
```

## 我们**不**声称什么

- 不声称能对内容真假做终审（我们给物证与可解释依据，最终判定权交平台与创作者）。
- 不声称这个自造集能替代真实世界评测（这是本包**唯一拆不掉的弱点**，已公开登记）。
- 不声称能覆盖全部伪造手法（盲区清单与漏报构成在跑分报告里逐条公开）。
- 不声称"包内可原地复算全部数字"：跑分器需要仓库内规则表，本包是数据包；`EVIDENCE/README.txt` 写清了
  第三方**不需要仓库**就能核的三件事。
"""
    (pack_root / "README.md").write_text(readme, encoding="utf-8")

    # 逐文件哈希（相对包根，排序 → 稳定）
    sums = []
    for p in sorted(pack_root.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS.txt":
            rel = p.relative_to(pack_root).as_posix()
            sums.append(f"{sha256_file(p)}  {rel}")
    (pack_root / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")

    zip_path = OUT_DIR / f"{pack_name}.zip"
    write_zip(pack_root, zip_path)
    zip_sha = sha256_file(zip_path)
    (OUT_DIR / f"{pack_name}.zip.sha256").write_text(f"{zip_sha}  {zip_path.name}\n", encoding="utf-8")

    print(f"[OK] 样本文件夹 {n_img + n_edge} 个（p2 {n_img} + 边缘 {n_edge}）→ {pack_root}")
    print(f"[OK] 提交压缩包 → {zip_path}")
    print(f"[OK] zip sha256 = {zip_sha}")
    total_files = sum(1 for p in pack_root.rglob("*") if p.is_file())
    total_bytes = sum(p.stat().st_size for p in pack_root.rglob("*") if p.is_file())
    # 硬断言：SHA256SUMS 必须覆盖除自身以外的每一个文件（差一个 = 交付包有漏网文件）
    assert len(sums) + 1 == total_files, f"SHA256SUMS 覆盖不全：{len(sums) + 1} != {total_files}"
    print(f"[OK] 包内文件数 = {total_files}（{len(sums)} 条进 SHA256SUMS，清单自身不计入）")
    print(f"[OK] 解压后总字节 = {total_bytes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())