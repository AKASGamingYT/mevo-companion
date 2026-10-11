@echo off
setlocal
cd /d "%~dp0"
title Mevo Companion startup
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Missing virtualenv Python:
  echo   %PY%
  pause
  exit /b 1
)
"%PY%" -m mevo_companion --install-startup
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  echo Could not install sign-in startup.
  pause
)
exit /b %ERR%
