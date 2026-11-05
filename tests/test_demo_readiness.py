"""Production-path regressions found while preparing the student demonstration."""
import pickle
import time
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document


def test_failed_graph_build_keeps_previous_graph_and_reports_failure(tmp_path, monkeypatch):
    from rag.knowledge_graph import graph_builder as gb
    from rag.knowledge_graph.task_manager import GraphBuildManager
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    subject = "demo_graph_failure"
    index = tmp_path / "vector_store" / subject
    index.mkdir(parents=True)
    with (index / "index.pkl").open("wb") as stream:
        pickle.dump({"documents": [Document(page_content="二叉树由根节点和左右子树构成，子树同样满足二叉树定义。") ]}, stream)
    old = {"nodes": [{"id": "previous"}], "edges": [], "stats": {}}
    gb._save_graph(subject, old)
    manager = GraphBuildManager()
    try:
        with patch("services.model_service.get_model_provider") as provider:
            provider.return_value.chat.side_effect = RuntimeError("offline model failure")
            accepted, _ = manager.submit(subject, lambda sid, progress: gb.build_knowledge_graph(sid, max_chunks=1, progress_cb=progress))
            assert accepted
            deadline = time.monotonic() + 3
            while manager.get_status(subject)["status"] in ("queued", "building") and time.monotonic() < deadline:
                time.sleep(.01)
        assert manager.get_status(subject)["status"] == "failed"
        assert gb.load_knowledge_graph(subject) == old
    finally:
        manager._executor.shutdown(wait=True)


def test_pptx_ocr_policy_changes_production_parse_fingerprint(monkeypatch):
    from services.import_service import _parse_config_signature
    monkeypatch.setenv("PPTX_IMAGE_OCR_FIRST", "1")
    monkeypatch.setenv("PPTX_OCR_MIN_CHARS", "20")
    original = _parse_config_signature()
    assert original == _parse_config_signature()
    monkeypatch.setenv("PPTX_IMAGE_OCR_FIRST", "0")
    assert original != _parse_config_signature()
    monkeypatch.setenv("PPTX_IMAGE_OCR_FIRST", "1")
    monkeypatch.setenv("PPTX_OCR_MIN_CHARS", "200")
    assert original != _parse_config_signature()


@pytest.mark.parametrize("reason,exception", [("cancelled", "TurnCancelled"), ("timeout", "TurnTimeout")])
def test_closed_upstream_after_stop_does_not_mark_model_unavailable(reason, exception):
    from services import turn_execution as tx
    from services.model_service import OpenAICompatibleProvider
    provider = object.__new__(OpenAICompatibleProvider)
    provider.model_name = "deepseek"
    provider.config = {"model_name": "offline-model", "base_url": "https://offline.invalid"}
    provider.api_key = "offline-test-only"
    execution = tx.Execution("offline-stop", 10)
    response = MagicMock()
    response.status_code = 200
    response.__enter__.return_value = response

    def closed_stream(**kwargs):
        execution.cancel(reason)
        raise AttributeError("'NoneType' object has no attribute 'readline'")
        yield  # make this a streaming failure, as requests/urllib3 can produce on close

    response.iter_lines.side_effect = closed_stream
    with tx.bind(execution), patch("services.model_service.requests.post", return_value=response), patch("services.capability_status.safe_report") as report:
        with pytest.raises(getattr(tx, exception)):
            list(provider.stream_with_tools([{"role": "user", "content": "offline"}]))
        report.assert_not_called()


def test_unexpected_upstream_failure_remains_visible():
    from services.model_service import OpenAICompatibleProvider
    provider = object.__new__(OpenAICompatibleProvider)
    provider.model_name = "deepseek"
    provider.config = {"model_name": "offline-model", "base_url": "https://offline.invalid"}
    provider.api_key = "offline-test-only"
    response = MagicMock()
    response.status_code = 200
    response.__enter__.return_value = response
    response.iter_lines.side_effect = AttributeError("unexpected stream failure")
    with patch("services.model_service.requests.post", return_value=response), patch("services.capability_status.safe_report") as report:
        with pytest.raises(AttributeError):
            list(provider.stream_with_tools([{"role": "user", "content": "offline"}]))
        assert report.call_args.args[1] is False


