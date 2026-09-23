@echo off
setlocal
title WHU Notice - Test Today's Push

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\test_today_push.ps1"
set "SCRIPT_EXIT=%ERRORLEVEL%"

echo.
if not "%SCRIPT_EXIT%"=="0" echo The test did not finish successfully. Keep the error above.
pause
exit /b %SCRIPT_EXIT%