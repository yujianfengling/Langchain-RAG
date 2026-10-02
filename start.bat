@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion

rem ================================================================
rem  智慧教育 RAG 系统 - 一键启动（后端 + 前端）
rem
rem  用法:
rem    start.bat                 启动后端 + 前端，并打开浏览器
rem    start.bat -BackendOnly    只启动后端
rem    start.bat -NoBrowser      启动完成后不打开浏览器
rem    start.bat -SkipPortCheck  不检查端口占用
rem    start.bat -NonInteractive 全程不等待输入（脚本 / CI / Agent 调用）
rem    start.bat -DryRun         只做环境自检，不启动任何服务
rem    start.bat -Help           显示帮助
rem
rem  重要:
rem    * 本文件保存为 UTF-8（无 BOM）。第二行的 chcp 65001 让下面的中文
rem      正常显示；cmd 默认按系统 ANSI（GBK）读 .bat，两者混用会乱码。
rem    * 不要用 "cmd /c ... < NUL" 这类会捕获输出后立刻退出的方式运行本
rem      脚本: 后端 / 前端是本脚本的子进程，调用方进程树被销毁时它们会
rem      被一起杀掉，现象是"刚启动就退出"。请在真实终端窗口里运行。
rem    * 后端与前端各自在独立窗口运行，关闭那两个窗口即停止服务。
rem ================================================================

rem --- 统一 UTF-8，避免 Python 的中文日志在 GBK 控制台下报编码错误 ---
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

rem --- 路径与端口 ---
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=3000"
set "PY=%ROOT%\venv\Scripts\python.exe"
set "RUN_BACKEND=%ROOT%\scripts\run_backend.bat"
set "RUN_FRONTEND=%ROOT%\scripts\run_frontend.bat"

rem --- 参数解析（-Foo 与 /Foo 都接受）---
set "OPT_NOBROWSER="
set "OPT_BACKENDONLY="
set "OPT_SKIPPORT="
set "OPT_NONINTERACTIVE="
set "OPT_DRYRUN="
set "EXITCODE=0"

:parse_args
if "%~1"=="" goto args_done
set "A=%~1"
if /i "%A%"=="-NoBrowser"      set "OPT_NOBROWSER=1"
if /i "%A%"=="/NoBrowser"      set "OPT_NOBROWSER=1"
if /i "%A%"=="-BackendOnly"    set "OPT_BACKENDONLY=1"
if /i "%A%"=="/BackendOnly"    set "OPT_BACKENDONLY=1"
if /i "%A%"=="-SkipPortCheck"  set "OPT_SKIPPORT=1"
if /i "%A%"=="/SkipPortCheck"  set "OPT_SKIPPORT=1"
if /i "%A%"=="-NonInteractive" set "OPT_NONINTERACTIVE=1"
if /i "%A%"=="/NonInteractive" set "OPT_NONINTERACTIVE=1"
if /i "%A%"=="-DryRun"         set "OPT_DRYRUN=1"
if /i "%A%"=="/DryRun"         set "OPT_DRYRUN=1"
if /i "%A%"=="-Help"           goto show_help
if /i "%A%"=="/?"              goto show_help
if /i "%A%"=="-h"              goto show_help
shift
goto parse_args
:args_done

rem 非交互模式隐含: 不打开浏览器、不询问端口
if defined OPT_NONINTERACTIVE (
    set "OPT_NOBROWSER=1"
    set "OPT_SKIPPORT=1"
)

rem 就绪等待上限: 交互 90 秒，无人值守 20 秒（快速失败而不是卡住）
set "WAIT_SECS=90"
if defined OPT_NONINTERACTIVE set "WAIT_SECS=20"

echo ==========================================================
echo   智慧教育 RAG 系统 - 一键启动
echo ==========================================================
echo.

rem ================================================== 1. 环境自检
echo [1/5] 环境自检

if not exist "%PY%" (
    echo       [X] 找不到虚拟环境解释器: %PY%
    echo           请先执行:
    echo             python -m venv venv
    echo             .\venv\Scripts\python.exe -m pip install -r requirements.txt
    set "EXITCODE=1"
    goto cleanup
)
for /f "delims=" %%v in ('"%PY%" --version 2^>^&1') do set "PYVER=%%v"
echo       [OK] !PYVER!

rem HTTP 探测工具: 优先 curl.exe（Win10 1803+ 自带），缺失时回退 PowerShell
set "HTTP_CURL="
where curl.exe >nul 2>&1 && set "HTTP_CURL=1"

