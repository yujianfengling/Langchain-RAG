"""上下文预算模块的纯函数测试（2026-10-28）。"""

from __future__ import annotations

import services.context_budget as cb


def test_history_budget_ratio_and_cap(monkeypatch):
    monkeypatch.setenv("CHAT_CONTEXT_WINDOW_TOKENS", "100000")
    total = cb.history_budget()
    assert total == 35000
    # 超大窗口不能把历史预算放大到失控
    monkeypatch.setenv("CHAT_CONTEXT_WINDOW_TOKENS", "10000000")
    assert cb.history_budget() == cb.MAX_HISTORY_PLAN_TOKENS


def test_recent_and_summary_split(monkeypatch):
    monkeypatch.setenv("CHAT_CONTEXT_WINDOW_TOKENS", "100000")
    total = cb.history_budget()
    assert cb.recent_budget(total) == int(total * cb.RECENT_RATIO)
    assert cb.summary_budget(total, max_output=800) == 800


def test_select_recent_keeps_newest_and_starts_with_user():
    messages = []
    for i in range(20):
        messages.append({"role": "user", "content": f"问题{i}"})
        messages.append({"role": "assistant", "content": f"回答{i}"})
    kept, dropped = cb.select_recent(messages, budget=40, max_turn_limit=30)
    assert kept, "至少要保留一条消息"
    assert kept[0]["role"] == "user"
    assert kept[-1]["content"] == "回答19"
    assert dropped
    assert len(kept) + len(dropped) == len(messages)


def test_select_recent_respects_turn_limit():
    messages = []
    for i in range(50):
        messages.append({"role": "user", "content": f"q{i}"})
        messages.append({"role": "assistant", "content": f"a{i}"})
    kept, _ = cb.select_recent(messages, budget=10**9, max_turn_limit=5)
    assert len(kept) == 10
    assert kept[0]["content"] == "q45"


def test_select_recent_single_huge_message_still_kept():
    messages = [
        {"role": "user", "content": "x" * 100000},
        {"role": "assistant", "content": "a" * 100000},
    ]
    kept, _ = cb.select_recent(messages, budget=1, max_turn_limit=30)
    assert len(kept) == 1
    assert kept[0]["role"] == "assistant"


def test_count_image_tokens():
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
        ],
    }
    assert cb.count_message_tokens(message) >= cb.IMAGE_TOKEN_ESTIMATE


def test_trim_incomplete_tail():
    assert cb.trim_incomplete_tail("第一行\n第二行\n半句") == "第一行\n第二行"
    assert cb.trim_incomplete_tail("单行") == "单行"


def test_should_summarize_floor():
    assert cb.should_summarize([]) is False
    assert cb.should_summarize([{"role": "user", "content": "短"}]) is False
    long_dropped = [{"role": "user", "content": "x" * 4000}]
    assert cb.should_summarize(long_dropped) is True


def test_format_messages():
    text = cb.format_messages([
        {"role": "user", "content": "什么是 B+ 树"},
        {"role": "assistant", "content": "一种索引结构"},
    ])
    assert "学生：什么是 B+ 树" in text
    assert "老师：一种索引结构" in text


def test_finalize_summary_truncates_at_guard(monkeypatch):
    monkeypatch.setenv("CHAT_SUMMARY_MAX_TOKENS", "100")
    text = "第一段\n" + ("很长的内容" * 200) + "\n半句"
    out = cb.finalize_summary(text, max_tokens=100)
    assert out
    assert not out.endswith("半句")
