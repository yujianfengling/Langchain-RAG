"""
建库后的题库预热（"预生成适当就行"）

背景：各校教材版本不同，本地知识库只覆盖学生自己上传的资料。
建库完成后，后台**适度**为该学科的前若干个知识点各生成几道题，
让学生第一次进测验就有题可做，而不是每次都等到点开才现生成。

"适当"的边界（有意控制成本与耗时，避免把建库变成烧钱任务）：
- 只预热前 N 个知识点（默认 8，WEB_SEARCH_PREGEN_POINTS；设 0 关闭）
- 每个知识点默认 3 题（WEB_SEARCH_PREGEN_COUNT）
- 同一学科在同一进程内只预热一次（重复建库不会反复烧模型额度）
- 在线程里跑，**不阻塞建库响应**；单点失败只记日志，不影响其它知识点

题目来源遵循 note_service 的分层：知识库证据 → 联网 → AI 通用知识。
"""

import os
import threading
from typing import Any, Dict, Set

from utils.logger import logger

# 已预热过的学科（进程级去重；重启后允许再预热一次）
_warmed: Set[str] = set()
_lock = threading.Lock()


def _pregen_points() -> int:
    try:
        return max(0, int(os.getenv("WEB_SEARCH_PREGEN_POINTS", "8")))
    except (TypeError, ValueError):
        return 8


def _pregen_count() -> int:
    try:
        return max(1, min(10, int(os.getenv("WEB_SEARCH_PREGEN_COUNT", "3"))))
    except (TypeError, ValueError):
        return 3


def is_enabled() -> bool:
    return _pregen_points() > 0


def prewarm_subject_quiz(subject_id: str) -> Dict[str, Any]:
    """
    同步预热某学科的题库（供后台线程调用，测试也可直接调用）。

    Returns:
        {"skipped": bool, "reason"?: str, "points"?: int, "ok"?: int, "failed"?: int}
    """
    limit = _pregen_points()
    if not subject_id or limit <= 0:
        return {"skipped": True, "reason": "预生成已关闭或缺少学科"}

    try:
        from services.learning_data import list_knowledge_points
        points = list_knowledge_points(subject_id) or []
    except Exception as e:
        logger.warning(f"题库预热：读取知识点失败（跳过）: {e}")
        return {"skipped": True, "reason": f"读取知识点失败: {e}"}

    names = [str(p.get("name")).strip() for p in points if isinstance(p, dict) and p.get("name")]
    names = names[:limit]
    if not names:
        return {"skipped": True, "reason": "该学科还没有知识点"}

    from services.note_service import generate_quiz
    count = _pregen_count()
    ok, failed = 0, 0
    for name in names:
        try:
            res = generate_quiz(subject_id, name, count=count)
            if res.get("quiz"):
                ok += 1
        except Exception as e:
            failed += 1
            logger.info(f"题库预热：知识点「{name}」出题失败（继续下一个）: {e}")
    logger.info(f"题库预热完成: subject={subject_id}, 成功 {ok}/{len(names)}，失败 {failed}")
    return {"skipped": False, "points": len(names), "ok": ok, "failed": failed}


def _run_safe(subject_id: str) -> None:
    try:
        prewarm_subject_quiz(subject_id)
    except Exception as e:  # 预热永远不该影响主流程
        logger.warning(f"题库预热异常（不影响建库结果）: {e}")


def schedule_prewarm(subject_id: str) -> bool:
    """
    建库成功后触发后台预热，立即返回。

    Returns:
        是否真的启动了预热（关闭 / 已预热过 / 缺学科 → False）
    """
    if not subject_id or not is_enabled():
        return False
    with _lock:
        if subject_id in _warmed:
            logger.info(f"题库预热：{subject_id} 本次进程内已预热过，跳过")
            return False
        _warmed.add(subject_id)
    threading.Thread(target=_run_safe, args=(subject_id,), daemon=True,
                     name=f"quiz-prewarm-{subject_id}").start()
    logger.info(f"题库预热已排入后台: subject={subject_id}, 知识点上限={_pregen_points()}, "
                f"每点题目数={_pregen_count()}")
    return True


def reset_prewarm_state() -> None:
    """清空"已预热"记录（测试用）"""
    with _lock:
        _warmed.clear()


__all__ = ["is_enabled", "prewarm_subject_quiz", "schedule_prewarm", "reset_prewarm_state"]
