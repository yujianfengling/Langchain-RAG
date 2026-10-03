@echo off
rem ============================================================
rem  教师端 —— 老师用这个地址访问
rem
rem  与学生端分开端口：本脚本固定 APP_ROLE=teacher，
rem  访问根路径会直接跳到 /teacher。学生端请用 start_student.bat。
rem
rem  换端口：start_teacher.bat -Port 8030
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_production.ps1" -Role teacher -Port 8020 %*
if errorlevel 1 (
    echo.
    echo [启动失败] 请查看上方错误信息。
    pause
)
