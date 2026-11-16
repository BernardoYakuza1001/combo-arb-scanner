@echo off
setlocal
cd /d %~dp0
title LIQBOT setup - installs Node.js locally (in this folder) and builds the bot
set LOG=%~dp0setup_log.txt
echo ==== setup %date% %time% ==== >> "%LOG%"
if not exist "%~dp0.node22\node.exe" (
  echo Downloading Node.js 22 portable into this folder...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get_node.ps1" >> "%LOG%" 2>&1
)
if not exist "%~dp0.node22\node.exe" (
  echo Node.js download failed - see setup_log.txt
  goto end
)
set PATH=%~dp0.node22;%PATH%
set COREPACK_INTEGRITY_KEYS=0
set COREPACK_ENABLE_DOWNLOAD_PROMPT=0
set COREPACK_ENABLE_DOWNLOAD_PROMPT=0
echo Installing pnpm...
if not exist "%~dp0.node22\pnpm.cmd" call "%~dp0.node22\npm.cmd" install -g pnpm@9 --prefix "%~dp0.node22" >> "%LOG%" 2>&1
if not exist "%~dp0.node22\pnpm.cmd" (
  echo pnpm install failed - see setup_log.txt
  goto end
)
echo Installing the bot's packages (a few minutes)...
call "%~dp0.node22\pnpm.cmd" install >> "%LOG%" 2>&1
if errorlevel 1 (
  echo Package install failed - see setup_log.txt
  goto end
)
echo Building...
call "%~dp0.node22\pnpm.cmd" build >> "%LOG%" 2>&1
if errorlevel 1 (
  echo Build failed - see setup_log.txt
  goto end
)
echo Build OK. >> "%LOG%"
echo Build OK.
set PY=python
if exist C:\morpho-scan\claude-lab\projects\22-edges\.venv\Scripts\python.exe set PY=C:\morpho-scan\claude-lab\projects\22-edges\.venv\Scripts\python.exe
"%PY%" setup_key.py
:end
echo.
pause
