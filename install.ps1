<#
智慧教育RAG系统 - Windows一键安装脚本【根目录版，适配本仓库结构】
仓库没有backend文件夹，根目录就是后端代码
#>
param(
    [switch]$SkipFrontend,
    [switch]$SkipModel
)

# 注意：不能用 "Stop"。pip/npm/模型下载都会往 stderr 写警告和进度条，
# PS 5.1 会把原生命令的 stderr 包装成 ErrorRecord，配合 "Stop" 会在下载中途直接终止脚本。
# 这里改用 "Continue"，关键步骤靠检查 $LASTEXITCODE 判断成败。
$ErrorActionPreference = "Continue"

function Write-Step {
    param([string]$Message)
    Write-Host "`n=== $Message ===" -ForegroundColor Blue
}

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

# ========== 校验：根目录必须找到config文件夹 ==========
if (-not (Test-Path "config")) {
    Write-Error-Custom "当前目录没有找到 config 文件夹！"
    Write-Host "✅ 正确用法：install.ps1 放在仓库根目录（和frontend、config同级）"
    pause
    exit 1
}

# 开始安装
Write-Step "智慧教育RAG系统 - Windows一键安装脚本"
Write-Host "本脚本将自动完成以下操作："
Write-Host "1. 检查系统环境（Python 3.8+, Node.js 16+）"
Write-Host "2. 创建虚拟环境"
Write-Host "3. 安装Python依赖"
Write-Host "4. 创建必要目录"
Write-Host "5. 初始化前端"
Write-Host "6. 下载Embedding模型"
Write-Host ""

# 步骤1：检查Python
Write-Step "步骤1/6：检查Python环境"

try {
    $pythonVersion = python --version 2>&1
    Write-Info "找到Python: $pythonVersion"
} catch {
    Write-Error-Custom "未找到Python！请先安装Python 3.8或更高版本。"
    Write-Info "推荐使用conda或官方安装包：https://www.python.org/downloads/"
    Write-Info "安装时勾选 'Add python.exe to PATH'，或先激活 conda 环境再运行本脚本。"
    exit 1
}

# 防止 python 解析到 Windows 应用商店的占位程序（运行后弹商店、不干活）
if ("$pythonVersion" -notmatch '\d+\.\d+') {
    Write-Error-Custom "python 命令无效（疑似 Windows 应用商店占位程序）。"
    Write-Info "请安装真正的 Python 并加入 PATH，或先激活 conda 环境。"
    exit 1
}

# 步骤2：检查Node.js
Write-Step "步骤2/6：检查Node.js环境"

if (-not $SkipFrontend) {
    try {
        $nodeVersion = node --version 2>&1
        Write-Info "找到Node.js: $nodeVersion"

        $npmVersion = npm --version 2>&1
        Write-Info "找到npm: $npmVersion"
    } catch {
        Write-Warn "未找到Node.js。将跳过前端安装。"
        $SkipFrontend = $true
    }
} else {
    Write-Warn "跳过Node.js检查。"
}

# 步骤3：创建虚拟环境并安装依赖
Write-Step "步骤3/6：创建虚拟环境并安装Python依赖"

if (-not (Test-Path "venv")) {
    Write-Info "创建虚拟环境..."
    python -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "虚拟环境创建失败（退出码 $LASTEXITCODE）"
        exit 1
    }
    Write-Info "虚拟环境创建成功"
} else {
    Write-Info "虚拟环境已存在，跳过创建。"
}

# 激活虚拟环境
Write-Info "激活虚拟环境..."
& .\venv\Scripts\Activate.ps1

# 升级pip
Write-Info "升级pip..."
python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Warn "pip升级失败（退出码 $LASTEXITCODE），继续安装依赖。"
}

# 安装依赖（加清华源）
Write-Info "安装Python依赖（这可能需要几分钟）..."
if (Test-Path "requirements.txt") {
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Python依赖安装失败（退出码 $LASTEXITCODE）"
        exit 1
    }
    Write-Info "Python依赖安装完成"
} else {
    Write-Error-Custom "未找到requirements.txt文件！"
    exit 1
}

