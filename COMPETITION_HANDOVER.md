# 智慧学伴比赛版 · 交接文档（持续更新）

> 工作目录：`D:/Document/Langchain-RAG-smart-study-partner`（独立 worktree）
> 比赛分支：`competition/smart-study-partner`（起点 `3ad7ae7`，**未提交、未推送**）
> 主仓库 master 在 `D:/Document/Langchain-RAG`，本次改动**不动 master**
> 更新：2026-11-20（录屏前定向验收：预算分组、覆盖分母、D08三阶段输入，最新结果见第十五节）

## 〇、接手方式与本轮计划（2026-11-20）

- 本项目接下来由用户**一人完成**，不再准备分工通知。
- 今天先用 WorkBuddy 继续开发，额度用完后由 **Qoder 直接接手**；本文档即交接主入口。
- 本轮目标：把**官方赛题真实数据**接进现有三页面，优先做到「真实两班统计 → 点到真实答卷」。**已完成，见第十节。**
- 约束：改动只留在比赛 worktree；不提交、不推送；每完成一个可验证小步骤就更新本文档。

## 一、当前 Git 状态（开始前核对，2026-11-20 14:29）

```
分支：competition/smart-study-partner
HEAD：3ad7ae7
git status --short：
 M frontend/index.html
 M frontend/src/App.tsx
 M frontend/src/components/AccountMenu/index.tsx
 M frontend/src/components/AppChrome/index.tsx
 M frontend/src/components/LoginRequired/index.tsx
 M frontend/src/index.css
 M frontend/src/pages/TeacherPage.tsx
?? COMPETITION_HANDOVER.md
?? frontend/e2e-competition.cjs
?? frontend/public/assets/
?? frontend/src/competition/
git diff --stat：7 files changed, 180 insertions(+), 175 deletions(-)
git diff --cached --stat：空（无暂存改动）
```

上述未提交改动**全部保留**，本轮在其之上继续。

## 二、启动与验收命令

```bash
# 1) 启动后端（托管 frontend/dist）
cd D:/Document/Langchain-RAG-smart-study-partner
# Windows CMD:
set EDU_DATA_DIR=D:/Document/langchain-rag-work/demo_data
set TEACHER_USERS=demo_teacher,e2e_teacher,new_teacher_mushevth
set COMPETITION_OFFICIAL_BUNDLE_DIR=D:/Document/langchain-rag-work/competition-data/official-bundle
D:/Document/langchain-rag-venv/Scripts/python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8000

# 2) 重新构建前端（node_modules 已从主仓库复制，dist 被 gitignore）
cd D:/Document/Langchain-RAG-smart-study-partner/frontend && npx vite build

# 3) 浏览器验收（Playwright + 系统 Edge）
cd D:/Document/Langchain-RAG-smart-study-partner/frontend
node e2e-competition.cjs          # 演示样例，22 项检查
node e2e-competition-real.cjs     # 自造样例 bundle，11 项检查（?source=real）
node e2e-competition-official.cjs # 官方数据链路，14 项检查（?source=official，需后端带私有目录环境变量）

# 4) 类型/语法检查
cd frontend && npx tsc --noEmit && npx eslint src

# 5) 数据读取/校验/页面 bundle（详见第九节）
cd D:/Document/Langchain-RAG-smart-study-partner
D:/Document/langchain-rag-venv/Scripts/python.exe competition_pipeline/pipeline.py self-test
D:/Document/langchain-rag-venv/Scripts/python.exe competition_pipeline/bundle.py build \
  --src D:/Document/langchain-rag-work/competition-data/self-test \
  --csv-dir competition_pipeline/samples \
  --out frontend/public/assets/competition-data --source sample
```

登录账号：`new_teacher_mushevth`（口令见本地口令记录，不写入本文档；TEACHER_USERS 白名单含 demo_teacher/e2e_teacher/new_teacher_mushevth）→ 默认进「班级学情」。

## 三、三个页面与四块模块入口

### 三个页面（路由）

| 页面 | 路由 | 当前交互（已跑通，虚构样例） |
|---|---|---|
| 班级学情 | `/class-analytics` | 测验切换/两班选择/主题筛选（仅三个正式主题）；考点对比表并排显示两班得分率+有效作答数；点考点展开失分题与相关学生；三种状态可区分；切换筛选后状态仍正确 |
| 学生答卷 | `/student-answers` | 从班级下钻带 `studentId/questionId/quizId/classId/kpId` 定位到题；顶部搜索/选学生；左=题干+原始步骤+得分+评分点(哪步丢分+证据)，右=下一步+学习建议+预览学生反馈 |
| 课标与教材 | `/curriculum` | 三主题→考点→课标要求/题目/教材页；从答卷题目反查出处（`?questionId=`）；教材侧边抽屉打开样例 PDF 指定页，关闭回原处 |

### 四块模块（全部在 `frontend/src/competition/`）

```
competition/
├─ shared/                       【集成】
│  ├─ types.ts                   共享类型 + ID 关联约定
│  ├─ ids.ts                     共享名册：TOPICS/CLASSES/QUIZZES/STUDENTS/教材定义
│  └─ TextbookDrawer.tsx         共享教材抽屉（PdfCanvasViewer）
├─ dataAccess.ts                 【集成】统一 re-export / 接线，不含样例数据实现
├─ classAnalytics/               【班级对比】ClassAnalyticsPage.tsx + data.ts
├─ studentAnswers/               【个人答卷】StudentAnswersPage.tsx + data.ts
├─ learningAdvice/               【学习建议】LearningAdvice.tsx + data.ts
└─ curriculum/                   【课标教材】CurriculumPage.tsx + data.ts
```

**数据接入点（改真实数据时优先改这些）**

| 功能 | 主要文件 | 数据接入函数 | 共享依赖 |
|---|---|---|---|
| 班级对比 | `classAnalytics/data.ts` | `listKnowledgePoints/getKpStat` | `shared/types.ts`(KpClassStat/MasteryStatus)、`shared/ids.ts`(CLASSES/TOPICS) |
| 个人答卷 | `studentAnswers/data.ts` | `getQuestion/getAnswer/listQuestionsByKp` | `shared/types.ts`(StudentAnswer/ScoringPoint)、题目出处走课标模块 |
| 学习建议 | `learningAdvice/data.ts` | `getSuggestion/getFeedback` | 组件入口固定：`kpId`+`studentId`+`onOpenTextbook` |
| 课标教材 | `curriculum/data.ts` | `getCurriculum/getSourceByQuestion` | `shared/TextbookDrawer.tsx`、`shared/ids.ts`(DEMO_TEXTBOOK) |

公共路由/页面外壳/跨模块跳转只由集成侧改：`frontend/src/App.tsx`、`components/AppChrome/index.tsx`、`competition/dataAccess.ts`、`competition/shared/`。

## 四、最新实测结果（截至 2026-11-20）

- **官方数据链路：14/14 PASS**（`?source=official`，受控接口），详情见第十节。
- 演示模式 Playwright：**22/22 PASS**（登录→两班对比→切换筛选→点失分→答卷→预览反馈→开教材第3页→关闭返回→返回保留筛选→课标页），无 JS 运行时错误。
- `tsc --noEmit`：通过；`eslint src`：通过；`vite build`：通过（仅 chunk 体积警告）。
- 评分一致性：虚构样例 q-201 四个评分点加总 4/10，与总分一致（e2e 已断言）。
- 截图：`D:/Document/langchain-rag-work/competition-shots/{class-analytics,student-answers,textbook-drawer}.png`。

## 五、仍是虚构样例（仅演示模式；官方数据已接，见第十节）

- **全部页面数据为【演示样例】虚构**：三个正式主题（代数式与整式运算/方程与方程组/不等式与不等式组）、两班、5 考点、李明移项题的部分得分作答、练习建议、教材出处。
- 重点样例 q-201（李明，4/10）：评分点 2/2 + 0/4（移项应先错：应 `2x=11−3=8`）+ 2/2（由本人 `2x=14` 得 `x=7`，除法本身对，但沿用错误等式）+ 0/2（无代回检验）= 4。教师诊断、右侧建议、学生反馈预览三处说法一致。
- 教材：`frontend/public/assets/demo-textbook.pdf` 是自制公开样例 PDF（6页，第3页=方程移项与检验），q-201 出处→第3页；**未改权限绕过真实页图 403**，真实赛事资料页图验证待有权限时做。
- **真实赛题材料、正式统计、诊断、练习推荐目前均未接**。数据换真实服务时，按上表各模块只改自己的 `data.ts`，保持共享 ID 对应。
- 本轮要求：真实答卷旁若还没有正式建议，显示「暂无学习建议」，**不要把虚构建议挂到真实学生身上**。

## 六、移除/删除说明（沿用）

- 已从导航与路由移除旧学生端/教师端多处入口，统一重定向到 `/class-analytics`。
- 已删：`components/Teacher/CompetitionFeedbackPanel.tsx`、`competition/demoData.ts`（内容拆到四模块 `data.ts` 与 `shared/`）。
- 保留未卸路由的页面与共享组件：`TeacherPage.tsx`、`StudentLearningPage.tsx` 等——共享组件仍被复用，后端清理留到有替代方案后。
- 保留登录权限、全部后端接口、`/me`、`/settings`。

## 七、进展日志（每完成一个小步骤即更新）

