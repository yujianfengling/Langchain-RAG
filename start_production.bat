@echo off
rem ============================================================
rem  FULL PLATFORM - student and teacher share ONE address.
rem  Default port 8000, no APP_ROLE.
rem
rem  Want separate addresses? Use start_student.bat / start_teacher.bat
rem
rem  Options:  -Port 8080   -SkipBuild   -Rebuild   -Open
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_production.ps1" %*
if errorlevel 1 (
    echo.
    echo [FAILED] See the message above.
    pause
)
