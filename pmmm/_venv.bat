@echo off
rem shared: make sure ..\.venv exists with the packages PMMM needs
if not exist ..\.venv\Scripts\python.exe (
  if exist C:\morpho-scan\venv\Scripts\python.exe (C:\morpho-scan\venv\Scripts\python.exe -m venv ..\.venv) else (python -m venv ..\.venv)
)
..\.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check requests py-clob-client pytest
