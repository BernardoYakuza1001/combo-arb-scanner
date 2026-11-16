@echo off
cd /d %~dp0
title LIQBOT RUNNING - Morpho liquidations on Base - close this window to stop
set PATH=%~dp0.node;%PATH%
set COREPACK_INTEGRITY_KEYS=0
set COREPACK_ENABLE_DOWNLOAD_PROMPT=0
:run
call "%~dp0.node\pnpm.cmd" liquidate >> liqbot_log.txt 2>&1
echo Bot stopped, restarting in 20 seconds - close this window to stop.
timeout /t 20 /nobreak >nul
goto run
