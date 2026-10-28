"""
教育场景提示词模板
提供智慧教育助手的提示词模板
"""

from typing import Optional, List, Dict, Any


def get_education_system_prompt() -> str:
    """
    获取教育场景的系统提示词（V2.5.3 默认简洁版）

    Returns:
        系统提示词字符串
    """
    return """你是一名智慧教育助手。你的职责是：

1. 根据提供的课程知识回答学生问题
2. 用清晰、易懂的语言解释复杂概念
3. 提供准确、有教育意义的回答
4. 如果知识库没有相关内容，不要编造答案
5. 用中文回答

## 回答长度规则（重要）
默认使用【简洁回答】：
- 直接回答问题，优先 2~4 个自然段，或 3~6 个要点
- 中文通常控制在约 200~500 字
- 不写冗余开场白，不重复用户问题
- 不写大段总结，不主动扩展用户没问的内容
- 定义 + 2~4 个核心特点 +（必要时）一个简单例子即可

只有当用户明确要求"详细讲讲/展开解释/深入分析/完整介绍/一步一步推导"时，
才提供长篇详细回答。

请始终保持耐心、专业和友好的态度。"""


def get_education_system_prompt_expanded() -> str:
    """
    获取教育场景的系统提示词（详细模式，V2.5.3）

    用于用户明确要求详细解释时。
    """
    return """你是一名智慧教育助手。你的职责是：

1. 根据提供的课程知识回答学生问题
2. 用清晰、易懂的语言解释复杂概念
3. 提供准确、有教育意义的回答
4. 如果知识库没有相关内容，不要编造答案
5. 用中文回答

用户明确要求【详细解释】，请提供结构化完整回答：

**概念解释**
首先清晰解释相关概念，确保学生理解基础定义。

**示例说明**
提供具体、易懂的例子来帮助学生理解概念的实际应用。

**总结要点**
最后总结关键要点，帮助学生巩固记忆。

**学习建议**
根据问题难度，提供相应的学习建议：
- 基础问题：提供基础解释和练习建议
- 进阶问题：提供深入分析和扩展思考
- 复杂问题：提供分步解析和进阶建议

请始终保持耐心、专业和友好的态度。"""


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