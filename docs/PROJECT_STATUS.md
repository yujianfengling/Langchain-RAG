# 项目状态文档（PROJECT_STATUS）

> **本文件是当前项目状态的唯一权威文档**，后续接手 Agent 应首先阅读本文件。
> 更新日期：2026-09-27（本轮：安全修复 + 教学效果评测 + 演示部署）。
> 本文件只记录经真实验证的状态，不包含任何真实 API Key。
>
> 阶段一《学生学习闭环》的详细交付说明见 [`docs/PHASE1_LEARNING_LOOP.md`](./PHASE1_LEARNING_LOOP.md)。
> 本地语音（ASR）选型、安装与实测数据见 [`docs/LOCAL_SPEECH.md`](./LOCAL_SPEECH.md)。
> 凭据暴露范围与轮换步骤见 [`docs/CREDENTIALS.md`](./CREDENTIALS.md)。
> 教师端（课程/班级/教案/作业/学情）见 [`docs/TEACHER_MODULE.md`](./TEACHER_MODULE.md)。
> 教学效果评测（评测集/结果/试用方案）见 [`docs/TEACHING_EVAL.md`](./TEACHING_EVAL.md)。
> 演示与部署（含首次启动自检）见 [`docs/DEMO_GUIDE.md`](./DEMO_GUIDE.md)。

---

## 项目简介

《智慧教育多模态 RAG 系统》——面向教育领域的检索增强生成（RAG）问答系统。支持知识库构建、FAISS+BM25 混合检索、RRF 融合、Rerank 重排序、LLM 智能问答，以及 PDF 多模态（文本/公式/表格/图片）解析与 Vision 图片理解。

当前仓库位于 `E:\WORKBUDDY\Game\git_text`（git 远端：`github.com/yujianfengling/Langchain-RAG`，分支 master，
HEAD 为 `7bd14fc`）。阶段一、阶段二、默认模型切换与本轮本地语音的全部改动均在**工作区未提交**
（本轮按用户要求未推送、未部署）。本地语音的模型与 venv 位于仓库**之外**的 `E:\WORKBUDDY\Game\speech_stack`。

## 当前技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11 · FastAPI · Uvicorn |
| 前端 | React 18 · TypeScript · Vite 5 · Ant Design · ECharts 6 |
| 向量检索 | FAISS（IndexFlatL2）· `BAAI/bge-small-zh-v1.5`（本地 Embedding，512 维） |
| 关键词检索 | rank-bm25 |
| 混合融合 | RRF（Reciprocal Rank Fusion，k=60，等权） |
| 重排序 | 轻量实现（复用 Embedding 做余弦重排，无独立 Reranker 模型） |
| LLM（文本，默认） | **DeepSeek**（`deepseek-chat`）· 可选：小米 MiMo、通义千问 |
| Vision | 通义千问 qwen-vl-plus |
| PDF 解析 | PyMuPDF + pdfplumber + Pillow + pytesseract |
| **语音识别（ASR，默认）** | **本地 FunASR** `paraformer-zh` + `fsmn-vad` + `ct-punc`（独立进程 :8100） |
| 语音（可选提供方） | MiMo ASR（会员到期中，代码保留、不再作为默认路径） |
| 教师端数据 | 独立库 `data/teaching.db`（课程/班级/成员/资料/教案版本/作业/审计/学科归属） |
| 角色 | `users.role`：student（默认）/ teacher（**受控开通**：环境白名单 · 管理员 · 教师邀请码）/ admin（环境变量白名单） |

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

              教师端 (前端 /teacher) ──> api/routes/teacher.py ──> services/teaching_*.py
                                                              ├─ teaching_data.py (teaching.db)
                                                              ├─ lesson_plan_service.py (教案+版本)
                                                              ├─ assignment_service.py (作业→学生任务)
                                                              └─ class_analytics.py (学情统计+AI解读)

              services/local_speech.py ──HTTP──> 本地语音服务 (8100端口, 独立进程/独立 venv)
                                                  FunASR paraformer-zh
                                                  （模型只加载一次，常驻显存；缺模型/失败不影响文字问答）
