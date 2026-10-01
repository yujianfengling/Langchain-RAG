# 阶段一：学生学习闭环（交付说明）

> 更新日期：2026-09-25
> 基线 commit：`7bd14fc`（本轮改动未提交，工作区改动见文末清单）
> 本文只记录**经真实验证**的内容，不含任何真实 API Key。

> **后续更新（2026-09-25）**：本轮在阶段一之上完成了 6 项针对性补验（答案真隔离、复习完成语义、
> 对象级权限、6 个失败测试、测试数据隔离、事件重试跨库幂等），并实现了阶段二「能按真实学习记录
> 调整教学的学习 Agent」。补验的问题、证据与修复见
> [`docs/PHASE2_LEARNING_AGENT.md`](./PHASE2_LEARNING_AGENT.md) 第三节；
> 正式库历史残留清单见 [`docs/TEST_DATA_INVENTORY.md`](./TEST_DATA_INVENTORY.md)。
> 当前全量测试：**432 passed / 0 failed**（本文下方"365 passed / 6 failed"为阶段一当时的状态）。

---

## 一、验收主链路（已端到端跑通）

```
登录 → 从学习计划进入知识点 → 生成资料/笔记 → 完成自测 → 后端判分
→ 保存作答与错题 → 更新学习状态 → 生成后续复习任务 → 首页/计划同步展示
```

### 1.1 真实模型验证（非 Mock）

脚本：`tests/e2e_real_flow.js`（驱动系统 Chrome，后端真实调用大模型）

```
node tests/e2e_real_flow.js http://localhost:3000 离散数学 "集合的基本概念" DeepSeek
```

结果：**16/16 项通过**（2026-09-25）

| 验证点 | 结果 |
|---|---|
| 真实模型生成自测题 | 5 道题入库并渲染 |
| 题目下发不泄露答案 | 通过（作答前页面无「正确答案/参考答案/解析」） |
| 后端权威判分 | 回答错误 / 你的作答：1 / 正确答案：D（4） / 解析来自知识库证据 |
| 判分后才返回标准答案 | 通过 |
| 首页统计联动 | 作答道数 1（正确率 0%）、错题 1 道、待复习 1 个知识点、待办 1 项 |
| 掌握度展示 | `集合的基本概念 / 暂估 / 33% / 证据（对 0 错 1）/ 学习行为 0 次 / 计算依据` |
| 联动事件可追溯 | `quiz_attempt_recorded`、`quiz_question_generated` 等全部 `DONE`，无 `FAILED` |

> 说明：该轮真实调用使用 **DeepSeek**，原因是 `.env` 中 `MIMO_API_KEY` 当前被供应商拒绝
> （HTTP 401 `invalid_api_key`），MiMo 走不通。这是**环境配置问题，不是代码问题**（详见第五节）。

### 1.2 Mock 验证（业务闭环与数据一致性）

脚本：`tests/test_learning_loop_phase1.py`（隔离临时库 + Mock 模型/RAG，13 个用例全部通过）

覆盖：身份归属、幂等、判分口径、错题合并、行为/证据分离、任务闸门、改期留痕、计划归属、
笔记事件归属。

### 1.3 页面与跳转验证

脚本：`tests/e2e_browser_check.js`（真实 Chrome，**27/27 通过**）

- 默认入口渲染「我的学习」；未登录显示登录提示，不展示任何学情
- 侧边栏 5 个入口齐全；注册 / 退出登录流程正常
- 学生页 6 个页签（今日任务 / 逾期 / 复习安排 / 错题本 / 掌握度 / 联动记录）
- 学科选择器可交互并触发数据刷新
- 学习计划页、学习助手页（含 自测 / 智能笔记 / 普通问答 模式）正常
- 无页面级 JS 异常、无 API 网络失败、无控制台错误

---

## 二、数据模型（阶段一新增）

新增库：`data/learning_loop.db`；`data/auth.db`；既有库补充归属列。

