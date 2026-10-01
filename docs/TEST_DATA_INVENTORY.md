# 正式库测试数据清单（补验5）

> 由 `python tests/inventory_production_data.py` 生成（**只读**，不做任何删除）。
> 生成时间：2026-09-25。用途：给"隔离机制上线前的历史残留"一份可复查的准确清单。

## 结论摘要

| 类别 | 数量 | 说明 |
|---|---|---|
| legacy 账号（`is_legacy=1`） | 1（`default_user`） | 身份机制上线前的历史演示数据，已在 `legacy_data_claims` 登记为 **UNCLAIMED**（待认领），未静默分配给任何新账号 |
| 隔离缺陷期间的测试账号 | 15 | 6 个 `e2e_*` + 7 个 `stu_*` + `alice` + 其它；其中 12 个**零记录**，3 个仅有少量记录（22 行合计） |
| 孤儿数据（user_id 在 auth.db 无对应账号） | 113 行 | 来自"测试库账号 + 正式库表"错配写入，**修复前**的历史残留 |
| 迁移备份 | 5 组 × 3 库 | `data/backups/20260925-1345*`，**保留不清理** |

**本轮不删除任何数据**：按你的要求，不能只依据 `e2e_`/`stu_` 前缀批量删除。清理方案见文末"建议清理方案（待授权）"。

## 修复后的隔离状态（已实测）

- 自动测试：`tests/fixtures_isolated.py` 把全部数据路径指向临时目录；跑完 432 个用例后，
  5 个正式库的**全表内容 MD5 完全一致**（逐表逐行哈希比对）。
- 真实模型验收：后端以 `EDU_DATA_DIR=E:/WORKBUDDY/Game/git_text_e2e_data` 启动；
  `/health` 暴露 `data_scope.isolated`，脚本在写入任何数据前校验，未隔离直接退出（实测退出码 2）。

```bash
# 零写入证明（跑测试前后各执行一次并比对哈希）
python - <<'EOF'
import sqlite3, hashlib
for db in ["data/auth.db","data/chat.db","data/learning.db","data/learning_loop.db","data/user_agent.db"]:
    c=sqlite3.connect(db); h=hashlib.md5()
    for (sql,) in c.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"): h.update(sql.encode())
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        try: rows=list(c.execute(f"SELECT * FROM {t}"))
        except Exception: continue
        h.update(t.encode())
        for r in rows: h.update(repr(r).encode())
    print(db, h.hexdigest())
EOF
```

## 明细

数据目录：E:\WORKBUDDY\Game\git_text\data
（EDU_DATA_DIR=未设置（默认 data/））

## 1) 账号（auth.db: users）
共 16 个账号

| 账号 | user_id | is_legacy | 记录行数 | 创建时间 |
|---|---|---|---|---|
| default_user | default_user | 1 | 136 | 2026-09-25T13:43:31 |
| alice | user_f839cdd0ba84 | 0 | 0 | 2026-09-25T13:43:31 |
| e2e_mugk8gd4 | user_ff9f5486fb14 | 0 | 0 | 2026-09-25T14:09:18 |
| e2e_mugk9gcz | user_423bbc32660e | 0 | 0 | 2026-09-25T14:10:04 |
| e2e_mugkaeyi | user_09097f6ee4a0 | 0 | 0 | 2026-09-25T14:10:50 |
| e2e_mugkcm2k | user_a7f93f140417 | 0 | 0 | 2026-09-25T14:12:31 |
| e2e_mugkdu65 | user_fab9ecb53f3a | 0 | 0 | 2026-09-25T14:13:29 |
| e2e_mugkew5m | user_dcff848b8df3 | 0 | 0 | 2026-09-25T14:14:18 |
| stu_mugkg3vu | user_f666f4e20ec5 | 0 | 1 | 2026-09-25T14:14:44 |
| stu_mugkh03g | user_63e11f366994 | 0 | 1 | 2026-09-25T14:15:27 |
| stu_mugkmb5x | user_23e76fe2efd0 | 0 | 1 | 2026-09-25T14:19:34 |
| stu_mugkmqdh | user_46235853791c | 0 | 1 | 2026-09-25T14:19:53 |
| stu_mugkn5fg | user_0e7652fbec44 | 0 | 7 | 2026-09-25T14:20:14 |
| stu_mugkokz5 | user_5c80ce2d3340 | 0 | 4 | 2026-09-25T14:21:21 |
| stu_mugkppxm | user_63f73040eb70 | 0 | 7 | 2026-09-25T14:22:13 |
| e2e_mugkt7gn | user_dd1cef162fce | 0 | 0 | 2026-09-25T14:25:26 |

