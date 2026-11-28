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
from typing import Any, Dict, Iterator, List, Optional, Tuple

from services import material_scope, tutor_session, tutor_tools
from utils.logger import logger

MAX_ROUNDS = 6          # 最多模型轮次
MAX_TOOL_CALLS = 12     # 最多工具调用次数
MAX_SECONDS = 180       # 单轮总耗时上限
HISTORY_TURNS = 6       # 带多少轮历史给模型
TUTOR_OUTPUT_TOKENS = 1024     # tutor 单轮输出预留（用于请求预算；不足以覆盖长回答时靠流式）
INPUT_TOO_LONG_MESSAGE = ("当前问题与必要的系统指令合计已超出上下文窗口，"
                          "无法在不破坏指令的前提下压缩。请缩短问题或到新会话中继续。")

# 本轮**工具结果**总字符预算（2026-10-28）：
# 注意：字符只是辅助上限，真正的口径是 `fit_request` 给出的 token 份额。
# 单条结果上限 6000 字符，最多 12 次调用 → 最坏约 7.2 万字符（≈4.8 万 token），
# 会顶穿窗口。这里再设一个累计上限，超出后截断并在前端给出提示。
MAX_TOOL_RESULT_CHARS = 12000

_COMMON_RULES = """
## 核心准则（必须遵守）
### 1. 事实与引用底线
- 涉及课程内容、概念、例题、作业时，先调用 search_materials 检索资料再作答；严禁凭记忆编造资料里没有的内容。
- 引用格式：`[1]` `[2]` 对应本次检索结果里的真实 `id`。**只标注真正支撑该结论的片段**，不把无关结果凑数；未用到资料时明确写“当前资料未覆盖”。必须去掉资料原文自带的参考文献编号，防止指向不存在片段。
- 严禁编造页码、章节号、分数、成绩、教师考点或学生数据；若工具返回 `scope_warning`，必须如实转告学生，不得包装成该知识点的讲解或练习。
- “掌握 / 通过检测”的判定仅由程序依据学生独立作答给出，你不得自行宣布。

### 2. 表达与防重复
- 全程使用中文，语言朴素直白、**先给结论再给依据**，不输出内部 ID、系统术语或调试信息。
- 回答直击问题本身：不写套话开场白，不重复复述学生提问；多轮对话中若前文已讲过基础定义或推导，追问时直接切入新问题或卡点，不再把前文讲过的大段内容重讲一遍。
- 工具把讲解或笔记写入侧栏成果时，只讲知识内容本身，不要播报“已保存”“学习成果”“随时可打开复习”或成果标题。
- 数学公式使用 LaTeX（行内 `$...$`、独立成行 `$$...$$`），但不要堆砌公式：公式前后用中文说清物理/数学含义并带单位；例题按“已知 → 求 → 思路 → 代入 → 结论”清晰展开。

### 3. 工具与出题纪律
- 需要澄清歧义或确认重大操作时，调用 `ask_user`，不要盲猜后直接改数据。
- 学生要出题/练习/辨析时，检索词必须用教材里真实出现的实词（概念名、章节名、公式名），禁用“练习/例题/辨析题”等请求词；若按实词仍 0 命中，直接沿用本轮对话里已确认的具体主题调用 `generate_practice`，不要反复追问“你想考什么”。
- 学生本轮只要出题/练习/检测（未同时要求讲解）时，正文**不要先概括、讲解或复述知识点**（定义、分类、前文回顾一句都不要写），也严禁调用 `generate_node_lesson`；正文只用 1~2 句话说明“出了几道题、考什么、请在卡片作答”。
"""

