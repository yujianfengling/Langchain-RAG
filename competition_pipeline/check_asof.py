"""
智慧学伴比赛版 · 「截至所选测验」口径一致性验收（修复2）
============================================================
做法：把官方 CSV 复制到临时目录，作答记录只保留 N01 行，用正式管线生成
仅 N01 的 src+analysis；再与全量 src+analysis 比较所有 N01 诊断：
  case / question_case / resource.material_id / pretest_questions /
  candidates(done, done_full)
必须完全一致（历史推荐不得用到后续测验 N02 的作答）。差异数应为 0。

另打印 D12/N01/T01I2 修复后的 resource.material_id 与 pretest_questions，
并列出 D12 在 N02 做过而 N01 没做过的 T01I2 题号（旧口径会误用的反例证据）。

用法：
  python competition_pipeline/check_asof.py \
    --csv-dir <官方CSV目录> --src <全量pipeline输出目录> [--work <临时工作区>]
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pipeline  # noqa: E402
import analysis  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

CSV_FILES = ["课程要求.csv", "题目与材料.csv", "模拟学生.csv", "测验节点.csv", "作答记录.csv", "字段说明.csv"]
DEFAULT_SRC = Path("D:/Document/langchain-rag-work/competition-data/official")


def _copy_only_n01(csv_dir: Path, dest: Path, node_id: str = "N01") -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name in CSV_FILES:
        src = csv_dir / name
        if not src.is_file():
            raise FileNotFoundError(f"官方 CSV 缺失：{src}")
        if name != "作答记录.csv":
            shutil.copy2(src, dest / name)
            continue
        with src.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            rows = [r for r in reader if (r.get("节点编号") or "").strip() == node_id]
        with (dest / name).open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(rows)
        print(f"[asof] 副本 作答记录.csv 仅保留 {node_id} 行：{len(rows)} 行")
    return dest


def _diag_by_key(path: Path, node_id: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(d["student_id"], d["node_id"], d["indicator_id"]): d
            for d in data["diagnoses"] if d["node_id"] == node_id}


def _view(d: dict) -> dict:
    return {
        "case": d["case"],
        "question_case": d.get("question_case"),
        "resource.material_id": d["resource"]["material_id"],
        "pretest_questions": d.get("pretest_questions", []),
        "candidates(done,done_full)": [
            {"material_id": c["material_id"], "done": c["done"], "done_full": c["done_full"]}
            for c in d.get("candidates", [])
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="截至所选测验口径一致性验收")
    ap.add_argument("--csv-dir", required=True, help="官方 5 张 CSV 目录（只读）")
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="全量 pipeline 输出目录（含 analysis/）")
    ap.add_argument("--analysis", help="全量 analysis 目录（默认 <src>/analysis）")
    ap.add_argument("--work", help="临时工作区（默认自动创建临时目录）")
    args = ap.parse_args()

    csv_dir, src = Path(args.csv_dir), Path(args.src)
    analysis_dir = Path(args.analysis) if args.analysis else src / "analysis"
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="asof-n01-"))
    print(f"[asof] 临时工作区：{work}")

    csv_copy = _copy_only_n01(csv_dir, work / "csv")
    n01_src = work / "src"
    pipeline.run(csv_copy, n01_src)
    analysis.run(n01_src, csv_copy, n01_src / "analysis")

    full = _diag_by_key(analysis_dir / "diagnoses.json", "N01")
    part = _diag_by_key(n01_src / "analysis" / "diagnoses.json", "N01")

    diffs = 0
    missing = sorted(set(full) - set(part))
    extra = sorted(set(part) - set(full))
    for k in missing:
        diffs += 1
        print(f"[asof] 差异 | 仅 N01 口径缺少诊断 {k}")
    for k in extra:
        diffs += 1
        print(f"[asof] 差异 | 仅 N01 口径多出诊断 {k}")
    for k in sorted(set(full) & set(part)):
        fv, pv = _view(full[k]), _view(part[k])
        for field in fv:
            if fv[field] != pv[field]:
                diffs += 1
                print(f"[asof] 差异 | {k} {field}: 全量={json.dumps(fv[field], ensure_ascii=False)} "
                      f"仅N01={json.dumps(pv[field], ensure_ascii=False)}")
    print(f"[asof] N01 诊断数：全量={len(full)} 仅N01={len(part)}；比较字段差异数={diffs}")

    # D12/N01/T01I2 反例证据：旧口径（全部节点）会用到 N02 的哪些题
    key = ("D12", "N01", "T01I2")
    d = full.get(key)
    if d:
        print(f"[asof] D12/N01/T01I2 修复后 resource.material_id={d['resource']['material_id']} "
              f"pretest_questions={d.get('pretest_questions', [])}")
    evidence = json.loads((src / "evidence_index.json").read_text(encoding="utf-8"))
    scope_p = src / "scope_index.json"
    if scope_p.is_file():
        item_ind = json.loads(scope_p.read_text(encoding="utf-8"))["item_indicator"]
    else:
        cm = json.loads((analysis_dir / "curriculum_map.json").read_text(encoding="utf-8"))
        item_ind = {m["material_id"]: r["indicator_id"]
                    for r in cm["indicators"] for m in r.get("materials", [])}
    done_n01 = {e["item_id"] for e in evidence
                if e["student_id"] == "D12" and e["node_id"] == "N01"}
    done_n02 = {e["item_id"] for e in evidence
                if e["student_id"] == "D12" and e["node_id"] == "N02"}
    counter = sorted(i for i in done_n02 - done_n01 if item_ind.get(i) == "T01I2")
    print(f"[asof] 反例证据 | D12 在 N02 做过而 N01 没做过的 T01I2 题号：{counter}"
          "（旧口径会把它们当作 N01 时点『已做过』，从而漏选补测/迁移题）")

    if diffs:
        raise SystemExit(f"[asof] 未通过：{diffs} 处差异")
    print("[asof] 全部通过：N01 诊断与仅 N01 数据重算完全一致")


if __name__ == "__main__":
    main()