@echo off
cd /d %~dp0
title PMMM DRY RUN THIN - logs the orders it would send, sends none
call _venv.bat
cd ..
set PMMM_DRY=YES
set PMMM_MODE=thin
set PMMM_N=30
set PM_STATUS_TOP=900
set PMMM_MAX_MINSZ=50
set PMMM_CAP=1
set PMMM_FRAC=0.75
set PMMM_AHEAD=0
set PMMM_STOP=1
set PMMM_MAX_TOTAL=1000
set PMMM_MAX_LOSS=40
.venv\Scripts\python.exe -u pmmm\live.py
pause