# 引导模式（2026-10-24）：只改变「怎么讲」，不改变检索、工具权限、判分与掌握度。
# 放在通用规则之后，确保它覆盖「先给结论再给依据」的默认风格。
_SOCRATIC_RULES = """
## 引导模式（socratic，优先级最高）
学生选择了「引导我」，目标是让他自己推出来，而不是直接递答案。本模式优先于通用规则的“先给结论再给依据”：
1. 除非学生明确要求“直接告诉我 / 看答案 / 不会了”，否则**不要给出最终答案、正确选项或完整解题步骤**。
2. 每轮只聚焦一步：先用 1 个具体问题定位学生卡点（已知什么、试过哪步、卡在哪）。
3. 按 5 级提示阶梯逐层推进，每次只上一层：L1 复述题意圈条件 → L2 相关概念/类比 → L3 只给第一步 → L4 关键中间步 → L5 完整解。
4. 仅当学生连续两次明确索要答案时才进入 L5，并提示“先看懂思路再合上自己复述一遍”。
5. 学生给出尝试后，先肯定做对的部分，再只纠正 1 个最关键的偏差，不一次全盘否定。
6. 做完后邀请学生用 1~2 句话复述核心思路，只给 1 条改进建议；掌握度仍由程序判定。
"""

_ABILITY_PROMPTS: Dict[str, str] = {
    tutor_session.ABILITY_CHAT: """
## 当前能力：资料问答与深入追问
- 围绕当前问题直接作答，需要资料依据时先调用 `search_materials`；回答结构：`结论 → 关键依据（带引用）→ 可选延伸`。
- 资料未覆盖时明确说明“当前资料未覆盖”，若补充通用知识必须显式标注为通用知识。
- 涉及图片、图表、表格、版式或“第几页”时，先调用 `search_pages` 按页检索，不凭纯文字猜测图示内容。
- 涉及概念关系、推导来源、前置依赖或跨章节对比时，可调用 `query_concept_graph` 结合知识图谱与前置掌握情况回答。
- 若本会话前文已经系统讲解过该知识点，后续追问或出题时不要重复前文内容。
""",
    tutor_session.ABILITY_EXPLAIN: """
## 当前能力：引导学习与知识点讲解
- 面向当前知识点做清晰的分步讲解：`一句话概括 → 核心要点与直白例子 → 1~2 个常见易错点 → 建议下一步`。
- 学生反馈“没理解 / 换个例子 / 更基础一点”时，降低台阶并用新类比重讲，绝不照搬原话。
- 学生要求系统讲解某个具体知识点时，先调用 `get_node` 或 `search_materials` 获取依据，再调用 `generate_node_lesson` 生成结构化讲解，并将要点自然讲给学生（不播报保存动作）。
- 学生反复出错或卡在推导时，可调用 `query_concept_graph` 排查薄弱前置概念，先快速补齐前置再回到当前知识点。
- 涉及图、表、公式版式时，先调用 `search_pages` 核对原页内容再讲。
""",
    tutor_session.ABILITY_PRACTICE: """
## 当前能力：练习与检测
- 直接调用 `generate_practice` 生成围绕当前知识点/主题的练习题（答案由后端保存，模型不可见）。
- **出题前后都不要概括、讲解或复述知识点**——严禁输出“一、概括”式定义回顾、知识梳理或前文复述，严禁调用 `generate_node_lesson`。
- 出题完成后，**只用 1~2 句话（不超过 60 字）说明“出了几道题、考查什么主题”并请学生在下方卡片作答**；**严禁复述题干、选项、编号清单、答案或解析**。
- 学生提交作答后，再基于后端判分结果做精准讲评与下一步建议。
""",
    tutor_session.ABILITY_MAP: """
## 当前能力：思维导图与知识地图
- 学生需要学习路线或思维导图时，调用 `generate_knowledge_map` 生成（资料未变更时自动复用）。
- 生成后简要说明覆盖的资料与未覆盖项，提示学生点击地图节点即可逐点学习。
""",
    tutor_session.ABILITY_ORGANIZE: """
## 当前能力：学习整理（笔记、提纲、学习安排）
- 将当前内容整理为结构化可复用成果（笔记 / 提纲 / 计划 / 复习卡片），并调用 `save_artifact` 保存。
- 规划学习安排前先调用 `get_learning_state` 读取真实薄弱点、待复习与时间预算；若超出预算需标明草案并请学生确认。
""",
}

