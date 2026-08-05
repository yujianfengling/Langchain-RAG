# 智慧教育RAG系统

一个基于检索增强生成(RAG)的智能教育问答系统，支持学科知识库构建、混合检索、Rerank重排序和LLM智能回答。

## 项目简介

本系统实现了一个完整的RAG（检索增强生成）流程，用于教育领域的知识问答：

1. **文档上传** - 支持多种格式文档上传（PDF、DOCX、TXT、Markdown、JPG、JPEG、PNG、WEBP）
2. **知识库构建** - 自动文本分割、向量化、构建FAISS索引和BM25索引
3. **混合检索** - 结合向量相似度检索和BM25关键词检索
4. **Rerank重排序** - 对检索结果进行相关性重排序
5. **LLM回答** - 基于检索到的上下文，由大语言模型生成最终答案

## 核心功能

### PDF 多模态处理
- **文本提取** - 提取 PDF 中的纯文本内容
- **公式识别** - 识别并提取数学公式，生成 LaTeX 表示
- **图片提取** - 提取 PDF 中的图片，保存到本地并记录路径
- **表格识别** - 识别表格结构，转换为 Markdown 格式

### 多模态文档支持
- **PDF 文本** - 标准文本内容
- **数学公式** - LaTeX 格式公式
- **图片** - JPG/JPEG/PNG/WEBP 格式图片
- **表格** - 结构化表格数据

### 智能检索
- **FAISS 向量检索** - 基于语义相似度的向量检索
- **BM25 关键词检索** - 基于关键词的精确匹配
- **混合检索** - 结合向量和关键词检索的优势
- **Rerank 重排序** - 对检索结果进行相关性重排序

### 视觉理解
- **Qwen Vision** - 接入阿里云通义千问视觉模型
- **图片描述** - 自动生成图片内容描述
- **OCR 文字提取** - 从图片中提取文字
- **持久化缓存** - Vision 结果缓存，30天有效期

### 可靠性保障
- **自动重试** - Vision API 失败自动重试（3次指数退避）
- **超时控制** - 可配置超时时间（默认60秒）
- **优雅降级** - API 失败时不阻断整体流程
- **错误处理** - 区分超时、认证、配额等错误类型

## 技术栈

### 后端
- **Python 3.10+**
- **FastAPI** - Web框架
- **FAISS** - 向量数据库（Facebook AI Similarity Search）
- **Sentence-Transformers** - 文本向量化（BAAI/bge-small-zh-v1.5）
- **Rank-BM25** - BM25检索算法
- **OpenAI SDK** - 统一LLM调用接口
- **Pillow** - 图像处理
- **pytesseract** - OCR文字提取
- **Pydantic** - 数据验证和配置管理
- **Loguru** - 日志管理

### 前端
- **React 18.2.0** - UI框架
- **TypeScript** - 类型安全
- **Vite** - 构建工具
- **Tailwind CSS** - 样式框架
- **Zustand** - 状态管理
- **Axios** - HTTP客户端
- **Framer Motion** - 动画库

## 模型配置

系统支持三种文本模型和一种视觉模型，**文本模型内置API密钥，clone后无需额外配置即可运行**：

| 模型 | 提供商 | 默认 | API密钥状态 |
|------|--------|------|-------------|
| **小米MiMo** | 小米 | 默认文本模型 | 已内置 |
| **DeepSeek** | DeepSeek | 可切换 | 已内置 |
| **通义千问** | 阿里云 | 可切换 | 需用户配置 |
| **通义千问视觉** | 阿里云 | 图片理解 | 需用户配置 |

**默认模型：小米MiMo**

### 模型优先级

API密钥读取优先级：**环境变量(.env) > 内置默认值(config/models.py)**

如需覆盖默认配置，创建 `.env` 文件：
```bash
cp config/env.example .env
# 编辑 .env 文件
```

### 模型API端点

| 模型 | Base URL | 模型名称 |
|------|----------|----------|
| MiMo | `https://token-plan-cn.xiaomimimo.com/v1` | mimo-v2.5-pro |
| DeepSeek | `https://api.deepseek.com` | deepseek-chat |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | qwen-plus |
| 通义千问视觉 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | qwen-vl-plus |

