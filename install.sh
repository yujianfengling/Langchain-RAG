#!/bin/bash
# 智慧教育RAG系统 - 一键安装脚本
# 支持 Linux/macOS/WSL
# 使用方法：bash install.sh

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # 恢复默认颜色

# 日志函数
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "\n${BLUE}=== $1 ===${NC}"
}

# 检查命令是否存在
check_command() {
    if ! command -v "$1" &> /dev/null; then
        return 1
    fi
    return 0
}

# 获取版本号
get_version() {
    local cmd="$1"
    local version_cmd="$2"
    eval "$version_cmd" 2>/dev/null | head -1
}

# 开始安装
log_step "智慧教育RAG系统 - 一键安装脚本"
echo "本脚本将自动完成以下操作："
echo "1. 检查系统环境（Python 3.8+, Node.js 16+, npm）"
echo "2. 创建虚拟环境（推荐）"
echo "3. 安装Python依赖"
echo "4. 创建必要目录"
echo "5. 初始化前端"
echo "6. 下载Embedding模型"
echo "7. 运行验证测试"
echo ""

# 步骤1：检查Python环境
log_step "步骤1/7：检查Python环境"

if check_command python3; then
    PYTHON=python3
    PIP=pip3
elif check_command python; then
    PYTHON=python
    PIP=pip
else
    log_error "未找到Python！请先安装Python 3.8或更高版本。"
    log_info "推荐使用conda或pyenv管理Python版本。"
    exit 1
fi

PYTHON_VERSION=$($PYTHON --version 2>&1)
log_info "找到Python: $PYTHON_VERSION"

# 检查Python版本是否 >= 3.8
PYTHON_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
    log_error "Python版本过低！需要3.8或更高版本，当前版本: $PYTHON_MAJOR.$PYTHON_MINOR"
    exit 1
fi

log_info "Python版本检查通过 ✓"

# 步骤2：检查Node.js环境
log_step "步骤2/7：检查Node.js环境"

if ! check_command node; then
    log_warn "未找到Node.js。"
    log_info "是否要跳过前端安装？(y/n)"
    read -r skip_frontend
    if [ "$skip_frontend" = "y" ] || [ "$skip_frontend" = "Y" ]; then
        SKIP_FRONTEND=true
        log_warn "跳过前端安装。"
    else
        log_error "请先安装Node.js 16或更高版本。"
        log_info "推荐使用nvm管理Node.js版本: https://github.com/nvm-sh/nvm"
        exit 1
    fi
else
    NODE_VERSION=$(node --version)
    log_info "找到Node.js: $NODE_VERSION"
    
    if ! check_command npm; then
        log_error "找到Node.js但未找到npm！"
        exit 1
    fi
    
    NPM_VERSION=$(npm --version)
    log_info "找到npm: $NPM_VERSION"
fi

# 步骤3：创建虚拟环境并安装依赖
log_step "步骤3/7：创建虚拟环境并安装Python依赖"

# 创建虚拟环境
if [ ! -d "venv" ]; then
    log_info "创建虚拟环境..."
    $PYTHON -m venv venv
    log_info "虚拟环境创建成功 ✓"
else
    log_info "虚拟环境已存在，跳过创建。"
fi

# 激活虚拟环境
log_info "激活虚拟环境..."
source venv/bin/activate

# 升级pip
log_info "升级pip..."
pip install --upgrade pip

# 安装依赖
log_info "安装Python依赖（这可能需要几分钟）..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    log_info "Python依赖安装完成 ✓"
else
    log_error "未找到requirements.txt文件！"
    exit 1
fi

# 步骤4：创建必要目录
log_step "步骤4/7：创建必要目录"

directories=(
    "data/vector_store"
    "data/subjects"
    "data/knowledge_graph"
    "data/cache"
    "data/bm25_index"
    "logs"
    "uploads"
)

for dir in "${directories[@]}"; do
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        log_info "创建目录: $dir"
    fi
done

log_info "目录结构创建完成 ✓"

# 步骤5：安装前端依赖
log_step "步骤5/7：安装前端依赖"

if [ "$SKIP_FRONTEND" = true ]; then
    log_warn "跳过前端安装。"
else
    if [ -d "frontend" ]; then
        cd frontend
        
        log_info "安装前端依赖（这可能需要几分钟）..."
        npm install
        
        if [ $? -eq 0 ]; then
            log_info "前端依赖安装完成 ✓"
        else
            log_warn "前端依赖安装失败，但可以继续使用后端。"
        fi
        
        cd ..
    else
        log_warn "未找到frontend目录，跳过前端安装。"
    fi
fi

# 步骤6：下载Embedding模型
log_step "步骤6/7：下载Embedding模型"

log_info "检查Embedding模型..."
EMBEDDING_MODEL="BAAI/bge-small-zh-v1.5"

# 检查是否已有本地模型
LOCAL_DIR="models/bge-small-zh-v1.5"
if [ -d "$LOCAL_DIR" ] && [ "$(ls -A $LOCAL_DIR 2>/dev/null)" ]; then
    log_info "本地模型已存在: $LOCAL_DIR"
else
    log_info "下载Embedding模型: $EMBEDDING_MODEL"
    log_info "首次下载可能需要几分钟..."
    
    # 使用huggingface-cli下载
    if check_command huggingface-cli; then
        huggingface-cli download "$EMBEDDING_MODEL" --local-dir "$LOCAL_DIR" --resume-download
    else
        # 使用Python下载
        $PYTHON -c "
from sentence_transformers import SentenceTransformer
import os
os.makedirs('$LOCAL_DIR', exist_ok=True)
model = SentenceTransformer('$EMBEDDING_MODEL')
model.save('$LOCAL_DIR')
print('模型下载完成')
"
    fi
    
    if [ $? -eq 0 ]; then
        log_info "Embedding模型下载完成 ✓"
    else
        log_warn "Embedding模型下载失败，将在首次使用时自动下载。"
    fi
fi

# 步骤7：运行验证测试
log_step "步骤7/7：运行验证测试"

log_info "运行配置验证..."
$PYTHON -c "
import sys
sys.path.insert(0, '.')
try:
    from config.settings import *
    print('✓ 配置加载成功')
except Exception as e:
    print(f'✗ 配置加载失败: {e}')
    sys.exit(1)

try:
    from config.models import get_default_model, list_models
    default = get_default_model()
    models = list_models()
    print(f'✓ 默认模型: {default}')
    print(f'✓ 可用模型: {[m[\"name\"] for m in models]}')
except Exception as e:
    print(f'✗ 模型配置加载失败: {e}')
    sys.exit(1)
"

if [ $? -eq 0 ]; then
    log_info "配置验证通过 ✓"
else
    log_warn "配置验证失败，请检查配置。"
fi

# 完成
log_step "安装完成！"
echo ""
echo "=========================================="
echo "  安装完成！"
echo "=========================================="
echo ""
echo "接下来，你可以："
echo ""
echo "1. 启动后端服务："
echo "   source venv/bin/activate"
echo "   python main.py"
echo ""
echo "2. 启动前端开发服务器（可选）："
echo "   cd frontend"
echo "   npm run dev"
echo ""
echo "3. 或者使用一键启动脚本（如果存在）："
echo "   ./start.sh"
echo ""
echo "默认访问地址："
echo "  - 后端API: http://localhost:8000"
echo "  - 前端界面: http://localhost:5173"
echo ""
echo "如需配置API Key，创建 .env 文件："
echo "  cp config/env.example .env"
echo "  # 编辑 .env 文件"
echo ""
echo "=========================================="