# 纯出题/检测意图识别：即使用户在 chat 或 explain 模式下自由输入“给我出3道题”，
# 也自动按 practice 纪律处理（不先写大段讲解、不调用 generate_node_lesson、不复述题干）。
_PRACTICE_INTENT_RE = re.compile(
    r"(?:(?:再|帮我|给我|请|来|做|出|生成|布置|准备|刷|练)\s*[几两三四五\d]{0,3}\s*道?\s*"
    r"(?:练习|自测|测试|检测|巩固|变式|选择|判断|简答|考)?题)"
    r"|(?:出(?:几道|两道|三道|一道|\d+道|点|些|套)?(?:练习|自测|测验|检测|题))"
    r"|(?:(?:考考我|测测我|做练习|做几道题|来几道题|出题|刷题|巩固练习|检测一下|自测一下))",
    re.IGNORECASE,
)
_EXPLAIN_CO_INTENT_RE = re.compile(
    r"(?:讲解|讲讲|讲一下|细讲|精讲|解释|推导|展开讲|梳理|概括|总结一下|"
    r"整理笔记|做笔记|生成笔记|写提纲|思维导图|学习计划|为什么|怎么理解|什么是|区别是什么|怎么算)",
    re.IGNORECASE,
)
_POST_PRACTICE_LECTURE_RE = re.compile(
    r"(?:^|\n)\s*(?:#{1,4}\s|[一二三四五六七八九十]+[、.]|\*\*一、|1\.\s*\*\*|A[\.、]\s)",
    re.MULTILINE,
)


def _is_pure_practice_request(message: str) -> bool:
    """判断本轮消息是否为「纯出题/练习请求」（未同时要求讲解或整理笔记）。"""
    text = (message or "").strip()
    if not text:
        return False
    if _EXPLAIN_CO_INTENT_RE.search(text):
        return False
    return bool(_PRACTICE_INTENT_RE.search(text))


def _compact_practice_result_for_model(result: Dict[str, Any]) -> Dict[str, Any]:
    """发回给模型的精简出题工具结果：隐去完整题干/选项，避免模型复述题目或长篇讲解。"""
    questions = result.get("questions") or []
    type_counts: Dict[str, int] = {}
    for q in questions:
        qt = str((q or {}).get("type") or "single_choice")
        type_counts[qt] = type_counts.get(qt, 0) + 1
    count = int(result.get("question_count") or len(questions))
    topic = str(result.get("topic") or "").strip()
    note = (
        f"已完成出题：共 {count} 道题（主题：{topic or '当前知识点'}），"
        "交互式练习卡片已自动下发到界面（答案由后端保存）。"
        "你的回复必须控制在 1~2 句话（不超过 60 字），只说明题数、主题并请学生作答；"
        "严禁复述前文讲解、严禁概括知识点、严禁抄写题干或选项。"
    )
    if result.get("scope_warning"):
        note = "注意存在 scope_warning，请先如实转告学生范围提示，再用 1 句话说明已出题。"
    return {
        "ok": bool(result.get("ok", True)),
        "topic": topic,
        "question_count": count,
        "question_types": type_counts,
        "source": result.get("source"),
        "scope_covered": result.get("scope_covered", True),
        "scope_warning": result.get("scope_warning"),
        "note": note,
    }


def _sanitize_post_practice_reply(
    text: str,
    practice_result: Optional[Dict[str, Any]],
    pure_practice: bool,
) -> str:
    """兜底守卫：纯出题轮次若模型仍输出大段讲解/复述题干，自动收敛为一句话确认。"""
    cleaned = (text or "").strip()
    if not (pure_practice and practice_result and practice_result.get("ok")):
        return cleaned
    if practice_result.get("scope_warning"):
        return cleaned
    count = int(practice_result.get("question_count") or len(practice_result.get("questions") or []))
    if count <= 0:
        return cleaned
    # 正常 1~2 句简洁回复（如 ≤160 字且不含大纲/分点讲解/选项抄写）原样放行
    is_lecture_wall = (
        len(cleaned) > 160
        or ("\n\n" in cleaned and len(cleaned) > 90)
        or bool(_POST_PRACTICE_LECTURE_RE.search(cleaned))
    )
    if not is_lecture_wall and cleaned:
        return cleaned
    topic = str(practice_result.get("topic") or "当前知识点").strip()
    return f"已围绕「{topic}」为你生成 {count} 道练习题，请直接在下方卡片作答；提交后系统会自动判分并更新掌握度。"