| 时间 | 步骤 | 结果 / 验证 |
|---|---|---|
| 2026-11-20 14:29 | 开始前核对分支/状态/diff | 已核对，分支与改动概况见第一节；未提交改动全部保留 |
| 2026-11-20 14:29 | 更新交接文档 | 本节此前内容已写入（当前状态、命令、入口、22/22、样例清单） |
| 2026-11-20 14:40 | 定位官方赛题资料 | **未找到**。全盘搜索了 Downloads/Desktop/Documents/D:/Document/D:/Download 及 QQ/企业微信接收目录，无官方 5 张 CSV、无《赛题数据说明》《参赛说明》《典型案例说明》PDF/DOCX。找到的相关文件只有 `D:/Download/智慧学伴_四人开发方案.docx`（含字段契约与 806→799 口径，但不是数据本身）和桌面 `北京市AI+大赛/开发方案.docx`（空文档）。详见第八节。 |
| 2026-11-20 14:42 | 建可复跑的数据读取/校验入口 | `competition_pipeline/{field_contract.py,pipeline.py}` + `samples/` 5 张自造 CSV（虚构）。命令：`python competition_pipeline/pipeline.py self-test`。结果：12 行→9 事件；独立核对 DC01/N01/IND-A 得分和=7、满分和=12、得分率=0.5833（3 条有效），DC02/N01/IND-A 有效零分保留（真实零分），DC01/N01/IND-B 仅 1 条有效→记录不足；重复行去 1 条写进 data_quality.csv。全部 PASS。详见第九节。 |
| 2026-11-20 14:52 | 生成页面 bundle + 运行时数据源开关 | `competition_pipeline/bundle.py` 把 pipeline 输出 reshap 到 `frontend/public/competition-data/*.json`；`competition/dataSource.ts` 默认演示样例、`?source=real` 才读 bundle。四模块 data.ts / ids.ts / 两页面 / 建议组件 / main.tsx 已接通。真实模式下建议一律返回空并显示「暂无学习建议」，缺步骤/评分点时页面如实说明。`tsc --noEmit`、`vite build` 通过。 |
| 2026-11-20 14:55 | 回归原有演示 22 项 | `node e2e-competition.cjs`：**22/22 PASS**，无 JS 错误；新增开关未破坏既有界面。 |
| 2026-11-20 14:57 | 官方资料到位 | 用户给出微信附件路径；`D:/Download` 下并无同名 zip。解压到仓库外私有目录，找到 5 张 CSV + 字段说明 + B01—B08 教材切片。第十节。 |
| 2026-11-20 15:00 | 官方导入 | 原解析器报 14 处字段差异；按字段说明补别名与可选字段后，导入 **806 行→799 事件、790/4/5、DC01=21/DC02=19**，与基准完全一致。2 条分数越界为官方原始数据，保留状态计数、隔离得分聚合。 |
| 2026-11-20 15:04 | 独立复算 | 直读官方 CSV 复算 N01/T01I2：DC01 69/101/67 条、DC02 61/93/65 条，与 pipeline 输出一致。 |
| 2026-11-20 15:10 | 官方链路跑通 | 受控接口 + `?source=official`：两班 68%/66% → DR0001（D01/Q015 2/3，题干/步骤/评分点齐全）→ 暂无学习建议。`node e2e-competition-official.cjs` **14/14 PASS**；演示 22/22、样例 11/11 回归通过；官方数据未进公开静态资源。 |

### 下一步先做什么（持续更新）

1. ~~定位官方赛题资料~~ → 已解压到私有目录（第十节 10.1）。
2. ~~数据读取/校验入口~~ → 已完成，官方基准一致（第十节 10.3）。
3. ~~官方两班统计 → 点到官方答卷~~ → 已跑通并独立复算（第十节 10.4/10.5）。
4. **当前真正下一步**：接**课标定位**（`curriculum/data.ts`，官方 B01—B08 切片 + 课标整理编号）与**练习建议**
   （`learningAdvice/data.ts`，官方学习卡 L01—L03）。入口详见第 10.8 节。

## 八、官方资料定位结果（2026-11-20 14:40 快照）

> **注意**：本节是上午的定位快照。用户随后提供了微信附件中的官方资料包，
> 官方数据已接入完成——**以第十节为准**。

**当时结论：官方赛题数据本机不存在，需要用户提供路径/文件。**

已搜索范围：`C:/Users/21508/{Downloads,Desktop,Documents}`（含 QQ/企业微信接收目录）、`D:/{Download,Document}` 全盘、`D:/神の文档`；搜索类型：`*.csv/xlsx/xls/json/zip/docx/pdf`；关键词：`课程要求/题目与材料/模拟学生/测验节点/作答记录/赛题/字段说明/学生/作答`。

找到的相关内容：
- `D:/Download/智慧学伴_四人开发方案.docx`：只有开发计划，注明赛题资料包含《参赛说明》《典型案例说明》《赛题数据说明》和模拟学情数据 CSV，并给出关键口径（5 张 CSV；806 行 → 799 事件＝790 已评分 + 4 待评分 + 5 未作答；DC01 21 人 / DC02 19 人；三主题 T01/T02/T03；节点 N01 只覆盖 T01 与 T02 的一元一次方程；候选案例 D01 的 DR0001 得 2/3、D15、D17）。**这些只是计划文档里的文字，不是官方数据。**
- `C:/Users/21508/Desktop/北京市AI+大赛/开发方案.docx`：空白文档，无内容。

**请用户提供以下任一路径**：
1. 官方赛题资料包解压后的目录（应含 5 张 CSV + 字段说明文档），或
2. 资料包压缩包（zip/rar/7z）的绝对路径，由 WorkBuddy 解压读取（原始包留在本地、不进 Git）。

需要的最小文件集合（按开发方案）：`课程要求.csv`、`题目与材料.csv`、`模拟学生.csv`、`测验节点.csv`、`作答记录.csv`（名称可能不同，以字段说明为准）。

**在拿到数据前**：按用户指示，先用现有虚构样例实现并验证「读取 + 校验」逻辑入口，接口按上述口径设计（有效零分/待评分/未作答/节点未覆盖/缺记录分开），不把演示题号或演示得分写成正式结果。

## 九、数据读取与校验入口（已完成，可复跑）

目录：`competition_pipeline/`
- `field_contract.py`：五张表的规范字段 + 别名表 + 状态归一。真实表头不在别名里时，写 `mapping.json` 用 `--mapping` 覆盖，不改计算逻辑。
- `pipeline.py`：读取 → 校验 → 版本归并 → 统计 → 输出。
- `samples/`：5 张**自造虚构 CSV**（课程要求/题目与材料/模拟学生/测验节点/作答记录），仅验证逻辑，不是官方数据。

命令：
```bash
cd D:/Document/Langchain-RAG-smart-study-partner
# 自造样例自测（含断言）
D:/Document/langchain-rag-venv/Scripts/python.exe competition_pipeline/pipeline.py self-test
# 官方数据到位后：
D:/Document/langchain-rag-venv/Scripts/python.exe competition_pipeline/pipeline.py run \
  --data-dir <官方CSV目录> --out-dir D:/Document/langchain-rag-work/competition-data
```

输出（本地，不在 Git）：`D:/Document/langchain-rag-work/competition-data/{class_analysis.json,evidence_index.json,curriculum.json,data_quality.csv,data_summary.json}`。

**self-test 实测结果（独立核算口径）**：
| 核算组 | 结果 | 口径 |
|---|---|---|
| 原始行数 → 归并事件数 | 12 → 9 | EV001/EV002 旧版本被最大版本覆盖；EV003 完全重复去重 1 条 |
| DC01 / N01 / IND-A | 7/12，0.5833，3 条有效 | 得分和÷满分和，只取已评分有效事件 |
| DC02 / N01 / IND-A | 0/12，真实零分 | 有效 0 分计入统计，区别于未测 |
| DC01 / N01 / IND-B | 1 条有效 → 记录不足；另有 1 待评分、1 未作答 | 状态分开，不把待评分/未作答当 0 分 |

### 9.1 页面 bundle 与数据源开关（已完成）

`competition_pipeline/bundle.py` 把上面的统计 reshap 成页面结构，写到
`frontend/public/assets/competition-data/*.json`（**必须放 assets 下**：后端只挂载 `/assets`）。
`frontend/src/competition/dataSource.ts` 提供运行时开关：

- 默认（无参数）= 演示样例，原有 22 项交互不变；
- URL 带 `?source=real` = 读取 bundle，用真实班级/测验/考点/答卷。

已接通的接手文件：`dataSource.ts`（开关）、`shared/ids.ts`（applyRealIds）、
`classAnalytics/data.ts`、`studentAnswers/data.ts`、`learningAdvice/data.ts`、
`learningAdvice/LearningAdvice.tsx`、两个 Page、`main.tsx`（启动时先加载数据源）。

真实模式下：
- 学习建议一律返回空，页面显示「暂无学习建议（正式推荐尚未接入）」——**不把虚构建议挂到真实学生身上**；
- 缺原始步骤 / 官方评分点时，页面明确写「原始步骤未提供」「官方评分点未提供」，不虚构；
- 答卷顶部显示来源记录号、修订版本、被覆盖的旧版本。

**真实链路验收**（`cd frontend && node e2e-competition-real.cjs`）：12/12 PASS，
含真实两班统计（DC01 IND-A=58% 即 7/12、DC02 真实零分）→ 点考点 → 真实部分得分答卷
（ITEM-A2 2/4，来源 DR0004）→ 题干/步骤/评分点/来源记录齐全 → 暂无学习建议 → 未挂演示建议。
截图：`D:/Document/langchain-rag-work/competition-shots/{real-class-analytics,real-student-answers}.png`。
回归：`node e2e-competition.cjs` 22/22 PASS，无 JS 错误。

### 9.2 自造样例阶段记录（已被第十节官方数据取代，仅留档）

原先 `?source=real` 读 `public/assets/competition-data/` 的自造样例 bundle；现该目录仍是**演示测试用静态目录**，
`source=sample`。官方数据改走第十节的受控接口，不再放进公开静态资源。

## 十、官方赛题数据接入（2026-11-20 完成）

### 10.1 资料位置（本地私有，不在 Git）

- 用户给的 `D:/Download/【赛题材料】智慧学伴.zip` 实际不存在；真正文件在微信接收目录：
  `D:/APP_DATA/xwechat_files/wxid_3gmpyb8tnnie22_ab31/msg/file/2026-11/【赛题材料】智慧学伴.zip`（70,418,528 字节）。
- 已解压到仓库外：`D:/Document/langchain-rag-work/official-competition-data/`。
- 5 张 CSV + 字段说明：`.../赛题数据/赛题资料包/4_赛题数据/模拟学情数据/`。
- 官方 bundle（含答卷）写到仓库外私有目录：`D:/Document/langchain-rag-work/competition-data/official-bundle/`；
  页面测试时经后端受控接口读取，**不打包进 `frontend/public`**。

### 10.2 官方字段与解析器调整（只改发现的具体差异）

`field_contract.py` 按官方《字段说明.csv》补齐别名：`材料编号`/`题干或内容`/`评分点`/`模拟班级编号`/
`已教及测量主题`/`可用题目编号`/`作答事件编号`/`修订版本`/`评分状态`/`解答过程`/`评分点得分` 等；
自造样例的英文表头别名保留（两套都兼容）。官方无「学生姓名/节点名称/课程要求.node_ids」列 → 标为可选，
不再报致命错误。节点覆盖改为按官方「可用题目编号」推指标（`pipeline._node_indicators`）。
`bundle.py` 用同一别名表读 CSV；评分点按官方 `Q015P1=1；Q015P2=0` 格式 + 题目台账
`Q015P1 正确去括号 1分` 解析出标签/满分/得分（自造样例 `xx2分` 格式仍兼容）。
计算逻辑（版本归并、状态分开、得分率口径）未改。

