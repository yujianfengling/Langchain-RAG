"""
学习闭环模块（V2.6 Agent核心）

将笔记、计划、问答、测验等学习活动联动，实现完整的学习闭环。

设计原则：
- 笔记生成 → 自动记录知识点到用户画像
- 测验完成 → 更新知识点掌握度
- 学习计划 → 跟踪任务完成进度
- 问答历史 → 分析学习模式
"""

from typing import Dict, Any, Optional, List
from utils.logger import logger

from services.user_profiler import (
    record_interaction,
    update_knowledge_mastery,
    get_or_create_profile,
    UserProfileError,
)


def on_note_generated(
    user_id: str,
    subject_id: str,
    topic: str,
    note_data: Dict[str, Any],
) -> None:
    """
    笔记生成后的回调
    
    自动记录：
    1. 交互记录
    2. 知识点（从笔记中提取）
    """
    try:
        # 记录交互
        record_interaction(
            user_id=user_id,
            interaction_type="note",
            subject_id=subject_id,
            question=f"生成笔记：{topic}",
            answer=note_data.get("title", ""),
            model_used=note_data.get("model"),
        )
        
        # 从笔记中提取知识点并记录
        # 笔记的quiz部分包含知识点
        quiz_items = note_data.get("quiz", [])
        if quiz_items:
            for item in quiz_items:
                kp = item.get("knowledge_point", topic)
                # 笔记生成表示用户在学习该知识点，给予初始掌握度
                _ensure_knowledge_point(user_id, subject_id, kp)
        
        logger.info(f"笔记生成回调完成: user={user_id}, topic={topic}")
    except Exception as e:
        logger.warning(f"笔记生成回调失败（不影响主流程）: {e}")


