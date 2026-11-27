import asyncio

import pytest
from fastapi import HTTPException

from api.routes import assistant


def test_assistant_statistics_stay_bound_to_selected_scope(monkeypatch):
    def answer(student, quiz, item, score, maximum):
        return dict(studentId=student, quizId=quiz, questionId=item, score=score, fullScore=maximum)

    def indicator(quiz, group, rate):
        return dict(id=f"{quiz}-I1", quizId=quiz, indicatorId="I1", name="同类项",
                    stats=[dict(classId=group, scoreRate=rate)])

    documents = {
        "students.json": [dict(id="S1", classId="C1"), dict(id="S2", classId="C2"), dict(id="S3", classId="C2")],
        "answers.json": [
            answer("S1", "N2", "Q1", 0, 9), answer("S2", "N1", "Q1", 0, 8),
            answer("S2", "N2", "Q1", 0, 2), answer("S2", "N2", "Q1", 1, 2),
            answer("S3", "N2", "Q2", 5, 5),
        ],
        "kps.json": [indicator("N1", "C2", 0), indicator("N2", "C1", 0.1), indicator("N2", "C2", 0.55)],
    }

    def rows(dataset, filename):
        assert dataset == "eval-12345678"
        return documents.get(filename)

    monkeypatch.setattr(assistant, "_rows", rows)
    scope = assistant.ChatScope(dataset="eval-12345678", quiz_id="N2", class_id="C2")
    state = {"scope": scope}
    misleading_args = {"dataset": "official", "class_id": "C1", "quiz_id": "N1"}
    summary = assistant._dispatch("read_class", misleading_args, state, "teacher")
    assert (summary["class"], summary["quiz"], summary["score_sum"], summary["full_sum"]) == ("C2", "N2", 6, 9)
    assert summary["mean_rate"] == 66.7
    assert summary["weakest"] == [{"indicator": "I1", "name": "同类项", "rate": 55.0}]
    assert [(item["item"], item["loss_count"]) for item in summary["most_loss"]] == [("Q1", 1)]
    items = assistant._dispatch("read_exam", misleading_args, state, "teacher")["items"]
    assert [(item["item"], item["loss_count"], item["n"]) for item in items] == [("Q1", 1, 1), ("Q2", 0, 1)]


def test_assistant_missing_scope_reminds_teacher_to_select_it():
    request = assistant.ChatRequest(message="这次哪道题失分人数最多")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(assistant.agent_chat(request, "teacher"))
    assert exc.value.status_code == 400
    assert "选择班级和测验" in exc.value.detail
