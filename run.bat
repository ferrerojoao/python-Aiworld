@echo off
setlocal
title AIWorld
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtualenv not found: .venv\Scripts\python.exe
    echo.
    echo Create it first:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -e ".[dev]"
    echo.
    pause
    exit /b 1
)

echo ==========================================================
echo   AIWorld server
echo ==========================================================
echo   Local     : http://127.0.0.1:8765/
".venv\Scripts\python.exe" -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',80));print('  LAN       : http://' + s.getsockname()[0] + ':8765/')" 2>nul || echo   LAN       : unknown - run ipconfig to find your IPv4
echo   Bind      : see AIWORLD_HOST / AIWORLD_PORT in .env
echo   Stop      : Ctrl+C
echo ==========================================================
echo   LAN access is only reachable when AIWORLD_HOST=0.0.0.0
echo   and the firewall allows inbound TCP 8765.
echo ==========================================================
echo.

".venv\Scripts\python.exe" app\main.py

echo.
echo Server stopped - exit code %ERRORLEVEL%.
pause
endlocal
