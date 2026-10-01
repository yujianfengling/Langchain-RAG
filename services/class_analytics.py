"""
班级学情分析（2026-09-26）

原则（对应本轮要求）：
- **统计一律由程序依据真实记录计算**（quiz_attempts / learning_tasks / class_members），
  不依赖模型；AI 只用于"解释与教学建议"，且必须在程序给出的数字范围内说话。
- 每个结论都带上 **班级、时间范围、参与人数、样本量、数据来源**。
- 样本过少或没有数据时明确返回 `enough_data=false` 与原因，**不给看似精确的班级结论**。
- 只使用汇总数据：**不读取学生个人聊天记录原文**，不返回单个学生的私聊内容。

**统计口径（2026-09-27 明确，避免重复提交导致重复计数）**：
- 参与情况（participation）：按**原始作答记录**计数（attempt_count），
  同时给出「去重作答数」distinct_question_count = 不重复的 (学生, 题目) 组合数，
  以及 average_attempts_per_question，便于识别"反复重做"。
- 知识点作答 / 常见错题：按**每个学生每题的最新一次作答**统计
  （教学上看"最终是否答对"），同时保留原始计数 raw_attempt_count / raw_wrong_count
  以便核对，避免"同一题连错三次"被算成三个错题样本。
"""

from datetime import date, datetime, timedelta
import json
from typing import Any, Dict, List, Optional

from utils.logger import logger

from services import teaching_access as access
from services import teaching_data as td
from services.teaching_data import TeachingError

# 样本量下限（低于此值不下结论）
MIN_ATTEMPTS_FOR_CONCLUSION = 5
MIN_STUDENTS_FOR_CONCLUSION = 2

DATA_SOURCE_QUIZ = "quiz_attempts（学生作答与后端判分记录）"
DATA_SOURCE_TASK = "learning_tasks（学习任务与完成状态）"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _range(days: Optional[int], start: Optional[str], end: Optional[str]) -> Dict[str, str]:
    today = date.today()
    if start and end:
        try:
            s, e = date.fromisoformat(start), date.fromisoformat(end)
            if s > e:
                raise TeachingError("开始日期不能晚于结束日期", "invalid_request")
            return {"start": s.isoformat(), "end": e.isoformat()}
        except ValueError:
            raise TeachingError("日期格式应为 YYYY-MM-DD", "invalid_request")
    d = int(days or 30)
    if d <= 0 or d > 365:
        raise TeachingError("时间范围应在 1-365 天之间", "invalid_request")
    return {"start": (today - timedelta(days=d - 1)).isoformat(), "end": today.isoformat()}


