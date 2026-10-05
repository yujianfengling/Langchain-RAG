"""课程资料上传后必须重建索引（2026-10-05 回归）

此前的实现只在"知识库尚未就绪"时重建：课程已有可用知识库时，新上传的资料
写进了学科目录、资料列表显示"已就绪"，却从未进入 FAISS/BM25 索引，
导致学生基于课程资料提问永远检索不到新资料。
"""
from unittest import mock

from services import teaching_service


def test_upload_material_rebuilds_even_when_kb_already_ready():
    material = {
        "id": "m1", "course_id": "c1", "subject_id": "s1", "filename": "f.txt",
        "size_bytes": 10, "status": "ready", "error_message": None,
        "visibility": "students", "uploaded_by": "u1",
        "created_at": "2026-10-05T00:00:00", "updated_at": "2026-10-05T00:00:00",
    }
    with mock.patch.object(teaching_service.access, "require_course_teacher",
                           return_value={"subject_id": "s1"}), \
         mock.patch("services.knowledge_service.validate_filename", return_value=(True, "")), \
         mock.patch("services.knowledge_service.validate_file_size", return_value=(True, "")), \
         mock.patch("services.knowledge_service.upload_file_to_subject",
                    return_value={"size": 10}), \
         mock.patch("services.knowledge_service.rebuild_subject_knowledge",
                    return_value={}) as rebuild, \
         mock.patch.object(teaching_service, "_kb_status",
                           return_value={"available": True,
                                         "stats": {"status": "ready"}}), \
         mock.patch.object(teaching_service.td, "upsert_material",
                           return_value=material), \
         mock.patch.object(teaching_service.td, "audit"):
        result = teaching_service.upload_material("u1", "c1", b"x", "f.txt")
    # 即使知识库已 ready，也必须重建，否则新资料不在索引里
    rebuild.assert_called_once_with("s1")
    assert result["kb_rebuild"]["attempted"] is True
    assert result["kb_rebuild"]["ok"] is True
