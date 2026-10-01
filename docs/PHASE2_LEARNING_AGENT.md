# 阶段二：能按真实学习记录调整教学的学习 Agent（交付说明）

> 更新日期：2026-09-26
> 本文只记录**经真实验证**的内容，不含任何真实 API Key。
> 阶段一交付见 [`docs/PHASE1_LEARNING_LOOP.md`](./PHASE1_LEARNING_LOOP.md)；
> 正式库历史残留清单见 [`docs/TEST_DATA_INVENTORY.md`](./TEST_DATA_INVENTORY.md)。

---

## 一、本轮目标与完成情况

| 目标 | 状态 | 落点 |
|---|---|---|
| 受约束的 Agent 编排（工具/状态机/限额/取消/运行记录） | 已完成 | `services/agent_runtime.py`、`api/routes/agent_run.py` |
| 画像真正影响教学（流式 + 非流式统一） | 已完成 | `services/teaching_context.py`、`chat_service.py` |
| 可管理的学习记忆（偏好/推断/课程三分） | 已完成 | `services/learning_memory.py`、`api/routes/memory.py` |
| 计划调整做成真实功能（预算/截止/冲突/版本/撤销） | 已完成 | `services/plan_adjustment.py`、`api/routes/plan_agent.py` |
| 界面接入与降级（进度/停止/模型不可用） | 已完成 | `frontend/src/components/ChatBox/*`、`StudentPage` 新增两个页签 |
| 阶段一 6 项补验 | 已完成 | 见第三节 |

---

## 二、Agent 能完成的具体操作（真实模型验证）

真实模型（**DeepSeek**，现为默认模型；MiMo 会员到期已降级为可选提供方）驱动的一次完整流程，浏览器脚本
`tests/e2e_agent_flow.js` → **19/19 通过**：

```
输入目标：我3天后要考这一章，每天只有30分钟，帮我复习
  → 查看学习记录      （知识点 0 个 / 已巩固 0 / 薄弱 0 / 待评估 0 / 错题 0 / 待复习 0）
  → 检查时间预算      （每天 30 分钟，截止 2026-09-28，剩余 0 分钟 / 可用 120 分钟，预算充足）
  → 生成讲解资料      （《主题复习笔记》，证据 8 条）
  → 生成学习计划      （《离散数学考前3天30分钟冲刺复习计划》，7 个任务，预算充足）
  → 解释安排依据      （每天 30 分钟，截止 2026-09-28）
  → 真实自测题生成 → 用户作答 → 后端权威判分（回答正确）
  → 「我的学习 / 计划与时间」显示约束与预算结论、「学习记忆」显示三类依据
```

可调用的工具（全部复用既有业务服务，只使用后端当前用户身份）：

| 工具 | 作用 | 关键约束 |
|---|---|---|
| `search_material` | 检索课程资料 | 只读当前学科知识库 |
| `get_learning_state` | 读学习状态/错题/待办 | 归属由令牌判定 |
| `generate_note` | 生成讲解资料/笔记 | 走既有 note 服务 |
| `generate_diagnostic` | 出诊断性自测题 | 题目入库、答案不下发 |
| `analyze_budget` | 时间预算分析 | 纯程序计算 |
| `create_plan` | 生成学习计划 | 日期/预算校验在程序侧 |
| `schedule_review` | 安排复习 | 幂等，不重复建任务 |
| `propose_adjustment` | 提出计划调整 | 幂等键去重、重大调整需确认 |
| `explain_plan` | 解释安排依据 | 只叙述工具结果 |

编排约束（`services/agent_runtime.py::LIMITS`）：`max_steps=12`、`max_seconds=180`、
`max_retries=1`、`max_llm_calls=6`；状态机 `PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED/TIMEOUT`；
每次运行记录 `agent_runs` + `agent_run_steps`（工具名、状态、结果摘要、耗时），
**不记录模型内部思维过程**。运行可取消（`POST /agent/runs/{id}/cancel`，或前端「停止」按钮 → 中止 SSE）。

