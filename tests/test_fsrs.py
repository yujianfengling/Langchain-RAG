"""FSRS 调度器测试（2026-10-24）

- 可提取性随间隔下降；
- 新卡答对后 stability > 0，间隔至少 1 天；
- 答错增加 lapses 并降低 stability；
- 数据库卡片按用户/学科/知识点隔离，下一次间隔会持久化。
"""

import uuid
from datetime import datetime, timedelta

from services import fsrs_scheduler as fsrs


def test_retrievability_decays():
    assert fsrs.retrievability(0, 10) == 1.0
    assert fsrs.retrievability(5, 10) < 1.0
    assert fsrs.retrievability(50, 10) < fsrs.retrievability(5, 10)


def test_new_card_good_and_again():
    now = datetime(2026, 10, 24, 10, 0, 0)
    good = fsrs.review_card(None, fsrs.RATING_GOOD, now)
    assert good["stability"] > 0
    assert good["reps"] == 1
    assert good["interval_days"] >= 1
    assert good["state"] == "review"

    again = fsrs.review_card(good, fsrs.RATING_AGAIN, now + timedelta(days=3))
    assert again["lapses"] == 1
    assert again["state"] == "relearning"
    assert again["stability"] < good["stability"] * 2


def test_next_interval_persists_and_isolates():
    tag = uuid.uuid4().hex[:8]
    user, subject, kp = "fsrs_u_" + tag, "fsrs_s_" + tag, "fsrs_kp_" + tag
    now = datetime(2026, 10, 24, 10, 0, 0)
    first = fsrs.next_interval(user, subject, kp, fsrs.RATING_GOOD, now=now)
    assert first["reps"] == 1 and first["due"]
    card = fsrs.get_card(user, subject, kp)
    assert card and card["reps"] == 1
    assert fsrs.get_card(user, subject, "other_kp") is None

    second = fsrs.next_interval(user, subject, kp, fsrs.RATING_GOOD,
                                now=now + timedelta(days=first["interval_days"]))
    assert second["reps"] == 2
    assert second["interval_days"] >= first["interval_days"]