def class_overview(user_id: str, class_id: str, days: int = 30,
                   start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, Any]:
    """
    班级学情总览：参与情况、任务完成、知识点作答、常见错题、变化趋势

    全部指标由程序计算；`enough_data` 为 false 时前端应显示"数据不足"。
    """
    klass = access.require_class_teacher(user_id, class_id)
    course = td.get_course(klass["course_id"]) or {}
    subject_id = course.get("subject_id")
    rng = _range(days, start, end)

    members = td.list_members(class_id, status="active")
    student_ids = [m["user_id"] for m in members]
    meta = {
        "class_id": class_id,
        "class_name": klass["name"],
        "course_id": klass["course_id"],
        "course_title": course.get("title"),
        "subject_id": subject_id,
        "range": rng,
        "generated_at": _now(),
        "sources": [DATA_SOURCE_QUIZ, DATA_SOURCE_TASK],
    }

    if not student_ids or not subject_id:
        return {**meta, "enough_data": False,
                "insufficient_reason": "班级还没有学生，或课程还没有知识库",
                "participation": {"student_count": len(student_ids),
                                  "answered_student_count": 0,
                                  "attempt_count": 0, "attempt_student_ratio": None},
                "task_completion": None, "knowledge_points": [],
                "weak_knowledge_points": [], "trend": []}

    from services import quiz_service
    conn = quiz_service._get_db()
    try:
        marks = ",".join("?" for _ in student_ids)
        attempts = conn.execute(
            f"""SELECT a.user_id, a.question_id, a.knowledge_point_id, a.knowledge_point_name,
                       a.is_correct, a.grade_status, a.attempt_kind, a.created_at
                FROM quiz_attempts a
                WHERE a.user_id IN ({marks}) AND a.subject_id=?
                  AND date(a.created_at) BETWEEN ? AND ?""",
            [*student_ids, subject_id, rng["start"], rng["end"]]).fetchall()
    finally:
        conn.close()

    attempt_rows = [dict(r) for r in attempts]
    graded = [a for a in attempt_rows if a.get("grade_status") == "graded"
              and a.get("is_correct") is not None]
    answered_students = {a["user_id"] for a in attempt_rows}

    # 参与情况
    distinct_pairs = {(a["user_id"], a["question_id"]) for a in attempt_rows}
    participation = {
        "student_count": len(student_ids),
        "answered_student_count": len(answered_students),
        # 原始作答次数（含重复提交/重做）
        "attempt_count": len(attempt_rows),
        # 去重作答数：不重复的 (学生, 题目) 组合 —— 与"作业题量×作答人数"对得上
        "distinct_question_count": len(distinct_pairs),
        "average_attempts_per_question": (round(len(attempt_rows) / len(distinct_pairs), 2)
                                          if distinct_pairs else None),
        "graded_count": len(graded),
        "attempt_student_ratio": (round(len(answered_students) / len(student_ids), 3)
                                  if student_ids else None),
        "source": DATA_SOURCE_QUIZ,
        "note": "attempt_count 含重做；判定掌握情况时以每题最新一次为准",
    }

    # 任务完成情况（教师布置的作业任务 + 全部任务）
    completed, total = _task_completion(student_ids, subject_id, rng)
    task_completion = {
        "total": total, "completed": completed,
        "ratio": round(completed / total, 3) if total else None,
        "source": DATA_SOURCE_TASK,
    }

    # 去重口径：每个学生每题的最新一次作答（教学上关心"最终答对没有"）
    latest = _latest_attempts_per_question(graded)
    # 知识点作答情况（最新口径 + 原始计数）
    kp_stats = _knowledge_point_stats(graded, latest)
    # 常见错题（最新口径）
    wrong_questions = _wrong_questions(latest, graded)
    # 时间趋势（按天，原始计数）
    trend = _trend(attempt_rows, rng)

    sample_enough = (len(graded) >= MIN_ATTEMPTS_FOR_CONCLUSION
                     and len(answered_students) >= MIN_STUDENTS_FOR_CONCLUSION)
    reasons = []
    if len(graded) < MIN_ATTEMPTS_FOR_CONCLUSION:
        reasons.append(f"已判分作答仅 {len(graded)} 次（少于 {MIN_ATTEMPTS_FOR_CONCLUSION} 次）")
    if len(answered_students) < MIN_STUDENTS_FOR_CONCLUSION:
        reasons.append(f"参与作答的学生仅 {len(answered_students)} 人"
                       f"（少于 {MIN_STUDENTS_FOR_CONCLUSION} 人）")

    return {
        **meta,
        "enough_data": sample_enough,
        "insufficient_reason": "；".join(reasons) if reasons else None,
        "participation": participation,
        "task_completion": task_completion,
        "knowledge_points": kp_stats,
        "weak_knowledge_points": [k for k in kp_stats if k["enough_data"] and k["wrong_rate"] is not None
                                  and k["wrong_count"] > 0][:8],
        "wrong_questions": wrong_questions[:10],
        "trend": trend,
        "thresholds": {"min_attempts": MIN_ATTEMPTS_FOR_CONCLUSION,
                       "min_students": MIN_STUDENTS_FOR_CONCLUSION},
    }


