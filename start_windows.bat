@echo off
:: ============================================================
::  Project Antigravity — Windows Launcher
::  Double-click to start everything automatically.
::  - Starts P2P nodes
::  - Starts React dashboard (http://localhost:5173)
::  - Auto-discovers other PCs on the same WiFi — NO manual IP needed!
:: ============================================================

title Project Antigravity — P2P Command Center
color 0B

echo.
echo  ╔══════════════════════════════════════════════════════════╗
echo  ║           PROJECT ANTIGRAVITY  —  P2P Launcher           ║
echo  ║      Kademlia / discv5-inspired P2P Discovery Protocol   ║
echo  ║   Auto-Discovery ON  — Nodes find each other on LAN      ║
echo  ╚══════════════════════════════════════════════════════════╝
echo.

:: ── Always run from the folder where this .bat lives ───────────────────────
cd /d "%~dp0"
echo [INFO] Project folder: %CD%
echo.

:: ── Check Python ────────────────────────────────────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found!
    echo         Download from: https://python.org/downloads
    echo         IMPORTANT: Check "Add Python to PATH" during install!
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do echo [OK] %%i found.

:: ── Allow through Windows Firewall (run as Administrator for best results) ──
echo.
echo [SETUP] Allowing Python through Windows Firewall...
netsh advfirewall firewall add rule name="Antigravity P2P UDP" ^
    dir=in action=allow protocol=UDP localport=9000-9020 >nul 2>&1
netsh advfirewall firewall add rule name="Antigravity P2P TCP" ^
    dir=in action=allow protocol=TCP localport=9001-9021 >nul 2>&1
netsh advfirewall firewall add rule name="Antigravity Discovery" ^
    dir=in action=allow protocol=UDP localport=19099 >nul 2>&1
echo [OK] Firewall rules applied.

:: ── Install Python dependencies ─────────────────────────────────────────────
echo.
echo [SETUP] Installing Python dependencies...
pip install -q cryptography PyQt6
if %errorlevel% neq 0 (
    echo [ERROR] pip install failed. Try right-clicking and "Run as Administrator".
    pause
    exit /b 1
)
echo [OK] Python dependencies ready.

:: ── Parse optional arguments ────────────────────────────────────────────────
set NODES=3
set START_PORT=9000
set EXTRA=

:parse_args
if "%~1"=="" goto run
if /i "%~1"=="--nodes"        ( set NODES=%~2      & shift & shift & goto parse_args )
if /i "%~1"=="--start-port"   ( set START_PORT=%~2 & shift & shift & goto parse_args )
if /i "%~1"=="--no-discovery" ( set EXTRA=--no-discovery & shift & goto parse_args )
if /i "%~1"=="--no-dashboard" ( set EXTRA=%EXTRA% --no-dashboard & shift & goto parse_args )
shift
goto parse_args

:run
echo.
echo [START] Launching %NODES% node(s) starting at port %START_PORT%...
echo [INFO]  Auto-discovery is ON — other PCs on this WiFi will connect automatically!
echo [INFO]  Dashboard will open at http://localhost:5173
echo.

python run.py --nodes %NODES% --start-port %START_PORT% %EXTRA%

pause
