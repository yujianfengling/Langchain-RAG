"""
智慧学伴比赛版 · 自造证据回放（隔离副本，用正式管线重算）
============================================================
目的：演示「证据改变后，诊断与建议怎么变」。全部计算复用 pipeline.py /
analysis.py 的正式规则；本脚本只负责：
  1) 把官方 5 张 CSV + 字段说明复制到隔离工作区（绝不写官方目录）；
  2) 在副本的 作答记录.csv 追加自造记录（明确标注非官方）；
  3) 在副本上跑 pipeline.run + analysis.run；
  4) 对比 --before（官方 analysis/diagnoses.json）与副本 after，输出 replay.json。

用法：
  python competition_pipeline/replay.py run \
    --csv-dir <官方CSV目录> --before <官方analysis目录> \
    --work <隔离工作区> --bundle-out <official-bundle目录>

replay.json 同时写入 --bundle-out 与 --work；bundle.py 若发现 src/replay.json
也会附带（本脚本不写 src，官方统计不受影响）。
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pipeline  # noqa: E402
import analysis  # noqa: E402

CSV_FILES = ["课程要求.csv", "题目与材料.csv", "模拟学生.csv", "测验节点.csv", "作答记录.csv", "字段说明.csv"]
LABEL = "自造回放（隔离副本用正式管线重算；非官方结果，不进入官方统计）"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _read_csv(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _copy_csvs(csv_dir: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name in CSV_FILES:
        src = csv_dir / name
        if not src.is_file():
            raise FileNotFoundError(f"官方 CSV 缺失：{src}")
        shutil.copy2(src, dest / name)
    return dest


def _append_answers(csv_copy: Path, new_rows: List[dict]) -> None:
    """按原表头追加行；只写副本。"""
    path = csv_copy / "作答记录.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = list(reader)
    for r in new_rows:
        missing = [k for k in header if k not in r]
        if missing:
            raise ValueError(f"自造行缺字段：{missing}")
        rows.append({k: r[k] for k in header})
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


def _snapshot(d: Optional[dict]) -> Optional[dict]:
    if d is None:
        return None
    res = d.get("resource") or {}
    return {
        "score": d.get("score"), "full_score": d.get("full_score"),
        "rate": d.get("rate"), "event_count": d.get("event_count"),
        "case": d.get("case"), "question_case": d.get("question_case"),
        "resource": {"material_id": res.get("material_id"), "type": res.get("type"),
                     "reason": res.get("reason")},
    }


def _find_diag(path: Path, key: tuple) -> Optional[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return next((d for d in data["diagnoses"]
                 if (d["student_id"], d["node_id"], d["indicator_id"]) == key), None)


def _rate_text(r) -> str:
    return "—" if r is None else f"{r:.2%}"


def _changes(before: dict, after: dict) -> List[str]:
    out = [
        f"分子/分母：{before['score']:g}/{before['full_score']:g} → {after['score']:g}/{after['full_score']:g}",
        f"得分率：{_rate_text(before['rate'])} → {_rate_text(after['rate'])}",
        f"有效记录数：{before['event_count']} → {after['event_count']}",
        f"考点判定：{before['case']} → {after['case']}",
    ]
    if before["question_case"] != after["question_case"]:
        out.append(f"本题判定：{before['question_case']} → {after['question_case']}")
    br, ar = before["resource"], after["resource"]
    if br["material_id"] != ar["material_id"] or br["type"] != ar["type"]:
        out.append(f"建议材料：{br['material_id']}（{br['type']}） → {ar['material_id']}（{ar['type']}）")
    else:
        out.append(f"建议材料/类型不变：{ar['material_id']}（{ar['type']}）")
    return out


def _wrong_answer(item: dict) -> str:
    """自造 0 分行的填答：选择题选一个非正确选项；填空题给一个不等于参考答案的值。"""
    ref = (item.get("参考答案") or "").strip()
    if item.get("题型") == "选择题":
        return "A" if ref != "A" else "B"
    return "0" if ref != "0" else "1"


def _rubric_line(item: dict, earned_full: bool) -> str:
    defs = analysis._rubric_defs(item.get("评分点", ""))
    if not defs:
        return ""
    return "；".join(f"{pid}={d['full']:g}" if earned_full else f"{pid}=0"
                     for pid, d in defs.items())


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------

def scenario_d15(csv_dir: Path, work: Path) -> dict:
    sid, nid, ind = "D15", "N02", "T03I2"
    scen_id = "d15-q044-zero"
    csv_copy = _copy_csvs(csv_dir, work / scen_id / "csv")
    answers = _read_csv(csv_dir / "作答记录.csv")
    base = next(r for r in answers if r["记录编号"] == "DR0343")
    print(f"[replay] 参照原行 DR0343：{ {k: base[k] for k in ('学生编号', '材料编号', '作答事件编号', '修订版本', '得分', '满分', '评分点得分')} }")
    new = {
        "记录编号": "DR9001", "学生编号": sid, "材料编号": "Q044", "主题编号": "T03",
        "节点编号": nid, "作答事件编号": base["作答事件编号"], "修订版本": "2",
        "模拟提交时间": "2026-06-18T10:00:00+08:00", "评分状态": "已评分",
        "得分": "0", "满分": base["满分"], "学生最终作答": "A", "解答过程": "",
        "评分点得分": "Q044P1=0",
    }
    _append_answers(csv_copy, [new])
    mutation = {
        "description": ("在副本 作答记录.csv 追加 DR9001：D15/N02/Q044 同一作答事件 "
                        f"{base['作答事件编号']} 的修订版本 2（原 DR0343 版本 1 得 1/1），"
                        "复核改分为 0/1（学生最终作答 A，Q044P1=0）；正式管线按最大版本归并。"),
        "added_records": [{"record_id": "DR9001", "event_id": base["作答事件编号"],
                           "version": "2", "score": 0, "full_score": float(base["满分"]),
                           "status": "已评分"}],
    }
    return {"id": scen_id, "label": "D15 复核改分：Q044 由满分改 0 分（版本 2 覆盖版本 1），T03I2 从表现较好变部分得分",
            "student_id": sid, "node_id": nid, "indicator_id": ind,
            "csv_copy": csv_copy, "mutation": mutation,
            "expect": {"score": 5, "full_score": 6, "event_count": 4, "case": "部分得分"}}


def scenario_d17(csv_dir: Path, work: Path) -> dict:
    sid, nid, ind = "D17", "N01", "T01I1"
    scen_id = "d17-fill-coverage"
    csv_copy = _copy_csvs(csv_dir, work / scen_id / "csv")
    items = _read_csv(csv_dir / "题目与材料.csv")
    nodes = _read_csv(csv_dir / "测验节点.csv")
    answers = _read_csv(csv_dir / "作答记录.csv")
    node = next(n for n in nodes if n["节点编号"] == nid)
    avail = set(analysis._split_ids(node["可用题目编号"]))
    done = {a["材料编号"] for a in answers if a["学生编号"] == sid}
    cands = [i for i in items
             if i["材料类型"] == "题目" and ind in analysis._split_ids(i["指标编号"])
             and i["材料编号"] in avail and i["材料编号"] not in done]
    cands.sort(key=lambda i: i["材料编号"])
    if len(cands) < 2:
        raise RuntimeError(f"D17 在 N01 可用的未做过 T01I1 题不足 2 道：{[c['材料编号'] for c in cands]}")
    picked = cands[:2]
    print(f"[replay] D17 未做过的 N01 可用 T01I1 题：{[c['材料编号'] for c in cands]}；选 {picked[0]['材料编号']}（满分）、{picked[1]['材料编号']}（0 分）")
    new_rows, added = [], []
    for k, (item, full) in enumerate(zip(picked, (True, False))):
        qid = item["材料编号"]
        eid = f"{sid}-{nid}-{qid}"
        score = float(item["满分"]) if full else 0.0
        row = {
            "记录编号": f"DR900{k + 2}", "学生编号": sid, "材料编号": qid,
            "主题编号": item["主题编号"], "节点编号": nid, "作答事件编号": eid,
            "修订版本": "1", "模拟提交时间": f"2026-01-21T10:0{k * 5}:00+08:00",
            "评分状态": "已评分", "得分": f"{score:g}", "满分": item["满分"],
            "学生最终作答": (item.get("参考答案") or "").strip() if full else _wrong_answer(item),
            "解答过程": "", "评分点得分": _rubric_line(item, full),
        }
        new_rows.append(row)
        added.append({"record_id": row["记录编号"], "event_id": eid, "version": "1",
                      "score": score, "full_score": float(item["满分"]), "status": "已评分"})
    _append_answers(csv_copy, new_rows)
    mutation = {
        "description": ("在副本 作答记录.csv 追加两条 D17/N01 的新作答事件（版本 1，已评分）："
                        f"{picked[0]['材料编号']} 得满分、{picked[1]['材料编号']} 得 0 分；"
                        "两题均为该生未做过、N01 可用的 T01I1 题，使有效记录从 2 条增至 4 条（≥3 可重新判断）。"),
        "added_records": added,
    }
    return {"id": scen_id, "label": "D17 补齐覆盖：新增两条 T01I1 有效记录，从记录不足变为可重新判断",
            "student_id": sid, "node_id": nid, "indicator_id": ind,
            "csv_copy": csv_copy, "mutation": mutation,
            "expect": {"event_count": 4, "sufficient": True}}


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

def run(csv_dir: Path, before_dir: Path, work: Path, bundle_out: Path) -> dict:
    csv_dir, before_dir, work, bundle_out = (Path(p).resolve() for p in (csv_dir, before_dir, work, bundle_out))
    # 隔离守卫：工作区不得与官方 CSV 目录重叠，输出不得指向官方 CSV / 官方 pipeline 目录
    if work == csv_dir or csv_dir in work.parents or work in csv_dir.parents:
        raise SystemExit(f"隔离工作区与官方 CSV 目录重叠，拒绝执行：{work} vs {csv_dir}")
    before_path = before_dir / "diagnoses.json"
    if not before_path.is_file():
        raise SystemExit(f"--before 目录缺少 diagnoses.json：{before_dir}")
    work.mkdir(parents=True, exist_ok=True)

    scenarios = []
    for build in (scenario_d15, scenario_d17):
        scen = build(csv_dir, work)
        key = (scen["student_id"], scen["node_id"], scen["indicator_id"])
        run_dir = work / scen["id"] / "run"
        print(f"\n[replay] === 场景 {scen['id']}（{scen['label']}）===")
        pipeline.run(scen["csv_copy"], run_dir)
        analysis.run(run_dir, scen["csv_copy"], run_dir / "analysis")
        before = _snapshot(_find_diag(before_path, key))
        after_d = _find_diag(run_dir / "analysis" / "diagnoses.json", key)
        after = _snapshot(after_d)
        if before is None or after is None:
            raise SystemExit(f"场景 {scen['id']} 找不到目标诊断 {key}（before={before is not None}, after={after is not None}）")
        changes = _changes(before, after)
        scenarios.append({
            "id": scen["id"], "label": scen["label"],
            "student_id": scen["student_id"], "node_id": scen["node_id"],
            "indicator_id": scen["indicator_id"],
            "mutation": scen["mutation"], "before": before, "after": after,
            "changes": changes,
        })
        print(f"[replay] {key} before: {before['score']:g}/{before['full_score']:g} "
              f"rate={_rate_text(before['rate'])} n={before['event_count']} case={before['case']} "
              f"resource={before['resource']['material_id']}（{before['resource']['type']}）")
        print(f"[replay] {key} after : {after['score']:g}/{after['full_score']:g} "
              f"rate={_rate_text(after['rate'])} n={after['event_count']} case={after['case']} "
              f"resource={after['resource']['material_id']}（{after['resource']['type']}）")
        for c in changes:
            print(f"[replay]   差异 | {c}")
        # 预期核对（只用于报告，不写入结果数据）
        diffs = []
        for k, v in scen["expect"].items():
            if k == "sufficient":
                actual = after_d["coverage"]["sufficient"]
            else:
                actual = after.get(k)
            if actual != v:
                diffs.append(f"{k}: 预期 {v}，实际 {actual}")
        print(("[replay] 预期核对 | 符合预期" if not diffs
               else "[replay] 预期核对 | 与预期不符：" + "；".join(diffs)))

    replay = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "label": LABEL, "scenarios": scenarios}
    bundle_out.mkdir(parents=True, exist_ok=True)
    # 同时写回 --before 的上级（即 pipeline 输出目录 src），bundle.py 重生成时会自动附带
    outs = [bundle_out / "replay.json", work / "replay.json", before_dir.parent / "replay.json"]
    for out in dict.fromkeys(outs):
        out.write_text(json.dumps(replay, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[replay] 写出 {out}")
    return replay


def main() -> None:
    ap = argparse.ArgumentParser(description="自造证据回放（隔离副本用正式管线重算）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--csv-dir", required=True, help="官方 5 张 CSV 所在目录（只读）")
    p.add_argument("--before", required=True, help="官方 analysis 目录（含 diagnoses.json）")
    p.add_argument("--work", required=True, help="隔离工作区（副本与重算输出）")
    p.add_argument("--bundle-out", required=True, help="replay.json 输出目录（official-bundle）")
    args = ap.parse_args()
    run(Path(args.csv_dir), Path(args.before), Path(args.work), Path(args.bundle_out))


if __name__ == "__main__":
    main()