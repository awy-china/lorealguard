"""一键录制演示视频三段真跑素材（A/B/C），并**用像素验证屏幕内容**。

为什么必须要有这个脚本
--------------------
1) 项目铁律：手工在窗口里敲出来的东西必须变成脚本，否则不可重造、不可复跑。
2) 2026-09-20 事故（本脚本诞生的直接原因）：把 Git-Bash 的 `env -u PYTHONPATH`
   直译成 cmd 的 `set PYTHONPATH=` —— 在 cmd 里这不是"取消变量"，而是**留下一个空值**
   PYTHONPATH，Python 启动即 `Fatal Python error: error evaluating path`。
   三段录屏于是全是废片（画面里只有崩溃回溯），但 `record_demo.py` 的自检
   全绿：文件在、非黑帧、时长合格 —— **判据错了，绿就是假的**。
   所以本脚本录完必须抽帧 → VL 逐字读回 → 与承诺数字机械比对；
   读不回来就把 mp4 挪进 `_rejected/` 并以非 0 退出：废片不许留在证据目录里冒充素材。

用法
----
    cd /d/LorealGuard && env -u PYTHONPATH .venv/Scripts/python.exe tools/record_all_demo.py
    （只录一段：--only A ；只抽帧不重录：--verify-only）
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import record_demo as rd  # noqa: E402
from voiceguard.semantic.vl import ask_vl  # noqa: E402

EV = ROOT / "output" / "video_evidence"
FRAMES = EV / "_vlframes"
REJECTED = EV / "_rejected"

# ⚠️ 命令里**不许**出现 `set PYTHONPATH=`（空值 PYTHONPATH 会让 Python 启动即崩，见文件头）；
#    `.bat` 以纯 ASCII 写盘，所以命令里也不许出现中文（会被 replace 成 ?）。
#    这两条由 tests/test_demo_recordings.py 断言。
DEMO = [
    dict(
        # seconds 是**上限**（兜底）。收尾由工具的"跑完标记"决定，
        # 抽帧点也是算出来的（末尾前一屏），不再靠猜第几秒是"跑完之后"。
        tag="A", title="LGUARD_REC_A", seconds=120,
        out="recA_p2_bench.mp4",
        cmd=r"cd /d D:\LorealGuard && .venv\Scripts\python.exe experiments\p2_bench.py",
        expect=["误报率", "0.0%", "7.2%", "75.0%", "71.7%", "0.841", "60/60"],
    ),
    dict(
        tag="B", title="LGUARD_REC_B", seconds=90,
        out="recB_pack.mp4",
        cmd=r"cd /d D:\LorealGuard && .venv\Scripts\python.exe tools\make_submission_pack.py",
        expect=["228", "7eaa8ac3"],
    ),
    dict(
        tag="C", title="LGUARD_REC_C", seconds=60,
        out="recC_abstain.mp4",
        # 小节名走脚本默认值（中文不能进 .bat）；先把控制台拉宽，
        # 否则表格右侧那列（占该侧弃权 = 我们要的 5 个数）会被切在屏外；
        # `&` 不是 `&&`：mode 失败也不许中断后面的真命令。输出重定向掉以免脏屏。
        cmd=r"mode con: cols=190 lines=45 >nul 2>&1 & cd /d D:\LorealGuard & "
            r".venv\Scripts\python.exe tools\show_bench_section.py --max-lines 18",
        expect=["60.5%", "28.9%", "5.3%", "2.6%", "弃权"],
    ),
]

QUESTION = (
    "这是一个 Windows 终端窗口的截图。请**逐字抄写**画面上的文字，"
    "不要解释、不要总结，尤其要准确抄下所有数字和百分数。看不清的字写『?』。"
)


def grab_frame(video: pathlib.Path, ts: float, tag: str) -> pathlib.Path:
    FRAMES.mkdir(parents=True, exist_ok=True)
    png = FRAMES / f"{tag}_{ts:.0f}s.png"
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


def verify(case: dict) -> tuple[bool, str]:
    """抽帧 → VL 逐字读回 → 机械比对。返回 (是否通过, 说明)。"""
    video = EV / case["out"]
    if not video.exists():
        return False, f"缺文件 {video}"
    # 抽帧点 = **最后一帧**：录制端在"跑完标记"后又录了 6s。
    # 为什么不是"标记时刻 +1s"：未获焦点的 Windows Terminal 渲染会被节流，
    # 画面比进程输出落后 1~3s（实测：命令 14.55s 跑完，+2.2s 的那一帧还停在 m18）。
    dur = float(rd.probe(video).get("duration", 0) or 0)
    ts = max(0.5, dur - 0.4)
    png = grab_frame(video, ts, case["tag"])
    res = ask_vl(png, QUESTION)
    ans = res.get("answer") or ""
    miss = [e for e in case["expect"] if e not in ans]
    print(f"      帧 {png.name} ({png.stat().st_size} B) | VL ok={res.get('ok')} "
          f"reasoning={res.get('reasoning_tokens')} | 命中 {len(case['expect']) - len(miss)}/{len(case['expect'])}")
    if miss:
        print(f"      ❌ 未读回：{miss}")
        print("      --- VL 逐字读回原文 ---")
        for ln in (ans or f"(空) err={res.get('error')}").splitlines():
            print(f"      | {ln}")
        return False, f"期望串未读回 {miss}"
    print("      ✅ 承诺数字全部逐字命中")
    return True, "ok"


def reject(case: dict, why: str) -> None:
    """废片挪进 _rejected/ 并留一张说明，绝不留在证据目录里冒充素材。"""
    REJECTED.mkdir(parents=True, exist_ok=True)
    src = EV / case["out"]
    if src.exists():
        dst = REJECTED / f"{case['tag']}_{case['out']}"
        shutil.move(str(src), str(dst))
        (REJECTED / f"{case['tag']}_WHY.txt").write_text(
            f"{case['out']} 被拒收（{why}）\n命令: {case['cmd']}\n"
            "废片特征：文件在、非黑帧、时长合格，但屏幕内容不是承诺的那几行。\n",
            encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只处理某一段（A/B/C）")
    ap.add_argument("--verify-only", action="store_true", help="不重录，只抽帧复核已有 mp4")
    a = ap.parse_args()

    cases = [c for c in DEMO if not a.only or c["tag"] == a.only]
    if not cases:
        print(f"[X] 没有匹配的段：--only {a.only}")
        return 2

    bad = []
    for c in cases:
        out = EV / c["out"]
        print(f"\n===== 段 {c['tag']}：{c['out']} =====")
        if not a.verify_only:
            print(f"  命令: {c['cmd']}")
            r = rd.record(c["title"], c["cmd"], out, c["seconds"])
            if r.get("rc") != 0:
                bad.append((c["tag"], f"录制失败 rc={r.get('rc')} {r.get('stderr')}"))
                print(f"  ❌ 录制失败：{r}")
                continue
            print(f"  录制完成：{r['rect'][2]}x{r['rect'][3]} 窗口在 {r['rect'][:2]} "
                  f"抢焦点={r['stole_focus']} 命令跑完于 {r['finished_after']}s "
                  f"（总 {r['elapsed']}s）")
        ok, why = verify(c)
        if not ok:
            bad.append((c["tag"], why))
            reject(c, why)

    print("\n" + "=" * 48)
    if bad:
        print(f"录屏判定：FAIL（{len(bad)} 段）—— 废片已挪进 {REJECTED.relative_to(ROOT)}/")
        for t, w in bad:
            print(f"   - {t}: {w}")
        return 1
    print("录屏判定：PASS —— 三段素材的屏幕内容都逐字对上了承诺数字")
    return 0


if __name__ == "__main__":
    sys.exit(main())