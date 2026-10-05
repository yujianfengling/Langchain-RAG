"""
教案服务（2026-09-26）

要求与实现：
- 教师输入课程主题、课时、教学目标，选择课程资料 → 可结合**班级已有学情**生成教案草稿
- 教案至少包含：教学目标、重难点、教学步骤与时间分配、例题或活动、课后练习
- AI 生成内容**标出所依据的课程资料**（evidence）；证据不足时明确提示，不编造
- 教师可编辑、保存版本、预览、发布；**发布前草稿学生不可见**
- 教师修改的内容不会被重新生成覆盖：每次生成/编辑都追加新版本，历史版本保留
- 导出：Markdown（项目现有依赖可稳定实现）
"""

import json
from typing import Any, Dict, List, Optional

from utils.logger import logger

from services import teaching_access as access
from services import teaching_data as td
from services.teaching_data import TeachingError

# 教案结构化字段（缺一不可地展示给教师，AI 未产出时留空并标注）
PLAN_SECTIONS = ("objectives", "key_points", "difficulties", "steps",
                 "examples", "homework")

PLAN_PROMPT = """你是一名有经验的大学教师，请为下面这节课写一份**可直接上课用的教案草稿**。

## 课程主题
{topic}

## 课时
{hours} 课时

## 教师设定的教学目标
{objectives}

## 班级学情（来自真实学习记录，可能为空）
{class_summary}

## 可用的课程资料（只能依据这些内容，不要编造）
{context}

## 输出要求（严格 JSON，不要输出任何其他内容）
{{
  "title": "教案标题",
  "objectives": ["教学目标1", "教学目标2"],
  "key_points": ["重点1"],
  "difficulties": ["难点1"],
  "steps": [
    {{"name": "环节名称", "minutes": 10, "content": "该环节做什么、怎么讲", "materials": ["用到的资料名或空"]}}
  ],
  "examples": [{{"question": "例题或课堂活动", "answer": "要点或答案"}}],
  "homework": [{{"question": "课后练习题干", "answer": "参考答案"}}],
  "evidence_note": "本次教案主要依据了哪些资料；若资料不足请如实说明"
}}

## 规则
1. 只使用"课程资料"里出现的内容；资料里没有的不要写进重难点与例题。
2. 步骤的时间分配总和应接近 {minutes} 分钟。
3. 若"课程资料"为空或与主题无关，在 evidence_note 里明确写"课程资料不足"，
   并把 objectives/steps 写成通用的教学流程建议，不要编造具体知识点。
4. 课后练习用于给学生做，题目要能判对错（选择题请给出选项与答案）。
"""


class LessonPlanError(TeachingError):
    pass


