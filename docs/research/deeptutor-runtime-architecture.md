# DeepTutor 运行架构深研（f07029c）

> 参考仓库：`.refs/DeepTutor`（短 SHA **f07029c**，已核实）、`.refs/Archify`（73aaa06）、`.refs/Study-Mate`（4f0b2bc）。
> 本文所有行号均来自真实源码。除特别标注外，路径相对 `.refs/DeepTutor/`。

DeepTutor 是 **agent-native** 的教育类应用：两层插件模型（Level 1 单次 **Tools** 由 LLM 按需调用；Level 2 多阶段 **Capabilities** 接管整轮），三个入口（CLI / WebSocket / Python SDK）统一进入 `TurnApplicationService`，再由共享 `TurnEngine` 路由 `UnifiedContext`。

---

## 1. 用户输入如何进入统一运行入口

调用链（自外向内）：

| 层 | 文件 | 关键行 |
|---|---|---|
| 入口 CLI (Typer) | `deeptutor_cli/main.py` | — |
| 入口 WebSocket | `deeptutor/api/routers/unified_ws.py` | `@router.websocket("/ws")` **L43-44** |
| 入口 Python SDK | `deeptutor/app/facade.py` `DeepTutorApp` | **L60**，`start_turn` **L131** |
| 应用服务 | `deeptutor/app/service.py` `TurnApplicationService` | **L19**，`start_turn` **L35** |
| 轮次运行时管理器 | `deeptutor/services/session/turn_runtime.py` `TurnRuntimeManager` | **L26-34**（组合 6 个专注服务） |
| 执行 + 持久化 | `deeptutor/services/session/turns/executor.py` `TurnExecutor._run_turn` | **L141**，构造 `UnifiedContext` **L890**，`self.turn_engine.execute(context)` **L1001** |
| 引擎 | `deeptutor/runtime/turn_engine.py` `TurnEngine` | **L11**，`execute` **L17** 懒加载 orchestrator |
| 编排器 | `deeptutor/runtime/orchestrator.py` `ChatOrchestrator.handle` | **L51, L61**，选 capability **L96** `context.active_capability or "chat"` |
| 能力执行 | `capability.run(context, bus)` | **L141** |
| 完成事件 | `_publish_completion` → EventBus | **L204-219** |

- 输入模型 `TurnRequest`（Pydantic，`deeptutor/core/turn_request.py:113`），字段含 content/capability/session_id/tools/knowledge_bases/config/attachments/notebook_references/reading_references/mastery_* 等 **L121-196**；`to_payload()` **L231**。
- `ChatOrchestrator.handle` 每轮：建 `StreamBus`(**L128**) → 注册 `register_bus(turn_id,bus)`(**L131**) → 后台 task 跑 capability → 统一在 `finally` 发 `DONE`(**L169-179**) 并 `bus.close()`；消费端停止读取时 `task.cancel()`(**L197-200**)。

---

## 2. 模型如何决定「直接回答 / 澄清 / 检索 / 调工具」

**核心事实：chat 不使用标签协议，改用原生 function calling。**（`deeptutor/runtime/agentic/labels.py:14-21` 明确记录 chat 自 46093e5e 起改为 native tool calling。）

- 决策单元：`AgentLoop._run_loop`（`deeptutor/agents/loop/agent_loop.py:376`）。
  - 有 `tool_calls` 的轮 → 追加 assistant+tool 消息并继续（**L678-701**）；
  - 无 `tool_calls` 的轮 → 该轮文本即最终答案，结束（**L453, L628-640**）。
  - 因此「直接回答/检索/调工具」完全由模型在每轮 tool choice 中决定。
- **澄清**用 `ask_user` 工具：`ToolResult.pause_for_user`（`core/tool_protocol.py:164`）→ 轮询暂停 **L703-715**；用户答复由 `_await_user_reply_and_resolve`（`pipeline.py:1184`）写回 tool 消息，同一轮续跑。
- **检索**主要是 `rag` 工具 + 预取 KB seed：`_retrieve_kb_seed_block`（`pipeline.py:1473`，上限 3 个 KB×4000 字符 **L102-103, L1486-1487**）。

**预算 / 轮数 / 超时（真实常量）**