### 10.3 官方导入结果（与基准完全一致）

命令：
```bash
cd D:/Document/Langchain-RAG-smart-study-partner
D:/Document/langchain-rag-venv/Scripts/python.exe competition_pipeline/pipeline.py run \
  --data-dir "D:/Document/langchain-rag-work/official-competition-data/赛题数据/赛题资料包/4_赛题数据/模拟学情数据" \
  --out-dir D:/Document/langchain-rag-work/competition-data/official
```

| 项 | 基准 | 实测 | 结论 |
|---|---|---|---|
| 原始作答行 | 806 | 806 | 一致 |
| 合并修订后事件 | 799 | 799 | 一致（7 个事件有修订版本 2） |
| 已评分 / 待评分 / 未作答 | 790 / 4 / 5 | 790 / 4 / 5 | 一致 |
| DC01 / DC02 人数 | 21 / 19 | 21 / 19 | 一致 |
| 课程指标 / 题目 / 节点 | 9 / 54(+11 材料) / 2 | 9 / 65 / 2 | 一致 |

质量报告 `official/data_quality.csv`：4 条——2 条「可选字段缺失」（官方无姓名/节点名称，非错误），
2 条官方原始数据分数越界：`DR0361`（得分 10、满分 1）与 `DR0535`（得分 4、满分 3）。
这两条**保留在状态计数**（故 790 不变），但标记 `quarantined` 并**排除得分聚合**，避免污染统计。
属资料本身问题，不是解析错误；若官方换包需复核。

### 10.4 独立复算（不调用 pipeline，直接读官方 CSV）

链路：**N01 / T01I2（识别同类项、去括号并化简）**，两班对比：
- DC01：得分和 **69**、满分和 **101**、有效作答数 **67**、不同题目 10、有记录 21/21 人、得分率 **0.6832**；
- DC02：得分和 **61**、满分和 **93**、有效作答数 **65**、不同题目 10、有记录 19/19 人、得分率 **0.6559**。

口径：按 `作答事件编号` 保留最大 `修订版本` → 取 `评分状态=已评分` 且分数有效 → 按 `模拟学生.模拟班级编号`
与 `题目与材料.指标编号` 归组 → 得分和÷满分和。所用记录号样例：
DC01 `DR0001, DR0025, DR0030, DR0032, DR0051, DR0052…`；DC02 `DR0436, DR0438, DR0439, DR0440, DR0445, DR0462…`。
独立脚本输出与 `official/class_analysis.json` 完全一致。

### 10.5 官方答卷链路（已跑通）

- 考点：N01 / T01I2；官方部分得分答卷：**D01 / Q015 / 记录 DR0001 / 版本 1，得分 2/3**。
- 题干：`化简：3(2a-b)-2(a+2b)。`
- 原始步骤：`1. 原式=6a-3b-2a-4b。` / `2. =4a-7b。`
- 官方评分点：Q015P1 正确去两个括号 1/1、Q015P2 分别合并 a 项和 b 项 1/1、
  Q015P3 写出正确结果 0/1 —— 加总 2/3，与总分一致。
- 页面显示「暂无学习建议」，未挂任何演示建议；答卷顶部显示来源记录 DR0001。

### 10.6 受控接口（官方数据不进公开静态资源）

- 后端新增 `api/routes/competition.py`：`GET /api/competition/official-bundle`，
  依赖 `require_teacher`（`X-User-Token`），只放行固定 8 个 JSON 白名单，目录由环境变量
  `COMPETITION_OFFICIAL_BUNDLE_DIR` 指定（未配置返回 404，不回退公开目录）。已注册到 `api/app.py`。
- 前端 `dataSource.ts`：`?source=official` 走该接口并带 token；`?source=real` 仍是公开静态样例；无参数仍演示。
  官方 bundle 的考点按 quizId 过滤（`classAnalytics/data.ts` 的 `listKnowledgePoints(topicId, quizId)`），
  页面测验选择器现在真正切换考点；`goStudent` 传 `indicatorId` 保证题目定位正确。
- 启动后端时需加：
  `COMPETITION_OFFICIAL_BUNDLE_DIR=D:/Document/langchain-rag-work/competition-data/official-bundle`。

### 10.7 验收

- `node e2e-competition-official.cjs`：**14/14 PASS**（官方标签、两班 68%/66%、点考点、
  题干/步骤/2-3/评分点/来源 DR0001、暂无学习建议、官方数据未进静态资源、无 JS 错误）。
  截图：`D:/Document/langchain-rag-work/competition-shots/{official-class-analytics,official-student-answers}.png`。
- 回归：`node e2e-competition.cjs` 演示 **22/22 PASS**；`node e2e-competition-real.cjs` 样例 **11/11 PASS**；
  `tsc --noEmit`、`vite build` 通过。

### 10.8 下一步先做什么、从哪个文件接着改（Qoder 接手口）

1. **课标定位入口**：`frontend/src/competition/curriculum/data.ts` 的
   `getCurriculum/getSourceByQuestion`。官方材料在
   `.../赛题资料包/5_课程标准及相关教学资源/`（B01—B08 切片 PDF + 课程标准_2022.pdf），
   课程要求表已有「课标整理编号 / 课标文件页／正文页 / 教材正文页」。
   真实模式下需先判断 `isRealData()`，用官方字段替换 `CURRICULUM_STANDARDS` 与 `QUESTION_SOURCES`；
   `curriculum.json`（pipeline 输出）已含这些字段，可直接作为数据源。
2. **练习建议入口**：`frontend/src/competition/learningAdvice/data.ts` 的
   `getSuggestion/getFeedback`。官方学习卡 L01—L03 在「题目与材料」表（材料类型=学习卡），
   教材切片 B01—B08 可用；当前真实模式一律返回空并显示「暂无学习建议」。
   接正式推荐时保留 `isRealData()` 分支，**不要**把 `PRACTICE_SUGGESTIONS` 的虚构建议挂到真实学生。
3. 若要换官方数据包：重跑 `pipeline.py run` → `bundle.py build --source official` 到私有目录 →
   无需重新 `vite build`（bundle 由后端运行时读取），刷新 `?source=official` 即可。

所有改动只在本 worktree，未提交、未推送；master 未动。

## 十一、本轮开发（2026-11-20 15:25 起）

### 11.1 开源调研（各记一条，已落到代码）

| 项目 | 具体页面/源码 | 借鉴设计 | 落点 |
|---|---|---|---|
| Kolibri | `kolibri/plugins/coach/frontend/views/common/reports/QuestionLearnersPage.vue` | `beforeRouteEnter` 保存 `prevRoute`，`navigate()` 始终带回 `classId/quizId/lessonId/groupId`；教师从难题看多个学生、多次作答后返回原报告 | 班级页下钻答卷保留 `quizId/classId/kpId`，答卷返回班级页恢复筛选（已有，本轮保持并补证据面板） |
| Moodle | `mod/quiz/report/statistics/report.php` | 同 `qubaid_condition->get_hash_code()` 驱动汇总与逐题统计，显式呈现 `s()` 有效尝试数、缓存新鲜度与新增记录数 | 证据面板显示得分和/满分和、有效作答数、有记录人数、参与题数、待评分/未作答/隔离数，并写清口径 |
| PrairieLearn | `docs/question/overview.md`、`docs/elements/index.md` | `partialCredit` 默认开启；`pl-question-panel` / `pl-submission-panel` / `pl-answer-panel` 分面板，答案与反馈分离 | 答卷页把官方评分点、原始步骤、最终填答分开展示；事实/推测/复查分离；不编造原因 |
| Khan Academy Exercises | `khan-exercise.js` | `#hint` 一次一条、`hintShown` 事件驱动下一条、`#issue-show-answer` 与提示分离 | 学习建议「分步提示（一次一条）」，下一条按钮 + 从头开始；答案/解析不入提示 |

未引入第三方运行时代码，只借鉴交互与版式思路，无需新增许可证归属。

### 11.2 教材与课标定位（已核对）

- 从官方《字段说明》《课程要求》整理 3 主题 / 9 指标，含课标整理编号、课标文件页／正文页、教材正文页范围。
- 用 pypdfium2 逐页核对：`B05` 第31页=去括号与合并同类项（印刷页上册98）、第33页=例7；`B06` 第19页=解一元一次方程、第44/48页=代入/加减消元；`B02` 第4页=不等式性质、第19页=不等式组。
- 未逐页视觉核对的（如课标 PDF 62/63 页为扫描无文本）标为“范围/待定位”，不臆造页码。
- 教材抽屉改为支持官方切片：受控接口 `GET /api/competition/official-pdf/{B01..B08|CR}`，教师鉴权 + 白名单；页面展示 PDF 文件页、教材印刷页、课标页三者分开。

### 11.3 证据化诊断与三类建议（已跑通）

- 新增 `competition_pipeline/analysis.py`：从官方 CSV + pipeline 输出生成 `curriculum_map.json`、`diagnoses.json`（364 条）、`case_book.json`（三类）、`recommendations.csv`。
- 三类案例：D01/N01/T01I2 部分得分 2/3（锚定 DR0001）、D15/N02/T03I2 表现较好 6/6（4 条）、D17 记录不足（N02/T03 无已评分记录）。D15 与开发方案的 12/12 不同（本包 8 条 T03、6/6 在 T03I2），按实际记录选人并已在案例册写明理由。
- D01 官方『解答过程』末行 4a−7b、『学生最终作答』4a+7b：页面突出此不一致，保留官方 2/3；原因只写“需核实”，不臆断。补练对准 Q015P3 符号核对（L01 + B05 第31页）。
- 建议分「观察到的事实 / 推测（需核实）/ 复查条件」三层；每条带 evidence_ids 与 material_id。

### 11.4 教师调整与复查（已跑通）

- 答卷右侧「教师调整」：候选替换 / 改为先补测，前后并排，单独记录到 localStorage（`competition.teacher.adjustments.v1`），可恢复系统建议；不改原始答卷与官方统计。
- 分步提示一次一条（借鉴 Khan），学生预览只显示本人内容。

### 11.5 本阶段验收

- `node e2e-competition-official.cjs` **21/21 PASS**（1366×768）：证据面板、过程/填答差异、受控教材 PDF、三类案例、教师调整与恢复、官方数据未进静态资源、无 JS 错误。
- 回归：演示 22/22、自造样例 11/11；`tsc --noEmit`、`vite build` 通过。

### 11.6 下一步

导出成果包（JSON/CSV）并页面—导出交叉核对；自造变体重跑；1366×768 视觉走查；写两条演示路线与录屏/PDF 证据清单。

