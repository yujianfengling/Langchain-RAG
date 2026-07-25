# 智慧教育RAG系统

一个基于检索增强生成(RAG)的智能教育问答系统，支持学科知识库构建、混合检索、Rerank重排序和LLM智能回答。

## 项目简介

本系统实现了一个完整的RAG（检索增强生成）流程，用于教育领域的知识问答：

1. **文档上传** - 支持多种格式文档上传（PDF、Word、TXT、Markdown等）
2. **知识库构建** - 自动文本分割、向量化、构建FAISS索引和BM25索引
3. **混合检索** - 结合向量相似度检索和BM25关键词检索
4. **Rerank重排序** - 对检索结果进行相关性重排序
5. **LLM回答** - 基于检索到的上下文，由大语言模型生成最终答案

## 技术栈

### 后端
- **Python 3.10+**
- **FastAPI** - Web框架
- **FAISS** - 向量数据库（Facebook AI Similarity Search）
- **Sentence-Transformers** - 文本向量化（BAAI/bge-small-zh-v1.5）
- **Rank-BM25** - BM25检索算法
- **DeepSeek API** - 大语言模型（支持多种LLM提供商）
- **Pydantic** - 数据验证和配置管理
- **Loguru** - 日志管理

### 前端
- **React 19** - UI框架
- **TypeScript** - 类型安全
- **Vite** - 构建工具
- **Tailwind CSS** - 样式框架
- **Zustand** - 状态管理
- **Axios** - HTTP客户端
- **Framer Motion** - 动画库

## 环境要求

- Python 3.10+
- Node.js 20+
- npm 或 yarn

## 后端启动步骤

### 1. 克隆项目
```bash
git clone <repository-url>
cd Game/git_text
```

### 2. 创建虚拟环境
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate
```

### 3. 安装依赖
```bash
pip install -r requirements.txt
```

### 4. 配置环境变量
```bash
# 复制示例配置
cp config/env.example .env

# 编辑.env文件，配置必要的环境变量
# 必须配置: DEEPSEEK_API_KEY
```

### 5. 启动后端服务
```bash
# 开发模式（自动重载）
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 或者直接运行
python main.py
```

### 6. 访问后端
- API文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health
- 根路径: http://localhost:8000/

## 前端启动步骤

### 1. 进入前端目录
```bash
cd frontend
```

### 2. 安装依赖
```bash
npm install
```

### 3. 启动开发服务器
```bash
npm run dev
```

### 4. 访问前端
- 前端地址: http://localhost:3000
- 生产构建: `npm run build`

## 环境变量配置

环境变量配置文件为 `.env`，可以从 `config/env.example` 复制：

```bash
cp config/env.example .env
```

### 主要配置项

#### LLM配置
```bash
# LLM提供商 (deepseek, ollama)
LLM_PROVIDER=deepseek

# DeepSeek配置（必须配置API Key）
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_API_BASE=https://api.deepseek.com

# Ollama配置（本地部署）
OLLAMA_MODEL=llama2
OLLAMA_BASE_URL=http://localhost:11434
```

#### RAG配置
```bash
# 检索文档数量
RAG_TOP_K=5

# 文本分割配置
CHUNK_SIZE=500
CHUNK_OVERLAP=50

# Embedding模型配置
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
EMBEDDING_DEVICE=cpu

# 知识库隔离模式
RAG_ISOLATION_MODE=directory
```

#### 服务配置
```bash
# 服务端口
SERVICE_PORT=8000

# 调试模式
DEBUG=false

# CORS配置
CORS_ORIGINS=["http://localhost:3000"]
```

## DeepSeek API Key配置说明

### 1. 获取API Key
1. 访问 [DeepSeek官网](https://platform.deepseek.com/)
2. 注册账号并登录
3. 在API密钥页面创建新的API Key
4. 复制生成的API Key

### 2. 配置API Key
在 `.env` 文件中配置：
```bash
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
```

### 3. 验证配置
启动后端服务后，访问健康检查接口：
```bash
curl http://localhost:8000/health
```

正常响应示例：
```json
{
  "status": "healthy",
  "timestamp": "2026-07-25T10:00:00",
  "version": "1.0.0",
  "checks": {
    "config": {"status": "ok"},
    "llm": {"status": "ok"},
    "rag": {"status": "ok"}
  }
}
```

## Embedding模型说明

### 模型选择
系统默认使用 `BAAI/bge-small-zh-v1.5` 模型进行文本向量化，这是一个中文优化的小型Embedding模型，具有以下特点：

- **维度**: 512维
- **语言**: 中文优化
- **大小**: 约93MB
- **性能**: 在中文语义相似度任务上表现良好

### 模型配置
在 `.env` 文件中配置：
```bash
# 使用HuggingFace模型名称
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5

