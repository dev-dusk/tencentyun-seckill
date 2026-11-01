@echo off
setlocal EnableExtensions
chcp 65001 >nul
rem ===== install: check python -^> .venv -^> deps -^> chromium -^> verify (log: logs\install.log) =====
set "ROOT=%~dp0"
cd /d "%ROOT%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%tools\step_install.ps1"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo ===== [OK] install finished successfully. See logs\install.log =====
) else (
    echo ===== [FAIL] install failed with code %RC%. See logs\install.log =====
)
pause
exit /b %RC%
