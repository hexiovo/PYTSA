@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Clean-Workspace.ps1" %*
exit /b %ERRORLEVEL%
