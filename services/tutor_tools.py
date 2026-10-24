"""
统一 Agent 的工具注册表与执行器（2026-10-07 统一伴学改造）

设计（参考 DeepTutor runtime/registry/tool_registry.py 与 tool_composition.py）：
- **工具**：读资料 / 读学习状态 / 读地图 / 生成讲解 / 出题 / 存成果 / 安排复习 / 计划。
  模型通过原生 function calling 决定调用哪些、按什么顺序。
- **能力（capability）**：只决定「系统提示词 + 可用工具子集 + 默认动作」，
  不在程序里写死固定步骤（上一轮的 plan_steps 是固定状态机，本轮改为模型决策）。
- **权威规则**：权限、判分、掌握度、预算、截止日期、确认与审计全部留在程序里；
  工具只接受程序注入的 user_id/subject_id，模型无法指定别的用户或绕过范围。

执行器会做：参数校验、写操作保护、异常转成结构化错误、耗时与摘要记录。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from services import tutor_session
from utils.logger import logger


@dataclass
class ToolContext:
    """一次工具调用的上下文（身份与范围由程序给定）。"""
    user_id: str
    subject_id: str
    role: str = "student"
    ability: str = tutor_session.ABILITY_CHAT
    scope: Dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None
    conversation_id: Optional[str] = None
    node_id: Optional[str] = None
    # 本轮的 id：出题时要写进题目，删掉这条历史时它的题要跟着消失
    turn_id: Optional[str] = None
    sources: List[Dict[str, Any]] = field(default_factory=list)   # 本轮检索到的真实片段
    artifacts: List[Dict[str, Any]] = field(default_factory=list)  # 本轮生成的成果
    events: List[Dict[str, Any]] = field(default_factory=list)     # 需要前端展示的中间结果
    _source_index: Dict[Any, Dict[str, Any]] = field(default_factory=dict)

    def record_sources(self, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        登记检索片段并分配**本轮全局引用编号**（1..N）。

        多次检索会返回各自从 1 开始的局部编号，直接透给模型会导致 [1] 指向不同片段；
        这里统一重编号，并把重编号后的结果回传给模型，保证「回答里的 [n]」= 前端能点开的原文。
        """
        out: List[Dict[str, Any]] = []
        for src in sources or []:
            key = (src.get("file_name"), src.get("page"), (src.get("snippet") or "")[:80])
            hit = self._source_index.get(key)
            if hit is None:
                enriched = dict(src)
                enriched["id"] = len(self.sources) + 1
                self.sources.append(enriched)
                self._source_index[key] = enriched
                out.append(enriched)
            else:
                out.append(hit)
        return out

    def record_artifact(self, artifact: Dict[str, Any]) -> None:
        if artifact and artifact.get("id"):
            self.artifacts.append(artifact)


@dataclass
class ToolSpec:
    name: str
    label: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Dict[str, Any]]
    uses_llm: bool = False
    writes: bool = False
    abilities: Tuple[str, ...] = ()


