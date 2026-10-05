"""长期掌握与复习调度的一致性（2026-10-05）

存储层的 consolidation 有"同一段时间连续答对"的 fast path；若直接用它跳过
延后复测，就会让密集答对提前结束复习——与"密集答对不等于长期掌握"冲突。
这里锁定：只有**跨天间隔复现**才跳过延后复测，教学策略/Agent/复习调度同口径。
"""
from unittest import mock

from services import mastery_service, learning_handlers


def test_is_spaced_consolidation():
    assert mastery_service.is_spaced_consolidation("2026-10-01T10:00:00",
                                                   "2026-10-01T10:05:00") is False
    assert mastery_service.is_spaced_consolidation("2026-10-01T10:00:00",
                                                   "2026-10-03T10:00:00") is True
    assert mastery_service.is_spaced_consolidation(None, "2026-10-03T10:00:00") is False
    assert mastery_service.is_spaced_consolidation("2026-10-01T10:00:00", None) is False


def _run_handler(state, attempt_kind="review"):
    ctx = {"user_id": "u1", "subject_id": "s1",
           "payload": {"knowledge_point": "KP", "knowledge_point_id": "kp1",
                       "is_correct": True, "attempt_kind": attempt_kind,
                       "attempt_id": "att1"}}
    with mock.patch("services.review_service.get_pending_review_for_point",
                    return_value={"reason": "错题复习"}), \
         mock.patch("services.review_service.complete_review_tasks", return_value=1), \
         mock.patch("services.mastery_service.get_mastery", return_value=state), \
         mock.patch("services.review_service.schedule_recheck",
                    return_value={"scheduled": False}) as sched, \
         mock.patch("services.mastery_service.mark_recheck_scheduled"):
        learning_handlers._handle_quiz_attempt_review(ctx)
    return sched


def test_dense_consolidation_still_schedules_recheck():
    """同一段时间连续答对（consolidated=True 但未跨天）→ 仍安排复测"""
    state = {"consolidated": True, "recheck_count": 0,
             "first_correct_at": "2026-10-05T10:00:00",
             "last_correct_at": "2026-10-05T10:05:00"}
    sched = _run_handler(state)
    assert sched.call_args.kwargs["consolidated"] is False


def test_spaced_consolidation_skips_recheck():
    """跨天间隔复现（consolidated=True 且跨天）→ 不再自动复测"""
    state = {"consolidated": True, "recheck_count": 0,
             "first_correct_at": "2026-10-03T10:00:00",
             "last_correct_at": "2026-10-05T10:00:00"}
    sched = _run_handler(state)
    assert sched.call_args.kwargs["consolidated"] is True
