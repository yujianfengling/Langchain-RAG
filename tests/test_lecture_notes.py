"""课堂录音笔记回归测试（2026-10-24）

- ASR 结果 → 结构化笔记 → 落库/列表/详情；
- 模型整理失败时保留转写与兜底提纲，不丢整段课堂；
- 用户隔离；ASR 空结果如实报错。
"""

import json
import uuid

import pytest

from services import lecture_service


def _subject():
    tag = uuid.uuid4().hex[:8]
    return "lec_u_" + tag, "lec_s_" + tag


def test_create_list_get_note(monkeypatch):
    user, subject = _subject()
    monkeypatch.setattr(
        "services.audio_service.transcribe_audio_detailed",
        lambda data, filename=None: {"text": "今天讲了二叉树的遍历。前序是根左右。",
                                     "service": "local", "engine": "funasr"})
    monkeypatch.setattr(
        lecture_service, "call_llm",
        lambda *a, **k: json.dumps({
            "title": "二叉树遍历",
            "outline": ["前序：根左右", "中序：左根右"],
            "key_points": ["三种遍历顺序"],
            "questions": ["后序怎么记"],
            "follow_ups": ["画一棵树练一遍"],
        }, ensure_ascii=False))

    note = lecture_service.create_note(user, subject, b"fake-audio", "lecture.wav")
    assert note["title"] == "二叉树遍历"
    assert note["summary"]["ai_generated"] is True
    assert note["transcript_chars"] > 0
    assert note["asr_service"] == "local"

    listed = lecture_service.list_notes(user, subject)
    assert len(listed) == 1
    assert listed[0]["id"] == note["id"]
    assert "transcript" not in listed[0], "列表不返回完整转写"

    detail = lecture_service.get_note(user, note["id"])
    assert detail and detail["transcript"].startswith("今天讲了")
    assert lecture_service.get_note("other_user", note["id"]) is None


def test_note_aligns_to_knowledge_map(monkeypatch):
    user, subject = _subject()
    monkeypatch.setattr(
        "services.audio_service.transcribe_audio_detailed",
        lambda data, filename=None: {"text": "今天讲二叉树，也讲了前序遍历。", "service": "local"})
    monkeypatch.setattr(
        lecture_service, "call_llm",
        lambda *a, **k: json.dumps({"title": "二叉树", "outline": ["前序"], "key_points": []}))
    monkeypatch.setattr(
        "services.knowledge_map.map_with_progress",
        lambda u, s: {"nodes": [{"label": "二叉树"}, {"label": "图"}]})
    note = lecture_service.create_note(user, subject, b"audio", "lecture.wav")
    assert note["summary"]["knowledge_points"] == ["二叉树"]


def test_model_failure_keeps_fallback_note(monkeypatch):
    user, subject = _subject()
    monkeypatch.setattr(
        "services.audio_service.transcribe_audio_detailed",
        lambda data, filename=None: {"text": "第一句话。第二句话。", "service": "local"})
    monkeypatch.setattr(lecture_service, "call_llm",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model down")))

    note = lecture_service.create_note(user, subject, b"fake-audio", "lecture.wav")
    assert note["summary"]["ai_generated"] is False
    assert note["summary"]["outline"], "至少保留兜底提纲"
    assert note["transcript_chars"] > 0


def test_empty_transcript_is_reported(monkeypatch):
    user, subject = _subject()
    monkeypatch.setattr(
        "services.audio_service.transcribe_audio_detailed",
        lambda data, filename=None: {"text": "   ", "service": "local"})
    with pytest.raises(lecture_service.LectureError) as exc:
        lecture_service.create_note(user, subject, b"fake-audio", "lecture.wav")
    assert exc.value.error_type == "asr_empty"