---

## 三、阶段一补验：发现并修复的问题

| # | 问题 | 证据 | 修复 |
|---|---|---|---|
| 1 | **答案隔离有真实绕过**：错题本接口直接带标准答案，学生可"先看答案再作答" | 旧 `GET /quiz/wrong` 响应含 `correct_option_id` | `to_public()` 统一下发通道；错题本默认隐藏答案，`GET /quiz/wrong/{id}` 才 reveal，且 reveal 状态存后端（`learning_events` 幂等键 `reveal:{user}:{question}`），换客户端/伪造请求都逃不掉（`after_reveal`） |
| 2 | **复习答对被当成长期掌握** | 掌握度把独立作答与复习作答同权 | 加权估计（独立 1.0 / 复习 0.5）+ 独立的"掌握巩固"状态（需 ≥2 次正确且间隔 ≥1 天，或 ≥3 次快速正确）；复习任务完成不直接认定长期掌握，按规则安排延后复测（`recheck_interval_days=3`） |
| 3 | **对象级权限缺口**：会话/笔记/计划/作答等按 ID 可跨账号读写 | 子代理审计 + 交叉账号测试 | 所有相关端点改为 `require_current_user` + 归属校验，跨账号一律 404/403；课程公共资料与个人资料区分；知识库构建限受控根目录；新增 `KNOWLEDGE_ADMIN_USERS` 白名单 |
| 4 | **6 个既有测试失败**（`subject_not_found`） | 依赖本机业务数据（学科 + 向量库） | 新增 `tests/fixtures_isolated.py`：在临时目录用**真实生产代码路径**创建学科与"已就绪"知识库标记，断言未放宽（不是删测试、不是跳过） |
| 5 | **测试数据散落到正式库** | 修复前跑出的 113 行孤儿数据 + 15 个测试账号 | 全部测试改走隔离夹具；真实模型验收用 `EDU_DATA_DIR`；新增 `/health.data_scope` 门禁，脚本未隔离即退出（实测退出码 2）；清单见 `docs/TEST_DATA_INVENTORY.md` |
| 6 | **事件重试可能重复写入** | 掌握度成功、复习任务失败 → 事件 FAILED → 重试重复计数 | 新增分步幂等账本 `services/idempotency.py`（`step_ledger`，与业务写入**同一事务**提交）：步骤成功即跳过，失败回滚后重试；作答处理器拆为 `review` / `memory` 两个独立步骤 |

另外修复（本轮发现）：**前端 fetch/SSE/语音调用未带身份令牌** → 后端退回遗留账号，导致
"会话不存在，请刷新后重试"与学习记录归属错误。新增 `authHeaders()`（`api/axios.ts`）并在
`api/chat.ts`、`api/audio.ts`、`utils/ttsPlayer.ts`、`api/agentRun.ts` 统一使用。

---

## 四、个性化与记忆如何影响实际输出

### 4.1 教学策略由真实证据决定（可解释、可核查）

`services/teaching_context.py::build_teaching_context()` 选取**与当前问题相关**的少量信息，
在 900 字符预算内组装画像块，产出 `teaching_mode`：

| 模式 | 触发条件 | 实际输出差异 |
|---|---|---|
| `foundation` 基础优先 | 该知识点独立答错 ≥1 且掌握估计 < 0.7 | 先讲概念与"为什么"，给小步骤示例，不直接跳综合题 |
| `standard` 常规 | 无作答证据 | 明确写"该知识点尚无作答证据 → 使用常规教学，不预设学生水平" |
| `advanced` 综合提升 | 样本 ≥2、掌握 ≥0.7 且**已通过间隔复现** | 直接进入多步推理与迁移应用 |

真实接口返回的 `teaching`（流式与非流式**同一实现**）包含：

