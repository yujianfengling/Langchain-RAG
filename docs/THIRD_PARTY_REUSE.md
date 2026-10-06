# 开源参考与复用清单（2026-10-07）

本轮升级以 DeepTutor 为主要参考，Archify / Study-Mate 用于知识地图与交互补充，NotebookLM 仅作产品层参考。
所有参考仓库只用于**阅读与设计对照**，放在 `.refs/`（已 gitignore，不入库）。

## 1. 读取的真实版本

| 项目 | 仓库 | 本轮读取的提交 | 许可 |
|---|---|---|---|
| DeepTutor | https://github.com/HKUDS/DeepTutor | `f07029cfcf2c8dfccdb671cdfc343db8334f5741`（2026-10-04） | Apache-2.0 |
| Archify | https://github.com/tt-a1i/archify | `73aaa0696e8f72c232ea710e6fa94fd953f3e773`（2026-10-05） | MIT |
| Study-Mate | https://github.com/Miaotofu01/Study-Mate | `4f0b2bc56272bd3a7e5eb911b6220a8eedfb597a`（2026-10-04） | MIT |
| NotebookLM | 官方帮助文档（见下） | 仅产品说明 | 闭源，不接入 |

研究报告（含行号级证据）：
- `docs/research/deeptutor-runtime-architecture.md`（17 行采用表、FastAPI 骨架）
- `docs/research/learning-flow-and-knowledge-map.md`（25 行采用表、节点结构与状态机建议）

**NotebookLM 说明**：本轮对 `support.google.com` / `notebooklm.google` 的抓取全部失败（网络不可达），
只确认了官方帮助页标题与 URL，未取得正文。因此其"资料—问答—成果"分层只作为产品信息架构参考，
**不作为已核实的实现依据**，也不接入任何 Google 服务。

## 2. 采用表（参考能力｜版本与源码位置｜实际设计｜采用方式｜本项目落地｜验证方式）

