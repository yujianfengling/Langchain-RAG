"""RAG 回答依据：来源片段与诊断字段（2026-10-05）

学生要能点开"回答依据"看真实原文，因此来源必须带真实 chunk 片段；
没有章节/分数时保持 None，不编造。
"""
from langchain_core.documents import Document

from services.ai_service import extract_sources


def test_extract_sources_includes_snippet_and_diagnostics():
    docs = [Document(
        page_content="本课程验收入口为 LAB-7731。\n\n用途：验证检索是否命中资料。",
        metadata={"file_name": "验收资料.txt", "page": 3,
                  "rerank_score": 0.87, "retrieval_type": "vector"},
    )]
    s = extract_sources(docs)[0]
    assert s["file_name"] == "验收资料.txt"
    assert s["page"] == 3
    assert "LAB-7731" in s["snippet"], "来源必须携带真实原文片段"
    assert "\n" not in s["snippet"], "片段应做空白归一，便于展示"
    assert s["score"] == 0.87
    assert s["retrieval_type"] == "vector"


def test_extract_sources_does_not_fabricate_missing_fields():
    docs = [Document(page_content="没有章节与分数", metadata={})]
    s = extract_sources(docs)[0]
    assert s["section"] is None
    assert s["score"] is None
    assert s["page"] is None
    assert s["snippet"] == "没有章节与分数"
