# 统一伴学重构 · 实施进度与恢复记录

> 最后更新：2026-10-07（本轮会话）
> 目的：重新接手时先读这里，不要从头研究。

## 0. 现场事实（已核实）

- 仓库 `D:\神の文档\Langchain-RAG`，分支 master，基线提交 `1c6fe38`，当前工作区有本轮未提交改动。
- Python：`D:\神の文档\langchain-rag-venv\Scripts\python.exe`（3.14.5）。
- 演示数据：`D:\神の文档\langchain-rag-work\demo_data`（12 项 recover_check 全通过，勿污染）。
- 启动：`powershell -ExecutionPolicy Bypass -File D:\神の文档\langchain-rag-work\start_demo.ps1`
  （后端 8000 / 前端 3000 / ASR 8100）。
- 演示账号 demo_student / demo_teacher / demo_admin；课程 subject `course_01ac138907`（8 份资料）。
- 参考源码在 `.refs/`（已 gitignore）：DeepTutor `f07029c`、Archify `73aaa06`、Study-Mate `4f0b2bc`。
  研究报告：`docs/research/deeptutor-runtime-architecture.md`、
  `docs/research/learning-flow-and-knowledge-map.md`。

## 1. 浏览器验收方式（重要，别重复踩坑）

DSH 沙箱默认禁止命名管道 → Playwright 自带 `launch()` 与 Chromium 渲染进程都会失败
（mojo Check failed）。**本会话文件策略已放宽为 danger-full-access**，但仍推荐用现成运行器：

```
powershell -ExecutionPolicy Bypass -File D:\神の文档\Langchain-RAG\tests\_browser\run.ps1 <脚本.js>
```

它会在同一进程生命周期内启动 Chromium（headless + CDP 9222，带 --no-sandbox 等规避参数），
再跑 node 脚本，最后关浏览器。`tests/_browser/harness.js` 提供 `launch/login/shot`。
截图写到 `tests/_screenshots/`（已 gitignore）。Playwright 从
`D:\神の文档\langchain-rag-work\e2e\node_modules` 绝对路径 require。

## 2. 本轮已落地（后端）

| 文件 | 作用 | 状态 |
|---|---|---|
| `services/model_service.py` | 新增 `chat_with_tools()`（原生 function calling，含观测/记账）；`_call_openai_compatible_api` 支持 extra_payload 与 raw_message 透出 | 已验证：DeepSeek 真实返回 tool_calls |
| `services/material_scope.py` | 统一资料范围：稳定 doc_id（`m:<material_id>` / `f:<filename>`）、4 态（all/selected/empty+invalid）、签名、范围过滤 | 已用演示数据验证（含同名/失效隔离） |
| `services/ai_service.py` | 新增 `retrieve_and_build_context_scoped()`：宽召回→按稳定 ID 过滤→命中不足时按资料名补充检索→合并 | 已用演示数据验证 |
| `services/tutor_session.py` | 统一上下文（tutor_sessions）+ 轮次（tutor_turns）+ 事件（tutor_turn_events，UNIQUE(turn_id,seq) 保证 after_seq 重放不重不漏） | 已 smoke |
| `services/knowledge_map.py` | 学习地图：稳定 node_id（语义指纹）、章节/知识点、先修+环检测、来源片段、覆盖情况、节点进度、下一步建议；与检索知识图谱分离 | 已 smoke（结构与稳定 ID） |
| `services/artifacts_service.py` | 学习成果：用户/学科/资料范围/来源签名/编辑状态/版本；已编辑成果重新生成时新建版本不覆盖 | 已 smoke（版本与 supersede） |

## 3. 下一步（未完成）

后端：`services/tutor_tools.py`（工具注册表+处理器）→ `services/tutor_agent.py`（模型驱动循环+SSE 事件）
→ `api/routes/tutor.py`（/tutor/stream、session、map、node、practice、artifacts、turn 事件重放/取消）
→ 接入 `api/app.py`。

前端：统一伴学三栏工作区、资料库、任务与复习、我的主页、教师四入口、全局视觉重做。

验收：12 项真实流程 + 390/800/1366 截图。

## 4. 设计决定（照此执行，勿另起一套）

