@echo off
cd /d %~dp0
title LIQBOT setup - installs Node.js locally (in this folder) and builds the bot
if not exist .node\node.exe (
  echo Downloading Node.js 20 portable into this folder...
  powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest https://nodejs.org/dist/v20.18.1/node-v20.18.1-win-x64.zip -OutFile node.zip; Expand-Archive node.zip -DestinationPath .nodetmp -Force; Move-Item .nodetmp\node-v20.18.1-win-x64 .node; Remove-Item node.zip; Remove-Item .nodetmp -Recurse"
)
set PATH=%~dp0.node;%PATH%
call corepack enable --install-directory .node
call pnpm install
call pnpm build
set PY=python
if exist C:\morpho-scan\claude-lab\projects\22-edges\.venv\Scripts\python.exe set PY=C:\morpho-scan\claude-lab\projects\22-edges\.venv\Scripts\python.exe
%PY% setup_key.py
pause
