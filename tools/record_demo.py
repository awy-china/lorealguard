r"""录屏工具：把我们自己开出来的 cmd 窗口录成 mp4（走 DXGI，不是 GDI）。

为什么不用整屏录制：整屏会把用户桌面上其它窗口（微信/浏览器）一起拍进去。
本工具做法：开一个标题已知的 cmd 窗口 → 量出它的客户区物理像素矩形 →
用 **ddagrab（DXGI Desktop Duplication）的 offset_x/offset_y/video_size 只抓这块区域**。
（GDI 抓屏对本机托管 cmd 的 Windows Terminal 只能得到纯色黑帧——实测过。）
  - 中间帧与成品里都**不含桌面其它区域**
  - ⚠️ **会抢前台焦点**：Windows 新建窗口的默认行为，实测如此；
    本工具不假装能避免，而是把"有没有抢"**量出来**打在输出里（stole_focus）
  - 录完一定 taskkill 掉自己开的窗口，不留残窗

用法：
  # 自检管线（拍 8 秒假数据，帧里出现 SMOKE 字样，绝不可能是真数字）
  python tools/record_demo.py --smoke

  # 真用：录 A/B/C 三条（seconds 是上限，命令跑完即收尾）
  python tools/record_demo.py --title LGUARD_REC_A --seconds 120 \
      --cmd "cd /d D:\LorealGuard && .venv\Scripts\python.exe experiments\p2_bench.py" \
      --out output/video_evidence/recA.mp4

⚠️ 命令里**不要**写 `set PYTHONPATH=`：cmd 里它留下一个空值 PYTHONPATH（≠取消），
   Python 启动就 `Fatal Python error: error evaluating path`。Git-Bash 的
   `env -u PYTHONPATH` 是另一回事（那是真取消）。venv 里的 python 不设也能跑。
   ⚠️ .bat 以 ASCII 写盘，命令里**不能有中文**（会被 replace 成 ?）。

判定通过的标准（脚本自己断言，不靠人看）：
  1) 输出 mp4 存在且 ffprobe 能读出时长/帧数
  2) 抽第 1 帧 + 中段帧，两帧都不是纯色（std > 3）
  3) 帧尺寸与量出来的客户区矩形一致
  4) 见到"命令跑完"标题标记才收尾（否则录到的可能不是末尾那一屏结果）
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
SMOKE_TEXT = "SMOKE TEST ONLY - NOT A REAL RESULT"
NO_WIN = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
# 命令跑完后把窗口标题改成 "<title> DONE"：给录制端一个**确定性的"跑完了"信号**。
# 标题栏在客户区之外，标记不会进成片。用固定秒数猜"跑完了没有"已被证明不靠谱
# （同一个 p2_bench：无争抢 13s，录屏时 >43s 还没跑完 → 汇总块根本没进画面）。
DONE_SUFFIX = " DONE"

#: 录制期间每隔这么久把被录窗口**重新压回最上层**一次（理由见 `_re_topmost`）。
#: 2026-09-22 实测：2.0s 太稀 —— 30s 那一镜里别人的窗口照样挤进来（探针点到了名字），
#: 而 10–20s 的短镜全过。收成 0.5s：SETWINDOWPOS 本身是廉价的窗口管理器调用。
KEEP_TOPMOST_EVERY = 0.5


def _dpi_aware() -> None:
    """声明 DPI 感知：否则在 125% 缩放下 SetWindowPos 的坐标会被系统虚拟化，
    窗口实际尺寸与我们要求的 1600x900 不一致。"""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _dpi_awareness() -> int:
    """本进程的 DPI 感知级别：0=UNAWARE（危险）1=SYSTEM 2=PER-MONITOR，-1=查不到。

    ⚠️ 2026-09-20 事故（最隐蔽的一个）：本机显示缩放 150%（逻辑 1707x1067 / 物理 2560x1600）。
    UNAWARE 进程拿到的 GetClientRect/ClientToScreen 是**逻辑**坐标，而 ddagrab 抓的是
    **物理**像素 → 抓到的区域偏左上、且比真窗口小：画面里混进别的窗口、
    窗口下半部分（正是结果汇总）被切掉。看着像"渲染卡住"，其实是坐标系差 1.5 倍。
    所以 record() 必须先声明感知级别，并在这里**硬闸门**：UNAWARE 直接拒绝录制。
    """
    if os.name != "nt":
        return 2
    v = ctypes.c_int(-1)
    try:
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(v))
    except Exception:
        return -1
    return v.value


def _u32():
    """user32 句柄安全封装：**必须**声明 argtypes/restype。

    ⚠️ 2026-09-20 事故：不声明时 ctypes 把 HWND 按 c_int 传，句柄高位被截断，
    GetClientRect/ClientToScreen 静默失败 → ClientToScreen 不写回 pt，量出来的
    矩形退回屏幕左上角 (0,0,1152,616)：录到的画面里**混进了别的窗口**、
    客户区宽度也不是真窗口的。修复后矩形必须够大且落在屏幕内，否则 record()
    直接拒绝录制 —— 宁可失败，也不要录一段"看着像证据"的废片。
    """
    u = ctypes.windll.user32
    HWND, RECT, POINT = wt.HWND, wt.RECT, wt.POINT
    u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
    u.FindWindowW.restype = HWND
    u.GetClientRect.argtypes = [HWND, ctypes.POINTER(RECT)]
    u.GetClientRect.restype = wt.BOOL
    u.ClientToScreen.argtypes = [HWND, ctypes.POINTER(POINT)]
    u.ClientToScreen.restype = wt.BOOL
    u.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.SetWindowPos.restype = wt.BOOL
    u.ShowWindow.argtypes = [HWND, ctypes.c_int]
    u.ShowWindow.restype = wt.BOOL
    u.GetForegroundWindow.restype = HWND
    u.IsWindow.argtypes = [HWND]
    u.IsWindow.restype = wt.BOOL
    return u


def _live_window(title: str, timeout: float = 12.0):
    """轮询找到我们开的窗口句柄。找不到返回 None。"""
    if os.name != "nt":
        return None
    u = _u32()
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = u.FindWindowW(None, title)
        if h:
            return h
        time.sleep(0.25)
    return None


def _show_no_activate(hwnd, w: int = 0, h: int = 0, topmost: bool = True) -> None:
    """把窗口显示出来但**不激活**（不抢用户键盘焦点），可选置顶以免被遮挡。

    为什么要置顶：DXGI 抓的是屏幕合成后的画面，被别的窗口压住的区域会录成别的内容，
    被别的窗口盖住的像素会录成黑块 —— 录屏内容必须完整可见。
    置顶只影响画面层级，不转移输入焦点。
    """
    u = _u32()
    SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    u.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
    if w and h:
        u.SetWindowPos(hwnd, HWND_TOPMOST if topmost else 0, 60, 60, w, h,
                       SWP_NOACTIVATE | SWP_SHOWWINDOW)
    else:
        u.SetWindowPos(hwnd, HWND_TOPMOST if topmost else 0, 60, 60, 0, 0,
                       SWP_NOACTIVATE | SWP_SHOWWINDOW | 0x0001)


def _un_topmost(hwnd) -> None:
    u = _u32()
    u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0010 | 0x0001)  # NOTOPMOST, NOACTIVATE


def _re_topmost(hwnd) -> None:
    """把窗口重新推到最上层：**不动位置、不动大小**，也不抢焦点。

    为什么不顺手再调一次 `_show_no_activate`：那会重新 SetWindowPos 到
    (60,60,1600,900)，客户区万一差一个像素，ffmpeg 的抓取矩形（开录前量好写死的）
    就和窗口错开了。

    为什么置顶要**一直在**、而不是开场压一次就完事：ddagrab 抓的是屏幕合成后的画面，
    被录窗口一旦被别的窗口盖住，录到的就是别人的界面 —— 而产物照样"文件在、非黑帧、
    时长合格"，只有抽帧逐字读回才发现得了。实测（2026-09-21，镜 D3）：同一轮里
    D1/D2 只有 11s 没事，D3 录 23s，片尾那一帧整幅成了编辑器/聊天窗。而**片尾那一帧
    正是复核抽的那一帧**（`dur-0.4s`）→ 收尾留帧的那几秒尤其不能松手。
    """
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
    HWND_TOPMOST = -1
    _u32().SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)


def _top_window_at(x: int, y: int) -> tuple:
    """抓取矩形上某一点**当前看得见**的顶层窗口 → (hwnd, title)。

    用来回答"这一镜到底被谁盖住了"。D3 那次只知道录出来的是别人的界面，既不知道
    是谁的、也不知道是全程盖着还是只盖了片尾 —— 量出来才有得说。
    (`GetAncestor(GA_ROOT)`：屏幕上一点拿到的是渲染子窗口，要比对的是顶层窗口。)
    返回 (0, "") 表示那一点上没有窗口，或不在 Windows 上。
    """
    if os.name != "nt":
        return (0, "")
    u = _u32()
    u.WindowFromPoint.argtypes = [wt.POINT]
    u.WindowFromPoint.restype = wt.HWND
    u.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
    u.GetAncestor.restype = wt.HWND
    u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    h = u.WindowFromPoint(wt.POINT(x, y))
    if not h:
        return (0, "")
    root = u.GetAncestor(h, 2) or h          # 2 = GA_ROOT
    buf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(root, buf, 512)
    return (root, buf.value)


def _kill_window(title: str) -> None:
    subprocess.run(
        # ⚠️ 通配匹配：命令跑完后 _write_bat 会把标题改成 "<title> DONE"，
        # 精确匹配 `eq {title}` 就杀不掉那个窗口了（残窗会挡住后面的录制）。
        ["taskkill", "/FI", f"WINDOWTITLE eq {title}*", "/F"],
        capture_output=True, creationflags=NO_WIN,
    )


def _enum_titles(needle: str = "LGUARD") -> list:
    """枚举当前顶层窗口标题（排错用）。"""
    if os.name != "nt":
        return []
    u = _u32()
    found = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    buf = ctypes.create_unicode_buffer(512)

    def cb(hwnd, _):
        u.GetWindowTextW(hwnd, buf, 512)
        t = buf.value
        if t and needle.lower() in t.lower():
            found.append(t)
        return True

    u.EnumWindows(CB(cb), None)
    return found


def _client_rect_on_screen(hwnd):
    """窗口客户区在屏幕上的物理像素矩形 → (x, y, w, h)，都取偶数（libx264 要求）。"""
    u = _u32()
    cr = wt.RECT()
    if not u.GetClientRect(hwnd, ctypes.byref(cr)):
        raise RuntimeError("GetClientRect 失败（句柄签名没声明？）")
    pt = wt.POINT(0, 0)
    if not u.ClientToScreen(hwnd, ctypes.byref(pt)):
        raise RuntimeError("ClientToScreen 失败（句柄签名没声明？）")
    x, y = int(pt.x), int(pt.y)
    w, h = int(cr.right), int(cr.bottom)
    return (x - x % 2, y - y % 2, w - w % 2, h - h % 2)


def _esc_display(s: str) -> str:
    """把命令文本安全地放进 `echo $ ...` 显示行：转义 cmd 的元字符。

    不转义会怎样：这行里的 `&` 会被 cmd 当命令分隔符 → 后面的 for 块被拆碎，
    屏幕上打印 "此时不应有 ----。"（本工具就是这么被抓出来的）。
    """
    for a, b in (("^", "^^"), ("&", "^&"), ("<", "^<"), (">", "^>"),
                 ("|", "^|"), ("(", "^("), (")", "^)"), ('"', '""')):
        s = s.replace(a, b)
    s = s.replace("%", "%%")
    return s


def _esc_run(s: str) -> str:
    """把命令文本放进 .bat 的**可执行行**：批处理里 %i 必须写 %%i（for 循环变量）。"""
    import re
    return re.sub(r"%(?!%)", "%%", s)


def _ddagrab_cmd(x: int, y: int, cw: int, ch: int, fps: int, seconds: int, out: Path) -> list:
    """ffmpeg 命令行：DXGI 只抓 (x,y,cw,ch) 这一块。抽出来是为了让两处录制共用同一份参数
    （区域抓屏的三个坑：GDI 对 Windows Terminal 只出黑帧、DPI 坐标系、区域必须只含本窗口）。"""
    return [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"ddagrab=output_idx=0:framerate={fps}:draw_mouse=0:"
                  f"offset_x={x}:offset_y={y}:video_size={cw}x{ch}",
            "-t", str(seconds),
            # ddagrab 出的是 d3d11 硬件帧，必须先下载到内存再编码
            "-vf", "hwdownload,format=bgra",
            "-pix_fmt", "yuv420p", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "20", str(out)]


def _write_bat(bat: Path, title: str, inner_cmd: str) -> Path:
    """写录制用 .bat（纯 ASCII，项目铁律）。提成独立函数是为了能被测试断言。"""
    bat.parent.mkdir(parents=True, exist_ok=True)
    bat.write_text(
        "@echo off\r\n"
        f"title {title}\r\n"
        "color 0F\r\n"
        "cls\r\n"
        "echo $ " + _esc_display(inner_cmd) + "\r\n"
        "echo.\r\n"
        f"{_esc_run(inner_cmd)}\r\n"
        # 命令跑完 → 改标题当作"跑完了"的确定信号（标题栏在客户区外，不进成片）
        f"title {title}{DONE_SUFFIX}\r\n"
        "echo.\r\n"
        "echo ---- command finished (window kept to record the last screen) ----\r\n"
        "cmd /k\r\n",
        encoding="ascii", errors="replace",
        # newline="" 关掉 Windows 的 \n→\r\n 自动转换；
        # 不关的话我们写的 \r\n 会变成 \r\r\n（CR CR LF），cmd 解析可能错行
        newline="",
    )
    return bat


def record(title: str, inner_cmd: str, out: Path, seconds: int = 120, fps: int = 15,
           size=(1600, 900), topmost: bool = True, tail: float = 6.0) -> dict:
    """录一段 mp4，并在**命令真的跑完**之后再收尾。

    seconds 是**上限**（防命令卡死时一直录），不是"录多久"。
    判"跑完了没有"不能靠固定秒数：同一个 p2_bench.py 单独跑 13s，
    录屏时 CPU 被 libx264 分走 → 43s 还没跑完，汇总块根本没进画面（实测事故）。
    现在用 _write_bat 埋的标题标记 "<title> DONE" 当信号，见到后多录 tail 秒。
    """
    # ⚠️ 必须在量矩形**之前**：本机缩放 150%，UNAWARE 进程量到的是逻辑坐标，
    # 与 ddagrab 抓的物理像素差 1.5 倍 → 区域偏左上且偏小（实测事故，见 _dpi_awareness）。
    _dpi_aware()
    aw = _dpi_awareness()
    if aw == 0:
        return {"rc": -3, "path": str(out), "dpi_awareness": aw,
                "stderr": "进程 DPI 感知=UNAWARE：量出的矩形与物理像素不在同一坐标系，拒绝录制"}
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    _kill_window(title)  # 清掉可能残留的同名窗口
    # 走 .bat 落地：避免 cmd 的 & / 引号 解析把命令拆碎；标题在 bat 第一行就设好
    bat = out.parent / f"_rec_{title}.bat"
    _write_bat(bat, title, inner_cmd)
    t0 = time.time()
    subprocess.Popen(["cmd", "/c", "start", "", str(bat)], creationflags=NO_WIN)
    hwnd = _live_window(title)
    if not hwnd:
        _kill_window(title)
        return {"rc": -1, "path": str(out),
                "stderr": f"找不到窗口 '{title}'；现有含 LGUARD 的窗口标题={_enum_titles()}"}
    w, h = (size if size else (0, 0))
    _show_no_activate(hwnd, w, h, topmost=topmost)  # 显示但不激活
    time.sleep(1.5)  # 等首屏输出稳定
    # 实测焦点有没有被抢走（不靠声明，靠量）
    stole_focus = _u32().GetForegroundWindow() == hwnd
    x, y, cw, ch = _client_rect_on_screen(hwnd)
    # 矩形闸门：句柄签名没声明时 ClientToScreen 会静默失败、矩形退回屏幕左上角，
    # 录出来的"证据"里混着别的窗口。宁可失败，也不要留一段看着像证据的废片。
    if cw < 320 or ch < 240:
        _un_topmost(hwnd)
        _kill_window(title)
        return {"rc": -2, "path": str(out), "rect": (x, y, cw, ch),
                "stderr": f"客户区矩形不可信 rect={(x, y, cw, ch)}，拒绝录制"}
    # ddagrab：DXGI 区域抓屏。用 offset+video_size 直接只抓这块区域，
    # 中间帧里**不含桌面其它部分**（GDI 抓屏对 Windows Terminal 只能得到黑帧）。
    rec = subprocess.Popen(
        _ddagrab_cmd(x, y, cw, ch, fps, seconds, out),
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=NO_WIN,
    )
    finished_after = None
    while rec.poll() is None:
        if finished_after is None and _u32().FindWindowW(None, title + DONE_SUFFIX):
            finished_after = round(time.time() - t0, 2)
            time.sleep(tail)  # 让"最后一屏"（结果汇总）留在片尾
            try:
                rec.stdin.write(b"q")  # 优雅收尾：写 q 让 x264 正常写 moov
                rec.stdin.flush()
            except Exception:
                pass
        time.sleep(0.25)
    try:
        _, err = rec.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        rec.kill()
        _, err = rec.communicate()
    _un_topmost(hwnd)
    _kill_window(title)  # 不留残窗
    return {"rc": rec.returncode, "path": str(out), "hwnd": hwnd,
            "stderr": (err or b"").decode("utf-8", "replace").strip()[:400],
            "rect": (x, y, cw, ch), "stole_focus": stole_focus,
            "finished_after": finished_after, "elapsed": round(time.time() - t0, 2)}


def _done_seen(target: Path, t0: float) -> bool:
    """"跑完了"的信号：**收尾产物**被写出来（mtime 晚于开录时刻）。

    target 可以带通配符（演示页的产物文件名带内容哈希，事先不知道 tag，
    所以写成 `output/demo/*_report.json`）。只看 mtime，不猜文件名。

    ⚠️ 刻意**不**支持"目录里有任何新文件就算跑完" —— 2026-09-21 拿探针实测过：
    演示页的证据图在 +1.0s 就落盘，报告要到 +18.6s 才落（中间那段在网络里跑 F3）。
    拿目录当信号 → 1.5s 就收尾，片尾停在页面顶部的**表单**上（没滚到账本），
    而且会把**上一镜**留下的报告当成这一镜的产物去生成期望值。
    """
    try:
        pat = target.name
        if any(ch in pat for ch in "*?["):
            return any(p.is_file() and p.stat().st_mtime >= t0
                       for p in target.parent.glob(pat))
        return target.is_file() and target.stat().st_mtime >= t0
    except OSError:
        return False


def record_window(title: str, out: Path, seconds: int = 120, *, fps: int = 15,
                  tail: float = 6.0, done_target: Path | None = None,
                  size=None, topmost: bool = True, t0: float | None = None) -> dict:
    """录一个**已经在跑的**窗口（浏览器、播放器…），按窗口标题找它。

    与 `record()` 的区别只有两点：
      1) 窗口不是我们开的 —— 不写 .bat、不 `start`、不改标题（标题是别人的，改了会打脸）；
      2) "跑完了没有"的信号不是标题标记，而是 **`done_target` 指名的收尾产物**
         被写出来（mtime 晚于开录时刻，见 `_done_seen`）。页面跑完最后一步才落
         `output/demo/<tag>_report.json`，它的出现即"这一跑结束"的确定信号，
         **不需要页面为了录制改任何行为**；`done_target` 要写成通配
         （`output/demo/*_report.json`），不能写成目录 —— 理由见 `_done_seen`。
    返回体里带 `t0`（开录时刻），调用方用它挑"**本次运行**写出的产物"。
    共同的坑一个都不少：DPI 感知、矩形闸门、区域抓屏、跑完后再录 tail 秒。
    """
    _dpi_aware()
    aw = _dpi_awareness()
    if aw == 0:
        return {"rc": -3, "path": str(out), "dpi_awareness": aw,
                "stderr": "进程 DPI 感知=UNAWARE：量出的矩形与物理像素不在同一坐标系，拒绝录制"}
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    # ⚠️ t0 必须在**找到窗口之前**取：被录的窗口（浏览器）是叫我们之前就开好的，
    # 页面自己会在这几百毫秒里跑完并落产物 —— t0 取晚了，产物 mtime 就永远 < t0，
    # "跑完了"这个信号一次都不会触发（实测踩过：录满 91s 上限才收尾）。
    # 调用方还能**再往前**取（`t0=`）：页面是它自己开的，从"开页那一刻"起算才不会
    # 把"开页到本函数被调用"这段漏在钟外（`record_demo_page.py` 就是这么传的）。
    t0 = time.time() if t0 is None else t0
    hwnd = _live_window(title)
    if not hwnd:
        return {"rc": -1, "path": str(out),
                "stderr": f"找不到窗口 '{title}'；现有含 LGUARD 的窗口标题={_enum_titles()}"}
    _show_no_activate(hwnd, *(size or (0, 0)), topmost=topmost)
    time.sleep(1.0)
    stole_focus = _u32().GetForegroundWindow() == hwnd
    x, y, cw, ch = _client_rect_on_screen(hwnd)
    if cw < 320 or ch < 240:
        _un_topmost(hwnd)
        return {"rc": -2, "path": str(out), "rect": (x, y, cw, ch),
                "stderr": f"客户区矩形不可信 rect={(x, y, cw, ch)}，拒绝录制"}
    rec = subprocess.Popen(
        _ddagrab_cmd(x, y, cw, ch, fps, seconds, out),
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=NO_WIN,
    )
    finished_after = None
    covered_by: list = []            # (时刻, 标题)：抓取区域被谁盖过（见 _top_window_at）
    covered_at_end: list = []        # 片尾留帧那几秒里被谁盖住 —— 复核抽的就是那几帧
    tail_started = False
    next_pin = time.time() + KEEP_TOPMOST_EVERY
    while rec.poll() is None:
        # 置顶要**一直在**：被录窗口被盖住 = 录到别人的界面，而产物照样"文件在、
        # 非黑帧、时长合格"（2026-09-21 镜 D3 就是这么废的，见 `_re_topmost`）。
        if topmost and time.time() >= next_pin:
            _re_topmost(hwnd)
            next_pin = time.time() + KEEP_TOPMOST_EVERY
        h_here, t_here = _top_window_at(x + cw // 2, y + ch // 2)
        # 句柄比对 + 标题比对：同标题的另一个顶层句柄也算我们自己（Edge 的 --app
        # 窗口在不同 DPI/合成路径下未必与 FindWindowW 给出同一个句柄，别把自家人
        # 报成"盖住我们的窗口"）。
        if h_here and h_here != hwnd and t_here != title:
            name = t_here or f"<无标题 {h_here}>"
            if not any(n == name for _, n in covered_by):
                covered_by.append((round(time.time() - t0, 2), name))
            if tail_started and name not in covered_at_end:
                covered_at_end.append(name)
        if finished_after is None and done_target is not None:
            if _done_seen(Path(done_target), t0):
                finished_after = round(time.time() - t0, 2)
                # 让"结果那一屏"留在片尾。这一段也照样要一直压顶 ——
                # **片尾那一帧正是抽帧复核的那一帧**（dur-0.4s），原来这里是一个
                # 6 秒的 time.sleep，等于把复核帧整段裸奔在别人的窗口下面。
                tail_started = True
                deadline = time.time() + tail
                while time.time() < deadline:
                    _re_topmost(hwnd)
                    time.sleep(0.25)
                try:
                    rec.stdin.write(b"q")   # 优雅收尾：写 q 让 x264 正常写 moov
                    rec.stdin.flush()
                except Exception:
                    pass
        time.sleep(0.25)
    try:
        _, err = rec.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        rec.kill()
        _, err = rec.communicate()
    _un_topmost(hwnd)
    return {"rc": rec.returncode, "path": str(out), "hwnd": hwnd,
            "stderr": (err or b"").decode("utf-8", "replace").strip()[:400],
            "rect": (x, y, cw, ch), "stole_focus": stole_focus,
            "covered_by": covered_by, "covered_at_end": covered_at_end,
            "t0": t0, "finished_after": finished_after,
            "elapsed": round(time.time() - t0, 2)}


def probe(path: Path) -> dict:
    p = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,nb_frames,avg_frame_rate",
         "-show_entries", "format=duration,size", "-of", "default=nw=1", str(path)],
        capture_output=True, text=True, creationflags=NO_WIN,
        # 钉 UTF-8 与上面两处 ffmpeg 抽帧同族（docs/DEBT.md D-07），但**这一行是预防性的**：
        # 2026-09-22 实测 ffprobe 把「打不开的路径」写进 **stderr**（按 locale 解会崩 → `stderr=None`），
        # 而本函数只读 stdout，`-of default=nw=1` 吐的全是数字 ⇒ 钉不钉都返回 `{}`，
        # **行为上惰性**，因此没有（也写不出）会变红的守卫。留着只为消掉读线程崩溃的噪音警告，
        # 以及防住以后有人往 `-show_entries` 里加会回显文件名的字段。
        encoding="utf-8", errors="replace",
    )
    d = {}
    for line in p.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v
    return d


def frame_std(path: Path, ts: float) -> float:
    png = path.with_suffix(f".t{ts}.png")
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", str(ts),
         "-i", str(path), "-frames:v", "1", str(png)],
        capture_output=True, creationflags=NO_WIN,
    )
    if not png.exists():
        return -1.0
    try:
        from PIL import Image, ImageStat
        st = ImageStat.Stat(Image.open(png).convert("L"))
        return round(st.stddev[0], 2)
    finally:
        png.unlink(missing_ok=True)


def main() -> int:
    _dpi_aware()
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="LGUARD_REC_SMOKE")
    ap.add_argument("--cmd", default="")
    ap.add_argument("--out", default="output/video_evidence/_smoke_capture.mp4")
    ap.add_argument("--seconds", type=int, default=30,
                    help="录制**上限**秒数（兜底）；正常在命令打印结束标记后 tail 秒收尾")
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()

    out = Path(a.out)
    cmd = a.cmd
    if a.smoke:
        # 逐行吐字，模拟跑分刷屏；文字明确写着"这不是真结果"
        # ⚠️ inner_cmd 会被写进 .bat：这里写单 %i，交给 _esc_run 转成 %%i
        #（批处理里 for 的循环变量必须 %%；写错会得到 cmd 的 "此时不应有 i。"，
        #  命令行跑起来看不出问题，靠抽帧 + VL 逐字读才抓到）
        cmd = (f"echo {SMOKE_TEXT} & echo. & "
               "for /l %i in (1,1,10) do @(echo line %i  =  0.0pct  PASS & ping -n 2 127.0.0.1 >nul) & "
               "echo. & echo SMOKE DONE")
        a.title = "LGUARD_REC_SMOKE"
        out = Path("output/video_evidence/_smoke_capture.mp4")

    print(f"[1/4] 录制窗口 '{a.title}'（上限 {a.seconds}s，跑完再录 6s 收尾）-> {out}")
    r = record(a.title, cmd, out, a.seconds)
    if r["rc"] != 0 or not out.exists():
        print(f"[FAIL] ffmpeg rc={r['rc']} {r['stderr']}")
        return 2
    print(f"      命令跑完时刻：{r['finished_after']}s（标题标记 "
          f"'{a.title} DONE'；None=没等到，按上限截断）")
    print(f"[2/4] ffmpeg rc=0, 文件 {out.stat().st_size/1024:.0f} KB；"
          f"抓取区域 rect={r.get('rect')}（客户区物理像素）")
    print(f"      焦点实测：{'⚠️ 抢到了前台焦点（Windows 新建窗口的默认行为）' if r.get('stole_focus') else '✅ 未抢焦点'}")
    info = probe(out)
    print(f"[3/4] ffprobe: {info}")
    fr = float(info.get("avg_frame_rate", "0/1").split("/")[0])
    dur = float(info.get("duration", 0) or 0)
    # 时长判据：不再拿"要求 N 秒"卡（收尾由结束标记决定），只卡下界 3s 与上限
    ok_dur = 3.0 <= dur <= a.seconds + 2
    # 抽两帧判非纯色
    s1, s2 = frame_std(out, 0.5), frame_std(out, max(1.0, dur * 0.5))
    ok_px = s1 > 3 and s2 > 3
    print(f"[4/4] 抽帧灰度 std: 0.5s={s1} 中段={s2} → {'非纯色 ✅' if ok_px else '纯色/空帧 ❌'}")
    print(f"      时长 {dur:.1f}s (要求 3s≤时长≤上限+2s) → {'✅' if ok_dur else '❌'}")
    if a.smoke:
        # 结束标记必须真被识别到：否则"录到了一段画面"并不能证明"录到了末尾那一屏"
        ok_fin = r.get("finished_after") is not None
        print(f"      结束标记识别：{'✅ 是' if ok_fin else '❌ 否'}")
        print(f"\n结论：录屏管线 {'可用 ✅' if (ok_dur and ok_px and ok_fin) else '不可用 ❌'}；"
              f"帧尺寸 {info.get('width')}x{info.get('height')}")
        return 0 if (ok_dur and ok_px and ok_fin) else 3
    return 0 if (ok_dur and ok_px) else 3


if __name__ == "__main__":
    sys.exit(main())