| 表 | 库 | 作用 |
|---|---|---|
| `knowledge_points` / `knowledge_point_aliases` | learning_loop.db | 知识点稳定 ID + 别名（同名/同义不重复建点） |
| `quiz_questions` | learning_loop.db | 题库：选项 ID、服务端标准答案、内容哈希去重 |
| `quiz_attempts` | learning_loop.db | 每次作答：判分结果、第几次、作答性质、幂等令牌 |
| `review_tasks` | learning_loop.db | 复习任务（同用户+学科+知识点唯一待办，合并去重） |
| `learning_events` | learning_loop.db | 统一事件（幂等键唯一 + 状态 + 重试） |
| `legacy_data_claims` | learning_loop.db | 历史 `default_user` 数据待认领清单 |
| `users` / `sessions` | auth.db | PBKDF2 口令哈希 + 不透明会话令牌 |
| `learning_plans.user_id`、`learning_tasks.{user_id,subject_id,knowledge_point_id,planned_date,source,source_attempt_id,defer_count,original_planned_date}` | learning.db | 计划可执行 + 归属 |
| `task_adjustments` | learning.db | 任务改期留痕 |
| `knowledge_mastery.{knowledge_point_id,mastery_status,evidence_count,independent_correct_count,independent_wrong_count,last_evidence_at,mastery_reason,behavior_count,last_behavior_at}` | user_agent.db | 行为/证据/掌握分离 |
| `conversations.user_id`、`messages.interaction_id` | chat.db | 会话归属 + 反馈关联真实交互 ID |

**迁移**：`services/learning_data.migrate_legacy()`，可重复执行、执行前自动备份到
`data/backups/<时间戳>/`，不删除历史资料/知识库/会话；历史归属标记为待认领，不静默分配。
新建库与历史库通过 `learning_data.ensure_columns()` 共用同一份列定义，避免结构漂移。

---

## 三、接口清单（阶段一新增）

### 身份 `api/routes/auth.py`
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/register` | 注册并签发令牌 |
| POST | `/api/auth/login` | 登录 |
| POST | `/api/auth/logout` | 撤销当前令牌 |
| GET | `/api/auth/me` | 当前用户 |
| GET | `/api/auth/legacy-claims` | 历史数据待认领清单 |
| POST | `/api/auth/claim-legacy` | 显式认领历史数据（先备份） |

### 自测 `api/routes/quiz.py`
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/quiz/answer` | 提交作答（**只收选项 ID，不收 `is_correct`**，后端判分，`client_token` 幂等） |
| POST | `/api/quiz/reveal/{question_id}` | 查看答案（被记录，之后作答标记 `after_reveal`，不计独立证据） |
| POST | `/api/quiz/self-evaluate` | 简答题自评（明确标记为自评，不伪装客观判分） |
| GET | `/api/quiz/attempts` | 作答历史 |
| GET | `/api/quiz/wrong` | 错题本（最近一次为错才在册；重做答对移出，历史保留） |
| GET | `/api/quiz/stats` | 作答统计 |
| GET | `/api/quiz/reviews` | 复习安排（含合并次数与原因） |

