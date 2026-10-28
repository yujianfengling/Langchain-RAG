"""请求总预算（2026-10-28）

覆盖：系统提示、当前问题、历史、摘要、RAG、工具定义、累计工具结果，并预留输出。
压缩优先级、工具结果配对、当前问题不被裁剪都必须锁死。
"""
from __future__ import annotations

from services import context_budget as cb


def test_output_reserved_before_input():
    """先扣输出预留，才有输入预算；max_tokens 优先于 env。"""
    assert cb.output_reserve(700) == 700
    assert cb.request_budget(window=10000, max_output=2000) == 8000


def test_total_stays_within_budget_everything_huge():
    fit = cb.fit_request(
        system="系统" * 5000,
        question="请解释第三范式的判断步骤？",
        rag="证据" * 20000,
        history="历史" * 20000,
        summary="摘要" * 5000,
        tools="工具定义" * 2000,
        tool_results=["结果" * 5000 for _ in range(6)],
        window=4000, max_output=500,
    )
    tokens = fit["tokens"]
    assert tokens["total"] <= tokens["budget"] or fit.get("too_long")
    assert tokens["output_reserve"] == 500
    # 当前问题永远完整保留
    assert fit["question"] == "请解释第三范式的判断步骤？"


def test_current_question_never_trimmed():
    fit = cb.fit_request(question="这是一个很长很长的问题" * 200,
                         system="", window=1000, max_output=100)
    assert fit["question"] == "这是一个很长很长的问题" * 200


def test_tool_results_capped_but_pairing_preserved():
    """工具结果可以被截断，但**不能少一条**（否则 tool_call/result 配对断裂）。"""
    fit = cb.fit_request(question="q", tool_results=["x" * 3000 for _ in range(5)],
                         window=4000, max_output=100)
    assert len(fit["tool_results"]) == 5
    assert fit["tokens"]["tool_results"] <= int(fit["tokens"]["budget"] * cb.TOOL_RESULT_SHARE)


def test_history_list_drops_oldest_keeps_recent():
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"第{i}条" * 300}
            for i in range(20)]
    fit = cb.fit_request(question="q", history=msgs, window=3000, max_output=100)
    kept = fit["history"]
    assert len(kept) < len(msgs)
    assert kept[-1]["content"].startswith("第19条")


def test_history_text_keeps_tail():
    text = "早期内容。" * 500 + "最新内容。"
    fit = cb.fit_request(question="q", history=text, window=800, max_output=100)
    assert fit["history"].endswith("最新内容。")


def test_system_prompt_never_trimmed_even_when_huge():
    """2026-10-28 收口：系统提示含权限与硬性规则，截断会改掉指令，因此绝不裁剪。
    规模受限时改为由调用方拒绝请求（too_long）。"""
    small = "系统提示" * 10
    assert cb.fit_request(question="q", system=small, window=10000)["system"] == small
    huge = "系统提示" * 5000
    fit = cb.fit_request(question="q", system=huge, window=4000, max_output=100)
    assert fit["system"] == huge


def test_actions_recorded_when_trimmed():
    fit = cb.fit_request(question="q", rag="证据" * 20000, window=2000, max_output=100)
    assert fit["actions"], "发生压缩时应记录动作，便于排查"


def _tool(name: str, desc: str):
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object",
                                        "properties": {"k": {"type": "string"}},
                                        "required": ["k"]}}}


def test_tool_definitions_never_truncated_only_descriptions_shortened():
    """工具定义只允许缩短描述或整项移除：JSON/schema/工具名/必需参数必须完整。"""
    tools = [_tool("read_source", "读取资料正文。" * 300),
             _tool("read_memory", "读取记忆。" * 300)]
    fit = cb.fit_request(system="规则", question="问题？", tools=tools,
                         window=1500, max_output=100)
    kept = fit["tools"]
    assert isinstance(kept, list)
    for item in kept:
        fn = item.get("function", item)
        assert fn.get("name") in ("read_source", "read_memory")
        assert fn.get("parameters", {}).get("required") == ["k"], "必需参数被改坏了"
        assert fn["parameters"]["properties"]["k"]["type"] == "string", "schema 被改坏了"
    assert any("工具描述已缩短" in a for a in fit["actions"])


def test_tool_definitions_dropped_whole_when_descriptions_not_enough():
    """描述清空后仍超预算时，整项移除工具（schema/工具名/必需参数始终不动）。"""
    tools = []
    for i in range(30):
        item = _tool(f"tool_{i}", "")
        # schema 很大但不可裁剪：只能通过"整项移除"来让位
        item["function"]["parameters"]["properties"][f"long_param_{i}" * 20] = {
            "type": "string"}
        tools.append(item)
    fit = cb.fit_request(system="规则", question="问题？", tools=tools,
                         window=1500, max_output=100)
    kept = fit["tools"]
    assert isinstance(kept, list) and len(kept) < len(tools)
    for item in kept:
        fn = item.get("function", item)
        assert fn.get("name", "").startswith("tool_")
        assert fn.get("parameters", {}).get("required") == ["k"], "必需参数被改坏了"
    assert any("移除" in a for a in fit["actions"]), f"未记录移除动作: {fit['actions']}"


def test_system_prompt_is_never_truncated():
    """系统提示里是权限与硬性规则，截断等于改指令，绝不允许。"""
    system = "硬性规则：不得越权访问他人资料。" * 400
    fit = cb.fit_request(system=system, question="问题？", rag="证据" * 20000,
                         window=2000, max_output=100)
    assert fit["system"] == system


def test_protocol_plus_question_over_window_returns_too_long():
    """必要协议 + 当前问题本身就超窗：明确置 too_long，不裁坏请求。"""
    system = "规则" * 5000
    question = "问题" * 5000
    fit = cb.fit_request(system=system, question=question, window=800, max_output=100)
    assert fit.get("too_long") is True
    assert fit["question"] == question, "当前问题不能被裁剪"
    assert fit["system"] == system, "系统指令不能被裁剪"
    assert fit["rag"] == "" and not fit["tool_results"]
