@echo off
setlocal
cd /d "%~dp0"
title Mevo Companion firmware task
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Missing virtualenv Python:
  echo   %PY%
  pause
  exit /b 1
)
"%PY%" -m mevo_companion.firmware install-task
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  echo Could not remove the old firmware scheduled task.
  pause
)
exit /b %ERR%
