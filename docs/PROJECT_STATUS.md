# 项目状态文档（PROJECT_STATUS）

> **本文件是当前项目状态的唯一权威文档**，后续接手 Agent 应首先阅读本文件。
> 更新日期：2026-08-06。
> 本文件只记录经真实验证的状态，不包含任何真实 API Key。

---

## 项目简介

《智慧教育多模态 RAG 系统》——面向教育领域的检索增强生成（RAG）问答系统。支持知识库构建、FAISS+BM25 混合检索、RRF 融合、Rerank 重排序、LLM 智能问答，以及 PDF 多模态（文本/公式/表格/图片）解析与 Vision 图片理解。

当前仓库位于 `E:\WORKBUDDY\Game\git_text`（git 远端：`github.com/yujianfengling/Langchain-RAG`，分支 master，HEAD 为 `9801d90`）。

## 当前技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11 · FastAPI · Uvicorn |
| 前端 | React 18 · TypeScript · Vite 5 · Ant Design · ECharts 6 |
| 向量检索 | FAISS（IndexFlatL2）· `BAAI/bge-small-zh-v1.5`（本地 Embedding，512 维） |
| 关键词检索 | rank-bm25 |
| 混合融合 | RRF（Reciprocal Rank Fusion，k=60，等权） |
| 重排序 | 轻量实现（复用 Embedding 做余弦重排，无独立 Reranker 模型） |
| LLM | 小米 MiMo（OpenAI 兼容接口）· DeepSeek · 通义千问（可切换） |
| Vision | 通义千问 qwen-vl-plus |
| PDF 解析 | PyMuPDF + pdfplumber + Pillow + pytesseract |

## 当前核心架构

```
前端 (React, 3000端口) ──/api 代理──> FastAPI 后端 (8000端口)
                                        │
                     ┌──────────────────┼──────────────────┐
                     │                  │                  │
                services/          chat_service.py      rag/
                业务服务层          (问答编排)           RAG核心层
                     │                  │                  │
                     │                  │       ├─ loaders/(PDF/文本/图片加载)
                     │                  │       ├─ multimodal/pdf/(Analyzer→Detector→Extractor→Adapter)
                     │                  │       ├─ enrichment/(Vision增强+缓存)
                     │                  │       ├─ embedding.py(本地模型,auto加载)
                     │                  │       ├─ vector_store.py(FAISS)
                     │                  │       ├─ bm25_retriever.py
                     │                  │       └─ hybrid_retriever.py(RRF融合+诊断)
                     │                  │
                     │        config/ (settings·models·rag·path·log·service)
                     │                  │
              data/ subjects/ · vector_store/(FAISS) · bm25_index/ · cache/
```

配置优先级：环境变量 > `.env` > 代码默认值。密钥仅存于 `.env`（未被 Git 跟踪），代码中不含任何凭据。

## 当前默认模型

| 用途 | Provider | Model |
|---|---|---|
| Chat | mimo | mimo-v2.5-pro |
| Vision | qwen-vision | qwen-vl-plus |

- Chat 与 Vision 配置独立，互不影响。
- Chat 支持请求级模型切换（`model` 参数）；Vision 为自动选型（qwen-vision 优先，无独立切换 UI）。
- 模型可配置变量：`LLM_MODEL`、`MIMO_MODEL`、`MIMO_BASE_URL`、`DEEPSEEK_MODEL`、`DASHSCOPE_MODEL`、`DASHSCOPE_VISION_MODEL` 等（见 `.env.example`）。

## 已验证可用功能

经端到端验收（2026-08-06，使用临时验收资料，非历史大学物理数据）：

- [x] 后端启动与健康检查（`/health`、`/health/ready`）
- [x] 普通 AI 问答（不依赖知识库，Chat 真实调用成功）
- [x] 新建 / 删除知识库（删除会清理 FAISS、BM25 与上传文件，恢复干净状态）
- [x] Markdown / PDF 上传与解析
- [x] PDF 文本解析
- [x] 公式检测（`type=formula, modality=formula`）
- [x] 表格检测（内容进入可检索文本）
- [x] 图片检测与提取（`image_path` 有效、Base64 Data URL 正确）
- [x] Embedding 本地缓存自动加载（`EMBEDDING_LOAD_MODE=auto`，source=local_cache，dim=512，不联网）
- [x] FAISS 索引构建与持久化
- [x] BM25 索引构建与持久化
- [x] RRF 混合检索（`retrieval_mode=hybrid_ready`，结果含 vector+bm25 融合）
- [x] RAG 问答（回答引用知识库内容，sources 正确返回）
- [x] 知识库持久化与重启后加载（重启不重建即可检索）
- [x] 前端生产构建（`npm run build` 通过）
- [x] 健康检查状态区分（`/health/ready` 返回 hybrid_ready / bm25_degraded / not_loaded / unavailable）
- [x] **Chat 真流式**（SSE + stream=True，逐块输出，支持 model 参数、停止生成、TTFT/耗时统计）
- [x] **RAG 参考来源结构化显示**（统一 `{id, file_name, page, type}`，前端列表展示，去重限 5 条）
- [x] **Knowledge Graph 后台生成**（异步线程池，不阻塞事件循环；防重复提交；状态查询接口）
- [x] **Knowledge Graph 原子保存与进度**（tmp→校验→rename，失败保留旧图谱；进度 current/total 展示）
- [x] **Knowledge Graph 数据一致性**（直接复用 RAG 已切分 chunk，与知识库完全一致；`GRAPH_MODEL` 独立配置）

