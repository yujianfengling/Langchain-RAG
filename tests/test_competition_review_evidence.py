import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "competition_pipeline"))

import review_plan  # noqa: E402
from api.routes import competition  # noqa: E402


def context():
    return {
        "evidence": [],
        "scope": {"item_indicator": {"Q001": "I1"}},
        "diagnoses": [{
            "student_id": "S1", "node_id": "N01", "indicator_id": "I1",
            "coverage": {"min_evidence": 3}, "pretest_questions": [],
        }],
        "inds": ["I1"], "item_ind": {"Q001": "I1"}, "rubric_by_item": {},
        "node": "N01", "class_id": "DC01", "theme": "T01",
    }


def test_duplicate_live_record_ids_do_not_inflate_evidence():
    wrong = {"student_id": "S1", "item_id": "Q001", "kind": "mid_wrong", "record_id": "LIVE-1"}
    correct = {"student_id": "S1", "item_id": "Q001", "kind": "correct", "record_id": "LIVE-2"}
    single = review_plan.build_facts(context(), ["S1"], [wrong, correct])
    repeated = review_plan.build_facts(context(), ["S1"], [wrong, wrong, correct, correct])

    assert repeated["lost"] == single["lost"]
    assert repeated["scored_count"] == single["scored_count"]
    assert repeated["insufficient"] == single["insufficient"]


def _plan():
    return {
        "lecture_items": [], "pretest_tasks": [], "covered_students": [],
        "covered_lost_points": 0,
        "uncovered": {"lost_points": [], "insufficient_students": []},
    }


@pytest.fixture
def recompute_files(tmp_path, monkeypatch):
    src = tmp_path / "src"
    analysis = src / "analysis"
    bundle = tmp_path / "bundle"
    analysis.mkdir(parents=True)
    bundle.mkdir()
    (src / "scope_index.json").write_text(json.dumps({"item_indicator": {"Q001": "I1"}}), encoding="utf-8")
    (analysis / "diagnoses.json").write_text(json.dumps({"diagnoses": []}), encoding="utf-8")
    (analysis / "curriculum_map.json").write_text(json.dumps({
        "indicators": [{"indicator_id": "I1", "theme_id": "T01"}],
    }), encoding="utf-8")
    (bundle / "students.json").write_text(json.dumps([{"id": "S1", "classId": "DC01"}]), encoding="utf-8")
    monkeypatch.setenv("COMPETITION_OFFICIAL_SRC_DIR", str(src))
    monkeypatch.setattr(competition, "_bundle_dir", lambda: bundle)
    monkeypatch.setattr(review_plan, "generate", lambda *args, **kwargs: {
        "strategies": {"common": {"budgets": {"2": _plan()}}},
        "comparison": {"2": {"naive_plan": _plan()}},
    })
    monkeypatch.setattr(review_plan, "compare_plans", lambda before, after: {})
    return src


@pytest.mark.parametrize("extra", [{}, [None], [{"student_id": [], "item_id": "Q001", "kind": "correct"}]])
def test_recompute_rejects_malformed_evidence(recompute_files, extra):
    with pytest.raises(HTTPException) as error:
        asyncio.run(competition.review_plan_recompute({
            "node_id": "N01", "class_id": "DC01", "theme": "T01", "budget": 2,
            "strategy": "common", "extra_evidence": extra,
        }, _="teacher"))
    assert error.value.status_code == 400


def test_recompute_deduplicates_and_rejects_conflicting_record_ids(recompute_files):
    body = {
        "node_id": "N01", "class_id": "DC01", "theme": "T01", "budget": 2,
        "strategy": "common",
        "extra_evidence": [
            {"student_id": "S1", "item_id": "Q001", "kind": "correct", "record_id": "LIVE-1"},
            {"student_id": "S1", "item_id": "Q001", "kind": "correct", "record_id": "LIVE-1"},
        ],
    }
    response = asyncio.run(competition.review_plan_recompute(body, _="teacher"))
    assert len(json.loads(response.body)["session_extra_evidence"]) == 1

    body["extra_evidence"][1]["kind"] = "mid_wrong"
    with pytest.raises(HTTPException) as error:
        asyncio.run(competition.review_plan_recompute(body, _="teacher"))
    assert error.value.status_code == 400
    assert "编号冲突" in error.value.detail
