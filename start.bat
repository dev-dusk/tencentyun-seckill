@echo off
setlocal EnableExtensions
chcp 65001 >nul
rem ===== start seckill program. default is DRY ^(simulation^): no real order request. =====
rem for REAL orders: set runtime.mode="real" in settings.json, then run and confirm YES
rem Ctrl+C exits safely. logs: logs\run.log + logs\snap_*.log
set "ROOT=%~dp0"
cd /d "%ROOT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%ROOT%tools\run_py.ps1' -Script 'snap_up_server.py' -LogName 'run'"
set "RC=%ERRORLEVEL%"
echo.
echo ===== start.bat finished with code %RC%. Logs: logs\run.log, logs\snap_*.log =====
pause
exit /b %RC%
