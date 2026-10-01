"""
正式库测试数据清单（只读，不删除任何数据）

背景（补验5）：
- 隔离机制上线**之前**的浏览器验收/接口试验把测试账号与学习记录写进了正式库
  （data/*.db），这些残留需要一份准确清单，供人工决定如何清理。
- 本脚本**只读**：以 `mode=ro` 打开数据库，不执行 INSERT/UPDATE/DELETE，
  也不按 `e2e_`/`stu_` 等名称前缀批量判断（前缀只能作为线索，不能作为删除依据）。

用法：
    python tests/inventory_production_data.py            # 打印清单
    python tests/inventory_production_data.py > 清单.txt  # 存档

判定口径：
- legacy：`users.is_legacy=1`（历史 default_user，迁移时登记为待认领数据）
- 测试账号：非 legacy、非显式业务账号，且创建时间落在本轮开发/验收时段，
  用户名带脚本化特征（e2e_/stu_/rt_/agent_/probe_ 等）——**仅作线索**
- 孤儿数据：user_id 在 auth.db 中查不到账号的记录（隔离缺陷修复前的残留，
  来自"测试库账号 + 正式库表"的错配写入）
"""

import os
import sqlite3
import sys
from collections import Counter, OrderedDict

DATA_DIR = os.environ.get("EDU_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# (库文件, 表名, 归属列或 (归属列, 关联表, 关联列))
TABLES = [
    ("chat.db", "conversations", "user_id"),
    ("chat.db", "messages", ("conversation_id", "conversations", "user_id")),
    ("learning.db", "learning_plans", "user_id"),
    ("learning.db", "learning_tasks", "user_id"),
    ("learning.db", "plan_constraints", "user_id"),
    ("learning.db", "plan_adjustments", "user_id"),
    ("learning.db", "task_adjustments", "user_id"),
    ("learning.db", "agent_runs", "user_id"),
    ("learning.db", "agent_run_steps", ("run_id", "agent_runs", "user_id")),
    ("learning_loop.db", "quiz_attempts", "user_id"),
    ("learning_loop.db", "review_tasks", "user_id"),
    ("learning_loop.db", "learning_events", "user_id"),
    ("user_agent.db", "knowledge_mastery", "user_id"),
    ("user_agent.db", "learning_memory", "user_id"),
    ("user_agent.db", "learning_memory_history", "user_id"),
    ("user_agent.db", "learning_preferences", "user_id"),
    ("user_agent.db", "user_interactions", "user_id"),
    ("user_agent.db", "user_feedback", "user_id"),
    ("user_agent.db", "user_profiles", "user_id"),
]


def _ro(name: str) -> sqlite3.Connection:
    path = os.path.join(DATA_DIR, name)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn, table) -> list:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def count_by_owner(db, table, owner) -> Counter:
    """按归属统计行数；owner 为 (列, 关联表, 关联列) 时通过 join 归属到用户"""
    conn = _ro(db)
    try:
        if not _columns(conn, table):
            return Counter()
        if isinstance(owner, tuple):
            # 关联归属：表本身没有 user_id（如 messages → conversations.user_id）
            col, ref_table, ref_col = owner
            sql = (f"SELECT r.{ref_col} AS u, COUNT(*) AS n FROM {table} t "
                   f"LEFT JOIN {ref_table} r ON t.{col} = r.id "
                   f"GROUP BY r.{ref_col}")
        else:
            sql = f"SELECT {owner} AS u, COUNT(*) AS n FROM {table} GROUP BY {owner}"
        return Counter({(r["u"] or "(空)"): r["n"] for r in conn.execute(sql)})
    finally:
        conn.close()


def _guard() -> None:
    """只读清单脚本：未隔离也会继续，但会明确打印实际读取的目录"""
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tests._isolation_guard import require_isolated
    require_isolated("tests/inventory_production_data.py",
                     "只读：统计正式库里的历史残留数据（不写入）", read_only=True)


def main():
    _guard()
    print(f"数据目录：{os.path.abspath(DATA_DIR)}")
    print(f"（EDU_DATA_DIR={os.environ.get('EDU_DATA_DIR') or '未设置（默认 data/）'}）\n")

    auth = _ro("auth.db")
    users = [dict(r) for r in auth.execute(
        "SELECT id, username, is_legacy, status, created_at FROM users ORDER BY created_at")]
    known_ids = {u["id"] for u in users}

    # 先统计每账号的记录数：分类依据是"库里实际有什么"，不是用户名长什么样
    per_user = OrderedDict()
    for db, table, owner in TABLES:
        for uid, n in count_by_owner(db, table, owner).items():
            per_user.setdefault(uid, Counter())[f"{db}:{table}"] = n

    # ---- 1) 账号清单 ----
    print("## 1) 账号（auth.db: users）")
    print(f"共 {len(users)} 个账号\n")
    print("| 账号 | user_id | is_legacy | 记录行数 | 创建时间 |")
    print("|---|---|---|---|---|")
    for u in users:
        n = sum(per_user.get(u["id"], Counter()).values())
        print(f"| {u['username']} | {u['id']} | {u['is_legacy']} | {n} | {u['created_at']} |")

    print("\n## 2) 各账号在各表中的记录数（只统计非零）")
    for u in users:
        rows = per_user.get(u["id"])
        print(f"\n### {u['username']}（{u['id']}）")
        if not rows:
            print("  （无学习记录）")
            continue
        for k, v in rows.items():
            print(f"  - {k}: {v}")

    # ---- 3) 孤儿数据 ----
    print("\n## 3) 孤儿数据（user_id 在 auth.db 中无对应账号）")
    orphans = OrderedDict()
    for uid, rows in per_user.items():
        if uid == "(空)" or uid in known_ids:
            continue
        for k, v in rows.items():
            if v:
                orphans.setdefault(k, Counter())[uid] = v
    if not orphans:
        print("  （无）")
    for k, cnt in orphans.items():
        print(f"\n### {k}：{sum(cnt.values())} 行，涉及 {len(cnt)} 个不存在的 user_id")
        for uid, v in cnt.most_common():
            print(f"  - {uid}: {v}")

    # ---- 4) 迁移备份 ----
    backup_root = os.path.join(DATA_DIR, "backups")
    print("\n## 4) 迁移备份（保留，不清理）")
    if not os.path.isdir(backup_root):
        print("  （无）")
    else:
        for name in sorted(os.listdir(backup_root)):
            path = os.path.join(backup_root, name)
            if not os.path.isdir(path):
                continue
            files = sorted(os.listdir(path))
            sizes = ", ".join(
                f"{f}({os.path.getsize(os.path.join(path, f)) // 1024}KB)" for f in files)
            print(f"  - {name}: {sizes}")

    print("\n说明：本脚本只读打开数据库，未做任何删除或修改。"
          "清理方案需人工确认，且不能仅依据名称前缀。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
