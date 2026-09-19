@echo off
rem LorealGuard acceptance launcher (offline, no API calls, no network)
rem ASCII only on purpose: cmd.exe parses .bat as ANSI; Chinese literals break it.
rem Canonical: run this instead of typing pytest by hand.
rem   DO NOT append -q to pytest -- pytest.ini addopts already has one,
rem   and a second -q swallows the "N passed" summary line (bit us 2026-09-19).
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH="
set "PYTHONIOENCODING=utf-8"
echo ============================================================
echo  LorealGuard - acceptance (regression + metrics + compile)
echo ============================================================
echo.
echo [1/3] regression tests (pytest, canonical command)
".venv\Scripts\python.exe" -m pytest tests --durations=3 -rf
echo.
echo [2/3] P2 benchmark (FPR / confidence bound / promises / IoU)
".venv\Scripts\python.exe" experiments\p2_bench.py
echo.
echo [3/3] syntax compile (voiceguard tests experiments samples)
".venv\Scripts\python.exe" -m compileall -q voiceguard tests experiments samples
echo.
echo report : %~dp0output\p2\bench_report.md
echo verify : %~dp0output\f4\_verify.txt
pause