- 工具/能力分层：**工具**（检索/读状态/存成果）由模型在原生 tool-calling 里决定调用；
  **能力**（问答/讲解/练习/地图/整理）只提供系统提示词与可用工具子集；
  **权威规则**（权限、判分、掌握度、预算、截止、确认、审计）全部在程序里，模型不能改。
- 身份与学科由程序注入工具参数，工具不接受模型传入的 user_id。
- 资料范围：模型和前端只能传 doc_id；范围解析、失效判定、可见性过滤都在后端。
- 地图与检索图谱分库：`learning_map.db`（学习路线）vs `data/knowledge_graph/`（关系图谱）。
- 成果与作答分离：翻开复习卡片不算独立作答，不改掌握度。
- TTS 相关一律不恢复；语音只保留 ASR 转写。
## 5. 前端改造（已完成）

| 文件 | 作用 |
|---|---|
| `frontend/src/App.tsx` | 新导航：学生「AI伴学 / 资料库 / 任务与复习」，教师「教学助手 / 课程与备课 / 作业与批改 / 学情分析」；头像进入 `/me`；旧网址（/chat、/knowledge、/student/courses、/student/todo、/student/assignments、/learning）全部重定向到新入口 |
| `frontend/src/theme.ts` | 全系统设计规范（颜色/字号/圆角/组件 token），App 与页面共用同一套 |
| `frontend/src/components/TutorWorkspace/` | 统一伴学三栏工作区：资料与学习路线 / 对话与学习活动 / 引用原文·学习成果·执行详情；能力切换（问答·讲解·练习·地图·整理）；节点工具栏（学习/练习/检测/记笔记）；练习卡（后端判分）；引用可点击并在右侧定位原文；窄屏抽屉 |
| `frontend/src/pages/StudentLearningPage.tsx` | 学生默认入口，支持 `?subject=&docs=&node=` 深链 |
| `frontend/src/pages/MaterialsPage.tsx` | 资料库：创建学科 → 上传 → 状态 → 选资料 → 开始伴学；并承接"知识图谱（概念关系分析）"（lazy，不进首屏） |
| `frontend/src/pages/TasksPage.tsx` | 任务与复习：逾期/今天/未来三段，具体动作按钮，计划调整草案确认 |
| `frontend/src/pages/ProfilePage.tsx` | 我的主页：账号、真实学习记录、最近成果、学习记忆、偏好与设置（按角色限制） |
| `frontend/src/components/Teacher/TutorAssistantPanel.tsx` | 教师教学助手（备课/教案/薄弱点练习/思维导图/自由问答），不含学生话术 |
| `frontend/src/pages/TeacherPage.tsx` | 四入口重组，旧的 materials/plans/classes/students 深链兼容 |
| 删除 | WorkbenchPage / MyCoursesPage / KnowledgePage / TodoPage / AssignmentsPage / LearningPlanPage / KnowledgeManager（已无引用，功能都有新入口） |

## 6. 已验证结果（2026-10-07）

**后端（真实模型 + 真实演示数据）**
- RAG 问答：2 次真实工具调用 → 1 条引用 → 回答含真实依据；通用知识单独标注。
- 资料范围：只选 1 份资料时，引用与命中都只来自该资料；范围外提问如实说明"资料未覆盖"。
- 学习地图：演示课程生成 12 个节点，2 份资料如实标为未覆盖（coverage 交代）。
- 节点练习：生成 3 道题（答案不下发）→ 提交 → 后端判分"本次答对"→ 知识点状态更新为"练习中"。
- 节点学习：生成讲解并保存成果，刷新后成果仍在。
- 单元测试 `tests/test_tutor_core.py`：范围四态/稳定 ID/事件重放/取消/地图校验与稳定 ID/成果版本/工具鉴权，全部通过。
- 全量回归 `pytest tests -n 4`：退出码 0。

**浏览器（真实点击 + 截图，`tests/_screenshots/`）**
- 学生端 1366：工作区、节点选择、问答引用、引用定位、资料库、任务与复习、我的主页 —— 见 `v2_*` / `v3_*` 截图。
- 移动端 390：顶部"资料与路线 / 引用"抽屉入口正常。
- 教师端 1366 / 800：四个入口页面渲染正常。
- 生产构建 `npm run build` 成功；首屏约 900 KB（与改造前 874 KB 基本持平），知识图谱被懒加载为独立 chunk。

