@echo off
cd /d %~dp0
title LIQBOT - deploy your executor contract on Base (one time, costs a few cents of gas)
set PATH=%~dp0.node;%PATH%
call pnpm deploy:executor
pause
