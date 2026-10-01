# 教学效果评测报告（自动生成）

- 运行时间：2026-09-27T14:48:11 → 2026-09-27T14:49:28
- 后端：`http://127.0.0.1:8001`
- 数据范围：**隔离数据目录（评测账号，非真实学生）**
- 评测课程学科：`course_5a116e2cd4`，知识库可用=True

> 说明：以下结果来自**隔离的评测账号**，用于验证功能链路与可复现性；
> **不等于真实学生的学习成效**。真实教学效果仍标注为「待真实试用验证」。

## 一、通过率总览

| 评测集 | 样本数 | 通过 | 失败 | 通过率 |
|---|---|---|---|---|
| rag | 8 | 8 | 0 | 1.0 |
| rag_with_distractor | 9 | 9 | 0 | 1.0 |
| loop | 13 | 13 | 0 | 1.0 |
| agent | 8 | 8 | 0 | 1.0 |
| voice | 9 | 9 | 0 | 1.0 |

## rag

无失败案例。

<details><summary>全部用例</summary>

| 用例 | 结果 | 详情 |
|---|---|---|
| rag-01 | PASS | 1.71s 命中要点=True 引用定位=True 来源=['01_二叉树的定义与性质.txt', '03_二叉搜索树.txt'] |
| rag-02 | PASS | 1.43s 命中要点=True 引用定位=True 来源=['02_二叉树的遍历.txt', '03_二叉搜索树.txt'] |
| rag-03 | PASS | 1.95s 命中要点=True 引用定位=True 来源=['03_二叉搜索树.txt', '02_二叉树的遍历.txt'] |
| rag-04 | PASS | 1.35s 命中要点=True 引用定位=True 来源=['01_二叉树的定义与性质.txt', '03_二叉搜索树.txt'] |
| rag-05 | PASS | 1.01s 命中要点=True 引用定位=True 来源=['01_二叉树的定义与性质.txt', '03_二叉搜索树.txt'] |
| rag-06 | PASS | 1.76s 命中要点=True 引用定位=True 来源=['03_二叉搜索树.txt', '01_二叉树的定义与性质.txt'] |
| rag-07 | PASS | 1.28s 承认不知道=True 谎称材料有=无 补充常识并标注=True |
| rag-08 | PASS | 1.88s 承认不知道=True 谎称材料有=无 补充常识并标注=True |

</details>

## rag_with_distractor

无失败案例。

<details><summary>全部用例</summary>

| 用例 | 结果 | 详情 |
|---|---|---|
| rag-01 | PASS | 1.48s 命中要点=True 引用定位=True 来源=['01_二叉树的定义与性质.txt', '03_二叉搜索树.txt'] |
| rag-02 | PASS | 1.08s 命中要点=True 引用定位=True 来源=['02_二叉树的遍历.txt', '03_二叉搜索树.txt'] |
| rag-03 | PASS | 1.44s 命中要点=True 引用定位=True 来源=['03_二叉搜索树.txt', '02_二叉树的遍历.txt'] |
| rag-04 | PASS | 1.12s 命中要点=True 引用定位=True 来源=['01_二叉树的定义与性质.txt', '03_二叉搜索树.txt'] |
| rag-05 | PASS | 1.25s 命中要点=True 引用定位=True 来源=['01_二叉树的定义与性质.txt', '03_二叉搜索树.txt'] |
| rag-06 | PASS | 1.51s 命中要点=True 引用定位=True 来源=['03_二叉搜索树.txt', '01_二叉树的定义与性质.txt'] |
| rag-07 | PASS | 1.53s 承认不知道=True 谎称材料有=无 补充常识并标注=True |
| rag-08 | PASS | 2.08s 承认不知道=True 谎称材料有=无 补充常识并标注=True |
| rag-extra-material | PASS | 已上传无关资料并重建知识库后重跑同一批样本 |

</details>

## loop

无失败案例。

<details><summary>全部用例</summary>