```

配置优先级：环境变量 > `.env` > 代码默认值。

> 凭据现状（2026-09-26 已处置）：`config/models.py::_BUILTIN_API_KEYS` **已清空为空字典**，
> 代码中不再有任何内置/回退密钥；密钥只从环境变量或未跟踪的 `config/local_secrets.json` 读取。
> **但历史提交 `85ee0a7`/`a650db2`/`f00c2fa` 中的真实值仍在 HEAD 与 `origin/master` 上可达** —— 已进入
> 历史的密钥必须**更换**，仅删代码不足以消除风险。范围与轮换步骤见 `docs/CREDENTIALS.md`（不输出密钥内容）。

## 当前默认模型

| 用途 | Provider | Model | 状态 |
|---|---|---|---|
| **文本（默认）** | **deepseek** | `deepseek-chat` | ✅ 可用 |
| 文本（可选） | mimo | `mimo-v2.5-pro` | ❌ 会员到期（凭证被拒），保留为可选提供方，**不会自动回退** |
| 文本（可选） | qwen | `qwen-plus` | 配置存在，未做本轮验收 |
| Vision | qwen-vision | `qwen-vl-plus` | ✅ 真实调用已验证可用（与 MiMo 无关） |
| 语音识别（默认） | **本地 local** | `funasr:paraformer-zh` | ✅ 已实测可用（冷启动 48.9s，短音频 0.2–1.2s） |
| 语音（可选） | mimo-asr | `mimo-v2.5-asr` | ❌ MiMo 会员到期 → 不可用（接口如实报错，非默认路径） |

- **单一解析入口**：`config/models.get_default_model()`（`LLM_MODEL` 环境变量 > `DEFAULT_MODEL="deepseek"`）。
  未知取值、语音/视觉专用模型名一律回落到默认文本模型；**不做任何"自动回退到 MiMo"的处理**。
- 文本/视觉/语音三条链路配置独立：切换默认文本模型**不会**把 DeepSeek 塞进 Vision / ASR。
- 未指定 `model` 的入口（问答、流式、Agent、笔记、自测、学习计划、知识图谱、知识提取）统一走该默认值；
  显式传入其它模型时按实际选择执行（失败也如实报错，不静默替换）。
- 相关变量：`LLM_MODEL`、`DEEPSEEK_MODEL`、`MIMO_MODEL`、`DASHSCOPE_MODEL`、`DASHSCOPE_VISION_MODEL`、
  `GRAPH_MODEL`（可选，知识图谱独立模型，未设置则复用默认文本模型）。

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

- [x] ~~Qwen Vision 真实图片理解~~ **已真实验证可用（2026-09-26）**：`qwen-vision`（qwen-vl-plus）真实调用成功返回图片描述，与 MiMo 到期无关（视觉走 DashScope，不共用文本模型）
- [x] ~~MiMo 真实调用~~ **已按用户说明定性（2026-09-26）**：MiMo 不可用是**会员到期**，不是程序故障。默认文本模型已统一改为 **DeepSeek**（`LLM_MODEL=deepseek`），MiMo 仅作可选提供方保留、不再自动回退。若日后续费，把 `LLM_MODEL` 改回 `mimo` 即可，无需改代码。
- [ ] **模型切换接口**：`POST /api/models/switch` 仅返回成功，不真正改变全局状态（实际生效依赖每次请求的 `model` 参数）
- [ ] **一键启动脚本**：`start.bat` / `setup.bat` / `stop.bat` 未实现（仅有 `install.ps1` / `install.sh` 安装脚本）
- [ ] **Reranker**：为轻量实现（复用 Embedding 余弦重排），非独立 Rerank 模型
- [ ] **知识图谱端到端人工验收**：后台生成链路已实现并通过 Mock 验证，尚未在正式知识库上人工验收（需用户点"生成知识图谱"确认）
- [x] ~~学习/笔记旧测试 6 个失败~~ **已修复（2026-09-25）**：新增 `tests/fixtures_isolated.py`，在临时目录用真实生产代码路径创建学科与"已就绪"知识库标记，断言未放宽，全量测试 432 passed。
- [ ] **正式库历史残留清理**：隔离机制上线前有 113 行孤儿数据 + 15 个测试账号残留在正式库（清单见 `docs/TEST_DATA_INVENTORY.md`）。本轮**只出清单未删除**（禁止按名称前缀批量删除），需授权后按方案执行。
- [ ] **前端代码分割**：`dist` 单包 3.7MB（gzip 1.25MB），未做 dynamic import / manualChunks。

## 阶段一：学生学习闭环（已完成并验收）

详见 [`docs/PHASE1_LEARNING_LOOP.md`](./PHASE1_LEARNING_LOOP.md)。要点：

- 新增身份机制（`auth.db` + 会话令牌）：学习数据一律归属后端当前用户，忽略请求体 `user_id`
- 数据基础：稳定知识点 ID + 别名、题库（服务端标准答案）、作答记录、复习任务、统一幂等事件层
- 真实自测：**后端权威判分**（不接受前端 `is_correct`）、`client_token` 幂等、查看答案降级为 `after_reveal`
- 错题本 + 复习联动：错题自动生成复习任务，同知识点合并去重；答对关闭待办并保留历史
- 学习画像修正：学习行为 / 作答证据 / 掌握估计三者分离，无证据显示「待评估」而非默认分数
- 计划可执行：任务带真实日期，支持今日/逾期/完成/改期（留痕），测验任务需先作答才能完成
- 学生端页面：`/student` 我的学习（今日任务 / 逾期 / 复习安排 / 错题本 / 掌握度 / 联动记录）+ 登录入口
- 页面跳转带上下文：计划任务「去自测 / 看笔记」跳转学习助手并预置模式、知识点与来源任务

## 阶段二：按真实学习记录调整教学的学习 Agent（已完成并验收）

详见 [`docs/PHASE2_LEARNING_AGENT.md`](./PHASE2_LEARNING_AGENT.md)。要点：

- **受约束 Agent 编排**：`POST /agent/run`、`/agent/run/stream`（SSE 进度）、`/agent/runs/{id}`、`/agent/runs/{id}/cancel`。
  工具复用既有业务服务且只用后端当前用户身份；`max_steps=12 / max_seconds=180 / max_retries=1 / max_llm_calls=6`；
  运行与每步结果摘要落库（不记录模型思维过程）；支持取消、超时、工具失败后继续
- **画像真正影响教学**：`services/teaching_context.py` 在 900 字符预算内按相关性选取信息，
  产出 `foundation / standard / advanced` 三种模式 + 可展示的 `reasons / signals / applied`；
  流式与非流式同一实现；无证据时明确"常规教学"，不虚构水平
- **可管理学习记忆**：`preference`（学生明确设置，优先级最高）/ `inference`（带证据与有效期，自动过期）/
  `curriculum`（教师或课程确认，待审核）三类；学生可查看 / 更正 / 删除 / 关闭；
  一次错误不会变成永久能力标签
- **计划调整**：截止日期、每天可用时长、教师固定截止时间、自动调整开关；预算冲突时给"缩小范围"方案，
  不生成超时计划；幂等提议键避免重复建任务；重大调整需确认；支持撤销、跳过手动编辑过的任务
- **界面接入与降级**：聊天页新增「学习助手」模式（执行进度、每步摘要、停止按钮）；
  「我的学习」新增「计划与时间」「学习记忆」页签；回答下方展示「本次讲解依据」与「已采用你的设置与记忆」；
  模型不可用时计划、任务、历史、错题、本地判分、记忆仍可用

## 试用前验收与效果评估（2026-09-27）

### 1. 修复的关键安全问题：教师身份可自助获取（提权漏洞）

- **实测确认**：上一轮"创建课程即授予教师角色"意味着**任何注册用户都能自助获得教师权限**
  （拿到教师工作台、建班级、看学情）。这不是设计意图，是漏洞。
- **已修复**：教师身份只能由受控通道开通——
  ① 环境白名单 `TEACHER_USERS`（部署时的初始教师）；② 管理员（`ADMIN_USERS`）直接开通；
  ③ **教师邀请码**（一次性/限量、可过期、可撤销、同一账号不可重复使用），
  用户在「设置 → 教师身份」凭码开通。创建课程**不再授予**任何角色。
- **签发权限再收紧（2026-09-27 第二轮）**：上一版把开通改成邀请码制，但**签发权仍在任何教师手上且无配额**，
  等于邀请码本身成了不受控的扩散渠道。现在的策略是：
  - **默认只有管理员**能签发教师邀请码；教师签发需部署方显式设置 `TEACHER_CAN_INVITE=1`；
  - 教师签发受三重上限：有效期 ≤ `TEACHER_ISSUE_MAX_TTL_DAYS`（默认 7 天）、
    可用次数 ≤ `TEACHER_ISSUE_MAX_USES`（默认 1）、名下"未用尽且未撤销"的邀请码 ≤ `TEACHER_ISSUE_QUOTA`（默认 2 个，超出返回 429）；
    未显式指定有效期时按上限收紧，**不会**因全局默认值（30 天）而误报错；
  - 管理员上限为 365 天 / 200 次，配额不限；
  - 可见范围：管理员可列出并撤销**全部**邀请码，教师**只看到并能撤销自己签发的**；
  - 判定全部在服务端（`services/auth_service.invite_policy` + `create_role_invite`），界面只据 `policy.can_issue` 禁用按钮。
- 新增端点：`GET /teacher/role`、`POST /teacher/invites`、`GET /teacher/invites`、
  `POST /teacher/invites/{code}/revoke`、`POST /teacher/apply-invite`、
  `POST /teacher/users/{id}/grant`（仅管理员）。
- **已有数据兼容**：只做幂等加列（`users.role_source`）；库内已是 teacher 的账号不受影响；
  未清空、未重建任何用户数据。已用测试固定"历史教师账号继续可用"。

### 2. 班级邀请码生命周期与退班访问

- 邀请码新增：**有效期**（默认 180 天）、**可撤销**、**可用次数上限**（默认 200）、
  **加入失败频率限制**（默认 10 次/10 分钟，防暴力猜码）；轮换即重置撤销与计数。
- 退班/被移除后：课程资料、作业、班级数据全部读不到（资料 403、作业 404、课程列表为空），
  但**自己的学习记录（作答/错题/复习）保留**。
- 教师端接口：`POST /teacher/classes/{id}/revoke-code`、`GET /teacher/classes/{id}/join-code`（状态与有效期）。

### 3. 访问隔离对抗测试（新）

`tests/test_isolation_and_consistency.py`（19 项）覆盖："换一个用户 ID"（路径/查询串两种）、
"退班后继续访问"、"直接请求接口（未登录）"、教师读学生私人对话、跨用户会话读写、
个人计划约束隔离等，全部按真实 HTTP 请求验证。

### 4. 教学闭环联动与统计口径（已明确并验证）

- 1 教师 + 2 学生隔离数据走通：发布资料/作业 → 学习/问答/提交 → 判分与错题 → 掌握度与复习 → 教师学情。
- **统计口径明确**（避免重复提交导致重复计数）：
  - 参与情况：`attempt_count` 为**原始**作答次数，另给 `distinct_question_count`（去重到"学生×题目"）
    与 `average_attempts_per_question`；
  - 知识点作答 / 常见错题：按**每个学生每题的最新一次**作答判定（重做答对即不再算错），
    同时保留 `raw_attempt_count` / `raw_wrong_count` 供核对，字段 `basis` 标明口径。
- 同一 `client_token` 重复提交幂等（不重复计数）；重做会新增原始记录但掌握判定按最新一次。

### 5. 教学效果评测（固定评测集，47/47 通过）

见 [`docs/TEACHING_EVAL.md`](./TEACHING_EVAL.md)。要点：

- 评测集：`tests/eval/dataset/eval_set.json` + 3 份固定材料 + 1 份无关干扰材料；
  脚本 `python tests/eval/run_eval.py --base http://127.0.0.1:8001`（真实 DeepSeek + 本地语音）。