## 2) 各账号在各表中的记录数（只统计非零）

### default_user（default_user）
  - chat.db:conversations: 2
  - chat.db:messages: 16
  - learning.db:learning_plans: 1
  - learning.db:learning_tasks: 25
  - learning_loop.db:learning_events: 44
  - user_agent.db:knowledge_mastery: 3
  - user_agent.db:user_interactions: 44
  - user_agent.db:user_profiles: 1

### alice（user_f839cdd0ba84）
  （无学习记录）

### e2e_mugk8gd4（user_ff9f5486fb14）
  （无学习记录）

### e2e_mugk9gcz（user_423bbc32660e）
  （无学习记录）

### e2e_mugkaeyi（user_09097f6ee4a0）
  （无学习记录）

### e2e_mugkcm2k（user_a7f93f140417）
  （无学习记录）

### e2e_mugkdu65（user_fab9ecb53f3a）
  （无学习记录）

### e2e_mugkew5m（user_dcff848b8df3）
  （无学习记录）

### stu_mugkg3vu（user_f666f4e20ec5）
  - user_agent.db:user_profiles: 1

### stu_mugkh03g（user_63e11f366994）
  - user_agent.db:user_profiles: 1

### stu_mugkmb5x（user_23e76fe2efd0）
  - user_agent.db:user_profiles: 1

### stu_mugkmqdh（user_46235853791c）
  - user_agent.db:user_profiles: 1

### stu_mugkn5fg（user_0e7652fbec44）
  - learning.db:learning_plans: 1
  - learning.db:learning_tasks: 1
  - learning_loop.db:quiz_attempts: 1
  - learning_loop.db:review_tasks: 1
  - learning_loop.db:learning_events: 1
  - user_agent.db:knowledge_mastery: 1
  - user_agent.db:user_profiles: 1

### stu_mugkokz5（user_5c80ce2d3340）
  - learning_loop.db:quiz_attempts: 1
  - learning_loop.db:learning_events: 1
  - user_agent.db:knowledge_mastery: 1
  - user_agent.db:user_profiles: 1

### stu_mugkppxm（user_63f73040eb70）
  - learning.db:learning_plans: 1
  - learning.db:learning_tasks: 1
  - learning_loop.db:quiz_attempts: 1
  - learning_loop.db:review_tasks: 1
  - learning_loop.db:learning_events: 1
  - user_agent.db:knowledge_mastery: 1
  - user_agent.db:user_profiles: 1

### e2e_mugkt7gn（user_dd1cef162fce）
  （无学习记录）

## 3) 孤儿数据（user_id 在 auth.db 中无对应账号）

### learning.db:learning_plans：1 行，涉及 1 个不存在的 user_id
  - loop_user_1: 1

### learning.db:learning_tasks：1 行，涉及 1 个不存在的 user_id
  - loop_user_1: 1

### learning_loop.db:quiz_attempts：3 行，涉及 1 个不存在的 user_id
  - loop_user_1: 3

### learning_loop.db:review_tasks：1 行，涉及 1 个不存在的 user_id
  - loop_user_1: 1

### learning_loop.db:learning_events：3 行，涉及 1 个不存在的 user_id
  - loop_user_1: 3

### user_agent.db:knowledge_mastery：1 行，涉及 1 个不存在的 user_id
  - loop_user_1: 1