## 7. 已知限制与后续

- 知识地图生成与节点讲解依赖模型，单次约 10-20 秒；已做"资料签名未变则复用"避免重复调用。
- 教师教学助手目前为单栏面板，未套用学生端的资料/路线侧栏（教师课程资料切换走页面顶部下拉）。
- NotebookLM 官方文档正文因网络不可达未取得，其分层只作低置信产品参考（见 THIRD_PARTY_REUSE.md）。
- `services/agent_runtime.py`（旧的确定性 Agent）仍被部分接口/工具复用（`get_learning_state` 等），
  尚未整体删除；统一伴学路径不再使用它的固定步骤编排。下一轮可评估合并。

---

# 第二轮（2026-10-07）：认证状态修复 + 真实拖拽导入

## 交付 1：未登录/登录失效重复报错

真实根因（详见 `docs/AUTH_FIX.md`）：
1. Axios 拦截器对**每个** 401 都派发一次失效事件，而 TasksPage 一次加载并发 6 个个人接口；
2. 侧栏 / 顶栏(CSS 隐藏但仍挂载) / 抽屉里各有一个 UserAuth 实例，每个都 toast → 6×2=12 条；
3. 未登录时页面照常发起个人接口请求；
4. 403 与 401 未在状态层区分，注销/切号存在竞态。

修复：新增 `api/authState.ts`（唯一状态源：anonymous/initializing/authenticated/expired +
登录代次 generation + 同一令牌只处理一次失效）、拦截器单次失效与陈旧响应丢弃、`AuthContext`
单一提示入口、`App.tsx` 的 `<Protected>` 未登录不挂载页面（个人接口一个都不发）。
验证：`tests/_browser/verify_auth.js` 12/12。

## 交付 2：真实拖拽导入 + 格式扩展

新增：`rag/formats.py`（能力单一来源）、`rag/loaders/pptx_loader.py`、`spreadsheet_loader.py`、
`legacy_office_loader.py`、`extended_loader.py`、`services/office_converter.py`、
`services/archive_service.py`、`services/import_service.py`、`api/routes/knowledge.py` 的导入接口；
前端 `api/import.ts` + `components/UploadDropzone`（真实拖拽、队列、摘要、刷新恢复）。
格式能力与配置见 `docs/FORMAT_SUPPORT.md`，接口契约 `docs/IMPORT_API_CONTRACT.md`。

## 本轮修掉的两个真实回归（端到端验收发现）

1. **个人学科资料全部不可见**：`student_visible_document_names` 没有 `personal` 分支，
   落到课程分支用 `course_id=None` 查课程资料，返回空集合 →
   `GET /knowledge/{sid}/documents` 把学生自己上传的资料全部隐藏（界面显示“资料 0 份”、
   资料范围选择为空）。已修复并加回归测试 `tests/test_personal_subject.py`（4 项）。
2. **note_service 的 mock 点被改变**：上轮把检索改为`retrieve_and_build_context_scoped` 后，
   既有测试 patch 的 `retrieve_and_build_context` 不再生效。已改回“无 scope 时走原入口、
   有 scope 时才走范围检索”，保持既有契约。
   同时更新 `test_phase1_hardening` 中“非白名单账号不能创建学科”的旧断言 ——
   该契约已按产品要求刻意改变（个人学科人人可建），新断言改为保护**历史全局学科**的写操作。

## 本轮验收结果

- 导入端到端（隔离数据，真实模型）：`tests/_browser/verify_import.py` **32/32**
- 浏览器真实拖拽：`tests/_browser/verify_dragdrop.js` **6/6**（390/800/1366 截图）
- 认证状态：`tests/_browser/verify_auth.js` **12/12**
- 后端新增测试：`test_formats_import.py` / `test_pptx_loader.py` / `test_archive_service.py` /
  `test_formats.py` / `test_import_service.py` 共 42 项
