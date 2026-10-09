"""录屏工具守卫的**变异测试**：证明 `tests/test_record_demo.py` 不是空转。

铁律：一条不会红的检查等于没有检查。这里**故意注入 5 种故障**，断言对应守卫变红。
注入的 5 种故障全是这个工具**真实踩过**的坑，不是编出来的：
  1) 抓屏退回 GDI 按标题抓窗        → 帧是纯色（Windows Terminal 是 DirectX 渲染）
  2) 显示行不转义元字符            → 屏幕上出现 "此时不应有 ----。"
  3) 可执行行不把 %i 翻成 %%i      → 屏幕上出现 "此时不应有 i。"
  4) bat 关掉 newline="" 的写字     → 文件变成 CR CR LF
  5) 自检片文字不带"我不是真结果"   → 有人可能把 smoke 帧当真证据
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_record_demo as trd  # noqa: E402
from tools import record_demo as rd  # noqa: E402


def _turns_red(fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except AssertionError:
        return True
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"守卫不是断言失败，而是崩了：{type(e).__name__}: {e}") from e
    return False


def test_mutation_1_gdi_capture_is_caught(tmp_path, monkeypatch):
    """把抓屏方式换回 gdigrab 按标题抓窗 → 源码守卫必须红。"""
    bad = trd._src().replace(
        '"-f", "lavfi",',
        '"-f", "gdigrab",',
    ).replace(
        '"-i", f"ddagrab=output_idx=0:framerate={fps}:draw_mouse=0:"',
        '"-i", f"title={title}",',
    )
    assert "-i title=" in bad.replace('f"title={title}"', "-i title=") or "gdigrab" in bad
    p = tmp_path / "record_demo.py"
    p.write_text(bad, encoding="utf-8")
    monkeypatch.setattr(trd, "SRC_PATH", p)
    assert _turns_red(trd.test_uses_dxgi_not_gdi), "退回 GDI 抓屏居然没被抓住"


def test_mutation_2_unescaped_display_line_is_caught(monkeypatch):
    """显示行不转义 → 元字符守卫必须红。"""
    monkeypatch.setattr(rd, "_esc_display", lambda s: s)
    assert _turns_red(trd.test_esc_display_defuses_cmd_metachars), \
        "显示行不转义居然没被抓住"


def test_mutation_3_percent_not_doubled_is_caught(monkeypatch):
    """可执行行不把 %i 翻成 %%i → 守卫必须红。"""
    monkeypatch.setattr(rd, "_esc_run", lambda s: s)
    assert _turns_red(trd.test_esc_run_doubles_percent_for_bat), \
        "%i 没翻成 %%i 居然没被抓住"


def test_mutation_4_cr_cr_lf_is_caught(tmp_path, monkeypatch):
    """bat 用默认换行写（Windows 会把 \\n 再转一次 → CR CR LF）→ 守卫必须红。"""
    def bad_write(bat: Path, title: str, inner_cmd: str) -> Path:
        bat.write_text(
            "@echo off\r\n"
            f"title {title}\r\n"
            "cmd /k\r\n",
            encoding="ascii", errors="replace",
        )  # 故意不传 newline=""
        return bat

    monkeypatch.setattr(rd, "_write_bat", bad_write)
    assert _turns_red(trd.test_write_bat_is_pure_ascii, tmp_path), \
        "CR CR LF 居然没被抓住"


def test_mutation_5_smoke_text_without_disclaimer_is_caught(monkeypatch):
    """自检片文字去掉免责 → 守卫必须红。"""
    monkeypatch.setattr(rd, "SMOKE_TEXT", "ALL PASS")
    assert _turns_red(trd.test_smoke_text_cannot_be_mistaken_for_real_numbers), \
        "自检片没说自己是假的，居然没被抓住"


def test_all_five_mutations_were_exercised():
    """元检查：变异注入必须真的是 5 种（防止有人把上面几条注释掉）。"""
    names = [n for n in dir(sys.modules[__name__]) if n.startswith("test_mutation_")]
    assert len(names) == 5, f"变异注入只剩 {len(names)} 种：{names}"