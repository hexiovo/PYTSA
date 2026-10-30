@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\Clean-Workspace.ps1" %*
exit /b %ERRORLEVEL%
