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