- 结果：**RAG 8/8、RAG(加无关资料) 9/9、教学闭环 13/13、Agent 8/8、语音 9/9，合计 47/47**。
- 覆盖了要求的四类：检索命中与引用定位、材料无答案时说明不知道、加无关资料后仍正确；
  判分/错题/掌握度/复习/学情一致与重复提交；
  Agent 依真实记录给建议且改计划走提议确认；语音识别流程与耗时。
- **真实教学效果仍为「待真实试用验证」**：试用任务、反馈表、前后测方案已备好（见该文档第五节），
  但没有真实学生参与，不得宣称教学成效。

### 6. 演示与首次启动体验（新）

- `docs/DEMO_GUIDE.md`：从自检 → 配置 → 启动 → 七步师生演示（建课/邀请/资料/教案/作业/学情）。
- `python tests/check_first_run.py [--url ...]`：逐项自检并给修复建议
  （Python/依赖/数据目录/建库/模型密钥/知识库/语音栈/后端健康与各能力状态）。

## 教师端：课程 · 班级 · 教案 · 作业 · 学情（已完成并验收，2026-09-26）

详见 [`docs/TEACHER_MODULE.md`](./TEACHER_MODULE.md)。要点：

- **后端强制的角色与课程归属**：新增 `users.role`（student 默认 / teacher 建课时显式授予 / admin 环境变量白名单）。
  **不静默提升任何历史或新账号**；前端菜单只是展示，越权一律在后端 403。
- **课程 = 一位授课教师 + 一个专属学科**：教师建课时后端新建学科并登记归属（`subject_owners`），
  资料/图谱/文档因此天然按课程隔离。历史全局学科保持既有行为，**不会**被自动分配给任何教师。
- **旧接口同步收紧**（本轮重点）：知识库上传/删除/重建/图谱、学科增删查、文档列表与下载、
  知识提取的验证与入库，全部按课程归属校验；并补上**文件级**过滤——
  在班学生看不到也下载不到教师的 `teacher_only` 资料。
  开关 `TEACHING_SCOPE_GUARD=0` 可临时关闭（仅排障）。
- **教案**：AI 依据本课程资料生成草稿，标出依据资料，资料不足时明确标注；
  教师每次编辑/重新生成都**追加新版本**（历史版本保留，修改不被覆盖）；草稿学生不可见，发布后才可见；支持导出 Markdown。
- **作业/练习**：复用既有题库与**后端权威判分 + 答案隔离**（不另建题库）；
  发布时为在班学生物化 `learning_tasks`（自动进入「今日任务」），并把截止日期写成
  **教师固定截止时间**——学生个人计划与 Agent 调整**只能更早、不能推后**。
- **学情分析**：参与人数与样本量、任务完成、知识点作答、常见错题、每日趋势全部由**程序按真实记录**计算，
  每块标注来源与时间范围；样本不足（<5 次已判分作答或 <2 名学生）明确显示「数据不足」，
  AI 解读只在样本足够时调用模型。教师端只用汇总数据，**不读取学生个人聊天原文**。
- **界面**：教师工作台（我的课程 / 待完成教案 / 已发布任务 / 需关注知识点）、班级与成员、
  课程资料、教案编辑与版本、作业与发布、学情与下钻；学生端「我的学习」新增「我的课程」页签
  （邀请码加入、看课程、做老师布置的作业、看已发布教案）。

## 本地语音输入（已完成并验收，2026-09-26）

详见 [`docs/LOCAL_SPEECH.md`](./LOCAL_SPEECH.md)。要点：

- **默认语音输入路径完全本地**：录音 → 本地 ASR（FunASR paraformer-zh，带标点）→ 文本进入输入框由学生确认后发送
  → DeepSeek 回答（流式）。**不会**用 DeepSeek 文本接口冒充语音。
- **独立进程 + 清晰接口**：模型推理全部在 `speech_service/`（:8100，独立 venv），主后端只通过
  `services/local_speech.py` 调用；主进程不加载任何语音依赖。模型只加载一次，支持预热与健康查询；
  缺模型/加载失败时如实报原因，**文字问答不受影响**。
- **界面**：设置页新增语音输入与本地语音状态（显存、预热按钮、不可用原因）。
  健康检查报告 text / vision / local ASR。

## 小班试用准备（2026-09-27 第三轮）

