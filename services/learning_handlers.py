"""
学习闭环事件处理器注册（阶段一）

把分散的业务动作统一挂到事件层，形成：
    提交作答 → 更新画像（证据）→ 记录错题 → 安排复习 → 计划可见

约束：
- 处理器抛错只标记事件 FAILED，不影响调用方已保存的核心数据
- 依赖幂等键，重复事件（刷新/断网重试/并发）不会重复产生业务结果
"""

from utils.logger import logger
from services.learning_events import register_handler, EVENT_DONE
from services import mastery_service, review_service, quiz_service

REVIEW_REASON_WRONG = "最近一次作答错误，需要复习"


def _handle_quiz_attempt(ctx: dict) -> None:
    """
    作答事件第 1 步：记录作答证据（掌握度画像，user_agent.db）。

    幂等：按 attempt_id 记账，重复投递不再累加。
    本步与第 2 步（复习联动）注册为**独立处理器**，因此任一步失败都不会
    阻断另一步；事件重试时各自跳过已完成的部分（跨库步骤级幂等）。
    """
    p = ctx.get("payload") or {}
    user_id = ctx.get("user_id")
    subject_id = ctx.get("subject_id") or p.get("subject_id")
    kp_name = p.get("knowledge_point")
    if not (user_id and subject_id and kp_name):
        logger.warning(f"作答事件缺少必要字段，跳过画像记录: {p}")
        return
    mastery_service.record_evidence(
        user_id=user_id,
        subject_id=subject_id,
        knowledge_point_name=kp_name,
        is_correct=bool(p.get("is_correct")),
        attempt_kind=p.get("attempt_kind") or "independent",
        source_attempt_id=p.get("attempt_id"),
    )


def _handle_quiz_attempt_review(ctx: dict) -> None:
    """
    作答事件第 2 步：错题/复习联动（learning_loop.db + learning.db）。

    - 答错 → 生成/复用复习任务（同知识点合并）
    - 真实答对（independent/review）→ 关闭待办复习
      未通过间隔复现的知识点再安排延后复测（一次答对 ≠ 长期掌握）
    - after_reveal（看过答案后作答）→ 不关闭待办
    """
    p = ctx.get("payload") or {}
    user_id = ctx.get("user_id")
    subject_id = ctx.get("subject_id") or p.get("subject_id")
    kp_name = p.get("knowledge_point")
    kp_id = p.get("knowledge_point_id")
    is_correct = bool(p.get("is_correct"))
    attempt_kind = p.get("attempt_kind") or "independent"
    attempt_id = p.get("attempt_id")
    if not (user_id and subject_id and kp_name and kp_id):
        logger.warning(f"作答事件缺少必要字段，跳过复习联动: {p}")
        return

    if not is_correct:
        review_service.ensure_review_task(
            user_id=user_id,
            subject_id=subject_id,
            knowledge_point_id=kp_id,
            knowledge_point_name=kp_name,
            source_attempt_id=attempt_id,
            source_task_id=p.get("source_task_id"),
            reason=REVIEW_REASON_WRONG,
        )
        return

    if attempt_kind not in review_service.REVIEW_RULES["closing_kinds"]:
        logger.info(f"作答性质 {attempt_kind} 不关闭复习待办: user={user_id}, kp={kp_name}")
        return

    closed = review_service.complete_review_tasks(
        user_id=user_id,
        subject_id=subject_id,
        knowledge_point_id=kp_id,
        reason="复习已通过（重做答对）",
        attempt_kind=attempt_kind,
        source_attempt_id=attempt_id,
    )
    if not closed:
        return
    logger.info(f"复习通过并关闭待办: user={user_id}, kp={kp_name}, n={closed}")

    # 关闭待办 ≠ 长期掌握：读取当前画像判断是否已通过间隔复现
    state = {}
    try:
        state = mastery_service.get_mastery(user_id, subject_id, kp_name) or {}
    except Exception as e:
        logger.warning(f"读取掌握状态失败（按未巩固处理）: {e}")

    recheck = review_service.schedule_recheck(
        user_id=user_id,
        subject_id=subject_id,
        knowledge_point_id=kp_id,
        knowledge_point_name=kp_name,
        source_attempt_id=attempt_id,
        consolidated=bool(state.get("consolidated")),
        recheck_times=int(state.get("recheck_count") or 0),
    )
    if recheck.get("scheduled"):
        mastery_service.mark_recheck_scheduled(user_id, subject_id, kp_name)
        logger.info(f"已安排延后复测: user={user_id}, kp={kp_name}, "
                    f"due={recheck.get('due_date')}")


