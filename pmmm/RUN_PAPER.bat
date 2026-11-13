@echo off
cd /d %~dp0\..
title PMMM paper (no orders sent)
if not exist .venv\Scripts\python.exe (python -m venv .venv && .venv\Scripts\pip install requests)
set PMMM_RUN_S=86400
set PMMM_OUT=data\pmmm\local
.venv\Scripts\python.exe -u pmmm\bot.py
pause
