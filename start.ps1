<#
.SYNOPSIS
    智慧教育 RAG 系统 —— 一键启动（后端 + 前端）

.DESCRIPTION
    依次完成：环境自检 → 端口清理 → 启动后端 → 等待就绪 → 启动前端 → 打开浏览器。
    后端与前端各自在独立窗口运行，关掉本脚本不会杀掉它们。

.PARAMETER NoBrowser
    启动完成后不自动打开浏览器。

.PARAMETER SkipPortCheck
    跳过端口占用检查（默认会询问是否结束占用进程）。

.PARAMETER BackendOnly
    只启动后端。

.EXAMPLE
    .\start.ps1
    .\start.ps1 -NoBrowser
    .\start.ps1 -BackendOnly

.NOTES
    若 PowerShell 因执行策略拒绝运行本脚本，用：
        powershell -ExecutionPolicy Bypass -File .\start.ps1
#>
[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$SkipPortCheck,
    [switch]$BackendOnly
)

$ErrorActionPreference = 'Stop'
$ROOT = $PSScriptRoot
$BACKEND_PORT = 8000
$FRONTEND_PORT = 3000

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    [!]  $msg" -ForegroundColor Yellow }
function Write-Err2($msg) { Write-Host "    [X]  $msg" -ForegroundColor Red }

function Test-PortBusy([int]$Port) {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return [bool]$conn
}

function Get-PortOwner([int]$Port) {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if (-not $conn) { return $null }
    $p = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    if ($p) { return $p } else { return $null }
}

function Wait-Http([string]$Url, [int]$TimeoutSec, [string]$Name) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -TimeoutSec 5 -UseBasicParsing
            if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { return $true }
        } catch {
            # 未就绪，继续等
        }
        Start-Sleep -Milliseconds 800
        Write-Host "." -NoNewline
    }
    return $false
}

Write-Host "==========================================================" -ForegroundColor White
Write-Host "  智慧教育 RAG 系统 - 一键启动" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor White

# ---------------------------------------------------------------- 1. 环境自检
Write-Step "1/5 环境自检"

$PY = Join-Path $ROOT 'venv\Scripts\python.exe'
if (-not (Test-Path $PY)) {
    Write-Err2 "未找到虚拟环境: $PY"
    Write-Host "        请先创建并安装依赖：" -ForegroundColor Gray
    Write-Host "          python -m venv venv" -ForegroundColor Gray
    Write-Host "          .\venv\Scripts\python.exe -m pip install -r requirements.txt" -ForegroundColor Gray
    exit 1
}
Write-OK "Python: $(& $PY --version 2>&1)"

if (-not $BackendOnly) {
    $NPM = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $NPM) {
        Write-Err2 "未找到 npm，请安装 Node.js 18+"
        exit 1
    }
    Write-OK "npm: $(npm --version 2>&1)"
    if (-not (Test-Path (Join-Path $ROOT 'frontend\node_modules'))) {
        Write-Warn2 "frontend\node_modules 不存在，正在安装前端依赖（首次较慢）..."
        Push-Location (Join-Path $ROOT 'frontend')
        try { npm install } finally { Pop-Location }
    }
    Write-OK "前端依赖已就绪"
}

# 关键依赖快检（这两个漏装过，会导致功能静默降级）
$depCheck = & $PY -c "import importlib.util as u; print('pytest' if u.find_spec('pytest') else '', 'jieba' if u.find_spec('jieba') else '')" 2>&1
if ($depCheck -notmatch 'pytest') { Write-Warn2 "未安装 pytest（无法运行测试）：pip install pytest pytest-xdist" }
if ($depCheck -notmatch 'jieba')  { Write-Warn2 "未安装 jieba（BM25 会静默回退字符级分词）：pip install jieba" }

# ---------------------------------------------------------------- 2. 端口检查
if (-not $SkipPortCheck) {
    Write-Step "2/5 端口检查"
    $ports = @{ $BACKEND_PORT = '后端'; $FRONTEND_PORT = '前端' }
    if ($BackendOnly) { $ports.Remove($FRONTEND_PORT) }

    foreach ($port in @($ports.Keys)) {
        if (Test-PortBusy $port) {
            $owner = Get-PortOwner $port
            $desc = if ($owner) { "$($owner.ProcessName) (PID $($owner.Id))" } else { '未知进程' }
            Write-Warn2 "$($ports[$port])端口 $port 已被占用：$desc"
            $answer = Read-Host "        是否结束该进程以继续？(y/N)"
            if ($answer -eq 'y' -or $answer -eq 'Y') {
                if ($owner) {
                    Stop-Process -Id $owner.Id -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Milliseconds 800
                    if (Test-PortBusy $port) { Write-Err2 "端口 $port 仍被占用，请手动处理"; exit 1 }
                    Write-OK "已释放端口 $port"
                }
            } else {
                Write-Warn2 "保留现有服务，跳过启动 $($ports[$port])"
                $ports.Remove($port) | Out-Null
            }
        } else {
            Write-OK "端口 $port 空闲"
        }
    }
}

