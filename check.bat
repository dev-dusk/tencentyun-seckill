@echo off
setlocal EnableExtensions
chcp 65001 >nul
rem ===== pre-flight read-only check, NEVER places an order (log: logs\check.log) =====
set "ROOT=%~dp0"
cd /d "%ROOT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%ROOT%tools\run_py.ps1' -Script 'snap_up_server.py' -LogName 'check' -ExtraArgs '--mode,check'"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo ===== [OK] check passed ^(no order was placed^). Next: start.bat =====
) else (
    echo ===== [ATTN] check found problems ^(code %RC%^). Fix them first. =====
)
pause
exit /b %RC%
