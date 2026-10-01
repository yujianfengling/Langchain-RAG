# 教师端：课程 · 班级 · 教案 · 作业 · 学情

> 更新时间：2026-09-26
> 面向对象：接手开发者、演示者（如何建立一门可演示的课程）、项目负责人（权限模型）

---

## 一、5 分钟建立一门可演示的课程

准备工作：后端 `:8000`、前端 `:3000` 已启动（见 `PROJECT_STATUS.md`「启动方法」）。

### 1. 教师：创建课程与班级

1. 打开 http://localhost:3000 → 右下角「登录 / 注册」→ 注册一个账号（如 `teacher_demo`）。
2. 左侧菜单出现 **教师工作台**（新账号默认是学生；**创建第一门课程时后端才授予教师角色**）。
3. 「我的课程」→ 新建课程（填写课程名称，如 `数据结构（演示）`）。
   系统会同时为该课程创建一个**专属学科与知识库**（课程资料与别处互不干扰）。
4. 进入「班级管理」→ 新建班级（如 `周一班`）→ 记下页面上大号显示的 **6 位邀请码**（如 `8JUMLM`）。

### 2. 教师：上传资料并建立知识库

1. 进入「课程资料」→ 上传讲义/教材（支持 pdf/docx/txt/md，单个 ≤20MB）。
2. 上传后可点「重建知识库」，页面会显示处理状态与失败原因（失败不静默）。
3. 资料可设为「仅教师可见」（学生看不到也下载不到）或「学生可见」。

### 3. 教师：编写并发布教案

1. 进入「教案」→ 填课程主题、课时、教学目标、选择班级 → **生成教案草稿**（AI 依据本课程资料）。
2. 草稿会显示**依据的课程资料**；资料为空时明确标注「课程资料不足」，不会编造具体知识点。
3. 在各区块直接修改 → 「保存为新版本」（**每次都新增版本，历史版本保留，重新生成不会覆盖你的修改**）。
4. 「预览」看 Markdown → 「发布」后学生才可见 → 「导出」下载 `.md`。

### 4. 教师：发布练习

1. 进入「作业与练习」→ 新建作业（选班级、标题、知识点、截止日期）。
2. 「AI 出题」依据课程资料生成题目（题干+选项，**答案留在服务端**）。
3. 「发布」→ 系统为该班**当前在班学生**各创建一条学习任务，并把截止日期写成
   **教师固定截止时间**；结果会如实显示成功/跳过/失败人数。
4. 已发布的作业不能再改（避免静默改动学生已看到的截止时间）——需要变更就新建一次。

### 5. 学生：加入并完成

1. 学生注册并登录 → 「我的学习」→「我的课程」→ 输入教师给的**邀请码** → 加入。
2. 「老师布置的作业」里点「进入作答」→ 逐题作答（**后端判分**，交卷前不显示标准答案）。
3. 题都作答后可「标记完成」；任务同时出现在「今日任务」里。
4. 学生自己的计划/Agent 建议**只能更早、不能推后**教师设定的截止时间。

### 6. 教师：看学情、调整教学

1. 进入「学情分析」→ 选班级与时间范围（7/30/90 天）。
2. 看到：参与人数与样本量、任务完成情况、各知识点作答、常见错题、每日趋势——**全部由程序按真实记录计算**，
   每块都标注数据来源；每项结论都带班级、时间范围、参与人数。
3. 样本不足（默认 <5 次已判分作答或 <2 名学生）时显示「数据不足」并给出原因，不出班级结论。
4. 从「某知识点错误较多」可下钻：错题（题干+选项，不含答案）→ 相关课程资料 → 建议动作（补资料 / 发补充练习）。
5. 「AI 解读」只在样本足够时调用模型；样本不足时直接返回「数据不足，未调用模型」。

---

## 二、角色与权限模型（**全部由后端判定**）

### 角色

| 角色 | 如何获得 | 说明 |
|---|---|---|
| `student` | 注册即默认 | **不静默提升任何历史/新账号** |
| `teacher` | 用户**显式创建课程**时由后端授予 | 记录 `role_granted_at`，可审计；只升不降 |
| `admin` | 仅环境变量 `ADMIN_USERS`（逗号分隔用户名） | 不能自助获取 |

前端菜单只是展示（`/teacher` 仅对 teacher/admin 显示），**隐藏菜单不等于权限**：
直接请求接口一样会被后端拒绝。

### 课程资源的三种可见性

