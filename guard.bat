@echo off
REM ============================================================
REM  LorealGuard  framework entry (F0)   -- ASCII ONLY, cmd parses .bat as ANSI
REM  usage:  guard.bat                       (default sample)
REM          guard.bat path\to\image.jpg
REM ============================================================
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH="
set "IMG=%~1"
if "%IMG%"=="" set "IMG=samples\base_neutral.jpg"
".venv\Scripts\python.exe" -m voiceguard "%IMG%"
echo.
echo [done] report + evidence sheet are in output\guard\
pause