def on_quiz_answered(
    user_id: str,
    subject_id: str,
    knowledge_point: str,
    is_correct: bool,
    quiz_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    测验答案提交后的回调
    
    更新知识点掌握度并返回学习建议
    """
    try:
        # 更新知识点掌握度
        update_knowledge_mastery(
            user_id=user_id,
            subject_id=subject_id,
            knowledge_point=knowledge_point,
            is_correct=is_correct,
            mastery_delta=0.1,
        )
        
        # 记录交互
        record_interaction(
            user_id=user_id,
            interaction_type="quiz",
            subject_id=subject_id,
            question=f"测验：{knowledge_point}",
            answer="正确" if is_correct else "错误",
        )
        
        # 获取当前掌握度
        profile = get_or_create_profile(user_id)
        
        return {
            "success": True,
            "knowledge_point": knowledge_point,
            "is_correct": is_correct,
            "message": "回答正确！继续加油！" if is_correct else "回答错误，建议复习该知识点。",
        }
    except Exception as e:
        logger.error(f"测验答案回调失败: {e}", exc_info=True)
        return {"success": False, "message": f"处理失败: {str(e)}"}


def on_plan_task_completed(
    user_id: str,
    subject_id: str,
    task_id: str,
    knowledge_point: str,
    task_type: str,
    actual_minutes: Optional[int] = None,
) -> Dict[str, Any]:
    """
    学习计划任务完成后的回调
    
    更新知识点掌握度
    """
    try:
        # 根据任务类型给予不同的掌握度增量
        mastery_delta = {
            "READ": 0.05,      # 阅读：小幅提升
            "REVIEW": 0.08,    # 复习：中等提升
            "QUIZ": 0.1,       # 测验：标准提升
            "PRACTICE": 0.12,  # 练习：较大提升
        }.get(task_type, 0.05)
        
        # 更新知识点掌握度（任务完成视为掌握了部分内容）
        update_knowledge_mastery(
            user_id=user_id,
            subject_id=subject_id,
            knowledge_point=knowledge_point,
            is_correct=True,  # 任务完成视为正向
            mastery_delta=mastery_delta,
        )
        
        # 记录交互
        record_interaction(
            user_id=user_id,
            interaction_type="plan",
            subject_id=subject_id,
            question=f"完成学习任务：{knowledge_point} ({task_type})",
            answer=f"耗时 {actual_minutes} 分钟" if actual_minutes else "已完成",
        )
        
        logger.info(f"学习任务完成回调: user={user_id}, task={task_id}, kp={knowledge_point}")
        
        return {
            "success": True,
            "knowledge_point": knowledge_point,
            "mastery_delta": mastery_delta,
            "message": f"任务完成！知识点掌握度 +{int(mastery_delta * 100)}%",
        }
    except Exception as e:
        logger.error(f"学习任务完成回调失败: {e}", exc_info=True)
        return {"success": False, "message": f"处理失败: {str(e)}"}


def on_chat_completed(
    user_id: str,
    subject_id: str,
    question: str,
    answer: str,
    model: str,
    latency: float,
    feedback_score: Optional[int] = None,
) -> None:
    """
    问答完成后的回调
    
    记录交互和反馈
    """
    try:
        record_interaction(
            user_id=user_id,
            interaction_type="chat",
            subject_id=subject_id,
            question=question,
            answer=answer,
            model_used=model,
            latency=latency,
        )
        
        logger.debug(f"问答交互记录: user={user_id}, subject={subject_id}")
    except Exception as e:
        logger.warning(f"问答交互记录失败（不影响主流程）: {e}")


def get_learning_progress_summary(
    user_id: str,
    subject_id: str,
) -> Dict[str, Any]:
    """
    获取学习进度摘要
    
    Returns:
        {
            "total_interactions": int,
            "knowledge_points_count": int,
            "mastery_distribution": {"strong": int, "medium": int, "weak": int},
            "recent_activity": list,
            "suggestions": list,
        }
    """
    try:
        from services.user_profiler import (
            get_user_interactions,
            get_weak_points,
            get_strong_points,
            analyze_user_performance,
        )
        
        # 获取交互统计
        performance = analyze_user_performance(user_id, subject_id)
        
        # 获取知识点分布
        weak = get_weak_points(user_id, subject_id, threshold=0.4)
        strong = get_strong_points(user_id, subject_id, threshold=0.7)
        
        # 计算中等掌握度的知识点数量
        from services.user_profiler import _get_db, _row_to_dict
        conn = _get_db()
        try:
            cursor = conn.execute(
                """SELECT COUNT(*) as count FROM knowledge_mastery 
                   WHERE user_id = ? AND subject_id = ? 
                   AND mastery_level >= 0.4 AND mastery_level <= 0.7""",
                (user_id, subject_id)
            )
            medium_count = cursor.fetchone()["count"]
        finally:
            conn.close()
        
        # 获取最近活动
        recent = get_user_interactions(user_id, subject_id, limit=5)
        
        # 生成建议
        suggestions = []
        if len(weak) > 3:
            suggestions.append("您有多个薄弱知识点，建议集中复习")
        if len(strong) > 5:
            suggestions.append("您已掌握多个知识点，可以尝试更高难度的内容")
        if not recent:
            suggestions.append("开始学习之旅吧！尝试提问或生成笔记")
        
        return {
            "total_interactions": performance.get("interaction_stats", {}).get("total_interactions", 0),
            "knowledge_points_count": len(weak) + medium_count + len(strong),
            "mastery_distribution": {
                "strong": len(strong),
                "medium": medium_count,
                "weak": len(weak),
            },
            "recent_activity": recent,
            "suggestions": suggestions,
            "positive_rate": performance.get("positive_rate", 0),
        }
    except Exception as e:
        logger.error(f"获取学习进度摘要失败: {e}", exc_info=True)
        return {
            "total_interactions": 0,
            "knowledge_points_count": 0,
            "mastery_distribution": {"strong": 0, "medium": 0, "weak": 0},
            "recent_activity": [],
            "suggestions": [],
            "positive_rate": 0,
        }


def _ensure_knowledge_point(user_id: str, subject_id: str, knowledge_point: str) -> None:
    """确保知识点存在于用户画像中（如果不存在则创建初始记录）"""
    try:
        from services.user_profiler import _get_db, _now
        import uuid
        
        conn = _get_db()
        try:
            # 检查是否已存在
            cursor = conn.execute(
                """SELECT id FROM knowledge_mastery 
                   WHERE user_id = ? AND subject_id = ? AND knowledge_point = ?""",
                (user_id, subject_id, knowledge_point)
            )
            if cursor.fetchone():
                return  # 已存在
            
            # 创建初始记录
            now = _now()
            conn.execute(
                """INSERT INTO knowledge_mastery 
                   (id, user_id, subject_id, knowledge_point, mastery_level, 
                    test_count, correct_count, last_tested_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 0.5, 0, 0, ?, ?, ?)""",
                (str(uuid.uuid4()), user_id, subject_id, knowledge_point, now, now, now)
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"确保知识点存在失败: {e}")


__all__ = [
    "on_note_generated",
    "on_quiz_answered",
    "on_plan_task_completed",
    "on_chat_completed",
    "get_learning_progress_summary",
]
