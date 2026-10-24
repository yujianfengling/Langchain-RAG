"""BKT 知识追踪回归测试（2026-10-24）

- 纯函数：答对提升、答错下降、复习只做部分更新；
- 数据层：按知识点聚合、after_reveal 不计证据、置信度随样本量增长。
"""

import uuid

from services import knowledge_tracing
from services.learning_data import _get_db, _new_id, _now


def test_update_probability_moves_in_right_direction():
    p0 = knowledge_tracing.DEFAULT_PARAMS["prior"]
    up = knowledge_tracing.update_probability(p0, True)
    down = knowledge_tracing.update_probability(p0, False)
    assert up > p0 > down
    assert 0.0 <= up <= 1.0 and 0.0 <= down <= 1.0


def test_estimate_sequence_confidence_and_prior():
    low = knowledge_tracing.estimate_sequence([(True, 1.0)] * 2)
    high = knowledge_tracing.estimate_sequence([(True, 1.0)] * 10)
    assert high["p_known"] > low["p_known"]
    assert high["confidence"] == 1.0
    assert low["confidence"] == 0.2


def test_estimate_sequence_review_weight_is_partial():
    independent = knowledge_tracing.estimate_sequence([(True, 1.0)] * 4)
    reviewed = knowledge_tracing.estimate_sequence([(True, 0.5)] * 4)
    assert independent["p_known"] > reviewed["p_known"]


def _seed_attempts(user_id: str, subject_id: str):
    rows = [
        ("函数", 1, "independent", 10),
        ("函数", 1, "independent", 11),
        ("函数", 0, "review", 12),
        ("极限", 0, "independent", 13),
        ("极限", 0, "after_reveal", 14),
    ]
    conn = _get_db()
    try:
        for index, (kp, correct, kind, minute) in enumerate(rows):
            created = f"2026-10-24T10:{minute:02d}:00"
            conn.execute(
                """INSERT INTO quiz_attempts
                   (id, user_id, subject_id, question_id, knowledge_point_id,
                    knowledge_point_name, attempt_no, attempt_kind, is_correct,
                    grade_status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (_new_id("att"), user_id, subject_id, "q_" + str(index), "kp_" + kp,
                 kp, index + 1, kind, correct, "graded", created),
            )
        conn.commit()
    finally:
        conn.close()


def test_estimate_user_groups_and_ignores_after_reveal():
    tag = uuid.uuid4().hex[:8]
    user, subject = "bkt_u_" + tag, "bkt_s_" + tag
    _seed_attempts(user, subject)
    data = knowledge_tracing.estimate_user(subject, user)
    by_name = {item["knowledge_point"]: item for item in data["points"]}
    assert set(by_name) == {"函数", "极限"}
    assert by_name["函数"]["attempts"] == 3
    assert by_name["极限"]["attempts"] == 1, "after_reveal 不应计入 BKT 证据"
    assert by_name["极限"]["p_known"] < by_name["函数"]["p_known"]
    assert data["recommended"] in {"函数", "极限"}
    assert data["params"]["prior"] == knowledge_tracing.DEFAULT_PARAMS["prior"]


def test_bkt_recommendation_drives_map_next_node():
    from services import knowledge_map
    from services.learning_data import _new_id

    tag = uuid.uuid4().hex[:8]
    user, subject = "bkt_map_u_" + tag, "bkt_map_s_" + tag
    skeleton, _ = knowledge_map._validate_map(
        {"title": "t", "nodes": [
            {"label": "A", "level": "point", "source_docs": []},
            {"label": "B", "level": "point", "source_docs": []},
        ]}, [])
    knowledge_map._save_map(subject, {
        "map_id": "map_" + tag, "title": "t", "nodes": skeleton["nodes"],
        "uncovered": [], "source_signature": "sig", "diagnostics": [],
    })
    conn = _get_db()
    try:
        for index, (kp, correct) in enumerate([("A", 1), ("A", 1), ("B", 0), ("B", 0)]):
            conn.execute(
                """INSERT INTO quiz_attempts
                   (id, user_id, subject_id, question_id, knowledge_point_id,
                    knowledge_point_name, attempt_no, attempt_kind, is_correct,
                    grade_status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (_new_id("att"), user, subject, "q" + str(index), "kp_" + kp,
                 kp, index + 1, "independent", correct, "graded", _now()))
        conn.commit()
    finally:
        conn.close()

    data = knowledge_map.map_with_progress(user, subject)
    assert data["next_node"]["label"] == "B"
    assert "BKT" in data["next_node"]["reason"]


def test_estimate_user_empty():
    data = knowledge_tracing.estimate_user("no_such_subject", "no_such_user")
    assert data["points"] == []
