"""智能组卷回归测试（2026-10-24）

- 知识点轮转覆盖，缺口如实返回；
- 题型/时长过滤生效；
- 预览绝不下发标准答案。
"""

import json
import uuid

from services import paper_builder
from services.learning_data import _get_db, _now


def _seed(subject_id: str, rows):
    conn = _get_db()
    try:
        for index, (kp, qtype, options, answer) in enumerate(rows):
            qid = "q_" + uuid.uuid4().hex[:10]
            conn.execute(
                """INSERT INTO quiz_questions
                   (id, subject_id, knowledge_point_id, knowledge_point_name,
                    question_type, stem, options_json, correct_option_id,
                    explanation, content_hash, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (qid, subject_id, "kp_" + kp, kp, qtype,
                 f"{kp} 的第 {index} 题", json.dumps(options, ensure_ascii=False),
                 answer, "解析", "hash_" + uuid.uuid4().hex, _now()),
            )
        conn.commit()
    finally:
        conn.close()


def test_preview_covers_points_and_reports_gaps():
    subject = "paper_" + uuid.uuid4().hex[:8]
    _seed(subject, [
        ("函数", "single_choice", [{"id": "A", "text": "1"}], "A"),
        ("函数", "single_choice", [{"id": "A", "text": "2"}], "A"),
        ("极限", "judgement", [{"id": "T", "text": "对"}, {"id": "F", "text": "错"}], "T"),
        ("极限", "judgement", [{"id": "T", "text": "对"}, {"id": "F", "text": "错"}], "T"),
        ("导数", "short_answer", [], None),
    ])
    out = paper_builder.build_preview(
        subject, knowledge_points=["函数", "极限", "导数", "不存在"], count=4)
    assert out["selected_count"] == 4
    assert out["gaps"] == ["不存在"]
    assert {item["knowledge_point"] for item in out["items"]} == {"函数", "极限", "导数"}
    for item in out["items"]:
        assert "correct_option_id" not in item, "预览不能下发答案"
        assert "reference_answer" not in item


def test_preview_filters_type_and_duration():
    subject = "paper_" + uuid.uuid4().hex[:8]
    _seed(subject, [
        ("函数", "single_choice", [{"id": "A", "text": "1"}], "A"),
        ("函数", "single_choice", [{"id": "A", "text": "2"}], "A"),
        ("极限", "short_answer", [], None),
    ])
    only_single = paper_builder.build_preview(subject, types=["single_choice"], count=10)
    assert only_single["selected_count"] == 2
    assert all(item["type"] == "single_choice" for item in only_single["items"])

    timed = paper_builder.build_preview(subject, count=10, duration_min=2)
    assert timed["selected_count"] == 1, "2 分钟只能放一道单选（预估 2 分钟）"
    assert timed["estimated_minutes"] == 2


def test_export_docx_contains_answer_key():
    from io import BytesIO

    from docx import Document

    subject = "paper_" + uuid.uuid4().hex[:8]
    _seed(subject, [
        ("函数", "single_choice", [{"id": "A", "text": "1"}, {"id": "B", "text": "2"}], "A"),
        ("极限", "short_answer", [], None),
    ])
    out = paper_builder.export_docx(subject, count=2, title="测试卷")
    assert out["content"][:2] == b"PK"
    doc = Document(BytesIO(out["content"]))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "测试卷" in text
    assert "参考答案" in text
    assert "答案：A" in text


def test_preview_empty_subject():
    out = paper_builder.build_preview("no_such_subject", count=5)
    assert out["items"] == []
    assert out["selected_count"] == 0
