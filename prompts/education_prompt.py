"""
教育场景提示词模板
提供智慧教育助手的提示词模板
"""

from typing import Optional, List, Dict, Any


def get_education_system_prompt() -> str:
    """
    获取教育场景的系统提示词（默认简洁版）

    Returns:
        系统提示词字符串
    """
    return """你是一名专业、清晰、耐心的智慧教育助手，始终使用中文回答。

## 核心准则
1. **依据优先**：严格基于提供的课程资料回答；资料未覆盖的内容如实说明，绝不编造定义、公式、页码或来源。
2. **结论先行**：默认控制在 200~450 字（2~4 段或 3~6 条要点），结构清晰：核心结论 → 关键机制/理由 →（必要时）1 个直白例子。
3. **拒绝冗余与重复**：不写寒暄开场白，不复述用户原问题；多轮对话中若前文已讲过基础定义，追问时直接聚焦新疑问，不再重复前文内容。
4. **公式可读**：数学公式用 LaTeX（行内 `$...$`、独立成行 `$$...$$`），公式前后用中文说明物理/数学含义与单位。

仅当用户明确要求“详细讲讲/展开解释/深入分析/完整介绍/一步一步推导”时，才展开长篇完整推导。"""


def get_education_system_prompt_expanded() -> str:
    """
    获取教育场景的系统提示词（详细模式）

    用于用户明确要求详细解释时。
    """
    return """你是一名专业、清晰、耐心的智慧教育助手，始终使用中文回答。

## 核心准则
1. 严格基于提供的课程资料回答；资料未覆盖时明确说明，不编造定义、数据或出处。
2. 数学公式使用 LaTeX（`$...$` / `$$...$$`），前后讲清符号含义与单位；避免整段堆砌公式。
3. 多轮追问时不重复前文已讲过的基础内容，重点补齐当前卡点。

用户明确要求【详细解释】，请按以下结构给出完整清晰的讲解：
- **核心概念与本质**：一句话点透本质，再给严谨定义与关键条件。
- **典型例题/推导示例**：按“已知 → 求 → 思路 → 代入 → 结论”展示完整过程。
- **易错点与辨析**：指出 1~3 个高频失分或易混淆之处。
- **下一步建议**：给出一句具体可执行的巩固方向（如做哪类题或回看哪个前置概念）。"""


def get_education_user_prompt(
    question: str,
    context: Optional[str] = None,
    history: Optional[str] = None
) -> str:
    """
    获取教育场景的用户提示词
    
    Args:
        question: 用户问题
        context: RAG检索到的上下文
        history: 格式化的历史对话
        
    Returns:
        用户提示词字符串
    """
    prompt_parts = []
    
    # 添加知识库上下文
    if context:
        prompt_parts.append(f"以下是相关的课程知识：\n{context}")
    
    # 添加对话历史
    if history:
        prompt_parts.append(f"以下是之前的对话记录：\n{history}")
    
    # 添加当前问题
    prompt_parts.append(f"学生问题：{question}")
    
    # 添加回答要求
    prompt_parts.append("请根据以上信息回答学生问题。如果知识库没有相关内容，请说明并尝试提供一般性建议。")
    
    return "\n\n".join(prompt_parts)


def get_education_prompt_with_context(
    question: str,
    context: str,
    history: Optional[str] = None,
    summary: Optional[str] = None,
) -> str:
    """
    获取带上下文的教育提示词

    Args:
        question: 用户问题
        context: RAG检索到的上下文
        history: 格式化的历史对话（最近若干轮）
        summary: 更早对话的滚动摘要（2026-10-28，预算化上下文）

    Returns:
        完整的提示词字符串
    """
    # 构建完整提示词
    prompt_parts = [get_education_system_prompt()]

    # 添加知识库上下文
    if context:
        prompt_parts.append(f"\n\n以下是相关的课程知识：\n{context}")

    # 更早对话的摘要：只用于理解连续性，不应被当成课程资料引用
    if summary:
        prompt_parts.append(
            f"\n\n以下是本次会话更早内容的摘要（仅用于理解上下文，不要当作资料引用）：\n{summary}")

    # 添加对话历史
    if history:
        prompt_parts.append(f"\n\n以下是最近的对话记录：\n{history}")
    
    # 添加当前问题
    prompt_parts.append(f"\n\n学生问题：{question}")
    
    # 添加回答要求
    # 要求标注来源编号，便于前端区分"实际引用"与"仅检索到"
    prompt_parts.append(
        "\n\n请根据以上信息回答学生问题。"
        "若你使用了「相关的课程知识」中的内容，请在相应结论后用 [编号] 标注来源"
        "（编号与课程知识片段开头的 [1][2] 对应，可标多个）；"
        "没有使用资料的部分不要标注编号，也绝不编造来源。"
        "如果知识库没有相关内容，请如实说明，并只提供一般性建议。")
    
    return "".join(prompt_parts)


def get_education_prompt_with_history(
    question: str,
    history: List[Dict[str, str]],
    max_turns: int = 5
) -> str:
    """
    获取带历史记录的教育提示词
    
    Args:
        question: 用户问题
        history: 对话历史列表
        max_turns: 最大保留轮数
        
    Returns:
        完整的提示词字符串
    """
    # 格式化历史记录
    formatted_history = _format_history(history, max_turns)
    
    # 构建完整提示词
    prompt_parts = [get_education_system_prompt()]
    
    # 添加对话历史
    if formatted_history:
        prompt_parts.append(f"\n\n以下是之前的对话记录：\n{formatted_history}")
    
    # 添加当前问题
    prompt_parts.append(f"\n\n学生问题：{question}")
    
    # 添加回答要求
    # 要求标注来源编号，便于前端区分"实际引用"与"仅检索到"
    prompt_parts.append(
        "\n\n请根据以上信息回答学生问题。"
        "若你使用了「相关的课程知识」中的内容，请在相应结论后用 [编号] 标注来源"
        "（编号与课程知识片段开头的 [1][2] 对应，可标多个）；"
        "没有使用资料的部分不要标注编号，也绝不编造来源。"
        "如果知识库没有相关内容，请如实说明，并只提供一般性建议。")
    
    return "".join(prompt_parts)


def _format_history(
    history: List[Dict[str, str]],
    max_turns: int = 5
) -> str:
    """
    格式化对话历史
    
    Args:
        history: 对话历史列表
        max_turns: 最大保留轮数
        
    Returns:
        格式化的历史字符串
    """
    if not history:
        return ""
    
    # 限制历史轮数
    recent_history = history[-max_turns * 2:]  # 每轮包含user和assistant
    
    formatted_parts = []
    for i, msg in enumerate(recent_history):
        role = msg.get("role", "").lower()
        content = msg.get("content", "").strip()
        
        if not content:
            continue
        
        if role == "user":
            formatted_parts.append(f"学生：{content}")
        elif role == "assistant":
            formatted_parts.append(f"助手：{content}")
    
    return "\n".join(formatted_parts)


# 公开API
__all__ = [
    "get_education_system_prompt",
    "get_education_user_prompt",
    "get_education_prompt_with_context",
    "get_education_prompt_with_history"
]