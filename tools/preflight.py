"""LorealGuard · 上场前自检（一条命令）

    python tools/preflight.py

它问 7 个问题（**只读**；唯一的写入是 demo 冒烟在 output/demo/ 落一份产物，那个目录本来就被 .gitignore 排除）：

  ① 口径闸（数字单一来源）         → exit 0
  ② 演示引擎冒烟（同进程）          → 账本**被判定字段确有值** + 4 件产物在场
  ③ 黄金测试包                     → 228 文件 / 19,645,182 B / sha16 7eaa8ac3a3cc3675
  ④ 成片                           → 1920x1080 / 179.000 s / 5370 帧 / sha16 ee2b2067f4c44057
  ⑤ 交付区 PPT / PDF               → 字节 + sha16 **＋ PDF 图像负载下限**（防"字节正常、图像全丢"）
  ⑥ 文档引用可追（tools/check_refs.py） → 正本=副本 ＋ 自证伪 ＋ 7 份交付文档的「文件:行号」全都能追到真身
  ⑦ 汇总                           → 有任何 FAIL 就 exit 1

为什么要有它：赛题评分第 1 位要「**可运行、可展示的完整项目形态**」，决赛是**现场跑**。
上台前一条命令把「闸绿不绿 / 引擎跑不跑得动 / 三个产物还在不在」一次问完，
而不是在台上才发现。**一条不会红的检查等于没有检查** —— 下面每一项都有确切的期望值，
任一项对不上就红，并印出「实测 vs 冻结基线」。

纪律：
  · **不新增判定**：不写阈值、不重算等级；② 只调既有入口 `demo/serve.py::run_once` 并**读回产物**。
  · **零网络**：冒烟走 F3 / Agent **默认关**的路径（与 `demo/serve.py` 纪律② 一致），不起端口。
  · 期望值全部是**实测冻结基线**。产品真改了就要连这里一起改；忘了改 ⇒ 自检变红，
    这正是它该有的行为（改前先跑 `tests/test_rebuild_from_scratch.py` 那一套）。
  · 交付区不在本仓库内 ⇒ 用 `--compete` 指定，默认 `D:/deliver/compete`。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")          # 一次性脚本纪律：先钉 UTF-8，免得 GBK 崩
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPETE = Path(r"D:\deliver\compete")

#: 同目录工具：交付 PDF 的**图像负载下限**判据（判据本体与真伤现场见该模块 docstring）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_pdf_payload  # noqa: E402  —— 必须在 sys.path 之后导入才找得到同目录模块

#: 冻结基线（全部实测；改动前请看模块 docstring 最后一条纪律）
BASELINE = {
    #: ⚠️ `bytes` = **zip 文件本身的字节数**（`check_golden` 比的是 `p.stat().st_size`），
    #: **不是**「解压后总字节」（v3 那份解压后是 20,066,051 —— 两个数差一倍量级里的零头，
    #: 混用只会让 ③ 恒红且看不出为什么）。`files` = zip 内**非目录**条目数（含 SHA256SUMS 自身）。
    #: 2026-09-29 换 v4：228 文件 / 19,645,182 B / sha16 7eaa8ac3a3cc3675（新增
    #: `EVIDENCE/generation_params.md` —— 实话版生成参数，由打包器从源码实时抽出；**样本一字节未变**）。
    #: 已作废的旧基线（v1/v2/v3 仍留盘上，但**提交只交 v4**）：v3 227 / 19,640,121 /
    #: 7a0d7d4c6a413cd8、v2 224 / 19,625,266 / 1db1208994dec901。
    "golden": {"files": 228, "bytes": 19_645_182, "sha16": "7eaa8ac3a3cc3675"},
    #: ⚠️ 2026-10-03 换版（**重跑 E1+E2**）：演示镜 D1–D5 按改版后页面重录 ＋ 摇镜落点语义修正
    #: （`docs/DEBT.md` D-45 / D-47）⇒ S04/S08/S11 重建、母版重拼、成片重出。
    #: ⚠️ 2026-10-05 二次修（**字幕锚真实句界 ＋ BGM 钉种子**，`docs/DEBT.md` D-51 / D-52）：
    #: 只重算字幕层 —— 10 个 mp3 逐字节未动（10/10 复核），但 BGM 粉噪原先**无种子** ⇒ 音轨
    #: 每次重拼都变（D-52），故本轮 sha 变化**含这一份修正**；修后**连拼两次 sha 完全相同**
    #: （可重放）。作废值（时间倒序）：`8,791,419 / fa427b95071b3338`（2026-10-05 换云希那份，
    #: D-50）、`8,878,108 / 1e5699db6206631c`（2026-10-03）。时长 / 帧数 / 几何**三项仍未变**。
    "film": {"bytes": 8_776_324, "sha16": "ee2b2067f4c44057",
             "duration": 179.000, "frames": 5370, "w": 1920, "h": 1080},
    #: ⚠️ 2026-09-29 同日**四次**重出 deck，只认最后一次：**19 页 / 官方模板三字段补齐
    #: （eyebrow 加板块名）/ 回归 527 / 数据包升 v4 后印章图随页重画**。作废值（时间倒序）：
    #: 17,203,120 / db9a9de87b9d2519 ＋ 4,003,956 / fbd4576a155166db（同日第三次）、
    #: 17,201,350 / 3a2cf529be37fa86、17,201,617 / 89eb615ac41cc477、3,920,277 /
    #: 3ad19656df6fadb9、17,194,991 / 333daa9ee504bde3（18 页）、3,739,128 / e4df84b8be7b34f0
    #: ⇒ 登记在 docs/DEBT.md D-38 / D-39 / D-40。**当轮＝同日第四次**：封面补齐官方模板三字段
    #: （作品名称 / 赛道 / 队伍名称＝小白学习记）—— 只动封面文字，判定字段 / 数字 / 图表一字未动
    #: （差异逐像素落在封面信息栏，见 D-40）。
    #: ⚠️ PDF 的字节 + sha16 只是**第一层**（"这份文件没被人动过"）。2026-09-29 抓到过一次
    #: **字节看着正常、图像全丢**的坏件（1,908 → 34 个图像对象 / 22.6 KB，真因＝挂到遗留的
    #: PowerPoint COM 实例）⇒ 这里再加**第二层**图像负载下限；两层都绿才算交付区可用。
    "deck": {"pptx_bytes": 17_203_219, "pptx_sha16": "0146fe0a463bcc03",
             "pdf_bytes": 3_968_499, "pdf_sha16": "3d5c2289348f1187",
             #: 19 页当轮实测 2,002 个图像对象 / 3,111,958 B（同日四次导出区间 2,002–2,130 个 /
             #: 3.11–3.12 MB —— **对象数随 PowerPoint 每次切片浮动，图像字节才是真不变量**）；
             #: 坏件 34 个 / 22,633 B；历史 18 页 1,556 个 / 2,848,919 B ⇒ 下限取历史值的约
             #: 1/3（3 倍余量），坏件在两侧都被判红。
             "pdf_min_image_objects": 200, "pdf_min_image_bytes": 1_000_000},
}

#: 冒烟输入（程序合成样图 + 一条文案 + 两条评论；explain / agent **不传** = 默认关）
SMOKE_PAYLOAD = {
    "image": "samples/base_neutral.jpg",
    "text": "全新配方首发｜这瓶精华真的绝了｜#护肤 #测评",
    "comments": "求链接\n已下单，坐等",   # ⚠️ 一行一条的**字符串**（列表会被 BadRequest 拒）
}


def sha16(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _clean_env() -> dict:
    """canonical 环境：剥掉 PYTHONPATH / PYTHONUTF8 / PYTHONIOENCODING（本机 shell 带 PYTHONUTF8=1 ⇒ 假红）。"""
    import os
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONUTF8", "PYTHONIOENCODING"):
        env.pop(k, None)
    return env


# ------------------------------------------------------------------ ① 口径闸

def check_numbers() -> tuple[bool, str]:
    p = ROOT / "tools" / "check_number_consistency.py"
    if not p.is_file():
        return False, f"找不到 {p.name}"
    r = subprocess.run([sys.executable, str(p)], cwd=str(ROOT), env=_clean_env(),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tail = (r.stdout or "").strip().splitlines()
        return False, "exit " + str(r.returncode) + "（最后一行：" + (tail[-1][:120] if tail else "无输出") + "）"
    return True, "exit 0"


# ------------------------------------------------------------- ② 演示引擎冒烟

def _load_serve():
    spec = importlib.util.spec_from_file_location("lorealguard_demo_serve",
                                                 ROOT / "demo" / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_engine() -> tuple[bool, str]:
    try:
        mod = _load_serve()
    except Exception as exc:                       # noqa: BLE001
        return False, "载入 demo/serve.py 失败：" + type(exc).__name__ + " " + str(exc)[:100]
    try:
        out = mod.run_once(dict(SMOKE_PAYLOAD))    # explain_ask/agent_ask 不传 ⇒ 零网络
    except Exception as exc:                       # noqa: BLE001
        return False, "冒烟跑挂了：" + type(exc).__name__ + " " + str(exc)[:140]

    if not isinstance(out, dict) or not out.get("ok"):
        return False, "run_once 没回 ok=True"
    rep = out.get("report") or {}
    verdict = rep.get("verdict") or {}
    # ⚠️ 防「假通过」：判定的字段必须**确有值**，不能只看 ok
    if not verdict.get("tier"):
        return False, "账本里 verdict.tier 是空的（判定字段没值 = 假通过）"
    if not verdict.get("confidence"):
        return False, "账本里 verdict.confidence 是空的"
    if not isinstance(rep.get("detectors"), list) or not rep["detectors"]:
        return False, "detectors 不是非空 list（层 1 没真跑）"
    if not isinstance(rep.get("layers"), list) or not rep["layers"]:
        return False, "layers 不是非空 list"
    if not rep.get("shape"):
        return False, "shape 是空的"

    tag = out.get("tag") or ""
    demo_dir = ROOT / "output" / "demo"
    want = [f"{tag}_report.json", f"{tag}_report.md",
            f"{tag}_decision.json", f"{tag}_evidence_sheet.png"]
    missing = [w for w in want if not (demo_dir / w).is_file()]
    if missing:
        return False, "产物缺 " + str(len(missing)) + " 件：" + ", ".join(missing)

    det = len(rep["detectors"])
    return True, ("tier=" + str(verdict["tier"]) + " conf=" + str(verdict["confidence"])
                  + " detectors=" + str(det) + " 产物 4/4 tag=" + tag)


# ------------------------------------------------------------- ③ 黄金测试包

def check_golden(compete: Path) -> tuple[bool, str]:
    import zipfile
    p = compete / "lorealguard_testset_v4.zip"
    if not p.is_file():
        return False, "找不到 " + str(p)
    want = BASELINE["golden"]
    n = len([x for x in zipfile.ZipFile(p).namelist() if not x.endswith("/")])
    b = p.stat().st_size
    s = sha16(p)
    got = "files=" + str(n) + " B=" + str(b) + " sha16=" + s
    want_s = ("files=" + str(want["files"]) + " B=" + str(want["bytes"])
              + " sha16=" + want["sha16"])
    if (n, b, s) != (want["files"], want["bytes"], want["sha16"]):
        return False, "实测 [" + got + "] ≠ 基线 [" + want_s + "]"
    return True, got


# ------------------------------------------------------------------- ④ 成片

def check_film() -> tuple[bool, str]:
    p = ROOT / "output" / "film" / "LorealGuard-demo-3min.mp4"
    if not p.is_file():
        return False, "找不到 " + str(p)
    want = BASELINE["film"]
    b = p.stat().st_size
    s = sha16(p)
    head = "B=" + str(b) + " sha16=" + s
    if (b, s) != (want["bytes"], want["sha16"]):
        return False, ("实测 [" + head + "] ≠ 基线 [B=" + str(want["bytes"])
                       + " sha16=" + want["sha16"] + "]")
    if shutil.which("ffprobe") is None:
        return True, head + "（无 ffprobe ⇒ 跳过时长/帧数核对）"
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames,duration,width,height",
         "-of", "default=nw=1", str(p)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    kv = dict(
        ln.split("=", 1) for ln in (r.stdout or "").splitlines() if "=" in ln
    )
    try:
        dur = float(kv.get("duration") or "nan")
        frames = int(kv.get("nb_read_frames") or "-1")
        w, h = int(kv.get("width") or "-1"), int(kv.get("height") or "-1")
    except ValueError:
        return False, "ffprobe 输出读不懂：" + (r.stdout or "")[:120]
    ok = (abs(dur - want["duration"]) < 0.001 and frames == want["frames"]
          and (w, h) == (want["w"], want["h"]))
    got = (str(dur) + "s frames=" + str(frames) + " " + str(w) + "x" + str(h))
    if not ok:
        return False, ("实测 [" + got + "] ≠ 基线 [" + str(want["duration"]) + "s frames="
                       + str(want["frames"]) + " " + str(want["w"]) + "x" + str(want["h"]) + "]")
    return True, head + " " + got


# ------------------------------------------------------------ ⑤ 交付区 PPT/PDF

def check_deck(compete: Path) -> tuple[bool, str]:
    d = compete / "loreal-ppt"
    pptx, pdf = d / "LorealGuard-Deck-v1.pptx", d / "LorealGuard-Deck-v1.pdf"
    for p in (pptx, pdf):
        if not p.is_file():
            return False, "找不到 " + str(p)
    w = BASELINE["deck"]
    got = ("pptx B=" + str(pptx.stat().st_size) + " sha16=" + sha16(pptx)
           + " | pdf B=" + str(pdf.stat().st_size) + " sha16=" + sha16(pdf))
    ok = (pptx.stat().st_size == w["pptx_bytes"] and sha16(pptx) == w["pptx_sha16"]
          and pdf.stat().st_size == w["pdf_bytes"] and sha16(pdf) == w["pdf_sha16"])
    if not ok:
        return False, ("实测 [" + got + "] ≠ 基线 [pptx B=" + str(w["pptx_bytes"])
                       + " sha16=" + w["pptx_sha16"] + " | pdf B=" + str(w["pdf_bytes"])
                       + " sha16=" + w["pdf_sha16"] + "]")
    # 第二层：**图像负载还在场**（判据本体在 tools/check_pdf_payload.py，这里只调用 + 转发读数）。
    # 为什么必须有：2026-09-29 那份坏 PDF 字节自洽、exit 0、体积只小 36%，只有"数图像对象"
    # 判得出它已经丢了全部图像 ⇒ **第一层绿 != 这份文件还能用**。
    has_img, img_msg, _ = check_pdf_payload.check(
        pdf, w["pdf_min_image_objects"], w["pdf_min_image_bytes"])
    if not has_img:
        return False, img_msg
    return True, got + " | " + img_msg


# ------------------------------------------------------- ⑥ 面向评委文档的引用

def check_refs(compete: Path) -> tuple[bool, str]:
    """⑥ 交付文档里的「文件:行号」必须能追到真身（判据本体在 tools/check_refs.py）。

    三层，缺一层这条闸就可能变空转：
      ① **正本=副本**：交付区 `_check_refs.py` 必须与仓库 `tools/check_refs.py` **逐字节同一份**
         （两处各自被改过＝静默漂移；2026-09-30 就是这么漏掉 6 处旧读数与旧行号的）；
      ② **先自证伪**：`--self-check` 喂假引用必须被抓到 —— 校验器自己不会红，这条闸就没意义；
      ③ **再跑真文档**：文档清单由校验器 `--list-docs` 自报（清单只在一处维护），任一条失败即红。
    """
    tool = Path(__file__).with_name("check_refs.py")
    if not tool.is_file():
        return False, "找不到 " + tool.name
    twin = compete / "_check_refs.py"
    if not twin.is_file():
        return False, "交付区缺副本 _check_refs.py"
    if tool.read_bytes() != twin.read_bytes():
        return False, "交付区 _check_refs.py 与仓库 tools/check_refs.py 不是同一份字节（漂移）"
    py = sys.executable

    def run(*extra):
        return subprocess.run([py, str(tool)] + list(extra), cwd=str(ROOT), env=_clean_env(),
                              capture_output=True, text=True, encoding="utf-8", errors="replace")

    r = run("--self-check")
    if r.returncode != 0:
        return False, "自证伪未过（校验器不可信，exit " + str(r.returncode) + "）"
    r = run("--list-docs")
    names = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    if r.returncode != 0 or len(names) < 3:
        return False, "取不到默认文档清单（--list-docs 只报 " + str(len(names)) + " 行）"
    docs = [compete / Path(n).name for n in names]
    r = run(*[str(d) for d in docs])
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    summary = lines[-1][:120] if lines else "无输出"
    if r.returncode != 0:
        bad = [ln.strip() for ln in lines if ln.strip().startswith("[FAIL]")]
        detail = bad[0][:110] if bad else summary
        if len(bad) > 1:
            detail = detail + " 等 " + str(len(bad)) + " 条"
        return False, "exit " + str(r.returncode) + "（" + detail + "）"
    return True, "正本=副本 ｜ 自证伪 OK ｜ " + summary


# --------------------------------------------------------------------- 主流程

CHECKS = ("① 口径闸", "② 演示引擎冒烟", "③ 黄金测试包", "④ 成片", "⑤ 交付区 PPT/PDF",
          "⑥ 文档引用可追")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools/preflight.py",
                                 description="LorealGuard 上场前自检（只读，零网络）")
    ap.add_argument("--compete", default=str(DEFAULT_COMPETE),
                    help="交付区目录（默认 D:/deliver/compete）")
    ap.add_argument("--quiet-nc", action="store_true", help="不转发口径闸的输出")
    args = ap.parse_args(argv)
    compete = Path(args.compete)

    print("=== LorealGuard 上场前自检 ===")
    results = [
        (CHECKS[0], check_numbers()),
        (CHECKS[1], check_engine()),
        (CHECKS[2], check_golden(compete)),
        (CHECKS[3], check_film()),
        (CHECKS[4], check_deck(compete)),
        (CHECKS[5], check_refs(compete)),
    ]
    for name, (ok, msg) in results:
        print(("  [PASS] " if ok else "  [FAIL] ") + name + "  " + msg)
    n_ok = sum(1 for _, (ok, _) in results if ok)
    n = len(results)
    if n_ok == n:
        print("--- " + str(n_ok) + "/" + str(n) + " 通过：闸绿、引擎跑得动、三个产物都在基线上一一 可以上台 ---")
        return 0
    print("--- " + str(n_ok) + "/" + str(n) + " 通过：上面标 [FAIL] 的项，**别上台**，先照它印的「实测 vs 基线」查 ---")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())