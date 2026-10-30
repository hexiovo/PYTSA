@echo off
setlocal
set "PYTSA_HOME=%~dp0Data"
if not exist "%~dp0PYTSA.exe" (
  echo PYTSA.exe was not found beside this launcher.
  pause
  exit /b 1
)
start "" "%~dp0PYTSA.exe"
