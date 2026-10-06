# 学习流程与知识地图：DeepTutor / Archify / Study-Mate 深研笔记

> 版本锚点（已用 git 核实）
> - DeepTutor: \`f07029c\`（\`f07029cfcf2c8dfccdb671cdfc343db8334f5741\`）
> - Archify: \`73aaa06\`（\`73aaa0696e8f72c232ea710e6fa94fd953f3e773\`）
> - Study-Mate: \`4f0b2bc\`（\`4f0b2bc56272bd3a7e5eb911b6220a8eedfb597a\`）
> 参考根：\`D:\神の文档\Langchain-RAG\.refs\<repo>\`

---

## 1 DeepTutor：目标→路线→知识点→学习活动

### 1.1 数据模型（\`deeptutor/learning/models.py\`）

| 层 | 类型 | 关键字段 | 行号 |
|---|---|---|---|
| 路线 | \`LearningProgress\` | \`book_id\`, modules, mastery_levels, qualitative_mastery, knowledge_types, quiz_attempts, error_records, learning_evidence, repetition_states, review_queue, pending_question, learner_profile, version | L532-582 |
| 模块 | \`LearningModule\` | id/name/order, \`pass_threshold=0.7\`, \`objective\`（一句话目的，是"重塑知识点"的契约）, knowledge_points | L94-108 |
| 知识点 | \`KnowledgePoint\` | id/name/\`type\`, module_id, \`prerequisite_ids\`（**必需目标，不是展示顺序**）, \`topic_source_ids\`（来源出处） | L80-91 |
| 类型 | \`KnowledgeType\` | memory / concept / procedure / design（含中文 legacy 映射） | L24-34 |
| 阶段 | \`LearningStage\` | diagnostic→explain→feynman_check→practice→error_diagnosis→review→completed | L61-72 |
| 来源 | \`TopicSource\`/\`TopicSourceKind\` | goal/book/notebook/knowledge_base/file/chat/question_bank/cowriter/partner_group; id, source_id, label, excerpt, position, available, metadata | L353-394 |
| 学习者 | \`LearnerProfile\` | prior_knowledge/target_level/time_budget/preferences/\`teaching_strategy\`(probe_first/teach_first)/notes | L439-485 |

要点：**知识点带显式先修与来源出处**；\`mastery_levels\`（量化）与 \`qualitative_mastery\`（布尔）是两套闸门；每模块有"能力型 objective"用来约束后续 AI 重塑知识点（L101-107）。

### 1.2 生成（\`deeptutor/learning/topic_generation.py\`）

- 入口 \`generate_topic_draft\`（L527-582）：先 grounding 来源 → 组装 prompt → LLM 返回 JSON → \`materialize_modules\` 校验 → 返回 modules + coverage 报告。
- \`ground_topic_sources\`（L241-272）：逐源 grounding；KB 走 \`rag_search(top_k=4)\` + 文档清单 inventory（L197-238），单文件走隔离进程抽取（L144-194）。单个来源失败只标 \`available=False\`，不拖垮整条路线（L224-237）。
- 边界常量（L24-39）：最多 16 源、每源摘录 4000 字、总 24000 字、模块 8（上限 20）、每模块最多 7 个目标、KB 文档名最多列 60。\`module_limit_for\` 按材料文档数扩展（L458-468）。
- \`materialize_modules\`（L308-455）：draft 宽松（坏项丢弃并记 \`discarded_modules\`）、用户确认时 \`strict=True\`（宁可报错不静默丢）。**身份保持**：已存在的 module/kp id 可复用，新增实体用 \`_new_entity_id\`（uuid 12 hex，L298-305），避免"删除后的 id 继承旧证据"。
- 生成 prompt：\`deeptutor/learning/prompts/zh.yaml\` 的 \`topic.system/user\`（L78-96）——要求 3-{module_limit} 模块、每模块 2-7 目标、每模块写"能力型 objective"、并对 \`documents\` 里**每个文件都有交代**（\`materials\` 字段，覆盖报告 \`_coverage_report\` L495-524 校验）。

### 1.3 存储（\`deeptutor/learning/storage.py\`）

SQLite（WAL），核心表（L403-513）：

- \`mastery_paths(path_id PK, state_json, revision, owner_session_id)\` —— 乐观并发用 revision（L407-414）。
- \`mastery_interactions\` —— \`UNIQUE ... WHERE status IN ('registered','awaiting_input','answered')\`，**结构上保证一条路径只有一个未决问题**（L429-445）。
- \`mastery_events(path_id, revision, event_type, payload_json)\` —— 事件溯源（L447-458）。
- \`mastery_learning_evidence(path_id, ordinal, knowledge_point_id, timestamp, source, assessment_type, result, quality, evidence_json)\` —— 证据独立投影表（L460-475）。
- \`mastery_path_leases\` —— 一条路径同一时刻一个变更中 turn（L482-487）。
- \`mastery_topic_meta\` / \`mastery_topic_sources\` —— 主题与来源（L489-513）。
- 写入走 \`transaction\`/\`mutate\`（L1092-1218），读走 \`load\`；\`save\` 校验 revision。

---

## 2 练习生成、判分、门槛、知识点分类

### 2.1 判分（\`deeptutor/learning/grading.py\`）
- \`grade_answer\`（L13-49）：choice 去空格精确；short 精确或 **≤30 字时相似度≥0.85**；open 按 expected 切关键词，**命中率≥0.6**。
- \`classify_error\`（L52-61）：空答→metacognitive，否则 application；四类 taxonomy（structural/deviation/application/metacognitive，models L36-40）由后续 LLM 在 error_diagnosis 阶段判定。

### 2.2 掌握度评分（\`deeptutor/learning/mastery.py\`）
- \`compute_mastery\`（L24-37）：最近作答**递近加权**(0.5,0.7,0.85,0.95,1.0)，且 **证据不足时封顶**（1 次≤0.5、2 次≤0.8）——"一两次蒙对不算掌握"。

### 2.3 硬闸门（\`deeptutor/learning/policy.py\`）
- \`QUANTITATIVE_GATE\`（L36-39）：memory/procedure 需 **≥0.9**（注释：对齐 Alpha School "90% before advance"）。
- \`QUALITATIVE_TYPES\`（L44-46）：concept/design 走 \`qualitative_mastery\` 布尔（费曼解释由 \`mastery_assess\` 判定），没有唯一标准答案就不做字符串判分。
- \`is_mastered\`（L103-111）：\`mastery_source\` 区分 \`system\` / \`learner\`（学习者自述可推进路线，但对外报告必须暴露不同来源，不冒充证据）。
- \`next_objective\`（L219-326）优先级：① 未决问题 → ② 到期复习 → ③ 第一个未掌握目标（**闸门即游标**，已掌握自动跳过 = "test out"）→ ④ complete；\`new\` 时按 \`teaching_strategy\` 选 probe/teach。

### 2.4 间隔重复（\`deeptutor/learning/scheduler.py\`）
- 类型化间隔序列（L16-21）：memory [0,1,3,7,14,30,60]、concept [3,7,14,30]、procedure [3,7,14]、design [14,28]。
- 类 FSRS 的 stability/difficulty/retrievability（L127-327），失败回退、延迟成功奖励、同会话重复不重复拉伸（L267-278）；\`replay\`（L475-502）按证据时间序**确定性重放**；\`forgetting_risk\`（L336-354）叠加逾期/错题/lapse。

### 2.5 出题工具（\`deeptutor/capabilities/mastery/tools.py\`）
- 工具清单 \`MASTERY_TOOL_NAMES\`（L93-108）：status/quiz/grade/skip_question/repair_question/defer_objective/assess/build/mode/profile/revise/paths/switch/leave。
- **答案存服务端**：\`mastery_quiz\` 注册 \`expected_answer\`（\`sensitive=True\`，L842-849），下一 turn 由 \`mastery_grade\` 对存储答案确定性判分；\`PendingQuestion\` 注释明说"答案永不往返模型"（models L243-269）。
- 选项契约（L181-314）：label+body 分离、标签必须 A,B,C…、禁止重复 body、**选项随机洗牌**避免正确答案总在 A（L290-314）。
- **单题未决**：再次 quiz 只会重呈原卡（L1023-1035）。

### 2.6 统一证据入口（\`deeptutor/learning/assessment.py\`）
- 身份 \`(origin_type, origin_ref, turn_id, question_id)\`；\`attempt_identity\` 做幂等键（L212-241，刻意不含 wall-clock，传输重试收敛）。
- \`record_assessment\`（L425-508）：同时落"最新投影(notebook)" + "不可变 attempt" + "关联证据"，跨学习面（Reading/Book/Partner）可通过 \`mastery_path_id+knowledge_point_id\` 更新同一目标；链接必须通过 \`_is_trusted_linkage\` 校验（L280-291）。
- \`_apply_linked_retention\`（L300-386）：\`evidence_id\` 去重，乱序到达时按时间重排 replay，并 \`emit("evidence.recorded")\`。

---

## 3 学习记忆/掌握度：证据如何产生、如何影响后续教学

- 证据类型 \`LearningEvidence\`（models L160-183）：\`evidence_id/question_id/knowledge_point_id/timestamp/source/assessment_type/result/quality/hints_used/attempt_count/confidence/response_time/session_id/turn_id\`。**存原始证据而非只存分数**，因此可重算 retention。
- 产生入口：① \`mastery_grade\` → \`LearningService.grade_interaction\`（service L832）；② \`mastery_assess\` → \`record_qualitative\`（service L1678）；③ 其他学习面经 \`record_assessment\` 关联；④ \`_recompute_objective_state\`（service L1372）。
- 影响后续教学：\`next_objective\` 读 mastery/闸门/复习队列决定下一步；\`map_summary\`（policy L329-380）给 tutor 每回合渲染；\`objective_report\`（L429-495）按需展开单目标完整证据链。tutor 每回合用工具读 gate、记录结果，**教学话术归模型、算术归引擎**（capability.py 设计公理 L18-21）。
- 复习队列 \`build_review_queue\`（scheduler L398-432）+ \`ReviewTask.forgetting_risk/priority/reason\`（models L210-222）。
- 质量换算 \`_resolved_quality\`（scheduler L505-517）：correct=1.0、partial=0.4，并因 hints/attempt_count 扣分。

---

## 4 资料解析 / 建库 / 版本 / 检索 / 引用

- **KB 类型（\`deeptutor/knowledge/kb_types.py\`）**：默认 indexed；连接型指针 \`obsidian/linked/subagent/lightrag_server/ima/weknora/marginnote4/kiwix\`（L62-99）。\`NON_RETRIEVABLE_KB_TYPES\`（L123）与 \`supports_rag_retrieval\`（L131-141）明确"connected ≠ unsearchable"。
- **清单（\`deeptutor/knowledge/manifest.py\`）**：\`KbManifest\`（L113-145）给"检索回答不了的事实"——文件数/是否上传/列表；主 prompt 内嵌截断清单、\`kb_files\` 工具给全量（L12-16）。明确**不报"入库文档数"**（docstore 计数不可靠，L27-34）。
- **版本（\`deeptutor/services/rag/index_versioning.py\`）**：\`EmbeddingSignature(binding,model,dimension,base_url,api_version,role_semantics)\` → sha256[:16]（L46-64）；新布局 \`data/knowledge_bases/<kb>/version-N/{docstore,...}\` + \`meta.json\`（L1-23）；旧布局只读兼容。
- **检索**：\`services/rag/service.py\`、\`smart_retriever.py\`；多 provider（llamaindex/graphrag/lightrag/pageindex/weknora…）。\`rag_tool.rag_search\` 被路线生成与 tutor 调用。
- **引用组织（\`deeptutor/agents/research/utils/citation_manager.py\`）**：把 RAG 结果归一成 \`{title, content_preview(≤200), source_file, page, chunk_id, score}\`（L42-59）；\`citations.json\` 落盘、全局 ID（PLAN-XX、CIT-X-XX）、\`citation_id→ref_number\` 供正文角标（L62-90）。**可复用于知识地图节点的来源片段**。

---

## 5 学习成果（artifact）的保存/再打开/编辑/复用

DeepTutor 里 artifact 有三条线：

### 5.1 Book（\`deeptutor/book/*\`）——最完整的产物模型
- 聚合 \`Book\`（models L517-547）：\`revision\`（乐观并发）、status(draft/spine_ready/compiling/paused/ready/error/archived)、\`kb_fingerprints\`/\`kb_document_fingerprints\`/\`stale_page_ids\`（**KB 漂移检测 → 标记过期页**）。
- \`Spine→Chapter→Page→Block\`：Chapter 有 \`prerequisites\`(章节 id)、learning_objectives、source_anchors（L229-242）；Page 有 \`links\`(deepens/prereq/references)、parent_page_id（L440-474）；Block 有 type/status/params/payload/source_anchors/metadata（L413-432）。
- \`ConceptGraph/ConceptNode/ConceptEdge\`（L250-288）：节点 id slug+label+chapter_id+weight；边 relation=\`depends_on|extends|related\` + rationale。
- 存储：\`book/storage.py\` 原子写 JSON（\`_atomic_write_json\` L54），按 book/page 分文件；\`manifest.json\` 存聚合。
- API（\`api/routers/book.py\`）：创建/确认提案/确认脊柱/编译页；\`insert-block/update-block/delete-block/move-block/change-block-type\`（L972-1071）；\`regenerate-block\`；\`deep-dive\` 子页；\`quiz-attempt\`；\`progress/visit\`、\`bookmark\`；\`export\`；\`pause/resume/rebuild\`；WebSocket 流式编译（L1520）。**产出的每个 block 可单独再生成/改类型/移动 = 编辑与复用**。
- **导入后不重生成**：\`_reset_page_for_force_compile\`（engine L435）与 \`rebuild_book\`（L1172）。

### 5.2 Co-Writer 文档（\`api/routers/co_writer.py\`）
文档 CRUD（L707-861）+ 编辑历史 \`/documents/history\`、\`/history/{op}\`、\`/tool-calls/{op}\`（L600-654），docx 导入导出（L758-824）。

### 5.3 Notebook 记录（\`api/routers/notebook.py\` + \`services/notebook/service.py\`）
Notebook 容器 + \`NotebookRecord\`（service L42-70）；记录增删改查、\`copy_record\`/\`move_record\`、\`export_markdown\`（L460-626）。

### 5.4 通用输出
\`api/routers/outputs.py\` 只有 \`GET/HEAD /{output_path}\`（L75-77），经 \`PathService\` 解析相对路径 → 统一"打开/下载产物"入口。\`services/session/artifact_attachments.py\` 处理回合产物附件。

---

## 6 知识地图：Archify 与 Study-Mate 可复用点

### 6.1 Archify —— 结构校验与交互

**节点/边结构（\`archify/schemas/architecture.schema.json\`）**
- 顶层 required：\`schema_version/diagram_type/meta/components\`（L6-8），\`additionalProperties:false\`。
- component required：\`id/type/label\`；可选 \`sublabel/tag/icon/sources/row/col/pos/size\`（L73-104）。
- \`sources\`（\`common.schema.json#/$defs/sourceReferences\`，L148-163）：\`path\`(必)、\`line\`、\`end_line\`、\`label\`，1-3 条 —— **节点→源码证据**的通用形状。
- \`meta.repository\`：\`url\` + **40 位 revision**（L134-147），链接固定在 commit，验证读该 commit 的 blob（authoring-contract L329-352）——**版本钉死**的参考实现。
- \`connections\`：\`from/to\`(必) + label/variant/fromSide/toSide/route/via/width（L123-149）。
- \`boundaries\`（region/security-group + \`wraps\` 成员 ids，L105-122）——层级/分组表达。
- \`meta.views\`（\`guidedViews\`，common L115-133）：id/label/**focus[]**/note，最多 5 个"引导视图"。

**ID 规范**：\`^[a-zA-Z][a-zA-Z0-9_-]*$\`（common L6-9）。

**校验（\`archify/renderers/shared/validator.mjs\`）**
- 用生成的 JSON-Schema validator；失败抛 \`DiagnosticError\`，每条诊断带 **\`code\`(\`schema/<keyword>\`) / \`severity\` / \`message\` / \`subject({diagramType,path,identity})\` / \`evidence\` / \`supportedFixes\`**（L38-85）。
- \`annotatedPath\` 会把 \`/components/3/label\` 注解成带 \`id/label\` 的可读路径（L4-26）——**面向修复者的报错**。
- 修复顺序（authoring-contract L255-264）：schema→重叠/越界→穿节点/端点方向→交叉/走廊/绕行→标签净空。
- 几何/可读性硬规则（L137-149）：端口 16px 角净空、showcase 每段≥8px、穿无关节点=硬失败。
- \`finalize\` 单命令多门禁：validate→deliver→strict check→真实浏览器 browser-check；**非零退出永不等于成功**（SKILL.md L40-42）。
- 每请求独立 \`.archify/<type>-<slug>-<timestamp>/\` 目录（SKILL.md L15）——与 Study-Mate 的编号/暂存同构。

**交互（PRODUCT.md L27-35 + README_ZH L105）**
- 设计原则：Truth before spectacle / One spatial narrative first / Progressive disclosure / Motion has one bounded owner / Portable proof / Identity without semantic drift。
- 深链接交互：\`#focus=<id>&reach=downstream\`、\`#route=a~b\`、\`#lens=x~y\`——**聚焦、可达性、路径、两节点关系镜头**四种查询。可迁移为知识地图的"聚焦某知识点 / 看下游依赖 / 看两点先修路径 / 看两节点关系"。
- 产物自包含、离线、可导出（PNG/JPEG/WebP/SVG/WebM），Viewer state 不进 canonical export。

### 6.2 Study-Mate —— 学习路线表达

**DAG 数据（\`schemas/curriculum.schema.json\`）**
- node required \`id/title/objective/prerequisites/status/kind\`，\`additionalProperties:false\`；\`id\` pattern \`^[a-z0-9]+([.-][a-z0-9]+)*$\`（如 \`net.ip\`）（L11-15）。
- node 富字段：\`objective/problem/concepts/resources/practice/pitfalls/realworld/mastery\`（L16-40）——**每节点自带"为什么学/常见坑/真实场景"**。
- \`status\` 六态 enum：未开始/学习中/初步理解/能独立应用/需要复习/已通过项目验证（L39）。
- \`edges\` required \`from/to/reason\`（L44-55）——**每条先修边必须写明理由**（示例 L102-120 全是因果句）。
- \`kind\` 三态：概念/实操/实验（L16-19）。

**进度与证据**
- \`progress.schema.json\`：\`nodes{status,mastery,notes}\`（只记**有变化的**节点）、\`misconceptions[{topic,question,misunderstanding,answer_summary,follow_up,importance}]\`、\`project.current\`（L5-42）。
- \`misconceptions.yaml\`：误解库**只追加**、与 progress 的 topic 逐字对应（示例 L1-27）。
- \`assessment.schema.json\`：node/date/**layer L1-L4**/questions[{q,kind,answer,verdict(通过/部分通过/不通过),note}]/mastery/verdict/next/misconceptions（L6-40）。

**教学闭环**
- \`scripts/curriculum.py\`：\`Curriculum.levels()\` 按 prerequisites 算**最长路径分层**，未知前置忽略、成环不死循环（L104-134）；严格/宽松两种 load 策略（L14-24）。
- \`scripts/statuses.py\`：\`DONE_FROM='能独立应用'\`，状态枚举**从 schema 读、全仓唯一映射**，改词表要改七处的教训（L1-19, L45-52）。
- 分层练习（\`.dsh/skills/layered-practice/SKILL.md\`）：L1 理解/L2 改造/L3 排错/L4 应用；**层级上限由 kind 定**（概念止 L3，实操/实验到 L4）；四种题型（选择/开放/实操/评估）；判分三档且**每条结论必须指向具体证据**，"说懂了不是证据"（L63-67）。
- record-keeping（\`.dsh/skills/record-keeping/SKILL.md\`）：状态由主教练亲自读写；**进度只认真"学习进度"文件**，大纲里的 status 是建课时快照、之后不再维护（L113）——避免双写漂移；**实验课通过硬规则**：把该节点与它 prerequisites 里被验收节点都置"已通过项目验证"（L94-99）；学习记录"只在出现可观察证据时写"（L81-83）。
- 产物三份归属：内容文件（讲解）+ 练习题库（出题）+ 渲染页面（脚本），**改内容要重渲、不手改产物**（L67-79）；渲染报 \`文件:行\` 按归属打回。
- 角色分工：curriculum-designer 只出 DAG（\`.dsh/skills/curriculum-designer/SKILL.md\`），practice-evaluator 是题目唯一 owner，产出先落 \`.stage/<角色>-<节点id>/deliver/\` 并写 \`handoff.json\`（practice-evaluator L39-46）。节点粒度"40 分钟一个学习单元"，实验课 3-5 个（L52-58）。

---

## 7 NotebookLM（仅官方说明；证据强度标注）

> ⚠️ 本次 \`web_fetch\` 对 support.google.com / notebooklm.google / workspaceupdates.googleblog.com **全部返回 \`fetch failed\`**（例：\`https://support.google.com/notebooklm/answer/16212283?hl=en\`）。仅 \`web_search\` 可用，且不返回正文摘要。因此下面**只确认到"官方帮助页存在及其标题"这一层**；更细的行为描述未经正文核实，落地前必须人工打开链接核对。

已由 web_search 确认存在的官方帮助页（URL 去掉本地化参数后的规范地址）：

| 官方页面 | 主题 |
|---|---|
| https://support.google.com/notebooklm/answer/16206563 | Create a notebook in NotebookLM（Notebook 是容器） |
| https://support.google.com/notebooklm/answer/16215270 | Add or discover new sources for your notebook（添加/发现来源） |
| https://support.google.com/notebooklm/answer/16179559 | Use chat in NotebookLM（基于来源的问答） |
| https://support.google.com/notebooklm/answer/16212283 | Use Mind Maps in NotebookLM（思维导图） |
| https://support.google.com/notebooklm/answer/16212820 | Generate Audio Overview in NotebookLM（音频概览） |
| https://support.google.com/notebooklm/answer/16262519 | Create and add notes in NotebookLM（笔记） |
| https://support.google.com/notebooklm/answer/14290935 | NotebookLM 官方帮助页（2023-12 起） |

产品层关系（**低置信，待正文核实**）：Notebook 为容器 → 加入 Sources → Chat 基于所选 Sources 作答并给出引用 → Studio 从 Sources 生成 Audio Overview / Mind Map 等成果 → Notes 是可保存的中间产物。**"inline citations 的确切呈现、source selector 的行为、各 Studio 输出的官方 grounding 措辞"本次均未取得官方正文，不应据本笔记实现。**

可采用的稳定结论只有一条：**官方把"资料(Source)—问答(Chat)—成果(Studio)都挂在 Notebook 之下，并用帮助页把三者拆成独立功能"**；这与 DeepTutor 的 Topic→Source/Evidence→Book/Artifact 分层高度一致。

---

## 8 建议：知识地图节点数据结构

融合 DeepTutor（证据/闸门/版本）、Archify（id/sources/revision/诊断）、Study-Mate（DAG/prerequisites/reason/富字段）：

\`\`\`jsonc
{
  "node_id": "kp_7f3a9c21e0b4",          // 稳定且永不复用；重新生成不继承旧 id
  "path_id": "topic_xxx",
  "module_id": "mod_...",
  "name": "子网掩码与网络地址",           // 对应 Study-Mate title/objective
  "type": "concept",                      // memory|concept|procedure|design，决定闸门种类
  "order": 3,
  "prerequisites": ["kp_..."],            // 硬先修（ids），非展示顺序
  "prerequisite_reasons": {"kp_...": "…"},// 每条先修边写理由（Study-Mate 的 reason）
  "source_refs": [                        // 来源片段：节点→材料证据
    {"source_id":"src_1","doc_id":"kb/doc.pdf","chunk_id":"c17",
     "page":12,"locator":4821,"snippet":"…≤200字","score":0.83,
     "doc_hash":"sha256:…"}               // 文档内容 hash，用于判断片段是否过期
  ],
  "hierarchy": {"level": 2, "parent_module": "mod_...", "sibling_order": 3},
  "version": {
    "entity_version": 4,                  // 结构被 revise 的次数
    "source_signature": "emb_sha256_16",  // 所属 KB 的 embedding/索引签名
    "generated_at": 1730000000
  },
  "mastery": 0.72,
  "mastery_source": "system",             // system | learner | ""
  "qualitative_mastery": null,            // concept/design 用 bool
  "status": "learning",                   // new | learning | mastered（由闸门+有无证据推出，不单独存游标）
  "deferred": false,
  "evidence_ids": ["att_..."]             // 追加式；真正事实在 evidence 表
}
\`\`\`

**稳定 ID 规则**（三选一，建议混用）：
1. 人工/AI 生成、需可读时用 slug（Archify/Study-Mate 的 \`^[a-z0-9]+([.-][a-z0-9]+)*$\`）；
2. 重新生成、需防碰撞时用 \`kp_{uuid12}\`（DeepTutor \`_new_entity_id\`）；
3. 跨重建保持身份：用**语义指纹**（name+type+module 的规范化哈希）在重建时匹配旧 id（DeepTutor \`assign_objective_identities\` / \`_objective_fingerprint\`）。
**禁止**：删除后把 id 让给新节点（会继承旧证据）。

**先修**：存 ids + reason；渲染前做 DAG 校验（无环、from/to 存在、实验节点必须有先修）——直接照搬 \`check_curriculum.py\` 的检查项（Study-Mate curriculum-designer L68-70）与 \`curriculum.py\` 的 levels() 分层。

**来源片段**：沿用 Archify \`sourceReferences\` 的 \`{path,line/end_line,label}\` 或 DeepTutor 的 \`{source_file,page,chunk_id,score,content_preview}\`；再加 **doc_hash** 做"片段是否随材料更新而失效"的判断（DeepTutor Book 的 \`kb_document_fingerprints\`→\`stale_page_ids\`）。

**版本**：节点结构版本(entity_version) 与 知识来源版本(embedding signature / 文档 hash) **分开存**；改 embedding 模型必须重建索引（DeepTutor \`EmbeddingSignature\`→\`version-N\`）。

---

## 9 建议：节点学习与检测闭环状态机

**每节点状态**（\`node_state\`，由证据+闸门计算，不单独持久化游标）：

\`\`\`
                    ┌─ deferred（学习者暂缓；永不计数为已掌握，路由优先其他节点）
new ─┬─(probe_first)→ probing ──┐
     └─(teach_first)→ teaching ─┤
                                 ↓
        ┌──────────────────── practice（量化型：memory/procedure）
        │                         │ 答案→判分→写 Evidence
        │                         ↓
        │                    mastered? ──否──┐
        │                         │是        │
        └─────────────────────────┼──────────┘
                                  ↓
teaching/assessing（概念/设计型）→ assess_qualitative（费曼/开放）
                                  ↓
                              mastered
                                  │
        learned ──(next_review_at 到期)──→ review_due → 答对→ learned
                                  │                      答错→ relearning → practice
                                  ↓
                        已通过项目验证（实验节点通过时，连同先修节点置位）
\`\`\`

**判定与转移规则（全部照搬/适配 DeepTutor）**
- 进入 \`mastered\`：
  - memory/procedure：\`mastery_levels[kp] ≥ 0.9\`（\`display_mastery\` 用递近加权 \`compute_mastery\`）；
  - concept/design：\`qualitative_mastery[kp] == true\`（\`mastery_assess\` 判定）；
  - 学习者显式声明：\`mastery_source='learner'\`，可推进但对报表暴露不同来源，**不覆盖 system 证据**。
- 前进不是靠阶段计数器：\`next_objective\` = ① 有未决问题先判分 → ② 有到期复习先复习 → ③ 第一个未掌握节点（跳过已掌握 = test-out）→ ④ complete。
- **单题未决**：\`UNIQUE(path_id) WHERE status IN (registered,awaiting_input,answered)\`，第二次 quiz 只重呈原卡。
- 证据层：\`Evidence{evidence_id 幂等, kp_id, timestamp, source(来源学习面), result, quality, hints_used, attempt_count}\`；重算走 \`replay(type, evidence[])\`，乱序到达按时间重排。
- 复习：类型化间隔 + 类 FSRS stability/difficulty；失败缩短、延迟成功增强；同会话重复练习不重复拉伸间隔。
- 材料更新：文档 hash 变化 → 该来源覆盖的节点进入 \`stale\`，复习/重测优先级提高（DeepTutor Book 的 stale_page_ids 思路）。
- 跨学习面：Reading/Book/Quiz 的证据可经 \`(path_id, kp_id)\` 关联进同一目标，但必须校验链接可信（节点确实属于该路径）后才生效。
- 实验/里程碑：通过时把该节点与其所有被验收先修节点置 \`已通过项目验证\`（Study-Mate 硬规则）。

---

## 10 采用表（参考能力｜版本与源码位置｜实际设计｜采用方式｜落地位置｜验证方式）

| 参考能力 | 版本与源码位置 | 实际设计 | 本项目采用方式 | 落地位置 | 验证方式 |
|---|---|---|---|---|---|
| 路线/模块/知识点数据模型 | DeepTutor f07029c \`deeptutor/learning/models.py:80-108,532-582\` | KnowledgePoint 带 type+prerequisite_ids+topic_source_ids；Module 带 objective | 适配移植 | \`services/knowledge_map\`（或现有 knowledge 模块） | 序列化/反序列化单测 + 先修引用完整性测试 |
| 目标→路线生成+校验 | DeepTutor \`deeptutor/learning/topic_generation.py:308-582\`；prompt \`learning/prompts/zh.yaml:78-96\` | strict/forgiving 两模式；coverage 报告保证每个文件有交代 | 适配移植 | 生成服务 + prompt 文件 | 用坏 JSON/超限输入测 strict 报错；coverage 用例 |
| 稳定实体 ID/重建保身份 | DeepTutor \`topic_generation.py:298-305\`；\`learning/service.py:178\` | uuid 生成 + 语义指纹匹配旧 id | 直接复用 | knowledge_map id 生成器 | 删节点重建后断言旧证据不继承 |
| 确定性判分 | DeepTutor \`learning/grading.py:13-61\` | choice 精确/short 0.85/open 0.6 关键词 | 直接复用 | 判分服务 | 三题型边界用例（30 字、恰好 0.85/0.6） |
| 掌握度评分+置信封顶 | DeepTutor \`learning/mastery.py:17-37\` | 递近加权 + 1/2 次封顶 | 直接复用 | mastery 服务 | 一两次蒙对不得 mastered |
| 分类型硬闸门 | DeepTutor \`learning/policy.py:36-111,219-326\` | memory/procedure≥0.9；concept/design 走定性；闸门即游标 | 适配移植 | 地图路由/next API | 各类型到达阈值/未到达的转移测试 |
| 间隔重复+确定性 replay | DeepTutor \`learning/scheduler.py:16-21,127-502\` | 类型序列+类 FSRS+证据重放 | 适配移植 | 复习调度器 | 证据乱序 replay 等于顺序结果 |
| 统一证据与幂等 | DeepTutor \`learning/assessment.py:212-386,425-537\` | 身份键+attempt 幂等+跨面带证据 | 适配移植 | evidence 表与服务 | 重试同一作答只记一条 |
| SQLite 证据/交互/事件/租约 | DeepTutor \`learning/storage.py:403-513\` | 单未决问题唯一索引、事件溯源、乐观 revision | 参考后实现 | 本项目存储层 | 并发两 turn 只允许一个 lease；唯一索引冲突测试 |
| 知识点学习工具闭环（存答案、洗牌、单题未决） | DeepTutor \`capabilities/mastery/tools.py:93-314,803-1063\` | 答案服务端、选项 A/B/C 契约、卡结束 turn | 适配移植 | 交互/API 层 | 选项重复/缺 body 拒绝；二次 quiz 重呈原卡 |
| Book artifact（Chapter/Page/Block/ConceptGraph + 漂移检测） | DeepTutor \`book/models.py:229-547\`；\`book/storage.py\`；\`api/routers/book.py\` | revision 并发、block 级编辑、KB 指纹→stale | 适配移植 | 成果(artifact)存储与 API | 编辑块后 revision 前进；改 KB 后 stale 标记 |
| 知识库版本化（embedding signature → version-N） | DeepTutor \`services/rag/index_versioning.py:1-64\` | 签名目录隔离、旧布局只读兼容 | 参考后实现 | 索引层 | 换 embedding 后建新 version 且旧库仍可读 |
| KB 清单（检索回答不了的"有什么"） | DeepTutor \`knowledge/manifest.py:113-145\` | Manifest 事实对象 + 有界截断 + 明确非枚举 | 参考后实现 | 知识库服务 | 连接型 KB 报"不可枚举"而非 0 |
| 引用归一与角标 | DeepTutor \`agents/research/utils/citation_manager.py:42-90\` | title/preview/file/page/chunk/score + ref_number | 适配移植 | 检索引用层 | 引用角标与来源可点击对应 |
| Schema 化 DAG+先修理由 | Study-Mate 4f0b2bc \`schemas/curriculum.schema.json:11-55\` | node id/title/objective/prerequisites/status/kind + edge.reason | 直接复用（结构） | knowledge_map schema | JSON Schema 校验 + 环检测 |
| DAG 分层/校验 | Study-Mate \`scripts/curriculum.py:104-174\`；\`curriculum-designer/SKILL.md:68-70\` | 最长路径分层、环安全、引用完整性 | 适配移植 | 地图布局与校验器 | 有环/孤儿/悬空先修用例 |
| 状态词表唯一来源 | Study-Mate \`scripts/statuses.py:1-52\` | 枚举只从 schema 读，映射唯一 | 直接复用 | 常量/样式层 | "schema 每状态都有配色"断言 |
| 分层练习与判分证据 | Study-Mate \`.dsh/skills/layered-practice/SKILL.md:10-67\` | L1-L4、kind 定上限、三档结论必指证据 | 参考后实现 | 出题/评估服务 | 无证据不得判"通过"用例 |
| 进度单写与证据记录 | Study-Mate \`.dsh/skills/record-keeping/SKILL.md:81-113\` | 进度只认独立状态文件；有观察证据才记 | 直接复用 | 进度存储约定 | 大纲快照与运行进度不一致时以进度为准 |
| 误解库（追加式） | Study-Mate \`schemas/progress.schema.json:20-34\`；misconceptions.yaml | topic/question/misunderstanding/answer/follow_up/importance 只追加 | 适配移植 | 误解/错题表 | 追加不改写；与进度 topic 逐字对应 |
| 结构校验诊断（可修复提示） | Archify 73aaa06 \`renderers/shared/validator.mjs:38-85\` | code/subject/evidence/supportedFixes + 可读路径 | 适配移植 | 地图校验器输出契约 | 断言诊断含 code 与 supportedFixes |
| 节点→来源证据（path/line/revision） | Archify \`schemas/common.schema.json:134-163\` | sourceReferences + 40 位 revision 钉死 | 适配移植 | node.source_refs | 引用的 commit/blob 不存在时报错 |
| 地图交互（focus/reach/route/lens + 引导视图） | Archify \`reference/README_ZH.md:105\`；\`common.schema.json:115-133\` | 聚焦/下游/两点路径/关系镜头 + 命名视图 | 参考后实现 | 前端知识地图 | 深链接可复现同一视图 |
| Artifact 自包含+多门禁交付 | Archify \`SKILL.md:15-42,72-88\` | 每请求独立目录、finalize 多门禁、非零退出≠成功 | 参考后实现 | 成果导出/交付流程 | 导出后离线打开可读 |
| 资料—问答—成果分层 | NotebookLM 官方帮助（见 §7 链接表） | Notebook 容器；Sources/Chat/Studio 三功能 | 参考后实现 | 产品信息架构 | 人工核对官方帮助正文后再定稿 |

---

## 附：证据边界与待办
1. NotebookLM 正文未取到（\`web_fetch\` 对 Google 域名全失败）。落地前须人工打开 §7 链接核对 inline citation / source selector / 各 Studio 输出的官方措辞。
2. DeepTutor \`web/\` 前端、\`api/routers/practice.py\` 的练习汇总分析与 \`capabilities/course_study\`、\`capabilities/reading\` 未逐行读完；本笔记只覆盖学习引擎与产物主干。
3. 本文所有行号针对上述三个 SHA；若参考目录更新需重新定位。