### 11.7 成果包与变体重跑（已完成）

- 导出脚本 `competition_pipeline/export_package.py`，输出目录
  `D:/Document/langchain-rag-work/competition-data/deliverable/`（仓库外）：
  `curriculum_map.json`、`class_analysis.json`、`student_analysis.json`、
  `study_recommendations.json`、`evidence_index.json`、`data_summary.json`、
  `field_dictionary.json`、`data_quality.csv`、`recommendations.csv`、`crosscheck.json`。
- `export_package.py verify`：页面—导出交叉核对 DC01/T01I2 与 DC02/T01I2 的
  score/max_score/score_rate 全部 PASS。
- 变体：`competition_pipeline/make_variant.py` 把班级改 XC01/XC02、学生 Dnn→Vnn、
  修订版本整体上移 10，重跑后仍是 806→799、790/4/5、21/19 人；案例册
  D01→V01、D15→V15、D17→V17 三类齐全。**这是自造工程验证，不是官方独立评测数据。**

### 11.8 最终验收（1366×768）

| 套件 | 命令 | 结果 |
|---|---|---|
| 演示模式 | `node e2e-competition.cjs` | 22/22 PASS |
| 自造样例 | `node e2e-competition-real.cjs` | 11/11 PASS |
| 官方链路 | `node e2e-competition-official.cjs` | 21/21 PASS |
| 两条演示路线 | `node e2e-competition-demo-routes.cjs` | 10/10 PASS |

`tsc --noEmit` 通过；`vite build` 通过（仅 chunk 体积警告）；无 JS 运行时错误。
新增补测已覆盖：教材实际打开 B05 第31页受控 PDF、三类案例不同、教师调整可恢复、
无记录未显示成零分、演示/官方数据不串用、关闭教材保持原位置。

### 11.9 两条可直接演示路线（给评委现场操作）

**路线 A · 完整主案例（约 2 分钟）**
1. 打开 `http://127.0.0.1:8000/class-analytics?source=official` → 教师登录。
2. 在 N01 选 T01I2，点 DC01 的 68% 格子 → 右侧证据面板显示 69/101、67 条有效、21/21 人、参与题目。
3. 点失分题 Q015 → D01（DR0001，2/3）→ 答卷页突出「过程末行 4a−7b / 填答 4a+7b」不一致，官方评分不变。
4. 点「打开教材 PDF 第31页」→ B05 受控切片，显示 PDF 第31页 / 印刷页上册98 / 课标 CR01；关闭保留原位置。
5. 右侧证据化诊断：事实/推测/复查三层 + 关键差异 → 点「教师调整」看前后并排 → 恢复系统建议。
截图：`competition-shots/route-A-{answer,textbook,adjust}.png`。

**路线 B · 对比学生与教师调整（约 2 分钟）**
1. 打开 D15/Q049（N02/T03I2）→ 表现较好 6/6，迁移变式建议。
2. 打开 D17/Q014（N01/T01I1）→ 记录不足 4/4 但仅 2 条有效，显示「先补测、≥3 条再判断」。
3. 对 D17 点「教师调整」→ 替换为候选材料 → 前后并排变化；点「预览学生反馈」只显示 D17 本人内容。
截图：`competition-shots/route-B-{good,insufficient,adjust}.png`。

### 11.10 5 分钟录屏提纲（≤5 分钟）

- 0:00–0:30 登录 → 班级页两班对比，说明 68%/66% 的计算依据（69÷101、61÷93）。
- 0:30–1:20 点证据面板：有效作答数、有记录人数、参与题目、待评分/未作答/隔离状态。
- 1:20–2:20 D01/Q015：过程与填答不一致、官方评分点、教材受控原页。
- 2:20–3:20 证据化诊断：事实/推测/复查 + L01 补练 + 分步提示一次一条。
- 3:20–4:20 教师调整：改为先补测/替换 → 前后并排 → 恢复 → 预览学生反馈。
- 4:20–5:00 D15 与 D17 对比，说明三类案例规则与「缺记录≠零分」。

### 11.11 设计方案 PDF（≤20 页）证据清单

1. 赛题三项任务对应表：班级证据 / 个体诊断 / 资源定位，每页配 route-A/B 截图。
2. 数据链：806→799、790/4/5、21/19；两条分数越界记录的质量报告截图。
3. 独立复算表：DC01 69/101/67、DC02 61/93/65，列记录号样例与口径。
4. 课标映射：9 指标 × 课标整理编号 × 教材切片 PDF 页/印刷页/课标页表。
5. 教材定位证据：B05 p31/p33、B06 p19/p44/p48、B02 p4/p19 的核对记录与抽屉截图。
6. 三案例卡：D01 部分得分、D15 表现较好、D17 记录不足（含 evidence_ids、material_id）。
7. 教师调整前后对照截图 + 恢复验证 + 学生预览截图。
8. 成果包目录树与 crosscheck.json 结果；字段字典与数据质量报告摘录。
9. 开源借鉴表（Kolibri/Moodle/PrairieLearn/Khan）与本作品落点，声明未引入第三方运行时代码。
10. 局限：未接模型改写（规则与模板可用）；回放样例自造、不代表教学成效；教材未逐页视觉核对的标待定位。

### 11.12 剩余问题与边界

- 模型改写学生反馈未启用（无稳定密钥/合规确认）；程序化诊断、资源选择、教师调整、导出全流程照常运行。
- 回放样例仅为流程演示，官方统计与回放保持独立，材料中不解读成教学成效。
- 官方包两条分数越界记录（DR0361/DR0535）保留在质量报告，待赛方确认是否换包。
- 课标 PDF 62/63 页为扫描无文本，按官方字段标注页码、未做视觉复核，页面标「待定位」。

本轮所有改动仅在比赛 worktree，未提交、未推送；master 未动。
## 十二、第五轮（Qoder 接手）：两个 P0 + 两个 P1 全部完成

> 本轮按「旧版怎么出错 → 新版怎么复现与通过 → 打开页面怎么验收」记录。
> 基线保持：806 行→799 事件、790/4/5、DC01=21/DC02=19、DR0361/DR0535 隔离保留计数。
> 五套浏览器验收 91 项全过；verify 4818 项全过；篡改测试 5/5 抓住。未提交、未推送。

### 12.1 P0-1 答卷定位跨测验不串

**旧版怎么出错**：`getAnswer` 只按学生+题号查找。官方数据有 37 组学生×题目跨节点重复
（D01/Q002：N01=DR0006 0/1，N02=DR0019 1/1），从 N02 进来可能拿到 N01 的答卷；
班级下钻 `listAnswersByQuestion` 不限测验/班级，会混入另一场、另一班；班级页不读 URL 参数，
从 N02 答卷返回会丢测验；诊断查找有跨节点回退，会拿另一场的诊断补上。

**新版怎么改**：
- `studentAnswers/data.ts`：`getAnswer(student, question, quizId?, recordId?)`、
  `listAnswersByQuestion(qid, {quizId, classId})`、`listStudentAnswers/listStudentEvents/getAnswerEvent`。
- `bundle.py` 新增 `events.json`（799 条全量事件，含待评分/未作答/隔离），受控接口白名单已加。
- 答卷页：测验选择器＋该生在所选测验的作答列表（含状态与记录号）；指定测验无记录时按
  待评分/未作答/无记录如实显示，并给「切换到 N02（DR0023）」入口，绝不跨测验替代；
  「过程末行 vs 最终填答」关键差异直接读结构化诊断的 question_findings。
- 班级页：挂载时从 URL 恢复 quizId/classId/kpId，筛选变化同步回 URL（保留 source）；
  下钻限定测验+班级；goStudent 带 recordId 与 source。
- LearningAdvice：严格节点匹配，禁止跨节点回退；无诊断时显示「该测验下暂无该考点的诊断」。

**复现与通过**：`node e2e-competition-p0.cjs` 前 11 项——N01/N02 各自记录号与分数正确、
N01 无 Q043 记录不拿 N02 补、下钻只出现 D01..D21、直接入口 N01=12 条/N02=10 条、
返回班级页真正恢复 N02 与 source。

**打开页面怎么验收**：`/class-analytics?source=official&quizId=N01` → 点 T01I1 的 DC01 单元格
→ Q002 → D01，答卷页显示 DR0006 0/1；把 URL 的 quizId 改成 N02 刷新 → DR0019 1/1。
直接进 `/student-answers?source=official` 选 D01，可切换测验看两场作答列表。

### 12.2 P0-2 诊断 / 案例册 / 导出同一份结论

**旧版怎么出错**：案例册覆盖 D01 的 case/resource（页面「部分得分·对照补练」，通用诊断与
CSV「记录不足·先收集证据」）；bundle 的 nextStep=「等待正式学习建议接入」与正式诊断冲突；
`export_package.py verify` 只核对班级汇总——把学生分数改成 999、材料编号改成无效值仍 exit 0。

**新版怎么改**：
- `analysis.py` 两层结构：`question_findings/question_case`（本题观察）与 `coverage/case`
  （该测验下该考点的证据覆盖）。主任务通用规则：有过程/填答不一致→先核对该题最后一行；
  覆盖不足→短测（给本节点可用、优先未做过的具体题号）；表现较好→迁移变式（优先未做过、
  题型有变化；用过的题如实标复练）；其余部分得分→对照失分评分点。不一致命中且覆盖不足时
  同时给 `secondary_actions`「补充观察」。**规则不依赖 D01/D15/D17 编号**。
- 案例册只选展示对象＋focus＋selection_reason，条目与诊断逐字段一致（verify 强制）。
- D01/N01/T01I2 现在：case=记录不足（1/3 条）、question_case=部分得分、
  resource=L01 对照练习/学习卡（核对 DR0001 最后一行）、补充观察题 Q003/Q004/Q005。
- bundle 答卷 diagnosis/nextStep 置空，页面不再渲染占位；D01 首屏=题干+得分+末行/填答并排
  +评分点（左）与主任务建议（右），完整证据/教材/复查收进 Collapse（forceRender）。
- 迁移反例修复：D15/N02/T03I2 的变式=Q045（未做过），不再推荐已做过且满分的 Q044。
- `verify` 重写：文件齐全/空数据、班级主键+自洽+与源一致、个体主键+证据可定位+归属正确
  +分数/分母/记录数按证据重算+rate+材料可定位+步骤/复查齐全+coverage 一致、案例册三类齐全
  +与个体诊断一致、CSV 逐行一致、crosscheck 双向。

