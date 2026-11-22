"""
智慧学伴比赛版 · 可重复运行的数据读取 / 校验 / 统计入口
============================================================
用法（全部在本地，不进 Git、不联网、不调大模型）：

  # 1) 用自造样例跑通（当前官方数据未到位时用这个）
  python competition_pipeline/pipeline.py self-test

  # 2) 官方数据到位后，指向解压目录
  python competition_pipeline/pipeline.py run --data-dir <官方CSV目录> --out-dir <输出目录>
  # 若真实表头不在别名表里，加 --mapping competition_pipeline/mapping.json

口径（来自四人开发方案，非虚构）：
- 作答事件按 event_id 归并，保留 version 最大的一条；旧版本留链接。
- 完全相同的行 = 重复，去重；同 event 同 version 内容冲突 = 待核并隔离。
- 状态分开：有效得分（含 0）、待评分、未作答、本次未测（节点未覆盖）、缺记录。
- 得分率 = 得分和 / 满分和，只取有效、已评分事件；分母 0 输出 null。
- 全部统计来自 CSV 行，不硬编码演示题号 / 演示得分。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from field_contract import (  # noqa: E402
    FILE_HINTS, TABLES, LoadIssue, LoadResult, STATUS_ALIASES,
    STATUS_NOT_COVERED, STATUS_NO_RECORD, STATUS_PENDING, STATUS_SCORED,
    STATUS_UNANSWERED, normalize_status,
)

HERE = Path(__file__).resolve().parent
DEFAULT_SAMPLES = HERE / "samples"
DEFAULT_OUT = Path("D:/Document/langchain-rag-work/competition-data")

INSUFFICIENT_EVENTS = 3  # 少于该数量的有效事件 → 标记记录不足（配置化，非硬编码题目）


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def _find_file(data_dir: Path, table: str, mapping: dict) -> Optional[Path]:
    custom = mapping.get("files", {}).get(table)
    candidates = [custom] if custom else FILE_HINTS[table]
    for name in candidates:
        p = data_dir / name
        if p.exists():
            return p
    # 兜底：目录内中文名包含关键字的 csv
    key = table[:2]
    for p in sorted(data_dir.glob("*.csv")):
        if key in p.name:
            return p
    return None


def _resolve_columns(header: List[str], fields: dict, mapping: dict) -> Tuple[Dict[str, str], List[str]]:
    """返回 {规范字段: 实际表头} 与找不到的字段列表。"""
    override = mapping.get("columns", {})
    colmap: Dict[str, str] = {}
    missing: List[str] = []
    for canonical, aliases in fields.items():
        candidates = override.get(canonical, aliases)
        if isinstance(candidates, str):
            candidates = [candidates]
        hit = next((c for c in candidates if c in header), None)
        if hit:
            colmap[canonical] = hit
        else:
            missing.append(canonical)
    return colmap, missing


def load_all(data_dir: Path, mapping: dict) -> LoadResult:
    result = LoadResult()
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise FileNotFoundError(f"数据目录不存在：{data_dir}")

    for table, (fields, required, optional) in TABLES.items():
        path = _find_file(data_dir, table, mapping)
        if path is None:
            result.missing_files.append(table)
            result.issues.append(LoadIssue(table, "", "", "", "缺文件", "停止统计并报告",
                                           f"未在 {data_dir} 找到 {table} 的 CSV"))
            continue
        result.file_paths[table] = str(path)
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            colmap, missing = _resolve_columns(header, fields, mapping)
            result.column_map[table] = colmap
            for canon in missing:
                if canon in optional:
                    result.issues.append(LoadIssue(table, "", "", "", "可选字段缺失", "记录并继续",
                                                   f"找不到可选字段 {canon}（别名：{fields[canon]}）"))
                else:
                    result.issues.append(LoadIssue(table, "", "", "", "缺字段", "停止统计并报告",
                                                   f"找不到规范字段 {canon}（别名：{fields[canon]}）"))
            rows = []
            for i, raw in enumerate(reader, start=2):
                row = {canon: (raw.get(actual) or "").strip() for canon, actual in colmap.items()}
                row["__line"] = i
                rows.append(row)
            result.rows[table] = rows
    return result


# ---------------------------------------------------------------------------
# 校验 + 版本归并
# ---------------------------------------------------------------------------

def _num(value: str) -> Optional[float]:
    value = (value or "").strip()
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def validate(loaded: LoadResult) -> LoadResult:
    students = {r.get("student_id") for r in loaded.rows.get("模拟学生", [])}
    items = {r.get("item_id"): r for r in loaded.rows.get("题目与材料", [])}
    nodes = {r.get("node_id"): r for r in loaded.rows.get("测验节点", [])}

    for r in loaded.rows.get("作答记录", []):
        rid, eid, ver = r.get("record_id", ""), r.get("event_id", ""), r.get("version", "")
        if r.get("student_id") and r["student_id"] not in students:
            loaded.issues.append(LoadIssue("作答记录", rid, eid, ver, "学生不存在", "隔离",
                                           f"student_id={r['student_id']}"))
        if r.get("item_id") and r["item_id"] not in items:
            loaded.issues.append(LoadIssue("作答记录", rid, eid, ver, "题目不存在", "隔离",
                                           f"item_id={r['item_id']}"))
        # 作答记录自带的主题编号可能与题目表不一致（官方包 DR0081 即为一例）。
        # 归组以题目表的指标编号为准，原始记录不改写；把口径写进质量报告供评委复核。
        it = items.get(r.get("item_id", ""))
        if it and r.get("theme_id") and it.get("theme_id") and r["theme_id"] != it["theme_id"]:
            loaded.issues.append(LoadIssue(
                "作答记录", rid, eid, ver, "主题字段冲突", "按题目与材料表的指标编号归组；原始记录不改写",
                f"作答记录主题={r['theme_id']}，题目表主题={it['theme_id']}，"
                f"指标={it.get('indicator_id', '')}；得分聚合按题目表口径"))
        if r.get("node_id") and r["node_id"] not in nodes:
            loaded.issues.append(LoadIssue("作答记录", rid, eid, ver, "节点不存在", "隔离",
                                           f"node_id={r['node_id']}"))
        s, fs = _num(r.get("score")), _num(r.get("full_score"))
        st = normalize_status(r.get("status"))
        if st == STATUS_SCORED:
            if s is None or fs is None:
                loaded.issues.append(LoadIssue("作答记录", rid, eid, ver, "已评分缺分数", "隔离待核",
                                               f"score={r.get('score')} full_score={r.get('full_score')}"))
            elif s < 0 or fs <= 0 or s > fs:
                # 保留在状态计数（基准 790 已评分不变），但标记后不进得分聚合。
                r["_invalid_score"] = "1"
                loaded.issues.append(LoadIssue("作答记录", rid, eid, ver, "分数越界", "隔离待核·不进得分聚合",
                                               f"score={s} full_score={fs}；保留状态计数，得分聚合已排除"))
        elif st not in (STATUS_PENDING, STATUS_UNANSWERED, STATUS_NOT_COVERED):
            loaded.issues.append(LoadIssue("作答记录", rid, eid, ver, "未知状态", "待核",
                                           f"status={r.get('status')}"))
    return loaded


def _version_key(v: str) -> Tuple[int, str]:
    n = _num(v)
    return (int(n), "") if n is not None else (-1, v)


def merge_events(loaded: LoadResult) -> Dict[str, dict]:
    """按 event_id 保留最大 version；重复去重，同版本冲突隔离。"""
    groups: Dict[str, List[dict]] = defaultdict(list)
    for r in loaded.rows.get("作答记录", []):
        if not r.get("event_id"):
            loaded.issues.append(LoadIssue("作答记录", r.get("record_id", ""), "", r.get("version", ""),
                                           "缺事件编号", "隔离", "event_id 为空"))
            continue
        groups[r["event_id"]].append(r)

    kept: Dict[str, dict] = {}
    for eid, rows in groups.items():
        best_v = max(_version_key(r.get("version", "")) for r in rows)
        latest = [r for r in rows if _version_key(r.get("version", "")) == best_v]

        payload = lambda r: tuple(  # noqa: E731  参与比较的业务字段
            r.get(k, "") for k in ("student_id", "item_id", "node_id", "score", "full_score",
                                   "status", "answer_text", "rubric_scores")
        )
        distinct = {payload(r) for r in latest}
        if len(distinct) > 1:
            for r in latest:
                loaded.issues.append(LoadIssue("作答记录", r.get("record_id", ""), eid,
                                               r.get("version", ""), "同版本冲突", "隔离待核",
                                               "同 event 同 version 内容不一致"))
            continue  # 整组隔离，不进统计
        chosen_row = latest[0]
        chosen = dict(chosen_row)
        for r in latest[1:]:
            loaded.issues.append(LoadIssue("作答记录", r.get("record_id", ""), eid,
                                           r.get("version", ""), "完全重复", "去重",
                                           "与保留行完全一致"))
        chosen["_superseded"] = [
            {"record_id": r.get("record_id", ""), "version": r.get("version", "")}
            for r in rows if r is not chosen_row
        ]
        # 修订版本是「重复记录处理」的一部分：逐条写明被哪一条保留行取代，
        # 否则质量报告里看不到 806→799 的归并依据。
        for r in rows:
            if r is chosen_row or _version_key(r.get("version", "")) >= best_v:
                continue
            loaded.issues.append(LoadIssue(
                "作答记录", r.get("record_id", ""), eid, r.get("version", ""), "修订版本归并",
                "保留最大 version；旧版本留 _superseded 链接，不进统计",
                f"同事件 {eid} 的旧版本；保留行 {chosen_row.get('record_id', '')} "
                f"v{chosen_row.get('version', '')}"))
        kept[eid] = chosen
    return kept


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------

def _split_ids(text: str) -> set:
    """官方用「、」分隔，自造样例用「,」或「|」；三者都兼容。"""
    normalized = (text or "").replace("、", ",").replace("|", ",")
    return {x.strip() for x in normalized.split(",") if x.strip()}


def _node_themes(node: dict) -> set:
    return _split_ids(node.get("theme_ids", ""))


def _node_date(node: dict) -> Optional[date]:
    """解析「模拟测验日期」（ISO 格式）；无法解析返回 None。"""
    text = (node.get("date") or "").strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def node_order(nodes: List[dict]) -> List[str]:
    """按模拟测验日期升序的节点编号；缺日期的排在最后（按编号稳定排序）。"""
    return [n.get("node_id", "") for n in sorted(
        nodes, key=lambda n: (_node_date(n) or date.max, n.get("node_id", "")))]


def _node_indicators(node: dict, curriculum: List[dict], items: Dict[str, dict]) -> set:
    """节点覆盖的指标：优先用官方「可用题目编号」（题目→指标）；样例回退到课程要求.node_ids。"""
    available = _split_ids(node.get("available_items", ""))
    if available:
        return {items[i].get("indicator_id", "") for i in available if i in items} - {""}
    nid = node.get("node_id", "")
    return {
        c.get("indicator_id") for c in curriculum
        if nid in _split_ids(c.get("node_ids", ""))
    } - {""}


def compute(loaded: LoadResult, events: Dict[str, dict], insufficient: int = INSUFFICIENT_EVENTS) -> dict:
    students = loaded.rows.get("模拟学生", [])
    items = {r.get("item_id"): r for r in loaded.rows.get("题目与材料", [])}
    nodes = loaded.rows.get("测验节点", [])
    curriculum = loaded.rows.get("课程要求", [])

    class_size: Dict[str, int] = defaultdict(int)
    for s in students:
        class_size[s.get("class_id", "")] += 1

    # 每个节点覆盖的指标 / 主题（节点未覆盖 → 本次未测）
    node_ind = {n["node_id"]: _node_indicators(n, curriculum, items) for n in nodes}
    node_theme = {n["node_id"]: _node_themes(n) for n in nodes}
    # 节点「可用题目编号」：超出该集合的作答 = 超出测验范围（保留原始记录，排除主统计）
    node_available = {n["node_id"]: _split_ids(n.get("available_items", "")) for n in nodes}

    # 累计桶：(class_id, node_id, indicator_id) -> 统计
    bucket: Dict[Tuple[str, str, str], dict] = {}
    evidence: List[dict] = []
    out_of_scope_events = 0
    quarantined_events = 0
    included_scored_events = 0

    for eid, r in events.items():
        sid, iid, nid = r.get("student_id", ""), r.get("item_id", ""), r.get("node_id", "")
        student = next((s for s in students if s.get("student_id") == sid), None)
        item = items.get(iid)
        if not student or not item or nid not in node_theme:
            continue
        cid, ind = student.get("class_id", ""), item.get("indicator_id", "")
        st = normalize_status(r.get("status"))
        s, fs = _num(r.get("score")), _num(r.get("full_score"))
        invalid = r.get("_invalid_score") == "1"
        avail = node_available.get(nid, set())
        out_of_scope = bool(avail) and iid not in avail
        if out_of_scope:
            out_of_scope_events += 1
            loaded.issues.append(LoadIssue(
                "作答记录", r.get("record_id", ""), eid, r.get("version", ""),
                "超出测验范围，待核", "保留原始记录，排除主统计与正式诊断有效证据",
                f"item_id={iid} 不在 {nid}「可用题目编号」内；保留状态计数与证据索引"))
        if st == STATUS_SCORED:
            if invalid:
                quarantined_events += 1
            elif not out_of_scope:
                included_scored_events += 1

        evidence.append({
            "evidence_id": f"EV-{eid}", "event_id": eid,
            "record_id": r.get("record_id", ""), "version": r.get("version", ""),
            "student_id": sid, "item_id": iid, "node_id": nid,
            "status": st, "score": s, "full_score": fs,
            "answer_text": r.get("answer_text", ""),
            "final_answer": r.get("final_answer", ""),
            "rubric_scores": r.get("rubric_scores", ""),
            "source_location": item.get("source_location", ""),
            "superseded": r.get("_superseded", []),
            "quarantined": invalid,
            "out_of_scope": out_of_scope,
        })
        if out_of_scope:
            continue  # 不进班级聚合桶与有效证据

        key = (cid, nid, ind)
        b = bucket.setdefault(key, {
            "class_id": cid, "node_id": nid, "indicator_id": ind,
            "score_sum": 0.0, "full_sum": 0.0, "valid_events": 0,
            "scored_events": 0, "pending_events": 0, "unanswered_events": 0,
            "items": set(), "students": set(),
        })
        b["items"].add(iid)
        b["students"].add(sid)
        if st == STATUS_SCORED and invalid:
            b["quarantined_events"] = b.get("quarantined_events", 0) + 1
        elif st == STATUS_SCORED and s is not None and fs is not None and fs > 0:
            b["scored_events"] += 1
            b["valid_events"] += 1
            b["score_sum"] += s
            b["full_sum"] += fs
        elif st == STATUS_PENDING:
            b["pending_events"] += 1
        elif st == STATUS_UNANSWERED:
            b["unanswered_events"] += 1

    class_rows = []
    for (cid, nid, ind), b in bucket.items():
        covered = ind in node_ind.get(nid, set()) and b["valid_events"] > 0
        if b["valid_events"] == 0:
            status = STATUS_NOT_COVERED
        elif b["valid_events"] < insufficient:
            status = "记录不足"
        elif b["full_sum"] == 0:
            status = "分母为零"
        elif b["score_sum"] == 0:
            status = "真实零分"
        else:
            status = "有效"
        class_rows.append({
            "class_id": cid, "node_id": nid, "indicator_id": ind,
            "score": b["score_sum"], "max_score": b["full_sum"],
            "score_rate": None if b["full_sum"] == 0 else round(b["score_sum"] / b["full_sum"], 4),
            "event_count": b["valid_events"], "item_count": len(b["items"]),
            "observed_students": len(b["students"]), "class_size": class_size.get(cid, 0),
            "status": status,
            "pending_events": b["pending_events"], "unanswered_events": b["unanswered_events"],
            "quarantined_events": b.get("quarantined_events", 0),
        })
    class_rows.sort(key=lambda x: (x["class_id"], x["node_id"], x["indicator_id"]))

    # 对账：原始行状态计数（保留超范围/隔离记录）与归并后口径
    raw_status = {STATUS_SCORED: 0, STATUS_PENDING: 0, STATUS_UNANSWERED: 0}
    for r in loaded.rows.get("作答记录", []):
        st = normalize_status(r.get("status"))
        if st in raw_status:
            raw_status[st] += 1
    reconciliation = {
        "raw_rows": len(loaded.rows.get("作答记录", [])),
        "merged_events": len(events),
        "raw_status": {"已评分": raw_status[STATUS_SCORED],
                       "待评分": raw_status[STATUS_PENDING],
                       "未作答": raw_status[STATUS_UNANSWERED]},
        "included_scored_events": included_scored_events,
        "quarantined_events": quarantined_events,
        "out_of_scope_events": out_of_scope_events,
    }

    # 可信期望集合（供 export_package.verify 独立核对主键与对账）
    item_indicator = {iid: it.get("indicator_id", "") for iid, it in items.items()
                      if it.get("indicator_id", "")}
    diagnosis_keys = sorted({
        (e["student_id"], e["node_id"], item_indicator[e["item_id"]])
        for e in evidence
        if not e["out_of_scope"] and e["item_id"] in item_indicator
    })
    scope_index = {
        "item_indicator": item_indicator,
        "node_available": {nid: sorted(avail) for nid, avail in node_available.items()},
        "node_order": node_order(nodes),
        "student_class": {s.get("student_id", ""): s.get("class_id", "") for s in students},
        "expected_class_keys": [[r["class_id"], r["node_id"], r["indicator_id"]] for r in class_rows],
        "expected_diagnosis_keys": [list(k) for k in diagnosis_keys],
        "reconciliation": reconciliation,
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_files": loaded.file_paths,
        "counts": {
            "raw_answer_rows": len(loaded.rows.get("作答记录", [])),
            "merged_events": len(events),
            "students": len(students), "items": len(items), "nodes": len(nodes),
            "curriculum_indicators": len(curriculum),
        },
        "reconciliation": reconciliation,
        "scope_index": scope_index,
        "class_analysis": class_rows,
        "evidence_index": evidence,
        "curriculum": [
            {k: c.get(k, "") for k in ("indicator_id", "theme_id", "node_ids", "requirement",
                                        "material_ids", "sources")}
            for c in curriculum
        ],
        "class_size": dict(class_size),
    }


def _append_quality_summary(loaded: LoadResult, result: dict, events: Dict[str, dict]) -> None:
    """把「重复/修订记录、状态处置、纳入统计口径」写成可核对的质量报告说明行。

    赛题要求说明缺失、异常及重复记录等情况的处理方式。逐条异常已在 issues 里，
    这里补三条汇总口径，避免评委只看到零散异常却看不到 806→799→787 的处置依据。
    只影响 data_quality.csv，不改变任何统计结果。
    """
    rec = result.get("reconciliation", {})
    raw = result["counts"].get("raw_answer_rows", 0)
    merged = result["counts"].get("merged_events", 0)
    raw_status = rec.get("raw_status", {})          # 原始 806 行的状态分布
    merged_status = Counter(normalize_status(e.get("status", "")) for e in events.values())
    scored = merged_status.get(STATUS_SCORED, 0)     # 归并后 799 事件的已评分数
    if raw > merged:
        loaded.issues.append(LoadIssue(
            "作答记录", "", "", "", "版本归并汇总", "按 event_id 保留最大 version",
            f"原始 {raw} 行 → 归并后 {merged} 个事件；{raw - merged} 条为同事件旧版本，逐条见上"))
    loaded.issues.append(LoadIssue(
        "作答记录", "", "", "", "状态处置汇总",
        "只把已评分、分数有效且题目在本节点范围内的记录纳入得分聚合",
        f"原始 {raw} 行状态：已评分 {raw_status.get('已评分', 0)} / 待评分 "
        f"{raw_status.get('待评分', 0)} / 未作答 {raw_status.get('未作答', 0)}；"
        f"归并后 {merged} 事件状态：已评分 {scored} / 待评分 "
        f"{merged_status.get(STATUS_PENDING, 0)} / 未作答 "
        f"{merged_status.get(STATUS_UNANSWERED, 0)}；后两类不计入得分率，也不解释为未掌握"))
    # 得分列与评分点得分之和不一致（官方 DR0133 等）：保留赛方给的得分并逐条标注，
    # 不擅自修正、不隔离，避免把官方评分结果改掉；但评委必须能看见这处矛盾。
    for e in events.values():
        if normalize_status(e.get("status", "")) != STATUS_SCORED:
            continue
        pts = [float(x) for x in re.findall(r"[=＝]\s*(-?\d+(?:\.\d+)?)", e.get("rubric_scores") or "")]
        s = _num(e.get("score", ""))
        if s is not None and pts and abs(sum(pts) - s) > 1e-9:
            loaded.issues.append(LoadIssue(
                "作答记录", e.get("record_id", ""), e.get("event_id", ""),
                e.get("version", ""), "得分与评分点之和不一致",
                "保留官方得分并标注，待赛方确认（不擅自修正）",
                f"score={s:g} 评分点和={sum(pts):g}；评分点={str(e.get('rubric_scores', ''))[:60]}"))

    loaded.issues.append(LoadIssue(
        "作答记录", "", "", "", "纳入统计汇总", "保留状态计数；隔离项排除聚合，有效零分计入",
        f"纳入统计 {rec.get('included_scored_events', 0)} = 归并后已评分 {scored} − 分数越界隔离 "
        f"{rec.get('quarantined_events', 0)} − 超出测验范围 {rec.get('out_of_scope_events', 0)}；"
        "有效零分是真实作答，不是缺记录"))


def write_outputs(result: dict, loaded: LoadResult, out_dir: Path) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "class_analysis.json").write_text(
        json.dumps(result["class_analysis"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "evidence_index.json").write_text(
        json.dumps(result["evidence_index"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "curriculum.json").write_text(
        json.dumps(result["curriculum"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "data_summary.json").write_text(
        json.dumps({k: result[k] for k in ("generated_at", "source_files", "counts",
                                           "class_size", "reconciliation")},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "scope_index.json").write_text(
        json.dumps(result["scope_index"], ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "data_quality.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["table", "record_id", "event_id", "version", "issue", "action", "detail"])
        for i in loaded.issues:
            w.writerow([i.table, i.record_id, i.event_id, i.version, i.issue, i.action, i.detail])
    print(f"[输出] {out_dir}")
    print(f"[计数] {json.dumps(result['counts'], ensure_ascii=False)}")
    print(f"[对账] {json.dumps(result['reconciliation'], ensure_ascii=False)}")
    print(f"[质量] 问题 {len(loaded.issues)} 条 → data_quality.csv")


def run(data_dir: Path, out_dir: Path, mapping: dict = None, insufficient: int = INSUFFICIENT_EVENTS) -> dict:
    mapping = mapping or {}
    loaded = load_all(data_dir, mapping)
    if loaded.missing_files:
        raise RuntimeError(f"缺少必要 CSV：{', '.join(loaded.missing_files)}（目录：{data_dir}）")
    fatal = [i for i in loaded.issues if i.action == "停止统计并报告"]
    if fatal:
        raise RuntimeError("字段校验未通过：" + "; ".join(f"{i.table}/{i.detail}" for i in fatal))
    validate(loaded)
    events = merge_events(loaded)
    result = compute(loaded, events, insufficient=insufficient)
    _append_quality_summary(loaded, result, events)
    write_outputs(result, loaded, out_dir)
    return result


def self_test() -> None:
    """用自造样例跑通全链路，并断言一组独立核算的值。"""
    out = DEFAULT_OUT / "self-test"
    result = run(DEFAULT_SAMPLES, out)

    by_key = {(r["class_id"], r["node_id"], r["indicator_id"]): r
              for r in result["class_analysis"]}

    # 独立核算（来自 samples/作答记录.csv 自造样例，非官方数据）
    checks = [
        ("归并事件数=9", result["counts"]["merged_events"] == 9),
        ("原始行数=12", result["counts"]["raw_answer_rows"] == 12),
        ("DC01/N01/指标A 有效事件=3",
         by_key.get(("DC01", "N01", "IND-A"), {}).get("event_count") == 3),
        ("DC01/N01/指标A 得分和=7 满分和=12 得分率≈0.5833",
         by_key.get(("DC01", "N01", "IND-A"), {}).get("score") == 7
         and by_key.get(("DC01", "N01", "IND-A"), {}).get("max_score") == 12
         and by_key.get(("DC01", "N01", "IND-A"), {}).get("score_rate") == 0.5833),
        ("DC02/N01/指标A 有效零分保留（score=0, status=真实零分）",
         by_key.get(("DC02", "N01", "IND-A"), {}).get("score") == 0
         and by_key.get(("DC02", "N01", "IND-A"), {}).get("status") == "真实零分"),
        ("DC01/N01/指标B 仅1条有效→记录不足",
         by_key.get(("DC01", "N01", "IND-B"), {}).get("status") == "记录不足"),
    ]
    for name, ok in checks:
        print(("PASS | " if ok else "FAIL | ") + name)
    if not all(ok for _, ok in checks):
        raise SystemExit("self-test 未通过")
    print("[self-test] 全部通过")


def main() -> None:
    ap = argparse.ArgumentParser(description="智慧学伴比赛版 数据读取/校验/统计")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("self-test", help="用自造样例跑通并断言")
    p = sub.add_parser("run", help="读取官方 CSV 目录并输出统计")
    p.add_argument("--data-dir", required=True, help="包含 5 张 CSV 的目录")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT), help="输出目录")
    p.add_argument("--mapping", help="字段映射覆盖 JSON（可选）")
    p.add_argument("--insufficient-events", type=int, default=INSUFFICIENT_EVENTS)
    args = ap.parse_args()

    if args.cmd == "self-test":
        self_test()
    else:
        mapping = {}
        if args.mapping:
            mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
        run(Path(args.data_dir), Path(args.out_dir), mapping, args.insufficient_events)


if __name__ == "__main__":
    main()
