@echo off
cd /d %~dp0
title PMMM key setup - the key stays on this PC
call _venv.bat
..\.venv\Scripts\python.exe setup_key.py
pause
