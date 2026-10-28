"""上下文预算与滚动摘要（2026-10-28，借鉴 DeepTutor）。

这一层只做**纯计算**，不碰数据库、不发请求：
- token 计数：tiktoken `cl100k_base`；缺失/离线时退化为 `len//4`（并只尝试一次）；
- 预算切分：历史预算 = min(0.35×窗口, 131072)，最近消息 60%、摘要 40%；
- 最近消息选择：从新往旧累加，至少保留最后一条，保证从 `user` 开始；
- 摘要提示词与截断：摘要命中 95% 上限时裁掉最后半句。

chat_service 与 tutor_agent 共用本模块，避免两套截断逻辑继续漂移。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 与 DeepTutor services/session/context_builder.py 对齐的比例与上限。
DEFAULT_CONTEXT_WINDOW = 32768
MAX_HISTORY_PLAN_TOKENS = 131072
HISTORY_BUDGET_RATIO = 0.35
SUMMARY_RATIO = 0.40
RECENT_RATIO = 0.60
DEFAULT_SUMMARY_MAX_TOKENS = 800
TRUNCATION_GUARD_RATIO = 0.95
IMAGE_TOKEN_ESTIMATE = 4096
MIN_RECENT_TOKENS = 128
DEFAULT_MAX_TURNS = 30

# ============ 请求总预算（2026-10-28） ============
# 历史预算只管“最近消息 + 摘要”；但一次真实请求还包含系统提示、当前问题、
# RAG 片段、工具定义与累计工具结果，还要给模型输出留空间。
# 这里给出**整包**预算与压缩优先级，供 chat 非流式 / 流式 / tutor 共用。
DEFAULT_OUTPUT_RESERVE = 1024     # 未知 max_tokens 时的保守输出预留
SYSTEM_SHARE = 0.20               # 系统提示最多占用的份额
RAG_SHARE = 0.30                  # RAG 片段份额
TOOL_RESULT_SHARE = 0.25          # 累计工具结果份额
TRIM_MARKER = "…（已按上下文预算截断）"

_encoder: Any = None
_encoder_failed = False


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _env_on(name: str, default: str = "1") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


def summary_enabled() -> bool:
    return _env_on("CHAT_HISTORY_SUMMARY", "1")


def context_window() -> int:
    return max(1024, _env_int("CHAT_CONTEXT_WINDOW_TOKENS", DEFAULT_CONTEXT_WINDOW))


def history_budget(window: Optional[int] = None) -> int:
    win = window or context_window()
    return min(max(256, int(win * HISTORY_BUDGET_RATIO)), MAX_HISTORY_PLAN_TOKENS)


def recent_budget(total: Optional[int] = None) -> int:
    base = total if total is not None else history_budget()
    return max(MIN_RECENT_TOKENS, int(base * RECENT_RATIO))


def summary_budget(total: Optional[int] = None, max_output: Optional[int] = None) -> int:
    base = total if total is not None else history_budget()
    cap = max_output if max_output is not None else _env_int(
        "CHAT_SUMMARY_MAX_TOKENS", DEFAULT_SUMMARY_MAX_TOKENS)
    return max(96, min(int(base * SUMMARY_RATIO), max(96, cap)))


def max_turns() -> int:
    return max(1, _env_int("CHAT_HISTORY_MAX_TURNS", DEFAULT_MAX_TURNS))


def _get_encoder() -> Any:
    """模块级缓存；离线或 tiktoken 缺失时只尝试一次，之后走字符估算。"""
    global _encoder, _encoder_failed
    if _encoder is not None or _encoder_failed:
        return _encoder
    try:
        import tiktoken

        _encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _encoder_failed = True
        _encoder = None
    return _encoder


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(str(text)))
        except Exception:
            pass
    # 中文约 1 token ≈ 1.5 字符；chars/4 是保守估计（宁多算，不超窗）。
    return max(1, len(str(text)) // 4)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                kind = item.get("type")
                if kind == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif kind in ("image_url", "input_image", "image"):
                    parts.append("[image]")
        return "\n".join(parts)
    return str(content or "")


def count_message_tokens(message: Dict[str, Any]) -> int:
    content = message.get("content")
    text_tokens = count_tokens(_content_text(content))
    images = 0
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") in (
                "image_url", "input_image", "image"
            ):
                images += 1
    return text_tokens + images * IMAGE_TOKEN_ESTIMATE


def count_messages_tokens(messages: Sequence[Dict[str, Any]]) -> int:
    return sum(count_message_tokens(m) for m in messages)


def select_recent(
    messages: Sequence[Dict[str, Any]],
    budget: Optional[int] = None,
    *,
    max_turn_limit: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """从新往旧选消息，返回 (保留的最近消息, 被折叠的较早消息)。

    - 至少保留 1 条（即使单条超预算，也不能把当前轮丢掉）；
    - 保留段若以 assistant 开头，会把它并入折叠段，保证上下文从 user 开始；
    - `max_turn_limit` 以“轮”计（user+assistant 为一轮）。
    """
    items = [m for m in messages if isinstance(m, dict)]
    if not items:
        return [], []

    limit = max_turn_limit if max_turn_limit is not None else max_turns()
    if limit > 0 and len(items) > limit * 2:
        items = items[-limit * 2:]

    cap = budget if budget is not None else recent_budget()
    kept_rev: List[Dict[str, Any]] = []
    total = 0
    for message in reversed(items):
        cost = count_message_tokens(message)
        if kept_rev and total + cost > cap:
            break
        kept_rev.append(message)
        total += cost
        if total >= cap:
            break

    kept = list(reversed(kept_rev))
    dropped = items[: len(items) - len(kept)]

    # 保留段必须以 user 开头；否则把开头的 assistant 退回折叠段。
    while kept and kept[0].get("role") != "user" and len(kept) > 1:
        dropped.append(kept.pop(0))

    return kept, dropped


def format_messages(messages: Sequence[Dict[str, Any]]) -> str:
    """把消息渲染成 prompt 里的历史文本（保持既有“学生/老师”口吻）。"""
    lines: List[str] = []
    for message in messages:
        role = str(message.get("role") or "").strip()
        text = _content_text(message.get("content")).strip()
        if not text:
            continue
        if role == "user":
            lines.append(f"学生：{text}")
        elif role == "assistant":
            lines.append(f"老师：{text}")
        elif role == "system":
            lines.append(f"（系统）{text}")
    return "\n\n".join(lines)


def trim_incomplete_tail(text: str) -> str:
    """摘要撞到硬上限时，丢掉最后半句，避免把截断内容当事实带进下一轮。"""
    cleaned = (text or "").rstrip()
    if not cleaned:
        return ""
    lines = cleaned.split("\n")
    if len(lines) > 1:
        return "\n".join(lines[:-1]).rstrip()
    return cleaned


def should_summarize(
    dropped_messages: Sequence[Dict[str, Any]],
    *,
    dropped_tokens: Optional[int] = None,
    summary_max_tokens: Optional[int] = None,
) -> bool:
    """是否需要生成/更新摘要：有被折叠内容且达到最小体量。"""
    if not dropped_messages:
        return False
    tokens = dropped_tokens if dropped_tokens is not None else count_messages_tokens(dropped_messages)
    floor = summary_budget(max_output=summary_max_tokens) // 4
    return tokens >= max(64, floor)


def build_summary_prompt(
    previous_summary: str,
    older_messages: Sequence[Dict[str, Any]],
    *,
    max_chars: int = 12000,
) -> str:
    """生成“合并旧摘要 + 折叠轮次”的中文摘要提示词。"""
    transcript = format_messages(older_messages)
    if len(transcript) > max_chars:
        # 保留最近的部分（对学习进展更重要），并显式标注截断。
        transcript = "（更早内容已省略）\n" + transcript[-max_chars:]
    previous = (previous_summary or "").strip()
    previous_block = f"已有摘要（需要合并，不要丢失其中的事实）：\n{previous}\n\n" if previous else ""
    return (
        "你在为一个学习系统压缩对话历史。请把下面的旧摘要与对话片段合并成一份新的中文摘要，"
        "供后续轮次继续使用。要求：\n"
        "1. 只保留对后续教学有用的事实，不要复述寒暄；\n"
        "2. 不要编造对话里没有的内容；\n"
        "3. 按固定小节输出：目标 / 关键事实与上下文 / 决定与偏好 / 进展 / 待办；\n"
        "4. 总长度控制在 600 字以内。\n\n"
        f"{previous_block}"
        "对话片段：\n"
        f"{transcript}\n"
    )


def _tools_payload(tools: Any) -> str:
    """把工具定义统一成可计量的文本（列表走 JSON，保持结构完整）。"""
    if isinstance(tools, (list, tuple)):
        try:
            import json as _json

            return _json.dumps(list(tools), ensure_ascii=False)
        except (TypeError, ValueError):
            return str(list(tools))
    return str(tools or "")


def _reduce_tools(tools: Any, need_cut: int) -> Tuple[Any, int, str]:
    """安全地缩小工具定义：先缩短描述，再整项移除。

    只改 `description`，从不改 name / parameters / required；
    描述清空后仍不够，就按列表末尾整项移除（保留工具结构完整）。
    """
    if not isinstance(tools, list) or not tools:
        return tools, count_tokens(_tools_payload(tools)), ""
    import copy

    work = copy.deepcopy(tools)
    before = count_tokens(_tools_payload(work))

    # ① 缩短描述：先砍到首句，再整体清空
    for mode in ("first_sentence", "empty"):
        for item in work:
            if isinstance(item, dict):
                fn = item.get("function") if isinstance(item.get("function"), dict) else item
                desc = str(fn.get("description") or "")
                if not desc:
                    continue
                if mode == "first_sentence":
                    cut = re.split(r"[。；;\n]", desc, maxsplit=1)[0]
                    fn["description"] = cut
                else:
                    fn["description"] = ""
        now = count_tokens(_tools_payload(work))
        if before - now >= need_cut:
            return work, now, f"工具描述已缩短 {before}→{now}"

    # ② 整项移除（从末尾开始）：判据用"还差多少"，不能用最开始的差值
    saved = before - count_tokens(_tools_payload(work))
    removed = 0
    while len(work) > 1 and saved < need_cut:
        work.pop()
        removed += 1
        saved = before - count_tokens(_tools_payload(work))
    now = count_tokens(_tools_payload(work))
    note = (f"工具定义过大，已移除 {removed} 个工具 {before}→{now}"
            if removed else "")
    return work, now, note


def trim_to_tokens(text: str, budget: int, *, keep: str = "head") -> str:
    """按 token 截断到预算内；`keep="tail"` 保留末尾（历史要留最近的）。"""
    content = str(text or "")
    if budget <= 0:
        return ""
    if count_tokens(content) <= budget:
        return content
    marker = TRIM_MARKER
    # 二分找最长的可容纳前缀/后缀，避免按字符粗暴切导致中文乱码（不会乱码，但长度不准）
    lo, hi = 0, len(content)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        piece = content[:mid] if keep == "head" else content[-mid:]
        if count_tokens(piece + marker) <= budget:
            lo = mid
        else:
            hi = mid - 1
    piece = content[:lo] if keep == "head" else content[-lo:]
    if keep == "head":
        return piece + marker
    return marker + piece


def output_reserve(max_output: Optional[int] = None) -> int:
    """给模型输出预留的 token：优先本次 max_tokens，其次 env，最后默认值。"""
    if max_output:
        try:
            value = int(max_output)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return max(128, _env_int("CHAT_OUTPUT_RESERVE_TOKENS", DEFAULT_OUTPUT_RESERVE))


def request_budget(window: Optional[int] = None,
                   max_output: Optional[int] = None) -> int:
    """扣掉输出预留后，输入侧可用的总预算。"""
    win = window or context_window()
    return max(256, int(win) - output_reserve(max_output))


def fit_request(
    *,
    system: str = "",
    question: str = "",
    rag: str = "",
    history: Any = None,
    summary: str = "",
    tools: Any = "",
    tool_results: Optional[Sequence[str]] = None,
    max_output: Optional[int] = None,
    window: Optional[int] = None,
) -> Dict[str, Any]:
    """整包请求预算：按优先级压缩，保证总输入不超过预算。

    安全约束（2026-10-28 收口，违反任何一条都会把请求"裁坏"）：
      - **不截断**系统提示（里面是权限/硬性规则等协议内容，截断等于改指令）；
      - **不截断**工具定义的 JSON/schema、工具名、必需参数；只允许**缩短描述**
        或**整项移除**工具；
      - **不丢**工具结果条目（只截断内容），保证 tool_call / tool_result 配对；
      - **不裁剪**当前问题。
    可压缩的只有：工具描述 → 整项工具 → 工具结果内容 → RAG → 历史（整条丢最早的）→ 摘要。
    若连"必要协议 + 当前问题"本身就超出窗口，则置 `too_long=True`，
    由调用方返回明确的"输入过长"提示，**绝不强行发送被裁坏的请求**。
    """
    budget = request_budget(window, max_output)
    actions: List[str] = []

    # 系统提示与当前问题属于"必要协议/输入"：只计量，不裁剪
    system_tokens = count_tokens(system)
    question_tokens = count_tokens(question)
    fixed_floor = system_tokens + question_tokens
    if fixed_floor >= budget:
        return {
            "system": system, "question": question, "rag": "", "history":
                [] if isinstance(history, list) else "", "summary": "",
            "tools": tools if not isinstance(tools, list) else list(tools),
            "tool_results": [],
            "tokens": {"budget": budget, "system": system_tokens,
                       "question": question_tokens, "tools": 0,
                       "tool_results": 0, "rag": 0, "summary": 0, "history": 0,
                       "total": fixed_floor,
                       "output_reserve": output_reserve(max_output)},
            "actions": ["必要协议与当前问题已超出窗口，未做任何裁剪"],
            "too_long": True,
            "reason": "系统提示与当前问题合计已超出可用预算，无法在不破坏协议的前提下压缩",
        }

    tools_out = tools
    tools_tokens = count_tokens(_tools_payload(tools))

    # 1. 工具结果：累计份额上限
    results = list(tool_results or [])
    result_cap = max(256, int(budget * TOOL_RESULT_SHARE))
    kept_results: List[str] = []
    used_results = 0
    for item in results:
        if used_results >= result_cap:
            kept_results.append("（该工具结果已超出本轮上下文预算，未放入）")
            continue
        piece = trim_to_tokens(item, result_cap - used_results)
        kept_results.append(piece)
        used_results += count_tokens(piece)
    if any(a != b for a, b in zip(results, kept_results)):
        actions.append(f"工具结果累计 {used_results}/{result_cap}")

    # 2. RAG
    fixed = system_tokens + question_tokens + tools_tokens + used_results
    rag_cap = max(0, min(int(budget * RAG_SHARE), budget - fixed))
    rag_out = trim_to_tokens(rag, rag_cap) if rag else ""
    if rag and count_tokens(rag) > rag_cap:
        actions.append(f"RAG 片段 {count_tokens(rag)}→{count_tokens(rag_out)}")
    rag_tokens = count_tokens(rag_out)

    # 3./4. 历史与摘要
    remaining = max(MIN_RECENT_TOKENS,
                    budget - system_tokens - question_tokens - tools_tokens
                    - used_results - rag_tokens)
    summary_out = trim_to_tokens(summary, min(summary_budget(), remaining)) if summary else ""
    summary_tokens = count_tokens(summary_out)

    if isinstance(history, list):
        kept, dropped = select_recent(history, max(0, remaining - summary_tokens))
        if dropped:
            actions.append(f"折叠历史 {len(dropped)} 条")
        history_out: Any = kept
        history_tokens = count_messages_tokens(kept)
    else:
        history_text = str(history or "")
        history_out = trim_to_tokens(history_text, max(0, remaining - summary_tokens),
                                     keep="tail")
        if history_text and count_tokens(history_text) > count_tokens(history_out):
            actions.append(f"历史文本 {count_tokens(history_text)}→"
                           f"{count_tokens(history_out)}")
        history_tokens = count_tokens(history_out)

    total = (system_tokens + question_tokens + tools_tokens + used_results
             + rag_tokens + summary_tokens + history_tokens)

    # 兜底钳制：各段独立配额仍可能合计超预算（例如摘要占了历史的余量）。
    # 按**同一优先级反序**继续让位：历史 → 摘要 → RAG → 系统提示，直到落回预算内。
    overflow = total - budget

    def _shrink_text(value: str, cut: int, keep: str = "head") -> str:
        target = max(0, count_tokens(value) - cut)
        return trim_to_tokens(value, target, keep=keep)

    if overflow > 0 and history_tokens > 0:
        if isinstance(history_out, list):
            kept, dropped = select_recent(history_out, max(0, history_tokens - overflow))
            before = history_tokens
            history_out = kept
            history_tokens = count_messages_tokens(kept)
            if history_tokens < before:
                actions.append(f"兜底压缩历史 {before}→{history_tokens}")
                overflow -= before - history_tokens
        else:
            before = history_tokens
            history_out = _shrink_text(str(history_out), overflow, keep="tail")
            history_tokens = count_tokens(history_out)
            overflow -= before - history_tokens

    if overflow > 0 and summary_tokens > 0:
        before = summary_tokens
        summary_out = _shrink_text(summary_out, overflow)
        summary_tokens = count_tokens(summary_out)
        overflow -= before - summary_tokens

    if overflow > 0 and rag_tokens > 0:
        before = rag_tokens
        rag_out = _shrink_text(rag_out, overflow)
        rag_tokens = count_tokens(rag_out)
        overflow -= before - rag_tokens

    # 系统提示在兜底阶段**仍然不动**：里面是权限与硬性规则，截断会改掉指令。

    # 工具定义：只做两件安全的事——① 缩短描述 ② 整项移除工具。
    # 绝不截断 JSON/schema/工具名/必需参数（那样会产出一个坏请求）。
    if overflow > 0 and tools_tokens > 0:
        before = tools_tokens
        tools_out, tools_tokens, note = _reduce_tools(tools, overflow)
        if note:
            actions.append(note)
            overflow -= max(0, before - tools_tokens)

    if overflow > 0 and used_results > 0:
        before = used_results
        kept_results = [_shrink_text(item, max(1, overflow // max(1, len(kept_results))))
                        for item in kept_results]
        used_results = sum(count_tokens(item) for item in kept_results)
        actions.append(f"工具结果兜底压缩 {before}→{used_results}")
        overflow -= before - used_results

    total = (system_tokens + question_tokens + tools_tokens + used_results
             + rag_tokens + summary_tokens + history_tokens)

    # 安全降级做到底仍超预算：不再继续裁（会裁坏协议），改由调用方明确拒绝。
    too_long = total > budget
    if too_long:
        actions.append("已用尽安全压缩手段仍超预算，拒绝发送被裁坏的请求")

    return {
        "system": system,
        "question": question,
        "rag": rag_out,
        "history": history_out,
        "summary": summary_out,
        "tools": tools_out,
        "tool_results": kept_results,
        "tokens": {
            "budget": budget,
            "system": system_tokens,
            "question": question_tokens,
            "tools": tools_tokens,
            "tool_results": used_results,
            "rag": rag_tokens,
            "summary": summary_tokens,
            "history": history_tokens,
            "total": total,
            "output_reserve": output_reserve(max_output),
        },
        "actions": actions,
        "too_long": too_long,
    }


def finalize_summary(text: str, *, max_tokens: Optional[int] = None) -> str:
    """按 token 上限收尾：命中 95% 闸值时裁掉最后半句。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    cap = max_tokens if max_tokens is not None else summary_budget()
    if count_tokens(cleaned) >= int(cap * TRUNCATION_GUARD_RATIO):
        cleaned = trim_incomplete_tail(cleaned)
    return cleaned


__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "MAX_HISTORY_PLAN_TOKENS",
    "HISTORY_BUDGET_RATIO",
    "SUMMARY_RATIO",
    "RECENT_RATIO",
    "IMAGE_TOKEN_ESTIMATE",
    "context_window",
    "history_budget",
    "recent_budget",
    "summary_budget",
    "max_turns",
    "summary_enabled",
    "count_tokens",
    "count_message_tokens",
    "count_messages_tokens",
    "select_recent",
    "format_messages",
    "trim_incomplete_tail",
    "should_summarize",
    "build_summary_prompt",
    "finalize_summary",
    # 请求总预算（2026-10-28）
    "output_reserve",
    "request_budget",
    "trim_to_tokens",
    "fit_request",
]
