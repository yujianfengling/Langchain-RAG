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
from services.ai_service import call_llm, AIServiceError, retrieve_and_build_context
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
    "content": "典型例题与解题思路（基于证据，如证据中无例题，则说明证据不足）",
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

## 硬性规则
1. concept/example/mistakes/class_tips 必须基于知识库证据，不得编造知识库中没有的内容。
2. class_tips 尤其禁止虚构"老师说过某句话"——只有证据中确实存在课堂说明时才填写。
3. 证据不足时，在对应 content 中明确写"当前知识库中没有足够证据"，不要编造。
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
            "knowledge_point": knowledge_point,
            # 为未来学情诊断预留（V2.6）
            "sources": [],
        })
    return quizzes


def generate_smart_note(
    subject_id: str,
    topic: str,
    user_instruction: Optional[str] = None,
    model: Optional[str] = None,
    top_k: int = 8,
) -> Dict[str, Any]:
    """
    生成五段式智能笔记

    Args:
        subject_id: 学科ID
        topic: 知识点主题（如"二叉树"）
        user_instruction: 用户附加要求（可选）
        model: 模型名（可选，默认使用全局默认模型）
        top_k: RAG 检索数量

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

        # 1. RAG 检索证据
        ctx = retrieve_and_build_context(topic, subject_id, top_k=top_k)
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
                "model": "",
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

        return {
            "title": str(raw.get("title") or f"{topic}复习笔记"),
            "concept": concept,
            "example": example,
            "mistakes": mistakes,
            "class_tips": class_tips,
            "quiz": quiz,
            "model": "",
            "context_used": True,
            "evidence_count": len(sources),
        }

    except KBError:
        raise
    except AIServiceError as e:
        # LLM/模型调用失败 → model_api_error
        raise NoteServiceError("AI生成服务暂时不可用，请稍后重试", error_type="model_api_error")
    except NoteServiceError:
        raise
    except Exception as e:
        logger.error(f"智能笔记异常: {e}", exc_info=True)
        raise NoteServiceError("智能笔记生成失败", error_type="unknown")
