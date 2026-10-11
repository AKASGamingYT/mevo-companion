@echo off
setlocal
cd /d "%~dp0"
title Mevo Companion API

set "PY=%~dp0.venv\Scripts\python.exe"
set "URL=http://127.0.0.1:8787"

if not exist "%PY%" (
  echo Missing virtualenv Python:
  echo   %PY%
  echo Create it first, then install the app:
  echo   py -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -e .
  pause
  exit /b 1
)

"%PY%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=1)" >nul 2>&1
if not errorlevel 1 (
  echo Mevo Companion is already running at %URL%
  start "" "%URL%"
  exit /b 0
)

set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=%PY%"

echo Starting Mevo Companion in the system tray.
echo Right-click the Mevo icon and choose Open web UI.
start "" "%PYW%" -m mevo_companion
exit /b 0