## 环境要求

- Python 3.10+
- Node.js 16+
- npm 或 yarn

## 快速开始

### 方式一：一键安装脚本

**Linux/macOS/WSL:**
```bash
bash install.sh
```

**Windows PowerShell:**
```powershell
.\install.ps1
```

### 方式二：手动安装

#### 1. 克隆项目
```bash
git clone <repository-url>
cd Game/git_text
```

#### 2. 创建虚拟环境并安装依赖
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

pip install -r requirements.txt
```

#### 3. 启动后端服务
```bash
python run_server.py
```

#### 4. 启动前端
```bash
cd frontend
npm install
npm run dev
```

#### 5. 访问系统
- **前端界面**: http://localhost:3000
- **后端API**: http://localhost:8000
- **API文档**: http://localhost:8000/docs

> **注意**: 系统内置了MiMo和DeepSeek的API密钥，无需创建`.env`文件即可使用这两个模型。通义千问需要用户自行配置API密钥。

## 前端交互

### 模型选择

在AI教师聊天页面，用户可以通过下拉菜单切换AI模型：

- **小米MiMo** (默认) - 小米自研大模型
- **DeepSeek** - DeepSeek大模型
- **通义千问** - 阿里云大模型
- **通义千问视觉** - 阿里云视觉大模型（用于图片理解）

用户无需理解模型配置细节，直接在聊天界面选择即可。

### 主要功能

- **学科管理** - 创建、查看、删除学科
- **知识库构建** - 上传文档、构建向量索引
- **AI问答** - 基于知识库的智能问答
- **模型切换** - 实时切换不同AI模型

## API接口说明

### 主要接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/api/chat` | POST | RAG增强对话 |
| `/api/chat/stream` | POST | RAG增强对话（SSE流式） |
| `/api/knowledge` | GET | 获取知识库列表 |
| `/api/knowledge/build` | POST | 构建知识库 |
| `/api/knowledge/{subject_id}` | DELETE | 删除知识库 |
| `/api/knowledge/{subject_id}/stats` | GET | 知识库统计 |
| `/api/knowledge/upload` | POST | 上传文档 |
| `/api/knowledge/{subject_id}/documents` | GET | 获取学科文档列表 |
| `/api/knowledge/{subject_id}/documents/{filename}` | DELETE | 删除学科文档 |
| `/api/knowledge/{subject_id}/rebuild` | POST | 重新构建知识库 |
| `/api/subjects` | GET | 获取学科列表 |
| `/api/subjects/{subject_id}` | GET | 获取单个学科信息 |
| `/api/subjects` | POST | 创建新学科 |
| `/api/subjects/{subject_id}` | DELETE | 删除学科 |
| `/api/models` | GET | 获取当前模型信息 |
| `/api/models/status` | GET | 获取模型状态 |
| `/api/models/available` | GET | 获取可用模型列表 |
| `/api/models/switch` | POST | 切换模型提供商 |
| `/api/education/batch-grading` | POST | 批量批改作业 |
| `/api/education/analyze` | POST | 教育数据分析 |

### 请求示例

#### RAG问答（默认MiMo模型）
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "question": "什么是RAG？",
    "subject_id": "ai_basics",
    "model": "mimo"
  }'
```

#### 切换DeepSeek模型
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "question": "解释FAISS的作用",
    "subject_id": "ai_basics",
    "model": "deepseek"
  }'
```

#### 上传文档
```bash
curl -X POST http://localhost:8000/api/knowledge/upload \
  -F "file=@document.pdf" \
  -F "subject_id=ai_basics"
```

#### 构建知识库
```bash
curl -X POST http://localhost:8000/api/knowledge/build \
  -H "Content-Type: application/json" \
  -d '{"subject_id": "ai_basics"}'
```

## 项目结构

