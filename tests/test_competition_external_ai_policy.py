import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import require_teacher
from api.routes import assistant, competition

FOLLOWUP = {"student_id": "S1", "quiz_id": "N1", "indicator_id": "I1"}
PROMPT = {key: "合成提示" for key in ("goal", "hint", "if_confirmed", "if_unclear")}
DRAFT = {key: "合成讲评内容" for key in ("start", "teach", "check")}
SCOPE = {"class_id": "C1", "quiz_id": "N1"}
CASES = [
    ("agent/chat", {"message": "哪道题失分最多？", "scope": SCOPE}, None),
    ("analyze-answer", {"stem": "合成题", "answer": "1", "score": 0, "full_score": 1}, "合成答卷分析"),
    ("ai-diagnosis", {"student_label": "S1", "quiz_label": "N1"}, "合成诊断"),
    ("ai-class-report", {"quiz_label": "N1"}, "合成班级诊断"),
    ("prep-prompt", {**SCOPE, "record_id": "SYN-1", "question_id": "Q1"}, "请补写这一步。"),
    ("follow-up/draft", {**FOLLOWUP, "question_ids": ["Q1"], "prompt": PROMPT}, PROMPT),
    ("follow-up/explain", {**FOLLOWUP, "question_id": "Q1", "result_kind": "correct"}, {"explanation": "合成说明"}),
    ("review-draft", {"quiz_id": "N1", "question_id": "Q1", "material_id": "B01", "pdf_page": 1}, DRAFT),
    ("ai-questions", {**FOLLOWUP, "indicator_name": "合成考点"}, [{"stem": "合成题", "answer": "1"}]),
    ("ai-lesson-plan", {"quiz_label": "N1", "class_label": "C1", "theme": "合成主题"}, {"stages": []}),
    ("ai-paper", {**SCOPE, "quiz_label": "N1", "class_label": "C1"}, {"sections": []}),
]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    imports = tmp_path / "imports"
    official = tmp_path / "official-bundle"
    evaluation = imports / "eval-12345678" / "bundle"
    monkeypatch.setattr(competition, "_DEFAULT_PRIVATE_DIR", official)
    monkeypatch.setenv("COMPETITION_OFFICIAL_BUNDLE_DIR", str(official))
    monkeypatch.setenv("COMPETITION_IMPORT_ROOT", str(imports))
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path / "runtime"))
    answer = {"id": "synthetic", "sourceRecordId": "SYN-1", "studentId": "S1", "quizId": "N1", "questionId": "Q1",
              "score": 0, "fullScore": 1}
    data = {"manifest": {"independentEvaluation": True}, "students": [{"id": "S1", "classId": "C1"}],
            "classes": [{"id": "C1"}], "quizzes": [{"id": "N1", "availableItems": ["Q1"]}],
            "questions": [{"id": "Q1", "stem": "合成题", "kpId": "I1"}], "answers": [answer]}
    evaluation.mkdir(parents=True)
    official.mkdir()
    for name, content in data.items():
        (evaluation / f"{name}.json").write_text(json.dumps(content), encoding="utf-8")
    (official / "answers.json").write_text(json.dumps([dict(answer, sourceRecordId="RESERVED")]), encoding="utf-8")
    test_app = FastAPI()
    test_app.include_router(competition.router, prefix="/api")
    test_app.include_router(assistant.router, prefix="/api")
    test_app.dependency_overrides[require_teacher] = lambda: "policy-test-teacher"
    return TestClient(test_app), evaluation


def payload(route, body, dataset):
    if route == "agent/chat":
        return {**body, "scope": {**body["scope"], "dataset": dataset}}
    return {**body, "dataset": dataset}


@pytest.mark.parametrize(("route", "body", "_"), CASES)
def test_official_and_legacy_empty_scope_cannot_reach_provider(route, body, _, workspace, monkeypatch):
    import llm

    model = Mock(side_effect=AssertionError("Protected data reached get_llm"))
    monkeypatch.setattr(llm, "get_llm", model)
    client, _ = workspace
    for dataset in ("official", ""):
        response = client.post(f"/api/competition/{route}", json=payload(route, body, dataset))
        # A blank assistant scope keeps its existing actionable scope error.
        assert response.status_code == (400 if route == "agent/chat" and not dataset else 403), response.text
    model.assert_not_called()


@pytest.mark.parametrize(("route", "body", "reply"), CASES)
def test_imported_synthetic_scope_can_reach_mock_provider(route, body, reply, workspace, monkeypatch):
    import llm

    client, _ = workspace
    call = {"id": "t1", "function": {"name": "read_class", "arguments": "{}"}}
    provider = Mock()
    provider.invoke.return_value = SimpleNamespace(
        content=reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False))
    provider.provider.chat_with_tools.side_effect = [
        {"tool_calls": [call], "raw_message": {"role": "assistant", "content": None, "tool_calls": [call]}},
        {"answer": "合成数据：Q1 有 1 人失分。"},
    ]
    get_model = Mock(return_value=provider)
    monkeypatch.setattr(llm, "get_llm", get_model)
    monkeypatch.setattr(competition, "_followup_diagnosis", lambda _: {"facts": ["合成事实"]})
    monkeypatch.setattr(competition, "_validate_followup", lambda *_: None)
    monkeypatch.setattr(competition, "_followup_explain_evidence", lambda *_: {"本次规则核对": "合成证据"})
    monkeypatch.setattr(competition, "_review_evidence", lambda *_: {"原题": "合成题", "原答卷": None})
    response = client.post(f"/api/competition/{route}", json=payload(route, body, "eval-12345678"))
    assert response.status_code == 200, response.text
    get_model.assert_called_once()
    if route == "agent/chat":
        assert response.json()["data_reads"][0]["dataset"] == "eval-12345678"
        assert provider.provider.chat_with_tools.call_count == 2
    else:
        provider.invoke.assert_called_once()


def test_imported_copy_of_official_record_is_also_protected(workspace, monkeypatch):
    import llm

    client, evaluation = workspace
    model = Mock(side_effect=AssertionError("Official reimport reached the model"))
    monkeypatch.setattr(llm, "get_llm", model)
    (evaluation / "answers.json").write_bytes((competition._bundle_dir() / "answers.json").read_bytes())
    response = client.post("/api/competition/agent/chat", json=payload(*CASES[0][:2], "eval-12345678"))
    assert response.status_code == 403
    assert "本地规则分析" in response.json()["detail"]
    model.assert_not_called()