def build_system_prompt(subject_name: str, ability: str, scope: Dict[str, Any],
                        context_state: Dict[str, Any],
                        node: Optional[Dict[str, Any]] = None,
                        guidance_mode: str = tutor_session.GUIDANCE_DIRECT,
                        *, teaching_block: str = "") -> str:
    role = context_state.get("role") or "student"
    mode = tutor_session.normalize_guidance_mode(guidance_mode)
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
    if teaching_block:
        # 2026-10-28：与 chat 同源的学习者画像/记忆（已含隐私早退，≤900 字符）
        lines.append("【学习者画像与记忆（系统提供，可据此个性化，不要逐条复述）】")
        lines.append(teaching_block.strip()[:900])
    lines.append(_ABILITY_PROMPTS.get(ability, _ABILITY_PROMPTS[tutor_session.ABILITY_CHAT]))
    lines.append(_COMMON_RULES)
    # 引导模式只作用于「对话/讲解/练习」；地图与学习整理是产出型任务，
    # 加引导规则反而会让模型偏离“直接生成成果”的目标。
    if (mode == tutor_session.GUIDANCE_SOCRATIC
            and ability in (tutor_session.ABILITY_CHAT,
                            tutor_session.ABILITY_EXPLAIN,
                            tutor_session.ABILITY_PRACTICE)):
        lines.append(_SOCRATIC_RULES)
    return "\n".join(lines)


_TUTOR_SUMMARY_LAST: Dict[str, float] = {}
_TUTOR_SUMMARY_THROTTLE_SECONDS = 30.0


def _history_with_summary(user_id: str, subject_id: str, conversation_id: Optional[str],
                          exclude_turn_id: Optional[str] = None,
                          context_state: Optional[Dict[str, Any]] = None,
                          summarize: bool = True) -> tuple[List[Dict[str, Any]], str]:
    """按 token 预算恢复最近轮次，并在需要时更新滚动摘要。

    与 chat 共用 `services.context_budget` 的比例；摘要写入 tutor 的 state_json，
    不新增表、不改消息计数。任何异常都退化为“只用最近轮次”，不阻断回答。
    """
    if not conversation_id:
        return [], ""
    from services import context_budget as cb

    turns = tutor_session.list_turns(user_id, subject_id, limit=60)
    picked: List[Dict[str, Any]] = []
    for turn in reversed(turns):
        if turn.get("conversation_id") != conversation_id:
            continue
        if exclude_turn_id and turn.get("id") == exclude_turn_id:
            continue
        if turn.get("status") != tutor_session.STATE_SUCCEEDED or not turn.get("answer"):
            continue
        picked.append({"role": "user", "content": turn.get("user_message") or "",
                       "turn_id": turn.get("id")})
        picked.append({"role": "assistant", "content": turn.get("answer") or "",
                       "turn_id": turn.get("id")})
    picked.reverse()
    while picked and picked[0]["role"] != "user":
        picked.pop(0)

    kept, dropped = cb.select_recent(picked, cb.recent_budget(),
                                     max_turn_limit=cb.max_turns())
    state = context_state if isinstance(context_state, dict) else {}
    summary = str(state.get("rolling_summary") or "").strip()
    if summarize and dropped and cb.summary_enabled() and cb.should_summarize(dropped):
        updated = _summarize_turns(summary, dropped)
        if updated:
            state["rolling_summary"] = updated
            state["summary_upto"] = dropped[-1].get("turn_id")
            summary = updated
            try:
                tutor_session.save_context(user_id, subject_id, state)
            except Exception as e:
                logger.warning(f"保存滚动摘要失败（本轮继续）: {e}")
    return kept, summary