### 1. 数据隔离：脚本"写入前拒绝"，而不是"写脏了再删"

- 新增 `tests/_isolation_guard.py`：所有会写数据的脚本共用一道闸门——
  未设置 `EDU_DATA_DIR` 或指向项目正式 `data/` 时**直接拒绝运行（退出码 2）**并打印设置方法；
  只读脚本降级为警告但仍打印实际目录。HTTP 类脚本（E2E）改为判定后端 `/api/health` 的 `data_scope.isolated`。
- 已接入：`tests/eval/run_eval.py`（闸门置于任何 HTTP 调用之前）、
  `tests/inventory_production_data.py`（只读）、`tests/export_trial_data.py`、`tests/seed_demo_data.py`、
  6 个 `tests/e2e_*.js`。
- `tests/check_first_run.py` 改为**非写入式**：不再在目标目录建表，数据库初始化改在临时目录试跑。
- `tests/conftest.py` 增加**会话级强制隔离**：未设置时自动落到 `git_text_test_data/`，
  若被指向正式 `data/` 则整个测试会话拒绝启动。
- 验证方式（`tests/test_isolation_guard.py`，10 例）：子进程真跑脚本 + **正式库逐库 SHA256 与行数前后比对**，
  证明确实一个字节都没写进去；另含"GBK 控制台下拒绝提示不会自己崩掉"的回归用例。

### 2. 教师邀请码签发权限收紧（防止教师身份无控制扩散）

- **默认只有管理员**能签发教师邀请码；教师签发需部署方显式设置 `TEACHER_CAN_INVITE=1`。
- 教师签发三重上限：有效期 ≤7 天（`TEACHER_ISSUE_MAX_TTL_DAYS`）、可用次数 ≤1（`TEACHER_ISSUE_MAX_USES`）、
  名下未用尽的邀请码 ≤2 个（`TEACHER_ISSUE_QUOTA`，超出返回 429）；管理员为 365 天 / 200 次且不限配额。
- 可见范围：管理员可列出并撤销**全部**邀请码，教师**只能看到并撤销自己签发的**。
- 判定全在服务端（`services/auth_service.invite_policy` / `create_role_invite`），
  前端仅依据 `policy.can_issue` 禁用按钮并说明原因。

### 3. 首次使用体验走查（真实浏览器，教师 + 2 名学生）

- 新增 `tests/e2e_first_use_walkthrough.js`：按第一次使用的顺序走完
  开通教师身份 → 建课建班 → 上传资料 → 教案 → 作业（AI 出题 → 发布）→ 学生入班 → 作答 → 错题/复习/掌握度 → 教师学情，
  并在 1366×768 与 390×844 下量布局。**56/56 通过**，详见 `docs/FIRST_USE_REVIEW.md`。
- 走查中发现并修复 6 个真实障碍（每个都有复现步骤）：上传资料后 AI 出题报"资料为空"的顺序陷阱、
  出题不挂作业导致发布被拒、学生答完作业错题本为空（学科未选 + 数据不刷新）、两种"邀请码"文案混淆、
  课程弹窗里"创建课程会授予教师身份"的过期错误文案、学生首次进入找不到入班入口。

### 4. 修复：学情"每日趋势"的错误次数恒为 0

- `services/class_analytics._trend` 用 `is_correct is False` 判断答错，而 SQLite 取出来是**整数 0/1**，
  `0 is False` 为假 → 趋势里的错误次数与错误率**永远是 0**，与同一份数据算出的知识点错误率自相矛盾。
- 已改为统一判定 `_is_wrong()`（整数 0 / 字符串 "0" 都算错；`None` 表示未判分，既不算对也不算错，
  且不进趋势分母），知识点统计也改用同一函数，保证两处口径一致；新增 `tests/test_class_analytics_counts.py` 固定。
- 实测复验：同一班级同一批作答，修复前趋势显示 0/6 错误，修复后为 6/6（100%），与知识点错误率一致。

### 5. 试用包与展示材料

- `docs/TRIAL_PACKAGE.md`：参与人数、统一章节、师生任务、时间安排、前后测评分规则、反馈问题、
  异常记录表、自动导出 vs 人工记录的分工。
- `tests/export_trial_data.py`：**默认只导出班级级聚合**（参与率/错误率/趋势），从源头不产生个人数据；
  需要核对学生时用 `--with-mapping` 另存"账号↔代号"文件并明确标注不要外传。
- `docs/TRIAL_RESULTS_TEMPLATE.md`：结果模板，**当前全部为"待试用"**，不含任何虚构提升率/满意度。
- `docs/DEMO_SCRIPT.md`：5–8 分钟演示路线（以教学闭环和"相对普通 AI 问答的差异"为主线）。
- `tests/seed_demo_data.py`：一条命令准备演示账号/班级/讲义/作业，课程名带「(演示数据)」标注。

## 响应式适配（2026-09-27 第四轮）

> 目标：试用前把"学生用手机能不能自己走完"解决掉；教师端保持电脑为主，但 800px 不能挡按钮。

### 1. 窄屏布局（≤900px）

- 应用外壳在窄屏改成「顶栏 + 抽屉导航」：顶栏有「☰ 菜单」与登录入口，抽屉里是同一份菜单。
  之前 ≤768px 只是把侧边栏 `display:none`，**手机上没有导航、也点不到登录按钮**（登录按钮在侧边栏里）。
- 断点取 **900px** 而不是 768：800px 时侧边栏仍占 220px，把内容挤到 981px 造成横向溢出。
- 隐藏规则改为精确的 `.app-sider`：原来的 `.ant-layout-sider { display:none }` 会**连带隐藏页面内部的 Sider**。

### 2. 学习助手窄屏可用（真实阻塞项）

- ChatPage 的「学科选择器」原本只在页面内部的历史侧栏里，窄屏被一起隐藏 →
  **发送按钮永久禁用，手机上一个问题都问不出来**。窄屏改为聊天区顶部一行（学科选择 + 新建 + 历史对话抽屉）。

### 3. 三档屏宽实测（`tests/e2e_responsive_walkthrough.js`，当轮 120 项全绿；
> 2026-10-01 合并后补上教师端「工作台」「学生画像」两个面板的检查，现为 **134 项**）

| 屏宽 | 学生端 | 教师端 |
|---|---|---|
| 390px | 注册/登录 → 抽屉导航 → 入班 → 作答（3 题判分）→ 错题本 → 复习 → 学习助手**真模型回答** 全部走通；无横向溢出 | 未做手机适配：内容宽 512px，需左右拖动（按电脑为主设计） |
| 800px | 全部走通，无横向溢出 | 6 个面板均无溢出；关键按钮与表格行内按钮都在可视区 |
| 1366px | 全部走通，无横向溢出 | 同上 |