# ---------------------------------------------------------------- 3. 启动后端
Write-Step "3/5 启动后端（端口 $BACKEND_PORT）"
$backendRunning = $false
try {
    $h = Invoke-RestMethod -Uri "http://localhost:$BACKEND_PORT/health" -TimeoutSec 4
    $backendRunning = $true
    Write-OK "后端已在运行（version=$($h.data.version)），跳过启动"
} catch { }

if (-not $backendRunning) {
    $backendArgs = "-NoExit -Command `"Set-Location '$ROOT'; `$env:PYTHONIOENCODING='utf-8'; `$env:PYTHONUTF8='1'; & '$PY' run_server.py`""
    Start-Process -FilePath 'powershell' -ArgumentList $backendArgs -WindowStyle Normal
    Write-OK "后端窗口已打开，等待就绪..."
    Write-Host "    " -NoNewline
    if (Wait-Http "http://localhost:$BACKEND_PORT/health" 90 'backend') {
        Write-Host ""
        Write-OK "后端就绪"
    } else {
        Write-Host ""
        Write-Err2 "后端 90 秒内未就绪，请查看后端窗口的报错信息"
        exit 1
    }
}

# 报告检索模式（能让用户尽早发现"降级"）
try {
    $ready = Invoke-RestMethod -Uri "http://localhost:$BACKEND_PORT/health/ready" -TimeoutSec 5
    $mode = $ready.data.retrieval_mode
    switch ($mode) {
        'hybrid_ready'   { Write-OK "检索模式: hybrid_ready（向量 + 关键词，最佳）" }
        'vector_only'    { Write-Warn2 "检索模式: vector_only（BM25 索引缺失，建议重建知识库）" }
        'bm25_degraded'  { Write-Warn2 "检索模式: bm25_degraded（向量检索不可用，只有关键词检索）" }
        'not_loaded'     { Write-Warn2 "检索模式: not_loaded（首次提问时会加载 embedding 模型）" }
        default          { Write-Warn2 "检索模式: $mode" }
    }
} catch { }

# ---------------------------------------------------------------- 4. 启动前端
if (-not $BackendOnly) {
    Write-Step "4/5 启动前端（端口 $FRONTEND_PORT）"
    $frontendRunning = $false
    try {
        $null = Invoke-WebRequest -Uri "http://localhost:$FRONTEND_PORT" -TimeoutSec 4 -UseBasicParsing
        $frontendRunning = $true
        Write-OK "前端已在运行，跳过启动"
    } catch { }

    if (-not $frontendRunning) {
        $feDir = Join-Path $ROOT 'frontend'
        $feArgs = "-NoExit -Command `"Set-Location '$feDir'; npm run dev`""
        Start-Process -FilePath 'powershell' -ArgumentList $feArgs -WindowStyle Normal
        Write-OK "前端窗口已打开，等待就绪（Vite 首次启动较慢）..."
        Write-Host "    " -NoNewline
        if (Wait-Http "http://localhost:$FRONTEND_PORT" 90 'frontend') {
            Write-Host ""
            Write-OK "前端就绪"
        } else {
            Write-Host ""
            Write-Warn2 "前端 90 秒内未就绪，请查看前端窗口"
        }
    }
}

# ---------------------------------------------------------------- 5. 完成
Write-Step "5/5 启动完成"
Write-Host ""
Write-Host "    前端界面 : " -NoNewline; Write-Host "http://localhost:$FRONTEND_PORT" -ForegroundColor Green
Write-Host "    后端 API : " -NoNewline; Write-Host "http://localhost:$BACKEND_PORT" -ForegroundColor Green
Write-Host "    API 文档 : " -NoNewline; Write-Host "http://localhost:$BACKEND_PORT/docs" -ForegroundColor Green
Write-Host "    健康检查 : " -NoNewline; Write-Host "http://localhost:$BACKEND_PORT/health/ready" -ForegroundColor Green
Write-Host ""
Write-Host "    提示：" -ForegroundColor Gray
Write-Host "      - 聊天页在 /chat（根路径 / 是学习首页，需要登录）" -ForegroundColor Gray
Write-Host "      - 后端/前端各自的日志在它们自己的窗口里" -ForegroundColor Gray
Write-Host "      - 关闭那两个窗口即停止服务" -ForegroundColor Gray

if (-not $NoBrowser) {
    $openUrl = if ($BackendOnly) { "http://localhost:$BACKEND_PORT/docs" } else { "http://localhost:$FRONTEND_PORT/chat" }
    Start-Process $openUrl
}
