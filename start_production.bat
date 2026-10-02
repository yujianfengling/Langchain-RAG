@echo off
rem 双击即可：生产模式启动（前端构建产物由后端直接托管，只启一个进程）
rem 底层调用同目录的 start_production.ps1；用 -ExecutionPolicy Bypass 绕过脚本执行策略限制。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_production.ps1" %*
if errorlevel 1 (
    echo.
    echo [启动失败] 请查看上方错误信息。
    pause
)
