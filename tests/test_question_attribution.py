"""题目知识点归属：真正的反例测试（2026-10-05）

背景：出题环节曾把整批题目套用请求的知识点，"最坏时间复杂度"的题被计入
"快速排序稳定性判断"的掌握度/错题/复习。这里用反例锁定修复：
- 复杂度题（归属待确认）答错 → 不影响稳定性掌握度，也不生成稳定性复习；
- 稳定性题答错 → 影响对应知识点；
- 综合练习按每题实际归属分组；
- 归属待确认的题保留作答记录但不污染精确掌握估计。
"""
import uuid

from services import quiz_service, mastery_service, review_service, kp_attribution, note_service
from services import learning_handlers  # noqa: F401  注册作答事件处理器（掌握度/复习/记忆）

# 每次运行使用独立学科/用户：避免 pytest 数据目录跨运行复用导致断言受历史数据影响
_RUN = uuid.uuid4().hex[:8]
SUBJECT = f"course_attr_{_RUN}"
USER = f"user_attr_{_RUN}"
STABILITY = "快速排序稳定性判断"
COMPLEXITY = "快速排序最坏时间复杂度"


# 每题带本次运行唯一标记：避免题库按题干去重后复用旧题，导致首答被判成"复习重做"
_MARK = uuid.uuid4().hex[:6]


def _stability_q():
    return {"type": "judgement", "question": f"快速排序是不稳定的排序算法（{_MARK}）。",
            "options": ["正确", "错误"], "answer": "正确",
            "explanation": "分区交换会打乱相等元素的相对次序，所以不稳定。"}


def _complexity_q():
    return {"type": "judgement", "question": f"快速排序的最坏时间复杂度是 O(n^2)（{_MARK}）。",
            "options": ["正确", "错误"], "answer": "正确",
            "explanation": "每次 pivot 取到最值时退化为 O(n^2)。"}


def _persist(kp, q):
    return quiz_service.persist_questions(SUBJECT, kp, [q], source="test")[0]


def _wrong_option_id(q):
    # 判断题正确选项是"正确"，答错就选另一个
    for o in q["options"]:
        if o["text"] != "正确":
            return o["id"]
    return q["options"][-1]["id"]


def _submit_wrong(q):
    # 每次用新的幂等令牌：避免跨测试会话复用导致命中幂等早返回、事件不再投递
    return quiz_service.submit_attempt(USER, q["id"], _wrong_option_id(q),
                                       "attr-" + uuid.uuid4().hex, SUBJECT)


def test_pending_question_keeps_attempt_but_no_precise_mastery_or_review():
    q = _persist(None, _complexity_q())          # 归属待确认
    assert q["knowledge_point"] == "待确认"
    res = _submit_wrong(q)
    assert res["is_correct"] is False

    # 作答记录保留（判分与留痕照常）
    attempts = quiz_service.list_attempts(USER, SUBJECT, None, 50)
    assert any(a["id"] == res["attempt_id"] for a in attempts)
    assert res["knowledge_point"] == "待确认"

    # 不污染任何精确知识点的掌握度
    names = {p["knowledge_point"] for p in mastery_service.list_mastery(USER, SUBJECT)}
    assert STABILITY not in names
    assert COMPLEXITY not in names

    # 也不据此生成稳定性复习
    reviews = review_service.list_review_tasks(USER, SUBJECT, "PENDING")
    assert not any(r.get("knowledge_point_name") == STABILITY for r in reviews)


def test_stability_wrong_affects_stability_point():
    q = _persist(STABILITY, _stability_q())
    res = _submit_wrong(q)
    assert res["is_correct"] is False
    assert res["attempt_kind"] == "independent"

    points = {p["knowledge_point"]: p for p in mastery_service.list_mastery(USER, SUBJECT)}
    assert STABILITY in points
    assert points[STABILITY]["evidence_breakdown"]["independent_wrong"] >= 1
    reviews = review_service.list_review_tasks(USER, SUBJECT, "PENDING")
    assert any(r.get("knowledge_point_name") == STABILITY for r in reviews)


def test_comprehensive_grouping_uses_per_question_kp():
    by_kp, pending = kp_attribution.resolve_items(
        [{"question": "稳定吗", "knowledge_point": STABILITY},
         {"question": "最坏复杂度", "knowledge_point": COMPLEXITY},
         {"question": "无所属"}],
        [STABILITY, COMPLEXITY])
    assert STABILITY in by_kp
    assert COMPLEXITY in by_kp
    assert len(pending) == 1


def test_normalize_quiz_no_longer_stamps_batch_topic():
    quiz = note_service._normalize_quiz(
        [{"question": "快速排序的最坏时间复杂度是？", "type": "single_choice",
          "options": ["A", "B"], "answer": "A"}],
        STABILITY)
    assert quiz[0]["knowledge_point"] is None


def test_verify_attribution_marks_mismatch_pending():
    def ask(_prompt):
        return ('{"assignments":['
                '{"index":1,"tests":"稳定性","knowledge_point":"快速排序稳定性判断"},'
                '{"index":2,"tests":"时间复杂度","knowledge_point":"待确认"}]}')
    out = kp_attribution.verify_attribution(
        [{"question": "快速排序稳定吗"}, {"question": "最坏时间复杂度"}],
        [STABILITY], ask)
    assert out[0] == STABILITY
    assert out[1] is None