# 步骤4：创建必要目录
Write-Step "步骤4/6：创建必要目录"

$directories = @(
    "data\vector_store",
    "data\subjects",
    "data\knowledge_graph",
    "data\cache",
    "data\bm25_index",
    "logs",
    "uploads"
)

foreach ($dir in $directories) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Info "创建目录: $dir"
    }
}

Write-Info "目录结构创建完成"

# 步骤5：安装前端依赖（frontend就在当前同级）
Write-Step "步骤5/6：安装前端依赖"

if (-not $SkipFrontend) {
    $frontendPath = Join-Path $PSScriptRoot "frontend"
    if (Test-Path $frontendPath) {
        Set-Location $frontendPath

        Write-Info "安装前端依赖（这可能需要几分钟）..."
        npm config set registry https://registry.npmmirror.com
        npm install
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "前端依赖安装失败（退出码 $LASTEXITCODE），但可以继续使用后端。"
        } else {
            Write-Info "前端依赖安装完成"
        }

        Set-Location $PSScriptRoot
    } else {
        Write-Warn "未找到frontend目录，跳过前端安装。"
    }
} else {
    Write-Warn "跳过前端安装。"
}

# 步骤6：下载Embedding模型【修复路径转义】
Write-Step "步骤6/6：下载Embedding模型"

if (-not $SkipModel) {
    Write-Info "检查Embedding模型..."

    $localDir = "models\bge-small-zh-v1.5"
    if ((Test-Path $localDir) -and (Get-ChildItem $localDir -ErrorAction SilentlyContinue)) {
        Write-Info "本地模型已存在: $localDir"
    } else {
        Write-Info "下载Embedding模型: BAAI/bge-small-zh-v1.5"
        Write-Info "首次下载可能需要几分钟..."

        # 国内网络直连 huggingface.co 会卡死/失败，走 HF 镜像
        $env:HF_ENDPOINT = "https://hf-mirror.com"

        # 把Windows反斜杠转为python识别的正斜杠
        $pyPath = $localDir -replace "\\", "/"
        python -c "
from sentence_transformers import SentenceTransformer
import os
os.makedirs('$pyPath', exist_ok=True)
model = SentenceTransformer('BAAI/bge-small-zh-v1.5')
model.save('$pyPath')
print('模型下载完成')
"

        if ($LASTEXITCODE -eq 0) {
            Write-Info "Embedding模型下载完成"
        } else {
            Write-Warn "Embedding模型下载失败，将在首次使用时自动下载。"
        }
    }
} else {
    Write-Warn "跳过模型下载。"
}

# 验证配置
Write-Step "验证配置"
python -c "
import sys
sys.path.insert(0, '.')
try:
    from config.settings import *
    print('配置加载成功')
except Exception as e:
    print(f'配置加载失败: {e}')
    sys.exit(1)

try:
    from config.models import get_default_model, list_models
    default = get_default_model()
    models = list_models()
    print(f'默认模型: {default}')
    print('可用模型:', [m['name'] for m in models])
except Exception as e:
    print(f'模型配置加载失败: {e}')
    sys.exit(1)
"

# 完成提示
Write-Step "安装完成！"
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  安装完成！" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "接下来，你可以："
Write-Host ""
Write-Host "1. 激活虚拟环境："
Write-Host "   .\venv\Scripts\Activate.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "2. 启动后端服务（根目录执行）："
Write-Host "   python main.py" -ForegroundColor Yellow
Write-Host ""
Write-Host "3. 启动前端开发服务器（新开窗口）："
Write-Host "   cd frontend" -ForegroundColor Yellow
Write-Host "   npm run dev" -ForegroundColor Yellow
Write-Host ""
Write-Host "默认访问地址："
Write-Host "  - 后端API: http://localhost:8000" -ForegroundColor Cyan
Write-Host "  - 前端界面: http://localhost:3000" -ForegroundColor Cyan
Write-Host ""
Write-Host "如需覆盖默认API Key，创建 .env 文件："
Write-Host "  Copy-Item config\env.example .env" -ForegroundColor Yellow
Write-Host "  # 编辑 .env 文件填入密钥" -ForegroundColor Yellow
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
