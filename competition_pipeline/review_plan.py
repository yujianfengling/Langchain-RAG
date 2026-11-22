"""
智慧学伴比赛版 · 班级讲评方案生成器（首版：N01 / DC01 / T01 整式运算）
============================================================
两套策略 × 题数预算（2/3）：
  common  优先讲共性失分：按「可定位到评分点的失分点数 → 失分人数」排序选题；
          另附证据不足学生的补测观察任务（不占讲评题预算）。
  pretest 优先给证据不足的学生补测：按「需要该题观察的学生数」排序选补测题。
对照口径 naive「按失分人数选题」：只按失分人数排序、不做评分点定位、不附补测任务。

所有数字由官方证据 + 诊断（截至所选测验口径）重算；会话现场证据通过
extra_evidence 注入（仅影响方案重算，不写回官方统计/答卷/评分）。
工作量按学生×题目×任务类型计数：同题同时安排讲评与补测算两个任务，
讲评只按现有 lost_students 分配计数，不扩展为全班；观察项数不是得分。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def _rubric_defs(text: str) -> Dict[str, dict]:
    defs = {}
    for line in (text or "").splitlines():
        m = re.match(r"\s*([A-Za-z0-9]+P\d+)\s+(.+?)\s+(\d+(?:\.\d+)?)\s*分\s*$", line)
        if m:
            defs[m.group(1)] = {"label": m.group(2).strip(), "full": float(m.group(3))}
    return defs


def _rubric_scores(text: str) -> Dict[str, float]:
    kv = {}
    for seg in re.split(r"[；;\n]", text or ""):
        m = re.match(r"\s*([A-Za-z0-9]+P\d+)\s*=\s*(\d+(?:\.\d+)?)", seg)
        if m:
            kv[m.group(1)] = float(m.group(2))
    return kv


def load_context(src: Path, analysis: Path, node: str, class_id: str, theme: str) -> dict:
    evidence = json.loads((src / "evidence_index.json").read_text(encoding="utf-8"))
    scope = json.loads((src / "scope_index.json").read_text(encoding="utf-8"))
    diagnoses = json.loads((analysis / "diagnoses.json").read_text(encoding="utf-8"))["diagnoses"]
    cmap = json.loads((analysis / "curriculum_map.json").read_text(encoding="utf-8"))["indicators"]
    inds = [c["indicator_id"] for c in cmap if c["theme_id"] == theme]
    item_ind = scope["item_indicator"]
    rubric_by_item = {m["material_id"]: m.get("rubric", "") for c in cmap for m in c["materials"]}
    # 班级成员由调用方传入（bundle students / scope_index.student_class），不在证据里反推。
    return {"evidence": evidence, "scope": scope, "diagnoses": diagnoses,
            "inds": inds, "item_ind": item_ind, "rubric_by_item": rubric_by_item,
            "node": node, "class_id": class_id, "theme": theme}


def build_facts(ctx: dict, class_members: List[str], extra: Optional[List[dict]] = None) -> dict:
    """失分点（学生×题×评分点）、覆盖不足学生、截至本测验做过题集合。"""
    node, inds = ctx["node"], ctx["inds"]
    item_ind, rubric_by_item = ctx["item_ind"], ctx["rubric_by_item"]
    lost: List[dict] = []
    done: Dict[str, set] = defaultdict(set)
    scored_count: Dict[tuple, int] = defaultdict(int)
    for e in ctx["evidence"]:
        sid = e["student_id"]
        if sid not in class_members or e["node_id"] != node:
            continue
        ind = item_ind.get(e["item_id"], "")
        if ind not in inds:
            continue
        done[sid].add(e["item_id"])
        if e["status"] != "有效得分" or e.get("quarantined") or e.get("out_of_scope"):
            continue
        scored_count[(sid, ind)] += 1
        if e.get("score") is None or e.get("full_score") is None:
            continue
        if float(e["score"]) < float(e["full_score"]):
            defs = _rubric_defs(rubric_by_item.get(e["item_id"], ""))
            kv = _rubric_scores(e.get("rubric_scores", ""))
            points = [{"point_id": p, "label": defs.get(p, {}).get("label", p),
                       "earned": v, "full": defs.get(p, {}).get("full", v)}
                      for p, v in kv.items() if v < defs.get(p, {}).get("full", v)]
            if not points:
                points = [{"point_id": "未定位", "label": "官方未提供评分点明细", "earned": e["score"], "full": e["full_score"]}]
            for p in points:
                lost.append({"student": sid, "item": e["item_id"], "indicator": ind,
                             "record_id": e["record_id"], **p})
    # 会话现场证据（仅方案重算用）；correct 也必须由调用方显式传入，
    # 不推断未提交的作答，不消除官方失分点，不写回官方成绩。
    live_kinds = {
        "mid_wrong": ("LIVE-MID", "去括号/符号（现场观察）"),
        "final_wrong": ("LIVE-FINAL", "结果核对（现场观察）"),
        "combine_wrong": ("LIVE-COMBINE", "合并同类项（现场观察）"),
        "transcription_wrong": ("LIVE-TRANSCRIPTION", "过程末行与最终填答不一致（现场观察）"),
    }
    for x in extra or []:
        sid, iid = x.get("student_id"), x.get("item_id")
        if sid not in class_members:
            continue
        ind = item_ind.get(iid, "")
        if ind not in inds:
            continue
        kind = x.get("kind")
        if kind == "correct":
            scored_count[(sid, ind)] += 1
        elif kind in live_kinds:
            point_id, label = live_kinds[kind]
            lost.append({"student": sid, "item": iid, "indicator": ind,
                         "record_id": x.get("record_id", "现场作答"),
                         "point_id": point_id, "label": label,
                         "earned": 0, "full": 1, "live": True})
    insufficient = []
    for d in ctx["diagnoses"]:
        if d["node_id"] != node or d["indicator_id"] not in inds or d["student_id"] not in class_members:
            continue
        eff = scored_count[(d["student_id"], d["indicator_id"])]
        if eff < d["coverage"]["min_evidence"]:
            insufficient.append({"student": d["student_id"], "indicator": d["indicator_id"],
                                 "effective": eff, "min": d["coverage"]["min_evidence"],
                                 "pretest_questions": d.get("pretest_questions", [])})
    return {"lost": lost, "done": done, "insufficient": insufficient, "scored_count": scored_count}


def _rank_common(facts: dict) -> List[dict]:
    by_item: Dict[str, dict] = {}
    for lp in facts["lost"]:
        b = by_item.setdefault(lp["item"], {"item": lp["item"], "indicator": lp["indicator"],
                                            "students": set(), "points": 0, "located": 0})
        b["students"].add(lp["student"])
        b["points"] += 1
        if lp["point_id"] != "未定位":
            b["located"] += 1
    rows = list(by_item.values())
    rows.sort(key=lambda b: (-b["located"], -len(b["students"]), b["item"]))
    return rows


def _rank_naive(facts: dict) -> List[dict]:
    by_item: Dict[str, dict] = {}
    for lp in facts["lost"]:
        b = by_item.setdefault(lp["item"], {"item": lp["item"], "indicator": lp["indicator"],
                                            "students": set(), "points": 0, "located": 0})
        b["students"].add(lp["student"])
        b["points"] += 1
    rows = list(by_item.values())
    rows.sort(key=lambda b: (-len(b["students"]), b["item"]))
    return rows


def _rank_pretest(facts: dict) -> List[dict]:
    need: Dict[str, dict] = {}
    for ins in facts["insufficient"]:
        for q in ins["pretest_questions"]:
            if q in facts["done"].get(ins["student"], set()):
                continue
            b = need.setdefault(q, {"item": q, "indicator": ins["indicator"], "students": set()})
            b["students"].add(ins["student"])
    rows = list(need.values())
    rows.sort(key=lambda b: (-len(b["students"]), b["item"]))
    return rows


def _point_key(point: dict) -> tuple:
    """独立作答记录不能因学生、题目、评分点相同而互相抵消。"""
    return (point["student"], point["item"], point["point_id"],
            point.get("record_id", ""), bool(point.get("live", False)))


def _point_ref(point: dict) -> dict:
    return {"student": point["student"], "item": point["item"],
            "point_id": point["point_id"], "label": point["label"],
            "record_id": point.get("record_id", ""), "live": bool(point.get("live", False))}


def _assignment_keys(lecture: List[dict], pretest: List[dict]) -> set:
    return ({(student, item["item_id"], "lecture")
             for item in lecture for student in item["lost_students"]}
            | {(student, item["item_id"], "pretest")
               for item in pretest for student in item["students"]})


def _workload(strategy: str, budget: int, lecture: List[dict], pretest: List[dict]) -> dict:
    """纯汇总既有分配；同题不同 type 算两个任务，不按评分点数重复布置。"""
    assignments = _assignment_keys(lecture, pretest)
    by_student = {}
    for student, item_id, kind in sorted(assignments):
        row = by_student.setdefault(student, {"student": student, "lecture_items": [],
                                             "pretest_items": [], "task_count": 0})
        row[f"{kind}_items"].append(item_id)
        row["task_count"] += 1
    lecture_ids = {item["item_id"] for item in lecture}
    pretest_ids = {item["item_id"] for item in pretest}
    lecture_count = sum(kind == "lecture" for _, _, kind in assignments)
    return {
        "lecture_budget": budget if strategy in ("common", "naive") else 0,
        "pretest_budget": budget if strategy == "pretest" else (3 if strategy == "common" else 0),
        "lecture_questions": len(lecture_ids), "pretest_questions": len(pretest_ids),
        "total_unique_questions": len(lecture_ids | pretest_ids),
        "lecture_assignments": lecture_count,
        "pretest_assignments": len(assignments) - lecture_count,
        "total_assignments": len(assignments),
        "groups": [{"item_id": item["item_id"], "indicator": item["indicator"],
                    "students": sorted(set(item["students"])), "questions_per_student": 1,
                    "assignments": len(set(item["students"]))} for item in pretest],
        "student_tasks": list(by_student.values()),
    }


def make_plan(strategy: str, budget: int, facts: dict, ranked: List[dict], with_pretest: bool) -> dict:
    picked = ranked[:budget]
    dropped = ranked[budget:]
    lecture = []
    pretest = []
    covered_students = set()
    covered_points = []
    if strategy == "pretest":
        # 补测优先：预算全部用于补测观察题，讲评题为空
        for b in picked:
            studs = sorted(b["students"])
            pretest.append({"item_id": b["item"], "indicator": b["indicator"], "students": studs,
                            "rank_reason": f"需该题观察的证据不足学生 {len(studs)} 人",
                            "observe": f"观察 {b['indicator']} 的两步表现（去括号/结果核对），收集有效记录"})
            covered_students.update(studs)
    else:
        for b in picked:
            pts = [lp for lp in facts["lost"] if lp["item"] == b["item"]]
            studs = sorted({lp["student"] for lp in pts})
            covered_students.update(studs)
            covered_points.extend(pts)
            lecture.append({"item_id": b["item"], "indicator": b["indicator"],
                            "rank_reason": f"可定位失分点 {b.get('located', 0)} 个、失分人数 {len(b['students'])}",
                            "lost_students": studs,
                            "lost_points": [{"student": p["student"], "point_id": p["point_id"],
                                             "label": p["label"], "record_id": p["record_id"],
                                             "live": bool(p.get("live", False))} for p in pts]})
        if with_pretest:
            for b in _rank_pretest(facts)[:3]:
                studs = sorted(b["students"])
                pretest.append({"item_id": b["item"], "indicator": b["indicator"], "students": studs,
                                "rank_reason": f"需该题观察的证据不足学生 {len(studs)} 人",
                                "observe": f"观察 {b['indicator']} 的两步表现（去括号/结果核对），收集有效记录"})
                covered_students.update(studs)
    covered_keys = {_point_key(p) for p in covered_points}
    uncovered_points = [lp for lp in facts["lost"] if _point_key(lp) not in covered_keys]
    assigned_pre_students = {s for t in pretest for s in t["students"]}
    uncovered_insufficient = [i for i in facts["insufficient"] if i["student"] not in assigned_pre_students]
    return {
        "strategy": strategy, "budget": budget,
        "lecture_items": lecture, "pretest_tasks": pretest,
        "covered_students": sorted(covered_students),
        "covered_lost_points": len(covered_points),
        "workload": _workload(strategy, budget, lecture, pretest),
        "coverage_audit": {
            "total_points": len(facts["lost"]), "covered_points": len(covered_points),
            "uncovered_points": len(uncovered_points),
            "official_points": sum(not p.get("live", False) for p in facts["lost"]),
            "live_points": sum(bool(p.get("live", False)) for p in facts["lost"]),
            "unit": "学生×题目×评分点观察项（不是得分）",
        },
        "uncovered": {
            "lost_points": [_point_ref(p) for p in uncovered_points],
            "insufficient_students": uncovered_insufficient,
            "reasons": (["讲评题预算外的失分点暂不覆盖"] if uncovered_points else [])
                       + (["证据不足学生未安排到补测题"] if uncovered_insufficient else []),
        },
        "dropped_if_lower_budget": [
            {"item_id": b["item"], "lost_students": sorted(b["students"]),
             "reason": f"排序低于保留题（可定位失分点 {b.get('located', 0)}、失分人数 {len(b['students'])}）"}
            for b in dropped],
    }


def compare_plans(before: dict, after: dict) -> dict:
    """对比两个 make_plan 结果，不修改方案或事实。

    added_points / removed_points 是未覆盖观察项的增减，含预算变化导致的
    转入/转出；不是增减官方得分。身份使用学生、题目、评分点、record_id、live，
    label 仅用于展示；任务身份使用学生、题目、type（同题不同 type 算两项）。
    """
    before_points = [_point_ref(p) for p in before["uncovered"]["lost_points"]]
    after_points = [_point_ref(p) for p in after["uncovered"]["lost_points"]]
    before_counts = Counter(_point_key(p) for p in before_points)
    after_counts = Counter(_point_key(p) for p in after_points)

    def point_delta(points, counts):
        refs = {_point_key(p): p for p in points}
        return [dict(refs[key]) for key in sorted(counts) for _ in range(counts[key])]

    added = point_delta(after_points, after_counts - before_counts)
    removed = point_delta(before_points, before_counts - after_counts)
    old_tasks = _assignment_keys(before["lecture_items"], before["pretest_tasks"])
    new_tasks = _assignment_keys(after["lecture_items"], after["pretest_tasks"])

    def task_refs(keys):
        return [{"student": student, "item_id": item, "type": kind}
                for student, item, kind in sorted(keys)]

    assignments = {"changed": old_tasks != new_tasks,
                   "added": task_refs(new_tasks - old_tasks),
                   "removed": task_refs(old_tasks - new_tasks),
                   "unchanged_count": len(old_tasks & new_tasks)}
    old_audit, new_audit = before["coverage_audit"], after["coverage_audit"]
    explanation = (
        f"观察项总数 {old_audit['total_points']} → {new_audit['total_points']}，"
        f"已覆盖 {old_audit['covered_points']} → {new_audit['covered_points']}，"
        f"未覆盖 {old_audit['uncovered_points']} → {new_audit['uncovered_points']}。"
        "具体增减项指未覆盖观察项的转入/转出，不是得分变化。"
    )

    def all_counts(plan):
        points = list(plan["uncovered"]["lost_points"])
        points.extend({**p, "item": item["item_id"]}
                      for item in plan["lecture_items"] for p in item["lost_points"])
        return Counter(_point_key(p) for p in points)

    def item_keys(plan):
        return ({(item["item_id"], "lecture") for item in plan["lecture_items"]}
                | {(item["item_id"], "pretest") for item in plan["pretest_tasks"]})

    old_all, new_all = all_counts(before), all_counts(after)
    only_new_uncovered = (
        bool(added) and not removed and not (old_all - new_all)
        and after_counts - before_counts == new_all - old_all
        and new_audit["total_points"] - old_audit["total_points"] == len(added)
        and new_audit["covered_points"] == old_audit["covered_points"]
    )
    if only_new_uncovered and not assignments["changed"] and item_keys(before) == item_keys(after):
        explanation += "仅新增未覆盖观察，题目/分配未变：观察项总数增加，学生任务未变化，不代表教学成效下降。"
    elif assignments["changed"]:
        explanation += f"学生任务已变化：新增 {len(assignments['added'])} 项，移除 {len(assignments['removed'])} 项。"
    else:
        explanation += "学生任务未变化。"
    explanation += "任务按学生×题目×类型计数，同题不同 type 算两个任务；仅模拟会话重算，不修改官方成绩。"
    return {"before": dict(old_audit), "after": dict(new_audit),
            "added_points": added, "removed_points": removed,
            "assignments": assignments, "explanation": explanation}


def budget_diff(higher: dict, lower: dict) -> dict:
    def items(p):
        return ({x["item_id"]: x for x in p["lecture_items"]}
                | {x["item_id"]: x for x in p["pretest_tasks"]})
    hi, lo = items(higher), items(lower)
    removed = sorted(set(hi) - set(lo))
    affected = sorted({s for q in removed for s in hi[q].get("lost_students", hi[q].get("students", []))}
                      - {s for x in lo.values() for s in x.get("lost_students", x.get("students", []))})
    return {"removed_items": removed, "affected_students": affected,
            "kept": [{"item_id": x["item_id"], "why": x.get("rank_reason", "")} for x in lo.values()]}


def generate(src: Path, analysis: Path, class_members: List[str], node: str, class_id: str,
             theme: str, budgets=(2, 3), extra: Optional[List[dict]] = None) -> dict:
    ctx = load_context(src, analysis, node, class_id, theme)
    facts = build_facts(ctx, class_members, extra)
    rc, rn, rp = _rank_common(facts), _rank_naive(facts), _rank_pretest(facts)
    strategies = {}
    for name, ranked, with_pre in (("common", rc, True), ("pretest", rp, False)):
        strategies[name] = {"budgets": {str(b): make_plan(name, b, facts, ranked, with_pre) for b in budgets}}
    comparison = {}
    for b in budgets:
        naive = make_plan("naive", b, facts, rn, False)
        plan = strategies["common"]["budgets"][str(b)]
        comparison[str(b)] = {
            "naive_plan": naive,
            "naive": {"covered_students": naive["covered_students"],
                      "covered_lost_points": naive["covered_lost_points"],
                      "located_points": sum(1 for x in naive["lecture_items"] for p in x["lost_points"] if p["point_id"] != "未定位"),
                      "pretest_tasks": len(naive["pretest_tasks"]),
                      "lecture_items": [x["item_id"] for x in naive["lecture_items"]]},
            "plan": {"covered_students": plan["covered_students"],
                     "covered_lost_points": plan["covered_lost_points"],
                     "located_points": sum(1 for x in plan["lecture_items"] for p in x["lost_points"] if p["point_id"] != "未定位"),
                     "pretest_tasks": len(plan["pretest_tasks"]),
                     "lecture_items": [x["item_id"] for x in plan["lecture_items"]]},
            "tradeoff": ("按失分人数选题优先覆盖最多学生的共性失分，但不保证失分能定位到评分点、也不安排证据不足学生的观察；"
                         "讲评方案按可定位失分点排序并附补测观察任务，可能少覆盖个别共性失分题，换来可讲评的评分点依据与覆盖不足学生的后续证据。"),
        }
    diffs = {}
    if 3 in budgets and 2 in budgets:
        for name in strategies:
            diffs[name] = budget_diff(strategies[name]["budgets"]["3"], strategies[name]["budgets"]["2"])
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "node_id": node, "class_id": class_id, "theme": theme,
            "indicators": ctx["inds"], "budgets": list(budgets),
            "strategies": strategies, "comparison": comparison, "budget_diff_3_to_2": diffs,
            "session_extra_evidence": extra or []}


def main() -> None:
    ap = argparse.ArgumentParser(description="班级讲评方案生成器")
    ap.add_argument("--src", required=True)
    ap.add_argument("--analysis", required=True)
    ap.add_argument("--students", required=True, help="班级学生编号 JSON 列表文件")
    ap.add_argument("--node", default="N01")
    ap.add_argument("--class-id", default="DC01")
    ap.add_argument("--theme", default="T01")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    members = json.loads(Path(a.students).read_text(encoding="utf-8"))
    plan = generate(Path(a.src), Path(a.analysis), members, a.node, a.class_id, a.theme)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[review-plan] {out} strategies={list(plan['strategies'])} budgets={plan['budgets']}")


if __name__ == "__main__":
    main()