- 弹窗在窄屏留 12px 边距（390px 下作答弹窗 374px），底部按钮始终可点。
- 验证脚本自身也修了一个**假通过**：窄屏标签被 antd 收进「更多」下拉，直接点 `.ant-tabs-tab`
  点的是被裁掉的隐藏标签（页面没切换），而断言写的是"不包含『暂无错题』" → 假通过。
  现在统一走「更多 → 菜单项」并要求正向证据。

### 4. 回归确认（本轮改动未破坏上一轮修复）

- 上一轮的 4 项修复全部复测通过：上传资料后**知识库自动就绪**、AI 出题**挂到草稿作业**后题目数 3 且发布成功、
  学生**错题本/复习/掌握度**在作答后正常出现、教师**学情趋势**错误次数与知识点口径一致（同一批数据都是 6/6）。
- 全量测试 **613 passed / 0 failed**（该轮）；电脑端完整闭环走查 **56/56**。

### 5. 仍未解决

- 教师工作台在 390px 手机宽度下横向溢出（本轮按"电脑为主"处理，未重排）。
- 手机端的语音输入（录音提问）未做专项验证。
- 学生端标签在窄屏会收进「更多」下拉，需要多点一下（antd 默认行为，未改）。

## 试用准备期（2026-09-27 第五轮，**新功能已冻结**）

> 本阶段只做：确认现有流程可按文档操作、补齐试用材料、修"能复现的阻塞问题"。
> 不新增功能、不做重构。

### 1. 现状核对（发现并修了文档与代码的漂移）

- `tests/check_first_run.py` 退出码 0（11 项 OK）；三个服务（主后端 / 语音 :8100 / 前端）均在运行。
- **文档项数漂移**：上一轮给响应式脚本加了 3 条表格检查后实际是 **120 项**，文档仍写 117 → 已全部改正
  （`TRIAL_PACKAGE.md` / `DEMO_GUIDE.md` / `FIRST_USE_REVIEW.md` / `PROJECT_STATUS.md`）。
- 文档里引用的 32 个脚本/文件全部存在。

### 2. 手机端语音实测（此前未专项验证）

- 方式：`tests/e2e_voice_flow.js` 支持传视口，在 **390×844** 下用**假麦克风音频 + 真实本地 ASR(FunASR) + 真实 DeepSeek** 跑语音输入链路。
- 结果：**旧文档所述手机宽度 24/24 通过、桌面 1440×1000 对照 22/22 通过**（该轮脚本含朗读相关检查，已随功能移除）；
  保留的检查为「录音按钮在可视区内」「录音→转写→确认后发送」。
- **明确未验证**：真实手机硬件（麦克风）、iOS Safari / Android Chrome、权限弹窗、弱网。
  详见 `docs/LOCAL_SPEECH.md` 第六节。

### 3. 修复的可复现阻塞问题

- **6 个 E2E 验收脚本一启动就崩**（`Cannot access 'page' before initialization`）：上一轮加的隔离闸门
  把 `page` 作为参数，但调用点在被引用的 `page` 声明之前（TDZ）。已把闸门改为用 Node `fetch` 查
  `/api/health`，不再依赖 Playwright；6 个脚本同步修好，闸门仍能在非隔离后端上以退出码 2 拒绝。
  复现步骤/影响范围/回归结果见 `docs/TRIAL_FIX_LOG.md` FIX-001。
- 语音脚本在窄屏下的选择器失配（登录入口、新建会话按钮文案）→ 已适配，属脚本问题非产品缺陷（FIX-002）。

### 4. 新增试用材料

- `docs/TRIAL_RUNBOOK.md`：**一页版操作清单**（启动与检查 / 建试用班级 / 学生加入 / 导出匿名汇总 /
  故障记录与恢复 / 演示数据与真实试用数据的区分）。
- `docs/TRIAL_FIX_LOG.md`：试用期缺陷修复记录模板 + 本轮两条修复 + 两条运维观察；明确"只修可复现的阻塞问题与明显统计错误"。

### 5. 试用前最终验证（本轮实跑）

| 检查 | 命令 | 结果 |
|---|---|---|
| 首次启动自检 | `python tests/check_first_run.py` | 退出码 0（11 项 OK） |
| 电脑端完整闭环 | `node tests/e2e_first_use_walkthrough.js` | **56/56** |
| 三档屏宽（390/800/1366） | `node tests/e2e_responsive_walkthrough.js` | **120/120**（当轮；合并后为 134 项） |
| 手机宽度语音 | `node tests/e2e_voice_flow.js <base> <wav> 390 844` | **24/24** |
| 后端全量测试 | `pytest tests/ -q --ignore=tests/eval` | **621 passed / 0 failed**（合并后新增：4 条教师侧检索降级 + 4 条学生画像权限/范围测试） |
| 匿名导出 | `python tests/export_trial_data.py …` | 只含班级级聚合，**无任何学生账号名** |

## 与远端合并（2026-10-01 第六轮）

远端 `origin/master` 到 `d7ff751`（组员的两次提交：教师端初版 + RAG/检索"优化"）与本地的五轮改动
并行发展。合并策略按项目组决定：**教师端以本地实现为主**，同时**吸收组员实现的优点**。

### 1. 合并结果（commit `9d360a6`）

| 组员提交的内容 | 处理 |
|---|---|
| RAG 检索优化：分块清洗、FAISS 索引加锁与缓存、BM25 改进、Embedding 批处理 | **保留**（价值高，且已被本地运行时路径解析适配） |
| 教师端初版（`api/routes/teacher.py` 等） | 以本地 40 个接口的实现为准；组员**独有的 3 个接口**（学生列表 / 单个学生画像 / 班级画像）并入，并补上鉴权与范围限定 |
| 组员教师端前端面板 | 本地已有等价面板；**新增组员的「学生画像」面板**（`StudentsPanel.tsx`，与"学情分析"互补） |
| 组员前端重复的 `/teacher` 路由 | 删除（与本地路由重复） |

- **接入组员 3 个接口时发现并修掉的安全问题**：原实现**没有任何鉴权依赖**、且返回**全体用户**的画像记录。
  按试用 runbook 的 `0.0.0.0` 绑定方式，这会把所有学生的画像暴露给任何登录用户。
  合并后改为 `Depends(require_teacher)`，数据范围限定为**自己名下班级的在班学生**（`_my_students`），
  且无画像的学生按 0 统计而不是漏人。

### 2. 合并引入的回归（已定位并修复）

合并后教师端「AI 出题」在不填知识点的默认路径下报 422「课程资料为空」。根因是组员的分块清洗
（去掉 `[来源: 文件名]` 包装——**清洗本身正确**）削掉了分块与课程标题的字面重合，而默认查询词正是课程标题。
修复方式不是回退清洗，而是给教师侧生成加**一次有记录、有上限的阈值降级**：