| 常量 | 值 | 位置 |
|---|---|---|
| `DEFAULT_MAX_ROUNDS` | 8 | `agents/loop/pipeline.py:106` |
| exploring / responding max_tokens | 1600 / 8000 | `pipeline.py:286-291` |
| `MAX_SETTLEMENT_ROUNDS` | 3 | `agent_loop.py:90` |
| `MAX_REASONING_ONLY_RECOVERIES` | 2 | `agent_loop.py:95` |
| `MAX_PARALLEL_TOOL_CALLS` | 15/轮 | `runtime/agentic/tool_dispatch.py:48` |
| 上下文窗口守卫比 | 0.9 | `pipeline.py:107`；`_guard_context_window` **L1615** |
| provider 重试延迟 | (0.5, 1.5)s | `agent_loop.py:100, L1382` |
| 流式空闲超时 | 90s | `services/llm/provider_core/openai_compat_provider.py:1110` |
| `min_loop_rounds` floor | 由 capability 设 | `pipeline.py:363-376` |

**提示词位置**
- 英文：`deeptutor/agents/chat/prompts/en/agentic_chat.yaml`（`loop.system` **L74-116**：默认行动、必要时 `ask_user`、无工具的答复结束循环、verbatim 工具名）；中文 `.../zh/agentic_chat.yaml`。
- 系统提示装配：`deeptutor/agents/loop/prompt_blocks.py` `LoopPromptAssembler`；`pipeline._build_system_prompt` **L436-464**。
- deep 模式仍用**标签驱动循环**：`deeptutor/runtime/agentic/loop.py:209 run_agentic_loop` + `labels.py:45 classify_label`，由 `LabelProtocol`（`loop.py:42`）声明 allowed/terminal/intermediate/final/tool_label。
- 能力清单/校验：`runtime/request_contracts.py` `CAPABILITY_CONFIG_MODELS` **L154-167**、`CAPABILITY_REQUEST_SCHEMAS` **L191**。

---

## 3. Tools 与 Capabilities 的注册 / 组合 / 限制 / 执行

