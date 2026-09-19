@echo off
rem LorealGuard generic launcher  --  usage: py.bat <script> [args]
rem ASCII only on purpose: cmd.exe parses .bat as ANSI, so Chinese literals here
rem break the batch parser (found the hard way: it silently skipped "set PYTHONPATH=").
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH="
set "PYTHONIOENCODING=utf-8"
".venv\Scripts\python.exe" %*