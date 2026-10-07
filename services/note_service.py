"""
五段式智能笔记服务（V2.5 Task B）

实现"概念—例题—易错点—课堂提示—自测题"结构化笔记生成。

设计原则：
- 基于 RAG 证据驱动：topic → 知识库检索 → evidence → LLM 结构化生成
- 事实边界：知识库证据不足时不编造"课堂重点"，明确显示证据不足
- 复用统一 SourceInfo 来源结构，不创建新来源类型
- 复用 Model Provider（call_llm），不引入第四套模型调用
"""

import json
import re
from typing import Dict, List, Any, Optional

from utils.logger import logger
from services.ai_service import (
    call_llm,
    AIServiceError,
    retrieve_and_build_context,
    retrieve_and_build_context_scoped,
)
from services.kb_guard import require_kb_ready, KBError

# 自测题类型
QUIZ_TYPES = ("single_choice", "judgement", "short_answer")


class NoteServiceError(Exception):
    """智能笔记服务异常（携带 error_type）"""

    def __init__(self, message: str, error_type: str = "unknown"):
        self.error_type = error_type
        self.message = message
        super().__init__(message)

# 五段式笔记 Prompt（严格 JSON 输出 + 事实边界）
NOTE_PROMPT = """你是一名教育领域的智能笔记生成助手。请基于【知识库证据】为用户生成"五段式智能笔记"。

## 用户主题
{topic}
{facts_note}

## 知识库证据（仅依据以下内容，不得编造）
{context}

## 输出要求（严格 JSON，不要输出任何其他内容）
{{
  "title": "主题复习笔记",
  "concept": {{
    "content": "核心概念讲解（基于证据，200字内）",
    "sources": []
  }},
  "example": {{
    "content": "典型例题与完整解答（按下方「例题处理规则」三选一）",
    "sources": []
  }},
  "mistakes": {{
    "content": "易错点与注意事项（基于证据，如无明确易错点则写'当前知识库中证据不足，无法确定易错点'）",
    "sources": []
  }},
  "class_tips": {{
    "content": "课堂提示（只有知识库中确实存在类似课堂说明/老师提示时才写；否则写'当前知识库中没有足够的课堂提示证据'）",
    "sources": []
  }},
  "quiz": [
    {{
      "question": "题干",
      "type": "single_choice | judgement | short_answer",
      "options": ["A...", "B...", "C...", "D..."],  // 判断题: ["正确","错误"]；简答题: []
      "answer": "答案",
      "explanation": "解析（基于证据）",
      "knowledge_point": "知识点"
    }}
  ]
}}

## 例题处理规则（example 字段，按证据情况三选一）
1. 证据中已有例题且带答案或解答：直接沿用原文的题目与解答过程，不要改写、不要另编新题。
2. 证据中只有题目、没有答案或解答：保留原题，由你补出完整、分步骤的解答流程，
   并在解答开头标注"（解答由 AI 补充）"，让读者能区分资料原文与补充内容。
3. 证据中完全没有例题：写"当前知识库中没有足够的证据来生成典型例题"。

## 硬性规则
1. concept/example/mistakes/class_tips 必须基于知识库证据，不得编造知识库中没有的内容。
2. class_tips 尤其禁止虚构"老师说过某句话"——只有证据中确实存在课堂说明时才填写。
3. 证据不足时，在对应 content 中明确写"当前知识库中没有足够证据"，不要编造。
   例外：例题的解答流程——题目本身来自证据时，解答可以补充（见上方例题处理规则 2）。
4. quiz 生成 2-3 道自测题，覆盖 single_choice / judgement / short_answer 至少两种。
5. 每道 quiz 必须能独立回答，explanation 基于知识库证据。
"""


