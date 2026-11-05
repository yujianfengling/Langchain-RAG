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