if not defined OPT_BACKENDONLY (
    where npm >nul 2>&1
    if errorlevel 1 (
        echo       [X] 未找到 npm，请先安装 Node.js 18+
        set "EXITCODE=1"
        goto cleanup
    )
    for /f "delims=" %%v in ('npm --version 2^>^&1') do set "NPMVER=%%v"
    echo       [OK] npm !NPMVER!

    if not exist "%ROOT%\frontend\node_modules" (
        echo       [..] frontend\node_modules 不存在，正在安装前端依赖（首次较慢）...
        pushd "%ROOT%\frontend"
        call npm install
        if errorlevel 1 (
            popd
            echo       [X] npm install 失败
            set "EXITCODE=1"
            goto cleanup
        )
        popd
    )
    echo       [OK] 前端依赖已就绪
) else (
    if not exist "%RUN_BACKEND%" (
        echo       [X] 缺少后端启动子脚本: %RUN_BACKEND%
        set "EXITCODE=1"
        goto cleanup
    )
)

rem 这两个依赖漏装过，会导致测试跑不了 / BM25 静默降级
"%PY%" -c "import pytest" >nul 2>&1
if errorlevel 1 echo       [警告] 未安装 pytest，无法运行测试: pip install pytest pytest-xdist
"%PY%" -c "import jieba" >nul 2>&1
if errorlevel 1 echo       [警告] 未安装 jieba，BM25 会静默回退字符级分词: pip install jieba

rem ================================================== 2. 端口检查
echo.
echo [2/5] 端口检查

if defined OPT_SKIPPORT goto ports_done
call :check_port %BACKEND_PORT% "后端"
if defined OPT_BACKENDONLY goto ports_done
call :check_port %FRONTEND_PORT% "前端"
:ports_done

if defined OPT_DRYRUN (
    echo.
    echo [DryRun] 环境自检完成，未启动任何服务。
    set "EXITCODE=0"
    goto cleanup
)

rem ================================================== 3. 后端
echo.
echo [3/5] 启动后端（端口 %BACKEND_PORT%）

call :http_ok "http://localhost:%BACKEND_PORT%/health" 4
if not errorlevel 1 (
    echo       [OK] 后端已在运行，跳过启动
) else (
    if not exist "%RUN_BACKEND%" (
        echo       [X] 缺少后端启动子脚本: %RUN_BACKEND%
        set "EXITCODE=1"
        goto cleanup
    )
    start "RAG 后端" /d "%ROOT%" cmd /k call "%RUN_BACKEND%"
    echo       [..] 后端窗口已打开，等待就绪
    call :wait_http "http://localhost:%BACKEND_PORT%/health" %WAIT_SECS%
    if errorlevel 1 (
        echo.
        echo       [X] 后端 %WAIT_SECS% 秒内未就绪
        echo           后端窗口会保留报错信息，请查看那个窗口
        set "EXITCODE=1"
        goto cleanup
    )
    echo.
    echo       [OK] 后端就绪
)

rem --- 用大白话报告检索模式，降级能被立刻发现 ---
if defined HTTP_CURL (
    set "READY_JSON="
    for /f "delims=" %%j in ('curl -s --max-time 5 "http://localhost:%BACKEND_PORT%/health/ready"') do set "READY_JSON=%%j"
    if defined READY_JSON (
        echo !READY_JSON! | findstr /i "hybrid_ready"  >nul && echo       [OK] 检索模式: hybrid_ready（向量 + 关键词，最佳）
        echo !READY_JSON! | findstr /i "vector_only"   >nul && echo       [警告] 检索模式: vector_only（BM25 索引缺失，建议重建知识库）
        echo !READY_JSON! | findstr /i "bm25_degraded" >nul && echo       [警告] 检索模式: bm25_degraded（向量检索不可用，只剩关键词）
        echo !READY_JSON! | findstr /i "not_loaded"    >nul && echo       [警告] 检索模式: not_loaded（首次提问时才加载 embedding 模型）
    )
)

rem ================================================== 4. 前端
if defined OPT_BACKENDONLY goto skip_frontend

echo.
echo [4/5] 启动前端（端口 %FRONTEND_PORT%）

call :http_ok "http://localhost:%FRONTEND_PORT%" 4
if not errorlevel 1 (
    echo       [OK] 前端已在运行，跳过启动
) else (
    if not exist "%RUN_FRONTEND%" (
        echo       [X] 缺少前端启动子脚本: %RUN_FRONTEND%
        set "EXITCODE=1"
        goto cleanup
    )
    start "RAG 前端" /d "%ROOT%" cmd /k call "%RUN_FRONTEND%"
    echo       [..] 前端窗口已打开，等待就绪（Vite 首次启动较慢）
    call :wait_http "http://localhost:%FRONTEND_PORT%" %WAIT_SECS%
    if errorlevel 1 (
        echo.
        echo       [警告] 前端 %WAIT_SECS% 秒内未就绪，请查看前端窗口
    ) else (
        echo.
        echo       [OK] 前端就绪
    )
)
:skip_frontend

