@echo off
cd /d %~dp0
title PMMM LIVE THIN - create a file named STOP in this folder to cancel all orders and exit
if not exist .env (echo Create pmmm\.env first - copy env.example to .env and fill it in & pause & exit /b)
if exist STOP del STOP
call _venv.bat
cd ..
set PMMM_LIVE=YES
set PMMM_MODE=thin
set PMMM_N=30
set PM_STATUS_TOP=900
set PMMM_MAX_MINSZ=20
set PMMM_CAP=1
set PMMM_FRAC=0.75
set PMMM_AHEAD=0
set PMMM_STOP=1
set PMMM_MAX_TOTAL=1000
set PMMM_MAX_LOSS=15
.venv\Scripts\python.exe -u pmmm\live.py
pause
