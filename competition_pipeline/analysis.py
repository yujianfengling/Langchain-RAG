"""
智慧学伴比赛版 · 证据驱动的课标映射 / 诊断 / 建议 / 导出
============================================================
输入：pipeline.py 的 class_analysis.json + evidence_index.json + curriculum.json
      + 官方 5 张 CSV（题目、学习卡、教材切片、测验节点）
输出：curriculum_map.json / diagnoses.json / case_book.json / recommendations.csv

设计原则：
- 分数、规则、资源选择全部由程序依据官方记录计算；不编造题干、页码或评分。
- 诊断分两层：「本题观察」（question_findings / question_case）与
  「该测验下该考点的证据覆盖」（coverage / case）。两层可同时成立：
  一道题有具体失分证据，而整个考点记录仍不足。
- 案例册只负责选择展示对象、排序和讲解重点，不覆盖诊断结论；
  页面、案例册、成果包引用同一份结构化诊断。
- 正式规则不依赖 D01/D15/D17 等固定编号；编号仅作为案例册的展示偏好（tie-break）。
- 教师调整单独记录，可恢复系统原建议；不写回原始答卷与官方统计。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from field_contract import (
    ITEM_FIELDS, NODE_FIELDS, STUDENT_FIELDS, normalize_status, STATUS_SCORED,
)

# 教材切片人工核对结果（见 COMPETITION_HANDOVER 第 11 节）：
# pdf_page = PDF 文件页（1-based，已用 pypdfium2 文本定位）；printed = 教材正文印刷页；
# verified = 具体知识点是否在该页文本中核对到。
# 九指标逐页核对（2026-11-21 补齐全量）：
# PDF 文件页用 pypdfium2 逐页取文本定位；印刷页码取自该页页脚
# （切片 PDF 文本层把页码按倒序输出，如「８９」反转得 98），
# 与原有已核对条目 98/100/128/91/95/123/138 交叉验证一致。
TEXTBOOK_VERIFIED = {
    "T01I1": [
        {"material_id": "B05", "pdf_page": 2, "printed": "上册69",
         "anchor": "3.1 列代数式表示数量关系（用式子表达实际问题中的数量关系）", "verified": True},
    ],
    "T01I2": [
        {"material_id": "B05", "pdf_page": 31, "printed": "上册98",
         "anchor": "去括号与合并同类项（4.2 整式的加法与减法）", "verified": True},
        {"material_id": "B05", "pdf_page": 33, "printed": "上册100",
         "anchor": "例7 去括号后合并同类项", "verified": True},
    ],
    "T01I3": [
        {"material_id": "B05", "pdf_page": 4, "printed": "上册71",
         "anchor": "例2 说出代数式的意义，并举例解释实际问题中的数量关系", "verified": True},
    ],
    "T02I1": [
        {"material_id": "B06", "pdf_page": 19, "printed": "上册128",
         "anchor": "解一元一次方程（去括号与去分母）", "verified": True},
    ],
    "T02I2": [
        {"material_id": "B06", "pdf_page": 44, "printed": "下册91",
         "anchor": "代入消元法", "verified": True},
        {"material_id": "B06", "pdf_page": 48, "printed": "下册95",
         "anchor": "加减消元法", "verified": True},
    ],
    "T02I3": [
        {"material_id": "B06", "pdf_page": 24, "printed": "上册133",
         "anchor": "5.3 实际问题与一元一次方程（设未知数、按等量关系列方程）", "verified": True},
        {"material_id": "B06", "pdf_page": 54, "printed": "下册101",
         "anchor": "10.3 实际问题与二元一次方程组", "verified": True},
    ],
    "T03I1": [
        {"material_id": "B02", "pdf_page": 4, "printed": "下册123",
         "anchor": "不等式的性质", "verified": True},
    ],
    "T03I2": [
        {"material_id": "B02", "pdf_page": 19, "printed": "下册138",
         "anchor": "一元一次不等式组（两个解集的交集）", "verified": True},
    ],
    "T03I3": [
        {"material_id": "B02", "pdf_page": 15, "printed": "下册134",
         "anchor": "从实际限制（下降率不小于5%）中找出不等关系并列不等式", "verified": True},
        {"material_id": "B02", "pdf_page": 25, "printed": "下册144",
         "anchor": "讨论数学结论是否符合实际意义、用结论解释取值范围", "verified": True},
    ],
}
TEXTBOOK_RANGE = {
    "T01": "上册68—109", "T02": "上册110—148；下册87—119", "T03": "下册120—145",
}
STANDARD = {
    "CR01": {"file": "课程标准_2022.pdf", "pdf_page": 62, "printed": 55},
    "CR02": {"file": "课程标准_2022.pdf", "pdf_page": 63, "printed": 56},
    "CR03": {"file": "课程标准_2022.pdf", "pdf_page": 63, "printed": 56},
}

ADVICE_THRESHOLD = 0.60      # 巩固候选阈值
GOOD_THRESHOLD = 0.85        # 表现较好阈值
MIN_EVIDENCE = 3             # 少于该数视为记录不足
PRETEST_MAX = 3              # 补测最多安排的题数


def _read(path: Path, fields: Optional[dict] = None) -> List[dict]:
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
    raise FileNotFoundError(f"在 {csv_dir} 找不到 {names}")


def _split_ids(text: str) -> List[str]:
    return [x.strip() for x in (text or "").replace("、", ",").replace("|", ",").split(",") if x.strip()]


def _rubric_defs(text: str) -> Dict[str, dict]:
    """题目台账『评分点』：Q015P1 正确去两个括号 1分（每行一条）。"""
    defs = {}
    for line in (text or "").splitlines():
        m = re.match(r"\s*([A-Za-z0-9]+P\d+)\s+(.+?)\s+(\d+(?:\.\d+)?)\s*分\s*$", line)
        if m:
            defs[m.group(1)] = {"label": m.group(2).strip(), "full": float(m.group(3))}
    return defs


def _rubric_scores(text: str) -> Dict[str, float]:
    """作答记录『评分点得分』：Q015P1=1；Q015P2=0。"""
    kv = {}
    for seg in re.split(r"[；;\n]", text or ""):
        m = re.match(r"\s*([A-Za-z0-9]+P\d+)\s*=\s*(\d+(?:\.\d+)?)", seg)
        if m:
            kv[m.group(1)] = float(m.group(2))
    return kv


def _transfer_review(q: Optional[dict]) -> str:
    """复查条件按题型生成：1 分选择题用「独立选择＋口头解释」，不套用解答题的步骤完整度。
    口头解释属后续教学安排，不改变原题官方评分。"""
    if q and q.get("question_type") == "选择题" and float(q.get("full_score") or 1) <= 1:
        stem = q.get("name") or q.get("stem") or ""
        m = re.search(r"(\d+)\s*\(", stem)
        phrase = f"括号外的 {m.group(1)} 应分别乘哪两项" if m else "每个选项对错的依据"
        return (f"先独立选择，再请学生解释{phrase}；选对且解释清楚后，再安排下一道变式。"
                "口头解释属于后续教学安排，不改变原题官方评分。")
    return "变式若仍≥80% 且步骤完整，则记录为已观察的迁移表现；否则回到具体失分步。"


def _mismatch(e: dict) -> Optional[dict]:
    """步骤末行与最终填答不一致：从官方两个字段直接比对，不推测原因。"""
    step_text = (e.get("answer_text") or "").replace("−", "-")
    last_expr = ""
    for line in [x.strip().rstrip("。．.;；") for x in step_text.splitlines() if x.strip()]:
        m = re.search(r"=\s*(-?\s*\d*\s*[a-zA-Z]\s*[+-]\s*\d*\s*[a-zA-Z])\s*$", line)
        if m:
            last_expr = re.sub(r"\s+", "", m.group(1))
    final = (e.get("final_answer") or "").replace("−", "-").replace(" ", "")
    if last_expr and final and last_expr != final:
        return {
            "last_expr": last_expr, "final": final,
            "fact": f"官方『解答过程』末行得到 {last_expr}，而『学生最终作答』栏填 {final}。",
            "inference": "可能是抄写或输入不一致；原因未记录，需向本人核实。",
            "action": f"先核对最后一行符号（{last_expr} 与 {final}），再补练同类对照题；保留官方评分不变。",
        }
    return None


def _official_requirements(csv_dir: Path) -> Dict[str, dict]:
    """直接读官方《课程要求.csv》：课标整理编号、课标文件页/正文页、教材正文页。

    pipeline 生成的 curriculum.json 只透传必填字段（standard_id / standard_page /
    textbook_page 是可选字段，未写入），因此课标出处必须以官方原表为唯一来源，
    不能从二手产物里猜。文件页与正文页写法如「62／55」，两者分开存。
    """
    path = _find(csv_dir, ["课程要求.csv", "curriculum.csv", "1_课程要求.csv"])
    out: Dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            ind = (row.get("指标编号") or "").strip()
            if not ind:
                continue
            page = (row.get("课标文件页／正文页") or row.get("课标文件页/正文页") or "").strip()
            parts = re.split(r"[／/]", page, maxsplit=1) if page else []
            pdf_page = int(parts[0]) if parts and parts[0].strip().isdigit() else None
            printed = int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else None
            out[ind] = {
                "standard_id": (row.get("课标整理编号") or "").strip(),
                "standard_pdf_page": pdf_page,
                "standard_printed_page": printed,
                "theme_name": (row.get("主题") or "").strip(),
                "theme_goal": (row.get("主题目标") or "").strip(),
                "requirement_text": (row.get("观察指标") or "").strip(),
                "textbook_range": (row.get("教材正文页") or "").strip(),
            }
    return out


def build_curriculum_map(src: Path, csv_dir: Path) -> dict:
    """指标 → 题目/学习卡 → 课标整理编号 + 教材/课标出处。"""
    curriculum = json.loads((src / "curriculum.json").read_text(encoding="utf-8"))
    items = _read(_find(csv_dir, ["题目与材料.csv", "items.csv"]), ITEM_FIELDS)
    official_req = _official_requirements(csv_dir)
    by_ind: Dict[str, List[dict]] = defaultdict(list)
    for it in items:
        for ind in _split_ids(it.get("indicator_id", "")):
            by_ind[ind].append({
                "material_id": it.get("item_id", ""),
                "material_type": it.get("material_type", ""),
                "question_type": it.get("question_type", ""),
                "name": it.get("stem", "")[:90],
                "full_score": it.get("full_score", ""),
                "source": it.get("source_location", ""),
                "rubric": it.get("rubric_points", ""),
            })
    result = []
    for c in curriculum:
        ind = c.get("indicator_id", "")
        req = official_req.get(ind, {})
        std = req.get("standard_id") or c.get("standard_id", "")
        std_meta = STANDARD.get(std, {})
        pages = TEXTBOOK_VERIFIED.get(ind, [])
        if any(p.get("verified") for p in pages):
            locating = "已核对具体页"
        elif pages:
            locating = "仅范围页，未逐页核对"
        else:
            locating = "仅主题范围，未定位到具体页"
        result.append({
            "indicator_id": ind,
            "theme_id": c.get("theme_id", ""),
            "theme_name": req.get("theme_name", ""),
            "theme_goal": req.get("theme_goal", ""),
            "requirement": c.get("requirement") or req.get("requirement_text", ""),
            "standard": {
                "code": std,
                "file": std_meta.get("file", "课程标准_2022.pdf"),
                "pdf_page": req.get("standard_pdf_page") or std_meta.get("pdf_page"),
                "printed": req.get("standard_printed_page") or std_meta.get("printed"),
                "verified": False,
                "note": "课标切片为扫描页；文件页与正文页来自官方课程要求表，未逐条视觉复核",
            },
            "textbook_range": (req.get("textbook_range") or c.get("textbook_page")
                               or TEXTBOOK_RANGE.get(c.get("theme_id", ""), "待定位")),
            "textbook_pages": pages,
            "locating_status": locating,
            "materials": by_ind.get(ind, []),
            "learning_cards": [m for m in by_ind.get(ind, []) if m["material_type"] == "学习卡"],
            "questions": [m for m in by_ind.get(ind, []) if m["material_type"] == "题目"],
        })
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "indicators": result}


def build_diagnoses(src: Path, csv_dir: Path, textbook_map: dict) -> dict:
    """规则诊断：对每个学生在每个节点/指标生成两层结构化结果。

    第一层「本题观察」question_findings：具体失分评分点、过程/填答不一致。
    第二层「考点覆盖」coverage/case：有效记录数是否足以判断掌握程度。
    主任务 resource 的选取规则（不依赖任何固定学生编号）：
      1) 有过程/填答不一致证据 → 先核对该题最后一行（对照练习）；
      2) 覆盖不足 → 短测/观察任务（给出本节点可用、优先未做过的具体题号）；
      3) 表现较好 → 迁移变式（优先未做过、题型有变化的题；用过的题如实标注复练）；
      4) 其余部分得分 → 对照失分评分点的补练。
    规则 1 命中且覆盖不足时，同时给出 secondary_actions「补充观察」。
    历史推荐按「截至所选测验」口径：done/done_full 只统计测验日期 <= 本节点日期的事件。
    """
    evidence = json.loads((src / "evidence_index.json").read_text(encoding="utf-8"))
    students = _read(_find(csv_dir, ["模拟学生.csv", "students.csv"]), STUDENT_FIELDS)
    items = _read(_find(csv_dir, ["题目与材料.csv", "items.csv"]), ITEM_FIELDS)
    nodes = _read(_find(csv_dir, ["测验节点.csv", "nodes.csv"]), NODE_FIELDS)
    item_by_id = {i.get("item_id", ""): i for i in items}
    item_ind = {i.get("item_id", ""): i.get("indicator_id", "") for i in items}
    node_available = {n.get("node_id", ""): set(_split_ids(n.get("available_items", ""))) for n in nodes}
    curriculum = json.loads((src / "curriculum.json").read_text(encoding="utf-8"))
    ind_requirement = {c["indicator_id"]: c["requirement"] for c in curriculum}
    textbook_by_ind = {x["indicator_id"]: x for x in textbook_map["indicators"]}

    # 「截至所选测验」口径：节点按模拟测验日期升序；对诊断 (sid, nid, ind)，
    # done/done_full 只统计节点日期 <= date(nid) 的事件，不用后续测验的作答。
    # 超范围事件不进 by_key/scored；done_full 同样排除超范围与隔离记录。
    node_date: Dict[str, Optional[date]] = {}
    for n in nodes:
        try:
            node_date[n.get("node_id", "")] = datetime.fromisoformat(
                (n.get("date") or "").strip()).date()
        except ValueError:
            node_date[n.get("node_id", "")] = None

    evs_by_student: Dict[str, List[dict]] = defaultdict(list)
    for e in evidence:
        evs_by_student[e.get("student_id", "")].append(e)

    _asof_cache: Dict[tuple, tuple] = {}

    def asof_done(sid: str, nid: str):
        """返回截至 nid 测验日期的 (做过题集合, 满分题集合, 满分题型集合)。"""
        ck = (sid, nid)
        if ck in _asof_cache:
            return _asof_cache[ck]
        cutoff = node_date.get(nid)
        done, full_items, full_types = set(), set(), set()
        for e in evs_by_student.get(sid, []):
            d = node_date.get(e.get("node_id", ""))
            if cutoff is not None and d is not None and d > cutoff:
                continue  # 后续测验的作答不参与历史推荐
            iid = e.get("item_id", "")
            done.add(iid)
            if (e.get("status") == STATUS_SCORED and not e.get("quarantined")
                    and not e.get("out_of_scope")
                    and e.get("score") is not None and e.get("full_score")
                    and float(e["score"]) >= float(e["full_score"])):
                full_items.add(iid)
                qt = item_by_id.get(iid, {}).get("question_type", "")
                if qt:
                    full_types.add(qt)
        _asof_cache[ck] = (done, full_items, full_types)
        return _asof_cache[ck]

    by_key = defaultdict(list)
    for e in evidence:
        if e.get("out_of_scope"):
            continue  # 超出测验范围：保留原始记录，不进诊断分组
        ind = item_ind.get(e.get("item_id", ""), "")
        if ind:
            by_key[(e.get("student_id", ""), e.get("node_id", ""), ind)].append(e)

    out = []
    for (sid, nid, ind), evs in sorted(by_key.items()):
        done_items, done_full_items, done_full_types = asof_done(sid, nid)
        scored = [e for e in evs if e.get("status") == STATUS_SCORED
                  and not e.get("quarantined") and not e.get("out_of_scope")]
        pending = [e for e in evs if e.get("status") == "待评分"]
        unanswered = [e for e in evs if e.get("status") == "未作答"]
        s = sum(float(e["score"]) for e in scored if e.get("score") is not None)
        f = sum(float(e["full_score"] or 0) for e in scored)
        rate = round(s / f, 4) if f else None
        evidence_ids = [e["evidence_id"] for e in scored]
        lost = [e for e in scored if e.get("score") is not None
                and float(e["score"]) < float(e.get("full_score") or 0)]
        tp = textbook_by_ind.get(ind, {})
        card = next((c for c in tp.get("learning_cards", [])), None)
        card_id = card["material_id"] if card else ind
        pages = tp.get("textbook_pages", [])
        page = pages[0] if pages else None
        ind_questions = tp.get("questions", [])
        avail = node_available.get(nid, set())
        node_questions = [q for q in ind_questions if not avail or q["material_id"] in avail]

        # ---- 第一层：本题观察 ----
        findings = []
        for e in scored:
            iid = e.get("item_id", "")
            defs = _rubric_defs(item_by_id.get(iid, {}).get("rubric_points", ""))
            kv = _rubric_scores(e.get("rubric_scores", ""))
            lost_points = [
                {"point_id": pid, "label": defs.get(pid, {}).get("label", pid),
                 "earned": earned, "full": defs.get(pid, {}).get("full", earned)}
                for pid, earned in kv.items() if earned < defs.get(pid, {}).get("full", earned)
            ]
            mis = _mismatch(e)
            score_v = float(e["score"]) if e.get("score") is not None else None
            full_v = float(e.get("full_score") or 0)
            is_zero = score_v == 0 and full_v > 0
            is_partial = score_v is not None and 0 < score_v < full_v
            is_lost = is_zero or is_partial
            if not mis and not is_lost:
                continue
            # kind 记录「作答问题」；score_status 记录「得分状态」，二者分开，零分不再冒充部分得分
            score_status = "有效零分" if is_zero else ("部分得分" if is_partial else "满分")
            kind = "过程与填答不一致" if mis else ("有效零分" if is_zero else "部分得分")
            if mis:
                fact, inference, action = mis["fact"], mis["inference"], mis["action"]
                if is_zero:
                    fact = f"{iid} 官方得分 0/{full_v:g}（有效零分，真实作答而非缺记录）。" + fact
            elif is_zero:
                lp = "、".join(f"{p['label']}（{p['earned']:g}/{p['full']:g}）" for p in lost_points) or "未提供评分点明细"
                fact = (f"{iid} 官方得分 0/{full_v:g}，为有效零分（真实作答，不是缺记录）；"
                        f"失分评分点：{lp}。")
                inference = "零分是真实作答结果；需核对是整题误读还是逐步错误，不按缺记录处理。"
                action = f"先核对 {iid} 的作答与评分点，区分整题误读与步骤错误，再做同指标对照题。"
            else:
                lp = "、".join(f"{p['label']}（{p['earned']:g}/{p['full']:g}）" for p in lost_points) or "未提供评分点明细"
                fact = (f"{iid} 官方得分 {score_v:g}/{full_v:g}，"
                        f"失分评分点：{lp}。")
                inference = "失分集中在上述评分点对应的步骤；具体原因需结合本人核对。"
                action = f"先核对 {iid} 失分评分点对应的步骤，再做同指标对照题。"
            findings.append({
                "item_id": iid, "evidence_id": e["evidence_id"], "record_id": e["record_id"],
                "kind": kind, "score_status": score_status,
                "score": e.get("score"), "full_score": e.get("full_score"),
                "lost_points": lost_points, "fact": fact, "inference": inference, "action": action,
                **({"last_expr": mis["last_expr"], "final_answer": mis["final"]} if mis else {}),
            })
        question_case = ("部分得分" if any(0 < float(e["score"]) < float(e.get("full_score") or 0) for e in lost)
                         else ("有效零分" if lost else ("满分" if scored else None)))

        # ---- 第二层：考点覆盖 ----
        sufficient = len(scored) >= MIN_EVIDENCE
        if not sufficient:
            coverage_case = "记录不足"
        elif rate is not None and rate >= GOOD_THRESHOLD:
            coverage_case = "表现较好"
        elif s == 0 and f > 0:
            coverage_case = "有效零分"
        else:
            coverage_case = "部分得分"
        coverage = {
            "event_count": len(scored), "min_evidence": MIN_EVIDENCE, "sufficient": sufficient,
            "note": (f"该测验下该考点有效记录 {len(scored)} 条"
                     + ("，达到重新判断所需的最少记录数。" if sufficient
                        else f"，少于 {MIN_EVIDENCE} 条，不足以判断整体掌握程度。")),
        }

        # 补测候选题：本节点可用 ∩ 同指标，优先未做过
        not_done_qs = [q for q in node_questions if q["material_id"] not in done_items]
        pretest_qs = (not_done_qs or node_questions)[:PRETEST_MAX]
        pretest_ids = [q["material_id"] for q in pretest_qs]

        facts = [
            f"在 {nid} 的 {ind}（{ind_requirement.get(ind, '')}）上，已评分有效记录 {len(scored)} 条，"
            f"累计 {s:g}/{f:g} 分，得分率 {'—' if rate is None else f'{rate:.2%}'}。",
            f"列入的证据编号为 {', '.join(evidence_ids[:6])}{'…' if len(evidence_ids) > 6 else ''}。",
        ]
        if lost:
            n_zero = sum(1 for e in lost if float(e["score"]) == 0)
            n_partial = len(lost) - n_zero
            seg = []
            if n_partial:
                seg.append(f"{n_partial} 条部分得分")
            if n_zero:
                seg.append(f"{n_zero} 条有效零分（真实作答，非缺记录）")
            facts.append("其中 " + "、".join(seg) + "，涉及题目 "
                         + "、".join(sorted({e['item_id'] for e in lost})[:8]) + "。")
        if pending:
            facts.append(f"另有 {len(pending)} 条待评分，未计入得分率。")
        if unanswered:
            facts.append(f"另有 {len(unanswered)} 条未作答，按状态保留、未当作 0 分。")
        facts.append(coverage["note"])

        # ---- 主任务选取（通用规则） ----
        secondary_actions: List[dict] = []
        mismatch = next((x for x in findings if x["kind"] == "过程与填答不一致"), None)
        if mismatch:
            resource = {
                "material_id": card_id, "type": "对照练习/学习卡", "kind": "question_focus",
                "reason": (f"官方评分点显示 {mismatch['item_id']} 末项失分且过程/填答不一致；"
                           f"{card_id} 覆盖 {ind}，可对照失分步骤；教材 "
                           f"{page['material_id'] if page else '待定位'} 第 "
                           f"{page['pdf_page'] if page else '待定位'} 页为同知识点例题位置。"),
                "steps": [
                    {"order": 1,
                     "title": f"核对 {mismatch['record_id']} 最后一行：{mismatch['fact']}",
                     "when_stuck": "先提示只看符号与系数，再提示可用分配律逐项核对；先不公布答案。"},
                    {"order": 2, "title": "用同指标对照题再练一遍，最后一行与填答栏分别写",
                     "when_stuck": "先提示写出每一步依据，再提示对照末项评分点自查。"},
                ],
                "review": (f"复查时看同类评分点是否从 0 变为满分，并记录新的作答证据编号；"
                           f"官方 {float(mismatch['score'] or 0):g}/{float(mismatch['full_score'] or 0):g} 评分保持不变。"),
            }
            inference = mismatch["inference"]
            observe = ("先核实本题过程末行与最终填答为何不一致；"
                       + ("该考点记录仍不足，需补充观察后再判断整体掌握。" if not sufficient
                          else "补练后看同一评分点是否从 0 变为满分。"))
            if not sufficient:
                secondary_actions.append({
                    "kind": "补充观察", "material_id": card_id, "question_ids": pretest_ids,
                    "reason": (f"该考点在本测验仅 {len(scored)} 条有效记录（<{MIN_EVIDENCE}），"
                               "本题结论不代表整体掌握；先收集新证据。"),
                    "review": f"新增记录按同一修订版本归并规则纳入，有效记录≥{MIN_EVIDENCE} 后重新计算考点得分率。",
                })
        elif not sufficient:
            steps = [
                {"order": i + 1,
                 "title": f"完成 {q['material_id']}（{q['name'][:40]}），观察{'未做过的新题表现' if q['material_id'] not in done_items else '复练表现'}",
                 "when_stuck": "先提示检查已知条件和目标量，再提示可用方法（先不公布答案）。"}
                for i, q in enumerate(pretest_qs)
            ] or [{"order": 1, "title": f"完成 {card_id} 的对应要求",
                   "when_stuck": "先提示检查已知条件和目标量，再提示可用方法（先不公布答案）。"}]
            resource = {
                "material_id": card_id, "type": "短测/观察任务", "kind": "coverage",
                "reason": ("指标记录不足，先用同指标题目收集新证据，不据此下结论。"
                           + (f"本节点可用补测题：{'、'.join(pretest_ids)}。" if pretest_ids
                              else "本节点暂无可用的同指标新题，需教师补充资源。")),
                "steps": steps,
                "review": f"新增记录按同一 version 归并规则纳入后，若有效记录≥{MIN_EVIDENCE} 再计算得分率。",
            }
            inference = f"有效记录仅 {len(scored)} 条，不足以判断该指标是否掌握。"
            observe = f"补一条针对该指标的短测或观察任务；看到至少 {MIN_EVIDENCE} 条有效已评分记录后再重新判断。"
        elif coverage_case == "表现较好":
            not_done = [q for q in node_questions if q["material_id"] not in done_items]
            varied = [q for q in not_done if q.get("question_type") not in done_full_types]
            pick_q = (varied or not_done or node_questions or [None])[0]
            if pick_q:
                repractice = pick_q["material_id"] in done_items
                resource = {
                    "material_id": pick_q["material_id"],
                    "type": "复练（已做过，如实标注）" if repractice else "迁移变式",
                    "kind": "transfer",
                    "reason": (
                        f"在已观察题目上表现较好；{pick_q['material_id']} "
                        + ("该生已做过，安排复练并如实标注，观察稳定性。" if repractice else
                           f"该生未做过{'且题型与已满分题不同' if pick_q.get('question_type') not in done_full_types else ''}，"
                           "可检验是否真正理解而非记住答案。")),
                    "steps": [
                        {"order": 1, "title": f"先独立做 {pick_q['material_id']}，不提示",
                         "when_stuck": "先提示找出与已做题不同的条件，再提示可迁移的方法。"},
                    ],
                    "review": _transfer_review(pick_q),
                }
            else:
                resource = {
                    "material_id": card_id, "type": "迁移变式", "kind": "transfer",
                    "reason": f"表现较好，但本节点暂无可用的同指标新题；先用 {card_id} 拓展，需教师补充变式资源。",
                    "steps": [{"order": 1, "title": f"用 {card_id} 的方法解一道教师补充的新情境题",
                               "when_stuck": "先提示找出新情境的已知量，再提示可迁移的方法。"}],
                    "review": _transfer_review(None),
                }
            inference = f"已观察题目上的得分率 {rate:.0%}，表现稳定；是否具备迁移能力仍需新题验证。"
            observe = "安排一道同指标但条件变化的变式，观察能否把方法用到新情境。"
        elif coverage_case == "有效零分":
            zero_item = lost[0]["item_id"] if lost else (scored[0]["item_id"] if scored else "")
            resource = {
                "material_id": card_id, "type": "对照练习/学习卡", "kind": "question_focus",
                "reason": (f"有效记录 {len(scored)} 条已达判断门槛，但合计 0/{f:g}：真实作答全为零分，"
                           f"属有记录、需巩固，不是缺记录；用 {card_id} 对照 {zero_item} 的零分步骤。"),
                "steps": [
                    {"order": 1, "title": f"核对 {zero_item} 的零分作答：整题误读还是逐步错误",
                     "when_stuck": "先提示读题圈出已知量与目标量，再提示第一步该写什么；先不公布答案。"},
                    {"order": 2, "title": "用同指标对照题练一遍，写出每步依据",
                     "when_stuck": "先提示写出每一步依据，再提示对照评分点自查。"},
                ],
                "review": "复查时看该评分点是否从 0 变为得分，并记录新的作答证据编号；原零分评分保持不变。",
            }
            inference = f"有效记录 {len(scored)} 条已达门槛，合计 0/{f:g}：有记录、需巩固（0/{f:g}），不按缺记录处理。"
            observe = "先核对零分作答是整题误读还是逐步错误，再安排同指标对照题巩固。"
        else:
            lost_item = lost[0]["item_id"] if lost else (scored[0]["item_id"] if scored else "")
            resource = {
                "material_id": card_id, "type": "对照练习/学习卡", "kind": "question_focus",
                "reason": f"同指标学习卡或教材例题能直接对照 {lost_item} 的失分步骤。",
                "steps": [
                    {"order": 1, "title": f"核对 {lost_item} 的失分评分点",
                     "when_stuck": "先提示检查最后一行符号/结果是否与过程一致，再提示可用方法。"},
                    {"order": 2, "title": "用同指标对照题练一遍",
                     "when_stuck": "先提示写出每一步依据，再提示对照评分点自查。"},
                ],
                "review": "复查时看该评分点是否得分，并记录新的作答证据编号。",
            }
            inference = f"部分失分集中在 {lost_item}；先核对失分评分点对应的步骤。"
            observe = "补练后看同一评分点是否从 0 变为满分，而不是只看总分。"

        # 教师调整候选：同指标学习卡+题目，标注做过/满分/本节点可用
        candidates = []
        for m in (tp.get("learning_cards", []) + ind_questions):
            mid = m["material_id"]
            candidates.append({
                "material_id": mid, "material_type": m.get("material_type", ""),
                "question_type": m.get("question_type", ""), "name": m.get("name", ""),
                "done": mid in done_items,
                "done_full": mid in done_full_items,
                "node_available": (not avail) or mid in avail or m.get("material_type") == "学习卡",
            })

        out.append({
            "student_id": sid, "node_id": nid, "indicator_id": ind,
            "case": coverage_case, "question_case": question_case,
            "question_findings": findings,
            "coverage": coverage,
            "facts": facts, "inference": inference, "observe": observe,
            "evidence_ids": evidence_ids, "pending_event_count": len(pending),
            "unanswered_event_count": len(unanswered),
            "score": s, "full_score": f, "rate": rate, "event_count": len(scored),
            "resource": resource, "secondary_actions": secondary_actions,
            "pretest_questions": pretest_ids, "candidates": candidates,
            "textbook": {
                "material_id": page["material_id"] if page else "待定位",
                "pdf_page": page["pdf_page"] if page else None,
                "printed": page["printed"] if page else tp.get("textbook_range", "待定位"),
                "anchor": page["anchor"] if page else "未核对到具体页，显示能确认的范围",
                "verified": bool(page and page.get("verified")),
            },
            "special": [x for x in findings if x["kind"] == "过程与填答不一致"],
        })
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "threshold": {"advice": ADVICE_THRESHOLD, "good": GOOD_THRESHOLD, "min_evidence": MIN_EVIDENCE},
            "diagnoses": out}


def build_case_book(src: Path, diagnoses: dict) -> dict:
    """三类展示案例：只选择对象、排序与讲解重点，不覆盖诊断结论。

    选择规则（通用）：
      部分得分 → 优先有「过程/填答不一致」本题证据的诊断；
      表现较好 → 覆盖判定为表现较好，按有效记录数与得分率排序；
      记录不足 → 覆盖判定为记录不足，按有效记录数升序。
    演示编号 D01/D15/D17（及变体 V01/V15/V17）仅作同分时的展示偏好。
    """
    ds = diagnoses["diagnoses"]
    demo_pref = {"部分得分": ("D01", "V01"), "表现较好": ("D15", "V15"), "记录不足": ("D17", "V17")}

    def pick(cands: List[dict], case_name: str, sort_key) -> Optional[dict]:
        if not cands:
            return None
        pref = demo_pref.get(case_name, ())
        return sorted(cands, key=lambda d: (d["student_id"] not in pref, sort_key(d)))[0]

    partial = pick(
        [d for d in ds if any(x["kind"] == "过程与填答不一致" and x.get("score_status") != "有效零分"
                              for x in d["question_findings"])]
        or [d for d in ds if d["case"] == "部分得分"],
        "部分得分", lambda d: (-len(d["question_findings"]), -d["event_count"]))
    good = pick([d for d in ds if d["case"] == "表现较好"], "表现较好",
                lambda d: (-d["event_count"], -(d["rate"] or 0)))
    insufficient = pick([d for d in ds if d["case"] == "记录不足"], "记录不足",
                        lambda d: (d["event_count"], d["student_id"]))

    focus = {
        "部分得分": "讲解重点：本题的具体失分证据（过程末行与最终填答、失分评分点），以及考点覆盖是否足以推广结论。",
        "表现较好": "讲解重点：在哪些题上稳定得分，迁移变式如何选（未做过、条件有变化）。",
        "记录不足": "讲解重点：缺什么证据、先补测哪些题、看到什么结果才重新判断。",
    }
    cases = []
    for name, d in [("部分得分", partial), ("表现较好", good), ("记录不足", insufficient)]:
        if not d:
            continue
        level = ("本题" if name == "部分得分" and d["question_findings"] else "考点覆盖")
        cases.append({
            **d,
            "case_name": name, "showcase_level": level,
            "focus": focus[name],
            "selection_reason": (
                f"按通用规则选中：{name}（{level}层面）。"
                + (f"本题证据 {len(d['question_findings'])} 条；考点覆盖 {d['event_count']} 条有效记录。"
                   if level == "本题" else f"考点覆盖 {d['event_count']} 条有效记录，得分率 "
                   f"{'—' if d['rate'] is None else format(d['rate'], '.2%')}。")
            ),
        })
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "selection_rule": ("案例册只选择展示对象与讲解重点，不修改诊断；"
                               "部分得分优先有过程/填答不一致证据者，表现较好按记录数与得分率，"
                               "记录不足按记录数升序；演示编号仅作同分偏好。"),
            "cases": cases}


def write_exports(out_dir: Path, src: Path, curriculum_map: dict, diagnoses: dict, case_book: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "curriculum_map.json": curriculum_map,
        "diagnoses.json": diagnoses,
        "case_book.json": case_book,
    }
    for name, data in payload.items():
        (out_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # CSV：每条建议一行，保留可定位 ID；两层结论分列
    with (out_dir / "recommendations.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["student_id", "node_id", "indicator_id", "case", "question_case",
                    "evidence_ids", "material_id", "reason", "review",
                    "score", "full_score", "event_count"])
        for d in diagnoses["diagnoses"]:
            w.writerow([d["student_id"], d["node_id"], d["indicator_id"], d["case"],
                        d.get("question_case") or "",
                        " ".join(d["evidence_ids"]), d["resource"]["material_id"],
                        d["resource"]["reason"], d["resource"]["review"],
                        d["score"], d["full_score"], d["event_count"]])
    print(f"[analysis] {out_dir}")


def run(src: Path, csv_dir: Path, out_dir: Path) -> None:
    cm = build_curriculum_map(src, csv_dir)
    dg = build_diagnoses(src, csv_dir, cm)
    cb = build_case_book(src, dg)
    write_exports(out_dir, src, cm, dg, cb)
    print(f"[analysis] curriculum={len(cm['indicators'])} diagnoses={len(dg['diagnoses'])} "
          f"cases={len(cb['cases'])}")


def main() -> None:
    ap = argparse.ArgumentParser(description="课标映射 / 诊断 / 建议 / 导出")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--src", required=True)
    p.add_argument("--csv-dir", required=True)
    p.add_argument("--out", required=True)
    args = ap.parse_args()
    run(Path(args.src), Path(args.csv_dir), Path(args.out))


if __name__ == "__main__":
    main()
