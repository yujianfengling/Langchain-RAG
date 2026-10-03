@echo off
rem ============================================================
rem  TEACHER side  -  keep this address for yourself
rem
rem  APP_ROLE=teacher, default port 8020.
rem  Root path goes straight to /teacher.
rem  Student side: run start_student.bat instead.
rem
rem  Change port:  start_teacher.bat -Port 8030
rem  Force rebuild: start_teacher.bat -Rebuild
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_production.ps1" -Role teacher -Port 8020 %*
if errorlevel 1 (
    echo.
    echo [FAILED] See the message above.
    pause
)
