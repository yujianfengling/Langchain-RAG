# -*- coding: utf-8 -*-
"""智能笔记 API 集成测试（Task B/C） - 使用 TestClient，不调真实 LLM"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient


def _make_client():
    from api.app import app
    return TestClient(app)


# mock LLM：返回结构化 JSON（含 markdown 代码块，验证解析）
def _fake_call_llm(prompt, system_message=None, model=None, **kw):
    return '''```json
{
  "title": "二叉树复习笔记",
  "concept": {"content": "二叉树是每个节点最多有两个子节点的树结构。"},
  "example": {"content": "前序遍历：根-左-右。"},
  "mistakes": {"content": "注意区分前序中序后序。"},
  "class_tips": {"content": "课堂提示：重点掌握三种遍历。"},
  "quiz": [
    {"question": "二叉树每个节点最多几个子树？", "type": "single_choice",
     "options": ["一个", "两个"], "answer": "两个", "explanation": "定义"},
    {"question": "前序遍历顺序？", "type": "short_answer",
     "options": [], "answer": "根-左-右", "explanation": "遍历"}
  ]
}
```'''


# 让 KB 守卫放行（知识库视为已构建），聚焦 RAG+LLM 链路
def _allow_kb_guard():
    return mock.patch("services.kb_guard.check_kb_ready", return_value=None)


# mock RAG 检索：模拟知识库命中
def _mock_rag_hit():
    from rag.hybrid_retriever import _make_simple_doc
    from langchain_core.documents import Document as _D
    docs = [
        _D(page_content="二叉树是每个节点最多有两个子节点的树结构。",
           metadata={"source": "数据结构.md", "file_name": "数据结构.md", "page": 1, "type": "text"}),
        _D(page_content="前序遍历的顺序是根-左-右。",
           metadata={"source": "数据结构.md", "file_name": "数据结构.md", "page": 2, "type": "text"}),
    ]
    return [
        mock.patch("rag.hybrid_retriever._vector_search",
                   return_value=([{"document": d, "score": 0.9, "retrieval_type": "vector"} for d in docs], None)),
        mock.patch("rag.hybrid_retriever._bm25_search", return_value=[]),
    ]


def test_generate_note_uses_rag_evidence():
    """智能笔记应基于 RAG 检索证据生成，并返回结构化内容"""
    client = _make_client()
    patches = _mock_rag_hit() + [
        mock.patch("services.note_service.call_llm", side_effect=_fake_call_llm),
        _allow_kb_guard(),
    ]
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post("/api/notes/generate", json={
            "subject_id": "数据结构",
            "topic": "二叉树",
        })

    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
    data = resp.json().get("data", {})
    assert data.get("title") == "二叉树复习笔记"
    for key in ("concept", "example", "mistakes", "class_tips"):
        assert key in data, f"缺 {key}"
        assert "content" in data[key], f"{key} 缺 content"
        assert "sources" in data[key], f"{key} 缺 sources"
    assert len(data["quiz"]) == 2
    assert data["quiz"][0]["type"] == "single_choice"
    assert data["quiz"][1]["type"] == "short_answer"
    concept_src = data["concept"]["sources"]
    assert concept_src, "concept 应有关联 source"
    assert "file_name" in concept_src[0]
    assert "page" in concept_src[0]
    assert "type" in concept_src[0]


def test_generate_note_no_evidence_degrades():
    """知识库无证据时，返回降级笔记而非报错，不调用 LLM"""
    client = _make_client()
    with mock.patch("rag.hybrid_retriever._vector_search",
                    return_value=([], "faiss_index_missing")), \
         mock.patch("rag.hybrid_retriever._bm25_search", return_value=[]), \
         mock.patch("services.note_service.call_llm") as mock_llm, \
         _allow_kb_guard():
        resp = client.post("/api/notes/generate", json={
            "subject_id": "数据结构",
            "topic": "不存在的主题",
        })

    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
    data = resp.json().get("data", {})
    assert data["context_used"] is False
    assert "没有足够的证据" in data["concept"]["content"]
    mock_llm.assert_not_called()


def test_generate_note_missing_topic():
    """缺少 topic → 400"""
    client = _make_client()
    resp = client.post("/api/notes/generate", json={"subject_id": "x", "topic": ""})
    assert resp.status_code == 400


def test_generate_note_kb_not_ready():
    """知识库未构建 → 409 + knowledge_base_not_ready，不调用 LLM"""
    client = _make_client()
    with mock.patch("services.kb_guard.check_kb_ready",
                    return_value="knowledge_base_not_ready"), \
         mock.patch("services.note_service.call_llm") as mock_llm:
        resp = client.post("/api/notes/generate", json={
            "subject_id": "11",
            "topic": "二叉树",
        })

    assert resp.status_code == 409
    body = resp.json()
    assert body["error_type"] == "knowledge_base_not_ready"
    assert "构建" in body["message"]
    mock_llm.assert_not_called()


def test_generate_note_subject_not_found():
    """学科不存在 → 409 + subject_not_found"""
    client = _make_client()
    with mock.patch("services.kb_guard.check_kb_ready",
                    return_value="subject_not_found"), \
         mock.patch("services.note_service.call_llm") as mock_llm:
        resp = client.post("/api/notes/generate", json={
            "subject_id": "不存在的学科",
            "topic": "二叉树",
        })

    assert resp.status_code == 409
    assert resp.json()["error_type"] == "subject_not_found"
    mock_llm.assert_not_called()
