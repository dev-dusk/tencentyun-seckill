@echo off
setlocal EnableExtensions
chcp 65001 >nul
rem ===== run offline automated tests with mocked data, never places real orders (log: logs\test.log) =====
set "ROOT=%~dp0"
cd /d "%ROOT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%ROOT%tools\run_py.ps1' -Test -LogName 'test'"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo ===== [OK] all tests passed. See logs\test.log =====
) else (
    echo ===== [FAIL] some tests failed ^(code %RC%^). See logs\test.log =====
)
pause
exit /b %RC%