def _handle_quiz_attempt_memory(ctx: dict) -> None:
    """
    作答事件第 3 步：维护可管理的学习记忆（阶段二）。

    - 独立答对 → 撤销该知识点的"需要加强"推断（不让一次错误变成永久标签）
    - 独立答错累计到达阈值 → 生成带依据与有效期的"需要加强"推断

    幂等：按 attempt_id 记账；记忆写入本身也按 (user, scope, type, key) 唯一。
    """
    p = ctx.get("payload") or {}
    user_id = ctx.get("user_id")
    subject_id = ctx.get("subject_id") or p.get("subject_id")
    kp_name = p.get("knowledge_point")
    kp_id = p.get("knowledge_point_id")
    is_correct = bool(p.get("is_correct"))
    attempt_kind = p.get("attempt_kind") or "independent"
    if not (user_id and subject_id and kp_name):
        return

    from services import learning_memory, mastery_service

    try:
        if is_correct and attempt_kind in ("independent", "review"):
            # 撤销"需要加强"推断（记忆里不保留已被证据推翻的判断）
            learning_memory.revise_inference_after_evidence(user_id, subject_id, kp_id)
            return

        if not is_correct:
            state = mastery_service.get_mastery(user_id, subject_id, kp_name) or {}
            bd = state.get("evidence_breakdown") or {}
            wrong = bd.get("independent_wrong", 0)
            correct = bd.get("independent_correct", 0)
            rules = learning_memory.INFERENCE_RULES
            if wrong >= rules["weak_min_wrong"] and correct <= rules["weak_max_correct"]:
                learning_memory.set_memory(
                    user_id=user_id,
                    key=f"weak_point:{kp_id or kp_name}",
                    value="需要加强",
                    memory_type=learning_memory.MEMORY_INFERENCE,
                    scope=subject_id,
                    evidence={"knowledge_point": kp_name, "independent_wrong": wrong,
                              "independent_correct": correct,
                              "sample_size": wrong + correct},
                    confidence=min(0.9, 0.5 + 0.1 * wrong),
                    ttl_days=rules["weak_ttl_days"],
                    note=f"最近 {wrong + correct} 次独立作答中有 {wrong} 次答错"
                         f"（会自动过期，答对后即消除）")
    except Exception as e:
        # 记忆维护失败不影响作答与复习联动；事件标记 FAILED 可重试
        logger.warning(f"学习记忆维护失败: {e}")
        raise


def _handle_plan_task_completed(ctx: dict) -> None:
    """
    任务完成事件：只记录学习行为，**不改变掌握度**。

    掌握度仅由独立作答证据决定，避免"读完即掌握"的误判。
    """
    p = ctx.get("payload") or {}
    user_id = ctx.get("user_id")
    kp_name = p.get("knowledge_point")
    subject_id = ctx.get("subject_id")
    if not (user_id and subject_id and kp_name):
        logger.info(f"任务完成事件无可记录的知识点，跳过行为记录: {p}")
        return
    mastery_service.record_behavior(
        user_id=user_id,
        subject_id=subject_id,
        knowledge_point_name=kp_name,
        behavior_type=p.get("task_type", "task_completed"),
        source_id=p.get("task_id"),
    )


