"""BM25 小语料关键词命中回归。"""
from langchain_core.documents import Document

from rag.bm25_retriever import BM25Retriever


def test_half_corpus_terms_keep_positive_weight():
    """df == N / 2 的明确关键词不能被第三方零 IDF 静默过滤。"""
    retriever = BM25Retriever([
        Document(page_content="组合形状内文本"),
        Document(page_content="完全无关的另一份资料"),
    ])

    results = retriever.search("组合形状内文本是什么", top_k=2)

    assert results
    assert "组合形状内文本" in results[0]["document"].page_content
    assert results[0]["score"] > 0
