@echo off
setlocal EnableExtensions
chcp 65001 >nul
rem ===== login: QR scan -^> cookies.json -^> verify params + facecheck + capture (log: logs\login.log) =====
set "ROOT=%~dp0"
cd /d "%ROOT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%ROOT%tools\run_py.ps1' -Script 'get_cookies.py' -LogName 'login'"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo ===== [OK] login finished. Next: check.bat =====
) else (
    echo ===== [FAIL] login failed with code %RC%. See logs\login.log =====
)
pause
exit /b %RC%
