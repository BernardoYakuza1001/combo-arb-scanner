@echo off
cd /d %~dp0
title PMMM DRY RUN - logs the orders it would send, sends none
call _venv.bat
cd ..
set PMMM_DRY=YES
set PMMM_N=5
set PMMM_CAP=200
set PMMM_FRAC=0.75
set PMMM_MAX_TOTAL=1000
.venv\Scripts\python.exe -u pmmm\live.py
pause
