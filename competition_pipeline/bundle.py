"""
智慧学伴比赛版 · 把 pipeline 输出 reshap 成页面可直接读取的 JSON bundle
============================================================
bundle 写到 frontend/public/assets/competition-data/（本地文件，不进 Git；
后端只挂载 /assets，因此必须放在 assets 下才能被托管）。
前端默认仍用演示样例；只有 URL 带 ?source=real 且 manifest.json 存在时才切换数据源。

真实官方数据到位后：
  1) pipeline.py run --data-dir <官方目录> --out-dir <本地输出>
  2) bundle.py build --src <本地输出> --csv-dir <官方目录> \
       --out frontend/public/assets/competition-data --source official
  3) npx vite build（后端已托管 dist，把 public 拷进 dist）
  4) 打开 <站点>/class-analytics?source=real，右上角标签变成「官方赛题数据」。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

# 官方主题编号 / 名称来自四人开发方案（公开口径），仅用于显示标签
THEME_LABELS = {
    "T01": "代数式与整式运算",
    "T02": "方程与方程组",
    "T03": "不等式与不等式组",
}


def _read_csv(path: Path, fields: dict | None = None) -> List[dict]:
    """读 CSV；给出 fields 时按别名表转成规范字段名（官方中文表头 → canonical）。"""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not fields:
        return rows
    header = reader.fieldnames or []
    out = []
    for row in rows:
        mapped = {}
        for canonical, aliases in fields.items():
            hit = next((a for a in aliases if a in header), None)
            mapped[canonical] = (row.get(hit) or "").strip() if hit else ""
        out.append(mapped)
    return out


def _find(csv_dir: Path, names: List[str]) -> Path:
    for n in names:
        p = csv_dir / n
        if p.exists():
            return p
    for p in sorted(csv_dir.glob("*.csv")):
        if any(k in p.name for k in ("学生", "students")):
            return p
    raise FileNotFoundError(f"在 {csv_dir} 找不到 {names}")


def _parse_steps(text: str) -> List[str]:
    if not text:
        return []
    parts = re.split(r"[→\n；;]", text)
    return [p.strip() for p in parts if p.strip()]


def _parse_rubric(text: str, rubric_def: str = "") -> List[dict]:
    """
    优先按官方格式：作答记录「评分点得分」= `Q015P1=1；Q015P2=0`，
    题目台账「评分点」= `Q015P1 正确去括号 1分`（多行）→ 提供标签与满分。
    老样例格式（`xx2分`）仍兼容；无法解析的条目原样作为证据，不编造分数。
    """
    if not text:
        return []
    # 官方：点编号=得分
    kv = {}
    for seg in re.split(r"[；;\n]", text):
        m = re.match(r"\s*([A-Za-z0-9]+P\d+)\s*=\s*(\d+(?:\.\d+)?)\s*", seg)
        if m:
            kv[m.group(1)] = float(m.group(2))
    if kv:
        defs = {}
        for line in (rubric_def or "").splitlines():
            m = re.match(r"\s*([A-Za-z0-9]+P\d+)\s+(.+?)\s+(\d+(?:\.\d+)?)\s*分\s*$", line)
            if m:
                defs[m.group(1)] = (m.group(2).strip(), float(m.group(3)))
        out = []
        for pid, earned in kv.items():
            label, full = defs.get(pid, (f"{pid}（官方评分点定义缺失）", earned))
            out.append({
                "id": pid, "label": label, "fullScore": full,
                "earnedScore": earned, "ok": earned >= full and full > 0,
                "evidence": f"{pid}={earned:g}" + (f"，满分 {full:g}" if pid in defs else ""),
            })
        return out
    # 兼容自造样例的「xxxN分」文本
    out = []
    for i, seg in enumerate(re.split(r"[；;]", text)):
        seg = seg.strip()
        if not seg:
            continue
        nums = re.findall(r"(\d+(?:\.\d+)?)\s*分", seg)
        earned = float(nums[0]) if nums else 0.0
        full = float(nums[1]) if len(nums) > 1 else earned
        out.append({
            "id": f"RP-{i+1}", "label": seg, "fullScore": full,
            "earnedScore": earned, "ok": earned >= full and full > 0,
            "evidence": seg,
        })
    return out


def build(src: Path, csv_dir: Path, out: Path, source_label: str = "sample") -> dict:
    src, csv_dir, out = Path(src), Path(csv_dir), Path(out)
    out.mkdir(parents=True, exist_ok=True)

    class_rows = json.loads((src / "class_analysis.json").read_text(encoding="utf-8"))
    evidence = json.loads((src / "evidence_index.json").read_text(encoding="utf-8"))
    curriculum = json.loads((src / "curriculum.json").read_text(encoding="utf-8"))
    summary = json.loads((src / "data_summary.json").read_text(encoding="utf-8"))

    from field_contract import ITEM_FIELDS, NODE_FIELDS, STUDENT_FIELDS  # noqa: E402

    students_rows = _read_csv(_find(csv_dir, ["模拟学生.csv", "students.csv"]), STUDENT_FIELDS)
    items_rows = _read_csv(_find(csv_dir, ["题目与材料.csv", "items.csv"]), ITEM_FIELDS)
    nodes_rows = _read_csv(_find(csv_dir, ["测验节点.csv", "nodes.csv"]), NODE_FIELDS)

    # 班级：官方编号就是展示名（不再用演示班名）
    class_ids = sorted({s.get("class_id", "") for s in students_rows if s.get("class_id")})
    classes = [
        {"id": cid, "name": cid, "color": "blue" if i == 0 else "teal"}
        for i, cid in enumerate(class_ids)
    ]

    # 主题 + 测验节点
    theme_ids = sorted({c.get("theme_id", "") for c in curriculum if c.get("theme_id")})
    topics = [
        {"id": t, "name": THEME_LABELS.get(t, t),
         "description": "按官方课程要求表的观察指标整理"}
        for t in theme_ids
    ]
    quizzes = [
        # 显示名用节点编号（简短可选）；官方「说明」列单独放 description，不占选择器
        {"id": n.get("node_id", ""), "name": n.get("node_id", ""),
         "description": n.get("name", ""),
         "date": n.get("date", ""),
         "topicIds": [x.strip() for x in n.get("theme_ids", "").replace("、", ",").replace("|", ",").split(",") if x.strip()],
         "availableItems": [x.strip() for x in n.get("available_items", "").replace("、", ",").split(",") if x.strip()]}
        for n in nodes_rows
    ]

    students = [
        {"id": s.get("student_id", ""), "name": s.get("name") or s.get("student_id", ""),
         "classId": s.get("class_id", "")}
        for s in students_rows
    ]

    # 考点 = 指标 × 测验节点（同一次测验下比较两班；页面测验选择器按 quizId 过滤）
    ind_meta = {c.get("indicator_id"): c for c in curriculum}
    node_ids = [n.get("node_id", "") for n in nodes_rows if n.get("node_id")]
    student_class = {s.get("student_id", ""): s.get("class_id", "") for s in students_rows}
    item_ind = {i.get("item_id", ""): i.get("indicator_id", "") for i in items_rows}
    wrong_by: Dict[tuple, set] = defaultdict(set)
    # 逐题聚合（已评分、未隔离、未超范围）：node+ind+class+item
    breakdown: Dict[tuple, dict] = {}
    oos_count: Dict[tuple, int] = defaultdict(int)
    for ev in evidence:
        iid = ev.get("item_id", "")
        ind = item_ind.get(iid, "")
        cid = student_class.get(ev.get("student_id", ""), "")
        nid = ev.get("node_id", "")
        if ev.get("out_of_scope"):
            oos_count[(nid, ind, cid)] += 1
        if ev.get("status") != "有效得分" or ev.get("quarantined") or ev.get("out_of_scope"):
            continue
        if not ind or ev.get("score") is None or ev.get("full_score") is None:
            continue
        if ev["score"] < ev["full_score"]:
            wrong_by[(nid, ind, cid)].add(iid)
        b = breakdown.setdefault((nid, ind, cid, iid), {
            "item_id": iid, "score": 0.0, "full_score": 0.0,
            "event_count": 0, "_students": set(),
        })
        b["score"] += ev["score"]
        b["full_score"] += ev["full_score"]
        b["event_count"] += 1
        b["_students"].add(ev.get("student_id", ""))

    def item_breakdown(nid: str, ind: str, cid: str) -> List[dict]:
        rows = [b for (n, i, c, _), b in breakdown.items() if (n, i, c) == (nid, ind, cid)]
        rows.sort(key=lambda b: b["item_id"])
        return [{"item_id": b["item_id"], "score": b["score"], "full_score": b["full_score"],
                 "event_count": b["event_count"], "observed_students": len(b["_students"])}
                for b in rows]

    # 直接用 pipeline 已算好的 class_analysis（含隔离/状态），不再二次聚合
    class_by = {(r["node_id"], r["indicator_id"], r["class_id"]): r for r in class_rows}
    kps = []
    for nid in node_ids:
        for ind in sorted(ind_meta):
            meta = ind_meta[ind]
            stats = []
            for cid in class_ids:
                r = class_by.get((nid, ind, cid))
                if not r or r["event_count"] == 0:
                    stats.append({"classId": cid, "scoreRate": None, "validAttempts": 0,
                                  "status": "本次未测", "topWrongQuestionIds": [],
                                  "itemBreakdown": item_breakdown(nid, ind, cid),
                                  "outOfScopeEvents": oos_count.get((nid, ind, cid), 0)})
                    continue
                st = r["status"]
                # pipeline 状态 → 页面状态（页面只认 正常/本次未测/记录不足/真实零分）
                page_status = st if st in ("记录不足", "真实零分") else "正常"
                stats.append({
                    "classId": cid, "scoreRate": r["score_rate"],
                    "validAttempts": r["event_count"], "status": page_status,
                    "topWrongQuestionIds": sorted(wrong_by.get((nid, ind, cid), set())),
                    "score": r["score"], "maxScore": r["max_score"],
                    "itemCount": r["item_count"],
                    "observedStudents": r["observed_students"], "classSize": r["class_size"],
                    "pendingEvents": r.get("pending_events", 0),
                    "unansweredEvents": r.get("unanswered_events", 0),
                    "quarantinedEvents": r.get("quarantined_events", 0),
                    "itemBreakdown": item_breakdown(nid, ind, cid),
                    "outOfScopeEvents": oos_count.get((nid, ind, cid), 0),
                })
            kps.append({
                "id": f"{nid}-{ind}", "quizId": nid, "indicatorId": ind,
                "topicId": meta.get("theme_id", ""),
                "name": f"{ind} · {meta.get('requirement', '')}"[:40], "stats": stats,
            })

    questions = []
    # 参考解析 / 参考答案来自官方台账原列（别名表未收录，按原始表头读取，不编造）
    raw_items = _read_csv(_find(csv_dir, ["题目与材料.csv", "items.csv"]))
    ref_by_id = {r.get("材料编号") or r.get("题目编号") or r.get("item_id", ""): r for r in raw_items}
    for it in items_rows:
        ref = ref_by_id.get(it.get("item_id", ""), {})
        questions.append({
            "id": it.get("item_id", ""), "kpId": it.get("indicator_id", ""),
            "stem": it.get("stem", ""),
            "fullScore": float(it.get("full_score") or 0),
            "sourceLocation": it.get("source_location", ""),
            "questionType": it.get("question_type", ""),
            "materialType": it.get("material_type", ""),
            "referenceAnalysis": (ref.get("参考解析") or "").strip(),
            "referenceAnswer": (ref.get("参考答案") or "").strip(),
        })

    # 答卷：只把有 answer_text 的有效得分事件作为可展示答卷；无步骤/评分点时如实留空
    item_map = {i.get("item_id"): i for i in items_rows}
    node_by_id = {n.get("node_id"): n for n in nodes_rows}
    answers = []
    for ev in evidence:
        # 隔离/超范围事件不作为可展示答卷：页面按状态提示，右栏仍给诊断与补测任务
        if ev.get("status") != "有效得分" or ev.get("quarantined") or ev.get("out_of_scope"):
            continue
        item = item_map.get(ev.get("item_id"))
        if not item:
            continue
        steps = _parse_steps(ev.get("answer_text", ""))
        rubric = _parse_rubric(ev.get("rubric_scores", ""), item.get("rubric_points", ""))
        answers.append({
            "id": f"ANS-{ev['event_id']}", "studentId": ev["student_id"],
            "questionId": ev["item_id"], "quizId": ev["node_id"],
            "steps": [{"step": i + 1, "text": s} for i, s in enumerate(steps)],
            "score": ev["score"], "fullScore": ev["full_score"],
            "scoringPoints": rubric,
            # 诊断与建议统一来自 analysis.py 的结构化结果（页面右侧读取），
            # 这里不再放占位文案，避免与正式诊断冲突。
            "diagnosis": "",
            "nextStep": "",
            "sourceRecordId": ev["record_id"], "sourceVersion": ev["version"],
            "finalAnswer": ev.get("final_answer", ""),
            "stepsUnavailable": len(steps) == 0,
            "rubricUnavailable": len(rubric) == 0,
            "superseded": ev.get("superseded", []),
        })

    manifest = {
        "mode": "real", "source": source_label,
        "generated_at": summary["generated_at"],
        "counts": summary["counts"],
        "reconciliation": summary.get("reconciliation", {}),
        "note": (
            "source=official 为官方赛题数据（经本地私有目录 + 受控接口读取）；"
            "source=sample 为自造虚构 CSV，仅验证链路。"
        ),
    }

    # 全量事件状态索引：含待评分/未作答/隔离/超范围，供答卷页在「指定测验无有效作答」时
    # 如实显示状态，而不是跨测验拿另一场记录补上。
    events = [{
        "studentId": ev.get("student_id", ""), "questionId": ev.get("item_id", ""),
        "quizId": ev.get("node_id", ""), "status": ev.get("status", ""),
        "recordId": ev.get("record_id", ""), "version": ev.get("version", ""),
        "score": ev.get("score"), "fullScore": ev.get("full_score"),
        "quarantined": bool(ev.get("quarantined")),
        "outOfScope": bool(ev.get("out_of_scope")),
    } for ev in evidence]

    # 异常清单：分数越界隔离 / 超出测验范围（保留原始记录，排除主统计，待人工核对）
    anomalies = []
    for ev in evidence:
        kinds = []
        if ev.get("quarantined"):
            kinds.append(("quarantined", "分数越界待核"))
        if ev.get("out_of_scope"):
            kinds.append(("out_of_scope", "超出测验范围待核"))
        for kind, reason in kinds:
            anomalies.append({
                "record_id": ev.get("record_id", ""), "event_id": ev.get("event_id", ""),
                "student_id": ev.get("student_id", ""),
                "class_id": student_class.get(ev.get("student_id", ""), ""),
                "item_id": ev.get("item_id", ""), "node_id": ev.get("node_id", ""),
                "indicator_id": item_ind.get(ev.get("item_id", ""), ""),
                "kind": kind, "score": ev.get("score"), "full_score": ev.get("full_score"),
                "status": ev.get("status", ""), "reason": reason,
            })
    anomalies.sort(key=lambda a: (a["record_id"], a["kind"]))

    payload = {
        "manifest.json": manifest,
        "classes.json": classes, "topics.json": topics, "quizzes.json": quizzes,
        "students.json": students, "kps.json": kps,
        "questions.json": questions, "answers.json": answers, "events.json": events,
        "anomalies.json": anomalies,
    }
    # 官方模式：附上课标映射 / 诊断 / 案例册（来自 analysis.py）
    analysis_dir = src / "analysis"
    for name in ("curriculum_map.json", "diagnoses.json", "case_book.json"):
        p = analysis_dir / name
        if p.is_file():
            payload[name] = json.loads(p.read_text(encoding="utf-8"))
    # 自造证据回放（replay.py 生成，明确标注非官方结果；不存在则不附带）
    replay = src / "replay.json"
    if replay.is_file():
        payload["replay.json"] = json.loads(replay.read_text(encoding="utf-8"))
    # 班级讲评方案（review_plan.py 生成；不存在则不附带）
    rp = src / "review_plans.json"
    if rp.is_file():
        payload["review_plans.json"] = json.loads(rp.read_text(encoding="utf-8"))
    for name, data in payload.items():
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[bundle] {out / name} ({len(data) if isinstance(data, list) else 'manifest'})")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="把 pipeline 输出转成页面 bundle")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--src", required=True, help="pipeline 输出目录")
    p.add_argument("--csv-dir", required=True, help="5 张 CSV 所在目录")
    p.add_argument("--out", required=True, help="前端 public/competition-data 目录")
    p.add_argument("--source", default="sample", choices=["sample", "official"])
    args = ap.parse_args()
    build(Path(args.src), Path(args.csv_dir), Path(args.out), args.source)


if __name__ == "__main__":
    main()
