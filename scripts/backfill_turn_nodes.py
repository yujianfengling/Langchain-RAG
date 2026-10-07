"""把历史轮次补上知识点归属（node_id）。

背景：tutor_turns 的 node_id 是 2026-10-03 才加的字段，之前产生的记录没有归属，
于是"点某个知识点看它以前讲过什么"会看到空白。

我们的提问文案里带着知识点名（例如「围绕知识点「压杆稳定」出 3 道练习题。」），
所以可以从中反推——这比按当前会话推断可靠得多。

用法（项目根目录）：
    python scripts/backfill_turn_nodes.py            # 只报告，不写入
    python scripts/backfill_turn_nodes.py --apply    # 实际回填
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import knowledge_map, tutor_session  # noqa: E402

# 追问预设里的知识点写法：「xxx」或【xxx】
_LABEL_RE = re.compile(r"[「【]([^」】]{1,40})[」】]")


def main(apply: bool) -> int:
    conn = tutor_session._get_db()
    try:
        rows = conn.execute(
            "SELECT id, subject_id, user_message FROM tutor_turns "
            "WHERE node_id IS NULL OR node_id = ''"
        ).fetchall()
        print(f"待回填 {len(rows)} 条")
        filled = skipped = 0
        for r in rows:
            m = _LABEL_RE.search(r["user_message"] or "")
            if not m:
                skipped += 1
                continue
            label = m.group(1).strip()
            node_id = knowledge_map.stable_node_id(label, "root")
            node = knowledge_map.get_node(r["subject_id"], node_id) if r["subject_id"] else None
            if not node:
                skipped += 1
                print(f"  未匹配到节点，跳过：{label}")
                continue
            if apply:
                conn.execute("UPDATE tutor_turns SET node_id = ? WHERE id = ?",
                             (node_id, r["id"]))
            filled += 1
            print(f"  {'回填' if apply else '可回填'}：{label} -> {node_id}")
        if apply:
            conn.commit()
        print(f"完成：{'已回填' if apply else '可回填'} {filled} 条，跳过 {skipped} 条"
              + ("" if apply else "（这是预演，加 --apply 才会写入）"))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