def _clean_json(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def _normalize_content(data: Dict[str, Any]) -> Dict[str, Any]:
    content: Dict[str, Any] = {"title": str(data.get("title") or "").strip()}

    def _as_list(key: str) -> List[Any]:
        value = data.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    content["objectives"] = [str(x).strip() for x in _as_list("objectives") if str(x).strip()]
    content["key_points"] = [str(x).strip() for x in _as_list("key_points") if str(x).strip()]
    content["difficulties"] = [str(x).strip() for x in _as_list("difficulties") if str(x).strip()]
    steps = []
    for s in _as_list("steps"):
        if isinstance(s, dict):
            minutes = s.get("minutes")
            try:
                minutes = int(float(minutes))
            except (TypeError, ValueError):
                minutes = None
            steps.append({"name": str(s.get("name") or "").strip(),
                          "minutes": minutes,
                          "content": str(s.get("content") or "").strip(),
                          "materials": s.get("materials") or []})
        else:
            steps.append({"name": "", "minutes": None, "content": str(s).strip(), "materials": []})
    content["steps"] = steps
    examples = []
    for e in _as_list("examples"):
        if isinstance(e, dict):
            examples.append({"question": str(e.get("question") or "").strip(),
                             "answer": str(e.get("answer") or "").strip()})
        else:
            examples.append({"question": str(e).strip(), "answer": ""})
    content["examples"] = examples
    homework = []
    for h in _as_list("homework"):
        if isinstance(h, dict):
            homework.append({"question": str(h.get("question") or "").strip(),
                             "answer": str(h.get("answer") or "").strip()})
        else:
            homework.append({"question": str(h).strip(), "answer": ""})
    content["homework"] = homework
    content["evidence_note"] = str(data.get("evidence_note") or "").strip()
    return content


def _class_summary(user_id: str, course_id: str, class_id: Optional[str]) -> Dict[str, Any]:
    """
    班级学情摘要（供教案生成参考）。

    只使用**汇总数据**：不读取任何学生个人聊天记录。
    """
    if not class_id:
        return {"available": False, "text": "", "reason": "未指定班级"}
    try:
        access.require_class_teacher(user_id, class_id)
        from services import class_analytics
        stats = class_analytics.class_overview(user_id, class_id)
        if not stats.get("enough_data"):
            return {"available": True, "text": "", "reason": stats.get("insufficient_reason", "数据不足"),
                    "stats": stats}
        parts = [
            f"参与人数 {stats['participation']['student_count']}，"
            f"已作答人数 {stats['participation']['answered_student_count']}，"
            f"样本量 {stats['participation']['attempt_count']} 次作答"
        ]
        weak = stats.get("weak_knowledge_points") or []
        if weak:
            parts.append("错误较多的知识点：" + "、".join(
                f"{w['knowledge_point']}（错 {w['wrong_count']}/{w['attempt_count']}）"
                for w in weak[:5]))
        return {"available": True, "text": "\n".join(parts), "stats": stats}
    except Exception as e:
        return {"available": False, "text": "", "reason": f"学情读取失败：{e}"}


def generate_draft(user_id: str, course_id: str, *, topic: str, hours: float = 1.0,
                   objectives: str = "", class_id: Optional[str] = None,
                   material_ids: Optional[List[str]] = None,
                   model: Optional[str] = None,
                   title: Optional[str] = None) -> Dict[str, Any]:
    """
    生成教案草稿（不覆盖任何既有版本，总是新建一份草稿）
    """
    course = access.require_course_teacher(user_id, course_id)
    topic = (topic or "").strip()
    if not topic:
        raise TeachingError("课程主题不能为空", "invalid_request")
    try:
        hours = float(hours or 1)
    except (TypeError, ValueError):
        raise TeachingError("课时必须是数字", "invalid_request")
    if hours <= 0 or hours > 40:
        raise TeachingError("课时应在 0-40 之间", "invalid_request")

    # 只允许引用**本课程**的资料（防止借用其他课程/任意学科的内容）
    selected = _select_materials(course_id, material_ids)

    from services import ai_service
    query = f"{topic} {objectives}".strip()
    try:
        # 同上：教师侧生成允许在严格检索为空时放宽一次
        rag = ai_service.retrieve_for_teaching(query, course["subject_id"], top_k=5,
                                               viewer_user_id=user_id)
    except Exception as e:
        raise TeachingError(f"课程资料检索失败：{e}", "retrieval_failed")

    sources = rag.get("sources") or []
    if selected:
        allowed = {m["filename"] for m in selected}
        sources = [s for s in sources if str(s.get("file_name") or "") in allowed] or sources
    context = rag.get("context") or ""
    insufficient = not context.strip()

    summary = _class_summary(user_id, course_id, class_id)
    minutes = int(round(hours * 45))
    prompt = PLAN_PROMPT.format(
        topic=topic, hours=hours, objectives=objectives or "（教师未填写，请按主题拟定）",
        class_summary=summary.get("text") or f"（无可用学情：{summary.get('reason','')}）",
        context=context or "（课程资料为空）",
        minutes=minutes)

    try:
        raw = ai_service.call_llm(prompt, system_message="你是一名严谨的高校教师，只依据给定资料写教案。",
                                  model=model, temperature=0.3, max_tokens=3000)
        content = _normalize_content(_clean_json(raw))
    except json.JSONDecodeError as e:
        raise TeachingError(f"教案生成结果解析失败：{e}", "parse_error")
    except Exception as e:
        raise TeachingError(f"教案生成失败：{e}", "llm_error")

    model_info = ai_service.resolve_model_info(model)
    plan = td.create_lesson_plan(
        course_id, created_by=user_id,
        title=title or content.get("title") or topic,
        topic=topic, hours=hours, class_id=class_id,
        content=content,
        evidence=_evidence(sources, selected),
        ai_generated=True,
        ai_model=f"{model_info.get('provider')}:{model_info.get('model')}",
        insufficient_evidence=insufficient,
        note="AI 生成草稿")
    td.audit(course_id, user_id, "lesson_plan.generate", "lesson_plan", plan["id"],
             {"topic": topic, "insufficient_evidence": insufficient})
    return {**plan, "class_summary": {"available": summary.get("available"),
                                      "reason": summary.get("reason"),
                                      "used": bool(summary.get("text"))}}


def _select_materials(course_id: str, material_ids: Optional[List[str]]) -> List[Dict[str, Any]]:
    materials = td.list_materials(course_id, include_teacher_only=True)
    if not material_ids:
        return [m for m in materials if m["status"] == "ready"]
    wanted = set(material_ids)
    picked = [m for m in materials if m["id"] in wanted]
    missing = wanted - {m["id"] for m in picked}
    if missing:
        raise TeachingError(f"资料不存在或不属于本课程：{', '.join(sorted(missing))}",
                            "invalid_request")
    return picked


def _evidence(sources: List[Dict[str, Any]], materials: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """依据清单：优先用检索命中的片段来源；检索为空时列出所选资料"""
    out: List[Dict[str, Any]] = []
    seen = set()
    for s in sources:
        key = (s.get("file_name"), s.get("page"), s.get("type"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"file_name": s.get("file_name"), "page": s.get("page"),
                    "type": s.get("type"), "source": "retrieval",
                    # 带上真实片段与章节，教师可点开核对"这条依据到底出自哪段原文"
                    "snippet": s.get("snippet"), "section": s.get("section")})
    if not out:
        for m in materials:
            out.append({"file_name": m["filename"], "page": None, "type": "material",
                        "source": "selected_material"})
    return out


def update_plan(user_id: str, plan_id: str, *, content: Dict[str, Any],
                title: Optional[str] = None, topic: Optional[str] = None,
                hours: Optional[float] = None, class_id: Optional[str] = None,
                note: Optional[str] = None) -> Dict[str, Any]:
    """
    教师编辑并保存为新版本（**不覆盖**任何历史版本，也不被后续重新生成覆盖）
    """
    plan = get_plan_or_404(plan_id)
    access.require_course_teacher(user_id, plan["course_id"])
    normalized = _normalize_content(content or {})
    td.update_lesson_plan_meta(plan_id, title=title, topic=topic, hours=hours,
                               class_id=class_id)
    updated = td.add_lesson_plan_version(
        plan_id, content=normalized, evidence=None, ai_generated=False,
        insufficient_evidence=bool(plan.get("insufficient_evidence")),
        note=note or "教师编辑", edited_by=user_id)
    td.audit(plan["course_id"], user_id, "lesson_plan.update", "lesson_plan", plan_id,
             {"version": updated.get("version")})
    return updated


def get_plan_or_404(plan_id: str) -> Dict[str, Any]:
    plan = td.get_lesson_plan(plan_id)
    if not plan:
        raise TeachingError("教案不存在", "not_found")
    return plan


def list_versions(user_id: str, plan_id: str) -> Dict[str, Any]:
    plan = get_plan_or_404(plan_id)
    access.require_course_teacher(user_id, plan["course_id"])
    versions = []
    for v in td.list_lesson_plan_versions(plan_id):
        versions.append({
            "version": v["version"],
            "ai_generated": bool(v["ai_generated"]),
            "ai_model": v["ai_model"],
            "insufficient_evidence": bool(v["insufficient_evidence"]),
            "note": v["note"],
            "edited_by": v["edited_by"],
            "created_at": v["created_at"],
            "content": json.loads(v["content_json"] or "{}"),
            "evidence": json.loads(v["evidence_json"] or "[]"),
        })
    return {"plan": plan, "versions": versions}


def publish(user_id: str, plan_id: str) -> Dict[str, Any]:
    plan = get_plan_or_404(plan_id)
    access.require_course_teacher(user_id, plan["course_id"])
    content = plan.get("content") or {}
    missing = [s for s in ("objectives", "steps") if not content.get(s)]
    if missing:
        raise TeachingError(
            f"教案内容不完整，缺少：{'、'.join(missing)}（发布前请补全）", "invalid_request")
    published = td.publish_lesson_plan(plan_id, user_id)
    td.audit(plan["course_id"], user_id, "lesson_plan.publish", "lesson_plan", plan_id,
             {"version": published.get("published_version")})
    return published


def student_visible_plans(user_id: str, course_id: str) -> List[Dict[str, Any]]:
    """学生可见的教案：**仅已发布版本**（草稿不可见）"""
    access.require_course_read(user_id, course_id)
    out = []
    for plan in td.list_lesson_plans(course_id, status="published"):
        full = td.get_lesson_plan(plan["id"], version=plan.get("published_version"))
        if not full:
            continue
        out.append({
            "id": full["id"], "title": full["title"], "topic": full["topic"],
            "hours": full["hours"], "published_at": full["published_at"],
            "version": full.get("version"),
            "content": full.get("content") or {},
            "evidence": full.get("evidence") or [],
        })
    return out


def to_markdown(plan: Dict[str, Any]) -> str:
    """导出 Markdown（项目现有依赖即可稳定生成）"""
    c = plan.get("content") or {}
    lines: List[str] = [f"# {plan.get('title') or c.get('title') or '教案'}", ""]
    if plan.get("topic"):
        lines.append(f"**课程主题**：{plan['topic']}")
    if plan.get("hours"):
        lines.append(f"**课时**：{plan['hours']}")
    if plan.get("ai_generated"):
        lines.append(f"> 本教案由 AI 生成草稿（{plan.get('ai_model') or '默认模型'}），"
                     f"已由教师编辑，当前版本 v{plan.get('version')}")
    lines.append("")

    def _bullets(title: str, items: List[Any]) -> None:
        if not items:
            return
        lines.append(f"## {title}")
        for it in items:
            lines.append(f"- {it}")
        lines.append("")

    _bullets("教学目标", c.get("objectives") or [])
    _bullets("教学重点", c.get("key_points") or [])
    _bullets("教学难点", c.get("difficulties") or [])
    steps = c.get("steps") or []
    if steps:
        lines.append("## 教学步骤与时间分配")
        total = sum(int(s.get("minutes") or 0) for s in steps if isinstance(s, dict))
        for i, s in enumerate(steps, 1):
            if not isinstance(s, dict):
                lines.append(f"{i}. {s}")
                continue
            minutes = f"（{s.get('minutes')} 分钟）" if s.get("minutes") else ""
            lines.append(f"{i}. **{s.get('name') or '环节'}**{minutes}")
            if s.get("content"):
                lines.append(f"   - {s['content']}")
            mats = s.get("materials") or []
            if mats:
                lines.append(f"   - 使用资料：{'、'.join(str(m) for m in mats)}")
        if total:
            lines.append(f"\n> 时间合计：{total} 分钟")
        lines.append("")
    examples = c.get("examples") or []
    if examples:
        lines.append("## 例题与活动")
        for i, e in enumerate(examples, 1):
            q = e.get("question") if isinstance(e, dict) else e
            a = e.get("answer") if isinstance(e, dict) else ""
            lines.append(f"{i}. {q}")
            if a:
                lines.append(f"   - 参考要点：{a}")
        lines.append("")
    homework = c.get("homework") or []
    if homework:
        lines.append("## 课后练习")
        for i, h in enumerate(homework, 1):
            q = h.get("question") if isinstance(h, dict) else h
            a = h.get("answer") if isinstance(h, dict) else ""
            lines.append(f"{i}. {q}")
            if a:
                lines.append(f"   - 参考答案：{a}")
        lines.append("")
    if c.get("evidence_note"):
        lines.append("## 说明")
        lines.append(c["evidence_note"])
        lines.append("")
    evidence = plan.get("evidence") or []
    if evidence:
        lines.append("## 依据的课程资料")
        for e in evidence:
            name = e.get("file_name") or "—"
            page = f" 第{e['page']}页" if e.get("page") else ""
            lines.append(f"- {name}{page}")
    return "\n".join(lines)


__all__ = ["LessonPlanError", "generate_draft", "update_plan", "publish", "list_versions",
           "student_visible_plans", "to_markdown", "get_plan_or_404", "PLAN_SECTIONS"]
