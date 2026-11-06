"""混合检索融合：候选池、RRF 身份、重排顺序。

这三件事以前是断的：
- RAG_RETRIEVAL_POOL 写在配置里，检索按写死的 top_k*2 走，配大了也不生效；
- RRF 用 hash(正文) 当键，不同页上的同一句话会被收成一条；
- 问答入口再按 retrieval_score 排序，Cross-Encoder 的顺序会被盖掉。
"""

import pytest

from rag.bm25_retriever import reciprocal_rank_fusion


class _Doc:
    def __init__(self, content, **meta):
        self.page_content = content
        self.metadata = meta


def _hit(content, retrieval_type, score, **meta):
    return {
        "document": _Doc(content, **meta),
        "score": score,
        "retrieval_type": retrieval_type,
    }


def test_same_sentence_on_two_pages_stays_two_hits():
    fused = reciprocal_rank_fusion([[
        _hit("光的干涉", "vector", 0.9, file_name="第1章.pptx", page=2, chunk_index=0),
        _hit("光的干涉", "vector", 0.8, file_name="第2章.pptx", page=4, chunk_index=0),
    ]])
    assert len(fused) == 2
    pages = {item["document"].metadata["page"] for item in fused}
    assert pages == {2, 4}


def test_same_chunk_from_vector_and_bm25_merges():
    fused = reciprocal_rank_fusion([
        [_hit("频率相同", "vector", 0.8, file_name="课件.pdf", page=1, chunk_index=3)],
        [_hit("频率相同", "bm25", 4.0, file_name="课件.pdf", page=1, chunk_index=3)],
    ])
    assert len(fused) == 1
    assert fused[0]["retrieval_type"] == "hybrid"
    assert set(fused[0]["original_scores"]) == {"vector", "bm25"}


def test_wider_pool_promotes_chunk_found_by_both(monkeypatch):
    """只取 top_k=1 时两路各见一条，并列；把池子放到 2，两边都命中的块应排到前面。"""
    from rag import hybrid_retriever
    from rag import reranker

    monkeypatch.setattr(reranker, "enabled", lambda: False)
    vector = [
        _hit("只有向量", "vector", 0.99, file_name="v.txt", page=0, chunk_index=0),
        _hit("两边都有", "vector", 0.40, file_name="b.txt", page=1, chunk_index=1),
    ]
    bm25 = [
        _hit("两边都有", "bm25", 8.0, file_name="b.txt", page=1, chunk_index=1),
        _hit("只有关键词", "bm25", 3.0, file_name="m.txt", page=2, chunk_index=2),
    ]
    depths = []

    def _vector(_query, _subject, k, _threshold):
        depths.append(k)
        return vector[:k], None

    def _bm25(_query, _subject, k):
        depths.append(k)
        return bm25[:k]

    monkeypatch.setattr(hybrid_retriever, "_vector_search", _vector)
    monkeypatch.setattr(hybrid_retriever, "_bm25_search", _bm25)
    monkeypatch.setattr(hybrid_retriever, "_bm25_retrievers", {"subj": object()})

    narrow = hybrid_retriever.retrieve("干涉", "subj", top_k=1, candidate_k=1)
    wide = hybrid_retriever.retrieve("干涉", "subj", top_k=1, candidate_k=2)

    assert narrow[0]["content"] == "只有向量"
    assert wide[0]["content"] == "两边都有"
    assert wide[0]["retrieval_type"] == "hybrid"
    assert depths == [1, 1, 2, 2]


def test_retrieve_context_keeps_rerank_order_and_uses_pool(monkeypatch):
    from config.settings import settings
    from rag import rag_service

    if not rag_service.HYBRID_AVAILABLE:
        pytest.skip("混合检索不可用")

    seen = {}

    def fake_hybrid(**kwargs):
        seen.update(kwargs)
        return [
            {
                "content": "重排第一",
                "score": 0.01,
                "rerank_score": 0.95,
                "retrieval_type": "hybrid",
                "source": "a.pdf",
                "section": "一",
                "metadata": {"file_name": "a.pdf", "source": "a.pdf", "page": 0},
            },
            {
                "content": "融合分更高",
                "score": 0.9,
                "rerank_score": 0.1,
                "retrieval_type": "vector",
                "source": "b.pdf",
                "section": "",
                "metadata": {"file_name": "b.pdf", "source": "b.pdf", "page": 2},
            },
        ]

    monkeypatch.setattr(rag_service, "hybrid_retrieve", fake_hybrid)
    monkeypatch.setattr(rag_service, "get_retrieval_diagnostics", lambda: {
        "vector_available": True,
        "retrieval_mode": "hybrid_ready",
        "fallback_reason": None,
    })
    docs = rag_service.retrieve_context("这道题考什么", "subj", top_k=5)
    assert [doc.page_content for doc in docs] == ["重排第一", "融合分更高"]
    assert docs[0].metadata["rerank_score"] == 0.95
    assert seen["top_k"] == 5
    assert seen["candidate_k"] == max(10, int(settings.RETRIEVAL_POOL))


def test_larger_retrieval_pool_widens_candidates(monkeypatch):
    from rag import rag_service

    if not rag_service.HYBRID_AVAILABLE:
        pytest.skip("混合检索不可用")

    seen = {}

    def fake_hybrid(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(
        type(rag_service.settings),
        "RETRIEVAL_POOL",
        property(lambda self: 24),
    )
    monkeypatch.setattr(rag_service, "hybrid_retrieve", fake_hybrid)
    monkeypatch.setattr(rag_service, "get_retrieval_diagnostics", lambda: {
        "vector_available": True,
        "retrieval_mode": "hybrid_ready",
        "fallback_reason": None,
    })
    rag_service.retrieve_context("问题", "subj", top_k=5)
    assert seen["candidate_k"] == 24
    assert seen["top_k"] == 5