**复现与通过**：verify **4818 PASS / 0 FAIL**；篡改副本 5/5 抓住（score→999、证据→EV-INVALID-999、
材料→Q999、删一个案例、诊断置空，均 exit 1 且 FAIL 行指出字段）。p0 第 12–17 项核对
页面=案例册=诊断=CSV、两层标签并存、无旧占位。

**打开页面怎么验收**：D01/Q015 页右侧同时有「本题：部分得分」与「考点覆盖：记录不足（1/3 条）」；
对照 `deliverable/recommendations.csv` 的 D01,N01,T01I2 行与 `crosscheck.json`，材料/分数/证据一致。

### 12.3 P1-3 教师调整 = 整套可执行任务

**旧版怎么出错**：换题只改 material_id/reason，步骤、提示、教材、复查条件沿用旧建议；
「先补测」只给考点编号；学生预览不同步。

**新版怎么改**：TeacherChoice v2 存整套 `ExecutableTask`（材料+类型+理由+步骤+复查），
localStorage key `competition.teacher.adjustments.v2`（形状校验，旧 v1 忽略）。替换候选标注
未做过/复练·已做过/已做过且满分；先补测用 `pretest_questions` 具体题号构造整套任务；
手动编号输入＋存在性校验；前后对照卡＋「本次改动」差异列表（材料/类型/步骤数/理由/复查）；
学生预览用生效任务；恢复=删除选择。教师选择不写回原始答卷、官方评分、班级统计。

**复现与通过**：p0 第 18–22 项——替换后当前方案与学生预览同步变化、恢复回原建议、
先补测显示 Q003/Q004/Q005、调整后重取受控接口核对班级统计与 D01/Q015 分数不变。

**打开页面怎么验收**：D01/Q015 → 教师调整 → 替换为 Q003 → 看「本次改动」与学生预览 → 恢复系统建议。

### 12.4 P1-4 证据回放（自造、隔离、可点击）

`competition_pipeline/replay.py`：官方 CSV 复制到隔离目录 → 追加记录 → **同一套正式
pipeline/analysis 重算** → 与 before 对比写 replay.json（bundle 附带、受控接口可选白名单）。
- 场景 1 `d15-q044-zero`：DR9001（D15-N02-Q044 v2，0/1，已评分）覆盖 DR0343 →
  T03I2 从 6/6（100%，表现较好，Q045 迁移）变 5/6（83.33%，部分得分，L03 对照）。
- 场景 2 `d17-fill-coverage`：DR9002（Q001 1/1）+DR9003（Q002 0/1）→
  T01I1 从 4/4（2 条，记录不足）变 5/6（4 条≥3，部分得分）。
界面：证据回放卡＋紫色「自造回放」标签；展开对照=变动记录表＋六项前后对照＋changes；
「恢复官方视图」收起。回放不写官方数据、不写教师选择。

**复现与通过**：p0 第 23–26 项（6/6→5/6、判定与建议变化、DR9001 可见、可恢复）。
**打开页面怎么验收**：D15/Q049（N02）页 → 证据回放 → 展开对照。

### 12.5 验收汇总与命令

```bash
cd D:/Document/Langchain-RAG-smart-study-partner
# 后端（托管 dist + 受控接口）
EDU_DATA_DIR=D:/Document/langchain-rag-work/demo_data \
TEACHER_USERS=demo_teacher,e2e_teacher,new_teacher_mushevth \
COMPETITION_OFFICIAL_BUNDLE_DIR=D:/Document/langchain-rag-work/competition-data/official-bundle \
D:/Document/langchain-rag-venv/Scripts/python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8000
# 前端
cd frontend && npx tsc --noEmit && npx eslint src/competition src/main.tsx && npx vite build
# 五套浏览器验收（1366×768，系统 Edge）
node e2e-competition.cjs            # 演示 22/22
node e2e-competition-real.cjs       # 自造样例 11/11
node e2e-competition-official.cjs   # 官方链路 21/21
node e2e-competition-demo-routes.cjs# 两条路线 10/10
node e2e-competition-p0.cjs         # 本轮反例 27/27
# 数据管线（可复跑）
OFF="D:/Document/langchain-rag-work/official-competition-data/赛题数据/赛题资料包/4_赛题数据/模拟学情数据"
OUT=D:/Document/langchain-rag-work/competition-data/official
D:/Document/langchain-rag-venv/Scripts/python.exe competition_pipeline/pipeline.py run --data-dir "$OFF" --out-dir "$OUT"
D:/Document/langchain-rag-venv/Scripts/python.exe competition_pipeline/analysis.py run --src "$OUT" --csv-dir "$OFF" --out "$OUT/analysis"
D:/Document/langchain-rag-venv/Scripts/python.exe competition_pipeline/replay.py run --csv-dir "$OFF" --before "$OUT/analysis" --work D:/Document/langchain-rag-work/competition-data/replay --bundle-out D:/Document/langchain-rag-work/competition-data/official-bundle
D:/Document/langchain-rag-venv/Scripts/python.exe competition_pipeline/bundle.py build --src "$OUT" --csv-dir "$OFF" --out D:/Document/langchain-rag-work/competition-data/official-bundle --source official
D:/Document/langchain-rag-venv/Scripts/python.exe competition_pipeline/export_package.py build --src "$OUT" --analysis "$OUT/analysis" --out D:/Document/langchain-rag-work/competition-data/deliverable
D:/Document/langchain-rag-venv/Scripts/python.exe competition_pipeline/export_package.py verify --pkg D:/Document/langchain-rag-work/competition-data/deliverable --src "$OUT"
```

结果：tsc 0 错；eslint 0 错 0 警；build 通过；五套 91 项全 PASS；verify 4818 PASS；
篡改 5/5 抓住（副本在 `competition-data/replay/tamper-*`，可复跑）。

### 12.6 关键结论的独立复算口径（原始记录 → 导出）

口径：按作答事件编号保留最大修订版本 → 只取已评分且未隔离 → 按学生/班级+节点+指标归组 → 得分和÷满分和。

| 结论 | 原始记录（官方 CSV） | 复算 |
|---|---|---|
| DC01/N01/T01I2 68.32% | DR0001、DR0025、DR0030… 共 67 条有效 | 69÷101，21/21 人有记录 |
| DC02/N01/T01I2 65.59% | DR0436、DR0438… 共 65 条有效 | 61÷93，19/19 人 |
| D01/Q015 2/3 | DR0001 v1：Q015P1=1、P2=1、P3=0 | 1+1+0=2；过程末行 4a-7b ≠ 填答 4a+7b |
| D01/Q002 跨节点 | N01=DR0006 0/1；N02=DR0019 1/1 | 两个独立事件，按节点各自定位 |
| D15/N02/T03I2 6/6 | DR0336(Q049 3/3)+DR0341(Q047 1/1)+DR0343(Q044 1/1)+DR0344(Q048 1/1) | 3+1+1+1=6 |
| 回放后 5/6（自造） | DR9001 v2 0/1 覆盖 DR0343 | 6-1=5，记录数仍 4 |
| D17/N01/T01I1 4/4 | DR0369(Q014 3/3)+DR0370(Q013 1/1) | 2 条 <3 → 记录不足 |
| 回放后 5/6（自造） | +DR9002(Q001 1/1)+DR9003(Q002 0/1) | 4 条 ≥3 → 部分得分 |

以上已用独立脚本直接读官方 CSV 复核，与 `official/class_analysis.json`、`diagnoses.json`、
`deliverable/crosscheck.json` 一致；页面展示值由 p0 套件逐项断言。

### 12.7 两条现场演示路线（更新版）

**路线 A · 主案例（约 2 分钟）**：班级页 N01 → T01I2 DC01 68% → 证据面板 69/101/67 条 →
Q015 → D01（DR0001 2/3，末行 4a-7b vs 填答 4a+7b 并排）→ 教材 B05 第31页（受控 PDF）→
右侧主任务「核对最后一行」+ 考点补充观察 → 教师调整替换/恢复。
**路线 B · 对比 + 回放（约 2 分钟）**：D15/Q049（N02）表现较好 6/6，迁移变式 Q045（未做过）→
证据回放展开：DR9001 使 6/6→5/6、表现较好→部分得分 → 恢复官方视图 →
D17/Q014（N01）记录不足 → 改为先补测（具体题号）→ 学生预览同步。

### 12.8 交付产物与截图

- 成果包：`D:/Document/langchain-rag-work/competition-data/deliverable/`（10 文件，verify 全过）。
- 回放隔离区：`D:/Document/langchain-rag-work/competition-data/replay/`（副本 CSV、重算输出、篡改副本）。
- 截图（1366×768）：`competition-shots/p0-main-case.png`（主案例首屏）、
  `deliver-teacher-adjust-before-after.png`（教师调整前后+本次改动）、
  `deliver-replay-before-after.png`（回放前后对照）、`official-evidence.png`（两班统计依据）。

### 12.9 本轮改动文件

- Python：`analysis.py`（两层诊断+通用规则+候选/补测预计算）、`bundle.py`（events.json、
  测验名/主题拆分修复、占位清除、replay 附带）、`export_package.py`（verify 重写）、
  `replay.py`（新增）。
- 前端：`dataSource.ts`（类型契约+events/replay 加载）、`studentAnswers/data.ts`（范围读取）、
  `StudentAnswersPage.tsx`（测验选择/状态显示/首屏重排）、`ClassAnalyticsPage.tsx`（URL 恢复/范围下钻）、
  `LearningAdvice.tsx`（两层展示/整套教师调整/回放卡）、`dataAccess.ts`、`TextbookDrawer.tsx`（依赖修复）。
- 后端：`api/routes/competition.py`（白名单加 events.json/replay.json，可选）。
- 验收：`e2e-competition-p0.cjs`（新增 27 项）、`shots-adjust-replay.cjs`（交付截图）。

### 12.10 仍待处理

- 4 条 event_count=0 的诊断（仅待评分/未作答/隔离记录，如 D16/N02/T03I2）evidence_ids 为空，
  verify 对此有专门放行分支（得分重算兜底）；页面显示 0/0 与覆盖说明，不当作零分掌握。
- 回放为自造工程验证；官方独立评测数据以赛方后续发放为准。
- 模型改写学生反馈未启用；程序化全流程不受影响。
- 课标 PDF 62/63 页扫描无文本，未视觉复核；DR0361/DR0535 越界待赛方确认。

本轮所有改动仅在比赛 worktree，未提交、未推送；master 未动。
## 十三、第六轮：五个边界修复 + 同题对比 + 现场补测（已验收，基线见 13.6）