def _task_completion(student_ids: List[str], subject_id: str,
                     rng: Dict[str, str]) -> tuple:
    # 任务在 learning.db（services.learning_service），不是 learning_loop.db
    from services import learning_service
    conn = learning_service._get_db()
    try:
        marks = ",".join("?" for _ in student_ids)
        rows = conn.execute(
            f"""SELECT status, COUNT(*) AS c FROM learning_tasks
                WHERE user_id IN ({marks}) AND subject_id=?
                  AND COALESCE(planned_date, substr(created_at,1,10)) BETWEEN ? AND ?
                GROUP BY status""",
            [*student_ids, subject_id, rng["start"], rng["end"]]).fetchall()
        total = sum(r["c"] for r in rows)
        completed = sum(r["c"] for r in rows if r["status"] == "COMPLETED")
        return completed, total
    finally:
        conn.close()


def _latest_attempts_per_question(graded: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    每个 (学生, 题目) 只保留**时间上最后一次**作答

    教学口径：学生重做后最终答对了，就不应再算作"这道题不会"。
    没有 created_at 的记录按原顺序保留（退化处理，不影响计数正确性）。
    """
    latest: Dict[tuple, Dict[str, Any]] = {}
    for a in graded:
        key = (a.get("user_id"), a.get("question_id"))
        prev = latest.get(key)
        if prev is None or str(a.get("created_at") or "") >= str(prev.get("created_at") or ""):
            latest[key] = a
    return list(latest.values())


def _knowledge_point_stats(graded: List[Dict[str, Any]],
                           latest: Optional[List[Dict[str, Any]]] = None
                           ) -> List[Dict[str, Any]]:
    """
    知识点作答情况

    - 结论用的数字（attempt_count/wrong_count/wrong_rate）按**每题最新一次**统计
    - 同时给出 raw_attempt_count/raw_wrong_count（原始记录计数），便于与原始数据核对
    """
    sample = latest if latest is not None else graded
    raw_by_kp: Dict[str, Dict[str, int]] = {}
    for a in graded:
        key = a.get("knowledge_point_id") or a.get("knowledge_point_name") or "未标注知识点"
        slot = raw_by_kp.setdefault(key, {"attempts": 0, "wrong": 0})
        slot["attempts"] += 1
        if _is_wrong(a.get("is_correct")):
            slot["wrong"] += 1

    by_kp: Dict[str, Dict[str, Any]] = {}
    for a in sample:
        key = a.get("knowledge_point_id") or a.get("knowledge_point_name") or "未标注知识点"
        item = by_kp.setdefault(key, {
            "knowledge_point_id": a.get("knowledge_point_id"),
            "knowledge_point": a.get("knowledge_point_name") or "未标注知识点",
            "attempt_count": 0, "wrong_count": 0, "students": set(),
        })
        item["attempt_count"] += 1
        if _is_wrong(a.get("is_correct")):
            item["wrong_count"] += 1
        item["students"].add(a["user_id"])
    out = []
    for item in by_kp.values():
        students = item.pop("students")
        item["student_count"] = len(students)
        item["wrong_rate"] = round(item["wrong_count"] / item["attempt_count"], 3) \
            if item["attempt_count"] else None
        item["enough_data"] = item["attempt_count"] >= MIN_ATTEMPTS_FOR_CONCLUSION
        item["source"] = DATA_SOURCE_QUIZ
        # 原始计数（含重做），用于与原始记录核对
        raw = raw_by_kp.get(item.get("knowledge_point_id") or item["knowledge_point"], {})
        item["raw_attempt_count"] = raw.get("attempts", item["attempt_count"])
        item["raw_wrong_count"] = raw.get("wrong", item["wrong_count"])
        item["basis"] = "每题最新一次作答"
        out.append(item)
    # 错得多的排前面；样本不足的排后面（避免用 1 次作答得出"最难知识点"）
    out.sort(key=lambda x: (not x["enough_data"], -(x["wrong_rate"] or 0),
                            -x["wrong_count"], x["knowledge_point"]))
    return out


def _wrong_questions(latest: List[Dict[str, Any]],
                     graded: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    常见错题（按**每题最新一次作答**统计：重做答对后不再算错）

    raw_wrong_count 为原始记录里的错误次数，用于核对。
    """
    from services import quiz_service
    raw_by_q: Dict[str, int] = {}
    for a in (graded if graded is not None else latest):
        if not a.get("is_correct"):
            raw_by_q[a["question_id"]] = raw_by_q.get(a["question_id"], 0) + 1
    by_q: Dict[str, Dict[str, Any]] = {}
    for a in latest:
        item = by_q.setdefault(a["question_id"], {"question_id": a["question_id"],
                                                  "attempt_count": 0, "wrong_count": 0,
                                                  "students": set(),
                                                  "knowledge_point": a.get("knowledge_point_name")})
        item["attempt_count"] += 1
        if not a.get("is_correct"):
            item["wrong_count"] += 1
        item["students"].add(a["user_id"])
    out = []
    for qid, item in by_q.items():
        if item["wrong_count"] == 0:
            continue
        students = item.pop("students")
        item["student_count"] = len(students)
        item["wrong_rate"] = round(item["wrong_count"] / item["attempt_count"], 3)
        item["raw_wrong_count"] = raw_by_q.get(qid, item["wrong_count"])
        item["basis"] = "每题最新一次作答"
        q = quiz_service.get_question_public(qid)
        if q:
            item["stem"] = q.get("stem")
            item["question_type"] = q.get("question_type")
            item["options"] = q.get("options")  # 不含答案
        out.append(item)
    out.sort(key=lambda x: (-x["wrong_count"], -x["wrong_rate"]))
    return out


def _is_wrong(value: Any) -> bool:
    """
    判定一次作答是否**确定答错**。

    注意：SQLite 里 is_correct 是整数 0/1，不是 Python 的 False/True，
    所以不能用 `value is False`（实测踩过：趋势里的错误次数恒为 0）。
    None / 空串表示"未判分"，既不算对也不算错。
    """
    if value is None or value == "":
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("0", "false", "no")
    return not value


def _trend(attempt_rows: List[Dict[str, Any]], rng: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    按天的作答趋势（只统计**已判分**的作答，未判分的不计入分母）
    """
    by_day: Dict[str, Dict[str, int]] = {}
    for a in attempt_rows:
        day = str(a.get("created_at") or "")[:10]
        if not day:
            continue
        if a.get("is_correct") is None or a.get("is_correct") == "":
            continue                      # 未判分：不进趋势，避免错误率被稀释
        item = by_day.setdefault(day, {"attempt_count": 0, "wrong_count": 0, "students": set()})
        item["attempt_count"] += 1
        if _is_wrong(a.get("is_correct")):
            item["wrong_count"] += 1
        item["students"].add(a["user_id"])
    out = []
    for day in sorted(by_day):
        item = by_day[day]
        students = item.pop("students")
        graded = item["attempt_count"]
        out.append({"date": day, "attempt_count": graded, "wrong_count": item["wrong_count"],
                    "student_count": len(students),
                    "wrong_rate": round(item["wrong_count"] / graded, 3) if graded else None})
    return out


def knowledge_point_detail(user_id: str, class_id: str, knowledge_point_id: str,
                           days: int = 30) -> Dict[str, Any]:
    """
    某个知识点的下钻：错得多的题目 → 相关课程资料 → 建议动作

    只返回**匿名汇总**（不返回学生姓名与个人对话）
    """
    overview = class_overview(user_id, class_id, days=days)
    kp = next((k for k in overview["knowledge_points"]
               if k.get("knowledge_point_id") == knowledge_point_id), None)
    if not kp:
        raise TeachingError("该知识点在选定时间范围内没有作答记录", "not_found")

    from services import quiz_service
    conn = quiz_service._get_db()
    try:
        rows = conn.execute(
            """SELECT question_id, COUNT(*) AS c,
                      SUM(CASE WHEN is_correct=0 THEN 1 ELSE 0 END) AS wrong
               FROM quiz_attempts
               WHERE subject_id=? AND knowledge_point_id=?
                 AND date(created_at) BETWEEN ? AND ? AND grade_status='graded'
               GROUP BY question_id HAVING wrong > 0
               ORDER BY wrong DESC LIMIT 20""",
            (overview["subject_id"], knowledge_point_id,
             overview["range"]["start"], overview["range"]["end"])).fetchall()
    finally:
        conn.close()

    questions = []
    for r in rows:
        q = quiz_service.get_question_public(r["question_id"]) or {}
        questions.append({
            "question_id": r["question_id"],
            "stem": q.get("stem"),
            "options": q.get("options"),
            "knowledge_point_name": q.get("knowledge_point_name"),
            "attempt_count": r["c"],
            "wrong_count": r["wrong"],
        })

    materials = _related_materials(overview["course_id"], kp.get("knowledge_point") or "")
    return {
        "class_id": class_id,
        "course_id": overview["course_id"],
        "range": overview["range"],
        "knowledge_point": kp,
        "questions": questions,
        "materials": materials,
        "enough_data": kp.get("enough_data", False),
        "sources": [DATA_SOURCE_QUIZ],
        "next_actions": _next_actions(kp),
    }


def _related_materials(course_id: str, keyword: str) -> List[Dict[str, Any]]:
    """相关课程资料（供教师"回看资料/调整教案"）"""
    out = []
    for m in td.list_materials(course_id, include_teacher_only=True):
        if m["status"] != "ready":
            continue
        out.append({"material_id": m["id"], "filename": m["filename"],
                    "visibility": m["visibility"], "matched": bool(keyword and keyword in m["filename"])})
    out.sort(key=lambda x: not x["matched"])
    return out[:10]


def _next_actions(kp: Dict[str, Any]) -> List[str]:
    """可执行建议（由程序基于数字给出，不夸大）"""
    actions = []
    if not kp.get("enough_data"):
        actions.append("样本量不足，建议先让学生完成练习再判断该知识点的掌握情况")
    if kp.get("wrong_count"):
        actions.append("可针对该知识点发布一次补充练习（题目可沿用错得最多的题干变式）")
        actions.append("可回看对应课程资料，若资料没有覆盖该知识点，先补充资料再重建知识库")
    return actions


def explain_with_ai(user_id: str, class_id: str, days: int = 30,
                    model: Optional[str] = None) -> Dict[str, Any]:
    """
    AI 解释学情并给教学建议。

    - 输入只有**程序算出来的汇总数字**（不含任何学生个人聊天内容）
    - 样本不足时**不调用模型**，直接返回"数据不足"，避免生成看似精确的结论
    """
    overview = class_overview(user_id, class_id, days=days)
    if not overview.get("enough_data"):
        return {"enough_data": False,
                "reason": overview.get("insufficient_reason") or "数据不足",
                "overview": overview, "explanation": None, "suggestions": [],
                "note": "样本量不足，未调用模型；请以参与情况与完成情况为准"}

    from services import ai_service
    payload = {
        "班级": overview["class_name"],
        "课程": overview["course_title"],
        "时间范围": f"{overview['range']['start']} ~ {overview['range']['end']}",
        "参与情况": overview["participation"],
        "任务完成": overview["task_completion"],
        "知识点作答": [{k: v for k, v in kp.items() if k != "source"}
                       for kp in overview["knowledge_points"][:10]],
        "错得最多的题目": [{"题干": w.get("stem"), "错误次数": w.get("wrong_count"),
                            "作答次数": w.get("attempt_count")}
                           for w in overview.get("wrong_questions", [])[:5]],
    }
    prompt = (
        "下面是一个班级的真实学情统计（由程序从作答记录算出，未包含任何学生个人聊天内容）。\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n\n"
        "请输出严格 JSON：{\"explanation\": \"用 3-5 句话说明当前学情\", "
        "\"suggestions\": [\"下一次课可以怎么做（具体、可执行，2-4 条）\"], "
        "\"cautions\": [\"需要提醒教师注意的数据局限（例如样本量、只看了一部分学生）\"]}\n"
        "要求：只能引用上面出现过的数字；不要编造学生姓名或个体表现；"
        "不要夸大结论。"
    )
    try:
        raw = ai_service.call_llm(prompt, system_message="你是一名教研员，基于数据说话，不夸大。",
                                  model=model, temperature=0.3, max_tokens=1200)
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1] if "```" in text[3:] else text[3:]
            if text.lstrip().lower().startswith("json"):
                text = text.lstrip()[4:]
        s, e = text.find("{"), text.rfind("}")
        data = json.loads(text[s:e + 1] if s >= 0 and e > s else text)
    except Exception as e:
        raise TeachingError(f"AI 解读失败：{e}", "llm_error")

    model_info = ai_service.resolve_model_info(model)
    return {
        "enough_data": True,
        "overview": overview,
        "explanation": data.get("explanation"),
        "suggestions": data.get("suggestions") or [],
        "cautions": data.get("cautions") or [],
        "model": f"{model_info.get('provider')}:{model_info.get('model')}",
        "note": "解释由 AI 生成，数字来自程序统计；请结合教学实际判断",
    }


def teacher_dashboard(user_id: str) -> Dict[str, Any]:
    """
    教师工作台：我的课程 / 待完成的教案 / 已发布任务 / 需要关注的知识点
    """
    from services import teaching_service

    courses = teaching_service.list_teacher_courses(user_id)
    pending_plans, published_assignments, focus_points = [], [], []
    for course in courses:
        for plan in td.list_lesson_plans(course["id"], status="draft"):
            pending_plans.append({"plan_id": plan["id"], "title": plan["title"],
                                  "course_id": course["id"], "course_title": course["title"],
                                  "updated_at": plan["updated_at"]})
        for a in td.list_assignments(course_id=course["id"], status="published"):
            klass = td.get_class(a["class_id"]) or {}
            bucket = (a.get("published_at") or "")[:10]
            published_assignments.append({
                "assignment_id": a["id"], "title": a["title"], "course_id": course["id"],
                "course_title": course["title"], "class_name": klass.get("name"),
                "due_date": a.get("due_date"), "published_at": a.get("published_at"),
                "_sort": bucket,
            })
        for klass in td.list_classes(course["id"]):
            try:
                overview = class_overview(user_id, klass["id"])
            except Exception as e:
                logger.warning(f"学情统计失败 class={klass['id']}: {e}")
                continue
            for kp in overview.get("weak_knowledge_points", [])[:3]:
                focus_points.append({
                    "class_id": klass["id"], "class_name": klass["name"],
                    "course_id": course["id"], "course_title": course["title"],
                    "knowledge_point_id": kp.get("knowledge_point_id"),
                    "knowledge_point": kp.get("knowledge_point"),
                    "wrong_count": kp.get("wrong_count"),
                    "attempt_count": kp.get("attempt_count"),
                    "wrong_rate": kp.get("wrong_rate"),
                    "enough_data": kp.get("enough_data"),
                    "range": overview["range"],
                })
    published_assignments.sort(key=lambda x: x.pop("_sort") or "", reverse=True)
    focus_points.sort(key=lambda x: -(x.get("wrong_rate") or 0))
    return {
        "role": "teacher",
        "courses": courses,
        "pending_lesson_plans": sorted(pending_plans, key=lambda x: x["updated_at"], reverse=True),
        "published_assignments": published_assignments[:20],
        "focus_knowledge_points": focus_points[:10],
    }


__all__ = ["class_overview", "knowledge_point_detail", "explain_with_ai", "teacher_dashboard"]