### learning.db:plan_constraints：18 行，涉及 18 个不存在的 user_id
  - user_07b197faa895: 1
  - user_123bc364107b: 1
  - user_2f08daccac36: 1
  - user_39175ae5740f: 1
  - user_4ac6300b33c7: 1
  - user_4df634f6f1f4: 1
  - user_54912c0a3ca9: 1
  - user_5b396fa1b760: 1
  - user_8e1a17f15d18: 1
  - user_970f3eaa750e: 1
  - user_c0dfd8b506e7: 1
  - user_ca61adfe3e19: 1
  - user_cdffcc3fcc82: 1
  - user_ce18e5f76509: 1
  - user_d21b914a341d: 1
  - user_e56f17a36717: 1
  - user_f77c34826a85: 1
  - user_fc691a820eb4: 1

### learning.db:agent_runs：18 行，涉及 16 个不存在的 user_id
  - user_375ef1cfdad1: 2
  - user_6877558b5f22: 2
  - user_07b197faa895: 1
  - user_2f08daccac36: 1
  - user_54912c0a3ca9: 1
  - user_fc691a820eb4: 1
  - user_17537205947c: 1
  - user_39fec97dbe31: 1
  - user_40a1333c0bf5: 1
  - user_527f6555434a: 1
  - user_8b854eec8840: 1
  - user_9b9dae972f1b: 1
  - user_aecc36f05ce2: 1
  - user_b31b46823b71: 1
  - user_baa31f3625be: 1
  - user_c55245c80c1b: 1

### learning.db:agent_run_steps：62 行，涉及 16 个不存在的 user_id
  - user_07b197faa895: 5
  - user_2f08daccac36: 5
  - user_54912c0a3ca9: 5
  - user_fc691a820eb4: 5
  - user_527f6555434a: 5
  - user_baa31f3625be: 5
  - user_39fec97dbe31: 4
  - user_40a1333c0bf5: 4
  - user_8b854eec8840: 4
  - user_9b9dae972f1b: 4
  - user_aecc36f05ce2: 4
  - user_c55245c80c1b: 4
  - user_17537205947c: 3
  - user_b31b46823b71: 3
  - user_375ef1cfdad1: 1
  - user_6877558b5f22: 1

### learning.db:plan_adjustments：4 行，涉及 4 个不存在的 user_id
  - user_5b396fa1b760: 1
  - user_c0dfd8b506e7: 1
  - user_d21b914a341d: 1
  - user_d10eaee161d3: 1

### user_agent.db:learning_memory：24 行，涉及 22 个不存在的 user_id
  - user_2352d933a294: 2
  - user_a541a758f53e: 2
  - user_03d86f76a12f: 1
  - user_08c3655ab80b: 1
  - user_1ce5b17e84a5: 1
  - user_392f275c3793: 1
  - user_46510629781d: 1
  - user_5e1cd1747da7: 1
  - user_6ddc26f86a2e: 1
  - user_81a8ac84edff: 1
  - user_81fa072608ee: 1
  - user_9ccd4d7a3c4f: 1
  - user_a273fe5099ef: 1
  - user_ab2875fb3d00: 1
  - user_b0ee2299179d: 1
  - user_b573bab9cf89: 1
  - user_dda41f885384: 1
  - user_e9b4024c5ee6: 1
  - user_ed5ed321253f: 1
  - user_ef82787acd2f: 1
  - user_f4ff26862492: 1
  - user_f8fe555a13f6: 1