> 复现→修改→结果→怎么验收。基线：806 行→799 事件；原始状态 795/6/5；纳入统计 787（隔离 2 + 超范围 1）；
> DC01=21/DC02=19。五套基线 91 项 + 本轮反例 28 项全过；verify 4859 PASS；8 篡改全抓住。

### 13.1 修复1 测验范围冲突（DR0515）
- 复现：N01 可用题不含 Q038，但 DR0515=D25/N01/Q038 1/1 被计入 DC02/N01/T03I1。
- 修改：`pipeline.py` 按节点「可用题目编号」校验，超范围事件标 `out_of_scope`，质量报告记「超出测验范围，待核」，
  保留原始记录但排除主统计与正式诊断有效证据；`data_summary.reconciliation` 分开对账
  （raw_rows/raw_status/merged/included/quarantined/out_of_scope）；`scope_index.json` 供 verify 独立重算。
  `bundle.py` 答卷排除隔离/超范围（answers 790→787），events 带 outOfScope，新增 anomalies.json。
- 结果：N01/T03I1 两班「本次未测」；DR0515 在班级页「待核异常」与 data_quality.csv 可查；
  答卷页 D25/Q038/N01 显示「超出所选测验的可用题范围，待核」。
- 验收：`/class-analytics?source=official&quizId=N01&classId=DC02&kpId=T03I1` 见 DR0515；r6 第 1–4 项。

### 13.2 修复2 历史推荐用了后续作答
- 复现：仅保留 N01 作答重算，154 条 N01 诊断中 27 条补测候选、4 条主推荐材料变化（旧口径用全节点 done）。
- 修改：`analysis.py` done/done_full/候选题/迁移选题改为「截至所选测验」（节点按日期升序截断）。
- 结果：`check_asof.py` 仅 N01 重算与全量 N01 子集差异 0；D12/N01/T01I2 主推荐=Q003、补测=[Q003,Q008,Q012]；
  反例 Q003（D12 仅 N02 做过）在 N01 标「未做过」。
- 验收：r6 第 5 项；复跑 `python competition_pipeline/check_asof.py`。

### 13.3 修复3 本题标签串考点汇总
- 复现：D01/N01/Q013 满分 1/1，右栏曾显示同考点其他题的「本题：部分得分」。
- 修改：`LearningAdvice.tsx` 本题判断/解释/引用评分点改按当前 questionId+nodeId 的作答事件生成
  （getAnswer 严格节点）；考点覆盖独立显示；Collapse 关键差异按当前题过滤。
- 验收：r6 第 6–7 项（Q013 满分标签+「本题满分、无失分评分点」；Q014 部分得分+当前评分点）。

### 13.4 修复4 零有效记录页看不到任务
- 复现：D20/N01/T01I3 待评分、D16/N02/Q044 隔离，旧版只渲染状态提示。
- 修改：`StudentAnswersPage.tsx` 无已评分作答时右栏仍渲染 LearningAdvice（诊断+补测任务）；
  状态区分 未测/无记录/待评分/未作答/隔离/超范围/有效零分；得分率 null 显示「—」。
- 验收：r6 第 8–10 项（D20 见 待评分+0/3+得分率—+Q011；D16 见 隔离+Q045；D01/Q002 有效零分≠未作答）。

### 13.5 修复5 导出校验漏查缺失行
- 复现：删班级汇总行/删个体诊断+CSV 行/删 crosscheck 条目，旧 verify 仍 exit 0。
- 修改：`export_package.py` 以 `src/scope_index.json` 为可信期望主键集合，逐项比对班级/个体/CSV/crosscheck；
  crosscheck 扩为全量班级行+案例；新增 same_question_comparison.json 独立重算核对；reconciliation 核对。
- 结果：verify 4859 PASS；8 篡改（原 5 + 删班级 f/删个体+CSV g/删 crosscheck h）全 exit 1 并指出缺失编号
  （日志 replay/tamper2-*.log）。

### 13.6 升级一 两班同题对比（N02/T03I1 验收值）
- 各班全部有效作答：DC01 6 题 33/57=57.89% 37 事件；DC02 7 题 25/47=53.19% 31 事件。
- 仅共同 6 题（Q037,Q039–Q043）：DC01 33/57=57.89% 37；DC02 20/41=48.78% 25；仅 DC02 有 Q038。
- 修改：`bundle.py` stats.itemBreakdown；`ClassAnalyticsPage.tsx` 口径切换+两口径面板+差集题号+
  「共同题目对照只统一题目范围，学生覆盖与作答分布仍可能不同」+无共同题「无法同题比较」+待核异常+对账摘要。
- 导出：same_question_comparison.json 保留两口径与题号，verify 独立重算。
- 验收：r6 第 11–17 项；路线见 13.8。

### 13.7 升级二 现场补测（整式化简两步观察）
- 修改：新增 `livePretest/expr.ts`（受限线性表达式解析/等价比较，支持交换项序、隐式乘号、括号分配；
  变量×变量/除变量/非法字符→null 待人工核对）与 `livePretest/LivePretest.tsx`。
- 选题规则：同指标、化简含括号、参考解析可解析为「去括号后」与「最终结果」两个等价但写法不同的式子；
  优先截至本测验未做过。D01/N01/T01I2 选中 Q009（6x-2-x-4 → 5x-6）。
- 三场景+正确：中间错→去括号/符号提示与专项；中间对填答错→结果核对提示与专项；无法解析→待人工核对可重输/教师核对；
  全对→确认表现但保留覆盖判断。每次显示新增证据与支持；现场作答单独保存（localStorage v1）可清除重来；
  官方答卷/评分/统计不变（r6 第 24 项重取接口核对）。
- 复用：D12/N01/T01I2 同逻辑判定正确（r6 第 25 项）。
- 验收：r6 第 18–25 项；解析器单元自检 14/14（expr-test.cjs）。

### 13.8 两条可复现路线（R6）
1. 同题对比：首页 → 测验 N02 → 点 T03I1 的 DC01 格 → 面板口径一 57.89%(33/57) → 点 DC02 格 53.19%(25/47)
   → 切「仅比较共同题目」→ DC02 48.78%(20/41)、仅一边 Q038 → 对照导出 same_question_comparison.json。
2. 现场补测：首页 → D01/Q015（N01）→ 看过程末行 4a-7b vs 填答 4a+7b → 开始补测（Q009）→
   提交中间错/填答错/无法解析/全对四种 → 看提示、新增证据、后续安排变化 → 清除现场作答。

### 13.9 基线快照（R6 验收通过时）
-  suites：demo 22/22、real 11/11、official 21/21、demo-routes 10/10、p0 27/27、r6 28/28。
-  verify 4859 PASS / 0 FAIL；篡改 8/8 exit 1。
-  对账：806/799/795-6-5/787/2/1。
-  产物：official-bundle（answers 787、events 799、anomalies 3、review_plans）、deliverable（11 文件含 same_question_comparison）。
-  截图：r6-same-question.png、r6-live-pretest.png。

### 13.10 仍待处理
- 超范围/隔离记录待赛方确认是否换包；课标扫描页未视觉复核；模型润色未启用。
- 现场补测首版仅整式化简线性表达式；非线性/分式待人工核对。
## 十四、第七轮：班级讲评方案生成器（首版 N01/DC01/T01，已验收）

> 在 R6 基线（13.9）之上直接开发。有效作答/本题判断/考点覆盖/候选题/补测结果均复用既有结构，
> 保留题号、评分点与证据编号；官方答卷、评分、班级统计全程不被会话操作改写。

### 14.1 两套方案 × 预算（review_plan.py）
- `common 优先讲共性失分`：按「可定位失分点数 → 失分人数」排序选讲评题；另附证据不足学生的补测观察任务（不占讲评预算）。
- `pretest 优先给证据不足学生补测`：预算全部用于补测观察题，按「需该题观察的学生数」排序。
- 每套给出：具体题号、安排学生（可点进答卷）、覆盖学生数/已定位失分点、未覆盖失分点与未安排补测学生及原因。
- N01/DC01/T01 预算 3：common 讲评 Q014(8人)/Q018(7人)/Q006(4人)，补测 Q001/Q002/Q003；覆盖 19 人、定位 44 点、未覆盖 53 点。
  pretest 补测 Q001(10人)/Q002(9人)/Q003(8人)。

### 14.2 预算 3→2
- 重算显示：删 Q006；受影响学生 D02（其失分仅由 Q006 覆盖）；保留 Q014（定位 22 点/8 人）、Q018（14 点/7 人）及理由。
- 页面：班级页讲评面板预算选择 2/3，Alert 展示删题/影响/保留理由（`budget_diff_3_to_2`）。

### 14.3 分组→D01→现场补测→方案变化→会话应用
- 面板学生芯片点 D01 → 答卷页（N01 官方答卷+评分点）→ Q015 页开始补测（Q009 两步）→ 提交。
- 「用现场证据重算」→ POST `/api/competition/review-plan/recompute`（教师鉴权）：
  会话现场证据注入方案重算（correct 计覆盖证据、mid_wrong/final_wrong 计现场失分点、manual 不进入），
  返回 base/new/changes（讲评题增删、补测任务增删、覆盖学生增删、定位/未覆盖失分点数、覆盖证据 n→m）。
- 「确认应用到当前演示会话」→ localStorage 快照+徽标；「清除会话应用」恢复；官方 bundle 重取核对不变。
- 自造回放（replay）保留为另一演示入口，与现场补测互不干扰。

### 14.4 可复算对照（按失分人数 vs 按讲评方案）
- `review_plans.json.comparison[budget]`：naive（只按失分人数、不定位评分点、不附补测）vs common。
  预算 3：naive 讲评 Q014,Q018,Q002、补测 0；common 讲评 Q014,Q018,Q006、补测 3；取舍文案写明
  「naive 覆盖最多共性失分学生但不保证评分点依据与覆盖不足学生观察；common 换可讲评依据+后续证据」。
- 独立复算：`check_review_plan.py` 不依赖排序函数、直接从 evidence+scope+curriculum 重算选题/覆盖/定位，
  与 review_plans.json 比对 **10/10 PASS**。

### 14.5 验收与路线
- R7 走查 `node e2e-competition-r7.cjs` **14/14 PASS**（面板默认/预算2删题影响/pretest 学生/对照表/
  分组点 D01/评分点/现场中间错/重算差异/会话应用/官方不变/清除/无 JS 错误）。
- 最终七套：demo 22、real 11、official 21、demo-routes 10、p0 27、r6 28、r7 14 = 133 项全过。
- 完整路线：首页 → N01/DC01 → 讲评面板选 common/3 → 调 2 看删 Q006 影响 D02 → 切 pretest 看补测学生 →
  点 D01 芯片看官方答卷评分点 → Q015 现场补测提交 → 回面板「用现场证据重算」看差异 → 确认应用 → 清除。