# 计算设备 (cpu, cuda, mps)
EMBEDDING_DEVICE=cpu
```

### 模型下载
首次运行时，模型会自动从HuggingFace下载到本地缓存：
- Windows: `C:\Users\{用户名}\.cache\huggingface\`
- Linux/Mac: `~/.cache/huggingface/`

如果网络受限，可以手动下载模型并指定本地路径。

## RAG流程说明

### 1. 文档上传
用户通过API上传文档到指定学科目录：
```
POST /api/upload
- 文件保存到 data/subjects/{subject_id}/
- 记录文件元数据到 data/files.json
```

### 2. 知识库构建
调用构建接口，系统自动完成以下步骤：
```
POST /api/knowledge/build
1. 加载文档: 从 data/subjects/{subject_id}/ 读取所有文档
2. 文本分割: 使用递归字符分割器，chunk_size=500, chunk_overlap=50
3. 向量化: 使用Embedding模型将文本转换为向量
4. FAISS索引: 构建FAISS向量索引，保存到 data/vector_store/{subject_id}/
5. BM25索引: 构建BM25关键词索引（可选）
```

### 3. 知识库查询
```
POST /api/knowledge/query
1. 查询向量化: 将查询文本转换为向量
2. 向量检索: 从FAISS索引中检索相似文档
3. BM25检索: 使用BM25算法进行关键词检索（如果启用）
4. 混合排序: 使用RRF算法融合两种检索结果
5. 返回结果: 返回Top-K相关文档
```

### 4. RAG问答
```
POST /api/chat
1. 检索上下文: 根据用户问题检索相关文档
2. 构建Prompt: 将检索到的文档作为上下文
3. LLM生成: 调用DeepSeek API生成最终答案
4. 流式返回: 支持SSE流式响应
```

### 数据存储结构
```
data/
├── subjects/                    # 学科文档目录
│   └── {subject_id}/           # 各学科文档
├── vector_store/               # 向量存储目录
│   └── {subject_id}/           # 各学科向量索引
│       ├── index.faiss         # FAISS向量索引
│       └── index.pkl           # 文档元数据
├── files.json                  # 文件元数据
└── knowledge_graph/            # 知识图谱（可选）
```

## API接口说明

### 主要接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/api/chat` | POST | RAG增强对话（自动检索知识库） |
| `/api/chat/stream` | POST | RAG增强对话（SSE流式输出） |
| `/api/knowledge/upload` | POST | 上传文档到学科 |
| `/api/knowledge/build` | POST | 构建知识库 |
| `/api/knowledge/{subject_id}/stats` | GET | 知识库统计 |
| `/api/subjects` | GET | 获取学科列表 |
| `/api/subjects/{subject_id}` | DELETE | 删除学科 |
| `/api/models` | GET | 获取模型列表 |
| `/api/models/status` | GET | 获取模型状态 |

### 请求示例

#### RAG问答
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "question": "什么是Python？",
    "subject_id": "python_basics"
  }'
```

#### 上传文档
```bash
curl -X POST http://localhost:8000/api/knowledge/upload \
  -F "file=@document.pdf" \
  -F "subject_id=python_basics"
```

#### 构建知识库
```bash
curl -X POST http://localhost:8000/api/knowledge/build \
  -H "Content-Type: application/json" \
  -d '{"subject_id": "python_basics"}'
```

## 开发说明

### 项目结构
```
Game/git_text/
├── api/                    # FastAPI应用
│   ├── app.py             # 应用配置
│   └── routes/            # 路由定义
├── config/                # 配置管理
├── data/                  # 数据存储
├── frontend/              # React前端
├── llm/                   # LLM集成
├── rag/                   # RAG核心模块
│   ├── document.py        # 文档加载
│   ├── text_splitter.py   # 文本分割
│   ├── embedding.py       # 向量化
│   ├── vector_store.py    # FAISS向量存储
│   ├── hybrid_retriever.py # 混合检索
│   └── rag_service.py     # RAG服务
├── services/              # 业务服务
├── utils/                 # 工具函数
├── main.py                # 后端入口
└── requirements.txt       # Python依赖
```

### 测试
```bash
# 运行后端测试
python -m pytest tests/

# 测试RAG流程
python test_rag_flow.py
```

## 常见问题

### 1. 模型下载失败
如果HuggingFace模型下载失败，可以：
- 配置镜像源：设置环境变量 `HF_ENDPOINT=https://hf-mirror.com`
- 手动下载模型到本地，修改 `EMBEDDING_MODEL` 为本地路径

### 2. FAISS安装问题
Windows用户如果遇到FAISS安装问题：
```bash
pip install faiss-cpu  # CPU版本
# 或
pip install faiss-gpu  # GPU版本（需要CUDA）
```

### 3. 内存不足
如果处理大文档时内存不足：
- 减小 `CHUNK_SIZE`（默认500）
- 增加 `CHUNK_OVERLAP`（默认50）
- 使用更小的Embedding模型

### 4. API Key错误
确保在 `.env` 文件中正确配置了 `DEEPSEEK_API_KEY`，并且Key有效。

## 许可证

[MIT License](LICENSE)

## 联系方式

如有问题或建议，请提交Issue或联系开发团队。