| 资源 | 授课教师 | 在班学生 | 其他教师/外来者 | 管理员 |
|---|---|---|---|---|
| 课程详情、班级、成员、学情 | ✅ | ❌ | ❌ | ✅ |
| 课程资料（`students` 可见） | ✅ | 只读 | ❌ | ✅ |
| 课程资料（`teacher_only`） | ✅ | ❌（列表与下载都拿不到） | ❌ | ✅ |
| 教案草稿 | ✅ | ❌ | ❌ | ✅ |
| 教案（已发布） | ✅ | 只读 | ❌ | ✅ |
| 作业草稿 | ✅ | ❌（对外一律"不存在"） | ❌ | ✅ |
| 作业（已发布） | ✅ | 只读、可作答 | ❌ | ✅ |
| 学生个人记录（作答/错题/计划/聊天） | ❌ | 仅本人 | ❌ | ❌ |

教师端**只使用汇总数据**，不读取、不返回学生个人聊天原文（有自动化用例守着这一点）。

### 学科归属：与历史数据兼容

- 历史全局学科（`data/subjects.json` 中**没有**归属记录的）保持既有行为：
  登录用户可读，写操作沿用原 `KNOWLEDGE_ADMIN_USERS` 白名单。
  **不会**被自动分配给任何教师。
- 教师建课时后端新建一个**课程专属学科**并登记归属（`teaching.db: subject_owners`）。
  只有**登记过的学科**才受课程权限约束：读=教师/在班学生/管理员，写=仅授课教师/管理员。
- 因此旧接口（知识库、文档、图谱、知识提取、学科）对课程学科**同样受约束**，
  不存在"只保护新接口"的绕过路径。

> 开关：`TEACHING_SCOPE_GUARD=0` 可临时关闭学科归属守卫（仅排障用，此时旧接口对课程学科
> 会退回"白名单模式"）。默认开启。

---

## 三、数据模型（`data/teaching.db`，独立库）

| 表 | 用途 |
|---|---|
| `courses` | 课程：一位授课教师 + 一个专属学科 |
| `classes` | 班级：`join_code` 供学生加入 |
| `class_members` | 成员：保留 `active/left/removed` 与时间，不物理删除 |
| `membership_events` | 加入/退班/移除留痕 |
| `course_materials` | 课程资料：状态、失败原因、可见范围 |
| `lesson_plans` / `lesson_plan_versions` | 教案与**每次编辑/生成的版本** |
| `assignments` / `assignment_questions` | 作业与题目关联（题目复用既有 `quiz_questions`） |
| `course_audit` | 课程操作留痕（谁在何时改了什么） |
| `subject_owners` | 学科归属登记（课程学科才受权限约束） |

既有库只做**幂等加列**，不改语义、不删数据：
- `auth.db: users.role` / `users.role_granted_at`
- `learning.db: learning_tasks.assignment_id`（教师作业物化的任务溯源）

迁移可重复执行：

```powershell
python -c "from services.teaching_data import migrate; import json; print(json.dumps(migrate(), ensure_ascii=False))"
```

运行前请备份业务库（复制 `data/*.db` 到 `data/backups/<时间戳>-pre-.../`）。

---

## 四、接口一览

### 教师端（均需登录，后端校验课程归属）

```
GET    /api/teacher/dashboard                              工作台：课程/待完成教案/已发布任务/需关注知识点
POST   /api/teacher/courses                                创建课程（并授予教师角色）
GET    /api/teacher/courses                                我的课程
GET    /api/teacher/courses/{id}                           课程详情（班级/资料/教案/作业/知识库状态）
PATCH  /api/teacher/courses/{id}                           改标题/描述/归档
POST   /api/teacher/courses/{id}/classes                   建班级（返回邀请码）
GET    /api/teacher/courses/{id}/classes                   班级列表
GET    /api/teacher/classes/{id}/members                    成员（含退班/被移除记录）
POST   /api/teacher/classes/{id}/reset-code                 重置邀请码
POST   /api/teacher/classes/{id}/members/{mid}/remove       移出学生
POST   /api/teacher/courses/{id}/materials                  上传资料（multipart file；**不接受路径**）
GET    /api/teacher/courses/{id}/materials                  资料列表 + 知识库状态
DELETE /api/teacher/materials/{mid}?course_id=              删除资料并重建索引
POST   /api/teacher/materials/{mid}/visibility?course_id=   设置可见范围
POST   /api/teacher/courses/{id}/knowledge/rebuild          重建课程知识库（失败返回原因）
POST   /api/teacher/courses/{id}/lesson-plans/generate      AI 生成教案草稿（含依据）
POST   /api/teacher/courses/{id}/lesson-plans               手写教案草稿
GET    /api/teacher/courses/{id}/lesson-plans               教案列表（可按状态筛选）
GET    /api/teacher/lesson-plans/{id}                       教案详情（可指定版本）
PUT    /api/teacher/lesson-plans/{id}                       编辑 → 保存为**新版本**
GET    /api/teacher/lesson-plans/{id}/versions              版本列表
POST   /api/teacher/lesson-plans/{id}/publish               发布（内容不完整会被拒）
GET    /api/teacher/lesson-plans/{id}/preview               预览 Markdown
GET    /api/teacher/lesson-plans/{id}/export                导出 .md
POST   /api/teacher/courses/{id}/assignments                建作业草稿
POST   /api/teacher/courses/{id}/assignments/generate-questions  AI 出题（不含答案）
GET    /api/teacher/courses/{id}/assignments                作业列表 + 发布统计
PUT    /api/teacher/assignments/{aid}                       修改（仅草稿）
POST   /api/teacher/assignments/{aid}/publish               发布（物化学生任务 + 教师固定截止）
POST   /api/teacher/assignments/{aid}/close                  关闭
GET    /api/teacher/classes/{id}/analytics?days=30           学情（程序统计）
GET    /api/teacher/classes/{id}/analytics/knowledge-points/{kp}  知识点下钻
POST   /api/teacher/classes/{id}/analytics/explain           AI 解读（样本不足不调用模型）
```