- 截图：r7-plan-budget2.png（删题影响一屏）、r7-plan-pretest.png、r7-recompute.png（新旧差异一屏）。

### 14.6 仍待处理
- 首版仅 N01/DC01/T01；其他班/测验/主题需扩展 generate 参数与面板范围提示。
- 会话应用仅存 localStorage（演示会话级），不落官方产物；跨设备不共享。
- 现场补测仅整式化简线性表达式；非线性待人工核对。

## 十五、正式录屏前定向验收（2026-11-20）

### 15.1 预算口径必须先说明

共性讲评策略中的预算仅约束讲评题；额外分组补测单独计数，不宣传为同一个总题数预算。

| 策略/预算 | 讲评题 | 补测题 | 去重题目数 | 讲评分配 | 补测分配 | 总任务份数 |
|---|---:|---:|---:|---:|---:|---:|
| 共性讲评/3 | 3 | 3 | 6 | 19 | 27 | 46 |
| 共性讲评/2 | 2 | 3 | 5 | 15 | 27 | 42 |
| 补测优先/2 | 0 | 2 | 2 | 0 | 19 | 19 |

补测题组 Q001：10人×1题=10份；Q002：9人×1题=9份；Q003：8人×1题=8份。
名单可重叠，总27人题不是27名学生。D01实际清单为讲评Q014/Q018、补测Q003，共3份。
`review_plan.make_plan` 新增 workload（每组/每生分配）与 coverage_audit（观察项分母）。
页面预算旁注明“不含补测”；展开“每名学生的实际任务清单”可逐人核对。

### 15.2 复现53→54，不把证据更新说成任务调整

D01/N01/Q009现场提交 `6x-2-x+4`、最终 `5x-6`，产生一条 LIVE-MID 观察。
- 观察项总数（分母）：97→98；已覆盖：44→44；未覆盖：53→54；现场新增：0→1。
- 具体新增项显示学生D01、题号Q009、LIVE-MID、标签与独立LIVE-证据编号。
- `compare_plans` 逐学生×题目×任务类型计算：新增0、移除0、保留46份。走查另对比完整讲评题/补测任务对象，内容相等。
- 本例学生任务**未实际改变**；不是得分下降，也不是教学效果下降。页面明确显示这个限制。
- 旧版“确认应用”仅亮徽标、上方未使用应用对象；现已修复，确认后观察项显示98、任务仍46份，清除后恢复97。

### 15.3 临时换D08的三阶段输入实测

旧版只有展开式与最终结果，不能区分合并计算错误和最后填答不一致。
现增加可选“合并后过程末行”，保留两个输入时的诚实边界；同一受限解析器比较每栏等价性。

| 去括号式 | 合并后过程末行 | 最终填答 | D08/N01/Q009实际判定 |
|---|---|---|---|
| 6x-2-x+4 | 5x-6 | 5x-6 | 去括号阶段差异 |
| 6x-2-x-4 | 5x+6 | 5x+6 | 合并同类项阶段首次不等价 |
| 6x-2-x-4 | 5x-6 | 5x+6 | 前两步等价、最终填答与过程不一致 |
| 6x-2-x-4 | 空 | 5x+6 | 暂不能区分合并错误和填答不一致 |
| 6x-2-x-4 | x*x | 5x-6 | 待人工核对 |
| 6x-2-x-4 | 5x-6 | -6+5x | 三处输入均等价，原覆盖判断保留 |

只定位输入首次不等价的阶段，不断定“粗心”等原因，不将等价核验表述为完整步骤证明或稳定掌握。
不提前展示参考答案；解析长度上限512字符、256词元，非有限数值/非线性/除变量不参与自动判断。
快速换学生再点题暴露了旧版URL与local state竞争：现以URL为学生/测验/题目的唯一来源，列表跳转带完整事件身份，D08实测通过。

### 15.4 本轮走查与交付

从班级页N01/DC01出发：核对3+3与分组任务量 → 改讲评预算2 → 切补测优先2 → 恢复共性3 →
点D01学生分组查官方记录 → D01/Q015开启Q009补测并提交错展开式 → 返回班级重算 →
看97→98、44不变、53→54、0增0删46保留 → 应用/清除 → 现场换D08 → 提交三种差异与边界输入 → 清除现场记录。

- 定向浏览器走查18项通过（Vite开发模式及最新构建各走通）；脚本 `frontend/e2e-competition-recording.cjs`，认证只读本地环境变量。
- Python预算与观察项/任务身份测试15项通过：`python -m unittest discover -s competition_pipeline -p test_review_recording.py -v`。
- 表达式及边界测试15项通过：`node frontend/test-recording-expressions.cjs`。
- 完整 `npm run lint`、`npm run build` 通过；成果包verify仍4859 PASS/0 FAIL。
- 回归初轮发生构建与浏览器同时运行导致旧动态资源失效，以及R7截图定位器仍指向旧Alert；保留initial日志并修复定位器。稳定构建重跑：22/22、11/11、21/21、10/10、27/27、28/28、14/14，七套共133项通过；加本轮18项定向走查共151项。输入提示移除参考答案后再跑定向/R6/R7，均通过。最终结果见验收目录 `regressions.json`。
- 官方答卷/事件/班级统计接口摘要SHA256前后一致：`39fa5c8f13a3f10bf2a8c869d6389f5ded4c2857deca0304bb0f6b3869f3d7fa`。

验收目录：`D:/Document/langchain-rag-work/competition-data/recording-acceptance-20261120/`。
- `RECORDING_WALKTHROUGH.md`：完整走查记录与可直接使用的录屏文案。
- `walkthrough.log` / `walkthrough.json`：逐项断言及前后对照结构。
- 三张最新版账号遮挡截图（1366×768）：`01-review-plan.png`、`02-live-pretest.png`、`03-recompute-diff.png`。
- `budget-tests.log` / `expression-tests.log` / `export-verify.log`：独立测试日志。

本轮使用独立端口8001与开发端口5174，未停止旧8000服务。开发命令：
`DSH_API_TARGET=http://127.0.0.1:8001 DSH_DEV_PORT=5174 npm --prefix frontend run dev -- --host 127.0.0.1 --strictPort`。
后端启动仍需原教师鉴权环境，使用 `--host 127.0.0.1 --port 8001`；环境凭据不写入新增脚本/走查记录。
设置 `RECORDING_USER`、`RECORDING_PASSWORD` 后运行 `node frontend/e2e-competition-recording.cjs`；
若验收构建则额外设 `RECORDING_BASE=http://127.0.0.1:8001`。官方材料及含作答产物仍在仓库外。

---

## 十六、第八轮收尾 + 第九轮「模拟评审、深度整改与最终交付」（2026-11-21，进行中）

### 16.1 接上的上一轮（Round 8）遗留现场

- Qoder 在最后一次走查（20:22）之后又改了 4 个文件（20:34–20:38）但未重建、未重跑：
  `StudentAnswersPage.tsx`、`LivePretest.tsx`、`ClassAnalyticsPage.tsx`、`ReviewPlanPanel.tsx`。
- 本轮接手后统一复验：补齐走查脚本与页面文案口径（「46 人题」「实际 / 预算」），
  把被移进折叠区的受影响学生标签放回讲评卡内（恢复「点学生进答卷」入口）。
- 最新稳定构建重跑七套回归：**22/22、11/11、21/21、10/10、27/27、28/28、14/14，共 133 项 0 失败**
  （日志 `acceptance-20261121/` 之前的 `recording-final-20261120/round8-*.log`）。
- 录屏彩排按旁白节奏 3:01（≤4:45），三张 1366×768 截图已遮挡账号。
  另实测 D08/Q009 三种输入的后续安排确实各不相同，清空合并末行明确「暂不能区分」。
- 上一轮未完成的交接文档回写由本节补上。

### 16.2 首轮「模拟评审」（独立、干净会话、普通入口）

三种角色互不通信，首轮未给代码 / 演示脚本 / 深链接 / 预期答案，全部自己实际打开浏览器并看图：

| 角色 | 实际看页面 | 截图 | 100 分制 | 官方四维 |
|---|---|---:|---:|---:|
| A 一线七年级数学老师 | 是 | 34 | **47** | 48 |
| B 视觉得分与交互评委 | 是 | 24 | **51** | 49 |
| C 技术与赛题评委 | 是（含自写独立复算） | 144 | **46** | 39 |
| 平均 | — | — | **48.0** | 45.3 |

完整报告 `acceptance-20261121/review-{A,B,C}/REVIEW.md`；汇总 `SIMULATION-REVIEW.md`。
三方共识扣分：①**普通入口默认是虚构演示样例**（官方 N01/DC01/D01/Q015/B05 完全看不到）；
②`.app-route{overflow:hidden}` 让答卷页下半 460px 永久不可达；
③没有三名代表性案例入口与讲评预算 / 补测输入；
④大创遗留「按住下拉进入我的主页」把老师带进 `/me` 空页、语音服务 8100 降级横幅暴露内部地址；
⑤演示建议「展开分步提示」直接给答案 x=3。
C 角另发现官方 **DR0133** 得分 3/3 但评分点之和=2、过程末行 4a−7b 与填答 4a+7b 矛盾。

### 16.3 提交要求与缺口（按赛题原文）

依据 `1_赛题规则.pdf`（三项任务、5 类提交资料、四维评分表、资料 80% + 答辩 20%）、
`2_参赛说明.docx`、`3_典型案例说明.docx`、队内 11/17 通知转述。
- ①设计方案 PDF ≤20 页、②任务成果包（JSON/CSV + 字段说明 + 输入输出样例 + 操作说明）、
  ③演示视频 MP4 ≤5 分钟、④分析脚本（可选，但须给可核对样例）、⑤现场答辩 PPT。
- 官方指定：三主题九指标、DC01(21)/DC02(19)、D01—D40、三名代表性案例（部分得分/表现较好/记录不足）、
  每例三段（答题分析 / 学情诊断 / 教学建议）、提示不直接给答案。
- 接手时 **①③⑤ 三类材料尚未生成**，②缺操作说明 / 输入输出样例 / 班级复算说明。

### 16.4 整改前基线（动代码前已冻结）

