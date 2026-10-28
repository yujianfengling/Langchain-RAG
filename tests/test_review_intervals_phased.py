"""分阶段复习间隔（2026-10-24）

把延后复测从固定 3 天改为 3 → 7 → 16 天，仍遵守：
- 到期前不关闭；
- 最多 3 次自动复测；
- 旧配置键 recheck_interval_days 仍作为回退。
"""

from unittest import mock

from services import review_service


def test_next_recheck_interval_progression():
    assert review_service.next_recheck_interval(0) == 3
    assert review_service.next_recheck_interval(1) == 7
    assert review_service.next_recheck_interval(2) == 16
    assert review_service.next_recheck_interval(99) == 16


def test_schedule_recheck_uses_fsrs_interval():
    import uuid

    tag = uuid.uuid4().hex[:8]
    with mock.patch.object(
        review_service, "ensure_review_task",
        return_value={"task_id": "rvw_1"},
    ) as ensure:
        out = review_service.schedule_recheck(
            "fsrs_u_" + tag, "fsrs_s_" + tag, "fsrs_kp_" + tag, "测试知识点",
            source_attempt_id="att1", recheck_times=1)
    assert out["scheduled"] is True
    assert out["interval_days"] == ensure.call_args.kwargs["interval_days"]
    assert out["interval_days"] >= 1
    assert out["fsrs"]["stability"] > 0


def test_schedule_recheck_stops_at_limit():
    out = review_service.schedule_recheck(
        "u1", "s1", "kp1", "测试知识点",
        source_attempt_id="att2", recheck_times=review_service.REVIEW_RULES["recheck_max_times"])
    assert out["scheduled"] is False
    assert "上限" in out["reason"]