def _summarize_turns(previous_summary: str,
                     dropped: List[Dict[str, Any]]) -> str:
    """把被折叠的较早轮次并入摘要；失败返回空串（保留旧摘要）。"""
    from services import context_budget as cb

    if not dropped:
        return ""
    key = "|".join(str(m.get("turn_id") or "") for m in dropped[:2])
    now = time.time()
    if now - _TUTOR_SUMMARY_LAST.get(key, 0.0) < _TUTOR_SUMMARY_THROTTLE_SECONDS:
        return ""
    _TUTOR_SUMMARY_LAST[key] = now
    try:
        from services.ai_service import call_llm

        cap = cb.summary_budget()
        prompt = cb.build_summary_prompt(previous_summary, dropped)
        text = call_llm(
            prompt=prompt,
            system_message="你只输出中文摘要，不要寒暄，不要编造对话里没有的内容。",
            max_tokens=cap,
        )
        return cb.finalize_summary(text, max_tokens=cap)
    except Exception as e:
        logger.warning(f"生成滚动摘要失败（保留旧摘要）: {e}")
        return ""


def _recent_history(user_id: str, subject_id: str, conversation_id: Optional[str],
                    exclude_turn_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """从持久化轮次恢复对话上下文（保留旧签名，供既有调用/测试使用）。"""
    kept, _ = _history_with_summary(
        user_id, subject_id, conversation_id, exclude_turn_id, summarize=False)
    return kept


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
    if name == "search_pages":
        return f"按页检索到 {result.get('page_count', 0)} 页"
    if name == "read_memory":
        return (f"读取到 {result.get('count', 0)} 条记忆 / "
                f"{len(result.get('recent_activity') or [])} 条最近活动")
    if name == "read_source":
        page = result.get("page")
        suffix = f" 第 {page} 页" if page else ""
        suffix += "（OCR）" if result.get("used_ocr") else ""
        return f"读取《{result.get('file_name')}》{suffix}"
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
                node_id: Optional[str] = tutor_session.NODE_UNSET,
                conversation_id: Optional[str] = None,
                model: Optional[str] = None,
                role: str = "student",
                guidance_mode: Optional[str] = None,
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
    # 本轮显式传入优先；否则沿用会话设置。两者都非法时回落到 direct（旧行为）。
    effective_mode = tutor_session.normalize_guidance_mode(
        guidance_mode if guidance_mode is not None else context_state.get("guidance_mode"))
    context_state.update({"ability": ability, "role": role,
                          "guidance_mode": effective_mode})
    node_id = tutor_session.resolve_node(user_id, subject_id, node_id)
    context_state["current_node_id"] = node_id
    # 引导模式需要跨轮记忆：前端没有显式传会话时，用按学科稳定的会话串起来。
    # 只作用于 socratic，direct 的既有行为不受影响。
    if not conversation_id and effective_mode == tutor_session.GUIDANCE_SOCRATIC:
        conversation_id = context_state.get("conversation_id") or f"socratic:{subject_id}"
    if conversation_id:
        context_state["conversation_id"] = conversation_id
    context_state = tutor_session.save_context(user_id, subject_id, context_state)

    # 记录 node_id：历史记录要按知识点归集（点某知识点时右侧显示它以前讲过什么）
    turn_id = tutor_session.create_turn(user_id, subject_id, ability, message, conversation_id,
                                        node_id=node_id)
    context_state["last_turn_id"] = turn_id
    tutor_session.save_context(user_id, subject_id, context_state)

    from services import turn_execution as tx
    execution = tx.Execution(turn_id, MAX_SECONDS,
        lambda: bool((cancel_check and cancel_check()) or tutor_session.is_cancelled(turn_id)))
    tx.register(execution)
    binding = tx.bind(execution)
    binding.__enter__()

    def emit(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if event_type not in ("error", "done"):
            execution.check()
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
            "guidance_mode": effective_mode,
            "node_id": node_id,
            "conversation_id": conversation_id,
            "model": model,
            "scope": material_scope.scope_to_public(scope),
        })
        yield emit("context", {
            "scope": material_scope.scope_to_public(scope),
            "node": {"node_id": node.get("node_id"), "label": node.get("label")} if node else None,
            "ability": ability,
            "guidance_mode": effective_mode,
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

        pure_practice_intent = _is_pure_practice_request(message)
        # 若学生在 chat / explain 下直接发起纯出题请求（如“给我出 3 道题”），
        # 本轮自动按 practice 能力运行：切到出题提示词、移除 generate_node_lesson、并抑制工具前讲解流式输出。
        effective_ability = (
            tutor_session.ABILITY_PRACTICE
            if (ability == tutor_session.ABILITY_PRACTICE
                or (pure_practice_intent and ability in (tutor_session.ABILITY_CHAT,
                                                         tutor_session.ABILITY_EXPLAIN)))
            else ability
        )
        suppress_pre_tool = (effective_ability == tutor_session.ABILITY_PRACTICE)

        tool_ctx = tutor_tools.ToolContext(
            user_id=user_id, subject_id=subject_id, role=role, ability=effective_ability,
            scope=scope, model=model, conversation_id=conversation_id, node_id=node_id,
            turn_id=turn_id)

        # 与 chat 同源的学习者画像/记忆；失败不影响回答（隐私早退在 teaching_context 内）
        teaching_block = ""
        try:
            from services import teaching_context
            teaching_block = (teaching_context.build_teaching_context(
                user_id=user_id, subject_id=subject_id, question=message) or {}
            ).get("prompt_block") or ""
        except Exception as e:
            logger.warning(f"构建教学上下文失败（不影响回答）: {e}")

        system_prompt = build_system_prompt(
            subject_name, effective_ability, scope, context_state, node,
            guidance_mode=effective_mode, teaching_block=teaching_block)
        history_messages, rolling_summary = _history_with_summary(
            user_id, subject_id, conversation_id, turn_id, context_state)

        tools = tutor_tools.tools_for_ability(effective_ability, role)
        # 整包请求预算（2026-10-28）：与 chat 链路同一套口径
        # 工具定义序列化后计入预算；工具结果在循环里按 token 累计上限截断。
        budget_report: Dict[str, Any] = {}
        try:
            from services import context_budget as cb

            fit = cb.fit_request(
                system=system_prompt, question=message, history=history_messages,
                summary=rolling_summary,
                tools=_json_dumps({"tools": tutor_tools.tools_for_ability(effective_ability, role)}),
                max_output=TUTOR_OUTPUT_TOKENS)
            if fit.get("too_long"):
                # 不发送被裁坏的请求：明确告知"输入过长"并结束本轮
                yield emit("error", {"message": INPUT_TOO_LONG_MESSAGE})
                tutor_session.finish_turn(turn_id, tutor_session.STATE_FAILED,
                                          error=INPUT_TOO_LONG_MESSAGE)
                return
            system_prompt = fit["system"]
            history_messages = fit["history"] if isinstance(fit["history"], list) else history_messages
            rolling_summary = fit["summary"]
            tools = fit["tools"] if isinstance(fit["tools"], list) else tools
            budget_report = fit["tokens"]
            tool_result_token_cap = max(256, int(fit["tokens"]["budget"] * cb.TOOL_RESULT_SHARE))
        except Exception as e:
            logger.warning(f"tutor 请求预算计算失败（不压缩）: {e}")
            tool_result_token_cap = MAX_TOOL_RESULT_CHARS
        else:
            logger.info(f"[context-budget] tutor 预算={budget_report.get('budget')} "
                        f"总输入={budget_report.get('total')} "
                        f"输出预留={budget_report.get('output_reserve')}")

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        if rolling_summary:
            messages.append({"role": "system",
                             "content": "【此前对话摘要】\n" + rolling_summary})
        messages.extend(history_messages)
        messages.append({"role": "user", "content": message})

        answer_parts: List[str] = []
        trace: List[Dict[str, Any]] = []
        llm_calls = 0
        tool_calls_made = 0
        tool_result_chars = 0
        tool_result_tokens = 0
        ask_user_payload: Optional[Dict[str, Any]] = None
        practice_generated: Optional[Dict[str, Any]] = None

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
            if round_index == 0 and suppress_pre_tool:
                stage = "正在准备练习题"
            elif round_index == 0 and effective_ability == tutor_session.ABILITY_CHAT:
                stage = "正在理解问题"
            yield emit("stage", {"stage": stage, "round": round_index + 1})

            try:
                stream = provider.stream_with_tools(
                    messages, tools=tools, temperature=0.3, max_tokens=2048)
                streamed_text = ""
                end_event: Dict[str, Any] = {}
                for ev in tx.iterate(stream, execution):
                    # 先累积文本；只有本轮流结束时没有 tool_calls，这段文本才是终答。
                    # 有 tool_calls 时它通常只是"我先去查资料"的前言，不作为回答展示。
                    if ev.get("type") == "content":
                        streamed_text += ev.get("text") or ""
                        # 出题轮次扣住前言不流式播出，防止模型先哗哗写一大段重复讲解再调用 generate_practice；
                        # 等确认本轮无工具调用（终答）后再一次性放出。
                        if not suppress_pre_tool:
                            yield emit("delta", {"content": ev.get("text") or "", "provisional": True})
                    elif ev.get("type") == "end":
                        end_event = ev
                execution.check()
                if not end_event:
                    raise RuntimeError('模型流提前结束，未收到结束事件')
                llm_calls += 1
            except (tx.TurnCancelled, tx.TurnTimeout):
                raise
            except NotImplementedError:
                fallback_reason = "当前模型提供方不支持工具调用，已降级为直接检索回答"
                break
            except Exception as e:
                logger.error(f"模型调用失败: {type(e).__name__}: {e}", exc_info=True)
                if not trace and not answer_parts:
                    tutor_session.finish_turn(turn_id, tutor_session.STATE_FAILED,
                                              error=f"{type(e).__name__}: {e}", answer=locals().get("streamed_text", ""))
                    yield emit("error", {"message": "模型暂时不可用，请稍后重试或换一个模型。"})
                    return
                raise

            calls = end_event.get("tool_calls") or []
            if not calls:
                final_piece = end_event.get("content") or streamed_text
                final_piece = _sanitize_post_practice_reply(
                    final_piece, practice_generated, suppress_pre_tool)
                if suppress_pre_tool and final_piece:
                    yield emit("delta", {"content": final_piece, "provisional": True})
                answer_parts.append(final_piece)
                break

            # 有工具调用：把模型这一轮的中间文本只当作"过程"，不当答案
            if streamed_text.strip() and not suppress_pre_tool:
                yield emit("stage", {"stage": "正在调用工具", "note": streamed_text.strip()[:200]})

            messages.append({"role": "assistant", "content": "" if suppress_pre_tool else (end_event.get("content") or ""),
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
                execution.check()
                execution.mark('tool:' + name)
                result = tx.call(lambda: tutor_tools.execute_tool(name, args, tool_ctx), execution)
                execution.check()
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
                if name == "generate_practice" and result.get("ok"):
                    practice_generated = result
                    payload_for_model = _compact_practice_result_for_model(result)
                else:
                    payload_for_model = {k: v for k, v in result.items() if not k.startswith("_")}
                raw_content = _json_dumps(payload_for_model)
                # 先按 token 份额截断（主口径），再按字符上限兜底（辅助）
                content = _trim_to_token_budget(raw_content, tool_result_tokens,
                                                tool_result_token_cap)
                content, cut = _fit_tool_result(content, tool_result_chars)
                tool_result_chars += len(content)
                tool_result_tokens += _count_tokens(content)
                if cut:
                    yield emit("notice", {"level": "info",
                                          "message": "工具返回内容较多，本轮已按上下文预算截断；"
                                                     "如需完整结果请缩小检索范围"})
                # tool_call 与 tool 结果必须成对：这里只截断内容，绝不丢弃消息
                messages.append({"role": "tool", "tool_call_id": call.get("id") or name,
                                 "content": content})

        execution.check()
        if fallback_reason:
            yield emit("notice", {"level": "info", "message": fallback_reason})
            yield from tx.iterate(_fallback_answer(provider, messages, tool_ctx, answer_parts, emit), execution)

        execution.check()
        answer = "".join(answer_parts).strip()
        if not answer and not ask_user_payload:
            answer = ("我已经读取了你的学习记录和资料，但这一轮没有生成有效回答。"
                      "可以换个更具体的问法，或点下方建议继续。")

        cited = _cited_ids(answer, tool_ctx.sources)
        message_info = _message_type_from_trace(trace)
        for artifact in tool_ctx.artifacts:
            yield emit("artifact", {"artifact": artifact})

        duration = round(time.time() - started, 2)
        won = tutor_session.finish_turn(turn_id, tutor_session.STATE_SUCCEEDED, answer=answer,
                                  steps=tool_calls_made, llm_calls=llm_calls)
        if not won:
            return
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
            "guidance_mode": effective_mode,
            "ask_user": ask_user_payload,
            "scope": material_scope.scope_to_public(scope),
            "elapsed": duration,
            "llm_calls": llm_calls,
            "tool_calls": tool_calls_made,
        })
    except (tx.TurnCancelled, tx.TurnTimeout) as e:
        status = tutor_session.STATE_FAILED if isinstance(e, tx.TurnTimeout) else tutor_session.STATE_CANCELLED
        tutor_session.finish_turn(turn_id, status, answer="".join(locals().get('answer_parts', [])) or locals().get('streamed_text', ''), error=str(e))
        yield emit("error", {"message": str(e), "cancelled": status == tutor_session.STATE_CANCELLED})
    except GeneratorExit:
        execution.cancel()
        # 客户端断开：保留已落库的事件与轮次状态，便于刷新后重连
        tutor_session.finish_turn(turn_id, tutor_session.STATE_CANCELLED, error="连接中断")
        raise
    except Exception as e:
        logger.error(f"统一伴学运行异常: {type(e).__name__}: {e}", exc_info=True)
        tutor_session.finish_turn(turn_id, tutor_session.STATE_FAILED,
                                  error=f"{type(e).__name__}: {e}", answer=locals().get("streamed_text", ""))
        yield emit("error", {"message": f"运行失败：{type(e).__name__}"})

    finally:
        tx.release(turn_id)
        binding.__exit__(None, None, None)


def _count_tokens(text: Any) -> int:
    try:
        from services import context_budget as cb

        return cb.count_tokens(str(text or ""))
    except Exception:
        return max(1, len(str(text or "")) // 4)


def _trim_to_token_budget(content: str, used: int, cap: int) -> str:
    """按 token 份额截断工具结果；已用满则只留一句说明。"""
    if used >= cap:
        return "（本轮工具结果已达上下文预算，后续内容不再放入）"
    try:
        from services import context_budget as cb

        return cb.trim_to_tokens(content, cap - used)
    except Exception:
        return content[: max(0, cap - used)]


def _fit_tool_result(content: str, used: int,
                     limit: Optional[int] = None) -> Tuple[str, bool]:
    """按本轮累计预算截断工具结果，返回 (内容, 是否被截断)。

    累计未超标 → 原样返回；还有余量 → 截到余量并标注；已无余量 → 只留一句说明。
    """
    cap = limit if limit is not None else MAX_TOOL_RESULT_CHARS
    text = str(content or "")
    if used + len(text) <= cap:
        return text, False
    room = cap - used
    if room > 200:
        return text[:room] + "…（本轮工具结果已达上下文预算，此处截断）", True
    return "（本轮工具结果已达上下文预算，后续内容不再放入）", True


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
        raise
        return
    answer_parts.append("".join(chunks))


__all__ = ["MAX_ROUNDS", "MAX_TOOL_CALLS", "MAX_SECONDS", "build_system_prompt",
           "stream_turn"]