rem ================================================== 5. 完成
echo.
echo [5/5] 启动完成
echo.
if defined OPT_BACKENDONLY (
    echo       后端 API : http://localhost:%BACKEND_PORT%
) else (
    echo       前端界面 : http://localhost:%FRONTEND_PORT%
    echo       后端 API : http://localhost:%BACKEND_PORT%
)
echo       API 文档 : http://localhost:%BACKEND_PORT%/docs
echo       健康检查 : http://localhost:%BACKEND_PORT%/health/ready
echo.
echo       提示:
echo         - 聊天页在 /chat（根路径 / 是学习首页，需要登录）
echo         - 后端 / 前端的日志在它们各自的窗口里
echo         - 关闭那两个窗口即停止服务
echo.

if defined OPT_NOBROWSER goto all_done
if defined OPT_BACKENDONLY (
    start "" "http://localhost:%BACKEND_PORT%/docs"
) else (
    start "" "http://localhost:%FRONTEND_PORT%/chat"
)
:all_done
set "EXITCODE=0"
goto cleanup

rem ================================================== 子程序

:check_port
rem %1 = 端口, %2 = 名称
set "CP_PORT=%~1"
set "CP_LABEL=%~2"
set "CP_PID="
for /f "tokens=5" %%p in ('netstat -ano -p TCP ^| findstr /i "LISTENING" ^| findstr /c:":%CP_PORT% "') do set "CP_PID=%%p"
if not defined CP_PID (
    echo       [OK] 端口 %CP_PORT% 空闲
    exit /b 0
)
set "CP_NAME=未知进程"
rem 只认 tasklist 的 CSV 行（含 .exe），否则它会把自己的提示语当成进程名
for /f "tokens=1 delims=," %%n in ('tasklist /fi "PID eq !CP_PID!" /nh /fo csv 2^>nul ^| findstr /i "\.exe"') do set "CP_NAME=%%~n"
echo       [警告] %CP_LABEL%端口 %CP_PORT% 已被 !CP_NAME! 占用（PID !CP_PID!）
if defined OPT_NONINTERACTIVE (
    echo              非交互模式: 保留现有服务
    exit /b 0
)
if defined OPT_DRYRUN (
    echo              DryRun: 不处理端口占用
    exit /b 0
)
set "ANS="
set /p "ANS=              是否结束它并继续? (y/N) "
if /i "!ANS!"=="y" (
    taskkill /f /pid !CP_PID! >nul 2>&1
    timeout /t 1 /nobreak >nul 2>&1
    echo       [OK] 端口 %CP_PORT% 已释放
) else (
    echo       [..] 保留现有服务
)
exit /b 0

:http_ok
rem %1 = URL, %2 = 超时秒数 ; errorlevel 0 = 可访问
if not defined HTTP_CURL goto http_ok_ps
curl -s -o NUL --max-time %~2 --connect-timeout %~2 "%~1" >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0
:http_ok_ps
powershell -NoProfile -Command "try{Invoke-WebRequest -Uri '%~1' -TimeoutSec %~2 -UseBasicParsing | Out-Null; exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0

:wait_http
rem %1 = URL, %2 = 最长等待秒数 ; errorlevel 0 = 已就绪
set /a WH_LEFT=%~2
:wh_loop
call :http_ok "%~1" 3
if not errorlevel 1 exit /b 0
set /a WH_LEFT-=2
if %WH_LEFT% LEQ 0 exit /b 1
<nul set /p "=."
timeout /t 2 /nobreak >nul 2>&1
goto wh_loop

:show_help
echo 智慧教育 RAG 系统 - 一键启动
echo.
echo   用法: start.bat [选项]
echo.
echo     -BackendOnly     只启动后端
echo     -NoBrowser       启动完成后不打开浏览器
echo     -SkipPortCheck   不检查端口占用
echo     -NonInteractive  全程不等待输入（脚本 / CI / Agent 调用）
echo     -DryRun          只做环境自检，不启动服务
echo     -Help            显示本帮助
echo.
echo   示例:
echo     start.bat
echo     start.bat -BackendOnly
echo     start.bat -NonInteractive
set "EXITCODE=0"
goto cleanup

:cleanup
endlocal & exit /b %EXITCODE%