### Tool 层（Level 1）
- 契约：`deeptutor/core/tool_protocol.py` — `ToolParameter`(**L17**，支持 `sensitive`)、`ToolDefinition`(**L56**，`raw_parameters` 兼容任意 JSON Schema)、`ToolResult`(**L130**，`pause_for_user/terminate_turn/model_message`)、`ToolLookup` Protocol(**L184**)、`BaseTool`(**L220**，`deferred` 渐进披露)。
- 注册表：`deeptutor/runtime/registry/tool_registry.py` `ToolRegistry`(**L26**)；`load_builtins`(**L55**) 只装载 import-cheap 的 specs；`_load_builtin`(**L59**) 首用时实例化；`register/unregister`(**L42/47**)、`get_enabled`(**L104**)、`build_openai_schemas`(**L147**)、`execute`(**L151**，别名解析)；单例 `get_tool_registry`(**L170**)。
- 目录：`deeptutor/tools/builtin_specs.py` `BuiltinToolSpec(name,class_path)`(**L20**)、`BUILTIN_TOOL_SPECS`(**L46-205**)、`BUILTIN_TOOL_NAMES`(**L207**)、`TOOL_ALIASES`(**L217**)。
- 用户开关白名单：`deeptutor/tools/builtin/__init__.py:1907 USER_TOGGLEABLE_TOOL_NAMES`（brainstorm/web_search/paper_search/zotero_search/reason/geogebra_analysis/imagegen/videogen）；`:1932 CONFIGURABLE_BUILTIN_TOOL_NAMES`（上下文门控的自动挂载集）。
- **组合策略（纯函数）**：`deeptutor/agents/_shared/tool_composition.py` — `AUTO_MOUNTED_TOOLS`(**L40**)、`_CONDITIONAL_MOUNT_FLAGS`(**L47-64** rag→has_kb、read_source→has_sources…)、`WORKSPACE_BASELINE_TOOLS`(**L73**)、`ToolMountFlags`(**L143**)、`compose_enabled_tools`(**L170-265**)：用户开关 → 条件自动挂载 → 能力自带 owned tools → always-on(`write_memory/web_fetch/github/ask_user/cron`)；`exclusive=True` 时知识能力替换整个界面（**L230-248**）。
- **权限/隔离**：`runtime/registry/scoped_registry.py` `ScopedToolRegistry`(**L40**)，只读视图 + 每用户 overlay；`execute` 处拒绝未授权 provider 工具（**L129-150**）；`runtime/providers/allowlist.py` `Allowlist`(**L24**，`names=None` 表示不受限，`narrow/widen` 全定义）。
- **渐进披露**：`runtime/registry/deferred_tools.py` `render_deferred_tools_manifest`(**L57**)、`DeferredToolLoader`(**L116**) 把手柄注入 `load_tools`，动态 append schema 到 live `tool_schemas` 列表（循环每轮重读）。
- **沙箱**：`deeptutor/services/sandbox/`（`service.py`、`spec.py`、`quota.py`、`runner/server.py`、`backends.py`）；参数注入 `deeptutor/agents/_shared/tool_runtime.py:44 bind_workspace_tool_runtime`（工作目录 + Mount，只读 workspace root，**L84-99**）；`deeptutor/tools/exec_tool.py:195` 命令黑名单拦截。
- **执行器**：`deeptutor/runtime/agentic/tool_dispatch.py` `dispatch_tool_calls`(**L122**)、`execute_tool_call`(**L647**)、`_execute_with_policy`(**L721**)；并行执行、每调用独立子 trace、重复调用检测(**L357**)、参数守卫(**L424**)、`PAUSE_LAST_TOOLS`(**L55**)。

### Capability 层（Level 2）
- 契约：`deeptutor/core/capability_protocol.py` `CapabilityManifest`(**L30**：name/description/stages/tools_used/cli_aliases/request_schema/config_defaults)、`TurnCapability`(**L42**，`run(context, stream)`)。
- 工厂目录：`runtime/capability_catalog.py` `CapabilityCatalog`(**L33**，键 `(kind,name)`，kind=`turn|loop_extension`，每项带 pydantic `config_model`)；`get_capability_catalog`(**L85**)。
- 注册/插件：`runtime/registry/capability_registry.py` `CapabilityRegistry`(**L57**)，`load_builtins`(**L84**)、`load_plugins`(**L102**，entry-points `deeptutor.extensions` + 旧 `deeptutor.plugins`)、`get_manifests`(**L147**) 输出含 request_schema。
- 内建清单：`runtime/bootstrap/builtin_capabilities.py` `BUILTIN_CAPABILITY_SPECS`（class_path+manifest）。
- 实际能力：`deeptutor/agents/{chat,solve,question,research,visualize,math_animator}` + `deeptutor/capabilities/{mastery,reading,watching,course_study,obsidian,marginnote4,ima,subagent,…}`。
- Loop 扩展点：`deeptutor/capabilities/protocol.py` `LoopExtension`(**L38**：owned_tools/is_active/system_block/augment_kwargs/pre_loop_seed，可选 `pre_loop/on_user_pause/on_user_resume/finish_instruction/rebinding_tools`)；`KnowledgeCapability`(**L130**，`exclusive_tools=True`)。
- 结果统一出口：`deeptutor/agents/_shared/capability_result.py:21 emit_capability_result`（合并 `cost_summary` + `usage_summary`）。

---

## 4. 多模式如何共用会话 / 资料 / 学习目标 / 上下文

- **统一上下文结构体** `deeptutor/core/context.py`：
  - `UnifiedContext`(**L86**)，字段 **L129-148**：session_id、user_message、conversation_history、enabled_tools、allowed_builtin_tools、active_capability、knowledge_bases、attachments、config_overrides、language、memory_context、persona_context、sidebar_context、skills_manifest、source_manifest、runtime、interaction、capability_output、extension_state、metadata。
  - `Attachment`(**L17**) type/url/base64/filename/mime_type/id/extracted_text。
  - `WorkspaceRuntimeContext`(**L35**) workspace_id/root/output_dir/security_level（**物理路径禁止进模型消息**）。
  - `TurnRuntimeContext`(**L50**) turn_id、wait_for_user_reply 回调、provider_response_state、model_history、model_turn、subagent_consult_budget、min_loop_rounds、workspace。
  - `InteractionState`(**L69**)、`CapabilityOutput`(**L77**)；`extension(namespace)`(**L150**) 隔离可变插件状态。
- 多模式共享资料：`TurnRequest` 统一携带 knowledge_bases/notebook/book/reading/memory/persona/partner_group references（`core/turn_request.py:131-196`）；`executor.py:923-997` 把所有这些折叠进 `context.metadata`（`source_index` **L990**、`mastery_topic_source_index` **L996**、`book_context`、`question_bank_context` 等）。
- 会话偏好持久化：`TurnApplicationService.start_turn` 把 language/各种 references/KB 写入 `store.update_session_preferences`，并维护历史 `workspace_dependencies`（`app/service.py:60-96`）。
- 能力切换通过 `active_capability` + `metadata`，而非独立上下文对象；`LoopExtension` 让 solve/mastery 等复用 chat 的完整工具面并叠加自身 owned tools（`tool_composition.py:196-199`）。

**持久化格式**：会话偏好/元数据均为 JSON 文本列（`preferences_json`、`metadata_json`、`events_json`、`attachments_json`）。

---

## 5. 会话 / 轮次 / 工具结果 / 学习进度的持久化与恢复

- **存储层**：`deeptutor/services/session/sqlite_store.py`（SQLite，WAL：**L281**），schema **L284-459**：
  - `sessions`(**L284**)：id/title/created_at/updated_at/compressed_summary/summary_up_to_msg_id/preferences_json；
  - `messages`(**L294**)：role/content/capability/events_json/attachments_json/metadata_json/parent_message_id（编辑分支，**L304-308**）；
  - `turns`(**L321**)：status/owner_id/**fencing_token**/state_version/failure_code/retryable/assistant_message_id；
  - `turn_events`(**L344**)：turn_id+seq+type/source/stage/content/metadata_json，**UNIQUE(turn_id,seq)**（**L355**）；
  - 学习进度：`notebook_entries`(**L361**)、`assessment_attempts`(**L407**)、`reading_quiz_pending/rewards`(**L431/440**)、`notebook_categories`(**L448**)。
- 存储协议：`deeptutor/services/session/protocol.py` `SessionStoreProtocol`(**L143**)、`SessionRepository`/`TurnRepository`/`MessageRepository`；`ActiveTurnConflict`(**L13**)。
- 轮次生命周期：`deeptutor/services/session/turns/lifecycle.py` — `cancel_turn`(**L305**)、`submit_user_reply`(**L325**)、`subscribe_turn`(**L354**)、事件持久化+seq 分配 `_publish_live_event`(**L655-720**，`append_events` 批量 **L709**）。
- **恢复入口**：`deeptutor/app/service.py:145 subscribe_turn` 先 `store.get_events(turn_id, after_seq)` 做**持久化重放**，再 `coordinator.read_events(turn_id, last_seq)` 补**实时尾部**；DONE 后额外等待 post-turn 事件（title），以「lease 消失」为流结束信号（**L112-135, L168-207**）。
- 僵尸轮清理：`_reclaim_unowned_active_turn`(**L349**)、`_reap_unowned_live_turn`(**L391**，CAS + `worker_lost` + error/done 事件)。
- 协调层：`deeptutor/runtime/coordination/protocol.py:11 RuntimeCoordinator`（lease / publish_event / read_events / submit_command / leader）；`recovery.py:15 TurnRecoveryService.recover_once`(**L25**) 由 leader 扫描过期 lease 并把轮标记 failed/worker_lost/retryable（**L45-101**）；`app/container.py:139 recover_once` 遍历用户 scope 恢复。
- 学习进度：`deeptutor/learning/`（44 文件）掌握度 store；`deeptutor/services/practice/storage.py`；掌握度单向从严口径见根 AGENTS.md §8。

---

## 6. 流式事件协议、取消、失败、重试、断线恢复

### 事件类型枚举（内部）
`deeptutor/core/stream.py:17 StreamEventType`：
`stage_start, stage_end, thinking, observation, content, tool_call, tool_result, progress, sources, result, error, session, session_meta, done, wait_for_input`。
`StreamEvent`(**L37**) 字段：type/source/stage/content/metadata/session_id/turn_id/seq/timestamp；`to_dict`(**L61**)。

### StreamBus（每轮一个）
`deeptutor/runtime/stream_bus.py:31 StreamBus`：`emit`(**L62**，支持 seq 分配、history 保留、跨 loop `call_soon_threadsafe`)、`subscribe(after_seq)`(**)L79**)、`mark_closed/close`(**L105/122**)、`stage`(**L128**)、`content/thinking/observation/tool_call/tool_result/progress/sources/result/error/wait_for_input`(**L156-342**)；`wait_for_input`(**L312**) 提供暂停等待，`submit_input`(**L344**) 注入；`register_bus/get_bus`(**L363-375**) 供 WS `user_input` 路由。

### WS 线协议（对浏览器）
`deeptutor/api/contracts/turn_protocol.py`：`PROTOCOL_VERSION="2.0"`(**L12**)；客户端命令 **L69-174**（message/start_turn、subscribe_turn、subscribe_session、**resume_from**、unsubscribe、cancel_turn、regenerate、submit_user_reply、user_input、check_active_turn、ping）；服务端事件 **L177-224**（StreamEvent、ActiveTurnInfo、PongEvent、CommandAckEvent、ProtocolErrorEvent）；`TurnFailureCode`(**L37**：worker_lost/lease_lost/coordination_unavailable/provider_error/internal_error/rejected/server_shutdown)。
端点 `deeptutor/api/routers/unified_ws.py:43`；命令分发 **L220-371**。

- **取消 API**：`TurnApplicationService.cancel_turn`(`app/service.py:263`)、`cancel_turn_and_wait`(**L286**)；底层 `coordinator.submit_command(turn_id,"cancel",{})`(**L279**)；WS 回 `command_ack`（`unified_ws.py:293-302`）。
- **失败**：orchestrator 捕获异常发 `error` 事件，metadata `turn_terminal/status/error_code/retryable/partial_response`（`orchestrator.py:142-167`），最终 `DONE` 带 `status/usage_summary`。
- **重试**：provider 流式失败在未产出可见输出时重试 `_PROVIDER_RETRY_DELAYS=(0.5,1.5)`（`agent_loop.py:100, L1382-1402`）；provider 拒绝 native tools → 去工具重试（**L1696-1731**）；reasoning-only 空答复最多 nudge 2 次（**L569**）；用户侧重试靠 `regenerate` 命令。
- **断线恢复**：客户端 `web/features/chat/transport/TurnRuntimeClient.ts` `setResumeCursor`(**L171**)、事件 gap 检测（**L374**）；`reconnect-policy.ts` 指数退避 250ms→8s+抖动、空闲上限 5 次；重连用 `resume_from`+seq 让服务端从持久化事件重放。

---

## 7. 后端对外接口

- FastAPI 应用：`deeptutor/api/main.py`（routers 注册 **L575-788**）。主要前缀：
  - REST：`/api/auth`、`/api/sessions`、`/api/capabilities`(`capabilities`+`capabilities_settings`)、`/api/settings`(+`/workspace`,`/mcp`,`/video-learning`)、`/api/knowledge*`(prefix `/api`)、`/api/mastery-paths`、`/api/reading`、`/api/memory`、`/api/notebook`、`/api/book`、`/api/question`、`/api/tools`、`/api/skills`、`/api/subagents`、`/api/partners`、`/api/courses`、`/api/dashboard`、`/api/system`、`/api/voice`、`/api/multi-user`。
  - 文件：`/files/outputs`(**L576**)、`/files/workspace-items`、`/files/library`、`/files/attachments`。
  - WS：`/ws`（unified **L784**）、`/ws/questions`、`/ws/partners`、`/ws/partner-groups`、`/ws/quiz-judge`。
  - 健康：`/`(**L791**)、`/health/live`(**L796**)、`/health/ready`(**L801**)。
- Python SDK 入口：`deeptutor/app/facade.py:60 DeepTutorApp`：`start_turn`(**L131**)、`stream_turn`(**L158**)、`cancel_turn`(**L171**)、`submit_user_reply`(**L183**)、`regenerate_last_turn`(**L202**)、`list/get/rename/delete_session`、`resolve_capability`(**L82**)、`get_capability_contracts`(**L94**)。
- 依赖容器：`deeptutor/app/container.py:78 ApplicationContainer`（capability catalog + registry + `TurnEngine`(#L93) + `TurnApplicationService`(#L96)）；`RedisCoordinator|MemoryCoordinator`(#L110-118)。

---

## 8. 前端主工作区如何组织

Next.js App Router（`web/`）。

- 路由：`web/app/(workspace)/chat/[sessionId]/page.tsx` → `web/features/chat/components/ChatWorkspace.tsx`（主工作区，3076 行）。
- **活动面板/trace**：`web/components/activity/`（ActivityRow/ActivityStack/ActivityFold/ActivityDetailGrid/ActivityOrb/StatusDot/types）；`web/components/chat/home/SessionActivityPanel.tsx`、`SessionViewerPanel.tsx`、`LazySessionViewerPanel.tsx`。
- **资料预览**：`web/components/chat/preview/FilePreviewDrawer.tsx` + `previewers/`（Pdf/Docx/Xlsx/Markdown/Image/Video/Svg/Office…）+ `previewerFor.ts`。
- **历史**：`web/components/SessionList.tsx`、`web/components/sidebar/`(SidebarShell/WorkspaceSidebar/SidebarNav)、`web/components/chat/HistorySessionPicker.tsx`、`TurnNavigator.tsx`。
- **输入/资源选择**：`web/components/chat/home/` ChatComposer、ComposerInput、ComposerRail、ComposerResources、ContextReferenceTree、SelectedResources、KnowledgeSelector、PersonaSelector、ModelSelector、MasteryQuestionCard、AskUserOptions。
- **传输/状态**：`web/features/chat/transport/`（UnifiedTurnClient、TurnRuntimeClient、socket、reconnect-policy、command-delivery）；`ChatRuntimeProvider.tsx`、`ChatStateAdapter.tsx`；`web/features/chat/model/protocol.ts`、`web/contracts/generated/turn-protocol.ts`。
- 学习区路由：`web/app/(workspace)/learning/{books,mastery,practice,reading,watching}`。

---

## 9. 可移植到 Python(FastAPI) 的设计要点 + 最小骨架

### 9.1 三层职责分离（直接复用）
1. **应用服务 `TurnApplicationService`**：唯一可变入口，负责持久化轮 + 起后台 task + 订阅/取消/答复。**所有入口（REST/WS/SDK/CLI）只调用它。**
2. **引擎 `TurnEngine → Orchestrator`**：把 `UnifiedContext` 路由到 capability，管理 per-turn `StreamBus` 生命周期与终止事件。
3. **能力 `TurnCapability.run(context, stream)`**：只依赖 `context` + `stream`，不感知会话存储与传输。

### 9.2 注册表设计
- **Tool**：`BuiltinToolSpec(name, class_path)` 惰性目录（避免 import 环）；`ToolRegistry.load_builtins()` 只记 spec，首用实例化；`BaseTool` 提供 `get_definition() -> ToolDefinition`、`async execute(**kwargs) -> ToolResult`、`deferred: bool`。
- **Capability**：`CapabilityCatalog` 工厂式注册（键 `(kind,name)`，带 pydantic `config_model`），manifest 供 UI 与 schema。
- **组合**：`compose_enabled_tools(registry, requested, whitelist, ToolMountFlags, owned, exclusive, ...) -> list[str]` 做成纯函数，便于测试与复用。
- **权限**：用 `Allowlist(names: frozenset|None)` 显式表达「不受限」；在 `execute` 处（而非只在 manifest）做拒绝。
- **渐进披露**：tool 带 `deferred`，先给一行 manifest，模型调 `load_tools` 后把 schema append 到 live 列表。

### 9.3 流式事件协议
- `StreamEventType(str, Enum)` + `@dataclass StreamEvent(type, source, stage, content, metadata, session_id, turn_id, seq, timestamp)`；`to_dict()` 用于 SSE data / WS JSON。
- `StreamBus`：每轮一个，`emit` 分配 seq 并写 history；`subscribe(after_seq)` 先重放 history 再挂队列；`close()` 发 sentinel。
- SSE 端点语义：`data: {event.to_dict()}\n\n`；断线用 `?after_seq=N` 续传。

### 9.4 最小代码骨架（FastAPI）

```python
# app/contracts.py
from pydantic import BaseModel, Field
class TurnRequest(BaseModel):
    content: str
    capability: str = "chat"
    session_id: str | None = None
    tools: list[str] | None = None
    knowledge_bases: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)

# core/context.py
from dataclasses import dataclass, field
@dataclass
class UnifiedContext:
    session_id: str = ""
    user_message: str = ""
    active_capability: str | None = None
    conversation_history: list[dict] = field(default_factory=list)
    enabled_tools: list[str] | None = None
    knowledge_bases: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    runtime: dict = field(default_factory=dict)

# core/stream.py
from dataclasses import dataclass, field
from enum import Enum
class StreamEventType(str, Enum):
    STAGE_START="stage_start"; STAGE_END="stage_end"; CONTENT="content"
    TOOL_CALL="tool_call"; TOOL_RESULT="tool_result"; SOURCES="sources"
    RESULT="result"; ERROR="error"; DONE="done"; WAIT_FOR_INPUT="wait_for_input"
@dataclass
class StreamEvent:
    type: StreamEventType; source: str=""; stage: str=""; content: str=""
    metadata: dict = field(default_factory=dict)
    session_id: str=""; turn_id: str=""; seq: int=0

# runtime/stream_bus.py
import asyncio
class StreamBus:
    def __init__(self): self._q=[]; self._closed=False; self._seq=0; self._history=[]
    async def emit(self, e: StreamEvent):
        if self._closed: return
        self._seq += 1; e.seq = self._seq; self._history.append(e)
        for q in self._q: q.put_nowait(e)
    async def subscribe(self, after_seq: int = 0):
        q: asyncio.Queue = asyncio.Queue(); self._q.append(q)
        try:
            for e in self._history:
                if e.seq > after_seq: yield e
            while not self._closed:
                yield await q.get()
        finally: self._q.remove(q)
    async def close(self): self._closed=True

# runtime/registry/tool_registry.py
class ToolRegistry:
    def __init__(self): self._tools={}; self._specs={}
    def register(self,t): self._tools[t.name]=t
    def load_builtins(self, specs): self._specs.update(specs)     # name -> class_path
    def get(self,name):
        if name not in self._tools and name in self._specs:
            import importlib
            mod,cls=self._specs[name].rsplit(":",1)
            self._tools[name]=getattr(importlib.import_module(mod),cls)()
        return self._tools.get(name)
    def build_openai_schemas(self,names=None):
        tools=[self.get(n) for n in (names or self._specs)]
        return [t.get_definition().to_openai_schema() for t in tools if t]

# runtime/orchestrator.py
class ChatOrchestrator:
    def __init__(self, capability_registry): self.caps=capability_registry
    async def handle(self, ctx: UnifiedContext):
        cap = self.caps.get(ctx.active_capability or "chat")
        if cap is None: raise ValueError(f"unknown capability {ctx.active_capability}")
        bus = StreamBus()
        async def run():
            try: await cap.run(ctx, bus)
            except Exception as exc:
                await bus.emit(StreamEvent(StreamEventType.ERROR, content=str(exc)))
            finally:
                await bus.emit(StreamEvent(StreamEventType.DONE)); await bus.close()
        task = asyncio.create_task(run())
        async for e in bus.subscribe(): yield e
        await task

# app/service.py
class TurnApplicationService:
    def __init__(self, store, engine): self.store=store; self.engine=engine
    async def start_turn(self, req: TurnRequest):
        turn = await self.store.begin_turn(req.session_id, req.capability)
        ctx = UnifiedContext(session_id=turn.session_id, user_message=req.content,
                             active_capability=req.capability, enabled_tools=req.tools,
                             knowledge_bases=req.knowledge_bases)
        asyncio.create_task(self._consume(turn.id, ctx))
        return turn
    async def _consume(self, turn_id, ctx):
        async for e in self.engine.execute(ctx):
            await self.store.append_event(turn_id, e.to_dict())
    async def subscribe_turn(self, turn_id, after_seq=0):
        for e in await self.store.get_events(turn_id, after_seq): yield e
        async for e in self.engine.subscribe_live(turn_id, after_seq): yield e

# api/routes.py
@router.post("/api/sessions/{session_id}/turns")
async def start(session_id: str, req: TurnRequest):
    return await svc.start_turn(req.model_copy(update={"session_id": session_id}))

@router.get("/api/turns/{turn_id}/events")
async def events(turn_id: str, after_seq: int = 0):
    async def gen():
        async for e in svc.subscribe_turn(turn_id, after_seq):
            yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
```

### 9.5 落地顺序建议
1. 先定 `StreamEventType` + `StreamEvent` + `StreamBus`（协议一旦定，前后端/SSE 都稳定）。
2. 再定 `UnifiedContext` + `TurnRequest`。
3. 再做 `ToolRegistry` + `BuiltinToolSpec` + `compose_enabled_tools` 纯函数。
4. 再做 `CapabilityCatalog` + `TurnCapability`。
5. 最后 `TurnApplicationService` 持久化（SQLite：turns/turn_events/messages）+ `subscribe_turn(after_seq)` 重放。
6. 断线恢复与取消：事件表按 `(turn_id, seq)` UNIQUE；取消用命令表或进程内 cancel + 终态 CAS。

---

## 10. 采用表

| 参考能力 | 版本与源码位置 | 实际设计 | 本项目采用方式 | 落地位置 | 验证方式 |
|---|---|---|---|---|---|
| 统一运行入口（入口→应用服务→引擎→能力） | DeepTutor f07029c；`app/service.py:19,35`、`runtime/turn_engine.py:11,17`、`runtime/orchestrator.py:51,61,96,141` | 所有入口收敛到 TurnApplicationService；引擎按 active_capability 路由 | 适配移植 | `backend/app/runtime/`（orchestrator.py, engine.py）+ `app/service.py` | 单测：同一 TurnRequest 经 REST/WS/SDK 得到相同事件序列 |
| 统一上下文结构体 | `core/context.py:86,129-148`（UnifiedContext/TurnRuntimeContext/InteractionState/CapabilityOutput） | 单个 dataclass 贯穿 orchestrator→tool/capability | 适配移植（裁剪字段） | `backend/app/core/context.py` | 类型检查 + 一轮端到端 trace 字段断言 |
| 原生 tool-calling 决策循环 + 轮数/预算 | `agents/loop/agent_loop.py:376,453`、`pipeline.py:106,285-300`、`tool_dispatch.py:48` | 无工具轮=终轮；max_rounds=8、settlement=3、并行工具=15 | 参考后实现 | `backend/app/agent/loop.py` | 假 LLM（有/无 tool_calls 两分支）+ 轮数上限用例 |
| 澄清（ask_user 暂停/恢复） | `core/tool_protocol.py:130,164`、`pipeline.py:1184`、`executor.py:183` | ToolResult.pause_for_user + reply 队列，同轮续跑 | 适配移植 | `backend/app/tools/ask_user.py`、executor 暂停逻辑 | 集成测试：暂停→submit→同一轮续跑 |
| Tool 注册表（惰性 class-path 目录 + 别名） | `runtime/registry/tool_registry.py:26,55,59,151`、`tools/builtin_specs.py:20,46,207,217` | load_builtins 只存 spec，首用实例化；execute 解析别名 | 直接复用 | `backend/app/registry/tool_registry.py`、`tool_specs.py` | 单测：未导入类可注册；别名映射；unknown KeyError |
| 工具组合纯函数 + 上下文门控 | `agents/_shared/tool_composition.py:40,47,73,143,170-265` | 用户开关→条件挂载→能力 owned→always-on；exclusive 替换 | 直接复用 | `backend/app/agent/tool_composition.py` | 表驱动单测覆盖每个 flag 与 exclusive |
| Capability 工厂目录 + manifest + pydantic config | `core/capability_protocol.py:30,42`、`runtime/capability_catalog.py:33`、`request_contracts.py:154,191` | (kind,name) 工厂注册，manifest 提供 stages/request_schema | 适配移植 | `backend/app/registry/capability_catalog.py`、`capabilities/` | 单测：注册/重复注册/unknown；manifest JSON schema |
| 工具权限与作用域视图 | `runtime/registry/scoped_registry.py:40,129-150`、`runtime/providers/allowlist.py:24,44` | 每轮 overlay 视图，在 execute 处拒绝未授权 | 适配移植 | `backend/app/registry/scoped_registry.py` | 单测：未授权 provider 工具返回失败而非执行 |
| 工具渐进披露（deferred + load_tools） | `runtime/registry/deferred_tools.py:57,116,171` | 先给 manifest，load 后 append schema | 参考后实现 | `backend/app/registry/deferred_tools.py` | 单测：初始 schema 不含 deferred；load 后含 |
| 沙箱执行 | `services/sandbox/{service,spec,quota,backends,runner/server}.py`、`agents/_shared/tool_runtime.py:44,84-99` | Mount 白名单 + 只读 workspace root + 资源配额 | 参考后实现（按需） | `backend/app/sandbox/` | 集成：越界写被拒；超时/配额终止 |
| 流式事件协议 | `core/stream.py:17,37,61`、`runtime/stream_bus.py:31,62,79,105` | 15 种事件 + seq + history 重放 | 直接复用（改名/裁剪） | `backend/app/core/stream.py`、`runtime/stream_bus.py` | 单测：seq 单调；after_seq 重放无重复/无缺 |
| 轮/事件持久化 + 断线重放 | `services/session/sqlite_store.py:284-459`（turns/turn_events UNIQUE(turn_id,seq)）、`app/service.py:145` | 先重放持久化事件，再补实时尾部 | 适配移植 | `backend/app/store/sqlite_store.py`、`app/service.py:subscribe_turn` | 集成：断开重连 after_seq 补齐事件；无重复 |
| 取消 / 失败 / 重试 | `app/service.py:263,286,279`、`orchestrator.py:142-167`、`agent_loop.py:100,1382` | 取消命令 + error(metadata retryable) + provider 退避重试 | 适配移植 | `backend/app/runtime/cancel.py`、loop 重试段 | 单测：取消后无新事件且终态 cancelled；错误带 retryable |
| 多租户 lease/恢复协调 | `runtime/coordination/{protocol,recovery}.py`、`app/container.py:139` | lease+fencing_token，leader 扫描过期轮标 worker_lost | 参考后实现（先单机内存版） | `backend/app/runtime/coordination/` | 单测：过期 lease 被恢复为 failed/retryable |
| WS/SSE 对外协议 + SDK 门面 | `api/contracts/turn_protocol.py:12,69-224`、`api/routers/unified_ws.py:43`、`app/facade.py:60,131,158` | v2 命令/事件判别式联合 + after_seq 续传 | 参考后实现 | `backend/app/api/contracts.py`、`routers/ws.py`、`sdk/client.py` | 契约快照测试 + SDK 端到端 |
| 前端主工作区（活动/预览/历史） | `web/features/chat/components/ChatWorkspace.tsx`、`web/components/activity/`、`web/components/chat/preview/`、`web/features/chat/transport/` | 事件驱动 trace 折叠 + 预览抽屉 + 会话列表 | 参考后实现 | `frontend/src/features/chat/` | 手工刷新验证 + E2E 重连 |