"""Contextual Retrieval + Cross-Encoder 测试（2026-10-24）

- 确定性前缀必须包含资料/章节信息，且重复 enrich 不叠加；
- CONTEXTUAL_LLM=1 时用模型生成一句上下文，失败自动回退；
- 重排默认关闭；开启后按 Cross-Encoder 分数排序，失败回退原顺序。
"""

from services import ai_service
from rag import contextual_retrieval, reranker


class _Doc:
    def __init__(self, content, **meta):
        self.page_content = content
        self.metadata = meta


def test_deterministic_prefix_and_idempotent(monkeypatch):
    monkeypatch.delenv("CONTEXTUAL_LLM", raising=False)
    doc = _Doc("二叉树的前序遍历是根左右。", file_name="课件.pdf", section="遍历", page=3)
    docs, stats = contextual_retrieval.enrich_documents([doc], "s1")
    assert stats["prefix_count"] == 1
    assert docs[0].page_content.startswith("【上下文】")
    assert "课件.pdf" in docs[0].page_content
    assert "遍历" in docs[0].page_content
    assert docs[0].metadata["original_content"] == doc.page_content
    again, _ = contextual_retrieval.enrich_documents(docs, "s1")
    assert again[0].page_content == docs[0].page_content


def test_llm_context_used_when_enabled(monkeypatch):
    monkeypatch.setenv("CONTEXTUAL_LLM", "1")
    monkeypatch.setattr(ai_service, "call_llm",
                        lambda *a, **k: "这一段在讲二叉树的遍历方式。")
    doc = _Doc("前序是根左右。", file_name="唯一课件.pdf", section="唯一章节xyz")
    docs, stats = contextual_retrieval.enrich_documents([doc], "s_llm")
    assert stats["prefix_count"] == 1
    assert "这一段在讲二叉树的遍历方式" in docs[0].page_content


def test_rerank_disabled_returns_original(monkeypatch):
    monkeypatch.delenv("RERANK_ENABLED", raising=False)
    items = [{"document": _Doc("a"), "score": 2}, {"document": _Doc("b"), "score": 1}]
    out, used = reranker.rerank("q", items, 2)
    assert used is False
    assert [item["document"].page_content for item in out] == ["a", "b"]


def test_rerank_sorts_by_cross_encoder(monkeypatch):
    class FakeModel:
        def predict(self, pairs, **kwargs):
            return [0.1, 0.9]

    monkeypatch.setattr(reranker, "enabled", lambda: True)
    monkeypatch.setattr(reranker, "_get_model", lambda: FakeModel())
    items = [{"document": _Doc("first"), "score": 10},
             {"document": _Doc("second"), "score": 1}]
    out, used = reranker.rerank("q", items, 2)
    assert used is True
    assert [item["document"].page_content for item in out] == ["second", "first"]
    assert out[0]["rerank_score"] == 0.9
