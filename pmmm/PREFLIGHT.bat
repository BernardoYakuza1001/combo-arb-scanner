@echo off
cd /d %~dp0
title PMMM preflight - places NO orders
call _venv.bat
cd ..
.venv\Scripts\python.exe -u pmmm\preflight.py
pause