```json
{"mode": "foundation",
 "reasons": ["该知识点有 2 次独立答错、0 次独立答对 → 以基础讲解与小步练习为主"],
 "signals": {"knowledge_point": "集合的基本概念", "independent_wrong": 2, "sample_size": 2,
             "mastery_level": 0.14, "consolidated": false},
 "applied": {"teaching_mode": "foundation",
             "memory": ["系统判断（依据：最近 2 次独立作答中有 2 次答错…）：集合的基本概念 需要加强"],
             "recent_mistakes": 2, "has_task_context": true}}
```

前端在回答下方渲染「本次讲解依据」+「已采用你的设置与记忆」，学生能看到**用了哪些信息**。

### 4.2 学习记忆三分（可查看 / 更正 / 删除 / 关闭）

- `preference`（学生明确设置，`explicit_user`）→ 优先级最高
- `inference`（系统按记录推断，带证据 + 有效期；`weak_ttl_days=14`、`slow_pace_ttl_days=21`、`advanced_ttl_days=30`）
- `curriculum`（教师/课程资料确认，需证据，保持待审核）

关键规则：显式设置**覆盖**同 key 的推断；推断过期自动失效；**一次错误不会被永久保存成能力标签**
（需要 ≥2 次独立答错且 0 次独立答对才生成"需要加强"，答对后撤销）；学生更正/学生点赞
**不会**变成课程权威知识。

真实模型验证（`tests/e2e_real_teaching.py`，23/23 通过）：
显式设置记忆 → 下一次提问的 `applied.memory` 立刻包含它；`PUT` 更正后按新值教学；
`DELETE` 后立即不再参与教学。

### 4.3 计划调整

约束（`plan_constraints`）：截止日期、每天可用分钟、教师固定截止时间、自动调整开关。
- 目标在预算内无法完成时**明确提示冲突**并给"缩小范围"方案，不生成看似合理但超时的计划
- 幂等提议键（待完成任务 + 截止日期指纹）→ 同一事件不重复创建调整建议或任务
- 重大调整需学生确认后才应用；`auto_adjust` 打开时在授权范围内自动执行
- 展示变更内容与原因，保留版本与撤销入口；手动编辑过的任务不被自动改动

---

## 五、测试命令与结果

### 5.1 自动测试（全部 Mock，隔离临时目录）

```bash
cd E:\WORKBUDDY\Game\git_text
python -m pytest tests/ -q          # → 454 passed（约 85s）
python -m pytest tests/test_phase1_hardening.py -q   # 补验 1–6，31 passed
python -m pytest tests/test_phase2_agent.py -q       # 阶段二，30 passed
```

**未通过项目：无。** 正式库零写入（跑测试前后 5 个库全表 MD5 一致，方法见 `docs/TEST_DATA_INVENTORY.md`）。

### 5.2 真实模型验收（与自动测试分开记录）

```bash
# 后端必须跑在隔离数据目录
EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_e2e_data" python -m uvicorn api.app:app --port 8000
python tests/make_e2e_env.py                    # 生成隔离验收数据目录（不复制任何个人库）

python tests/e2e_real_teaching.py               # → 23/23 通过
python tests/verify_default_model.py            # → 19/19 通过（默认模型 / 旧会话兼容）
node tests/e2e_agent_flow.js http://localhost:3000 离散数学 \
  "我3天后要考这一章，每天只有30分钟，帮我复习" DeepSeek   # → 19/19 通过（真实 Chrome）
node tests/e2e_model_default.js http://localhost:3000     # → 9/9 通过（前端默认模型）
```

**实际使用的模型：DeepSeek**（`provider=deepseek`，`model=deepseek-chat`），且已是**默认**模型
（请求不必带 `model`）。MiMo 为**会员到期**：显式选择 MiMo 时接口如实报错
（`/chat` 返回 500 `LLM服务不可用，请检查API配置`），**不会**静默替换成 DeepSeek；此时计划、任务、历史、
错题、本地题库判分、记忆**全部仍可用**（已在真实验收中逐项验证）。

