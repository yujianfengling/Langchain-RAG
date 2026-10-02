"""
主动学习推荐模块（V2.6 Agent核心）

根据用户画像和学习历史，主动推荐学习内容，识别知识盲点。

设计原则：
- 基于知识点掌握度识别薄弱环节
- 结合学习计划进度推荐下一步
- 从交互历史中挖掘学习模式
- 生成个性化学习路径
"""

from typing import List, Dict, Any, Optional
from utils.logger import logger

from services.user_profiler import (
    get_or_create_profile,
    get_weak_points,
    get_strong_points,
    get_user_interactions,
    _get_db,
    _row_to_dict,
)

try:
    from services.learning_service import list_plans, get_plan
    LEARNING_AVAILABLE = True
except ImportError:
    LEARNING_AVAILABLE = False


def generate_recommendations(
    user_id: str = "default_user",
    subject_id: Optional[str] = None,
    current_topic: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    生成个性化学习推荐

    Returns:
        {
            "recommendations": list,
            "weak_points": list,
            "strong_points": list,
            "suggested_actions": list,
            "learning_path": list,
            "user_level": float,
        }
    """
    try:
        # 1. 获取用户画像
        profile = get_or_create_profile(user_id)
        user_level = profile.get("knowledge_level", {}).get(subject_id, 0.5) if subject_id else 0.5

        # 2. 获取薄弱知识点
        weak = get_weak_points(user_id, subject_id) if subject_id else []
        weak_points = [w["knowledge_point"] for w in weak[:5]]

        # 3. 获取强项知识点
        strong = get_strong_points(user_id, subject_id) if subject_id else []
        strong_points = [s["knowledge_point"] for s in strong[:5]]

        # 4. 用户表现：当前推荐逻辑没有消费它（此前算完即丢，白搭一次查询）。
        #    若要用它参与推荐，在这里恢复 analyze_user_performance(user_id, subject_id)。

        # 5. 生成推荐
        recommendations = []
        suggested_actions = []

        # 基于薄弱知识点推荐
        if weak_points:
            recommendations.append({
                "type": "review_weak",
                "title": "复习薄弱知识点",
                "description": f"建议重点复习：{', '.join(weak_points[:3])}",
                "priority": "high",
                "knowledge_points": weak_points[:3],
                "icon": "warning",
            })
            suggested_actions.append("生成薄弱知识点的智能笔记")
            suggested_actions.append("进行薄弱知识点的自测练习")

        # 基于当前主题推荐相关知识
        if current_topic:
            recommendations.append({
                "type": "explore_related",
                "title": f"深入学习：{current_topic}",
                "description": f"可以进一步探索与'{current_topic}'相关的知识点",
                "priority": "medium",
                "icon": "search",
            })

        # 基于学习风格推荐
        style = profile.get("learning_style", "balanced")
        if style == "visual":
            recommendations.append({
                "type": "learning_method",
                "title": "使用思维导图",
                "description": "根据您的视觉学习偏好，建议使用思维导图整理知识结构",
                "priority": "low",
                "icon": "bulb",
            })
        elif style == "kinesthetic":
            recommendations.append({
                "type": "learning_method",
                "title": "多做练习题",
                "description": "根据您的动手学习偏好，建议多做实践练习",
                "priority": "low",
                "icon": "tool",
            })

        # 基于学习计划推荐
        if LEARNING_AVAILABLE and subject_id:
            try:
                plans = list_plans(subject_id=subject_id)
                if plans:
                    active_plans = [p for p in plans if p.get("status") == "ACTIVE"]
                    if active_plans:
                        recommendations.append({
                            "type": "continue_plan",
                            "title": "继续学习计划",
                            "description": f"您有{len(active_plans)}个进行中的学习计划，建议继续完成",
                            "priority": "medium",
                            "plan_ids": [p["id"] for p in active_plans[:2]],
                            "icon": "calendar",
                        })
            except Exception as e:
                logger.warning(f"获取学习计划失败: {e}")

        # 6. 生成学习路径
        learning_path = generate_learning_path(user_id, subject_id)

        return {
            "recommendations": recommendations,
            "weak_points": weak_points,
            "strong_points": strong_points,
            "suggested_actions": suggested_actions,
            "learning_path": learning_path,
            "user_level": user_level,
        }
    except Exception as e:
        logger.error(f"生成推荐失败: {e}", exc_info=True)
        return {
            "recommendations": [],
            "weak_points": [],
            "strong_points": [],
            "suggested_actions": [],
            "learning_path": [],
            "user_level": 0.5,
        }


def generate_learning_path(
    user_id: str = "default_user",
    subject_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    生成个性化学习路径

    基于知识点掌握度和依赖关系，生成有序的学习序列。

    Returns:
        学习路径列表，每项包含：
        {
            "step": int,
            "knowledge_point": str,
            "current_mastery": float,
            "target_mastery": float,
            "suggested_activity": str,
            "estimated_minutes": int,
            "status": "pending" | "in_progress" | "completed",
        }
    """
    if not subject_id:
        return []

    try:
        # 获取所有知识点及其掌握度
        conn = _get_db()
        try:
            cursor = conn.execute(
                """SELECT knowledge_point, mastery_level, test_count, correct_count
                   FROM knowledge_mastery 
                   WHERE user_id = ? AND subject_id = ?
                   ORDER BY mastery_level ASC""",
                (user_id, subject_id)
            )
            all_points = [_row_to_dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

        if not all_points:
            return []

        # 生成学习路径
        path = []
        step = 1

        # 优先处理薄弱知识点（掌握度 < 0.4）
        weak_points = [p for p in all_points if p["mastery_level"] < 0.4]
        for point in weak_points[:3]:  # 最多3个薄弱知识点
            path.append({
                "step": step,
                "knowledge_point": point["knowledge_point"],
                "current_mastery": round(point["mastery_level"], 2),
                "target_mastery": 0.6,
                "suggested_activity": _suggest_activity(point),
                "estimated_minutes": _estimate_time(point),
                "status": "pending",
                "priority": "high",
            })
            step += 1

        # 处理中等掌握度的知识点（0.4 <= 掌握度 < 0.7）
        medium_points = [p for p in all_points if 0.4 <= p["mastery_level"] < 0.7]
        for point in medium_points[:2]:  # 最多2个中等知识点
            path.append({
                "step": step,
                "knowledge_point": point["knowledge_point"],
                "current_mastery": round(point["mastery_level"], 2),
                "target_mastery": 0.8,
                "suggested_activity": _suggest_activity(point),
                "estimated_minutes": _estimate_time(point),
                "status": "pending",
                "priority": "medium",
            })
            step += 1

        # 已掌握的知识点可以跳过或进行巩固
        strong_points = [p for p in all_points if p["mastery_level"] >= 0.7]
        if strong_points:
            # 只推荐1个强项知识点进行巩固
            point = strong_points[0]
            path.append({
                "step": step,
                "knowledge_point": point["knowledge_point"],
                "current_mastery": round(point["mastery_level"], 2),
                "target_mastery": 0.9,
                "suggested_activity": "进行高级练习或教授他人",
                "estimated_minutes": 15,
                "status": "pending",
                "priority": "low",
            })

        return path
    except Exception as e:
        logger.error(f"生成学习路径失败: {e}", exc_info=True)
        return []


def _suggest_activity(point: Dict[str, Any]) -> str:
    """根据知识点状态建议学习活动"""
    mastery = point.get("mastery_level", 0)
    test_count = point.get("test_count", 0)

    if mastery < 0.3:
        return "阅读基础概念并生成智能笔记"
    elif mastery < 0.5:
        if test_count < 3:
            return "做基础练习题巩固理解"
        else:
            return "复习笔记并尝试解释给他人"
    elif mastery < 0.7:
        return "做进阶练习题提升掌握度"
    else:
        return "进行综合应用或挑战难题"


def _estimate_time(point: Dict[str, Any]) -> int:
    """估计学习时间（分钟）"""
    mastery = point.get("mastery_level", 0)

    if mastery < 0.3:
        return 30  # 基础学习需要更多时间
    elif mastery < 0.5:
        return 20
    elif mastery < 0.7:
        return 15
    else:
        return 10  # 已掌握的知识点只需要巩固


def get_knowledge_gaps(user_id: str = "default_user", subject_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    识别知识盲点

    Returns:
        知识盲点列表
    """
    try:
        weak = get_weak_points(user_id, subject_id) if subject_id else []
        return [
            {
                "knowledge_point": w["knowledge_point"],
                "mastery_level": w["mastery_level"],
                "test_count": w["test_count"],
                "correct_rate": w["correct_count"] / w["test_count"] if w["test_count"] > 0 else 0,
                "suggestion": _suggest_activity(w),
                "estimated_minutes": _estimate_time(w),
            }
            for w in weak
        ]
    except Exception as e:
        logger.error(f"识别知识盲点失败: {e}", exc_info=True)
        return []


def get_learning_stats(user_id: str = "default_user", subject_id: Optional[str] = None) -> Dict[str, Any]:
    """
    获取学习统计信息

    Returns:
        {
            "total_knowledge_points": int,
            "mastery_distribution": {"strong": int, "medium": int, "weak": int},
            "average_mastery": float,
            "total_study_time": int,
            "learning_efficiency": float,
        }
    """
    try:
        if not subject_id:
            return {
                "total_knowledge_points": 0,
                "mastery_distribution": {"strong": 0, "medium": 0, "weak": 0},
                "average_mastery": 0,
                "total_study_time": 0,
                "learning_efficiency": 0,
            }

        conn = _get_db()
        try:
            # 获取知识点统计
            cursor = conn.execute(
                """SELECT 
                    COUNT(*) as total,
                    AVG(mastery_level) as avg_mastery,
                    SUM(CASE WHEN mastery_level >= 0.7 THEN 1 ELSE 0 END) as strong,
                    SUM(CASE WHEN mastery_level >= 0.4 AND mastery_level < 0.7 THEN 1 ELSE 0 END) as medium,
                    SUM(CASE WHEN mastery_level < 0.4 THEN 1 ELSE 0 END) as weak
                   FROM knowledge_mastery 
                   WHERE user_id = ? AND subject_id = ?""",
                (user_id, subject_id)
            )
            stats = _row_to_dict(cursor.fetchone())

            # 获取交互统计
            cursor = conn.execute(
                """SELECT COUNT(*) as total, SUM(latency) as total_latency
                   FROM user_interactions 
                   WHERE user_id = ? AND subject_id = ?""",
                (user_id, subject_id)
            )
            interaction_stats = _row_to_dict(cursor.fetchone())

            total_points = stats.get("total", 0)
            avg_mastery = stats.get("avg_mastery", 0) or 0
            total_latency = interaction_stats.get("total_latency", 0) or 0

            return {
                "total_knowledge_points": total_points,
                "mastery_distribution": {
                    "strong": stats.get("strong", 0),
                    "medium": stats.get("medium", 0),
                    "weak": stats.get("weak", 0),
                },
                "average_mastery": round(avg_mastery, 2),
                "total_study_time": round(total_latency / 60, 1),  # 转换为分钟
                "learning_efficiency": round(avg_mastery * 100 / max(1, total_points), 1),
            }
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"获取学习统计失败: {e}", exc_info=True)
        return {
            "total_knowledge_points": 0,
            "mastery_distribution": {"strong": 0, "medium": 0, "weak": 0},
            "average_mastery": 0,
            "total_study_time": 0,
            "learning_efficiency": 0,
        }


__all__ = [
    "generate_recommendations",
    "generate_learning_path",
    "get_knowledge_gaps",
    "get_learning_stats",
]
