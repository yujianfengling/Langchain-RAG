"""
教师作业/练习服务（2026-09-26）

关键约束（对应本轮要求）：
- **复用既有题库与判分**：题目一律走 `quiz_service.persist_questions`（答案服务端保存、
  下发不含答案），学生作答走 `quiz_service.submit_attempt`（后端权威判分、幂等、答案隔离），
  不再新建一套题库。
- **教师固定截止时间**：发布作业时把 `due_date` 写入学生 `plan_constraints.teacher_deadline`，
  学生的个人计划与 Agent 调整**只能更早、不能推后**（该规则已在 plan_adjustment 中实现）。
- 发布是幂等的：同一次作业不会给学生重复堆任务。
- 草稿状态学生完全不可见（学生接口只返回 published/closed）。
"""

import json
from datetime import date
from typing import Any, Dict, List, Optional

from utils.logger import logger

from services import teaching_access as access
from services import teaching_data as td
from services.teaching_data import TeachingError

QUESTION_PROMPT = """你是一名命题老师，请依据下面的课程资料出 {count} 道**可判对错的练习题**。

## 知识点
{knowledge_points}

## 课程资料（只能依据这些内容命题，不要编造）
{context}

## 输出要求（严格 JSON，不要输出任何其他内容）
{{
  "questions": [
    {{
      "type": "single_choice",
      "question": "题干",
      "options": ["选项A内容", "选项B内容", "选项C内容", "选项D内容"],
      "answer": "正确选项的内容（必须与某个选项完全一致）",
      "explanation": "为什么选它，并指出依据"
    }}
  ]
}}

## 规则
1. type 只用 single_choice（单选）或 judgement（判断题用 options=["正确","错误"]）。
2. 每题必须有唯一正确答案，答案必须能在资料里找到依据。
3. 若资料不足，就少出题（可以少于 {count} 道），不要编造知识点。
"""


def _parse_questions(raw: str) -> List[Dict[str, Any]]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    data = json.loads(text)
    items = data.get("questions") if isinstance(data, dict) else data
    return [q for q in (items or []) if isinstance(q, dict)]


# ==================== 教师侧 ====================

