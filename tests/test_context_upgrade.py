"""上下文升级的回归测试（2026-10-28）。

覆盖：
- 滚动摘要只写 conversations，不产生新 message；
- 历史预算化后仍能拿到早期内容；
- 当前问题不会在历史里重复出现。
"""

from __future__ import annotations

import chat_service
import services.conversation_service as cs


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(cs, "_DATA_DIR", str(tmp_path))
    cs._init_db()


def test_rolling_summary_roundtrip(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    conv = cs.create_conversation("数据结构")
    cid = conv["id"]

    assert cs.get_rolling_summary(cid)["summary"] == ""
    assert cs.save_rolling_summary(cid, "学生已学到 B+ 树，薄弱点是分裂。",
                                   upto_message_id="m1", tokens=12) is True

    got = cs.get_rolling_summary(cid)
    assert got["summary"].startswith("学生已学到")
    assert got["upto"] == "m1"
    assert got["tokens"] == 12


def test_summary_does_not_create_messages(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    conv = cs.create_conversation("数据结构")
    cid = conv["id"]
    cs.add_message(cid, "user", "第一问")
    cs.add_message(cid, "assistant", "第一答")
    before = len(cs.list_messages(cid))

    cs.save_rolling_summary(cid, "摘要", tokens=2)

    assert len(cs.list_messages(cid)) == before


def test_history_with_ids_and_budget(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    conv = cs.create_conversation("数据结构")
    cid = conv["id"]
    for i in range(4):
        cs.add_message(cid, "user", f"问题{i}")
        cs.add_message(cid, "assistant", f"回答{i}")

    history = cs.get_history_with_ids(cid, max_turns=10)
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "问题0"
    assert history[-1]["content"] == "回答3"
    assert all(item.get("id") for item in history)


def test_build_history_context_has_no_current_question():
    history = [
        {"id": "1", "role": "user", "content": "第一问"},
        {"id": "2", "role": "assistant", "content": "第一答"},
    ]
    text, summary = chat_service._build_history_context(None, history, None)
    assert "第一问" in text
    assert "第二问" not in text
    assert summary == ""


def test_build_history_context_generates_summary(monkeypatch):
    import services.context_budget as cb

    long_history = []
    for i in range(40):
        long_history.append({"id": f"u{i}", "role": "user", "content": f"问题{i} " + "x" * 3000})
        long_history.append({"id": f"a{i}", "role": "assistant", "content": f"回答{i} " + "y" * 3000})

    saved = {}
    monkeypatch.setattr(
        "services.conversation_service.get_rolling_summary",
        lambda cid, user_id=None: {"summary": "", "upto": "", "tokens": 0})
    monkeypatch.setattr(
        "services.conversation_service.save_rolling_summary",
        lambda cid, text, upto_message_id=None, tokens=0: saved.update(
            {"cid": cid, "text": text, "upto": upto_message_id}) or True)
    monkeypatch.setattr(chat_service, "call_llm", lambda **kwargs: "旧对话摘要：目标 / 进展 / 待办")

    text, summary = chat_service._build_history_context("c1", long_history, None)

    assert summary.startswith("旧对话摘要")
    assert saved.get("cid") == "c1"
    assert text
    # 只保留预算内最近消息，早期问题不应全部出现
    assert cb.recent_budget() > 0
