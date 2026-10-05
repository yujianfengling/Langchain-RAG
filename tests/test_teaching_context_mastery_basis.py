"""教学上下文 / Agent 读取掌握度的口径（2026-10-05）

断点：学生面板展示"每题最新一次有效作答"，而 Agent 与教学上下文读的是累计
mastery_level，出现"刚答错但累计仍高 → Agent 仍按已掌握走"的分裂。
本测试锁定：两者统一按最新有效作答口径，且学生可感知的变化说明只在有真实
证据时出现。
"""
from unittest import mock

from services import teaching_context
from services import agent_runtime


def _point(**over):
    p = {
        "knowledge_point": "二叉树遍历",
        "knowledge_point_id": "kp1",
        "mastery_level": 0.72,                 # 历史累计（旧口径）
        "consolidated": False,
        "mastery_status": "estimated",
        "evidence_breakdown": {"independent_correct": 11, "independent_wrong": 2,
                               "review_wrong": 0},
        "latest_view": {"latest_mastery_level": 0.333, "latest_evidence_count": 4,
                        "latest_independent_correct": 0, "latest_independent_wrong": 1,
                        "history_attempt_count": 20, "history_wrong_count": 6,
                        "history_correct_count": 14},
    }
    p.update(over)
    return p


def test_teaching_context_uses_latest_evidence_mastery():
    with mock.patch("services.mastery_service.list_mastery", return_value=[_point()]):
        ctx = teaching_context.build_teaching_context("u1", "s1", question="二叉树遍历")
    sig = ctx["signals"]
    assert sig["mastery_basis"].startswith("每题最新")
    # 当前掌握用最新口径（0.333），不是累计（0.72）
    assert abs(sig["mastery_level"] - 0.333) < 1e-6
    assert abs(sig["history_mastery_level"] - 0.72) < 1e-6
    # 刚有独立答错 → 基础优先，并给出对应真实记录的变化说明
    assert ctx["teaching_mode"] == "foundation"
    assert "二叉树遍历" in (ctx.get("adaptation_note") or "")
    assert any("每题最新" in r for r in ctx["reasons"])


def test_adaptation_note_does_not_quote_mismatched_question():
    """题目的知识点标签可能与语义不符 → 说明只引用知识点与真实答错次数，不引用题干。"""
    with mock.patch("services.mastery_service.list_mastery", return_value=[_point()]), \
         mock.patch("services.teaching_context._recent_mistakes",
                    return_value=[{"stem": "完全无关的题干：另一个主题", "attempt_count": 1}]):
        ctx = teaching_context.build_teaching_context("u1", "s1", question="二叉树遍历")
    note = ctx.get("adaptation_note") or ""
    assert "完全无关的题干" not in note
    assert "二叉树遍历" in note
    assert "1 次独立答错" in note


def test_no_evidence_does_not_claim_adaptation():
    p = _point(mastery_level=None,
               evidence_breakdown={},
               latest_view={"latest_mastery_level": None, "latest_evidence_count": 0,
                            "latest_independent_correct": 0, "latest_independent_wrong": 0})
    with mock.patch("services.mastery_service.list_mastery", return_value=[p]):
        ctx = teaching_context.build_teaching_context("u1", "s1", question="二叉树遍历")
    assert ctx["teaching_mode"] == "standard"
    assert not ctx.get("adaptation_note")


def test_agent_surfaces_fresh_wrong_even_when_level_is_high():
    """刚答错、但掌握估计仍不低（0.75）的知识点也必须被 Agent 看到。"""
    p = _point(mastery_level=0.75, consolidated=True,
               latest_view={"latest_mastery_level": 0.75, "latest_evidence_count": 6,
                            "latest_independent_correct": 5, "latest_independent_wrong": 1,
                            "history_attempt_count": 20, "history_wrong_count": 6,
                            "history_correct_count": 14})
    with mock.patch("services.mastery_service.list_mastery", return_value=[p]),          mock.patch("services.mastery_service.summarize_mastery",
                    return_value={"distribution": {}, "accuracy": None,
                                  "accuracy_sample_size": 0}),          mock.patch("services.learning_service.get_task_buckets",
                    return_value={"today": [], "overdue": []}),          mock.patch("services.quiz_service.list_wrong_questions", return_value=[]),          mock.patch("services.review_service.list_review_tasks", return_value=[]),          mock.patch("services.plan_adjustment.analyze_plan_budget",
                    return_value=mock.MagicMock()),          mock.patch("services.learning_memory.get_effective_memory",
                    return_value={"items": []}):
        state = agent_runtime._tool_get_learning_state("u1", "s1")
    weak = state["weak_points"]
    assert len(weak) == 1
    assert weak[0]["knowledge_point"] == "二叉树遍历"
    assert "最近独立答错 1 次" in weak[0]["reason"]
    assert weak[0]["mastery_level"] == 0.75


def test_agent_weak_points_use_latest_evidence():
    p = _point()
    with mock.patch("services.mastery_service.list_mastery", return_value=[p]), \
         mock.patch("services.mastery_service.summarize_mastery",
                    return_value={"distribution": {}, "accuracy": None,
                                  "accuracy_sample_size": 0}), \
         mock.patch("services.learning_service.get_task_buckets",
                    return_value={"today": [], "overdue": []}), \
         mock.patch("services.quiz_service.list_wrong_questions", return_value=[]), \
         mock.patch("services.review_service.list_review_tasks", return_value=[]), \
         mock.patch("services.plan_adjustment.analyze_plan_budget",
                    return_value=mock.MagicMock()), \
         mock.patch("services.learning_memory.get_effective_memory",
                    return_value={"items": []}):
        state = agent_runtime._tool_get_learning_state("u1", "s1")
    weak = state["weak_points"]
    assert len(weak) == 1
    # 弱点是按最新口径判定（0.333 < 0.4），并同时给出历史累计
    assert abs(weak[0]["mastery_level"] - 0.333) < 1e-6
    assert abs(weak[0]["history_mastery_level"] - 0.72) < 1e-6
    assert state["mastery_basis"].startswith("当前掌握=每题最新")
