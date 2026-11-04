"""Production entry regressions for the de4f2a9 review; all model edges mocked."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services import ai_service, lesson_plan_service as lp


@pytest.mark.parametrize("selected,expected", [(["A"], False), (None, True)])
def test_lesson_prompt_obeys_selection(selected, expected):
    from langchain_core.documents import Document
    docs = [Document(page_content="A_ONLY_FACT", metadata={"source": "A.pdf", "material_id": "A"}),
            Document(page_content="B_ONLY_FACT", metadata={"source": "B.pdf", "material_id": "B"})]
    materials = [{"id": name, "filename": name + ".pdf", "status": "ready"} for name in ("A", "B")]
    prompts = []
    def generate(prompt, **kwargs):
        prompts.append(prompt)
        return json.dumps({"title": "mock"})
    with patch.object(lp.access, "require_course_teacher", return_value={"subject_id": "test"}), \
         patch.object(lp.td, "list_materials", return_value=materials), \
         patch.object(lp, "_class_summary", return_value={"available": False}), \
         patch.object(ai_service, "retrieve_and_build_context", return_value={
             "context": "A_ONLY_FACT B_ONLY_FACT", "sources": [], "documents": docs}), \
         patch.object(ai_service, "call_llm", side_effect=generate), \
         patch.object(ai_service, "resolve_model_info", return_value={}), \
         patch.object(lp.td, "create_lesson_plan", return_value={"id": "mock"}), \
         patch.object(lp.td, "audit"):
        lp.generate_draft("teacher", "course", topic="topic", material_ids=selected)
    assert "A_ONLY_FACT" in prompts[0]
    assert ("B_ONLY_FACT" in prompts[0]) is expected


@pytest.mark.parametrize("answer,finish", [
    ("", "stop"), ("not JSON", "stop"),
    ('{"is_content":true,"description":"","ocr_text":""}', "stop"),
    ('{"is_content":true,"description":"cut","ocr_text":""}', "length"),
])
def test_formal_vision_rejects_invalid_answer(tmp_path, answer, finish):
    from PIL import Image
    from services.multimodal_service import MultimodalService
    path = tmp_path / "sample.png"
    Image.new("RGB", (200, 200), "white").save(path)
    provider = SimpleNamespace(model_name="mock", config={}, vision=lambda *a, **k: {
        "answer": answer, "finish_reason": finish, "reasoning_content": "DO_NOT_USE"})
    with patch("rag.multimodal.image.extractor.ImageExtractor.extract_text", return_value=""):
        result = MultimodalService(provider).process_image_to_document(str(path))
    assert result["metadata"]["vision_status"] == "failed"
    assert result["metadata"]["error"]
    assert "DO_NOT_USE" not in result["page_content"]


def test_image_policy_invalidates_both_parse_caches(tmp_path, monkeypatch):
    from services.import_service import _parse_config_signature
    from rag.loaders.pdf_loader import PdfLoader
    loader = PdfLoader()
    monkeypatch.setenv("VISION_MAX_IMAGES_PER_TEXT_PDF", "4")
    first = (_parse_config_signature(), loader._range_cache_path(Path("x.pdf"), 0, 10, "sha"))
    assert first == (_parse_config_signature(), loader._range_cache_path(Path("x.pdf"), 0, 10, "sha"))
    monkeypatch.setenv("VISION_MAX_IMAGES_PER_TEXT_PDF", "5")
    second = (_parse_config_signature(), loader._range_cache_path(Path("x.pdf"), 0, 10, "sha"))
    assert first[0] != second[0]
    assert first[1] != second[1]
    from services.import_service import _save_docs_cache, _load_docs_cache
    from langchain_core.documents import Document
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    _save_docs_cache("same-content", first[0], [Document(page_content="old-policy", metadata={})])
    assert _load_docs_cache("same-content", first[0])[0].page_content == "old-policy"
    assert _load_docs_cache("same-content", second[0]) is None


def test_delete_index_failure_preserves_source(tmp_path, monkeypatch):
    from services import knowledge_service as ks
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    folder = tmp_path / "subjects" / "delete-failure"
    folder.mkdir(parents=True)
    source = folder / "this.txt"
    source.write_text("recoverable source")
    (folder / "other.txt").write_text("other upload")
    with patch.object(ks, "rebuild_subject_knowledge", side_effect=RuntimeError("mock index failure")):
        with pytest.raises(ks.KnowledgeServiceError):
            ks.delete_subject_document("delete-failure", "this.txt")
    assert source.read_text() == "recoverable source"


@pytest.mark.parametrize("selection", [[], ["missing"], ["pending"]])
def test_invalid_lesson_scope_never_calls_model(selection):
    with patch.object(lp.access, "require_course_teacher", return_value={"subject_id": "s"}), \
         patch.object(lp.td, "list_materials", return_value=[{"id": "pending", "filename": "p.pdf", "status": "pending"}]), \
         patch.object(ai_service, "call_llm") as model:
        with pytest.raises(lp.TeachingError):
            lp.generate_draft("u", "c", topic="test", material_ids=selection)
        model.assert_not_called()


def test_subject_delete_failure_preserves_registry(monkeypatch):
    from services import subject_service as ss, knowledge_service as ks
    monkeypatch.setattr(ss, "_load_subjects", lambda: [{"id": "test-delete"}])
    with patch.object(ss, "_save_subjects") as save, \
         patch.object(ks, "cleanup_subject_data", side_effect=OSError("mock permission denied")):
        with pytest.raises(ss.SubjectServiceError):
            ss.delete_subject("test-delete")
        save.assert_not_called()


@pytest.mark.parametrize("replacement,reused", [(False, False), (True, False), (False, True)])
def test_server_discard_only_deletes_this_upload(tmp_path, monkeypatch, replacement, reused):
    from services import import_service as imp, knowledge_service as ks
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    imp._init_db()
    source = tmp_path / "subjects" / "discard-test" / "same.txt"
    source.parent.mkdir(parents=True)
    source.write_text("original")
    sha = imp._sha256(source)
    if replacement:
        source.write_text("new version")
    other = source.with_name("unrelated.txt")
    other.write_text("other upload")
    with imp._connect() as conn:
        conn.execute("INSERT INTO import_jobs(job_id,subject_id,status,kb_json) VALUES('discard-job','discard-test','partial','{}')")
        conn.execute("INSERT INTO import_items(item_id,job_id,status,stored_path,content_sha,timings_json) VALUES(?,?,?,?,?,?)",
                     ("item", "discard-job", "ready", str(source), sha, json.dumps({"reused": reused})))
    def delete(sid, name):
        assert sid == "discard-test" and name == source.name
        source.unlink()
    with patch.object(ks, "delete_subject_document", side_effect=delete):
        result = imp.discard_job("discard-job")
        assert source.exists() is (replacement or reused)
        assert other.read_text() == "other upload"
        assert result["status"] == "cancelled"
        imp.discard_job("discard-job")  # safe repeat, even when already removed


def test_discard_cancel_failure_does_not_delete(tmp_path, monkeypatch):
    from services import import_service as imp, knowledge_service as ks
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    imp._init_db()
    with patch.object(imp, "get_job", return_value={"subject_id": "s"}), \
         patch.object(imp, "cancel_job", side_effect=RuntimeError("cancel failed")), \
         patch.object(ks, "delete_subject_document") as delete:
        with pytest.raises(RuntimeError):
            imp.discard_job("unknown")
        delete.assert_not_called()


def test_delete_last_unindexed_document_keeps_other_assets(tmp_path, monkeypatch):
    from services import knowledge_service as ks
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    folder = tmp_path / "subjects" / "last-delete"
    folder.mkdir(parents=True)
    (folder / "this.txt").write_text("last document")
    (folder / "other.bin").write_bytes(b"unrelated asset")
    result = ks.delete_subject_document("last-delete", "this.txt")
    assert result["success"] is True
    assert not (folder / "this.txt").exists()
    assert (folder / "other.bin").read_bytes() == b"unrelated asset"
