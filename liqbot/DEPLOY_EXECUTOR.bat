@echo off
cd /d %~dp0
title LIQBOT - deploy your executor contract on Base (one time, costs a few cents of gas)
set PATH=%~dp0.nodejs;%PATH%
set COREPACK_INTEGRITY_KEYS=0
set COREPACK_ENABLE_DOWNLOAD_PROMPT=0
call "%~dp0.nodejs\pnpm.cmd" deploy:executor
pause
