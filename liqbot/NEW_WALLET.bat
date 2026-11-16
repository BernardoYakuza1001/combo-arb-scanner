@echo off
cd /d %~dp0
title LIQBOT - create bot wallet
set PY=python
if exist C:\morpho-scan\claude-lab\projects\22-edges\.venv\Scripts\python.exe set PY=C:\morpho-scan\claude-lab\projects\22-edges\.venv\Scripts\python.exe
"%PY%" new_wallet.py
pause
