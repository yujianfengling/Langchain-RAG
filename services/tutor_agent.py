"""
统一伴学 Agent 运行机制（2026-10-07 统一伴学改造）

一句话：**模型读取上下文 → 决定回答或调用工具 → 程序执行工具 → 结果写回本轮 →
模型继续处理 → 给出回答或学习活动**。

与上一轮的区别：上一轮是程序写死的确定性状态机（plan_steps），无论问什么都是同一套
工具序列；本轮改为**模型通过原生 function calling 自己决定**是否检索、读学习状态、
生成练习、整理笔记或先澄清。程序只负责：

- 注入身份与资料范围（模型不能指定别的用户 / 越出范围）；
- 限额（最大轮数、最大工具次数、最长耗时）；
- 权限、判分、掌握度、预算、截止日期等权威规则（都在被调用的业务服务里）；
- 事件落库与重放（取消、断线、刷新后仍可恢复）。

流式事件协议（与 api/routes/tutor.py 的 SSE 对齐）：
    start / context / stage / tool_call / tool_result / sources / delta /
    artifact / notice / done / error
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterator, List, Optional

from services import material_scope, tutor_session, tutor_tools
from utils.logger import logger

MAX_ROUNDS = 6          # 最多模型轮次
MAX_TOOL_CALLS = 12     # 最多工具调用次数
MAX_SECONDS = 180       # 单轮总耗时上限
HISTORY_TURNS = 6       # 带多少轮历史给模型

_COMMON_RULES = """
## 硬性规则（必须遵守）
1. 涉及课程内容、概念、例题、作业时，先调用 search_materials 检索资料，再作答；
   不许凭记忆编造资料里没有的内容。
2. 引用格式：[1] [2] 对应检索结果里的 id。**只标注真正支持该结论的片段**，
   不要把所有检索结果都列成依据；没有用到就写“资料未覆盖”。
   **只能用本次检索结果里真实存在的 id**；资料原文自带的参考文献编号（如 [6]）
   照抄过来会指向不存在的片段，必须去掉。
3. 不许编造页码、章节号、分数、成绩、教师已确定的考点或学生数据。
4. 判定“掌握 / 通过检测”必须由程序给出（学生独立作答后的结果），你不能自行宣布。
5. 需要澄清或重大操作确认时，调用 ask_user，不要猜测学生意图后直接改数据。
6. 用中文回答；面向学生时语言朴素具体，先给结论再给依据，不要输出内部 ID 或调试信息。
7. 工具结果里如果出现 scope_warning，必须如实转告学生（当前资料范围不覆盖该知识点），
   不得忽略、也不得把它包装成"这个知识点的练习/讲解"。
8. 数学公式用 LaTeX：行内写 $...$、独立成行写 $$...$$（界面会渲染成公式）。
   但**不要整段堆公式**——每个公式前后都要用中文说清"在算什么、符号什么含义"，
   结果带单位；能一句话讲明白的就不上公式。
   讲例题要给"已知 → 求 → 思路 → 代入 → 结论"的完整脉络，只丢算式等于没讲。
"""

_ABILITY_PROMPTS: Dict[str, str] = {
    tutor_session.ABILITY_CHAT: """
## 当前能力：资料问答与深入追问
按学生的问题直接回答，需要时先检索资料。回答结构：结论 → 依据（带引用）→ 可选的延伸追问。
如果资料没有覆盖，明确说明“当前资料未覆盖”，可以补充通用知识但必须单独标注其为通用知识。
""",
    tutor_session.ABILITY_EXPLAIN: """
## 当前能力：引导学习与知识点讲解
围绕学生当前知识点做分步讲解：先给出概括，再讲重点与例子，最后提示易错点。
学生说“没理解 / 换个例子 / 更基础一点”时，用更小的步子重讲，不要重复原话。
**当学生要求讲解某个具体知识点时，先调用 get_node 或 search_materials 取依据，
再调用 generate_node_lesson 生成结构化讲解并保存为学习成果**（这样学生才能在“学习成果”里再次打开）。
讲解完成后，用一两句话总结，并给出下一步（练习/检测/下一节点）。
""",
    tutor_session.ABILITY_PRACTICE: """
