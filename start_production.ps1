<#
.SYNOPSIS
    智慧教育 RAG 系统 —— 生产模式启动（给老师/学生正式使用）

.DESCRIPTION
    与 start.bat（开发模式）的区别：
      - 前端先构建成静态产物，由**后端直接托管** -> 只启一个进程，不需要 npm run dev；
      - 不开启热重载，适合正式使用；
      - 默认监听 0.0.0.0，同局域网的其他电脑/手机可访问。

.PARAMETER Port
        监听端口，默认 8000。

.PARAMETER SkipBuild
        跳过前端构建（dist 已是最新时可用，省 1 分钟左右）。

.PARAMETER Open
        启动后自动打开浏览器。

.EXAMPLE
    .\start_production.ps1
    .\start_production.ps1 -SkipBuild -Open
    .\start_production.ps1 -Port 8080
#>
param(
    [int]$Port = 8000,
    [switch]$SkipBuild,
    [switch]$Open
)

$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot
Set-Location $ROOT

function Step($m) { Write-Host ""; Write-Host "=== $m ===" -ForegroundColor Cyan }
function OK($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  [X]  $m" -ForegroundColor Red }

Write-Host "==========================================================" -ForegroundColor White
Write-Host "  智慧教育 RAG 系统 - 生产模式启动" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor White

# ---------------------------------------------------------------- 1. 环境
Step "1/4 环境检查"

$PY = Join-Path $ROOT "venv\Scripts\python.exe"
if (-not (Test-Path $PY)) {
    Fail "未找到虚拟环境：$PY"
    Write-Host "        请先运行一次安装脚本：  .\install.ps1" -ForegroundColor Gray
    exit 1
}
OK "Python: $(& $PY --version 2>&1)"

if (-not (Test-Path (Join-Path $ROOT ".env"))) {
    Warn "未找到 .env —— 模型 API Key 等配置缺失，问答/出题会失败"
    Write-Host "        可从模板复制：Copy-Item config\env.example .env" -ForegroundColor Gray
} else {
    OK ".env 已存在"
}

# ---------------------------------------------------------------- 2. 前端构建
Step "2/4 前端构建产物"

$DIST = Join-Path $ROOT "frontend\dist\index.html"
if ($SkipBuild) {
    if (Test-Path $DIST) { OK "已跳过构建（使用现有 dist）" }
    else { Fail "dist 不存在，无法跳过构建"; exit 1 }
} else {
    $NPM = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $NPM) { Fail "未找到 npm，无法构建前端（装 Node.js 18+，或用 -SkipBuild）"; exit 1 }
    Push-Location (Join-Path $ROOT "frontend")
    try {
        if (-not (Test-Path "node_modules")) {
            Write-Host "  [..] 安装前端依赖（首次较慢）" -ForegroundColor Gray
            & npm install
            if ($LASTEXITCODE -ne 0) { Fail "npm install 失败"; exit 1 }
        }
        Write-Host "  [..] 构建前端（约 1 分钟）" -ForegroundColor Gray
        & npm run build
        if ($LASTEXITCODE -ne 0) { Fail "前端构建失败"; exit 1 }
    } finally { Pop-Location }
    OK "前端已构建：frontend\dist"
}

# ---------------------------------------------------------------- 3. 端口
Step "3/4 端口检查"
$busy = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
if ($busy) {
    $owner = Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue
    Fail "端口 $Port 已被占用（$($owner.ProcessName) PID=$($busy.OwningProcess)）"
    Write-Host "        换端口：.\start_production.ps1 -Port 8080" -ForegroundColor Gray
    Write-Host "        或结束它：Stop-Process -Id $($busy.OwningProcess) -Force" -ForegroundColor Gray
    exit 1
}
OK "端口 $Port 空闲"

# ---------------------------------------------------------------- 4. 启动
Step "4/4 启动服务"
$lan = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -First 1 -ExpandProperty IPAddress)

Write-Host ""
Write-Host "  本机访问 : " -NoNewline; Write-Host "http://localhost:$Port" -ForegroundColor Green
if ($lan) {
    Write-Host "  局域网   : " -NoNewline; Write-Host "http://$($lan):$Port" -ForegroundColor Green
    Write-Host "             （学生用手机连同一个 WiFi 打开这个地址）" -ForegroundColor Gray
}
Write-Host "  API 文档 : " -NoNewline; Write-Host "http://localhost:$Port/docs" -ForegroundColor Green
Write-Host ""
Write-Host "  按 Ctrl+C 停止服务。" -ForegroundColor Gray
Write-Host ""

if ($Open) { Start-Process "http://localhost:$Port" }

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
& $PY -m uvicorn api.app:app --host 0.0.0.0 --port $Port --log-level info
