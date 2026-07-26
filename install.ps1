# 智慧教育RAG系统 - Windows一键安装脚本
# 使用方法：在PowerShell中运行 .\install.ps1

param(
    [switch]$SkipFrontend,
    [switch]$SkipModel
)

$ErrorActionPreference = "Stop"

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

# 安装依赖
Write-Info "安装Python依赖（这可能需要几分钟）..."
if (Test-Path "requirements.txt") {
    pip install -r requirements.txt
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

# 步骤5：安装前端依赖
Write-Step "步骤5/6：安装前端依赖"

if (-not $SkipFrontend) {
    if (Test-Path "frontend") {
        Set-Location frontend
        
        Write-Info "安装前端依赖（这可能需要几分钟）..."
        try {
            npm install
            Write-Info "前端依赖安装完成"
        } catch {
            Write-Warn "前端依赖安装失败，但可以继续使用后端。"
        }
        
        Set-Location ..
    } else {
        Write-Warn "未找到frontend目录，跳过前端安装。"
    }
} else {
    Write-Warn "跳过前端安装。"
}

# 步骤6：下载Embedding模型
Write-Step "步骤6/6：下载Embedding模型"

if (-not $SkipModel) {
    Write-Info "检查Embedding模型..."
    
    $localDir = "models\bge-small-zh-v1.5"
    if ((Test-Path $localDir) -and (Get-ChildItem $localDir -ErrorAction SilentlyContinue)) {
        Write-Info "本地模型已存在: $localDir"
    } else {
        Write-Info "下载Embedding模型: BAAI/bge-small-zh-v1.5"
        Write-Info "首次下载可能需要几分钟..."
        
        python -c "
from sentence_transformers import SentenceTransformer
import os
os.makedirs('$localDir', exist_ok=True)
model = SentenceTransformer('BAAI/bge-small-zh-v1.5')
model.save('$localDir')
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
    print(f'可用模型: {[m[\"name\"] for m in models]}')
except Exception as e:
    print(f'模型配置加载失败: {e}')
    sys.exit(1)
"

# 完成
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
Write-Host "2. 启动后端服务："
Write-Host "   python main.py" -ForegroundColor Yellow
Write-Host ""
Write-Host "3. 启动前端开发服务器（可选）："
Write-Host "   cd frontend" -ForegroundColor Yellow
Write-Host "   npm run dev" -ForegroundColor Yellow
Write-Host ""
Write-Host "默认访问地址："
Write-Host "  - 后端API: http://localhost:8000" -ForegroundColor Cyan
Write-Host "  - 前端界面: http://localhost:5173" -ForegroundColor Cyan
Write-Host ""
Write-Host "如需覆盖默认API Key，创建 .env 文件："
Write-Host "  Copy-Item config\env.example .env" -ForegroundColor Yellow
Write-Host "  # 编辑 .env 文件" -ForegroundColor Yellow
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
