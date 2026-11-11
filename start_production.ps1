<#
.SYNOPSIS
    智慧教育 RAG 系统 —— 生产模式启动（给老师/学生正式使用）

.DESCRIPTION
    前端构建成静态产物，由后端直接托管，只启一个进程。
    不开启热重载。默认监听 0.0.0.0，同局域网可访问。
    请通过 start_production.bat 启动（本机默认禁止直接运行 .ps1）。

.PARAMETER Port
    监听端口，默认 8000。

.PARAMETER Role
    student 或 teacher。留空则两端共用同一入口。

.PARAMETER SkipBuild
    跳过前端构建（dist 已是最新时可用）。

.PARAMETER Rebuild
    无论源码是否变化都重新构建前端。

.PARAMETER Open
    启动后自动打开浏览器。

.EXAMPLE
    .\start_production.bat
    .\start_production.bat -SkipBuild -Open
    .\start_production.bat -Port 8080
#>
param(
    [int]$Port = 8000,
    [ValidateSet('', 'student', 'teacher')]
    [string]$Role = '',
    [switch]$SkipBuild,
    [switch]$Rebuild,
    [switch]$Open
)

$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot
Set-Location $ROOT

# 本机用户级 PYTHONPATH 指向 D:\Lib\site-packages（pydantic 1.x）。
# 它排在项目 venv 前面，会把 pydantic 2.x 盖住，知识库接口导入即 500。
# 必须在启动 python 之前从本进程拿掉，子进程才不会继承。
Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue

function Step($m) { Write-Host ""; Write-Host "=== $m ===" -ForegroundColor Cyan }
function OK($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  [X]  $m" -ForegroundColor Red }

# dist 不存在，或源码/配置比 dist 新，才重建。避免每次启动都等一次 tsc+vite。
function Test-NeedBuild {
    $distIndex = Join-Path $ROOT "frontend\dist\index.html"
    if (-not (Test-Path $distIndex)) { return $true }
    $distTime = (Get-Item $distIndex).LastWriteTime
    $fe = Join-Path $ROOT "frontend"
    $watch = @()
    $srcDir = Join-Path $fe "src"
    if (Test-Path $srcDir) {
        $watch += Get-ChildItem $srcDir -Recurse -File -ErrorAction SilentlyContinue
    }
    foreach ($f in @("package.json", "vite.config.ts", "index.html", "tsconfig.json")) {
        $fp = Join-Path $fe $f
        if (Test-Path $fp) { $watch += (Get-Item $fp) }
    }
    foreach ($item in $watch) {
        if ($item.LastWriteTime -gt $distTime) { return $true }
    }
    return $false
}

Write-Host "==========================================================" -ForegroundColor White
Write-Host "  智慧教育 RAG 系统 - 生产模式启动" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor White

Step "1/4 环境检查"

$PY = Join-Path $ROOT "venv\Scripts\python.exe"
if (-not (Test-Path $PY)) {
    Fail "未找到虚拟环境：$PY"
    Write-Host "        请先安装依赖：powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1" -ForegroundColor Gray
    exit 1
}
# 虚拟环境的 python.exe 只是启动器。基础解释器被删掉时，它把
# "No Python at ..." 打到 stderr，在 ErrorAction=Stop 下会变成难读的远程异常。
$pyCheck = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$pyVer = & $PY -c "import sys; print(sys.version.split()[0])" 2>&1
$pyCode = $LASTEXITCODE
$ErrorActionPreference = $pyCheck
if ($pyCode -ne 0) {
    $detail = ($pyVer | Out-String).Trim()
    Fail "虚拟环境无法启动。"
    if ($detail) { Write-Host "        $detail" -ForegroundColor Gray }
    $cfg = Join-Path $ROOT "venv\pyvenv.cfg"
    if (Test-Path $cfg) {
        $homeLine = Select-String -Path $cfg -Pattern '^home\s*=' | Select-Object -First 1
        if ($homeLine) { Write-Host "        $($homeLine.Line.Trim())" -ForegroundColor Gray }
    }
    Write-Host "        pyvenv.cfg 里的基础解释器已经不在。装回同一版本的 Python 3.12，或重建 venv。" -ForegroundColor Gray
    exit 1
}
OK "Python: $pyVer"

if (-not (Test-Path (Join-Path $ROOT ".env"))) {
    Warn "未找到 .env —— 模型 API Key 等配置缺失，问答/出题会失败"
    Write-Host "        可从模板复制：Copy-Item config\env.example .env" -ForegroundColor Gray
} else {
    OK ".env 已存在"
}

Step "2/4 前端构建产物"

$DIST = Join-Path $ROOT "frontend\dist\index.html"
if ($SkipBuild) {
    if (Test-Path $DIST) { OK "已跳过构建（-SkipBuild）" }
    else { Fail "dist 不存在，无法跳过构建"; exit 1 }
} elseif ((-not $Rebuild) -and (-not (Test-NeedBuild))) {
    OK "前端没有改动，跳过构建（要强制重建加 -Rebuild）"
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

Step "3/4 端口检查"
$busy = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
if ($busy) {
    $owner = Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue
    Fail "端口 $Port 已被占用（$($owner.ProcessName) PID=$($busy.OwningProcess)）"
    Write-Host "        换端口：start_production.bat -Port 8080" -ForegroundColor Gray
    Write-Host "        或结束它：Stop-Process -Id $($busy.OwningProcess) -Force" -ForegroundColor Gray
    exit 1
}
OK "端口 $Port 空闲"

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
if ($Role) { $env:APP_ROLE = $Role } else { Remove-Item Env:\APP_ROLE -ErrorAction SilentlyContinue }
if ($Role) { Write-Host "  角色     : $Role（根路径将跳转到 /$Role）" -ForegroundColor Green }
& $PY -m uvicorn api.app:app --host 0.0.0.0 --port $Port --log-level info