### 学生端（教师端联动）

```
GET    /api/student/courses                       我加入的课程与班级
POST   /api/student/courses/join                  用邀请码加入
POST   /api/student/classes/{id}/leave            退出班级（留痕）
GET    /api/student/assignments                   老师布置给我的作业（仅已发布，无答案）
GET    /api/student/assignments/{id}              作业详情（含题目、我的作答结果）
POST   /api/student/assignments/{id}/complete     标记完成（题目都要作答过）
GET    /api/student/courses/{id}/lesson-plans     本课程已发布教案
GET    /api/student/courses/{id}/materials        本课程对学生可见的资料
```

作答仍走既有 `POST /api/quiz/answer`（**后端权威判分 + 答案隔离 + 幂等**），不另建题库。

---

## 五、与既有学习闭环的衔接

- 发布作业 → 为每名在班学生创建 `learning_tasks`（`source='teacher_assignment'`, `assignment_id`），
  因此**自动出现在学生「今日任务」**，并按既有 QUIZ 规则（需真实作答才能完成）处理。
- 发布作业同时写 `plan_constraints.teacher_deadline`；既有规则保证学生侧**只能更早、不能推后**，
  Agent 的调整方案也不会把任务排到该截止之后。
- 学情统计读取 `quiz_attempts` / `learning_tasks` / `class_members`，与原始记录一致（有用例校验样本量）。
- 答错仍会触发出错题本与自动复习（既有联动），教师端不重复实现。

---

## 六、本轮验收结果

| 验收项 | 结果 |
|---|---|
| 教师端全流程（真实 Chrome + 真实 DeepSeek） | **37/37**（`node tests/e2e_teacher_flow.js`） |
| 教师端自动测试（服务层，Mock 模型） | **33 passed**（`tests/test_teacher_module.py`） |
| 教师端接口测试（HTTP 层，含旧接口越权对抗） | **23 passed**（`tests/test_teacher_api.py`） |
| 全量测试 | **540 passed**（正式库 6 个 DB 零写入） |
| 回归 | 默认模型 9/9、Agent 19/19、语音 22/22 |

真实模型调用与 Mock 测试分开报告：`e2e_teacher_flow.js` 用真实 DeepSeek（教案生成、AI 出题、AI 解读）；
`test_teacher_module.py` / `test_teacher_api.py` 的外部模型全部 Mock，两者不混记。

---

## 七、已知限制（如实记录）

1. **加入方式只有邀请码**：首版不接入学校统一身份认证（按本轮要求避免复杂 SSO）；邀请码可重置（旧码立即失效）。
2. **一门课程一位授课教师**：暂不支持多教师协同授课；如需协作，请由同一位账号管理课程知识库。
3. **作业题目绑定课程学科**：题目不能跨课程复用（防止把别的课程内容挂进来）；
   跨课程复用需要先复制资料再出题。
4. **学生退班后再加入**：会复用同一条成员记录并留痕；期间的作答记录仍保留（不做删除）。
5. **学情阈值固定**：`<5` 次已判分作答或 `<2` 名学生即判定"数据不足"（`class_analytics.MIN_*`，可按需调整）。
6. **教案导出为 Markdown**：未做 docx/pdf 导出（不引入新的重依赖；Markdown 在现有依赖下最稳）。
7. **课程归档**：`courses.status=archived` 后不再出现在课程列表，但数据保留；暂未做"恢复归档"入口。
8. **管理员角色需配置环境变量**：未配置 `ADMIN_USERS` 时没有管理员（不影响教师自身功能）。