def create_assignment(user_id: str, course_id: str, *, class_id: str, title: str,
                      description: Optional[str] = None,
                      assign_type: str = "quiz",
                      knowledge_points: Optional[List[str]] = None,
                      due_date: Optional[str] = None,
                      estimated_minutes: int = 30,
                      question_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    course = access.require_course_teacher(user_id, course_id)
    klass = access.require_class_teacher(user_id, class_id)
    if klass["course_id"] != course_id:
        raise TeachingError("班级不属于该课程", "invalid_request")
    _validate_due(due_date)

    # 手工指定的题目必须属于本课程学科（防止挂载别的课程/学科的题）
    if question_ids:
        _assert_questions_belong(course["subject_id"], question_ids)

    assignment = td.create_assignment(
        course_id, class_id, course["subject_id"], created_by=user_id, title=title,
        description=description, assign_type=assign_type,
        knowledge_points=knowledge_points, due_date=due_date,
        estimated_minutes=estimated_minutes, question_ids=question_ids)
    td.audit(course_id, user_id, "assignment.create", "assignment", assignment["id"],
             {"class_id": class_id, "due_date": due_date})
    return assignment


def _validate_due(due_date: Optional[str]) -> None:
    if not due_date:
        return
    try:
        date.fromisoformat(due_date)
    except ValueError:
        raise TeachingError("截止日期格式应为 YYYY-MM-DD", "invalid_request")


def _assert_questions_belong(subject_id: str, question_ids: List[str]) -> None:
    from services import quiz_service
    for qid in question_ids:
        q = quiz_service.get_question_admin(qid)
        if not q:
            raise TeachingError(f"题目不存在：{qid}", "invalid_request")
        if q["subject_id"] != subject_id:
            raise TeachingError("只能选用本课程知识库中的题目", "forbidden")


def generate_questions(user_id: str, course_id: str, *, assignment_id: Optional[str] = None,
                       knowledge_points: Optional[List[str]] = None, count: int = 3,
                       model: Optional[str] = None) -> Dict[str, Any]:
    """
    AI 依据课程资料出题（可直接挂到草稿作业上）

    资料不足时**少出题**并如实说明，不编造。
    """
    course = access.require_course_teacher(user_id, course_id)
    try:
        # 前端未填数量时会传 null：按默认值处理，而不是抛 422 的原始校验错误
        count = 3 if count is None else int(count)
    except (TypeError, ValueError):
        raise TeachingError("题目数量必须是数字", "invalid_request")
    if not (1 <= count <= 10):
        raise TeachingError("单次生成 1-10 道题", "invalid_request")

    kps = [str(k).strip() for k in (knowledge_points or []) if str(k).strip()]
    from services import ai_service
    query = " ".join(kps) or course["title"]
    try:
        # 教师侧检索：严格阈值为空时放宽一次，避免"分块清洗后课程标题查不到"导致误报资料为空
        rag = ai_service.retrieve_for_teaching(query, course["subject_id"], top_k=5,
                                               viewer_user_id=user_id)
    except Exception as e:
        raise TeachingError(f"课程资料检索失败：{e}", "retrieval_failed")

    context = rag.get("context") or ""
    if not context.strip():
        raise TeachingError(
            "课程资料为空，无法出题（请先上传课程资料并重建知识库）", "insufficient_evidence")
    try:
        raw = ai_service.call_llm(
            QUESTION_PROMPT.format(count=count, context=context,
                                   knowledge_points="、".join(kps) or "（未指定，按资料重点出题）"),
            system_message="你是严谨的命题老师，只依据给定资料命题。",
            model=model, temperature=0.3, max_tokens=2000)
        items = _parse_questions(raw)
    except Exception as e:
        raise TeachingError(f"出题失败：{e}", "llm_error")
    if not items:
        raise TeachingError("模型没有产出可用题目（资料可能不足）", "insufficient_evidence")

    from services import quiz_service
    kp_name = kps[0] if kps else (course["title"] or "课程练习")
    public = quiz_service.persist_questions(
        subject_id=course["subject_id"], knowledge_point_name=kp_name,
        questions=items, source="teacher_assignment")
    question_ids = [q["id"] for q in public if q.get("id")]

    if assignment_id:
        assignment = td.get_assignment(assignment_id)
        if not assignment or assignment["course_id"] != course_id:
            raise TeachingError("作业不存在或不属于本课程", "not_found")
        merged = list(dict.fromkeys(list(assignment.get("question_ids") or []) + question_ids))
        td.set_assignment_questions(assignment_id, merged)

    model_info = ai_service.resolve_model_info(model)
    td.audit(course_id, user_id, "assignment.generate_questions", "assignment",
             assignment_id or course_id,
             {"count": len(question_ids), "knowledge_points": kps,
              "requested": count})
    return {"questions": public, "question_ids": question_ids,
            "requested": count, "generated": len(question_ids),
            "knowledge_point": kp_name,
            "evidence": rag.get("sources") or [],
            "model": f"{model_info.get('provider')}:{model_info.get('model')}"}


def publish(user_id: str, assignment_id: str) -> Dict[str, Any]:
    """
    发布作业：物化学生任务 + 设定教师固定截止时间

    - 只对**当前在班**学生生效（发布后退班的学生不再新增任务）
    - 幂等：重复发布会跳过已存在的任务
    - 结果里如实报告成功/失败人数（不谎报全部成功）
    """
    assignment = td.get_assignment(assignment_id)
    if not assignment:
        raise TeachingError("作业不存在", "not_found")
    course = access.require_course_teacher(user_id, assignment["course_id"])
    if assignment["status"] == "published":
        return {"assignment": assignment, "already_published": True,
                "created": [], "failed": [], "skipped": []}
    if assignment["status"] == "closed":
        raise TeachingError("已关闭的作业不能再次发布", "invalid_request")
    if assignment["assign_type"] == "quiz" and not assignment["question_ids"]:
        raise TeachingError("作业还没有题目，无法发布", "invalid_request")

    due = assignment.get("due_date")
    _validate_due(due)

    from services import learning_service
    from services import plan_adjustment

    created, skipped, failed = [], [], []
    for member in td.list_members(assignment["class_id"], status="active"):
        student_id = member["user_id"]
        try:
            existed = bool(learning_service.find_teacher_tasks(student_id, assignment_id))
            task_id = learning_service.create_teacher_task(
                student_id, course["subject_id"],
                title=f"【{course['title']}】{assignment['title']}",
                description=assignment.get("description") or "教师布置的作业",
                task_type="QUIZ" if assignment["assign_type"] == "quiz" else "PRACTICE",
                knowledge_point="、".join(assignment.get("knowledge_points") or []) or None,
                planned_date=due,
                estimated_minutes=int(assignment.get("estimated_minutes") or 30),
                source_id=assignment_id)
            if not task_id:
                failed.append({"user_id": student_id, "reason": "任务创建失败"})
                continue
            entry = {"user_id": student_id, "task_id": task_id}
            (skipped if existed else created).append(entry)
            if due:
                # 教师固定截止：写入约束（学生侧只能更早，不能推后）
                try:
                    plan_adjustment.set_constraints(
                        student_id, course["subject_id"], teacher_deadline=due)
                except Exception as e:
                    failed.append({"user_id": student_id,
                                   "reason": f"截止时间写入失败：{e}"})
        except Exception as e:
            failed.append({"user_id": student_id, "reason": str(e)})

    published = td.mark_assignment_published(assignment_id, due_date=due)
    td.audit(course["id"], user_id, "assignment.publish", "assignment", assignment_id,
             {"created": len(created), "failed": len(failed), "due_date": due})
    logger.info(f"发布作业: id={assignment_id} 学生任务 {len(created)} 个, 失败 {len(failed)}")
    return {"assignment": published, "already_published": False,
            "created": created, "skipped": skipped, "failed": failed,
            "student_count": len(td.list_members(assignment["class_id"], status="active"))}


def update_assignment(user_id: str, assignment_id: str, *, title: Optional[str] = None,
                      description: Optional[str] = None, due_date: Optional[str] = None,
                      knowledge_points: Optional[List[str]] = None,
                      estimated_minutes: Optional[int] = None,
                      question_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    assignment = td.get_assignment(assignment_id)
    if not assignment:
        raise TeachingError("作业不存在", "not_found")
    course = access.require_course_teacher(user_id, assignment["course_id"])
    if assignment["status"] != "draft":
        raise TeachingError("已发布的作业不能修改（如需变更请新建一次作业，"
                            "避免学生的截止时间被静默改动）", "invalid_request")
    _validate_due(due_date)
    if question_ids is not None:
        _assert_questions_belong(course["subject_id"], question_ids)
        td.set_assignment_questions(assignment_id, question_ids)

    sets, params = [], []
    if title is not None:
        if not title.strip():
            raise TeachingError("作业标题不能为空", "invalid_request")
        sets.append("title=?"); params.append(title.strip())
    if description is not None:
        sets.append("description=?"); params.append(description)
    if due_date is not None:
        sets.append("due_date=?"); params.append(due_date)
    if knowledge_points is not None:
        sets.append("knowledge_points_json=?"); params.append(json.dumps(knowledge_points, ensure_ascii=False))
    if estimated_minutes is not None:
        sets.append("estimated_minutes=?"); params.append(int(estimated_minutes))
    if sets:
        conn = td._get_db()
        try:
            sets.append("updated_at=?"); params.append(td._now()); params.append(assignment_id)
            conn.execute(f"UPDATE assignments SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
        finally:
            conn.close()
    td.audit(course["id"], user_id, "assignment.update", "assignment", assignment_id, {})
    return td.get_assignment(assignment_id) or {}


def list_teacher_assignments(user_id: str, course_id: str,
                             class_id: Optional[str] = None) -> List[Dict[str, Any]]:
    access.require_course_teacher(user_id, course_id)
    out = []
    for a in td.list_assignments(course_id=course_id, class_id=class_id):
        quest = _assignment_questions_public(a["id"])
        published = _publish_stats(a)
        out.append({**a, "questions": quest, "publish_stats": published})
    return out


def close_assignment(user_id: str, assignment_id: str) -> Dict[str, Any]:
    assignment = td.get_assignment(assignment_id)
    if not assignment:
        raise TeachingError("作业不存在", "not_found")
    access.require_course_teacher(user_id, assignment["course_id"])
    closed = td.close_assignment(assignment_id)
    td.audit(assignment["course_id"], user_id, "assignment.close", "assignment",
             assignment_id, {})
    return closed


# ==================== 题目与作答 ====================

def _assignment_questions_public(assignment_id: str) -> List[Dict[str, Any]]:
    """题目（不含标准答案；教师端也不需要下发答案，避免误泄露）"""
    from services import quiz_service
    out = []
    assignment = td.get_assignment(assignment_id) or {}
    for qid in assignment.get("question_ids") or []:
        q = quiz_service.get_question_public(qid)
        if q:
            out.append(q)
    return out


def _publish_stats(assignment: Dict[str, Any]) -> Dict[str, Any]:
    """发布范围与完成情况（按学生任务与作答记录统计）"""
    if assignment["status"] == "draft":
        return {"published": False}
    from services import learning_service
    members = td.list_members(assignment["class_id"], status="active")
    # 列表接口（td.list_assignments）不返回 question_ids，这里按需回查，
    # 否则"已作答人数"会永远算成 0（统计必须与真实记录一致）
    qids = assignment.get("question_ids")
    if qids is None:
        qids = (td.get_assignment(assignment["id"]) or {}).get("question_ids") or []
    attempted_students, completed = set(), 0
    for member in members:
        tasks = [t for t in learning_service.find_teacher_tasks(
            member["user_id"], assignment["id"])]
        if tasks and all(t.get("status") == "COMPLETED" for t in tasks):
            completed += 1
        if qids and _student_answered_any(member["user_id"], qids):
            attempted_students.add(member["user_id"])
    return {
        "published": True,
        "student_count": len(members),
        "task_completed_count": completed,
        "answered_student_count": len(attempted_students),
        "question_count": len(qids),
    }


def _student_answered_any(user_id: str, question_ids: List[str]) -> bool:
    from services import quiz_service
    if not question_ids:
        return False
    conn = quiz_service._get_db()
    try:
        marks = ",".join("?" for _ in question_ids)
        row = conn.execute(
            f"SELECT 1 FROM quiz_attempts WHERE user_id=? AND question_id IN ({marks}) LIMIT 1",
            [user_id, *question_ids]).fetchone()
        return row is not None
    finally:
        conn.close()


# ==================== 学生侧 ====================

def list_student_assignments(user_id: str, course_id: Optional[str] = None,
                             class_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    学生可见的作业：只返回已发布/已关闭的**自己班级**的作业，且不含标准答案。
    """
    class_ids = [class_id] if class_id else access.student_class_ids(user_id)
    if class_id:
        access.require_class_member(user_id, class_id)
    out = []
    for a in td.list_assignments_for_classes(class_ids, status=None):
        if a["status"] == "draft":
            continue  # 草稿对学生完全不可见
        if course_id and a["course_id"] != course_id:
            continue
        klass = td.get_class(a["class_id"]) or {}
        course = td.get_course(a["course_id"]) or {}
        attempts = _student_attempt_map(user_id, a.get("question_ids") or [])
        out.append({
            "id": a["id"],
            "title": a["title"],
            "description": a["description"],
            "due_date": a["due_date"],
            "status": a["status"],
            "course_id": a["course_id"],
            "course_title": course.get("title"),
            "class_id": a["class_id"],
            "class_name": klass.get("name"),
            "knowledge_points": a.get("knowledge_points") or [],
            "questions": _assignment_questions_public(a["id"]),
            "submitted": len(attempts),
            "question_count": len(a.get("question_ids") or []),
            "results": attempts,
            "published_at": a.get("published_at"),
        })
    return out


def _student_attempt_map(user_id: str, question_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """学生对本次作业各题的作答结果（已判分；用于学生端显示自己是否做过）"""
    from services import quiz_service
    if not question_ids:
        return {}
    conn = quiz_service._get_db()
    try:
        marks = ",".join("?" for _ in question_ids)
        rows = conn.execute(
            f"""SELECT question_id, is_correct, attempt_no, created_at, grade_status
                FROM quiz_attempts WHERE user_id=? AND question_id IN ({marks})
                ORDER BY attempt_no ASC""",
            [user_id, *question_ids]).fetchall()
    finally:
        conn.close()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        out[r["question_id"]] = {"is_correct": bool(r["is_correct"]),
                                 "attempt_no": r["attempt_no"],
                                 "grade_status": r["grade_status"],
                                 "created_at": r["created_at"]}
    return out


def assignment_for_student(user_id: str, assignment_id: str) -> Dict[str, Any]:
    """学生打开一次作业（含题目，不含答案）；必须是自己班级已发布的作业"""
    assignment = td.get_assignment(assignment_id)
    if not assignment:
        raise TeachingError("作业不存在", "not_found")
    if assignment["status"] == "draft":
        raise TeachingError("作业不存在", "not_found")  # 草稿对外一律"不存在"
    access.require_class_member(user_id, assignment["class_id"])
    course = td.get_course(assignment["course_id"]) or {}
    tasks = _student_tasks(user_id, assignment_id)
    return {
        **assignment,
        "course_title": course.get("title"),
        "questions": _assignment_questions_public(assignment_id),
        "my_tasks": tasks,
        "results": _student_attempt_map(user_id, assignment.get("question_ids") or []),
    }


def _student_tasks(user_id: str, assignment_id: str) -> List[Dict[str, Any]]:
    from services import learning_service
    return learning_service.find_teacher_tasks(user_id, assignment_id)


def complete_assignment_task(user_id: str, assignment_id: str) -> Dict[str, Any]:
    """
    学生完成作业任务（在作业页点"完成"时调用）。

    只在**题目都作答过**时才置为完成，避免"没做题就算完成"。
    """
    assignment = td.get_assignment(assignment_id)
    if not assignment:
        raise TeachingError("作业不存在", "not_found")
    access.require_class_member(user_id, assignment["class_id"])
    qids = assignment.get("question_ids") or []
    answered = _student_attempt_map(user_id, qids)
    if qids and len(answered) < len(qids):
        raise TeachingError(
            f"还有 {len(qids) - len(answered)} 道题没有作答，完成后才能标记完成",
            "invalid_request")
    from services import learning_service
    updated = []
    for task in _student_tasks(user_id, assignment_id):
        if task.get("status") == "COMPLETED":
            continue
        result = learning_service.complete_task(user_id, task["id"])
        updated.append(result)
    return {"assignment_id": assignment_id, "completed_tasks": len(updated)}


__all__ = [
    "create_assignment", "generate_questions", "publish", "update_assignment",
    "list_teacher_assignments", "close_assignment",
    "list_student_assignments", "assignment_for_student", "complete_assignment_task",
]