- `ruff check .` 0；`npx tsc --noEmit` 0；`npm run build` 成功
- 前端 `npm run lint` **无法运行**：仓库里没有 ESLint 配置文件（既有情况，非本轮引入）

## 已知限制

- `.ppt/.doc` 需要 LibreOffice（本机未装，能力清单如实标 false 并给配置方法）；
  `.rar` 需要外部 unrar/7z。除此之外不需要任何额外安装。
- 单文件上传上限仍是 20MB（沿用既有规则，未放宽）。
- 导入任务在内存后台线程执行，进程重启后任务状态保留在库里但不会自动续跑（可重试）。

---

# 第三轮（2026-10-07）：上传限制统一 + 旧格式与 RAR 真正可用

## 1. 上传限制（唯一来源 rag/formats.py，capabilities 全量下发）

| 项 | 默认 | env |
|---|---|---|
| 普通文档单文件 | 200 MB | `IMPORT_MAX_DOCUMENT_MB`（兼容旧 `IMPORT_MAX_FILE_MB`） |
| 压缩包单文件 | 500 MB | `IMPORT_MAX_ARCHIVE_MB` |
| 展开后单个成员 | 200 MB | `IMPORT_MAX_EXTRACT_FILE_MB` |
| 单批展开总量 | 1024 MB | `IMPORT_MAX_EXTRACT_TOTAL_MB`（旧 `max_total_mb` 同义） |
| 文件数量 / 递归深度 / 单任务超时 | 100 / 2 / 300s | `IMPORT_MAX_FILES` / `_DEPTH` / `_SECONDS` |

后端按"是否压缩包"取不同上限；`knowledge_service` 的 20MB 硬编码已删除；
Vite dev 代理与 uvicorn 本身不限请求体（生产用 Nginx 时需 `client_max_body_size 512m`）。
超限一律返回"实际大小 / 上限 + 类型"，绝不继续建库。

## 2. 旧格式与 RAR：本机 0 安装即接通

- **旧 .ppt/.doc**：检测顺序 `OFFICE_CONVERTER_PATH` → LibreOffice → **Microsoft Office COM(pywin32)** → 无。
  本机装了 Office 16，实测 COM 把 pptx 转成真实 107KB .ppt 并转回。
  `OFFICE_CONVERTER=auto|libreoffice|msoffice|none` 可强制或停用。
- **.rar**：`rarfile` + 后端探测（unrar/unar/7z/7zz → **系统 `%SystemRoot%\System32\tar.exe`(bsdtar)**）。
  实测 `seektest.rar` 解出成员并导入；加密/solid/分卷分别给
  `archive_encrypted` / `archive_unsupported` / `archive_corrupt`（已用外部工具复核，是能力边界不是 bug）。
- 新依赖：`pywin32==312; sys_platform == "win32"`（COM 转换用，缺失时能力清单如实标 false）。

## 3. 本轮验收

- 大文件（真实 25.8MB PPTX / 47.8MB ZIP）：`tests/_browser/verify_large_import.py` **25/25**
  —— 均被接受、解析、建库、检索；导入期间 `/health` 与 capabilities 5/5 可用（最慢 0.67s）；
  引用保留原文件名+幻灯片号；刷新可恢复；重复内容不重复导入；全成功时 retry 明确 400。
- 旧格式与 RAR：`tests/_browser/verify_legacy_formats.py` **13/13**
  —— 真实旧 PPT 命中 LEGACY-9241 且引用为 `旧课件.ppt` 第 1 页；真实 RAR 解出成员；加密 RAR 明确失败。
- 浏览器限制与能力提示：`verify_limits_ui.js` **9/9**（25.8MB/47.8MB 提交前被拒且不发请求，原因精确到文件大小与上限）、
  `verify_capability_ui.js` **3/3**（`OFFICE_CONVERTER=none` 时列出"暂不可用格式与原因"，拖入 .ppt 被拒）。
- 后端新增/更新测试：`test_upload_limits.py`、`test_office_converter.py` 等；全量 `pytest tests -n 4` 见提交说明。
- 顺手修：登录态校验遇到网络/500 时界面会永久停在"正在恢复登录状态…"且无法重试 —— 现在如实提示并提供「重试」。

