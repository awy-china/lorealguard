"""录屏工具的守卫测试（`tools/record_demo.py`）。

为什么值得写：这个工具第一次跑出来的 mp4 **帧是纯色**、第二版屏幕上打印
"此时不应有 i。"——两次都是"命令跑过但产物是错的"。这里把踩过的坑钉死：
  1) GDI 抓屏抓 Windows Terminal（CASCADIA_HOSTING_WINDOW_CLASS）只能得到黑帧
     → 必须走 ddagrab（DXGI）+ hwdownload；
  2) 命令文本写进 .bat 必须做两套转义：显示行转义元字符、可执行行把 %i 变 %%i；
  3) .bat 必须纯 ASCII（项目铁律）；
  4) 录完必须关掉自己开的窗口，不留残窗。
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import record_demo as rd  # noqa: E402

# 被扫的源码路径（变异测试会 monkeypatch 它，用来证明这些守卫会变红）
SRC_PATH = ROOT / "tools" / "record_demo.py"


def _src() -> str:
    return SRC_PATH.read_text(encoding="utf-8")


# ---------- 转义（纯函数，能变红） ----------

def test_esc_display_defuses_cmd_metachars():
    """显示行：& | < > ( ) ^ " 都要被转义，否则 cmd 会把命令拆碎。"""
    raw = r'for /l %i in (1,1,3) do (echo a & echo b >nul | find "x")'
    out = rd._esc_display(raw)
    for ch in ("&", "|", "<", ">"):
        # 每个元字符的裸出现都必须前有 ^
        for m in re.finditer(re.escape(ch), out):
            assert m.start() > 0 and out[m.start() - 1] == "^", (ch, out)
    assert "^(" in out and "^)" in out


def test_esc_run_doubles_percent_for_bat():
    """可执行行：批处理里 for 循环变量必须 %%i，写 %i 会打印"此时不应有 i。"。"""
    assert rd._esc_run("for /l %i in (1,1,3) do echo hi") == \
        "for /l %%i in (1,1,3) do echo hi"
    # 已双写的不要二次转义
    assert rd._esc_run("for /l %%i in (1,1,3) do echo %%~nxi").count("%%") == 2


def test_esc_run_leaves_env_percent_alone_when_already_escaped():
    """`%PATH%` 这种环境变量在 bat 里单写是对的，不该被再翻倍成歧义。"""
    out = rd._esc_run("echo %PATH%")
    assert out.startswith("echo ")


# ---------- .bat 落地（纯文件写，可测） ----------

def test_write_bat_is_pure_ascii(tmp_path):
    bat = rd._write_bat(tmp_path / "_rec_T.bat", "T", "echo hello")
    raw = bat.read_bytes()
    assert max(raw) < 128, "bat 必须纯 ASCII（非 ASCII 会在不同代码页下乱码）"
    assert b"\r\n" in raw, "必须 CRLF（LF 结尾的 bat 在 cmd 下可能错行）"
    assert b"\r\r" not in raw, "禁止 CR CR LF：write_text 的换行转换会在 \\r\\n 上再插一个 \\r"


def test_write_bat_keeps_title_and_keeps_window_open(tmp_path):
    bat = rd._write_bat(tmp_path / "_rec_X.bat", "MYTITLE", "python x.py")
    txt = bat.read_text(encoding="ascii")
    assert [ln.strip() for ln in txt.splitlines()][:2] == ["@echo off", "title MYTITLE"]
    assert txt.strip().endswith("cmd /k"), "末尾要 cmd /k，否则命令跑完窗口关闭、录不到末屏"


def test_write_bat_does_not_split_command_line(tmp_path):
    """含 & 的命令：显示行必须转义，否则屏幕上会出现 '此时不应有 ----。'"""
    bat = rd._write_bat(tmp_path / "_rec_Y.bat", "Y", "echo a & echo b")
    lines = bat.read_text(encoding="ascii").splitlines()
    disp = [ln for ln in lines if ln.startswith("echo $ ")][0]
    assert "^&" in disp


# ---------- 抓屏方式（防回归：不许退回 GDI） ----------

def test_uses_dxgi_not_gdi():
    """源码里必须用 ddagrab（DXGI）。GDI 抓屏对 Windows Terminal 出黑帧。"""
    src = _src()
    assert "ddagrab" in src and "hwdownload" in src
    # 不许再有 GDI 抓屏 / 按窗口标题抓的输入写法（那是出黑帧的那一版）。
    # 注：这条守卫最初只挡 `-i title=` 字面，被变异测试抓到挡不住
    #     `gdigrab` + `f"title={title}"` 的真回归 → 已按语义加强。
    assert "gdigrab" not in src, "退回 gdigrab 抓屏：对 DirectX 渲染的窗口只能得到黑帧"
    assert 'f"title=' not in src, "不许再用按标题抓窗的输入写法"


def test_kills_own_window_and_measures_focus():
    """录完必须 taskkill 自己开的窗；且焦点是否被抢必须是**量出来的**，不是声明的。"""
    src = _src()
    assert "_kill_window(title)" in src
    assert "GetForegroundWindow" in src and "stole_focus" in src


def test_smoke_text_cannot_be_mistaken_for_real_numbers():
    """自检片里的文字必须自证是假的，防止有人把 smoke 帧当成真结果。"""
    assert "NOT A REAL RESULT" in rd.SMOKE_TEXT


@pytest.mark.parametrize("needle", ["1730", "924"])
def test_no_hardcoded_smoke_pixels(needle):
    """抓取区域必须是量出来的（GetClientRect），不许把某次实测的像素写死。"""
    src = _src()
    assert needle not in src