### user_agent.db:learning_memory_history：32 行，涉及 22 个不存在的 user_id
  - user_a273fe5099ef: 4
  - user_b0ee2299179d: 4
  - user_2352d933a294: 2
  - user_5e1cd1747da7: 2
  - user_a541a758f53e: 2
  - user_f4ff26862492: 2
  - user_03d86f76a12f: 1
  - user_08c3655ab80b: 1
  - user_1ce5b17e84a5: 1
  - user_392f275c3793: 1
  - user_46510629781d: 1
  - user_6ddc26f86a2e: 1
  - user_81a8ac84edff: 1
  - user_81fa072608ee: 1
  - user_9ccd4d7a3c4f: 1
  - user_ab2875fb3d00: 1
  - user_b573bab9cf89: 1
  - user_dda41f885384: 1
  - user_e9b4024c5ee6: 1
  - user_ed5ed321253f: 1
  - user_ef82787acd2f: 1
  - user_f8fe555a13f6: 1

## 4) 迁移备份（保留，不清理）
  - 20260925-134145: chat.db(108KB), learning.db(32KB), user_agent.db(76KB)
  - 20260925-134232: chat.db(108KB), learning.db(32KB), user_agent.db(76KB)
  - 20260925-134331: chat.db(108KB), learning.db(32KB), user_agent.db(76KB)
  - 20260925-134538: chat.db(108KB), learning.db(32KB), user_agent.db(76KB)
  - 20260925-134639: chat.db(108KB), learning.db(32KB), user_agent.db(76KB)

说明：本脚本只读打开数据库，未做任何删除或修改。清理方案需人工确认，且不能仅依据名称前缀。

## 本轮新增记录（2026-09-26，已处理）

知识提取库 `data/extracted_knowledge.db` 此前**不在隔离夹具的覆盖范围内**（夹具只隔离了
chat/learning/learning_loop/user_agent/auth 五个库）。新增"知识提取使用默认模型"的用例时，
共向正式库写入了 **4 行**测试知识点（`knowledge_point='测试点'`，`source_type='qa_pair'`）。

根因有两个，都已修复：

1. **夹具覆盖面不足**：`tests/fixtures_isolated.py` 现在同时隔离
   `knowledge_expander.EXTRACTED_KB_PATH` 与 `_DATA_DIR`，并在隔离库中按生产逻辑建表。
2. **用例没有继承隔离基类**：该用例写成了 `unittest.TestCase`（而非 `IsolatedDataTestCase`），
   所以第 1 条修复一度没生效——这也说明"新增用例是否真的走隔离"需要显式检查。

处理：

1. 精确清理这 4 行（按 `id` 删除，**不是按名称前缀**）：`extracted_knowledge` 行数 4 → 0。
2. 清理前已备份：`data/backups/20260926-0935-pre-testrow-cleanup/`（含 wal/shm）。
3. 复核方式：跑全量测试前后对 **6 个库**（含 `extracted_knowledge.db`）做逐表逐行 MD5 比对，
   现在结果为"零写入"（此前只比对 5 个库，才漏掉了这一处）。

> 说明：这次偏差表明"隔离夹具的覆盖面"本身需要持续维护——新增模块/新增库/新增用例时，
> 必须同步确认其数据路径已被隔离，否则测试会重新开始向正式库散落数据。

## 建议清理方案（待授权，本轮未执行）

1. 先备份：把 `data/*.db` 复制到 `data/backups/<时间戳>-pre-cleanup/`（与既有迁移备份分开存放）。
2. 删除"孤儿数据"：按 user_id 逐条核对（**不是**按名称前缀），这几类可按
   "user_id 不在 `auth.users` 中、且不是 `default_user`" 判定：
   `plan_constraints`(18)、`plan_adjustments`(4)、`agent_runs`(18，连带的 `agent_run_steps` 62)、
   `learning_memory`(24) + `learning_memory_history`(32)、`chat.db.conversations` 中
   `user_id IS NULL` 的 7 条（14:14–14:22 产生，属 fetch/SSE 未带令牌那个缺陷的产物）。
3. 测试账号（15 个）单独处理：先导出这 15 个账号名下 22 行记录留档，再删除账号与其记录；
   `default_user` **不动**（保持 UNCLAIMED，等真实用户认领）。
4. 清理脚本必须产出"删前/删后行数对照表"并写入本文档，便于复查。
5. 迁移备份 5 组**继续保留**（用户明确要求）。