`acceptance-20261121/BASELINE-20261121.md` + `baseline/`（原图）与 `baseline/masked/`（账号遮挡）：
11 张真实截图（含 1100×720、放大 125%、教材抽屉）、`baseline-walk.json` 操作记录。
接手时 8000 服务已掉线，用当前源码重建 `vite build` 后重启，首页资源与 dist 完全一致，
未登录访问官方 bundle 返回 401 —— 基线证据来自与源码一致的构建。
独立复算（`independent-recompute.py`，不调用管线）：806 → 799（7 条旧版本）→ 790/4/5
→ 扣 2 条越界（DR0361、DR0535）与 1 条超范围（DR0515）= **787**；DC01 21 / DC02 19；
N01/T01I2 为 69/101（67 条）、61/93（65 条）。DR0081 是全表唯一主题字段冲突，按题目表指标归组正确。

### 16.5 本轮已落地并复测的改动

**数据管线（`competition_pipeline/`）**
- `analysis.py`：新增 `_official_requirements()` 直接读官方《课程要求》表，九指标补齐 CR01–CR03、
  课标文件页/正文页、教材范围；新增 `theme_name/theme_goal/locating_status`；补 5 个教材具体页，
  九指标全部有已核对具体页。`verify_textbook_pages.py` 对 13 页页脚印刷页码逐页回查，全部一致。
- `pipeline.py` / `field_contract.py`：质量报告补 7 条修订版本逐条记录、DR0081 主题冲突、
  3 条「得分与评分点之和不一致」（DR0133/0361/0535，保留官方得分、标注待确认）、
  三条口径汇总（明确区分原始 806 行 795/6/5 与归并后 799 事件 790/4/5）。`self-test` 通过。
- `export_package.py`：`verify` 增加 `data_quality.csv` 行数 / 逐行内容 / 异常编号集合比对。
  篡改检验从 6/7 提升到 **7/7 全部检出**（`tamper-results.json`）。
- `rebuild-final-data.sh` 已执行：备份 → pipeline → analysis → review_plan → bundle → 成果包 build/verify
  → 教材页回查。成果包 **4862 PASS / 0 FAIL**，16 个文件（12 结构化 + README、
  字段说明补充、输入输出样例、班级复算说明）。

**前端 P0（源码已改，待统一构建复验）**
- `dataSource.ts`：**默认官方赛题数据**；`?source=demo` 显式回到虚构演示样例，
  `?source=real` 保留自造样例；官方接口失败时给可见降级原因与重试。
- `App.tsx` + 新 `competition/shared/CompetitionChrome.tsx`：换掉大创 AppChrome，
  常显三页导航、数据来源标识、一键切换官方；`/me` `/settings` 回收到首页；
  移除语音服务降级横幅；登录页补产品定位与「请先登录」说明。
- `index.css`：新增 `.comp-*` 外壳与统一设计 token；**滚动容器改为 `.comp-main{overflow-y:auto}`**，
  不再裁切长页面；统一焦点环与 ≥12px 字号。
- `curriculum/CurriculumPage.tsx`：官方模式改为九指标视图（课标编号/页码、教材具体页、
  定位状态、关联题目与学习材料）；数据源标签随实际数据源变化；演示模式保留原结构供回归。
- 演示回归脚本显式加 `?source=demo`；截图路径误改已还原。
- `tsc --noEmit` 通过。

**交付生成器（仓库外 `acceptance-20261121/`，均已冒烟）**
- `build_design_pdf.py`：设计方案 PDF，16 页 / 126KB（≤20 页），数据全部从最终成果包读取。
- `build_defense_ppt.py`：答辩 PPT，13 页含逐页口播备注。
- `build_package_readme.py`：成果包 README / 字段说明补充 / 输入输出样例 / 班级复算说明。
- `record-frames.cjs` + `encode-video.py`：真实浏览器逐段截帧 → MP4（OpenCV mp4v），
  同时产出按时间分段的逐段操作与旁白稿；MP4 编码与中文 PDF 渲染能力已验证。

### 16.6 仍在进行

- 三个视觉整改子代理（F1 班级页 + 讲评方案 + 三名案例入口；F2 答卷页 + 建议 + 提示去答案；
  F3 现场补测单视区 + 输入差异高亮）按 `DESIGN-SPEC.md` 并行改各自文件，主代理统一构建。
- 待完成：统一 `tsc`/lint/build → 前后截图对比 → 8 套浏览器回归 → 换未参与开发的子代理复评
  → 出 MP4/PDF/PPT → 提交包解压自验收 → 最终验收报告与本节结论补写。

### 16.7 运行入口与演示步骤（当前版）

```
EDU_DATA_DIR=D:/Document/langchain-rag-work/demo_data
TEACHER_USERS=demo_teacher,e2e_teacher,new_teacher_mushevth
COMPETITION_OFFICIAL_BUNDLE_DIR=D:/Document/langchain-rag-work/competition-data/official-bundle
D:/Document/langchain-rag-venv/Scripts/python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8000
# 前端：cd frontend && npx vite build
```
普通入口 `http://127.0.0.1:8000/`，教师账号 `new_teacher_mushevth`（口令见本地口令记录，不写入本文档）
（凭据只在本地环境，不写入交付物）。登录后**默认就是官方赛题数据**。
演示主线：班级学情看两班差异 → 讲评预算 3→2（Q006 移除、D02 受影响、19→15、补测仍 27）
→ 点 D01 看 Q015 评分点与教材原页 → 三名代表性案例 → D08/Q009 同一填答的两种后续安排
→ 成果包复核。现场补测输入是演示证据，单独保存、录后清除。

### 16.8 实际遗留问题（如实）

- 课标切片为扫描页，CR01–CR03 的文件页/正文页来自官方《课程要求》表，未逐条视觉复核。
- 官方两条分数越界记录（DR0361、DR0535）与 DR0133 评分点矛盾待赛方确认，已隔离或标注但不改写官方得分。
- 演示模式（`?source=demo`）仍有切测验数据不变、班级标题写死等虚构样例简化，已明确标注「非赛题数据」，
  不作为正式展示路径。
- 现场补测是受限线性表达式等价核验，不代表完整数学步骤证明或稳定掌握；教师调整与回放样例为自造演示证据。
- 模型改写未启用（规则 / 模板流程完整）；不依据模拟数据证明真实教学成效。
- 改动全部留在比赛 worktree，**未提交、未推送**，master 未动；正式提交 / 发布由用户确认。

---

## 十七、R4：按备课顺序的顺序与密度改版（2026-11-21 深夜，未提交未推送）

> 基线 R3。目标：老师打开系统很快知道两班哪儿有差距、本班讲哪两题、点哪里看学生错在哪。
> 基线截图 `acceptance-final-20261121/shots-r4-before/`；详细清单 `acceptance-final-20261121/CHANGES-R4.md`。

### 17.1 首页两件东西
- 右区题单置顶：预算（不含补测）下拉贴标题直接改；题卡=题干+一句教学理由
  （「8 名学生在这题失分」）+「看失分步骤」逐生展开；「少讲一道会怎样」整块默认不显示，
  预算改 2 后题单旁出实际结果一句话+「看 4 份任务的去向」（前后条形 19→15/27 不变+逐人去向）
- 左区图表只画可比考点（真实零分照常 0%），未测/证据不足 4 项收进「查看其余考点（4）」；
  主题/口径/看明细表收成一行轻量入口
- 失分明细改右侧抽屉：点柱条立刻进视区，关闭焦点回原柱条（Enter 开/Esc 关）；返回班级时
  URL 的 kpId 不再自动弹抽屉（修 R4 盲测前自查发现的遮挡问题）
- 答卷页右栏=一句建议+一行依据+单按钮「现场补测」；删重复橙警告与双补测入口
- 补测弹窗补「系统按考点自动选了最适合两步观察的题（以弹层题号为准）」说明（R4 盲测发现记录会挂错题号）

### 17.2 复验与盲测
- 定向复验 23/23（退出码语义修复：FAIL→非零；D4 改为四名学生各自实际任务列表逐行验收）
- 八套回归 152/152（r7 新增去向展开断言；脚本断言同步到有意改动）
- 独立盲测（verify-agent-r4/REPORT.md）：10 秒首屏三问全部可答（差距/讲哪几道/依据入口）；
  完整任务 21 分钟走通（Q014+Q018、预算 2、D02 补测、带回方案后预算保留可见、撤销干净）
- 遗留：答卷页搜索框无即时反馈（只过滤下拉）；「现场补测」导航默认落案例首例而非受影响学生（见 REPORT.md）

### 17.3 发布前检修（2026-11-22 凌晨）
- **竞态修复**：方案主题快速切换/重算进行中切范围时，迟到的旧响应不再覆盖新方案
  （浏览器注入延迟实测：切换后迟到的 T02 重算卡曾被渲染进 T03 面板；修复后丢弃旧响应，面板保持当前范围）
- **慢加载白屏修复**：数据源就绪前 main.tsx 不挂载任何内容，慢网络下整页空白（4 秒延迟实测 body=0）；
  现在加载期显示「正在加载 智慧学伴…」，加载后正常挂载（复测含 pretest=1 冷启动直达补测）
- **登录过期**：无效 token 回到登录表单，无崩溃/undefined
- **干净启动验收**：全新 EDU_DATA_DIR + 全新账号注册/登录/官方 bundle 200（2.26MB）全通
- **数据管线复建**（独立目录 final-pipeline/）：806→799、790/4/5、隔离2+超范围1=787、DC01 21/DC02 19、
  69/101 与 61/93 全部与基线一致；verify 4862 PASS/0 FAIL；篡改两次（删文件/改分数）均非零退出码抓住
- **部署前提（如实）**：api/routes/competition.py 与 competition_pipeline 含本机绝对路径**默认值**
  （D:/Document/langchain-rag-work/...），新机器部署必须设 COMPETITION_OFFICIAL_BUNDLE_DIR/
  COMPETITION_OFFICIAL_SRC_DIR 等环境变量，未设时接口返回带配置指引的 404；前端源码无绝对路径
- **脚本纪律**：定向复验失败返回非零退出码；D4 用四名学生各自的实际任务列表逐行验收

### 17.4 推送前清理（2026-11-22）
- **凭据去明文**：7 个 e2e 脚本改为 `E2E_USER`/`E2E_PASS` 环境变量（缺失即退出，不用明文默认值）；
  本文档两处口令明文改为「见本地口令记录」。本地运行示例：
  `E2E_USER=new_teacher_mushevth E2E_PASS=<本地口令> node e2e-competition.cjs`
- **竞态与白屏修复**：见 17.3
- **逐文件提交白名单**：见仓库外 `acceptance-final-20261121/push-whitelist.txt`（65 个文件，逐文件 add，
  不用目录级 add；tmp 调试残留与 __pycache__ 明确排除）

