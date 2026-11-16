@echo off
cd /d %~dp0
title LIQBOT RUNNING - Morpho liquidations on Base - close this window to stop
set PATH=%~dp0.node;%PATH%
:run
call "%~dp0.node\pnpm.cmd" liquidate >> liqbot_log.txt 2>&1
echo Bot stopped, restarting in 20 seconds - close this window to stop.
timeout /t 20 /nobreak >nul
goto run