## 当前能力：练习与检测
先用 generate_practice 生成围绕当前知识点的题目（答案由后端保存，**你不会拿到答案**）。
出题后**只用一两句话说明"出了几道题、考的是什么"并请学生作答**；
**不要复述题干、选项、答案或解析**——题目会以可交互的卡片直接呈现在学生面前，
你这里再抄一遍，学生就会看到两份一模一样的题（实测确认过，界面会重复得很难看）。
同理，也不要再罗列"第 1 题 / 第 2 题"这样的清单。
学生提交作答后，由后端判分并给出结果；你负责解释结果与下一步安排。
""",
    tutor_session.ABILITY_MAP: """
## 当前能力：思维导图与知识地图
学生要学习路线/思维导图时，调用 generate_knowledge_map 生成（资料未变化会复用已有地图）。
生成后简要说明：覆盖了哪些资料、哪些资料没有被整理成节点。点击节点的学习在节点面板完成。
""",
    tutor_session.ABILITY_ORGANIZE: """
## 当前能力：学习整理（笔记、提纲、学习安排）
把当前学习内容整理成可复用的成果：笔记 / 提纲 / 学习计划 / 复习卡片。
用 save_artifact 保存（成果会带上资料范围与引用）。安排学习时先读 get_learning_state
了解真实薄弱点、待复习与时间预算，再给建议；超预算时说明是草案，需学生确认。
""",
}


def build_system_prompt(subject_name: str, ability: str, scope: Dict[str, Any],
                        context_state: Dict[str, Any],
                        node: Optional[Dict[str, Any]] = None) -> str:
    role = context_state.get("role") or "student"
    role_text = "教师" if role in ("teacher", "admin") else "学生"
    lines = [
        f"你是「{subject_name or '本课程'}」的智慧学习助手，正在服务一位{role_text}。",
        f"当前学科 ID：{context_state.get('subject_id')}",
        f"当前资料范围：{scope.get('label')}（范围由系统确定，你无法更改）",
    ]
    if scope.get("mode") == "selected":
        names = "、".join(m.get("file_name", "") for m in (scope.get("selected") or [])[:8])
        lines.append(f"范围包含：{names}")
    if scope.get("invalid"):
        lines.append(f"注意：有 {len(scope['invalid'])} 项已选资料不可用（已删除或失去权限），"
                     "如学生问起要如实说明。")
    if node:
        lines.append(f"学生当前学习的知识点：{node.get('label')}（node_id={node.get('node_id')}）")
        lines.append(f"该知识点来源资料：{'、'.join(node.get('source_docs') or []) or '未标注'}")
    lines.append(_ABILITY_PROMPTS.get(ability, _ABILITY_PROMPTS[tutor_session.ABILITY_CHAT]))
    lines.append(_COMMON_RULES)
    return "\n".join(lines)


def _recent_history(user_id: str, subject_id: str, conversation_id: Optional[str],
                    exclude_turn_id: Optional[str] = None) -> List[Dict[str, str]]:
    """从持久化轮次恢复对话上下文（刷新/重启后依然连续）。"""
    if not conversation_id:
        return []
    turns = tutor_session.list_turns(user_id, subject_id, limit=30)
    picked: List[Dict[str, str]] = []
    for turn in reversed(turns):
        if turn.get("conversation_id") != conversation_id:
            continue
        if exclude_turn_id and turn.get("id") == exclude_turn_id:
            continue
        if turn.get("status") != tutor_session.STATE_SUCCEEDED or not turn.get("answer"):
            continue
        picked.append({"role": "user", "content": turn.get("user_message") or ""})
        picked.append({"role": "assistant", "content": turn.get("answer") or ""})
        if len(picked) >= HISTORY_TURNS * 2:
            break
    picked.reverse()
    # 反转后要保证 user/assistant 成对且从 user 开始
    while picked and picked[0]["role"] != "user":
        picked.pop(0)
    return picked


def _cited_ids(answer: str, sources: List[Dict[str, Any]]) -> List[int]:
    ids = set()
    for match in re.findall(r"\[(\d{1,2})\]", answer or ""):
        idx = int(match)
        if 1 <= idx <= len(sources):
            ids.add(idx)
    return sorted(ids)


def _summarize_tool_result(name: str, result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return str(result)[:120]
    if not result.get("ok", True):
        return f"失败：{result.get('error', '未知错误')}"
    if name == "search_materials":
        return f"检索到 {result.get('evidence_count', 0)} 条片段" + (
            "" if result.get("context_used") else "（未命中）")
    if name == "generate_practice":
        return f"生成 {result.get('question_count', 0)} 道题（答案不下发）"
    if name == "generate_node_lesson":
        return f"生成讲解《{result.get('title')}》"
    if name == "generate_knowledge_map":
        return (f"地图含 {result.get('node_count', 0)} 个节点"
                + (f"，{len(result.get('uncovered') or [])} 份资料未覆盖"
                   if result.get("uncovered") else ""))
    if name == "get_learning_state":
        mastery = result.get("mastery") or {}
        dist = mastery.get("distribution") or {}
        return (f"知识点 {sum(dist.values())} 个，错题 {result.get('wrong_count', 0)} 道，"
                f"待复习 {len(result.get('pending_reviews') or [])} 项")
    if name == "save_artifact":
        return f"已保存成果《{result.get('title')}》"
    if name == "schedule_review":
        return f"已安排复习（到期 {result.get('due_date')}）"
    if name == "create_plan":
        return f"生成计划《{result.get('title')}》"
    if name == "ask_user":
        return "等待学生确认"
    return "已完成"


def _message_type_from_trace(trace: List[Dict[str, Any]]) -> Dict[str, Any]:
    """根据本轮真实发生的工具调用决定前端卡片类型（不假装调用了工具）。"""
    for step in reversed(trace):
        result = step.get("result") or {}
        if step.get("tool") == "generate_practice" and result.get("questions"):
            return {"message_type": "quiz", "payload": {"quiz": result["questions"],
                                                        "topic": result.get("topic")}}
        if step.get("tool") == "generate_node_lesson":
            return {"message_type": "note",
                    "payload": {"artifact_id": result.get("artifact_id"),
                                "title": result.get("title")}}
        if step.get("tool") == "generate_knowledge_map" and result.get("map_id"):
            return {"message_type": "mindmap", "payload": {"map_id": result["map_id"]}}
    return {"message_type": "chat", "payload": None}


def stream_turn(user_id: str, subject_id: str, message: str, *,
                ability: str = tutor_session.ABILITY_CHAT,
                selected_doc_ids: Optional[List[str]] = None,
                node_id: Optional[str] = None,
                conversation_id: Optional[str] = None,
                model: Optional[str] = None,
                role: str = "student",
                cancel_check: Optional[Any] = None) -> Iterator[Dict[str, Any]]:
    """
    执行一轮统一伴学，逐条产出事件。

    这是**同步生成器**：由接口层放到工作线程里，通过队列转为 SSE（与既有 chat_stream 一致）。
    """
    started = time.time()
    if ability not in tutor_session.ABILITIES:
        ability = tutor_session.ABILITY_CHAT

    scope = material_scope.build_scope(user_id, subject_id, selected_doc_ids)
    context_state = tutor_session.load_context(user_id, subject_id)
    context_state.update({"ability": ability, "role": role})
    if node_id:
        context_state["current_node_id"] = node_id
    if conversation_id:
        context_state["conversation_id"] = conversation_id
    context_state = tutor_session.save_context(user_id, subject_id, context_state)

    # 记录 node_id：历史记录要按知识点归集（点某知识点时右侧显示它以前讲过什么）
    turn_id = tutor_session.create_turn(user_id, subject_id, ability, message, conversation_id,
                                        node_id=node_id)
    context_state["last_turn_id"] = turn_id
    tutor_session.save_context(user_id, subject_id, context_state)

    def emit(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            payload = dict(payload)
            payload["turn_id"] = turn_id
            payload["type"] = event_type
            seq = tutor_session.append_event(turn_id, event_type, payload)
            payload["seq"] = seq
        except Exception as e:      # 事件落库失败不能中断回答
            logger.warning(f"写入轮次事件失败: {e}")
        return payload

    try:
        subject_name = ""
        try:
            from services import subject_service
            subject_name = (subject_service.get_subject(subject_id) or {}).get("name") or ""
        except Exception:
            pass

        node = None
        if node_id:
            try:
                from services import knowledge_map
                node = knowledge_map.get_node(subject_id, node_id)
            except Exception:
                node = None

        yield emit("start", {
            "subject_id": subject_id,
            "subject_name": subject_name,
            "ability": ability,
            "node_id": node_id,
            "conversation_id": conversation_id,
            "model": model,
            "scope": material_scope.scope_to_public(scope),
        })
        yield emit("context", {
            "scope": material_scope.scope_to_public(scope),
            "node": {"node_id": node.get("node_id"), "label": node.get("label")} if node else None,
            "ability": ability,
        })
        if scope.get("invalid"):
            yield emit("notice", {
                "level": "warning",
                "message": "有 " + str(len(scope["invalid"])) + " 项已选资料不可用，已从本轮范围中排除。",
                "invalid": scope["invalid"],
            })
        if scope.get("mode") == "empty":
            yield emit("notice", {"level": "warning",
                                  "message": "当前没有可用资料，回答将不引用课程资料。"})

        tool_ctx = tutor_tools.ToolContext(
            user_id=user_id, subject_id=subject_id, role=role, ability=ability,
            scope=scope, model=model, conversation_id=conversation_id, node_id=node_id,
            turn_id=turn_id)

        system_prompt = build_system_prompt(subject_name, ability, scope, context_state, node)
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(_recent_history(user_id, subject_id, conversation_id, turn_id))
        messages.append({"role": "user", "content": message})

        tools = tutor_tools.tools_for_ability(ability, role)
        answer_parts: List[str] = []
        trace: List[Dict[str, Any]] = []
        llm_calls = 0
        tool_calls_made = 0
        ask_user_payload: Optional[Dict[str, Any]] = None

        from services.model_service import get_model_provider
        provider = get_model_provider(model)

        fallback_reason = None
        for round_index in range(MAX_ROUNDS):
            if cancel_check and cancel_check():
                tutor_session.finish_turn(turn_id, tutor_session.STATE_CANCELLED,
                                          error="用户取消")
                yield emit("error", {"message": "已取消", "cancelled": True})
                return
            if tutor_session.is_cancelled(turn_id):
                tutor_session.finish_turn(turn_id, tutor_session.STATE_CANCELLED,
                                          error="用户取消")
                yield emit("error", {"message": "已取消", "cancelled": True})
                return
            if time.time() - started > MAX_SECONDS:
                yield emit("notice", {"level": "warning",
                                      "message": "本轮耗时已达上限，先给出已有结果。"})
                break
            if tool_calls_made >= MAX_TOOL_CALLS:
                yield emit("notice", {"level": "warning",
                                      "message": "本轮工具调用已达上限，先给出已有结果。"})
                break

            stage = "正在思考"
            if round_index == 0 and ability == tutor_session.ABILITY_CHAT:
                stage = "正在理解问题"
            yield emit("stage", {"stage": stage, "round": round_index + 1})

            try:
                stream = provider.stream_with_tools(
                    messages, tools=tools, temperature=0.3, max_tokens=2048)
                streamed_text = ""
                end_event: Dict[str, Any] = {}
                for ev in stream:
                    # 先累积文本；只有本轮流结束时没有 tool_calls，这段文本才是终答。
                    # 有 tool_calls 时它通常只是"我先去查资料"的前言，不作为回答展示。
                    if ev.get("type") == "content":
                        streamed_text += ev.get("text") or ""
                    elif ev.get("type") == "end":
                        end_event = ev
                llm_calls += 1
            except NotImplementedError:
                fallback_reason = "当前模型提供方不支持工具调用，已降级为直接检索回答"
                break
            except Exception as e:
                logger.error(f"模型调用失败: {type(e).__name__}: {e}", exc_info=True)
                if not trace and not answer_parts:
                    tutor_session.finish_turn(turn_id, tutor_session.STATE_FAILED,
                                              error=f"{type(e).__name__}: {e}")
                    yield emit("error", {"message": "模型暂时不可用，请稍后重试或换一个模型。"})
                    return
                break

            calls = end_event.get("tool_calls") or []
            if not calls:
                answer_parts.append(end_event.get("content") or streamed_text)
                break

            # 有工具调用：把模型这一轮的中间文本只当作"过程"，不当答案
            if streamed_text.strip():
                yield emit("stage", {"stage": "正在调用工具", "note": streamed_text.strip()[:200]})

            messages.append({"role": "assistant", "content": end_event.get("content") or "",
                             "tool_calls": calls})
            for call in calls:
                if tool_calls_made >= MAX_TOOL_CALLS:
                    break
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    import json as _json
                    args = _json.loads(raw_args) if raw_args.strip() else {}
                except ValueError:
                    args = {}
                tool_calls_made += 1
                yield emit("tool_call", {"tool": name, "label": tutor_tools.tool_label(name),
                                         "args": {k: v for k, v in (args or {}).items()
                                                  if isinstance(v, (str, int, float, bool))}})
                result = tutor_tools.execute_tool(name, args, tool_ctx)
                summary = _summarize_tool_result(name, result)
                yield emit("tool_result", {"tool": name, "label": tutor_tools.tool_label(name),
                                           "ok": bool(result.get("ok", True)),
                                           "summary": summary,
                                           "error": result.get("error")})
                trace.append({"tool": name, "args": args, "result": result, "summary": summary})
                if result.get("sources"):
                    yield emit("sources", {"sources": result["sources"]})
                if name == "ask_user":
                    ask_user_payload = {"question": result.get("question"),
                                        "options": result.get("options") or []}
                payload_for_model = {k: v for k, v in result.items() if not k.startswith("_")}
                messages.append({"role": "tool", "tool_call_id": call.get("id") or name,
                                 "content": _json_dumps(payload_for_model)})

        if fallback_reason:
            yield emit("notice", {"level": "info", "message": fallback_reason})
            yield from _fallback_answer(provider, messages, tool_ctx, answer_parts, emit)

        answer = "".join(answer_parts).strip()
        if not answer and not ask_user_payload:
            answer = ("我已经读取了你的学习记录和资料，但这一轮没有生成有效回答。"
                      "可以换个更具体的问法，或点下方建议继续。")

        cited = _cited_ids(answer, tool_ctx.sources)
        message_info = _message_type_from_trace(trace)
        for artifact in tool_ctx.artifacts:
            yield emit("artifact", {"artifact": artifact})

        duration = round(time.time() - started, 2)
        tutor_session.finish_turn(turn_id, tutor_session.STATE_SUCCEEDED, answer=answer,
                                  steps=tool_calls_made, llm_calls=llm_calls)
        yield emit("done", {
            "answer": answer,
            "sources": tool_ctx.sources,
            "cited_source_ids": cited,
            "artifacts": tool_ctx.artifacts,
            "tool_trace": [{"tool": t["tool"], "label": tutor_tools.tool_label(t["tool"]),
                            "summary": t["summary"], "ok": bool(t["result"].get("ok", True))}
                           for t in trace],
            "message_type": message_info["message_type"],
            "payload": message_info["payload"],
            "ask_user": ask_user_payload,
            "scope": material_scope.scope_to_public(scope),
            "elapsed": duration,
            "llm_calls": llm_calls,
            "tool_calls": tool_calls_made,
        })
    except GeneratorExit:
        # 客户端断开：保留已落库的事件与轮次状态，便于刷新后重连
        tutor_session.finish_turn(turn_id, tutor_session.STATE_CANCELLED, error="连接中断")
        raise
    except Exception as e:
        logger.error(f"统一伴学运行异常: {type(e).__name__}: {e}", exc_info=True)
        tutor_session.finish_turn(turn_id, tutor_session.STATE_FAILED,
                                  error=f"{type(e).__name__}: {e}")
        yield emit("error", {"message": f"运行失败：{type(e).__name__}"})


def _json_dumps(value: Any) -> str:
    import json as _json
    try:
        return _json.dumps(value, ensure_ascii=False)[:6000]
    except (TypeError, ValueError):
        return str(value)[:6000]


def _fallback_answer(provider, messages: List[Dict[str, Any]], tool_ctx: tutor_tools.ToolContext,
                     answer_parts: List[str], emit) -> Iterator[Dict[str, Any]]:
    """不支持工具调用时的降级：仍然走真实 RAG 检索 + 流式回答（不假装调用工具）。"""
    question = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            question = msg.get("content") or ""
            break
    tool_ctx.scope = tool_ctx.scope or {}
    result = tutor_tools.execute_tool("search_materials", {"query": question, "top_k": 6},
                                      tool_ctx)
    if result.get("sources"):
        yield {"type": "sources", "sources": result["sources"]}
    context = result.get("snippet") or ""
    system = messages[0]["content"] if messages else ""
    user_prompt = (f"学生问题：{question}\n\n"
                   f"课程资料片段：\n{context or '（本轮没有检索到资料）'}\n\n"
                   "请依据片段回答，并用 [序号] 标注引用；资料未覆盖时明确说明。")
    chunks: List[str] = []
    try:
        for piece in provider.chat_stream(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user_prompt}], temperature=0.3, max_tokens=2048):
            chunks.append(piece)
            yield {"type": "delta", "content": piece}
    except Exception as e:
        logger.error(f"降级回答失败: {e}")
        yield {"type": "error", "message": "回答生成失败，请稍后重试。"}
        return
    answer_parts.append("".join(chunks))


__all__ = ["MAX_ROUNDS", "MAX_TOOL_CALLS", "MAX_SECONDS", "build_system_prompt",
           "stream_turn"]