```
Game/git_text/
├── api/                    # FastAPI应用
│   ├── app.py             # 应用配置
│   ├── routes/            # 路由定义
│   └── schemas/           # 数据模型
├── config/                # 配置管理
│   ├── models.py          # 模型配置（内置API密钥）
│   ├── settings.py        # 系统配置
│   └── env.example        # 环境变量示例
├── data/                  # 数据存储
├── frontend/              # React前端
│   ├── src/
│   │   ├── components/    # 组件
│   │   ├── pages/         # 页面
│   │   ├── api/           # API调用
│   │   └── types/         # 类型定义
│   └── package.json
├── llm/                   # LLM集成
├── rag/                   # RAG核心模块
│   └── multimodal/        # 多模态处理模块
├── services/              # 业务服务
│   ├── model_service.py   # 模型服务（ModelProvider架构）
│   └── multimodal_service.py # 多模态服务
├── utils/                 # 工具函数
├── run_server.py          # 后端入口
├── requirements.txt       # Python依赖
├── install.sh             # Linux/macOS安装脚本
└── install.ps1            # Windows安装脚本
```

## 多模态处理

### 支持的文档类型

| 类型 | 说明 | 输出格式 |
|------|------|----------|
| **文本** | PDF 中的纯文本内容 | 原始文本 |
| **公式** | 数学公式和方程式 | LaTeX 格式 |
| **图片** | 嵌入的图片资源 | 图片文件路径 |
| **表格** | 结构化表格数据 | Markdown 表格 |

### 支持图片格式
- JPG/JPEG
- PNG
- WEBP

### PDF 处理流程

用户上传 PDF
↓
PDFAnalyzer 识别元素类型
↓
文本提取 → 公式识别 → 图片提取 → 表格识别
↓
DocumentAdapter 统一转换
↓
MultimodalEnricher 增强
↓
Embedding 向量化
↓
FAISS + BM25 混合索引
↓
RAG 智能问答

### Vision 处理流程

提取的图片
↓
Qwen Vision 模型理解
↓
生成图片描述 + OCR 文字提取
↓
持久化缓存（30天）
↓
Embedding 向量化
↓
混合检索

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

### 3. RAG问答
```
POST /api/chat
1. 检索上下文: 根据用户问题检索相关文档
2. 构建Prompt: 将检索到的文档作为上下文
3. LLM生成: 调用选定的AI模型生成最终答案
4. 返回结果: 返回答案和引用来源
```

## 模型架构

### ModelProvider系统

系统采用统一的ModelProvider架构，所有模型通过OpenAI兼容接口调用：

```python
# config/models.py - 模型配置
MODEL_CONFIG = {
    "mimo": {"name": "小米MiMo", "base_url": "...", "model_name": "mimo-v2.5-pro"},
    "deepseek": {"name": "DeepSeek", "base_url": "...", "model_name": "deepseek-chat"},
    "qwen": {"name": "通义千问", "base_url": "...", "model_name": "qwen-plus"},
    "qwen-vision": {"name": "通义千问视觉", "base_url": "...", "model_name": "qwen-vl-plus"},
}
```

### 前端模型选择

前端ChatBox组件提供模型选择下拉菜单：
- 默认选中 `mimo`
- 用户可切换到 `deepseek` 或 `qwen`
- 选择后立即生效，下次请求使用新模型
- 图片理解时自动使用 `qwen-vision` 视觉模型

## 常见问题

### 1. 模型下载失败
如果HuggingFace模型下载失败，可以：
- 配置镜像源：设置环境变量 `HF_ENDPOINT=https://hf-mirror.com`
- 手动下载模型到本地，修改 `EMBEDDING_MODEL` 为本地路径

### 2. FAISS安装问题
Windows用户如果遇到FAISS安装问题：
```bash
pip install faiss-cpu  # CPU版本
```

### 3. 内存不足
如果处理大文档时内存不足：
- 减小 `CHUNK_SIZE`（默认500）
- 增加 `CHUNK_OVERLAP`（默认50）

### 4. 通义千问无法使用
通义千问需要用户自行配置API密钥。创建 `.env` 文件：
```bash
cp config/env.example .env
# 编辑 .env 文件，添加 DASHSCOPE_API_KEY
```

## 许可证

[MIT License](LICENSE)

## 联系方式

如有问题或建议，请提交Issue或联系开发团队。
