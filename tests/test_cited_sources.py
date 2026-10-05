"""回答引用编号解析（2026-10-05）

context_used 只表示"检索到课程资料并放进上下文"，不等于回答引用了它。
"实际引用"以后端从回答中解析出的 cited_source_ids 为准（确定性信号，
不靠回答文案关键词猜测）。
"""
from langchain_core.documents import Document

from chat_service import _cited_source_ids
from rag.rag_service import build_context


def test_parses_only_in_range_citations():
    # 越界的 [9] 不算引用（本次只检索到 3 个来源）
    assert _cited_source_ids("口令是 FALCON-3310 [1]，另见 [3] 与 [9]。", 3) == [1, 3]


def test_no_citation_when_answer_has_none():
    assert _cited_source_ids("课程资料中没有相关内容，无法回答。", 3) == []
    assert _cited_source_ids("", 3) == []


def test_numbered_context_matches_source_ids():
    docs = [
        Document(page_content="片段一", metadata={"source": "a.txt"}),
        Document(page_content="片段二", metadata={"source": "b.txt"}),
    ]
    numbered = build_context(docs, numbered=True)
    assert "[1] " in numbered and "[2] " in numbered
    plain = build_context(docs)
    assert "[1] " not in plain
