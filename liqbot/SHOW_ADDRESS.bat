@echo off
cd /d %~dp0
title LIQBOT wallet address
set PY=python
if exist C:\morpho-scan\claude-lab\projects\22-edges\.venv\Scripts\python.exe set PY=C:\morpho-scan\claude-lab\projects\22-edges\.venv\Scripts\python.exe
"%PY%" show_address.py
pause