- `rag_service.retrieve_context(min_score=…)`：可选覆盖阈值，不传时行为不变（学生问答不受影响）；
- `ai_service.retrieve_for_teaching()`：严格阈值检索为空时放宽到 0.0 再试一次，并如实返回 `relaxed` 标记；
- 只有放宽后仍为空才报「课程资料为空」。

详见 `docs/TRIAL_FIX_LOG.md` FIX-003。

### 3. 合并后回归验证（本轮实跑）

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量测试 | `pytest tests/ -q --ignore=tests/eval` | **621 passed / 0 failed** |
| 电脑端完整闭环（含 AI 出题默认路径） | `node tests/e2e_first_use_walkthrough.js` | **56/56**，AI 出题实测 200 / 生成 3 道 |
| 三档屏宽（390/800/1366） | `node tests/e2e_responsive_walkthrough.js` | **134/134**（补上「工作台」「学生画像」两个面板的检查） |
| 手机宽度语音（390×844） | `node tests/e2e_voice_flow.js <base> <wav> 390 844` | **24/24**（旧文档所述项数；该轮含朗读相关检查，已随功能移除） |

- 数据隔离：全部验证均在后端 `EDU_DATA_DIR=git_text_e2e_data`（`isolated: true`）下进行，正式库未被写入。

### 4. 推送结果（已完成）

- 推送目标：`origin/master`（`github.com/yujianfengling/Langchain-RAG`），**fast-forward，未 force push、未改写历史**。
- 实际输出：`d7ff751..2c3ee03  local-consolidated-20260927 -> master`（退出码 0）。
- 本地 `master` 随后快进到同一提交 `2c3ee03`，与 `origin/master` 一致（`git rev-parse` 两个 ref 相同）。
- 推送前 `git fetch` 曾失败一次（`Recv failure: Connection was reset`，网络原因）；
  推送时重新确认 `git ls-remote origin master` 仍为 `d7ff751`（远端未前进），再执行的推送。
- **未做**：没有推送其它分支、没有删远端分支、没有改 tags。

## 阻塞项（对外开放 / 正式汇报前必须由人完成）

> 下面几件事**本轮没有完成，也不允许写成"已完成"**。

| # | 事项 | 为什么是阻塞项 | 需要谁做 |
|---|---|---|---|
| 1 | **轮换 DeepSeek / MiMo 历史密钥** | 真实密钥仍随历史提交（`85ee0a7`/`a650db2`/`f00c2fa`）分发，删代码不够 | 密钥持有人登录供应商后台更换；步骤见 `docs/CREDENTIALS.md` |
| 2 | **真实小班试用** | 目前只有"自动评测 + 浏览器走查"两类证据，**没有真实学生数据**；任何提升率/满意度都还不成立 | 按 `docs/TRIAL_PACKAGE.md` 组织，结果填 `docs/TRIAL_RESULTS_TEMPLATE.md` |
| 3 | **在另一台电脑上的部署验证** | 现有启动/部署验证全部在开发机上完成；"换台机器照着文档能不能跑起来"没有第二台机器的记录 | 找一名未参与开发的成员，按 `docs/TRIAL_RUNBOOK.md` 第 1 节从零跑一次并记录 |
| 4 | 手机端语音输入**真机**验证（非阻塞，但影响"手机能否用语音"的结论） | 目前只有 390×844 浏览器宽度 + 假麦克风的实测；真实手机麦克风、iOS Safari/Android Chrome 未验证 | 让一名成员用自己的手机按 `docs/LOCAL_SPEECH.md` 第六节试一次并记录 |
| 5 | 教师端移动适配（非阻塞） | 学生端手机已可用（390px 实测走通）；**教师工作台在手机上仍横向溢出**，按"电脑为主"设计 | 后续再做教师端窄屏重排 |

> 口径约束：**"自动评测通过"、"真实学生试用通过"是两种不同证据，任何材料里都不许混写。**

## 测试状态

- 全量测试：**612 passed / 0 failed**（`pytest tests/ -q --ignore=tests/eval`，2026-09-27，约 2 分 24 秒）；
  上一轮：587 passed；本轮新增：邀请码签发权限收紧 8 例、数据隔离闸门 10 例（含 GBK 控制台场景）、
  学情统计口径回归 8 例
- **测试套件本身的坑（本轮修掉）**：`asyncio.run()` 在 Python 3.11 会把当前事件循环置空，
  导致"先跑的文件"把"后跑的文件"全部打挂（实测 60 个用例连环失败，单文件跑却全绿）。
  已在 `tests/conftest.py` 加 autouse fixture 补齐事件循环——之前若只单独跑文件会看不出问题。
- 阶段一补验：`pytest tests/test_phase1_hardening.py -q` → **31 passed**（答案隔离、复习语义、对象级权限、数据隔离、事件重试幂等）
- 阶段二：`pytest tests/test_phase2_agent.py -q` → **30 passed**（教学个性化、学习记忆、计划调整、Agent 运行时：含取消/超时/工具失败恢复）
- 真实模型教学验收：`python tests/e2e_real_teaching.py` → **23/23 通过**（模式差异、记忆更正/删除、答案隔离、模型不可用降级）
- 真实模型 Agent 流程（真实 Chrome）：`node tests/e2e_agent_flow.js …` → **19/19 通过**
  （目标输入 → 查看学习记录 → 检查时间预算 → 生成资料 → 生成计划 → 解释依据 → 自测判分 → 记忆页）
- 默认模型切换验收：`python tests/verify_default_model.py` → **19/19 通过**（不选模型即可用、响应与实际调用一致、旧会话兼容、显式选择不替换、视觉语音分别标记）
- 前端默认模型验收（真实 Chrome）：`node tests/e2e_model_default.js http://localhost:3000` → **9/9 通过**（默认选中 DeepSeek、一次性提示、不重复覆盖）
- **语音输入全链路验收（真实 Chrome + 假麦克风 + 真实本地 ASR + 真实 DeepSeek）**：
  `node tests/e2e_voice_flow.js http://localhost:3000` → **22/22 通过**
  （旧文档所述项数；该轮含朗读相关检查，已随功能移除；保留的是"录音转写进输入框不自动发送"）
- 凭据卫生：`pytest tests/test_credentials_hygiene.py -q` → **7 passed**（代码中无内置密钥、示例文件只含占位符、
  本地配置未被跟踪；发现密钥形态只报 file:line 不打印值）
- **教师端全流程（真实 Chrome + 真实 DeepSeek）**：`node tests/e2e_teacher_flow.js http://localhost:3000`
  → **43/43 通过**（学生自助提权被拒→管理员发教师邀请码→新账号凭码开通→建课→邀请码加入→上传资料→
  真实模型生成/编辑/发布教案→AI 出题→发布练习→学生作答→教师端学情更新→草稿不提前公开→
  两教师互不可改→未入班读不到资料→9 项旧接口越权全被拒→学生改不动教师固定截止）。
  注意：本轮起后端需带 `TEACHER_USERS=e2e_teacher,e2e_teacher2 ADMIN_USERS=e2e_admin` 启动。