def _track_point(user_id, subject_id, topic) -> None:
    """
    把知识点纳入学习范围（生成自测题/笔记即视为在学）。

    只创建"待评估"记录：不写默认掌握度，避免伪造学情。
    """
    if not (user_id and subject_id and topic):
        return
    try:
        mastery_service.ensure_point_tracked(user_id, subject_id, topic)
    except Exception as e:
        logger.warning(f"纳入学习范围失败（不影响主流程）: {e}")


def _handle_note_generated(ctx: dict) -> None:
    """笔记生成事件：记录交互 + 纳入学习范围（不改变掌握度）"""
    p = ctx.get("payload") or {}
    user_id = ctx.get("user_id")
    subject_id = ctx.get("subject_id")
    topic = p.get("topic")
    if not (user_id and subject_id and topic):
        return
    _track_point(user_id, subject_id, topic)
    try:
        from services.user_profiler import record_interaction
        record_interaction(
            user_id=user_id,
            interaction_type="note",
            subject_id=subject_id,
            question=f"生成笔记：{topic}",
            answer=p.get("title", ""),
            model_used=p.get("model"),
        )
    except Exception as e:
        logger.warning(f"笔记交互记录失败: {e}")


def _handle_quiz_generated(ctx: dict) -> None:
    """
    自测题生成事件：记录"生成过题目"这一行为 + 纳入学习范围（待评估）。

    掌握度必须来自真实作答证据（见 services/mastery_service.py）。
    """
    p = ctx.get("payload") or {}
    user_id = ctx.get("user_id")
    topic = p.get("topic")
    if not (user_id and topic):
        return
    _track_point(user_id, ctx.get("subject_id"), topic)
    try:
        from services.user_profiler import record_interaction
        record_interaction(
            user_id=user_id,
            interaction_type="quiz",
            subject_id=ctx.get("subject_id"),
            question=f"生成自测题：{topic}",
            answer=f"共 {p.get('quiz_count', 0)} 道题",
            model_used=p.get("model"),
        )
    except Exception as e:
        logger.warning(f"自测题生成交互记录失败: {e}")


def _handle_chat_interaction(ctx: dict) -> None:
    """问答事件：记录交互（流式与非流式统一走这里）"""
    p = ctx.get("payload") or {}
    user_id = ctx.get("user_id")
    if not user_id:
        return
    try:
        from services.user_profiler import record_interaction
        iid = record_interaction(
            user_id=user_id,
            interaction_type="chat",
            subject_id=ctx.get("subject_id"),
            question=p.get("question"),
            answer=p.get("answer"),
            model_used=p.get("model"),
            latency=p.get("latency"),
        )
        # 回写 interaction_id，供反馈按钮做正确关联
        try:
            from services.conversation_service import attach_interaction_id
            if p.get("conversation_id") and p.get("message_id") and iid:
                attach_interaction_id(p["message_id"], iid)
        except Exception as e:
            logger.debug(f"回写 interaction_id 失败（不阻断）: {e}")
    except Exception as e:
        logger.warning(f"问答交互记录失败: {e}")


_registered = False


def register_all() -> None:
    """注册全部处理器（幂等，可重复调用）"""
    global _registered
    if _registered:
        return
    # 作答事件拆成独立步骤：任一步失败不阻断其他步，重试时各自幂等
    register_handler("quiz_attempt_recorded", _handle_quiz_attempt)
    register_handler("quiz_attempt_recorded", _handle_quiz_attempt_review)
    register_handler("quiz_attempt_recorded", _handle_quiz_attempt_memory)
    register_handler("plan_task_completed", _handle_plan_task_completed)
    register_handler("note_generated", _handle_note_generated)
    register_handler("quiz_question_generated", _handle_quiz_generated)
    register_handler("chat_interaction", _handle_chat_interaction)
    _registered = True
    logger.info("学习闭环事件处理器已注册")


register_all()


__all__ = ["register_all"]
