@echo off
cd /d %~dp0
title PMMM LIVE - create a file named STOP in this folder to cancel all orders and exit
if not exist .env (echo Create pmmm\.env first - copy env.example to .env and fill it in & pause & exit /b)
if exist STOP del STOP
call _venv.bat
cd ..
set PMMM_LIVE=YES
set PMMM_N=5
set PMMM_CAP=200
set PMMM_MAX_TOTAL=1000
.venv\Scripts\python.exe -u pmmm\live.py
pause