## 当前未完全通过功能

- [ ] **Qwen Vision 真实图片理解**：实现与 Mock 链路已验证，但真实调用返回 401 `invalid_api_key`（`.env` 中 `DASHSCOPE_API_KEY` 无效），需用户配置有效 Key 后重验 1 次
- [ ] **模型切换接口**：`POST /api/models/switch` 仅返回成功，不真正改变全局状态（实际生效依赖每次请求的 `model` 参数）
- [ ] **一键启动脚本**：`start.bat` / `setup.bat` / `stop.bat` 未实现（仅有 `install.ps1` / `install.sh` 安装脚本）
- [ ] **Reranker**：为轻量实现（复用 Embedding 余弦重排），非独立 Rerank 模型
- [ ] **知识图谱端到端人工验收**：后台生成链路已实现并通过 Mock 验证，尚未在正式知识库上人工验收（需用户点"生成知识图谱"确认）

## 测试状态

- 后端安全测试：**252 passed / 0 failed / 0 skipped**（`pytest tests/ --ignore=tests/test_chat_service.py`，约 10s）
- 前端构建：`npm run build` **通过**（tsc + vite）
- 测试说明：`tests/test_chat_service.py` 中 `test_chat_with_ai_response_structure` 会真实调用 DeepSeek 付费 API，已从默认测试命令排除；全部网络相关测试使用 Mock。

## 启动方法

```powershell
# 1. 后端（终端一）
cd E:\WORKBUDDY\Game\git_text
C:\Users\21508\AppData\Local\Programs\Python\Python311\python.exe run_server.py
#   或：C:\Users\21508\AppData\Local\Programs\Python\Python311\python.exe -m uvicorn api.app:app --host 0.0.0.0 --port 8000

# 2. 前端（终端二）
cd E:\WORKBUDDY\Game\git_text\frontend
npm run dev

# 3. 访问
#    前端 http://localhost:3000
#    后端 API http://localhost:8000
#    Swagger 文档 http://localhost:8000/docs
```

- 首次启动 `data/` 为空属正常状态（前端显示"未构建"/"知识库检索服务不可用"）。
- Embedding 首次检索时自动加载（本地缓存命中，无需联网）。

## 配置方法

复制 `config/env.example` 为 `.env` 并填写（`.env` 已被 `.gitignore` 忽略，不提交 Git）：

```bash
cp config/env.example .env
```

关键配置项（不含真实值）：

| 变量 | 说明 | 默认 |
|---|---|---|
| `LLM_MODEL` | 默认聊天模型 | mimo |
| `MIMO_API_KEY` / `MIMO_MODEL` / `MIMO_BASE_URL` | 小米 MiMo | — |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` / `DEEPSEEK_BASE_URL` | DeepSeek | — |
| `DASHSCOPE_API_KEY` / `DASHSCOPE_MODEL` / `DASHSCOPE_BASE_URL` / `DASHSCOPE_VISION_MODEL` | 通义千问 / Vision | qwen-vl-plus |
| `EMBEDDING_MODEL` | Embedding 模型 | BAAI/bge-small-zh-v1.5 |
| `EMBEDDING_LOAD_MODE` | auto / offline / online | auto |
| `EMBEDDING_LOCAL_DIR` | 本地模型目录（可选） | 空 |
| `RAG_TOP_K` / `CHUNK_SIZE` / `CHUNK_OVERLAP` | 检索与切块参数 | 5 / 500 / 50 |
| `SERVICE_HOST` / `SERVICE_PORT` | 服务地址 | 0.0.0.0 / 8000 |
| `CORS_ORIGINS` | 跨域白名单 | ["*"] |

> 注意：`run_server.py` 当前硬编码端口 8000（`SERVICE_PORT` 仅生效于直启 uvicorn 时）。

## 后续开发优先级

1. **修复 Vision 真实调用**：确认/更换有效 `DASHSCOPE_API_KEY`，重跑 1 次真实图片理解验收（实现已就绪）。
2. **模型切换真正生效**：让 `/api/models/switch` 持久化当前模型，或移除该接口避免误导。
3. **知识图谱端到端人工验收**：后台生成链路已实现并通过 Mock 验证，在正式知识库上点"生成知识图谱"确认。
4. **一键启动脚本**：`start.bat` / `stop.bat`（支持中文路径）。
5. **Reranker 独立化**：评估引入真实 Rerank 模型，或明确当前轻量实现的边界。
6. **工程治理**：requirements.txt 锁定版本、缓存目录改为绝对路径、清理 `data_manager.py`/`mindmap_service.py` 等遗留死代码、`test_rag_service` 测试残留清理。

## 下一阶段：V2.5 智能学习助手

- 五段式智能笔记
- 自测题
- 学习计划基础系统

> 当前 commit 作为 V2.5 开发前回退基线。

## 版本说明

- `config/__init__.py` 中 `APP_VERSION = "2.2.1"`（代码层版本，尚未与文档对齐）。
- Git 无 tag；历史文档自称 V2.3.1 与代码版本不一致，以代码为准。

## 数据与安全

- 当前 `data/` 为空（正常新安装状态）；历史大学物理测试数据已不保留。
- 含旧密钥的 Git 历史已清理并 force push（远端干净，四层扫描零残留）；旧 bundle/zip/副本已删除。
- 安全事故内部审计报告已移出仓库，保存在 `E:\WORKBUDDY\Game\docs_offsite_backup_20260806\`（不随仓库分发）。