| 参考能力 | 版本与源码位置 | 实际设计 | 采用方式 | 落地位置 | 验证方式 |
|---|---|---|---|---|---|
| 统一运行入口（服务 → 引擎 → 能力） | DeepTutor `f07029c` `app/service.py:19,35`；`runtime/turn_engine.py:11`；`runtime/orchestrator.py:61` | 入口按能力路由，能力只决定提示词与工具子集 | 适配移植 | `services/tutor_agent.py`、`api/routes/tutor.py` | 一轮请求只走一个能力，事件序列与落库一致 |
| 统一上下文 UnifiedContext | `core/context.py:86,129-148` | 单份上下文贯穿运行与工具，含范围/节点/会话/目标 | 适配移植（裁剪） | `services/tutor_session.py:load_context/save_context` | `tests/test_tutor_core.py::test_context_roundtrip_and_scope` |
| 原生 tool-calling 决策环 | `agents/loop/agent_loop.py:376,453`；`pipeline.py:106` | 有 tool_calls 继续、无则终轮；轮数/工具数/耗时都有上限 | 参考后实现 | `services/tutor_agent.py:stream_turn` | 真实运行记录里 tool_trace 只包含真正调用过的工具 |
| Tool 注册表（惰性/别名） | `runtime/registry/tool_registry.py:26,151`；`tools/builtin_specs.py:20` | 名称→(处理器, 说明, JSON Schema, 是否用模型/写数据) | 直接复用设计（Python 重写） | `services/tutor_tools.py:TOOLS` | `test_tool_schemas_are_valid_json_schema` |
| 工具组合与鉴权 | `agents/_shared/tool_composition.py:170`；`scoped_registry.py:129-150` | 纯函数按能力组合；执行处拒绝越权工具 | 适配移植 | `tools_for_ability` + `execute_tool` | `test_tools_filtered_by_ability`、`test_execute_rejects_unknown_and_forbidden_tool` |
| 身份由程序注入 | `core/tool_protocol.py` 的调用约定 | 工具不接受模型传入的 user_id/subject_id | 适配移植 | `execute_tool` 剥离身份字段 | `test_execute_ignores_model_supplied_identity` |
| 轮次事件持久化 + `after_seq` 重放 | `services/session/sqlite_store.py:284-459`（`UNIQUE(turn_id,seq)`）；`app/service.py:145` | 先落库再推送，断线按序号补齐 | 适配移植 | `tutor_turn_events` + `GET /tutor/turns/{id}` | `test_turn_events_and_replay` |
| 取消与终态 | `app/service.py:263` | 取消只置标记，步进检查点生效，终态不可再取消 | 适配移植 | `tutor_session.request_cancel/is_cancelled` | `test_turn_cancel_semantics` |
| 三层学习模型（进度→模块→知识点） | `learning/models.py:80-108,532-582` | 章节/小节/知识点 + 类型 + 先修 + 来源 | 适配移植 | `services/knowledge_map.py` | `test_validate_map_structure` |
| 路线生成 coverage 交代 | `learning/topic_generation.py:308-582` | 每份资料都要在 covered/uncovered 里有交代 | 适配移植 | `generate_map` + `collect_evidence` | 演示课程生成 12 节点、2 份资料标为未覆盖 |
| 稳定 ID / 重建保身份 | `topic_generation.py:298-305`；`service.py:178` | 语义指纹 + 父路径决定 node_id，重建不归零 | 直接复用设计 | `knowledge_map.stable_node_id` | `test_stable_node_id_and_regeneration_keeps_identity` |
| 掌握度封顶 / 证据不足不判掌握 | `learning/mastery.py:24-37` | 沿用本项目既有 mastery_service（本轮不改口径） | 不采用（本项目已有更严格口径） | `services/mastery_service.py` | 既有 `test_phase1_hardening.py`、`test_mastery_rebuild.py` |
| 分类型硬闸门 | `learning/policy.py:36-111` | 只把"独立作答且掌握度达阈值"写作"检测通过" | 参考后实现 | `api/routes/tutor.py:_sync_node_from_attempt` | 通过状态不可手动设置（接口 400） |
| Book artifact（block 级编辑、KB 指纹） | `book/models.py:229-547` | 成果带来源签名/编辑标记/版本，不覆盖用户修改 | 适配移植（简化为单表） | `services/artifacts_service.py` | `test_artifact_regenerate_respects_user_edit` |
| 结构化校验诊断 | Archify `73aaa06` `validator.mjs:38-85` | 每条诊断带 code/subject/evidence/fix | 适配移植 | `knowledge_map._validate_map` | 诊断码断言 |
| DAG 先修 + 理由 | Study-Mate `4f0b2bc` `curriculum.schema.json:11-55` | 先修边 + 环检测，成环时断开并记录 | 适配移植 | `_find_cycles` | `test_validate_map_structure` 的 `prereq_cycle` |
| 状态词表单一来源 | Study-Mate `scripts/statuses.py:1-52` | 状态枚举与配色只从一处取 | 直接复用设计 | `knowledge_map.NODE_STATUSES/STATUS_LABELS` + `MapTree` | 前端按 status_labels 渲染 |
| 资料—问答—成果分层 | NotebookLM 官方帮助（正文未取得，低置信） | 同一工作区内资料/对话/成果三区 | 参考后实现（产品层） | `components/TutorWorkspace` | 浏览器截图 |

**没有采用的部分（明确记录）**：DeepTutor 的 WS v2 协议、多智能体协作、Book 的 block 级富文本编辑、
向量库与 Embedding 方案、代码执行沙箱——本项目要么已有等价实现（自有 FAISS+BM25 检索、SSE 协议、
既有掌握度与复习调度），要么与课程场景无关。

## 3. LangChain 的真实参与点（本项目要求 LangChain 是一等公民）

| 环节 | 文件 | LangChain 承担的工作 |
|---|---|---|
| 文档加载 | `rag/loaders/pdf_loader.py`、`text_loader.py`、`docx_loader.py`、`markdown_loader.py` | `PyPDFLoader` / `TextLoader` / `Docx2txtLoader` / `UnstructuredMarkdownLoader` 抽取文本 |
| 文档切分 | `rag/text_splitter.py` | `RecursiveCharacterTextSplitter`（`CHUNK_SIZE/CHUNK_OVERLAP` 实际生效） |
| 向量化 | `rag/embedding.py` | `langchain_community.embeddings.HuggingFaceEmbeddings` + `Embeddings` 接口，逐批容错 |
| 文档抽象 | `rag/vector_store.py`、`rag/hybrid_retriever.py`、`rag/rag_service.py` | `langchain_core.documents.Document` 贯穿检索与上下文组装 |
| 思维导图链 | `mindmap_service.py` | `ChatPromptTemplate | ChatOpenAI` 链（既有能力，本轮保留） |

