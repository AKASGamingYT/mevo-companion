@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
if not exist "%~dp0firmware-store" mkdir "%~dp0firmware-store"
"%PY%" -m mevo_companion.firmware check >> "%~dp0firmware-store\check.log" 2>&1
exit /b %ERRORLEVEL%
