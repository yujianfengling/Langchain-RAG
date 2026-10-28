"""智能组卷（2026-10-24）

从题库按「知识点覆盖 + 题型 + 时长」选出一套卷子（预览，不落库）。
先做**可解释的选题器**，不调用模型、不编造题目：
- 每个知识点轮转取题，避免一张卷子全压在一个知识点上；
- 指定知识点没有题时进 gaps，如实告诉老师；
- 题目一律走 quiz_service.to_public，答案不下发；
- 时长按题型估算，超预算时停止加题。

后续可在这个骨架上加 IRT 难度/区分度与自动补题；当前不假装有这些数据。
"""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any, Dict, List, Optional

from services.learning_data import _get_db
from services.quiz_service import QUESTION_TYPES, get_question_admin, to_public

# 每种题型的预估作答分钟数（用于组卷时长预算）
TYPE_MINUTES = {"single_choice": 2, "judgement": 1, "short_answer": 6}
DEFAULT_COUNT = 10
MAX_COUNT = 50


def _normalize_types(types: Optional[List[str]]) -> List[str]:
    if not types:
        return list(QUESTION_TYPES)
    return [t for t in types if t in QUESTION_TYPES] or list(QUESTION_TYPES)


def build_preview(subject_id: str,
                  knowledge_points: Optional[List[str]] = None,
                  count: int = DEFAULT_COUNT,
                  types: Optional[List[str]] = None,
                  duration_min: Optional[int] = None) -> Dict[str, Any]:
    """按知识点轮转选一套题，返回预览（不含答案）。"""
    if not subject_id:
        return {"items": [], "coverage": [], "gaps": [], "selected_count": 0,
                "available_count": 0, "estimated_minutes": 0}
    count = max(1, min(int(count or DEFAULT_COUNT), MAX_COUNT))
    allowed_types = _normalize_types(types)
    requested = [str(k).strip() for k in (knowledge_points or []) if str(k).strip()]

    sql = ("SELECT * FROM quiz_questions WHERE subject_id=? "
           "AND (deleted_at IS NULL OR deleted_at='')")
    args: List[Any] = [subject_id]
    if requested:
        marks = ",".join("?" for _ in requested)
        sql += f" AND knowledge_point_name IN ({marks})"
        args.extend(requested)
    if allowed_types:
        marks = ",".join("?" for _ in allowed_types)
        sql += f" AND question_type IN ({marks})"
        args.extend(allowed_types)
    sql += " ORDER BY created_at DESC, rowid DESC"

    conn = _get_db()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()

    groups: Dict[str, List[Any]] = {}
    for row in rows:
        name = (row["knowledge_point_name"] or "未标注").strip() or "未标注"
        groups.setdefault(name, []).append(row)
    order = requested or sorted(groups.keys())
    for name in order:
        groups.setdefault(name, [])
    if not requested:
        order = sorted(groups.keys())

    selected_rows: List[Any] = []
    selected_minutes = 0
    cursor = {name: 0 for name in order}
    while len(selected_rows) < count:
        progressed = False
        for name in order:
            if len(selected_rows) >= count:
                break
            bucket = groups.get(name) or []
            index = cursor.get(name, 0)
            if index >= len(bucket):
                continue
            row = bucket[index]
            cursor[name] = index + 1
            minutes = TYPE_MINUTES.get(row["question_type"], 2)
            if (duration_min and selected_rows and
                    selected_minutes + minutes > int(duration_min)):
                continue
            selected_rows.append(row)
            selected_minutes += minutes
            progressed = True
        if not progressed:
            break

    items = []
    for row in selected_rows:
        public = to_public(row)
        public["estimated_minutes"] = TYPE_MINUTES.get(row["question_type"], 2)
        items.append(public)

    selected_by_kp: Dict[str, int] = {}
    for row in selected_rows:
        name = (row["knowledge_point_name"] or "未标注").strip() or "未标注"
        selected_by_kp[name] = selected_by_kp.get(name, 0) + 1

    coverage = []
    for name in order:
        coverage.append({
            "knowledge_point": name,
            "selected": selected_by_kp.get(name, 0),
            "available": len(groups.get(name) or []),
        })
    gaps = [item["knowledge_point"] for item in coverage if item["selected"] == 0]

    return {
        "subject_id": subject_id,
        "items": items,
        "coverage": coverage,
        "gaps": gaps,
        "selected_count": len(items),
        "available_count": len(rows),
        "estimated_minutes": selected_minutes,
        "types": allowed_types,
        "duration_min": duration_min,
    }


TYPE_LABELS = {"single_choice": "单选题", "judgement": "判断题", "short_answer": "简答题"}


def export_docx(subject_id: str,
                knowledge_points: Optional[List[str]] = None,
                count: int = DEFAULT_COUNT,
                types: Optional[List[str]] = None,
                duration_min: Optional[int] = None,
                title: str = "智能组卷") -> Dict[str, Any]:
    """生成可下载的 Word 试卷 + 参考答案（答案只给老师）。"""
    preview = build_preview(subject_id, knowledge_points, count, types, duration_min)
    if not preview["items"]:
        raise ValueError("没有可导出的题目（题库为空或筛选条件过窄）")

    from docx import Document
    doc = Document()
    doc.add_heading(title, 0)
    doc.add_paragraph(
        f"共 {preview['selected_count']} 题 · 预估 {preview['estimated_minutes']} 分钟 · "
        f"缺口知识点：{'、'.join(preview['gaps']) or '无'}")

    for index, item in enumerate(preview["items"], start=1):
        admin = get_question_admin(item["id"])
        label = TYPE_LABELS.get(item.get("type") or "", "题目")
        stem = (admin or {}).get("stem") or item.get("question") or ""
        doc.add_paragraph(f"{index}. [{label}] {stem}")
        options = []
        if admin:
            try:
                options = json.loads(admin["options_json"] or "[]")
            except (TypeError, ValueError):
                options = []
        for option in options:
            doc.add_paragraph(f"    {option.get('id')}. {option.get('text')}")

    doc.add_page_break()
    doc.add_heading("参考答案", level=1)
    for index, item in enumerate(preview["items"], start=1):
        admin = get_question_admin(item["id"]) or {}
        options = []
        try:
            options = json.loads(admin.get("options_json") or "[]")
        except (TypeError, ValueError):
            options = []
        correct = admin.get("correct_option_id")
        correct_text = next((o.get("text") for o in options if o.get("id") == correct), "")
        answer = f"{correct} {correct_text}".strip() if correct else (
            admin.get("reference_answer") or "（无参考答案）")
        doc.add_paragraph(f"{index}. 答案：{answer}")
        if admin.get("explanation"):
            doc.add_paragraph(f"   解析：{admin['explanation']}")

    buffer = BytesIO()
    doc.save(buffer)
    return {"content": buffer.getvalue(), "preview": preview,
            "filename": "paper.docx"}


__all__ = ["build_preview", "export_docx", "TYPE_MINUTES", "TYPE_LABELS",
           "DEFAULT_COUNT", "MAX_COUNT"]
