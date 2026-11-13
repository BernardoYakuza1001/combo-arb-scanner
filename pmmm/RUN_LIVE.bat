@echo off
cd /d %~dp0\..
title PMMM LIVE - create pmmm\STOP to cancel everything and exit
if not exist pmmm\.env (echo Create pmmm\.env first - see top of pmmm\live.py & pause & exit /b)
if not exist .venv\Scripts\python.exe (python -m venv .venv)
.venv\Scripts\pip install -q requests py-clob-client
set PMMM_LIVE=YES
set PMMM_N=5
set PMMM_CAP=200
set PMMM_MAX_TOTAL=1000
.venv\Scripts\python.exe -u pmmm\live.py
pause