### 学生端 `api/routes/student.py`
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/student/tasks` | 任务视图（`bucket=today\|todo\|completed\|overdue`，不传返回聚合） |
| POST | `/api/student/tasks/{id}/complete` | 完成任务（幂等；只记行为；测验任务需先有真实作答，否则 409） |
| PUT | `/api/student/tasks/{id}/schedule` | 改期（保留原日期 + 调整历史） |
| GET | `/api/student/tasks/{id}/adjustments` | 改期历史 |
| GET | `/api/student/mastery` | 掌握情况（无证据返回 `mastery_level=null` + `pending`） |
| GET | `/api/student/home` | 首页聚合（今日任务/逾期/待复习/错题/最近作答/统计/上次位置） |
| GET | `/api/student/learning-events` | 联动事件流水（可追溯） |
| POST | `/api/student/learning-events/retry` | 重试本人失败事件 |

### 身份口径（重要）
- 所有学习数据接口从 **`Depends(get_current_user)` / `require_current_user`** 取用户，
  **忽略请求体传入的 `user_id`**；写接口未登录返回 401。
- 历史「路径含 `{user_id}`」的 Agent 接口由 `api.deps.resolve_user_scope()` 统一处理：
  `me`/空/等于本人/历史占位账号 → 本人；指向其他真实账号 → 403。
- `/api/agent/quiz-answer` 已改为**服务端权威判分**（旧版接受前端 `is_correct`，已移除）。
- 学习计划列表/详情/删除、任务状态接口均已按当前用户过滤（修复此前任何人可见他人计划的问题）。

---

## 四、口径修正（容易踩坑，务必保持）

1. **行为 ≠ 掌握**：阅读/完成任务只累加 `behavior_count`，**不改变掌握度**。
2. **掌握度只由独立作答证据计算**：`mastery = (对 + 1) / (证据 + 2)`（拉普拉斯平滑）；
   证据仅 `independent` 与 `review`；`after_reveal` 记录但不计入。
3. **无证据不给分**：`mastery_status=pending`、`mastery_level=NULL`，前端显示「待评估」。
4. **答案不下发**：题库 → 前端的公开题目不含 `answer`/`explanation`；只有提交作答或
   显式查看答案才返回。
5. **简答题不伪造客观分**：`grade_status=pending_self_eval`，自评后为 `self_evaluated`。
6. **复习合并去重**：同一用户+学科+知识点的多次错题合并为一条待办（`merged_count` 累加，
   取更早到期日）；答对则关闭待办、保留历史。
7. **已废弃、勿重新接线**：`services/learning_loop.py` 的 `on_quiz_answered` /
   `on_plan_task_completed` / `_ensure_knowledge_point`（按行为直接加/初始化掌握度）与
   `user_profiler.update_knowledge_mastery`。

---

## 五、已知限制与环境问题（未通过项，如实记录）

1. **MiMo 密钥无效**：`MIMO_API_KEY` 调用返回 401 `invalid_api_key`（供应商侧拒绝）。
   因此本轮真实模型验证改用 DeepSeek（可用）。修复方式：更换有效 MiMo Key 后重跑
   `tests/e2e_real_flow.js`（不传第 5 个参数即用默认 MiMo）。
2. **旧学习/笔记测试 6 个失败（既有问题，非本轮引入）**：`test_learning_service.py`、
   `test_note_service.py` 用「数据结构」学科，但 `data/subjects.json` 实际只有
   离散数学 / 大学物理 / 大学物理1，且该学科无向量库 → `subject_not_found`。
   属于运行时数据缺失，需先创建学科并构建知识库，或改用已建库学科。
3. **前端包体**：`dist` 单包 3.7MB（gzip 1.25MB），未做代码分割（既有状态）。
4. **浏览器验收产生的测试数据**：真实库中新增了若干测试账号（`e2e_*` / `stu_*`）与
   对应作答记录、5 份迁移备份（`data/backups/`）。这些是本轮真实验证的副产物，
   未自动清理（避免误删用户数据）。如需清理，请先确认后再手工删除对应用户及其记录。

---

## 六、本轮改动清单

新增后端：`api/deps.py`、`api/routes/{auth,quiz,student}.py`、
`services/{auth_service,learning_data,learning_events,learning_handlers,mastery_service,quiz_service,review_service}.py`

新增前端：`src/api/{auth,student}.ts`、`src/components/{QuizList,UserAuth}/`、`src/pages/StudentPage.tsx`

新增测试：`tests/test_learning_loop_phase1.py`、`tests/e2e_browser_check.js`、`tests/e2e_real_flow.js`

修改：`api/app.py`、`api/routes/{agent,chat,learning}.py`、`api/schemas/chat_schema.py`、
`chat_service.py`、`services/{conversation_service,learning_loop,learning_service,note_service,user_profiler}.py`、
`frontend/src/{App.tsx,types/index.ts,api/*,components/*,pages/*}`、`.gitignore`

---

## 七、复现验证步骤

```powershell
# 1. 后端
cd E:\WORKBUDDY\Game\git_text
python -m uvicorn api.app:app --host 127.0.0.1 --port 8000

# 2. 前端
cd E:\WORKBUDDY\Game\git_text\frontend
npm run dev

# 3. 单元/接口验收（Mock，快）
python -m pytest tests/test_learning_loop_phase1.py -q

# 4. 浏览器页面与跳转验收（不调模型）
node tests/e2e_browser_check.js http://localhost:3000

# 5. 真实模型端到端验收（会真实调用模型）
node tests/e2e_real_flow.js http://localhost:3000 离散数学 "集合的基本概念" DeepSeek
```

> 浏览器脚本依赖 `playwright`（使用系统 Chrome，不下载额外浏览器）。
> 截图输出到 `tests/_screenshots/`（已在 `.gitignore` 中忽略）。
