@echo off
cd /d %~dp0
title PMMM preflight - places NO orders
call _venv.bat
cd ..
.venv\Scripts\python.exe -m pytest -q pmmm\test_bot.py pmmm\test_live.py
.venv\Scripts\python.exe -u pmmm\preflight.py
pause
