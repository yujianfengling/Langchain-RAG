"""间隔复习语义（2026-10-05）：到期前 / 到期后 / 提前练习。

背景：学生用"同知识点的新题独立做对"提前练习时，旧实现会直接关闭还没到期的
复习任务，并在同一知识点上再排一次延后复测——等于把复测阶段提前消耗掉。
现在规则：
- 到期（due_date <= 今天）的复习：有效作答可关闭；
- 未到期的复习/延后复测：独立作答不关闭（提前练习不算完成）；
- 直接重做同一题（attempt_kind=review）仍按原语义关闭；
- after_reveal（看过答案）不关闭；
- 只作用于对应知识点。

注意：关闭一条复习后，系统会按间隔复现规则再排一条"延后复测"（PENDING），
所以断言看的是**被关闭的那条复习的状态**，而不是"待复习数量清零"。
"""
import uuid
from datetime import date

from services import review_service
from services.learning_handlers import _handle_quiz_attempt_review


def _new_scope():
    return (f"u_{uuid.uuid4().hex[:10]}", f"sub_{uuid.uuid4().hex[:8]}",
            f"kp_{uuid.uuid4().hex[:8]}", "测试知识点")


def _rows(user_id, subject_id, kp_id):
    return [r for r in review_service.list_review_tasks(user_id, subject_id)
            if r["knowledge_point_id"] == kp_id]


def _pending(user_id, subject_id, kp_id):
    return [r for r in _rows(user_id, subject_id, kp_id) if r["status"] == "PENDING"]


def _make_review(interval_days):
    user_id, subject_id, kp_id, kp_name = _new_scope()
    review_service.ensure_review_task(
        user_id, subject_id, kp_id, kp_name,
        reason="测试前置", interval_days=interval_days)
    return user_id, subject_id, kp_id, kp_name


def _answer_review(user_id, subject_id, kp_id, kp_name, *, is_correct=True,
                   attempt_kind="independent", attempt_id=None):
    ctx = {
        "user_id": user_id,
        "subject_id": subject_id,
        "payload": {
            "attempt_id": attempt_id or f"att_{uuid.uuid4().hex[:10]}",
            "question_id": f"q_{uuid.uuid4().hex[:8]}",
            "knowledge_point": kp_name,
            "knowledge_point_id": kp_id,
            "is_correct": is_correct,
            "attempt_kind": attempt_kind,
            "source_task_id": None,
        },
    }
    _handle_quiz_attempt_review(ctx)


def test_complete_review_respects_due_date():
    """到期前不关闭；到期后（due_on_or_before=今天）关闭。"""
    today = date.today().isoformat()

    # 到期前（5 天后）
    u1, s1, k1, _ = _make_review(interval_days=5)
    closed = review_service.complete_review_tasks(
        u1, s1, k1, reason="提前练习", due_on_or_before=today)
    assert closed == 0
    assert len(_pending(u1, s1, k1)) == 1, "未到期的复习不应被关闭"

    # 到期（今天）
    u2, s2, k2, _ = _make_review(interval_days=0)
    closed2 = review_service.complete_review_tasks(
        u2, s2, k2, reason="到期完成", due_on_or_before=today)
    assert closed2 == 1


def test_early_independent_practice_does_not_close_future_review():
    """提前练习（独立做对、复习还没到期）不得关闭未来复测。"""
    u, s, k, n = _make_review(interval_days=5)
    _answer_review(u, s, k, n, is_correct=True, attempt_kind="independent")
    pending = _pending(u, s, k)
    assert len(pending) == 1, "提前用新题做对不应关闭未到期的复习"
    assert pending[0]["due_date"] > date.today().isoformat()


def test_due_review_closed_by_independent_answer():
    """到期后，用同知识点的新题独立做对可以完成复习。"""
    u, s, k, n = _make_review(interval_days=0)
    review_id = _pending(u, s, k)[0]["id"]
    _answer_review(u, s, k, n, is_correct=True, attempt_kind="independent")
    done = [r for r in _rows(u, s, k) if r["id"] == review_id and r["status"] == "DONE"]
    assert done, "到期的复习应在有效作答后关闭"


def test_review_kind_closes_even_before_due():
    """重做同一题（review）按原语义关闭，不受到期日限制。"""
    u, s, k, n = _make_review(interval_days=5)
    review_id = _pending(u, s, k)[0]["id"]
    _answer_review(u, s, k, n, is_correct=True, attempt_kind="review")
    done = [r for r in _rows(u, s, k) if r["id"] == review_id and r["status"] == "DONE"]
    assert done, "重做同一题应关闭待办"


def test_after_reveal_does_not_close_due_review():
    """看过答案后的作答（after_reveal）不能关闭复习。"""
    u, s, k, n = _make_review(interval_days=0)
    review_id = _pending(u, s, k)[0]["id"]
    _answer_review(u, s, k, n, is_correct=True, attempt_kind="after_reveal")
    still = [r for r in _rows(u, s, k) if r["id"] == review_id and r["status"] == "PENDING"]
    assert still, "after_reveal 不应关闭复习"


def test_unrelated_knowledge_point_is_not_closed():
    """关闭只作用于对应知识点，相邻知识点的待办不受影响。"""
    u, s, k1, _ = _make_review(interval_days=0)
    k2, n2 = f"kp_{uuid.uuid4().hex[:8]}", "另一个知识点"
    review_service.ensure_review_task(u, s, k2, n2, reason="测试前置", interval_days=5)

    review_service.complete_review_tasks(u, s, k1, reason="到期完成",
                                         due_on_or_before=date.today().isoformat())
    assert all(r["status"] == "DONE" for r in _rows(u, s, k1))
    assert len(_pending(u, s, k2)) == 1, "无关知识点不应被关闭"

def _make_recheck(interval_days):
    user_id, subject_id, kp_id, kp_name = _new_scope()
    review_service.ensure_review_task(
        user_id, subject_id, kp_id, kp_name,
        reason="延后复测：上次复习已通过但尚未间隔复现", interval_days=interval_days)
    return user_id, subject_id, kp_id, kp_name


def test_early_same_question_redo_does_not_close_recheck():
    """提前重做同一道题：不得关闭未到期的延后复测，也不得重复安排复测。"""
    u, s, k, n = _make_recheck(interval_days=5)
    original = _pending(u, s, k)[0]["id"]
    _answer_review(u, s, k, n, is_correct=True, attempt_kind="review")
    rows = _rows(u, s, k)
    still = [r for r in rows if r["id"] == original and r["status"] == "PENDING"]
    assert still, "提前重做同一题不应关闭未到期的延后复测"
    assert len(rows) == 1, "不应因为提前重做而重复安排复测（阶段提前推进）"


def test_due_recheck_same_question_redo_closes():
    """到期后，重做同一道题可以完成这次复测。"""
    u, s, k, n = _make_recheck(interval_days=0)
    original = _pending(u, s, k)[0]["id"]
    _answer_review(u, s, k, n, is_correct=True, attempt_kind="review")
    done = [r for r in _rows(u, s, k) if r["id"] == original and r["status"] == "DONE"]
    assert done, "到期的复测应在重做答对后关闭"