---

## 六、数据迁移与启动方式

### 6.1 数据目录

| 用途 | 位置 | 说明 |
|---|---|---|
| 正式数据 | `data/`（或 `EDU_DATA_DIR` 指定） | `chat.db`、`learning.db`、`learning_loop.db`、`user_agent.db`、`auth.db` |
| 迁移备份 | `data/backups/20260925-1345*`（5 组） | 迁移前快照，**保留** |
| 自动测试 | 系统临时目录 | 夹具自动创建/清理 |
| 真实模型验收 | `E:/WORKBUDDY/Game/git_text_e2e_data` | `tests/make_e2e_env.py` 生成，只复制知识库，不复制个人库 |

`config/path_config.get_data_dir()` 是数据根目录的**单一事实来源**；`/health` 的
`data_scope.isolated` 用于让脚本在写入前自检。

### 6.2 启动

```powershell
# 后端
cd E:\WORKBUDDY\Game\git_text
python run_server.py            # 或 uvicorn api.app:app --port 8000

# 前端
cd frontend && npm run dev      # http://localhost:3000

# 隔离验收用后端（不影响正式数据）
$env:EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_e2e_data"
python -m uvicorn api.app:app --host 127.0.0.1 --port 8000
```

迁移幂等可重复执行；历史 `default_user` 数据登记为 `legacy_data_claims`（UNCLAIMED），
不共享给新用户、不静默分配。

---

## 六点五、默认文本模型切换（2026-09-26）

**结论：MiMo 不可用是会员到期（用户确认），不是程序故障。默认文本模型已统一为 DeepSeek。**

| 位置 | 改动 |
|---|---|
| `config/models.py` | `DEFAULT_MODEL = "deepseek"`；新增 `TEXT_MODELS`；`get_default_model()` 校验取值，未知/语音/视觉取值回落到默认，**不自动回退 MiMo** |
| `config/llm_config.py` | `provider` 改为 `default_factory=get_default_model`（与默认模型同源，避免两处默认值打架）；新增 `resolved_text_model()` |
| `.env` | `LLM_MODEL=mimo` → `LLM_MODEL=deepseek`（**真正生效的一处**，只改代码默认值会被它覆盖） |
| `chat_service.py` | 6 处硬编码 `"mimo"` 回退值改为统一解析入口；响应/事件新增 `provider`，`model` 改为**实际调用的模型名** |
| `api/schemas/chat_schema.py`、`api/routes/chat.py`、`api/routes/models.py` | 默认值改为运行期解析；响应透出 `provider` / `provider_display` |
| `services/note_service.py`、`services/education_service.py` | 结构化结果的 `model` 由空串/`"current"` 改为**实际模型名**（此前笔记/自测消息在会话里丢失模型标记） |
| `services/multimodal_service.py` | 视觉不再回退到默认文本模型；无可用视觉凭证时标记不可用并给出原因 |
| `services/audio_service.py` | 新增 `speech_capability()`（带缓存探测）；401/403 文案改为"MiMo 凭证被拒（会员到期）" |
| `api/app.py` | `/health` 新增 `capabilities`：文本 / 视觉 / 语音各自"提供方 + 是否可用 + 原因" |
| 前端 `ChatBox/index.tsx` 等 | 默认模型改为从后端 `/models/status` 取；空值表示"跟随后端默认"；旧版遗留的 `chat_model=mimo` **一次性提示并切换**，之后不再强制覆盖；主动选择记为显式选择；回答下方显示"provider / model" |

**验收结果（真实运行，隔离数据目录）**

```bash
python -m pytest tests/ -q                              # 454 passed（新增 22 例默认模型回归）
python tests/verify_default_model.py                    # 19/19 通过
node tests/e2e_model_default.js http://localhost:3000   # 9/9 通过（真实 Chrome）
```

