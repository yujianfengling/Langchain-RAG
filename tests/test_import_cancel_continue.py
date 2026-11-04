"""取消导入之后不能再显示「可以继续」。"""
import json

from services import import_service


def test_cancelled_job_with_leftover_members_cannot_continue():
    job = {
        "job_id": "imp_cancel",
        "subject_id": "sub",
        "status": "cancelled",
        "raw_dir": "D:/tmp/raw",
        "totals_json": "{}",
        "warnings_json": "[]",
        "archive_summaries_json": "[]",
        "kb_json": json.dumps({"status": "empty", "ready": False}),
        "plan_json": json.dumps([
            {"key": "a", "origin_archive": "", "source_path": "a.pptx"},
        ]),
        "paused": 0,
        "pause_requested": 0,
        "created_at": "2026-11-04T00:00:00",
        "updated_at": "2026-11-04T00:00:00",
    }
    view = import_service._job_to_public(job, [])
    assert view["status"] == "cancelled"
    assert view["can_continue"] is False
    assert view["totals"]["unprocessed"] >= 1
