@echo off
cd /d %~dp0
title BOT RUNNING - REAL ORDERS - to stop: double-click STOP_BOT
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
:run
.venv\Scripts\python.exe -u pmmm\live.py
if errorlevel 1 if not exist pmmm\STOP (
  echo.
  echo The bot stopped on an error. All orders were cancelled. Restarting in 20 seconds - close this window to stop.
  timeout /t 20 /nobreak >nul
  goto run
)
pause