- **教学效果评测（固定评测集，真实模型）**：`python tests/eval/run_eval.py --base http://127.0.0.1:8001`
  → **47/47 通过**（RAG 8、RAG+无关资料 9、教学闭环 13、Agent 8、语音 9）；详见 `docs/TEACHING_EVAL.md`。
- **测试数据隔离（已实测）**：跑完全量测试后正式库 **7 个 DB** 全表哈希与逐表行数与跑前一致（零写入）；
  新增库 `teaching.db` 在跑完教师端测试后仍为 0 行（用例全部在隔离目录）；
  真实模型验收必须 `EDU_DATA_DIR` 指向独立目录，`/health.data_scope.isolated=false` 时脚本直接退出（退出码 2）
- 测试说明：外部模型/RAG 在自动化测试中全部使用 Mock；真实模型验证单独执行，两者结果分开记录，不混用。
  **本轮真实模型一律为 DeepSeek**（`MIMO_API_KEY` 仍被供应商拒绝 401，未替换、未伪装）。

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

**可选：本地语音服务（要用语音问答才需要启动；不启动则文字功能完全不受影响）**

```powershell
# 终端三（独立 venv，模型只加载一次，常驻显存）
cd E:\WORKBUDDY\Game\git_text
$env:SPEECH_STACK_DIR="E:\WORKBUDDY\Game\speech_stack"
$env:SPEECH_WARMUP_ON_START="1"
E:\WORKBUDDY\Game\speech_stack\venv\Scripts\python.exe -m speech_service.server
# 就绪判断：curl http://127.0.0.1:8100/health → engines.asr.state 为 "ready"
# 安装、模型下载与依赖版本见 docs/LOCAL_SPEECH.md 第三节（可重复执行）
```

- 首次启动 `data/` 为空属正常状态（前端显示"未构建"/"知识库检索服务不可用"）。
- Embedding 首次检索时自动加载（本地缓存命中，无需联网）。
- 语音服务未启动时：`/api/health` 的 `capabilities.local_asr.available=false` 并给出原因，
  语音按钮会提示不可用，**文字问答、学习助手、自测等全部照常**。

**隔离验收用后端**（自动测试 / 真实模型验收，不影响正式数据）：

```powershell
$env:EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_e2e_data"   # 先用 python tests/make_e2e_env.py 生成
python -m uvicorn api.app:app --host 127.0.0.1 --port 8000
# 校验：curl http://127.0.0.1:8000/health  →  data_scope.isolated 必须为 true，
# 否则验收脚本会拒绝执行（避免测试账号写入正式库）

## 配置方法

复制 `config/env.example` 为 `.env` 并填写（`.env` 已被 `.gitignore` 忽略，不提交 Git）：

```bash
cp config/env.example .env
```

关键配置项（不含真实值）：

| 变量 | 说明 | 默认 |
|---|---|---|
| `LLM_MODEL` | 默认聊天模型 | **deepseek** |
| `SPEECH_BACKEND` | 语音后端：`local`（只用本地）/ `mimo` / `auto`（本地优先） | auto |
| `LOCAL_SPEECH_URL` / `LOCAL_SPEECH_TIMEOUT` | 本地语音服务地址 / 超时（秒） | http://127.0.0.1:8100 / 120 |
| `SPEECH_STACK_DIR` | 本地语音模型根目录（仓库之外） | 同级 `speech_stack` |
| `SPEECH_DEVICE` | 本地推理设备 | auto |
| `SPEECH_QUEUE_WAIT_SECONDS` | 本地语音排队等待上限（秒） | 20 |
| `SPEECH_HOST` / `SPEECH_PORT` | 本地语音服务监听 | 127.0.0.1 / 8100 |
| `MIMO_API_KEY` / `MIMO_MODEL` / `MIMO_BASE_URL` | 小米 MiMo | — |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` / `DEEPSEEK_BASE_URL` | DeepSeek | — |
| `DASHSCOPE_API_KEY` / `DASHSCOPE_MODEL` / `DASHSCOPE_BASE_URL` / `DASHSCOPE_VISION_MODEL` | 通义千问 / Vision | qwen-vl-plus |
| `EMBEDDING_MODEL` | Embedding 模型 | BAAI/bge-small-zh-v1.5 |
| `EMBEDDING_LOAD_MODE` | auto / offline / online | auto |
| `EMBEDDING_LOCAL_DIR` | 本地模型目录（可选） | 空 |
| `RAG_TOP_K` / `CHUNK_SIZE` / `CHUNK_OVERLAP` | 检索与切块参数 | 5 / 500 / 50 |
| `SERVICE_HOST` / `SERVICE_PORT` | 服务地址 | 0.0.0.0 / 8000 |
| `CORS_ORIGINS` | 跨域白名单 | ["*"] |
| `EDU_DATA_DIR` | 数据根目录（隔离测试/验收用；不设则用 `data/`） | 空 |
| `KNOWLEDGE_ADMIN_USERS` | **历史全局学科**管理白名单（逗号分隔；不设则任一登录用户可管理；**课程学科不受此影响**） | 空 |
| `ADMIN_USERS` | 管理员白名单（逗号分隔用户名）；不设则没有管理员 | 空 |
| `TEACHING_SCOPE_GUARD` | 学科归属守卫开关（`0` 关闭，仅排障用） | 1 |

> 注意：`run_server.py` 当前硬编码端口 8000（`SERVICE_PORT` 仅生效于直启 uvicorn 时）。

## 后续开发优先级

1. **密钥轮换（建议尽快）**：历史提交中的 MiMo / DeepSeek 密钥仍在 HEAD 与 `origin/master` 上可达，
   应按 `docs/CREDENTIALS.md` 第四节轮换（本轮未停用、未改写历史、未 force push）。
2. **正式库历史残留清理（待授权）**：按 `docs/TEST_DATA_INVENTORY.md` 第五节方案执行；
   禁止按 `e2e_`/`stu_` 前缀批量删除，迁移备份继续保留。
3. **教师端深化（可选）**：多教师协同授课、作业跨课程复用题目、教案导出 docx/pdf、
   邀请码之外的其他加入方式（如按学号导入名单，仍不接 SSO）、归档课程恢复入口。
4. **课程知识审核**：学习记忆已有 `curriculum` 类型与待审核状态，可挂教师审核入口
   （学生推断的知识不应自动成为课程权威内容）。
5. **MiMo 续费（可选）**：MiMo 会员到期，默认文本模型已切换 DeepSeek，语音默认路径已是**本地方案**
   （不依赖 MiMo）。续费后如需启用，把 `LLM_MODEL` 改回 `mimo` / `SPEECH_BACKEND=mimo` 并重跑
   对应验收脚本即可（无需改代码）。