| 用例 | 结果 | 详情 |
|---|---|---|
| judge-stu_good | PASS | 判分与预期一致：对 4 错 0 |
| wrongbook-stu_good | PASS | 错题本 0 道 |
| mastery-stu_good | PASS | 掌握度知识点 1 个，含证据=True |
| review-stu_good | PASS | 待复习 0 条 |
| judge-stu_mid | PASS | 判分与预期一致：对 2 错 2 |
| wrongbook-stu_mid | PASS | 错题本 2 道 |
| mastery-stu_mid | PASS | 掌握度知识点 1 个，含证据=True |
| review-stu_mid | PASS | 待复习 1 条 |
| analytics-sample-size | PASS | 学情样本量 18 vs 原始记录 18 |
| analytics-source-labelled | PASS | 来源与时间范围已标注：['quiz_attempts（学生作答与后端判分记录）', 'learning_tasks（学习任务与完成状态）'] / {'start': '2026-08-29', 'end': '2026-09-27'} |
| repeat-raw-count-increases | PASS | 重做后原始作答 18 → 19 |
| repeat-idempotent-token | PASS | 同一 client_token 重复提交：20 → 20 |
| repeat-latest-attempt-basis | PASS | 知识点统计口径=每题最新一次作答，原始计数=20 |

</details>

## agent

- 说明：Agent 建议的措辞由模型生成，本项只验证「是否引用了该生的真实记录」与「改计划是否走确认机制」，不评判措辞质量。

无失败案例。

<details><summary>全部用例</summary>

| 用例 | 结果 | 详情 |
|---|---|---|
| agent-run-status | PASS | 运行状态=SUCCEEDED |
| agent-tools-called | PASS | 调用工具 6 个：['get_learning_state', 'analyze_budget', 'schedule_review', 'generate_note', 'create_plan', 'explain_plan'] |
| agent-tool-get_learning_state | PASS | 调用了 get_learning_state |
| agent-tool-analyze_budget | PASS | 调用了 analyze_budget |
| agent-reads-real-records | PASS | 学习状态摘要：知识点 1 个（已巩固 0、薄弱 0、待评估 0）；错题 1 道；待复习 1 项 每天 30 分钟，截止 None，剩余 15 分钟 / 可用 None 分钟，预算充足 复习任务（到期 2026-09-28，合并次数 1） 生成资料《二叉树 |
| agent-changes-via-proposal | PASS | 与计划变更相关的工具：无 |
| agent-uses-student-data | PASS | 建议中出现的课程相关词：['二叉树'] |
| plan-confirmation | PASS | 重大调整=True 需确认=True 状态=PROPOSED |

</details>

## voice

- 说明：识别回读文本可用于粗查漏读/错读；同音字与断句仍需人工确认

无失败案例。

**已知限制**：

- 本项只验证流程与耗时；**音质/自然度必须人工试听**（样本见 output/listen/），程序跑通不代表听感合格
- 耗时会被 GPU 争用显著放大：首轮全量评测中同一接口出现 RTF 17.3 的离群值，单独复测为 0.90-1.00；因此以 tts-latency-repeat 的区间为准，并建议评测时不要让其他 GPU 任务同时运行

<details><summary>全部用例</summary>

| 用例 | 结果 | 详情 |
|---|---|---|
| speech-capability | PASS | 本地 ASR=True TTS=True |
| tts-voice-01 | PASS | 合成 1 段 / 1.51s 音频 / 用时 1.95s （RTF=1.29） |
| asr-roundtrip-voice-01 | PASS | 识别用时 0.15s，回读：二叉树的定义是什么？ |
| end2end-voice-01 | PASS | 识别→问答 用时 1.65s，回答 320 字 |
| tts-voice-02 | PASS | 合成 1 段 / 4.96s 音频 / 用时 4.68s （RTF=0.94） |
| asr-roundtrip-voice-02 | PASS | 识别用时 0.2s，回读：前序遍历的顺序是跟左右中序遍历的顺序是左跟右。 |
| tts-voice-03 | PASS | 合成 1 段 / 4.93s 音频 / 用时 4.57s （RTF=0.93） |
| asr-roundtrip-voice-03 | PASS | 识别用时 0.21s，回读：显存占用约八GB,准确率提升了三点一四倍，耗时一点五秒。 |
| tts-latency-repeat | PASS | 同一文本重复 3 次合成：RTF 最小 0.92 / 中位 0.93 / 最大 1.05，绝对耗时 [4.37, 5.63, 4.96]s |

</details>
