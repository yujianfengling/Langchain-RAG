@echo off
rem ============================================================
rem  STUDENT side  -  share this address with students
rem
rem  APP_ROLE=student, default port 8010.
rem  Root path goes straight to /student.
rem  Teacher side: run start_teacher.bat instead.
rem
rem  Change port:  start_student.bat -Port 8030
rem  Force rebuild: start_student.bat -Rebuild
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_production.ps1" -Role student -Port 8010 %*
if errorlevel 1 (
    echo.
    echo [FAILED] See the message above.
    pause
)
