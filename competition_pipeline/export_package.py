"""
智慧学伴比赛版 · 成果包导出与交叉核对
============================================================
把 pipeline / analysis 的输出组装成资料评审可独立检查的 JSON/CSV 成果包。
引用编号贯穿：记录编号 / 作答事件编号 / 材料编号 / 指标编号 / 课标整理编号。

用法：
  python competition_pipeline/export_package.py build --src <pipeline输出> \
    --analysis <analysis目录> --out <成果包目录>
  python competition_pipeline/export_package.py verify --pkg <成果包目录> --src <pipeline输出>
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List

FIELD_DICTIONARY = [
    {"artifact": "课程要求", "field": "指标编号", "meaning": "观察指标主键，如 T01I2"},
    {"artifact": "题目与材料", "field": "材料编号", "meaning": "Q001—Q054 题目；L01—L03 学习卡；B01—B08 教材切片"},
    {"artifact": "作答记录", "field": "作答事件编号", "meaning": "一次作答；重新提交/复核改分沿用同一编号"},
    {"artifact": "作答记录", "field": "修订版本", "meaning": "同一事件版本号，统计保留最大版本"},
    {"artifact": "作答记录", "field": "评分状态", "meaning": "已评分/待评分/未作答；待评分与未作答不计入得分率"},
    {"artifact": "数据质量", "field": "分数越界", "meaning": "官方原始记录得分>满分；保留状态计数，排除得分聚合"},
    {"artifact": "evidence_index", "field": "evidence_id", "meaning": "EV-作答事件编号，逐条可回溯记录编号与版本"},
    {"artifact": "case_book", "field": "evidence_ids", "meaning": "该结论支持的作答事件编号列表"},
]


def _read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _valid_events(evidence: List[dict]) -> List[dict]:
    """主统计口径：已评分、未隔离、未超范围、分数齐全。"""
    return [e for e in evidence
            if e.get("status") == "有效得分" and not e.get("quarantined")
            and not e.get("out_of_scope")
            and e.get("score") is not None and e.get("full_score") is not None]


def _agg(evs: List[dict]) -> dict:
    return {
        "score": sum(float(e["score"]) for e in evs),
        "full_score": sum(float(e["full_score"]) for e in evs),
        "event_count": len(evs),
        "observed_students": len({e["student_id"] for e in evs}),
        "items": sorted({e["item_id"] for e in evs}),
    }


def compute_same_question(evidence: List[dict], item_ind: Dict[str, str],
                          student_class: Dict[str, str], node_ids: List[str],
                          indicators: List[str]) -> List[dict]:
    """同题对比：每个 (node, ind) × 两班，all / common（两班题目交集）/ only。
    全部数字由 evidence 重算，不写死；build 与 verify 共用同一口径。"""
    valid = _valid_events(evidence)
    class_ids = sorted(set(student_class.values()))
    out = []
    for nid in node_ids:
        for ind in indicators:
            cls_evs = {
                cid: [e for e in valid
                      if e.get("node_id") == nid
                      and item_ind.get(e.get("item_id", "")) == ind
                      and student_class.get(e.get("student_id", "")) == cid]
                for cid in class_ids
            }
            item_sets = {cid: {e["item_id"] for e in evs} for cid, evs in cls_evs.items()}
            common = set.intersection(*item_sets.values()) if item_sets else set()
            out.append({
                "node_id": nid, "indicator_id": ind,
                "classes": {
                    cid: {
                        "all": _agg(cls_evs[cid]),
                        "common": _agg([e for e in cls_evs[cid] if e["item_id"] in common]),
                    } for cid in class_ids
                },
                "common_items": sorted(common),
                "only": {cid: sorted(item_sets[cid] - common) for cid in class_ids},
            })
    return out


def build(src: Path, analysis: Path, out: Path) -> None:
    src, analysis, out = Path(src), Path(analysis), Path(out)
    out.mkdir(parents=True, exist_ok=True)

    class_rows = _read_json(src / "class_analysis.json")
    evidence = _read_json(src / "evidence_index.json")
    summary = _read_json(src / "data_summary.json")
    curriculum = _read_json(analysis / "curriculum_map.json")
    diagnoses = _read_json(analysis / "diagnoses.json")
    cases = _read_json(analysis / "case_book.json")

    artifacts = {
        "curriculum_map.json": curriculum,
        "class_analysis.json": class_rows,
        "student_analysis.json": diagnoses,
        "study_recommendations.json": cases,
        "evidence_index.json": evidence,
        "data_summary.json": summary,
        "field_dictionary.json": FIELD_DICTIONARY,
    }
    for name, data in artifacts.items():
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 讲评方案（含两策略×预算与 naive 对照），供独立复算
    rp = src / "review_plans.json"
    if rp.is_file():
        (out / "review_plans.json").write_text(rp.read_text(encoding="utf-8"), encoding="utf-8")

    # 数据质量报告：直接复制 pipeline 输出，保持原始记录号
    q = src / "data_quality.csv"
    if q.is_file():
        (out / "data_quality.csv").write_text(q.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")

    # 推荐 CSV：从 analysis 复制（如存在）
    r = analysis / "recommendations.csv"
    if r.is_file():
        (out / "recommendations.csv").write_text(r.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")

    # 交叉核对清单：全部班级汇总行 + 全部案例（页面展示的关键值 ↔ 导出行）
    crosscheck = []
    for c in cases["cases"]:
        crosscheck.append({
            "page": "学生答卷",
            "case": c["case_name"],
            "student_id": c["student_id"], "node_id": c["node_id"], "indicator_id": c["indicator_id"],
            "score": c["score"], "full_score": c["full_score"],
            "evidence_ids": c["evidence_ids"],
            "material_id": c["resource"]["material_id"],
            "textbook": c["textbook"],
        })
    for row in class_rows:
        crosscheck.append({
            "page": "班级学情", "case": "班级汇总",
            **{k: row[k] for k in ("class_id", "node_id", "indicator_id", "score", "max_score",
                                   "event_count", "observed_students", "class_size", "score_rate")},
        })
    (out / "crosscheck.json").write_text(
        json.dumps(crosscheck, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同题对比：两班在同一 (node, ind) 下的全部题 / 共同题口径（由 evidence 重算）
    # 指标集合取自课程要求表（正式九项），不把学习卡的多指标关联串当单指标
    scope = _read_json(src / "scope_index.json")
    formal_inds = sorted({i["indicator_id"] for i in curriculum["indicators"]})
    same_q = compute_same_question(
        evidence, scope["item_indicator"], scope["student_class"],
        scope["node_order"], formal_inds)
    (out / "same_question_comparison.json").write_text(
        json.dumps(same_q, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[export] {out} files={len(list(out.iterdir()))} crosscheck={len(crosscheck)} "
          f"same_question={len(same_q)}")


def verify(pkg: Path, src: Path) -> None:
    """全量校验成果包：班级 / 个体 / 案例 / 推荐CSV / 交叉清单。

    任何缺文件、空数据、主键重复、分数/分母/记录数不一致、证据或材料编号
    无法定位、案例与个体诊断不一致，都逐条报 FAIL 并以非零码退出。
    """
    pkg, src = Path(pkg), Path(src)
    failures: List[str] = []
    passes = 0

    def check(ok: bool, label: str):
        nonlocal passes
        if ok:
            passes += 1
            print(f"PASS | {label}")
        else:
            failures.append(label)
            print(f"FAIL | {label}")

    def load(name: str, where: Path, required=True):
        p = where / name
        if not p.is_file():
            if required:
                check(False, f"缺文件 {where.name}/{name}")
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if name.endswith(".json") else p.read_text(encoding="utf-8-sig")
        except (OSError, ValueError) as exc:
            check(False, f"文件损坏 {name}: {exc}")
            return None
        if required and (data is None or (isinstance(data, (list, dict)) and len(data) == 0)):
            check(False, f"空数据 {name}")
            return None
        return data

    # ---- 1) 必需文件齐全 ----
    required = ["curriculum_map.json", "class_analysis.json", "student_analysis.json",
                "study_recommendations.json", "evidence_index.json", "data_summary.json",
                "field_dictionary.json", "crosscheck.json", "data_quality.csv",
                "recommendations.csv", "same_question_comparison.json"]
    for name in required:
        check((pkg / name).is_file(), f"成果包文件存在 {name}")

    # ---- 1b) 质量报告必须与源产物逐行一致 ----
    # 反例驱动：曾出现「删掉 data_quality.csv 一行，verify 仍 4859 PASS/0 FAIL」。
    # 只检查文件存在不足以证明异常记录没被删改，这里比对行数、逐行内容与异常记录编号集合。
    def _quality_rows(p: Path):
        with p.open(encoding="utf-8-sig", newline="") as f:
            return [tuple(row) for row in csv.reader(f) if any(c.strip() for c in row)]

    src_quality, pkg_quality = src / "data_quality.csv", pkg / "data_quality.csv"
    if src_quality.is_file() and pkg_quality.is_file():
        srows, prows = _quality_rows(src_quality), _quality_rows(pkg_quality)
        check(len(srows) == len(prows), f"data_quality.csv 行数与源质量报告一致（{len(srows)} 行）")
        check(srows == prows, "data_quality.csv 逐行内容与源质量报告一致")
        skeys = [r[1] for r in srows[1:] if len(r) > 1 and r[1].strip()]
        pkeys = [r[1] for r in prows[1:] if len(r) > 1 and r[1].strip()]
        check(sorted(skeys) == sorted(pkeys), "data_quality.csv 异常记录编号集合一致")
    else:
        check(False, "源质量报告 src/data_quality.csv 可读")

    cm = load("curriculum_map.json", pkg)
    cls = load("class_analysis.json", pkg)
    sa = load("student_analysis.json", pkg)
    cb = load("study_recommendations.json", pkg)
    ev = load("evidence_index.json", pkg)
    xc = load("crosscheck.json", pkg)
    sq = load("same_question_comparison.json", pkg)
    pkg_summary = load("data_summary.json", pkg)
    rec_csv_text = load("recommendations.csv", pkg)
    if not all([cm, cls, sa, cb, ev, xc, sq, pkg_summary, rec_csv_text]):
        raise SystemExit(f"verify 未通过：{len(failures)} 项失败（必需数据缺失或为空）")

    # 可信期望集合（pipeline 生成；缺失则新增核对全部 FAIL）
    scope = None
    if (src / "scope_index.json").is_file():
        try:
            scope = _read_json(src / "scope_index.json")
        except (OSError, ValueError) as exc:
            check(False, f"文件损坏 src/scope_index.json: {exc}")
    else:
        check(False, "缺文件 src/scope_index.json")

    # ---- 2) 索引 ----
    ev_by_id = {e["evidence_id"]: e for e in ev}
    check(len(ev_by_id) == len(ev), "evidence_index 主键唯一")
    item_ind = {}
    known_materials = set()
    for ind_row in cm["indicators"]:
        for m in ind_row.get("materials", []):
            item_ind[m["material_id"]] = ind_row["indicator_id"]
            known_materials.add(m["material_id"])
        for p in ind_row.get("textbook_pages", []):
            known_materials.add(p["material_id"])
    known_materials |= {f"B{i:02d}" for i in range(1, 9)} | {"待定位"}
    ind_ids = {r["indicator_id"] for r in cm["indicators"]}

    # ---- 3) 班级汇总：主键唯一、分数/分母/得分率一致、与 pipeline 源一致 ----
    src_cls = _read_json(src / "class_analysis.json")
    src_by = {(r["class_id"], r["node_id"], r["indicator_id"]): r for r in src_cls}
    cls_keys = [(r["class_id"], r["node_id"], r["indicator_id"]) for r in cls]
    check(len(set(cls_keys)) == len(cls_keys), "class_analysis 主键唯一")
    for r in cls:
        key = (r["class_id"], r["node_id"], r["indicator_id"])
        label = f"class_analysis {key}"
        ok = (0 <= r["score"] <= r["max_score"] and r["event_count"] > 0
              and (r["max_score"] == 0 or abs(r["score_rate"] - r["score"] / r["max_score"]) < 5e-4))
        check(ok, f"{label} 分数/分母/得分率自洽")
        s = src_by.get(key)
        check(bool(s) and all(abs(float(r[k]) - float(s[k])) < 1e-9
                              for k in ("score", "max_score", "event_count", "observed_students", "class_size")),
              f"{label} 与 pipeline 源一致")

    # ---- 4) 个体诊断：主键唯一、证据可定位且归属正确、数值与证据重算一致 ----
    ds = sa["diagnoses"]
    d_keys = [(d["student_id"], d["node_id"], d["indicator_id"]) for d in ds]
    check(len(set(d_keys)) == len(d_keys), "student_analysis 主键唯一")
    d_by = dict(zip(d_keys, ds))
    check(len(ds) > 0, "student_analysis 非空")
    # 有「已评分且未隔离」证据的键集合：用于判断 evidence_ids 为空是否合法
    # （仅待评分/未作答/分数越界隔离的考点，契约允许无已评分证据）。
    scored_keys = {(e["student_id"], e["node_id"], item_ind.get(e["item_id"]))
                   for e in ev if e.get("status") == "有效得分"
                   and not e.get("quarantined") and not e.get("out_of_scope")}
    for d in ds:
        key = (d["student_id"], d["node_id"], d["indicator_id"])
        label = f"diagnosis {key}"
        check(d["indicator_id"] in ind_ids, f"{label} 指标编号可定位")
        check(d["case"] in ("记录不足", "表现较好", "部分得分", "有效零分"), f"{label} case 取值合法")
        eids = d.get("evidence_ids", [])
        # 契约允许「无已评分有效证据」的诊断（仅待评分/未作答/越界隔离）：
        # evidence_ids 可为空，但必须 event_count=0 且证据索引中确无该键的有效得分；
        # 得分聚合为 0 由下方重算校验兜底。
        no_scored = d["event_count"] == 0 and key not in scored_keys
        check(len(eids) > 0 or no_scored,
              f"{label} evidence_ids 非空（或无已评分有效证据）")
        evs = [ev_by_id.get(x) for x in eids]
        check(all(e is not None for e in evs), f"{label} 证据编号全部可定位")
        if any(e is None for e in evs):
            continue
        check(all(e["student_id"] == d["student_id"] and e["node_id"] == d["node_id"]
                  and item_ind.get(e["item_id"]) == d["indicator_id"] for e in evs),
              f"{label} 证据归属学生/测验/指标正确")
        rs = sum(float(e["score"] or 0) for e in evs)
        rf = sum(float(e["full_score"] or 0) for e in evs)
        check(abs(rs - d["score"]) < 1e-9 and abs(rf - d["full_score"]) < 1e-9
              and len(evs) == d["event_count"],
              f"{label} score/full_score/event_count 与证据重算一致")
        check(d["full_score"] == 0 or abs(d["rate"] - d["score"] / d["full_score"]) < 5e-4,
              f"{label} rate 自洽")
        res = d.get("resource") or {}
        mid = res.get("material_id", "")
        check(mid in known_materials or mid == d["indicator_id"],
              f"{label} resource.material_id 可定位（{mid}）")
        steps = res.get("steps") or []
        check(len(steps) > 0 and all(x.get("title") and x.get("when_stuck") for x in steps),
              f"{label} 步骤齐全且带卡住提示")
        check(bool(res.get("review")), f"{label} 复查条件非空")
        tb = d.get("textbook") or {}
        check(tb.get("material_id") in known_materials, f"{label} textbook.material_id 可定位")
        check(tb.get("pdf_page") is None or (isinstance(tb["pdf_page"], int) and tb["pdf_page"] >= 1),
              f"{label} textbook.pdf_page 合法")
        cov = d.get("coverage") or {}
        check(cov.get("event_count") == d["event_count"]
              and cov.get("sufficient") == (d["event_count"] >= sa["threshold"]["min_evidence"]),
              f"{label} coverage 与记录数一致")

    # ---- 5) 案例册：三类齐全、与个体诊断完全一致（不覆盖结论） ----
    cases = cb.get("cases", [])
    names = [c.get("case_name") for c in cases]
    check(set(names) >= {"部分得分", "表现较好", "记录不足"}, f"案例册三类齐全（实际 {names}）")
    for c in cases:
        key = (c["student_id"], c["node_id"], c["indicator_id"])
        label = f"case {c.get('case_name')} {key}"
        d = d_by.get(key)
        check(d is not None, f"{label} 能在个体诊断中定位")
        if not d:
            continue
        same = all(c.get(k) == d.get(k) for k in
                   ("case", "question_case", "score", "full_score", "event_count",
                    "evidence_ids", "resource", "facts", "inference", "observe", "textbook"))
        check(same, f"{label} 与个体诊断结论/建议一致（案例册不覆盖）")
        check(bool(c.get("focus")) and bool(c.get("selection_reason")), f"{label} 讲解重点与选择理由齐全")

    # ---- 6) 推荐 CSV 与个体诊断逐行一致 ----
    rows = list(csv.DictReader(rec_csv_text.splitlines()))
    check(len(rows) == len(ds), f"recommendations.csv 行数={len(rows)} 与诊断数={len(ds)} 一致")
    csv_by = {(r["student_id"], r["node_id"], r["indicator_id"]): r for r in rows}
    check(len(csv_by) == len(rows), "recommendations.csv 主键唯一")
    mismatch = 0
    for d in ds:
        key = (d["student_id"], d["node_id"], d["indicator_id"])
        r = csv_by.get(key)
        if not r:
            mismatch += 1
            continue
        if (r["case"] != d["case"] or r.get("question_case", "") != (d.get("question_case") or "")
                or r["material_id"] != d["resource"]["material_id"]
                or r["review"] != d["resource"]["review"]
                or abs(float(r["score"]) - d["score"]) > 1e-9
                or abs(float(r["full_score"]) - d["full_score"]) > 1e-9
                or int(r["event_count"]) != d["event_count"]
                or r["evidence_ids"].split() != d["evidence_ids"]):
            mismatch += 1
    check(mismatch == 0, f"recommendations.csv 与诊断逐行一致（不一致 {mismatch} 行）")

    # ---- 7) crosscheck 清单 ↔ 班级/案例 ----
    for item in xc:
        if item.get("page") == "班级学情":
            key = (item["class_id"], item["node_id"], item["indicator_id"])
            r = next((x for x in cls if (x["class_id"], x["node_id"], x["indicator_id"]) == key), None)
            check(bool(r) and all(abs(float(item[k]) - float(r[k])) < 1e-9
                                  for k in ("score", "max_score", "score_rate")),
                  f"crosscheck 班级 {key} 与导出一致")
        elif item.get("page") == "学生答卷":
            key = (item["student_id"], item["node_id"], item["indicator_id"])
            c = next((x for x in cases if (x["student_id"], x["node_id"], x["indicator_id"]) == key), None)
            check(bool(c) and abs(float(item["score"]) - c["score"]) < 1e-9
                  and abs(float(item["full_score"]) - c["full_score"]) < 1e-9
                  and item["evidence_ids"] == c["evidence_ids"]
                  and item["material_id"] == c["resource"]["material_id"],
                  f"crosscheck 案例 {key} 与案例册一致")

    # ---- 8) 主键集合 ↔ src/scope_index.json 可信期望（缺失/多余都列出编号） ----
    if scope:
        exp_cls = {tuple(k) for k in scope.get("expected_class_keys", [])}
        exp_diag = {tuple(k) for k in scope.get("expected_diagnosis_keys", [])}

        def keyset_check(actual: set, expected: set, label: str):
            missing, extra = sorted(expected - actual), sorted(actual - expected)
            check(not missing and not extra,
                  f"{label} 键集合与 scope_index 一致（{len(actual)} 键）"
                  if not missing and not extra else
                  f"{label} 键集合与 scope_index 不一致：缺失 {missing}；多余 {extra}")

        keyset_check(set(cls_keys), exp_cls, "class_analysis")
        keyset_check(set(d_keys), exp_diag, "student_analysis")
        keyset_check(set(csv_by.keys()), exp_diag, "recommendations.csv")

        # ---- 9) crosscheck 覆盖全部班级行与全部案例 ----
        xc_cls_keys = {(x["class_id"], x["node_id"], x["indicator_id"])
                       for x in xc if x.get("page") == "班级学情"}
        xc_case_keys = {(x["student_id"], x["node_id"], x["indicator_id"])
                        for x in xc if x.get("page") == "学生答卷"}
        case_keys = {(c["student_id"], c["node_id"], c["indicator_id"]) for c in cases}
        keyset_check(xc_cls_keys, exp_cls, "crosscheck 班级条目")
        keyset_check(xc_case_keys, case_keys, "crosscheck 案例条目")

        # ---- 10) 对账透传一致 ----
        check(pkg_summary.get("reconciliation") == scope.get("reconciliation"),
              "data_summary.reconciliation 与 src/scope_index.reconciliation 一致")

        # ---- 11) 同题对比：用 src 证据 + scope_index 独立重算比对 ----
        src_ev = _read_json(src / "evidence_index.json")
        expected_sq = compute_same_question(
            src_ev, scope["item_indicator"], scope["student_class"],
            scope["node_order"], sorted({i["indicator_id"] for i in cm["indicators"]}))
        exp_by = {(x["node_id"], x["indicator_id"]): x for x in expected_sq}
        got_by = {(x.get("node_id"), x.get("indicator_id")): x for x in sq}
        keyset_check(set(got_by.keys()), set(exp_by.keys()), "same_question_comparison")

        def stats_eq(a, b) -> bool:
            return (isinstance(a, dict) and isinstance(b, dict)
                    and abs(float(a.get("score", 0)) - float(b.get("score", 0))) < 1e-9
                    and abs(float(a.get("full_score", 0)) - float(b.get("full_score", 0))) < 1e-9
                    and a.get("event_count") == b.get("event_count")
                    and a.get("observed_students") == b.get("observed_students")
                    and a.get("items") == b.get("items"))

        for key, exp in sorted(exp_by.items()):
            got = got_by.get(key)
            label = f"same_question {key}"
            if got is None:
                check(False, f"{label} 缺失条目")
                continue
            ok = (got.get("common_items") == exp["common_items"]
                  and got.get("only") == exp["only"]
                  and set(got.get("classes", {})) == set(exp["classes"]))
            if ok:
                for cid, cexp in exp["classes"].items():
                    cgot = got["classes"].get(cid, {})
                    ok = ok and stats_eq(cgot.get("all"), cexp["all"]) \
                        and stats_eq(cgot.get("common"), cexp["common"])
            check(ok, f"{label} all/common/only/items 与 src 证据重算一致")

    print(f"[verify] PASS={passes} FAIL={len(failures)}")
    if failures:
        for f in failures:
            print(f"  失败项: {f}")
        raise SystemExit(f"verify 未通过：{len(failures)} 项失败")


def main() -> None:
    ap = argparse.ArgumentParser(description="成果包导出与交叉核对")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--src", required=True)
    p.add_argument("--analysis", required=True)
    p.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--pkg", required=True)
    v.add_argument("--src", required=True)
    args = ap.parse_args()
    if args.cmd == "build":
        build(Path(args.src), Path(args.analysis), Path(args.out))
    else:
        verify(Path(args.pkg), Path(args.src))


if __name__ == "__main__":
    main()