def test_short_teaching_image_survives_real_paired_index_build(tmp_path, monkeypatch):
    import rag.rag_service as rs
    import rag.embedding as emb
    from services import knowledge_service as ks
    from tests.test_index_atomic_publish import _FakeEmbeddings
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    fake = _FakeEmbeddings()
    monkeypatch.setattr(rs, "get_embedding_model", lambda: fake)
    monkeypatch.setattr(emb, "get_embedding_model", lambda: fake)
    picture = Document(page_content="图片描述：绿色系统框包含蓝色模块，模块编号 DEMO-37。", metadata={
        "file_name": "course.pptx", "modality": "image", "page": 2,
        "is_content": True, "vision_status": "success", "enhancement_item_id": "image-demo"})
    ks._build_index_with_rollback("demo_short_image", [
        Document(page_content="需求说明软件需要做什么，设计说明如何实现。" * 12, metadata={"file_name": "course.pptx", "page": 1}), picture])
    with (tmp_path / "vector_store" / "demo_short_image" / "index.pkl").open("rb") as stream:
        docs = pickle.load(stream)["documents"]
    assert any(d.metadata.get("enhancement_item_id") == "image-demo" for d in docs)
    from rag.hybrid_retriever import read_bm25_meta
    assert read_bm25_meta(str(tmp_path / "bm25_index" / "demo_short_image"))["documents"] == len(docs)


def test_read_ppt_source_uses_indexed_page_instead_of_zip_bytes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from services import tutor_tools as tools
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    folder = tmp_path / "subjects" / "demo_read_ppt"
    folder.mkdir(parents=True)
    (folder / "course.pptx").write_bytes(b"PK\x03\x04binary-container")
    docs = [Document(page_content="图片独有事实 DEMO-37，绿色框包含蓝色圆。", metadata={"file_name": "course.pptx", "page": 2}),
            Document(page_content="其他页不能进入结果", metadata={"file_name": "course.pptx", "page": 1})]
    ctx = tools.ToolContext("offline-user", "demo_read_ppt", scope={"mode": "all"})
    with patch("rag.vector_store.load_vector_store", return_value=SimpleNamespace(documents=docs)):
        out = tools._t_read_source(ctx, "course.pptx", page=2)
    assert out["ok"] and "DEMO-37" in out["text"]
    assert "binary-container" not in out["text"] and "其他页" not in out["text"]
    assert out["sources"][0]["file_name"] == "course.pptx" and out["sources"][0]["page"] == 2


def test_read_ppt_source_without_index_reports_unavailable(tmp_path, monkeypatch):
    from services import tutor_tools as tools
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    folder = tmp_path / "subjects" / "demo_no_index"
    folder.mkdir(parents=True)
    (folder / "course.pptx").write_bytes(b"PK\x03\x04binary-container")
    ctx = tools.ToolContext("offline-user", "demo_no_index", scope={"mode": "all"})
    with patch("rag.vector_store.load_vector_store", return_value=None):
        out = tools._t_read_source(ctx, "course.pptx", page=2)
    assert out["ok"] is False
    assert "text" not in out


@pytest.mark.parametrize("fast_scan", [False, True])
def test_cached_teaching_image_keeps_content_classification(tmp_path, fast_scan):
    from PIL import Image
    from rag.enrichment.multimodal_enricher import MultimodalEnricher
    from rag.vector_store import filter_documents_by_min_length
    picture = tmp_path / "image.png"
    Image.new("RGB", (200, 200), "white").save(picture)
    doc = Document(page_content="", metadata={"file_name": "course.pptx", "image_path": str(picture), "modality": "image"})
    cached = {"page_content": "系统边界包含模块 DEMO-37，这是测试范围示意图。",
              "metadata": {"is_content": True, "content_kind": "diagram", "description": "系统边界包含模块"}}
    provider = MagicMock()
    enricher = MultimodalEnricher(vision_provider=provider)
    with patch.object(enricher, "_load_from_cache", return_value=cached):
        if fast_scan:
            result = enricher.fill_images_from_cache([doc])[0]
        else:
            result = enricher._enrich_image_document(doc)
    provider.analyze_image.assert_not_called()
    assert result.metadata.get("is_content") is True
    kept, _ = filter_documents_by_min_length([
        Document(page_content="正常正文内容。" * 30, metadata={"file_name": "course.pptx"}), result], min_length=90)
    assert result in kept
