@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Skill.ps1" %*
if errorlevel 1 goto failed
echo Skill installed.
pause
exit /b 0
:failed
echo Skill installation failed.
pause
exit /b 1