- 不手动选择模型 → 问答/流式/Agent/笔记/自测/计划全部走 DeepSeek，响应里 `provider=deepseek`、`model=deepseek-chat`
- 旧会话：历史 MiMo 提问标记**保持原样**，同会话新回答用 DeepSeek 并如实标注
- 显式选择 MiMo → 如实报错（`LLM服务不可用，请检查API配置`），**不静默替换**成 DeepSeek
- 知识图谱（`GRAPH_MODEL` 未设置时）与知识提取同样走默认文本模型

**视觉与语音（单独判定，未受文本切换影响）**

| 能力 | 提供方 | 现状 |
|---|---|---|
| Vision | qwen-vision（DashScope） | ✅ 真实调用成功（返回图片描述）；与 MiMo 无关 |
| ASR / TTS | mimo-asr / mimo-tts | ❌ MiMo 会员到期 → 不可用；接口返回 502 + `invalid_api_key` 并说明原因，**不使用 DeepSeek 文本接口冒充** |

语音不可用时，文字学习功能（问答、计划、任务、错题、本地判分、记忆）实测全部正常。

---

## 七、尚未完成的内容与下一轮入口

| 项 | 现状 | 下一轮入口 |
|---|---|---|
| MiMo 文本（可选） | 会员到期；默认模型已切 DeepSeek | 续费后把 `LLM_MODEL` 改回 `mimo`，重跑 `tests/verify_default_model.py` |
| MiMo 语音（ASR/TTS） | 会员到期 → 不可用（已如实上报原因） | 续费即恢复；本轮不部署本地 TTS |
| Qwen Vision 图片理解 | ✅ 已真实验证可用 | — |
| 正式库历史残留清理 | 已出清单，**未删除**（待授权，不按前缀批量删） | 按 `docs/TEST_DATA_INVENTORY.md` 第五节方案执行 |
| 教师端 | 本轮明确不做 | 记忆/课程知识已有 `curriculum` 类型与待审核状态，可挂教师审核入口 |
| 本地 TTS 模型 | 本轮明确不做 | 保留 `audio` 服务边界，前端播放接口已统一带身份 |
| 课程知识自动扩展 | 仅修正"异常时默认通过"，保持待审核 | 接 `knowledge_expander` + 审核队列 |
| 前端代码分割 | 单包 3.7MB（gzip 1.25MB） | manualChunks / dynamic import |
| Agent 计划复用 | 每次运行现算 | 可把 `agent_runs.results` 作为缓存键复用 |

---

## 八、关键文件索引

```
services/idempotency.py        分步幂等账本（跨库重试安全）
services/teaching_context.py   画像 → 教学策略（预算 900 字符，可解释）
services/learning_memory.py    记忆三分 + 证据/时效/覆盖规则
services/plan_adjustment.py    时间预算分析、调整提议/应用/撤销
services/agent_runtime.py      工具注册表 + 状态机 + 限额 + 运行记录
services/mastery_service.py    加权掌握估计 + 掌握巩固（间隔复现）
services/review_service.py     复习任务 / 延后复测
services/quiz_service.py       题库、答案隔离、权威判分、错题本
api/routes/{memory,plan_agent,agent_run}.py   阶段二接口
tests/fixtures_isolated.py     测试数据隔离夹具（临时目录 + 全路径 patch）
tests/test_phase1_hardening.py 补验 1–6（31 用例）
tests/test_phase2_agent.py     阶段二（30 用例）
tests/e2e_real_teaching.py     真实模型教学验收（23 项）
tests/e2e_agent_flow.js        真实模型 Agent 流程 + 浏览器验收（19 项）
tests/verify_default_model.py  默认文本模型运行时验收（19 项）
tests/e2e_model_default.js     前端默认模型 + 旧版本兼容验收（9 项）
tests/test_default_model_switch.py  默认模型切换回归（22 例）
tests/inventory_production_data.py  正式库残留清单（只读）
```