**本项目自己实现（非 LangChain）**：FAISS + BM25 混合检索与 RRK 融合、资料范围过滤与稳定 doc_id、
统一 Agent 运行环与工具注册表、学习地图与稳定性 ID、成果版本管理、掌握度与复习调度。

**统一伴学主链路**：`资料解析 → 文档切分 → 本地向量化 → 有权限的资料检索 → 上下文组装 →
结构化生成 → 真实引用与原文核对`（`services/material_scope.py` + `services/ai_service.py:
retrieve_and_build_context_scoped` + `services/tutor_agent.py`）。问答、地图、节点学习、练习、
学习整理、教师备课全部走这一主干。

## 4. 许可与署名

- DeepTutor（Apache-2.0）、Archify（MIT）、Study-Mate（MIT）均允许在注明来源的前提下参考与复用。
- 本项目的实现为**独立 Python/TypeScript 重写**，未逐段拷贝上述仓库源码；
  借鉴的是数据模型、注册表/协议设计与校验思路，已在文件头注释与本文档中注明出处。
- 交付/答辩材料中如需引用，请保留本文件与 `docs/research/` 两份研究报告。

---

## 2026-10-07 布局轮：AI 伴学工作台信息层级（采用位置与出处）

本轮只改前端布局，不改检索/权限/会话隔离。参考实现均**只读源码**后按需重写，
未整文件搬运，也未引入第二套系统。版本与 SHA 见本文档开头表格。

| 我们的实现 | 位置 | 参考出处（文件:行） | 许可证 | 采纳了什么 / 没采纳什么 |
|---|---|---|---|---|
| 右栏默认收起、关闭后不留占位列 | `frontend/src/components/TutorWorkspace/index.tsx`（rightOpen 条件渲染 + .tutor-panel-overlay） | DeepTutor `web/components/reading/workspace/ReadingWorkspace.tsx:602-611` | Apache-2.0 | 采纳"关栏即去掉列轨道"；未采纳拖动改宽与 4 轨 grid（我们只读三种内容） |
| 右栏偏好持久化（按学科） | 同上 panelPrefKey + sessionStorage | DeepTutor `web/components/reading/workspace/useLearningMode.ts:44,67-71` | Apache-2.0 | 采纳"按工作区归属的开关快照"；键名与结构自建 |
| 覆盖式抽屉 + 遮罩 + Esc/外点关闭 | `tutor.css` .tutor-panel-overlay 与键盘 useEffect | Archify `reader-layout.js:145-166,270-278,354-376` | MIT | 强采纳"默认收起 + 记忆 + overlay 关闭"；未采纳其 SVG 尺寸测量 |
| 收起后入口仍可发现 | `App.tsx` 导航折叠按钮、`tutor-main-actions` | Archify `reader-layout.js:361` | MIT | 采纳可发现性与基础无障碍 |
| 学习路线状态中文 + 当前节点高亮 | `MapTree.tsx` STATUS_FALLBACK / statusText | DeepTutor `SourceNavigator.tsx:135-138,288-324` | Apache-2.0 | 采纳"当前行归约高亮"；滚动居中未做 |
| 资料范围"一行摘要 + 点开勾选" | `TutorWorkspace/index.tsx` Popover + .tutor-scope-btn | DeepTutor `web/components/workspaces/WorkspaceResourcePicker.tsx:116-152,172-176` | Apache-2.0 | 部分采纳：只取勾选交互与计数文案；未采纳其 settings 页资源模型 |
| 窄屏只开一个面板 | `useIsNarrow`(900/1559) + .tutor-drawer | DeepTutor `ReadingWorkspace.tsx:271-304` 的思路 | Apache-2.0 | 采纳原则；390/800 具体断点三仓库都没有，为自定义 |

**明确未采用**：DeepTutor `SessionViewerPanel.tsx`（1361 行，含 tab 组/命令式 handle/桥接层）——
我们的右栏只展示三种只读内容，引入等于重复系统；Archify `node-finder.js`/`guided-views.js`
的 SVG 相机定位与我们无关。**三个仓库都没有"点 [1] 打开侧栏并定位真实来源"的现成实现**
（它们的引用 [n] 只是文档内锚点滚动）；这条交互是本项目原有实现，本轮只把它接到新的可开关面板上。