6. **模型切换真正生效**：让 `/api/models/switch` 持久化当前模型，或移除该接口避免误导。
7. **知识图谱端到端人工验收**：后台生成链路已实现并通过 Mock 验证，在正式知识库上点"生成知识图谱"确认。
8. **一键启动脚本**：`start.bat` / `stop.bat`（支持中文路径，建议一并拉起语音服务）。
9. **Reranker 独立化**：评估引入真实 Rerank 模型，或明确当前轻量实现的边界。
10. **本地语音输入体验打磨**：语音服务随主后端一键拉起与崩溃自恢复。
11. **工程治理**：requirements.txt 锁定版本、清理 `data_manager.py`/`mindmap_service.py` 等遗留死代码、
    继续收敛硬编码的 `data/` 路径（本轮已修 `rag/vector_store.py` 与 `api/app.py` 两处）。
12. **阶段三候补**：V2.6 主动学习 Agent 接线、前端代码分割（当前单包 3.8MB）、历史数据认领 UI 引导。

## 历史阶段

- **V2.5 智能学习助手**：五段式智能笔记、自测题、学习计划基础系统（已交付）
- **V2.5.1 / V2.5.2 / V2.5.4**：统一学习交互与语音会话、音频运行时修复、事件循环并发修复、流式渲染性能优化（已交付）
- **阶段一 学生学习闭环**：见上（已交付并验收）
- **阶段二 学习 Agent**：见上（已交付并验收，2026-09-25）

## 版本说明

- `config/__init__.py` 中 `APP_VERSION = "2.2.1"`（代码层版本，尚未与文档对齐）。
- Git 无 tag；历史文档自称 V2.3.1 与代码版本不一致，以代码为准。

## 数据与安全

- `data/` 现有运行时库：`chat.db`、`learning.db`、`learning_loop.db`（阶段一新增）、
  `user_agent.db`、`auth.db`（阶段一新增）、`extracted_knowledge.db`，以及 `vector_store/`、`bm25_index/`、
  `subjects/`、`subjects.json`、`backups/`。数据库文件与备份均在 `.gitignore` 中，不随仓库分发。
- **已修（本轮教师端验收时发现）**：`rag/vector_store.py` 原先把向量库根目录硬编码为
  `<项目根>/data/vector_store`，绕过了 `EDU_DATA_DIR`。后果是**隔离数据目录下"写入隔离目录、检索读正式目录"**：
  索引找不到就静默降级（关键词检索/检索不可用），表现为回答凭空少了资料依据，
  且"教师上传资料 → AI 依据资料出题"这类链路在隔离环境无法验证。
  已改为由 `config.path_config.get_data_dir()` 运行时推导（`api/app.py` 里同样的硬编码一并改掉），读写同源。
  注意：**历史全局学科**的索引本来就只在正式目录里，所以此前的隔离验收从未暴露该问题。

- 语音相关产物**不进仓库**：`speech_stack/`（venv、FunASR 缓存、样本音频）、
  `data/recordings/`（学生录音）、`data/audio_cache/`，以及 `*.pth/*.onnx/*.ckpt/*.safetensors`
  均已在 `.gitignore` 中；`.env` 与 `config/local_secrets.json` 同样不被跟踪。
- **测试隔离（阶段一补验5）**：自动测试用临时目录；真实模型验收用 `EDU_DATA_DIR` 独立目录，
  并由 `/health.data_scope` 门禁拦截；跑完全量测试后正式库全表哈希不变（零写入）。
- **历史残留**：正式库仍有隔离修复前的 113 行孤儿数据与 15 个测试账号（`default_user` 的历史数据
  保持 UNCLAIMED），准确清单与清理方案见 `docs/TEST_DATA_INVENTORY.md`，本轮未删除。
- 记忆与隐私：学习记忆支持查看 / 更正 / 删除 / 关闭，显式设置优先于系统推断；
  未经核验的回答、学生点赞与用户纠正不会成为课程权威知识。
- **凭据（2026-09-26 已处置，仍需轮换）**：
  - 已改：`config/models.py::_BUILTIN_API_KEYS` 清空为空字典；密钥读取顺序 = 环境变量 →
    未跟踪的 `config/local_secrets.json` → 返回空并给出可理解提示（**无内置回退**）。
  - 已改：`config/env.example` 全部改为占位符；新增 `config/local_secrets.example.json` 示例（不含真实值）；
    `.gitignore` 补充 `config/local_secrets.json`、`*.pem`、`*.key`、`speech_stack/`、模型文件与录音缓存目录，
    并补上 `*.db-wal` / `*.db-shm`（SQLite 伴生文件此前未被忽略，含聊天正文与会话令牌，是真实泄露路径）。
  - **遗留风险**：`_BUILTIN_API_KEYS` 的真实值在提交 `85ee0a7`/`a650db2`/`f00c2fa` 中，
    **仍是 HEAD 与 `origin/master` 的祖先**，即随仓库分发。涉及提供方：MiMo（1 个 Key，被 vision/asr/tts 复用）
    与 DeepSeek（1 个 Key）；qwen 字段当时为空字符串，未泄露。**这类密钥必须更换**（删代码不够）。
  - 本轮未停用任何密钥、未 force push、未改写 Git 历史（按用户要求）。范围与轮换步骤见 `docs/CREDENTIALS.md`。
  - 防回归：`tests/test_credentials_hygiene.py`（7 例）扫描已跟踪文件中的密钥形态，**只报 file:line 不打印值**。
  - **2026-09-27 复核**：已跟踪文件中**没有**真实明文密钥（2 处 `sk-…` 出现在
    `tests/test_qwen_vision_reliability.py` 的安全测试里，是断言"日志不得含密钥"的假串）；
    真实密钥仅在未跟踪的 `.env`（2 处，已被 git 忽略）。**轮换仍是待办，且是对外部署的阻塞项**。
- 阶段一迁移会**自动备份**业务库到 `data/backups/<时间戳>/`，可重复执行；不删除历史资料/知识库/会话。
- 历史 `default_user` 数据保留原归属并登记为"待认领"，不静默分配给任何新账号；认领由用户显式触发。
- 阶段一真实验证在真实库中新增了测试账号（`e2e_*` / `stu_*`）及对应作答记录，未自动清理。
- **2026-08-06 的那次历史清理只覆盖了当时已知的泄露密钥**，其"远端干净、四层扫描零残留"的结论
  **不覆盖** `_BUILTIN_API_KEYS`（2026-09-26 核实的 MiMo / DeepSeek 内置值，见上），二者是不同批次。
- 安全事故内部审计报告已移出仓库，保存在 `E:\WORKBUDDY\Game\docs_offsite_backup_20260806\`（不随仓库分发）。