def _parse_note_json(text: str) -> Dict[str, Any]:
    """
    解析笔记 JSON（兼容 markdown 代码块 / 前后解释文字）

    Returns:
        解析出的字典；失败返回 {}
    """
    if not text:
        return {}

    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    # 尝试整体解析
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # 提取第一个完整对象
    m = re.search(r'\{[\s\S]*\}', stripped)
    if m:
        try:
            data = json.loads(m.group())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    logger.warning(f"智能笔记 JSON 解析失败: {text[:120]}")
    return {}


def _resolved_model_name(model: Optional[str] = None) -> str:
    """
    本次实际调用的模型名（如 deepseek-chat）

    说明：此前这些位置写死为空串或 "current"，导致结构化消息（笔记/自测/总结）
    在会话里丢失模型标记，界面只能显示"模型: "。这里统一如实标注。
    """
    from services.ai_service import resolve_model_info
    return resolve_model_info(model).get("model") or ""


def _persist_and_publicize(
    subject_id: str,
    topic: str,
    quiz: List[Dict[str, Any]],
    source: str = "note",
    turn_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    将生成的题目持久化到题库，并返回"可下发给前端"的公开题目。

    关键约束（阶段一）：
    - 公开题目**不含标准答案与解析**，答案只能通过"提交作答"或"显式查看答案"获得
    - 返回项携带服务端题目 ID 与选项 ID，前端据此提交，由后端权威判分
    - 未入库成功时降级为原始题目（无服务端 ID，前端应提示不可提交判分），
      不伪造题目 ID

    Returns:
        公开题目列表
    """
    if not quiz:
        return []
    try:
        from services.quiz_service import persist_questions
        from services import kp_attribution, learning_data
        # 允许集合 = 请求主题 + 本课程已登记的知识点；按每题自身归属分组，
        # 归属不在集合内的记为待确认（不整批套用主题）。
        allowed = [topic] + [p.get("name") for p in learning_data.list_knowledge_points(subject_id)
                             if p.get("name")]
        # 归属审核：题干的真实考查点可能与模型首轮标注不符，逐题确认或待确认
        verified = kp_attribution.verify_attribution(
            quiz, allowed,
            lambda p: call_llm(p, system_message="你是严谨的命题审核员。",
                               temperature=0.0, max_tokens=800))
        for it, kp in zip(quiz, verified):
            if isinstance(it, dict):
                it["knowledge_point"] = kp
        by_kp, pending = kp_attribution.resolve_items(quiz, allowed)
        out: List[Dict[str, Any]] = []
        for kp_name, group in by_kp.items():
            out += persist_questions(subject_id, kp_name, group, source=source,
                                     turn_id=turn_id)
        if pending:
            out += persist_questions(subject_id, None, pending, source=source,
                                     turn_id=turn_id)
        return out
    except Exception as e:
        logger.warning(f"题目入库失败（降级为未入库题目，不参与判分）: {e}")
        return [
            {**item, "server_persisted": False}
            for item in quiz if isinstance(item, dict)
        ]


def _normalize_quiz(raw: Any, knowledge_point: str) -> List[Dict[str, Any]]:
    """规范化自测题列表（统一字段结构）"""
    if not isinstance(raw, list):
        return []
    quizzes = []
    for i, q in enumerate(raw):
        if not isinstance(q, dict):
            continue
        qtype = str(q.get("type", "")).strip()
        if qtype not in QUIZ_TYPES:
            # 兼容宽松类型名
            if "judg" in qtype.lower():
                qtype = "judgement"
            elif "single" in qtype.lower() or "choice" in qtype.lower():
                qtype = "single_choice"
            elif "short" in qtype.lower():
                qtype = "short_answer"
            else:
                qtype = "single_choice"
        question = str(q.get("question", "")).strip()
        if not question:
            continue
        options = q.get("options", [])
        if not isinstance(options, list):
            options = []
        if qtype == "judgement":
            options = ["正确", "错误"]
        quizzes.append({
            "id": f"q{i + 1}",
            "question": question,
            "type": qtype,
            "options": options,
            "answer": str(q.get("answer", "")).strip(),
            "explanation": str(q.get("explanation", "")).strip(),
            # 保留题目**自己的**知识点；模型没给则 None（待确认）。
            # 修复（2026-10-05）：此前无条件写成整批主题，导致"复杂度题"被计入"稳定性"。
            "knowledge_point": (str(q.get("knowledge_point")).strip()
                                if q.get("knowledge_point") else None),
            # 为未来学情诊断预留（V2.6）
            "sources": [],
        })
    return quizzes


# ==================== 无知识库证据时的两级降级出题 ====================

WEB_QUIZ_PROMPT = """你是一名教育出题助手。下面是【网络搜索到的资料片段】，请据此为用户整理自测题。

## 知识点主题
{topic}

## 网络资料（仅作素材，不得整卷照抄）
{material}

## 输出要求（严格 JSON，不要输出任何其他内容）
{{
  "quiz": [
    {{
      "question": "题干",
      "type": "single_choice | judgement | short_answer",
      "options": ["A...", "B...", "C...", "D..."],
      "answer": "答案",
      "explanation": "解析",
      "knowledge_point": "{topic}"
    }}
  ]
}}

## 规则
1. 生成 {count} 道题，覆盖 single_choice / judgement / short_answer 至少两种。
2. 题目必须与"知识点主题"相关；资料里没有依据的内容不要编造。
3. **答案必须自己重新推导核对**：网页上的答案经常是错的，照抄会误人子弟。
   某道题的答案无法确定时，换一道能确定的题，不要硬凑。
4. 不要整段复制网页原文，用自己的话重新组织题干与解析。
"""

GENERAL_QUIZ_PROMPT = """你是一名教育出题助手。用户的知识库里没有覆盖这个主题的资料，
请用你自己掌握的知识出一套**通用**自测题。

## 知识点主题
{topic}

## 输出要求（严格 JSON，不要输出任何其他内容）
{{
  "quiz": [
    {{
      "question": "题干",
      "type": "single_choice | judgement | short_answer",
      "options": ["A...", "B...", "C...", "D..."],
      "answer": "答案",
      "explanation": "解析",
      "knowledge_point": "{topic}"
    }}
  ]
}}

## 规则
1. 生成 {count} 道题，覆盖 single_choice / judgement / short_answer 至少两种。
2. 只考各版本教材都会讲的核心内容，避免依赖某一本特定教材的表述、例题编号或页码。
3. 答案必须准确；拿不准的知识点不要出题。
4. explanation 要讲清楚原因，不能只重复答案。
"""


def _subject_display_name(subject_id: str) -> str:
    """取学科显示名（联网查询词里带上它，命中率更高）；失败返回空串"""
    try:
        from services import subject_service
        return str((subject_service.get_subject(subject_id) or {}).get("name") or "").strip()
    except Exception:
        return ""


def _quiz_from_prompt(prompt: str, topic: str, model: Optional[str]) -> List[Dict[str, Any]]:
    """让模型按统一结构出题并规范化；解析失败返回空列表（由调用方继续降级）"""
    answer = call_llm(prompt, system_message="你是智慧教育自测题助手。", model=model)
    raw = _parse_note_json(answer)
    if not raw:
        return []
    return _normalize_quiz(raw.get("quiz"), topic)


def _generate_fallback_quiz(
    subject_id: str,
    topic: str,
    model: Optional[str],
    count: int,
) -> Dict[str, Any]:
    """
    知识库证据不足时的降级出题（2026-10-02）

    层 2：联网搜索（Tavily）→ 由 LLM 从素材整理题目，并记录素材来源 URL；
    层 3：AI 通用知识出题（不联网），标注 source=general_knowledge。

    为什么要降级：各校教材版本不同，本地知识库只覆盖学生自己上传的资料。
    证据不足时直接返回空题目 = 学生"无题可做"，所以给出通用题并**明确标注来源**，
    既不冒充"你的资料里有这道题"，也不让学生空手。

    任何一层失败都继续往下，最终仍失败才返回空列表。
    """
    subject_name = _subject_display_name(subject_id)
    model_name = _resolved_model_name(model)

    # ---- 层 2：联网搜索素材 → LLM 整理成题 ----
    try:
        from services import web_search_service
        if web_search_service.is_enabled():
            material = web_search_service.search_question_material(
                web_search_service.build_query(topic, subject_name))
            if material:
                block = "\n\n".join(
                    f"[{i + 1}] {m['title']}（{m['url']}）\n{m['content']}"
                    for i, m in enumerate(material))
                quiz = _quiz_from_prompt(
                    WEB_QUIZ_PROMPT.format(topic=topic, material=block[:12000], count=count),
                    topic, model)
                if quiz:
                    public = _persist_and_publicize(subject_id, topic, quiz, source="web_search")
                    logger.info(f"联网出题成功: subject={subject_id}, topic={topic}, n={len(public)}")
                    return {
                        "quiz": public, "model": model_name, "context_used": False,
                        "evidence_count": 0, "source": "web_search",
                        "material_sources": [{"title": m["title"], "url": m["url"]}
                                             for m in material],
                    }
    except Exception as e:
        logger.warning(f"联网出题失败（降级为通用知识出题）: {e}")

    # ---- 层 3：AI 通用知识出题 ----
    try:
        quiz = _quiz_from_prompt(GENERAL_QUIZ_PROMPT.format(topic=topic, count=count), topic, model)
        if quiz:
            public = _persist_and_publicize(subject_id, topic, quiz, source="general_knowledge")
            logger.info(f"通用知识出题: subject={subject_id}, topic={topic}, n={len(public)}")
            return {
                "quiz": public, "model": model_name, "context_used": False,
                "evidence_count": 0, "source": "general_knowledge", "material_sources": [],
            }
    except Exception as e:
        logger.warning(f"通用知识出题失败: {e}")

    return {"quiz": [], "model": model_name, "context_used": False,
            "evidence_count": 0, "source": "none", "material_sources": []}


def generate_quiz(
    subject_id: str,
    topic: str,
    model: Optional[str] = None,
    top_k: int = 6,
    count: int = 5,
    user_id: Optional[str] = None,
    scope: Optional[Dict[str, Any]] = None,
    turn_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    生成自测题（V2.5.1 Chat 自测模式）

    基于 RAG 证据生成自测题，复用 note_service 的 quiz 规范化逻辑。

    Args:
        subject_id: 学科ID
        topic: 知识点主题（如"二叉树"）
        model: 模型名（可选）
        top_k: RAG 检索数量
        count: 题目数量

    Returns:
        {"quiz": [...], "model": str, "context_used": bool, "evidence_count": int}

    Raises:
        NoteServiceError
        KBError: 学科不存在 / 知识库未构建
    """
    try:
        # 知识库就绪守卫
        require_kb_ready(subject_id)

        # RAG 检索证据（按查看者可见范围过滤课程资料）
        # 未指定资料范围时走原有入口，保持既有契约与可 mock 点；指定范围时才用范围检索。
        if scope:
            ctx = retrieve_and_build_context_scoped(topic, subject_id, top_k=top_k,
                                                    viewer_user_id=user_id, scope=scope)
        else:
            ctx = retrieve_and_build_context(topic, subject_id, top_k=top_k,
                                             viewer_user_id=user_id)
        context = ctx.get("context", "")
        sources = ctx.get("sources", [])
        context_used = ctx.get("context_used", False)

        if not context_used or not context:
            # 知识库没有证据：不冒充"你的资料里有这道题"，改走联网 / 通用知识并标注来源
            logger.info(f"自测题证据不足，尝试联网/通用知识出题"
                        f"（subject={subject_id}, topic={topic}）")
            return _generate_fallback_quiz(subject_id, topic, model, count)

        prompt = f"""你是一名教育自测题生成助手。请基于【知识库证据】生成自测题。

## 知识点主题
{topic}

## 知识库证据（仅依据以下内容，不得编造）
{context[:10000]}

## 输出要求（严格 JSON，不要输出任何其他内容）
{{
  "quiz": [
    {{
      "question": "题干",
      "type": "single_choice | judgement | short_answer",
      "options": ["A...", "B...", "C...", "D..."],
      "answer": "答案",
      "explanation": "解析（基于证据）",
      "knowledge_point": "知识点"
    }}
  ]
}}

## 规则
1. 生成 {count} 道题，覆盖 single_choice / judgement / short_answer 至少两种。
2. 每道题必须能独立回答，explanation 基于上面的资料。
3. 不得编造资料里没有的概念。

## 用语要求（重要——这些文字会原样显示给学生）
学生看不到"知识库""检索片段""证据"这些内部概念，所以一律用他们能懂的话：
- **禁止出现**"知识库""检索""片段""证据""资料范围""模型"这类词；
- 指代来源时就说"你上传的资料""课本"，或者干脆直接讲内容——
  例如写"压杆稳定实验表明……"，而不是"知识库证据（图7）启示1明确指出……"；
- 资料原文里的编号、图号、"启示1"这类排版标记**不要照抄**；
  确有必要提页码图号时，写成"（资料第 7 页的图）"这种学生看得懂的形式；
- explanation 要讲清"为什么"，一句话把道理说透，不要罗列资料来源。
"""
        answer = call_llm(prompt, system_message="你是智慧教育自测题助手。", model=model)

        raw = _parse_note_json(answer)
        if not raw:
            raise NoteServiceError("自测题生成失败：模型输出无法解析为结构化内容",
                                   error_type="structured_output_error")

        quiz = _normalize_quiz(raw.get("quiz"), topic)
        if not quiz:
            # 有证据却没出出题来（模型没按结构输出）→ 同样降级，别让学生无题可做
            logger.info(f"基于证据出题为空，降级出题（subject={subject_id}, topic={topic}）")
            return _generate_fallback_quiz(subject_id, topic, model, count)
        # 阶段一：题目入库并返回服务端题目 ID（测验模式提交前不返回标准答案）
        public_quiz = _persist_and_publicize(subject_id, topic, quiz, source="quiz",
                                            turn_id=turn_id)
        return {
            "quiz": public_quiz,
            "model": _resolved_model_name(model),
            "context_used": True,
            "evidence_count": len(sources),
            "source": "knowledge_base",
            "material_sources": [],
        }

    except KBError:
        raise
    except AIServiceError:
        raise NoteServiceError("AI生成服务暂时不可用，请稍后重试", error_type="model_api_error")
    except NoteServiceError:
        raise
    except Exception as e:
        logger.error(f"自测题异常: {e}", exc_info=True)
        raise NoteServiceError("自测题生成失败", error_type="unknown")


def generate_smart_note(
    subject_id: str,
    topic: str,
    user_instruction: Optional[str] = None,
    model: Optional[str] = None,
    top_k: int = 8,
    user_id: Optional[str] = None,
    scope: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    生成五段式智能笔记

    Args:
        subject_id: 学科ID
        topic: 知识点主题（如"二叉树"）
        user_instruction: 用户附加要求（可选）
        model: 模型名（可选，默认使用全局默认模型）
        top_k: RAG 检索数量
        user_id: 查看者身份（课程资料可见性过滤；None 按学生视角 fail-closed）

    Returns:
        {
            "title": str,
            "concept": {"content": str, "sources": [...]},
            "example": {...},
            "mistakes": {...},
            "class_tips": {...},
            "quiz": [...],
            "model": str,
            "context_used": bool,
            "evidence_count": int,
        }

    Raises:
        NoteServiceError: 检索或生成失败
        KBError: 学科不存在 / 知识库未构建
    """
    try:
        # 0. 知识库就绪守卫（快速失败，不调 RAG/LLM）
        require_kb_ready(subject_id)

        # 1. RAG 检索证据（按查看者可见范围过滤课程资料）
        # 未指定资料范围时走原有入口（保持既有契约与可 mock 点），指定范围时才用范围检索。
        if scope:
            ctx = retrieve_and_build_context_scoped(topic, subject_id, top_k=top_k,
                                                    viewer_user_id=user_id, scope=scope)
        else:
            ctx = retrieve_and_build_context(topic, subject_id, top_k=top_k,
                                             viewer_user_id=user_id)
        context = ctx.get("context", "")
        sources = ctx.get("sources", [])
        context_used = ctx.get("context_used", False)

        if not context_used or not context:
            # 知识库无证据：明确降级，不编造
            note = {
                "title": f"{topic}复习笔记",
                "concept": {"content": "当前知识库中没有足够的证据来生成核心概念。请先在知识库中添加相关文档。", "sources": []},
                "example": {"content": "当前知识库中没有足够的证据来生成典型例题。", "sources": []},
                "mistakes": {"content": "当前知识库中没有足够的证据来确定易错点。", "sources": []},
                "class_tips": {"content": "当前知识库中没有足够的课堂提示证据。", "sources": []},
                "quiz": [],
                "model": _resolved_model_name(model),
                "context_used": False,
                "evidence_count": 0,
            }
            logger.info(f"智能笔记证据不足（subject={subject_id}, topic={topic}）")
            return note

        # 2. 构造 Prompt（含事实边界说明）
        facts_note = ""
        if user_instruction:
            facts_note = f"\n## 用户附加要求\n{user_instruction}（在满足证据边界的前提下尽量满足）"
        prompt = NOTE_PROMPT.format(topic=topic, context=context[:12000], facts_note=facts_note)

        # 3. 调用 LLM（复用 Model Provider）
        answer = call_llm(prompt, system_message="你是智慧教育智能笔记助手。", model=model)

        # 4. 解析并规范化
        raw = _parse_note_json(answer)
        if not raw:
            raise NoteServiceError("智能笔记生成失败：模型输出无法解析为结构化内容",
                                   error_type="structured_output_error")

        # 统一 source 关联：每个模块关联检索证据来源（去重，最多 5 条）
        note_sources = sources[:5]

        def _module(raw_content: Any) -> Dict[str, Any]:
            if isinstance(raw_content, dict):
                content = str(raw_content.get("content", "")).strip()
            else:
                content = str(raw_content).strip()
            if not content:
                content = "当前知识库中没有足够证据。"
            return {"content": content, "sources": list(note_sources)}

        concept = _module(raw.get("concept"))
        example = _module(raw.get("example"))
        mistakes = _module(raw.get("mistakes"))
        class_tips = _module(raw.get("class_tips"))
        quiz = _normalize_quiz(raw.get("quiz"), topic)
        # 阶段一：题目入库，返回不含答案的公开题目（答案经提交/查看答案接口获取）
        display_quiz = _persist_and_publicize(subject_id, topic, quiz, source="note")

        return {
            "title": str(raw.get("title") or f"{topic}复习笔记"),
            "concept": concept,
            "example": example,
            "mistakes": mistakes,
            "class_tips": class_tips,
            "quiz": display_quiz,
            "model": _resolved_model_name(model),
            "context_used": True,
            "evidence_count": len(sources),
        }

    except KBError:
        raise
    except AIServiceError:
        # LLM/模型调用失败 → model_api_error
        raise NoteServiceError("AI生成服务暂时不可用，请稍后重试", error_type="model_api_error")
    except NoteServiceError:
        raise
    except Exception as e:
        logger.error(f"智能笔记异常: {e}", exc_info=True)
        raise NoteServiceError("智能笔记生成失败", error_type="unknown")
