@echo off
rem ---------------------------------------------------------------
rem  Frontend launcher (called by start.bat in its own window).
rem  Separate file for the same nested-quote reason as run_backend.bat.
rem ---------------------------------------------------------------
setlocal EnableExtensions

set "SCRIPTS=%~dp0"
for %%I in ("%SCRIPTS%..") do set "ROOT=%%~fI"

cd /d "%ROOT%\frontend"
if errorlevel 1 (
    echo [X] cannot enter frontend folder: %ROOT%\frontend
    pause
    exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
    echo [X] npm not found. Install Node.js 18+ first.
    pause
    exit /b 1
)

if not exist "node_modules" (
    echo [..] node_modules missing - running npm install first
    call npm install
    if errorlevel 1 (
        echo [X] npm install failed
        pause
        exit /b 1
    )
)

echo ==========================================================
echo   Frontend ^|  http://localhost:3000
echo   Chat page ^| http://localhost:3000/chat
echo   Press Ctrl+C to stop. Close this window to stop.
echo ==========================================================
echo.

call npm run dev

echo.
echo [frontend exited with code %errorlevel%]
pause
endlocal
