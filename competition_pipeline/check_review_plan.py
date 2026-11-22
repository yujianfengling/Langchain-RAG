"""独立复算讲评方案对照（不依赖 review_plan 的排序函数）。

直接从 evidence_index + scope_index + curriculum_map 重算：
  - 每题失分人数 / 可定位失分点（naive 与 common 的排序依据）
  - 同预算下 naive 选题 vs common 选题、覆盖学生数、补测任务数
与 official/review_plans.json 的 comparison 比对；不一致即 FAIL。
用法：python competition_pipeline/check_review_plan.py --src <official> --analysis <official/analysis> \
        --plans <official/review_plans.json> --students <dc01-members.json>
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


def rubric_lost(e: dict, rubric_text: str):
    defs = {}
    for line in (rubric_text or "").splitlines():
        m = re.match(r"\s*([A-Za-z0-9]+P\d+)\s+(.+?)\s+(\d+(?:\.\d+)?)\s*分\s*$", line)
        if m:
            defs[m.group(1)] = float(m.group(3))
    kv = {}
    for seg in re.split(r"[；;\n]", e.get("rubric_scores", "") or ""):
        m = re.match(r"\s*([A-Za-z0-9]+P\d+)\s*=\s*(\d+(?:\.\d+)?)", seg)
        if m:
            kv[m.group(1)] = float(m.group(2))
    return [p for p, v in kv.items() if v < defs.get(p, v)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--analysis", required=True)
    ap.add_argument("--plans", required=True)
    ap.add_argument("--students", required=True)
    a = ap.parse_args()
    src, analysis = Path(a.src), Path(a.analysis)
    ev = json.loads((src / "evidence_index.json").read_text(encoding="utf-8"))
    scope = json.loads((src / "scope_index.json").read_text(encoding="utf-8"))
    cmap = json.loads((analysis / "curriculum_map.json").read_text(encoding="utf-8"))["indicators"]
    plans = json.loads(Path(a.plans).read_text(encoding="utf-8"))
    members = set(json.loads(Path(a.students).read_text(encoding="utf-8")))
    node, theme = plans["node_id"], plans["theme"]
    inds = {c["indicator_id"] for c in cmap if c["theme_id"] == theme}
    rubric = {m["material_id"]: m.get("rubric", "") for c in cmap for m in c["materials"]}
    item_ind = scope["item_indicator"]

    students_by_item = defaultdict(set)
    located_by_item = defaultdict(int)
    for e in ev:
        if e["student_id"] not in members or e["node_id"] != node:
            continue
        if item_ind.get(e["item_id"]) not in inds:
            continue
        if e["status"] != "有效得分" or e.get("quarantined") or e.get("out_of_scope"):
            continue
        if e.get("score") is None or e.get("full_score") is None:
            continue
        if float(e["score"]) < float(e["full_score"]):
            students_by_item[e["item_id"]].add(e["student_id"])
            # 与 review_plan 口径一致：只有能定位到评分点的失分才计 located
            located_by_item[e["item_id"]] += len(rubric_lost(e, rubric.get(e["item_id"], "")))

    fails = 0
    for b in plans["budgets"]:
        cmp = plans["comparison"][str(b)]
        naive_ids = sorted(students_by_item, key=lambda q: (-len(students_by_item[q]), q))[:b]
        common_ids = sorted(students_by_item, key=lambda q: (-located_by_item[q], -len(students_by_item[q]), q))[:b]
        ok_n = cmp["naive"]["lecture_items"] == naive_ids
        ok_c = cmp["plan"]["lecture_items"] == common_ids
        naive_students = set().union(*[students_by_item[q] for q in naive_ids]) if naive_ids else set()
        common_students = set().union(*[students_by_item[q] for q in common_ids]) if common_ids else set()
        ok_ns = cmp["naive"]["covered_students"] == sorted(naive_students)
        ok_cs = cmp["plan"]["covered_students"] == sorted(common_students | {
            s for t in plans["strategies"]["common"]["budgets"][str(b)]["pretest_tasks"] for s in t["students"]})
        ok_loc = cmp["plan"]["located_points"] == sum(located_by_item[q] for q in common_ids)
        for label, ok in [(f"budget{b} naive选题", ok_n), (f"budget{b} common选题", ok_c),
                          (f"budget{b} naive覆盖学生", ok_ns), (f"budget{b} common覆盖学生", ok_cs),
                          (f"budget{b} common定位失分点", ok_loc)]:
            print(("PASS | " if ok else "FAIL | ") + label)
            fails += 0 if ok else 1
    print(f"[check-review-plan] fails={fails}")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