def _schema(props: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


# =====================================================================
# 工具实现
# =====================================================================

def _t_search_materials(ctx: ToolContext, query: str, top_k: int = 5) -> Dict[str, Any]:
    from services import ai_service
    top_k = max(1, min(int(top_k or 5), 10))
    result = ai_service.retrieve_and_build_context_scoped(
        query, ctx.subject_id, top_k=top_k, viewer_user_id=ctx.user_id, scope=ctx.scope)
    cited = ctx.record_sources(result.get("sources") or [])
    docs = result.get("documents") or []
    return {
        "context_used": bool(result.get("context_used")),
        "evidence_count": len(docs),
        "sources": cited[:8],
        "snippet": (result.get("context") or "")[:1500],
        "scope_label": (ctx.scope or {}).get("label"),
        "supplemented": result.get("supplemented", 0),
        "note": ("已在所选资料范围内检索" if (ctx.scope or {}).get("mode") == "selected"
                 else "已在本学科全部资料范围内检索"),
    }


def _t_search_pages(ctx: ToolContext, query: str, top_k: int = 5) -> Dict[str, Any]:
    """按页检索：图片型课件/扫描件的兜底；没有索引时如实说明。"""
    from services import page_index
    top_k = max(1, min(int(top_k or 5), 10))
    pages = page_index.search_pages(ctx.subject_id, query, top_k)
    if not pages:
        status = page_index.get_page_index_status(ctx.subject_id)
        note = ("该学科还没有页面索引，可先在资料库点「建立页面索引」"
                if not status.get("pages") else "页面索引里没有命中这一页")
        return {"ok": True, "page_count": 0, "sources": [], "indexed_pages":
                status.get("pages", 0), "note": note}
    sources = ctx.record_sources([
        {"file_name": p["file_name"], "page": p["page"], "type": "page",
         "snippet": p["snippet"], "page_image_url": p["page_image_url"],
         "doc_id": p["doc_id"], "bbox": p.get("bbox"),
         "image_width": p.get("image_width"), "image_height": p.get("image_height")}
        for p in pages
    ])
    return {
        "ok": True,
        "page_count": len(pages),
        "sources": sources,
        "note": "以下结果按页返回；页面图可点开核对原图与版式。",
    }


def _t_get_learning_state(ctx: ToolContext) -> Dict[str, Any]:
    from services.agent_runtime import _tool_get_learning_state
    return _tool_get_learning_state(ctx.user_id, ctx.subject_id)


def _t_get_knowledge_map(ctx: ToolContext) -> Dict[str, Any]:
    from services import knowledge_map
    data = knowledge_map.map_with_progress(ctx.user_id, ctx.subject_id)
    if not data:
        return {"exists": False, "note": "还没有学习地图，可以调用 generate_knowledge_map 生成"}
    return {
        "exists": True,
        "map_id": data.get("map_id"),
        "title": data.get("title"),
        "node_count": len(data.get("nodes") or []),
        "progress": data.get("progress_summary"),
        "uncovered": data.get("uncovered") or [],
        "next_node": data.get("next_node"),
        "nodes": [{"node_id": n["node_id"], "label": n["label"], "level": n["level"],
                   "status": n["status"], "parent_id": n.get("parent_id")}
                  for n in (data.get("nodes") or [])[:40]],
    }


def _t_get_node(ctx: ToolContext, node_id: str) -> Dict[str, Any]:
    from services import knowledge_map
    node = knowledge_map.get_node(ctx.subject_id, node_id)
    if not node:
        return {"found": False, "error": "节点不存在（可能地图已重建），请重新获取地图"}
    progress = knowledge_map.list_progress(ctx.user_id, ctx.subject_id)
    return {
        "found": True,
        "node_id": node["node_id"],
        "label": node["label"],
        "level": node.get("level"),
        "type": node.get("type"),
        "objective": node.get("objective"),
        "source_docs": node.get("source_docs") or [],
        "coverage_note": node.get("coverage_note"),
        "status": progress.get(node["node_id"], {}).get("status", "new"),
    }


def _t_generate_knowledge_map(ctx: ToolContext, force: bool = False) -> Dict[str, Any]:
    from services import knowledge_map
    from services import subject_service
    try:
        subject = subject_service.get_subject(ctx.subject_id) or {}
    except Exception:
        subject = {}
    data = knowledge_map.generate_map(ctx.user_id, ctx.subject_id, ctx.scope,
                                      subject_name=subject.get("name") or "",
                                      model=ctx.model, force=bool(force))
    ctx.record_artifact({"id": f"map:{data.get('map_id')}", "kind": "mindmap",
                         "title": data.get("title"), "map_id": data.get("map_id")})
    return {
        "map_id": data.get("map_id"),
        "title": data.get("title"),
        "node_count": len(data.get("nodes") or []),
        "uncovered": data.get("uncovered") or [],
        "diagnostics": data.get("diagnostics") or [],
        "reused": data.get("reused"),
        "next_node": (knowledge_map.map_with_progress(ctx.user_id, ctx.subject_id) or {}).get("next_node"),
    }


def _t_generate_node_lesson(ctx: ToolContext, node_id: Optional[str] = None,
                            focus: Optional[str] = None) -> Dict[str, Any]:
    from services import knowledge_map, note_service, artifacts_service
    node = None
    if node_id:
        node = knowledge_map.get_node(ctx.subject_id, node_id)
    topic = (node or {}).get("label") or ctx.node_id or focus
    if not topic:
        return {"ok": False, "error": "缺少知识点主题"}
    if focus:
        topic = f"{topic}（重点讲：{focus}）"
    note = note_service.generate_smart_note(
        subject_id=ctx.subject_id, topic=topic, model=ctx.model,
        user_id=ctx.user_id, scope=ctx.scope)
    ctx.record_sources([s for m in ("concept", "example", "mistakes", "class_tips")
                        for s in ((note.get(m) or {}).get("sources") or [])])
    title = note.get("title") or f"{topic} 讲解"
    artifact = artifacts_service.save_generated(
        ctx.user_id, ctx.subject_id, artifacts_service.KIND_NOTE, title,
        {"note": note}, node_id=node_id or ctx.node_id,
        source_scope={"signature": (ctx.scope or {}).get("signature"),
                      "label": (ctx.scope or {}).get("label")},
        source_signature=(ctx.scope or {}).get("signature"))
    ctx.record_artifact(artifact)
    coverage = scope_covers_node(ctx.scope, node) if node else {"checked": False, "covered": True}
    if node_id and coverage.get("covered"):
        _set_node_status(ctx, node_id, knowledge_map.STATUS_LEARNING)
    return {
        "ok": True,
        "title": title,
        "scope_warning": None if coverage.get("covered") else (
            "当前所选资料不包含该知识点的来源资料（" + "、".join(coverage.get("missing") or [])
            + "），讲解内容可能不是这个知识点的内容，请如实告知学生并建议把来源资料加入范围。"),
        "scope_covered": coverage.get("covered"),
        "context_used": note.get("context_used"),
        "evidence_count": note.get("evidence_count"),
        "artifact_id": artifact.get("id"),
        "concept": ((note.get("concept") or {}).get("content") or "")[:600],
        "example": ((note.get("example") or {}).get("content") or "")[:400],
        "mistakes": ((note.get("mistakes") or {}).get("content") or "")[:400],
        "note": "讲解内容已保存到学习成果，可继续出练习或更换例子",
    }


def _t_generate_practice(ctx: ToolContext, topic: Optional[str] = None,
                         count: int = 3, node_id: Optional[str] = None) -> Dict[str, Any]:
    from services import knowledge_map, note_service
    count = max(1, min(int(count or 3), 5))
    node = None
    if node_id:
        node = knowledge_map.get_node(ctx.subject_id, node_id)
    topic = topic or (node or {}).get("label") or ctx.node_id
    if not topic:
        return {"ok": False, "error": "缺少练习主题"}
    quiz = note_service.generate_quiz(subject_id=ctx.subject_id, topic=topic,
                                      model=ctx.model, count=count,
                                      user_id=ctx.user_id, scope=ctx.scope,
                                      turn_id=ctx.turn_id)
    items = quiz.get("quiz") or []
    coverage = scope_covers_node(ctx.scope, node) if node else {"checked": False, "covered": True}
    if node_id and coverage.get("covered"):
        _set_node_status(ctx, node_id, knowledge_map.STATUS_PRACTICING)
    return {
        "ok": True,
        "topic": topic,
        "scope_warning": None if coverage.get("covered") else (
            "当前所选资料不包含该知识点的来源资料（" + "、".join(coverage.get("missing") or [])
            + "），这些题目并非该知识点的内容。必须如实告诉学生：题目来自当前所选资料，"
            "不是这个知识点的检测；并建议把来源资料加入范围后重出。"),
        "scope_covered": coverage.get("covered"),
        "question_count": len(items),
        "questions": [{"id": q.get("id"), "question": q.get("question"),
                       "type": q.get("type"), "options": q.get("options"),
                       "knowledge_point": topic, "node_id": node_id}
                      for q in items],
        "source": quiz.get("source"),
        "evidence_count": quiz.get("evidence_count"),
        "note": "题目已入库，学生在练习面板作答后由后端判分（答案不下发给模型）",
    }


def scope_covers_node(scope: Dict[str, Any], node: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    当前资料范围是否覆盖该知识点的来源资料。

    为什么需要：节点来自地图，其来源资料是某几份文件；如果学生把范围切成了别的资料，
    继续"围绕该节点出题"就会用不相干的材料出题、却挂着这个知识点的名字——
    这是"答案看起来对但依据错位"的典型问题。这里如实检测，让模型/界面先说清楚。
    """
    if not node:
        return {"checked": False, "covered": True, "missing": []}
    docs = [d for d in (node.get("source_docs") or []) if d]
    if not docs:
        return {"checked": False, "covered": True, "missing": []}
    mode = (scope or {}).get("mode")
    if mode != "selected":
        return {"checked": True, "covered": True, "missing": []}
    names = {(m.get("file_name") or "") for m in (scope.get("selected") or [])}
    missing = [d for d in docs if d not in names]
    return {"checked": True, "covered": not missing, "missing": missing,
            "node_docs": docs, "scope_label": (scope or {}).get("label")}


def _set_node_status(ctx: ToolContext, node_id: str, status: str) -> None:
    try:
        from services import knowledge_map
        knowledge_map.set_node_status(ctx.user_id, ctx.subject_id, node_id, status)
    except Exception as e:
        logger.warning(f"更新节点状态失败: {e}")


def _t_set_node_status(ctx: ToolContext, node_id: str, status: str) -> Dict[str, Any]:
    from services import knowledge_map
    res = knowledge_map.set_node_status(ctx.user_id, ctx.subject_id, node_id, status)
    return {"ok": True, **res}


def _t_save_artifact(ctx: ToolContext, kind: str, title: str,
                     content: Dict[str, Any]) -> Dict[str, Any]:
    from services import artifacts_service
    if kind not in artifacts_service.KINDS:
        kind = artifacts_service.KIND_NOTE
    artifact = artifacts_service.save_generated(
        ctx.user_id, ctx.subject_id, kind, title, content or {},
        node_id=ctx.node_id,
        source_scope={"signature": (ctx.scope or {}).get("signature"),
                      "label": (ctx.scope or {}).get("label")},
        source_signature=(ctx.scope or {}).get("signature"),
        origin="agent")
    ctx.record_artifact(artifact)
    return {"ok": True, "artifact_id": artifact.get("id"), "title": artifact.get("title"),
            "kind": artifact.get("kind"), "version": artifact.get("version"),
            "note": "成果已保存，可在资料与成果区再次打开或继续编辑"}


def _t_list_artifacts(ctx: ToolContext, kind: Optional[str] = None) -> Dict[str, Any]:
    from services import artifacts_service
    items = artifacts_service.list_artifacts(
        ctx.user_id, ctx.subject_id, kind,
        current_signature=(ctx.scope or {}).get("signature"), limit=20)
    return {"count": len(items),
            "artifacts": [{"id": i["id"], "kind": i["kind"], "kind_label": i["kind_label"],
                           "title": i["title"], "updated_at": i["updated_at"],
                           "edited": i["edited"], "stale": i["stale"]} for i in items]}


def _t_schedule_review(ctx: ToolContext, knowledge_point: str,
                       reason: Optional[str] = None) -> Dict[str, Any]:
    from services import review_service
    from services.learning_data import ensure_knowledge_point
    kp_id = ensure_knowledge_point(ctx.subject_id, knowledge_point, source="tutor")
    if not kp_id:
        return {"ok": False, "error": "知识点名称无效"}
    res = review_service.ensure_review_task(
        user_id=ctx.user_id, subject_id=ctx.subject_id, knowledge_point_id=kp_id,
        knowledge_point_name=knowledge_point,
        reason=reason or "学习助手根据本次学习结果安排")
    return {"ok": True, **res}


def _t_analyze_budget(ctx: ToolContext, deadline: Optional[str] = None,
                      daily_minutes: Optional[int] = None) -> Dict[str, Any]:
    from services import plan_adjustment
    if deadline or daily_minutes is not None:
        plan_adjustment.set_constraints(ctx.user_id, ctx.subject_id,
                                        deadline=deadline,
                                        daily_minutes=daily_minutes)
    budget = plan_adjustment.analyze_plan_budget(ctx.user_id, ctx.subject_id)
    return {"fits": budget.get("fits"), "conflicts": budget.get("conflicts"),
            "suggestions": budget.get("suggestions"),
            "daily_minutes": budget.get("daily_minutes"),
            "deadline": budget.get("deadline"),
            "remaining_minutes": budget.get("remaining_minutes"),
            "capacity_minutes": budget.get("capacity_minutes")}


def _t_create_plan(ctx: ToolContext, goal: str) -> Dict[str, Any]:
    from services import learning_service
    plan = learning_service.generate_plan(subject_id=ctx.subject_id, goal=goal,
                                          model=ctx.model, user_id=ctx.user_id)
    return {"plan_id": plan.get("id"), "title": plan.get("title"),
            "status": plan.get("status"),
            "pending_confirmation": plan.get("status") == "DRAFT",
            "task_count": len(plan.get("tasks") or []),
            "budget": plan.get("budget"),
            "note": ("计划为草案，需学生确认调整后才生效"
                     if plan.get("status") == "DRAFT" else "计划已生成")}


def _t_propose_adjustment(ctx: ToolContext) -> Dict[str, Any]:
    from services import plan_adjustment
    key = f"tutor:{ctx.user_id}:{ctx.subject_id}"
    res = plan_adjustment.propose_schedule(ctx.user_id, ctx.subject_id,
                                           source="agent", idempotency_key=key)
    return res


def _t_explain_plan(ctx: ToolContext) -> Dict[str, Any]:
    from services.agent_runtime import _tool_explain_plan
    return _tool_explain_plan(ctx.user_id, ctx.subject_id)


def _t_ask_user(ctx: ToolContext, question: str,
                options: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"needs_user_input": True, "question": question,
            "options": options or [],
            "note": "需要学生确认或补充信息后再继续"}


# =====================================================================
# 注册表
# =====================================================================

_TOPIC_PROP = {"type": "string", "description": "知识点或主题名称"}
_NODE_PROP = {"type": "string", "description": "知识地图节点 ID（node_ 开头）"}

TOOLS: Dict[str, ToolSpec] = {
    "search_materials": ToolSpec(
        "search_materials", "正在查找资料",
        "在**有权访问且在当前资料范围内**的课程资料中检索证据片段，回答涉及资料内容时必须先调用它。",
        _schema({"query": {"type": "string", "description": "检索问题"},
                 "top_k": {"type": "integer", "description": "返回片段数，1-10"}}, ["query"]),
        _t_search_materials,
        abilities=(tutor_session.ABILITY_CHAT, tutor_session.ABILITY_EXPLAIN,
                   tutor_session.ABILITY_PRACTICE, tutor_session.ABILITY_MAP,
                   tutor_session.ABILITY_ORGANIZE)),
    "search_pages": ToolSpec(
        "search_pages", "正在按页查找资料",
        "在页面级索引中检索图片、图表、表格、版式或“第几页”的内容；"
        "返回页码、片段与页面图。没有索引时会如实说明。",
        _schema({"query": {"type": "string", "description": "要查找的页面内容"},
                 "top_k": {"type": "integer", "description": "返回页数，1-10"}}, ["query"]),
        _t_search_pages,
        abilities=(tutor_session.ABILITY_CHAT, tutor_session.ABILITY_EXPLAIN,
                   tutor_session.ABILITY_PRACTICE)),
    "get_learning_state": ToolSpec(
        "get_learning_state", "正在查看学习记录",
        "读取该学生本学科的真实学习状态：掌握度分布、薄弱点、错题、待复习、时间预算与学习偏好。",
        _schema({}), _t_get_learning_state,
        abilities=(tutor_session.ABILITY_CHAT, tutor_session.ABILITY_EXPLAIN,
                   tutor_session.ABILITY_PRACTICE, tutor_session.ABILITY_ORGANIZE)),
    "get_knowledge_map": ToolSpec(
        "get_knowledge_map", "正在读取知识地图",
        "读取本学科已有的学习地图（章节与知识点、学习状态、下一步建议）。",
        _schema({}), _t_get_knowledge_map,
        abilities=(tutor_session.ABILITY_CHAT, tutor_session.ABILITY_EXPLAIN,
                   tutor_session.ABILITY_MAP, tutor_session.ABILITY_ORGANIZE)),
    "get_node": ToolSpec(
        "get_node", "正在读取知识点",
        "读取地图中某个知识点的详情（目标、来源资料、学习状态）。",
        _schema({"node_id": _NODE_PROP}, ["node_id"]), _t_get_node,
        abilities=(tutor_session.ABILITY_CHAT, tutor_session.ABILITY_EXPLAIN,
                   tutor_session.ABILITY_PRACTICE, tutor_session.ABILITY_MAP)),
    "generate_knowledge_map": ToolSpec(
        "generate_knowledge_map", "正在生成知识地图",
        "根据当前资料范围生成（或重新生成）章节与知识点学习地图，会调用模型。",
        _schema({"force": {"type": "boolean", "description": "资料未变化时是否强制重算"}}),
        _t_generate_knowledge_map, uses_llm=True, writes=True,
        abilities=(tutor_session.ABILITY_MAP, tutor_session.ABILITY_ORGANIZE)),
    "generate_node_lesson": ToolSpec(
        "generate_node_lesson", "正在生成讲解",
        "为某个知识点生成讲解（概括、重点、例子、易错点），引用节点资料并保存为成果。",
        _schema({"node_id": _NODE_PROP, "focus": {"type": "string", "description": "学生特别要求，如更基础、换个例子"}}),
        _t_generate_node_lesson, uses_llm=True, writes=True,
        abilities=(tutor_session.ABILITY_EXPLAIN, tutor_session.ABILITY_MAP,
                   tutor_session.ABILITY_CHAT, tutor_session.ABILITY_ORGANIZE)),
    "generate_practice": ToolSpec(
        "generate_practice", "正在生成练习",
        "围绕知识点或主题生成练习与检测题（答案由后端保存，学生作答后判分）。",
        _schema({"topic": _TOPIC_PROP, "node_id": _NODE_PROP,
                 "count": {"type": "integer", "description": "题量 1-5"}}),
        _t_generate_practice, uses_llm=True, writes=True,
        abilities=(tutor_session.ABILITY_PRACTICE, tutor_session.ABILITY_EXPLAIN,
                   tutor_session.ABILITY_CHAT)),
    "set_node_status": ToolSpec(
        "set_node_status", "正在更新学习状态",
        "更新知识点的学习状态。status 只能是 new/learning/practicing/passed/needs_review；"
        "passed 只能在学生独立通过检测后由程序判定，模型不得为了好看而设置。",
        _schema({"node_id": _NODE_PROP,
                 "status": {"type": "string",
                            "enum": ["new", "learning", "practicing", "passed",
                                     "needs_review"]}},
                ["node_id", "status"]),
        _t_set_node_status, writes=True,
        abilities=(tutor_session.ABILITY_PRACTICE, tutor_session.ABILITY_MAP,
                   tutor_session.ABILITY_EXPLAIN)),
    "save_artifact": ToolSpec(
        "save_artifact", "正在保存成果",
        "把笔记、提纲、学习计划、复习卡片等保存为学习成果（保留用户、学科、资料范围与引用）。",
        _schema({"kind": {"type": "string",
                          "enum": ["note", "mindmap", "outline", "plan", "practice_set", "review_cards"]},
                 "title": {"type": "string"},
                 "content": {"type": "object", "description": "结构化内容"}},
                ["kind", "title", "content"]),
        _t_save_artifact, writes=True,
        abilities=(tutor_session.ABILITY_ORGANIZE, tutor_session.ABILITY_EXPLAIN,
                   tutor_session.ABILITY_CHAT, tutor_session.ABILITY_MAP)),
    "list_artifacts": ToolSpec(
        "list_artifacts", "正在整理已有成果",
        "列出该学生本学科已保存的学习成果（含是否需要更新）。",
        _schema({"kind": {"type": "string"}}), _t_list_artifacts,
        abilities=(tutor_session.ABILITY_ORGANIZE, tutor_session.ABILITY_CHAT,
                   tutor_session.ABILITY_EXPLAIN)),
    "schedule_review": ToolSpec(
        "schedule_review", "正在安排复习",
        "为某个知识点安排到期复习（复习调度由程序决定，模型只提出对象）。",
        _schema({"knowledge_point": _TOPIC_PROP, "reason": {"type": "string"}},
                ["knowledge_point"]),
        _t_schedule_review, writes=True,
        abilities=(tutor_session.ABILITY_PRACTICE, tutor_session.ABILITY_EXPLAIN,
                   tutor_session.ABILITY_ORGANIZE)),
    "analyze_budget": ToolSpec(
        "analyze_budget", "正在检查时间预算",
        "设置或读取可用的每日学习时间与截止日期，并检查时间预算是否足够。",
        _schema({"deadline": {"type": "string", "description": "YYYY-MM-DD"},
                 "daily_minutes": {"type": "integer"}}),
        _t_analyze_budget, writes=True,
        abilities=(tutor_session.ABILITY_ORGANIZE, tutor_session.ABILITY_CHAT)),
    "create_plan": ToolSpec(
        "create_plan", "正在生成学习计划",
        "按学习目标生成学习计划；超出预算时为草案，需学生确认后才生效。",
        _schema({"goal": {"type": "string"}}, ["goal"]),
        _t_create_plan, uses_llm=True, writes=True,
        abilities=(tutor_session.ABILITY_ORGANIZE, tutor_session.ABILITY_CHAT)),
    "propose_adjustment": ToolSpec(
        "propose_adjustment", "正在准备调整方案",
        "当预算不足或有逾期任务时，提出计划调整方案（仅提出，不直接改数据，需学生确认）。",
        _schema({}), _t_propose_adjustment, writes=True,
        abilities=(tutor_session.ABILITY_ORGANIZE,)),
    "explain_plan": ToolSpec(
        "explain_plan", "正在整理安排依据",
        "读取当前计划与约束，用于向学生解释为什么这样安排。",
        _schema({}), _t_explain_plan,
        abilities=(tutor_session.ABILITY_ORGANIZE, tutor_session.ABILITY_CHAT)),
    "ask_user": ToolSpec(
        "ask_user", "需要你的确认",
        "当问题有歧义、缺少必要信息，或需要学生确认重大操作时，用它可以先问清楚再继续。",
        _schema({"question": {"type": "string"},
                 "options": {"type": "array", "items": {"type": "string"}}},
                ["question"]),
        _t_ask_user,
        abilities=(tutor_session.ABILITY_CHAT, tutor_session.ABILITY_EXPLAIN,
                   tutor_session.ABILITY_PRACTICE, tutor_session.ABILITY_MAP,
                   tutor_session.ABILITY_ORGANIZE)),
}


def tools_for_ability(ability: str, role: str = "student") -> List[Dict[str, Any]]:
    """组合可用工具（纯函数，按能力过滤）。"""
    if ability not in tutor_session.ABILITIES:
        ability = tutor_session.ABILITY_CHAT
    schemas: List[Dict[str, Any]] = []
    names: List[str] = []
    for name, spec in TOOLS.items():
        if ability not in spec.abilities:
            continue
        names.append(name)
        schemas.append({
            "type": "function",
            "function": {"name": spec.name, "description": spec.description,
                         "parameters": spec.parameters},
        })
    logger.debug(f"能力 {ability}（{role}）可用工具: {names}")
    return schemas


def tool_label(name: str) -> str:
    spec = TOOLS.get(name)
    return spec.label if spec else name


def tool_uses_llm(name: str) -> bool:
    spec = TOOLS.get(name)
    return bool(spec and spec.uses_llm)


def execute_tool(name: str, args: Dict[str, Any],
                 ctx: ToolContext) -> Dict[str, Any]:
    """
    执行一次工具调用。**权威校验在这里**：
    - 工具必须存在且在能力允许集合内；
    - user_id / subject_id / scope 一律来自 ctx，忽略模型传入的同名字段；
    - 异常转成结构化错误，Agent 可以继续（不会中断整轮）。
    """
    spec = TOOLS.get(name)
    if not spec:
        return {"ok": False, "error": f"未知工具: {name}", "error_type": "unknown_tool"}
    if ctx.ability not in spec.abilities:
        return {"ok": False, "error": f"当前能力不允许调用 {name}", "error_type": "forbidden_tool"}
    clean = {k: v for k, v in (args or {}).items()
             if k not in ("user_id", "subject_id", "scope", "role")}
    started = time.time()
    try:
        out = spec.handler(ctx, **clean)
        out = out if isinstance(out, dict) else {"result": out}
        out.setdefault("ok", True)
        out["_tool"] = name
        out["_elapsed_ms"] = int((time.time() - started) * 1000)
        return out
    except TypeError as e:
        # 参数不匹配：明确告知模型重试，不吞掉
        logger.warning(f"工具 {name} 参数错误: {e}")
        return {"ok": False, "error": f"参数不正确: {e}", "error_type": "bad_arguments",
                "_tool": name, "_elapsed_ms": int((time.time() - started) * 1000)}
    except Exception as e:
        logger.error(f"工具 {name} 执行失败: {type(e).__name__}: {e}", exc_info=True)
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "error_type": "tool_error",
                "_tool": name, "_elapsed_ms": int((time.time() - started) * 1000)}


__all__ = ["ToolContext", "ToolSpec", "TOOLS", "execute_tool", "tool_label",
           "tool_uses_llm", "tools_for_ability"]