## 4. 仍存在的边界（如实记录）

- RAR 内**中文目录/同名文件/混合格式**与 ZIP/7z 共用同一套成员映射与去重代码（有单测），
  但本轮没有用"真实含中文目录的 RAR"验证 —— 机器上没有 RAR 压缩器（RAR 只能由 WinRAR 生成）。
  需要时装 WinRAR 后可自行打包复验。
- Office COM 属于桌面自动化：单机演示可用，服务化部署建议改用 LibreOffice CLI（设 `OFFICE_CONVERTER_PATH`）。

---

# 第四轮（2026-10-07）：真实压缩包导入的可靠、可恢复与可解释

用户真实上传两次都出问题：(a) 367MB 的 16 份课件 zip 只导了 8 份就超时，页面却显示"已完成"；
(b) 莫大物化.7z 直接失败，页面写"压缩包损坏"。本轮只修导入链路，不扩大重构。

## 1. 为什么超时（实测归因，见 docs/IMPORT_TIMEOUT_RCA.md）

- 逐张图片的**视觉调用串行**，且单次调用约 40s；解析本身只要 0.6s/份。
- 超大图（5439x4501、8294400 像素）**不缩放着送**，必然被提供方拒绝 —— 又慢又丢内容。
- 超时判定只在"每个文件开始前"检查整批 300s，单个文件可无限拖住整批；
  触发后只写一条 warning，状态仍按条目标成功 → "succeeded"。
- 未处理的 8 个成员**没有 item**，计数与界面完全看不见；也没有继续入口。

## 2. 改了什么

- **状态与计数**：新增 total/processing/unprocessed，保证 total = ready+processing+failed+unprocessed+skipped；
  有未处理或失败即 partial；未进入索引不得标"可学习"；旧任务用 archive_summaries.extracted 回填（只改视图不改历史行）。
- **可继续**：`POST /knowledge/imports/{job_id}/continue`，从保留原包重跑剩余成员，
  已 ready 的按 content_sha + 解析配置 + embedding 版本复用；计划与原包落库，跨重启可恢复；
  单科建库加锁，避免并发覆盖索引。
- **超时与卡死**：单文件 `IMPORT_ITEM_MAX_SECONDS=180`（只失败该文件）、
  单张视觉 `VISION_IMAGE_TIMEOUT_SECONDS=30`、单批预算默认 300→900s（有实测依据）。
- **提速不丢内容**：超大图先等比缩放再送审、同批图片按内容去重、
  受控并发 `VISION_MAX_CONCURRENCY=3`；原生文字/表格/备注优先，不整页识图。
- **计时可解释**：每条目 timings（parse/vision/calls/cached/deduped/timeout），任务 stage_timings。
- **7z 目录根因**：py7zr 的 getnames() 对目录**不带结尾斜杠**，旧代码按 `endswith("/")` 判目录 →
  12 个目录被当文件 `read_bytes()` → Windows PermissionError → 被误报 `archive_corrupt`。
  改为按 `FileInfo.is_directory` 判定 + 解压后 `path.is_dir()` 兜底；目录不进 targets、不读取；
  单成员失败只跳过该成员（新错误类型 `archive_member_unreadable`）；解包改到受控目录。
- **顺带**：`services/rag_loader.load_rag_stack()` 修复后台线程导入 rag 与预热线程并发导致的
  `deadlock detected by _ModuleLock('rag.rag_service')`（曾让第一轮"继续"直接失败、0 条目）。

## 3. 验收结果

- 367MB / 16 份课件：**16 份全部可检索**（753 分块）；8 份剩余用 **244s** 完成；
  第8章"消隐算法"、第9章"颜色模型"现在命中正确章节（此前只能命中第10章）。
- 莫大物化.7z：**0 failed / 0 skipped**，92 个成员全部被发现，12 个目录零失败；
  按预算分批推进，界面显示"部分完成 16/92"并提供「继续处理剩余资料」。
- 浏览器实测点击「继续处理剩余资料」→ 真实调用 continue 接口 → 队列继续推进，已完成的 16 份不重复。
- ruff 0；pytest tests -n 4 全绿；npx tsc --noEmit 0；npm run build 成功。
