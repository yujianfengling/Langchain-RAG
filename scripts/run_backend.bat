@echo off
rem ---------------------------------------------------------------
rem  Backend launcher (called by start.bat in its own window).
rem
rem  Keeping this in a separate file avoids the nested-quote problem
rem  that kills a one-liner like:
rem      start "x" cmd /k "cd /d "%ROOT%" && "%PY%" run_server.py"
rem  cmd terminates the command at the second quote, so the window
rem  opens and closes immediately.
rem ---------------------------------------------------------------
setlocal EnableExtensions

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
chcp 65001 >nul 2>&1

rem %~dp0 ends with a backslash and points at this file's folder (scripts\)
set "SCRIPTS=%~dp0"
for %%I in ("%SCRIPTS%..") do set "ROOT=%%~fI"

cd /d "%ROOT%"
if errorlevel 1 (
    echo [X] cannot enter project root: %ROOT%
    pause
    exit /b 1
)

set "PY=%ROOT%\venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [X] venv python not found: %PY%
    pause
    exit /b 1
)

echo ==========================================================
echo   Backend  ^|  http://localhost:8000
echo   API docs ^|  http://localhost:8000/docs
echo   Press Ctrl+C to stop. Close this window to stop.
echo ==========================================================
echo.

"%PY%" run_server.py

rem If the server exits (crash or Ctrl+C), keep the window so the
rem error stays readable instead of flashing away.
echo.
echo [backend exited with code %errorlevel%]
pause
endlocal
