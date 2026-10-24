"""引导模式（socratic）回归测试（2026-10-24）

约束：
- direct 是默认值；旧会话 / 旧客户端不传 guidance_mode 时，提示词与行为必须保持不变；
- socratic 只影响提示词与事件字段，不改能力、工具权限、判分与掌握度；
- 非法值一律回落到 direct，绝不抛 500。
"""

import uuid

import pytest
from pydantic import ValidationError

from api.schemas.tutor_schema import TutorSessionUpdate, TutorStreamRequest
from services import tutor_agent, tutor_session


def _ids():
    tag = uuid.uuid4().hex[:8]
    return "soc_user_" + tag, "soc_sub_" + tag


def test_default_guidance_mode_is_direct():
    ctx = tutor_session.default_context("u", "s")
    assert ctx["guidance_mode"] == tutor_session.GUIDANCE_DIRECT


def test_guidance_mode_persists_and_invalid_falls_back():
    user, subject = _ids()
    tutor_session.update_context(user, subject, guidance_mode="socratic")
    assert tutor_session.load_context(user, subject)["guidance_mode"] == "socratic"

    # 历史脏数据：读取时回落，不能把非法值传给提示词
    ctx = tutor_session.load_context(user, subject)
    ctx["guidance_mode"] = "weird"
    tutor_session.save_context(user, subject, ctx)
    assert tutor_session.load_context(user, subject)["guidance_mode"] == "direct"

    # 非法 patch 也回落，不抛异常
    tutor_session.update_context(user, subject, guidance_mode="weird")
    assert tutor_session.load_context(user, subject)["guidance_mode"] == "direct"


def test_prompt_keeps_direct_behavior_by_default():
    prompt = tutor_agent.build_system_prompt(
        "离散数学", "chat", {"label": "全部资料", "mode": "all"}, {"subject_id": "s"})
    assert "先给结论再给依据" in prompt
    assert "引导模式（socratic" not in prompt


def test_prompt_adds_socratic_rules_after_common_rules():
    prompt = tutor_agent.build_system_prompt(
        "离散数学", "chat", {"label": "全部资料", "mode": "all"},
        {"subject_id": "s"}, guidance_mode="socratic")
    assert "引导模式（socratic" in prompt
    assert "不要给出最终答案" in prompt
    # 必须放在通用规则之后，才能覆盖「结论先行」的默认风格
    assert prompt.index("引导模式（socratic") > prompt.index("先给结论再给依据")


def test_socratic_rules_not_applied_to_map_or_organize():
    for ability in ("map", "organize"):
        prompt = tutor_agent.build_system_prompt(
            "离散数学", ability, {"label": "全部资料", "mode": "all"},
            {"subject_id": "s"}, guidance_mode="socratic")
        assert "引导模式（socratic" not in prompt, f"{ability} 不应被引导规则干扰"


def test_prompt_unknown_mode_falls_back_to_direct():
    prompt = tutor_agent.build_system_prompt(
        "离散数学", "chat", {"label": "全部资料", "mode": "all"},
        {"subject_id": "s"}, guidance_mode="hack")
    assert "引导模式（socratic" not in prompt


def test_request_schema_rejects_unknown_mode():
    req = TutorStreamRequest(message="x", subject_id="s", guidance_mode="socratic")
    assert req.guidance_mode == "socratic"
    with pytest.raises(ValidationError):
        TutorStreamRequest(message="x", subject_id="s", guidance_mode="hack")
    upd = TutorSessionUpdate(subject_id="s", guidance_mode="socratic")
    assert upd.guidance_mode == "socratic"


def test_stream_turn_passes_mode_to_prompt_and_events(monkeypatch):
    user, subject = _ids()
    captured = {}

    class FakeProvider:
        def stream_with_tools(self, messages, **kwargs):
            captured["system"] = messages[0]["content"]
            captured["messages"] = messages
            yield {"type": "end", "content": "先说说你卡在哪一步？", "tool_calls": []}

    monkeypatch.setattr(
        tutor_agent.material_scope, "build_scope",
        lambda *a, **k: {"mode": "all", "label": "全部资料", "selected": [], "invalid": []})
    monkeypatch.setattr(
        tutor_agent.material_scope, "scope_to_public",
        lambda s: {"mode": s.get("mode", "all"), "label": s.get("label", ""),
                   "selected": [], "invalid": []})
    monkeypatch.setattr("services.model_service.get_model_provider",
                        lambda model=None: FakeProvider())

    events = list(tutor_agent.stream_turn(
        user, subject, "这题怎么做", guidance_mode="socratic"))
    assert "引导模式（socratic" in captured["system"]
    start = next(e for e in events if e["type"] == "start")
    assert start["guidance_mode"] == "socratic"
    assert start["conversation_id"] == f"socratic:{subject}"
    done = next(e for e in events if e["type"] == "done")
    assert done["guidance_mode"] == "socratic"

    # 下一轮不传 mode：沿用会话设置，而不是悄悄回到 direct；
    # 同时要带上上一轮，否则学生说“我还是不会”时模型不知道在说什么题。
    captured.clear()
    list(tutor_agent.stream_turn(user, subject, "我还是不会"))
    assert "引导模式（socratic" in captured["system"]
    assert any(m.get("role") == "user" and m.get("content") == "这题怎么做"
               for m in captured["messages"][1:])
