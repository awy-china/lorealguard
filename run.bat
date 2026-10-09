@echo off
rem LorealGuard acceptance launcher (offline, no API calls, no network)
rem ASCII only on purpose: cmd.exe parses .bat as ANSI; Chinese literals break it.
rem Canonical: run this instead of typing pytest by hand.
rem   DO NOT append -q to pytest -- pytest.ini addopts already has one,
rem   and a second -q swallows the "N passed" summary line (bit us 2026-09-19).
rem Step 7 (red team) is OFFLINE by design: it only reads the sealed corpus
rem   samples\red_team\red_team.json. Regenerating that corpus needs the LLM and
rem   is a deliberate one-off (samples\make_red_team.py) -- never inside acceptance.
rem EXIT CODE (2026-09-24): each step records the FIRST failure into RC, and the
rem   script ends with "exit /b %RC%" AFTER pause. Before this the last command
rem   was "pause", so %ERRORLEVEL% was pause's own code -- always 0. The claim
rem   "RUNBAT_EXIT=0, 8 steps all green" was therefore true no matter what the
rem   steps did: a check that cannot go red. Measured: a step exiting 7 still
rem   yielded 0. Two lines now carry the number into the log: ACCEPTANCE for
rem   humans, RUNBAT_EXIT=<n> because the delivery report quotes that exact
rem   token -- it was quoted for weeks while not existing anywhere. A token
rem   people cite must be grep-able in evidence, not remembered.
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH="
set "PYTHONIOENCODING=utf-8"
set "RC=0"
echo ============================================================
echo  LorealGuard - acceptance (regression + metrics + adversarial + compile)
echo ============================================================
echo.
echo [1/8] regression tests (pytest, canonical command)
".venv\Scripts\python.exe" -m pytest tests --durations=3 -rf
if errorlevel 1 if "%RC%"=="0" set "RC=1"
echo.
echo [2/8] P2 image-channel benchmark (FPR / bounds / promises / IoU)
".venv\Scripts\python.exe" experiments\p2_bench.py
if errorlevel 1 if "%RC%"=="0" set "RC=2"
echo.
echo [3/8] P2-T text-channel benchmark (paired: same image, two copies)
if not exist "output\p2\text_pairs.json" ".venv\Scripts\python.exe" samples\make_p2_text_pairs.py
".venv\Scripts\python.exe" experiments\p2_text_bench.py
if errorlevel 1 if "%RC%"=="0" set "RC=3"
echo.
echo [4/8] P5 comment-channel benchmark (F5: paired threads, FPR upper / detect lower / ablation)
if not exist "output\p5\comment_pairs.json" ".venv\Scripts\python.exe" samples\make_p5_comment_pairs.py
".venv\Scripts\python.exe" experiments\p5_comment_bench.py
if errorlevel 1 if "%RC%"=="0" set "RC=4"
echo.
echo [5/8] submission pack (official format: per-sample folder + zip, byte-reproducible)
".venv\Scripts\python.exe" tools\make_submission_pack.py
if errorlevel 1 if "%RC%"=="0" set "RC=5"
echo.
echo [6/8] three-scenario closed loop: detect to decision (verdict, alert, advice, report)
".venv\Scripts\python.exe" experiments\demo_closed_loop.py
if errorlevel 1 if "%RC%"=="0" set "RC=6"
echo.
echo [7/8] P6 red team bench (sealed corpus; evade / frame / blind; target fingerprint gate)
if not exist "samples\red_team\red_team.json" goto skip_red_team
".venv\Scripts\python.exe" experiments\red_team_bench.py
if errorlevel 1 echo   WARNING: red team step failed - likely target fingerprint mismatch, see docs\REDTEAM-PROTOCOL.md
if errorlevel 1 if "%RC%"=="0" set "RC=7"
goto after_red_team
:skip_red_team
echo   skipped: samples\red_team\red_team.json not found
:after_red_team
echo.
echo [8/8] syntax compile (voiceguard tests experiments samples tools)
".venv\Scripts\python.exe" -m compileall -q voiceguard tests experiments samples tools
if errorlevel 1 if "%RC%"=="0" set "RC=8"
echo.
echo image report : %~dp0output\p2\bench_report.md
echo text  report : %~dp0output\p2\text_report.md
echo comment rpt  : %~dp0output\p5\comment_report.md
echo redteam rpt  : %~dp0output\redteam\red_team_report.md
echo submission   : %~dp0dist\submission_pack\lorealguard_testset_v4.zip
echo verify       : %~dp0output\f4\_verify.txt
echo.
if "%RC%"=="0" echo ACCEPTANCE: RC=0 -- all 8 steps passed
if not "%RC%"=="0" echo ACCEPTANCE: RC=%RC% is non-zero -- first failing step number is above
echo RUNBAT_EXIT=%RC%
pause
exit /b %RC%
