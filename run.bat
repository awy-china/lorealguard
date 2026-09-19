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
echo [1/5] regression tests (pytest, canonical command)
".venv\Scripts\python.exe" -m pytest tests --durations=3 -rf
echo.
echo [2/5] P2 image-channel benchmark (FPR / bounds / promises / IoU)
".venv\Scripts\python.exe" experiments\p2_bench.py
echo.
echo [3/5] P2-T text-channel benchmark (paired: same image, two copies)
if not exist "output\p2\text_pairs.json" ".venv\Scripts\python.exe" samples\make_p2_text_pairs.py
".venv\Scripts\python.exe" experiments\p2_text_bench.py
echo.
echo [4/5] submission pack (official format: per-sample folder + zip, byte-reproducible)
".venv\Scripts\python.exe" tools\make_submission_pack.py
echo.
echo [5/5] syntax compile (voiceguard tests experiments samples tools)
".venv\Scripts\python.exe" -m compileall -q voiceguard tests experiments samples tools
echo.
echo image report : %~dp0output\p2\bench_report.md
echo text  report : %~dp0output\p2\text_report.md
echo submission   : %~dp0dist\submission_pack\lorealguard_testset_v1.zip
echo verify       : %~dp0output\f4\_verify.txt
pause