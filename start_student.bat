@echo off
rem ============================================================
rem  学生端 —— 学生用这个地址访问
rem
rem  与学生端/教师端分开端口：本脚本固定 APP_ROLE=student，
rem  访问根路径会直接跳到 /student。教师端请用 start_teacher.bat。
rem
rem  换端口：start_student.bat -Port 8030
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_production.ps1" -Role student -Port 8010 %*
if errorlevel 1 (
    echo.
    echo [启动失败] 请查看上方错误信息。
    pause
)
