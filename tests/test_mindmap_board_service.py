"""思维导图板书生成测试（2026-11-14 白板重做）

模型只产出受控导图 JSON；后端必须做白名单、限深限宽校验；
页面文本优先按 file_name+page 从页面索引取，取不到退回检索兜底，都没有就明确报错。
"""

import json

import pytest

from services import mindmap_board_service as mm


def test_normalize_tree_keeps_text_children_and_caps():
    raw = {"root": {
        "text": "逻辑结构分类",
        "children": [
            {"text": "集合结构", "children": [{"text": "无序", "children": []}]},
            {"text": "x" * 100, "children": [{"text": "超限孙", "children": []}]},
            {"not_a_node": True},
            "junk",
        ],
    }}
    tree = mm.normalize_tree(raw)
    assert tree["text"] == "逻辑结构分类"
    # 超长文本截断到上限，但合法孩子保留
    assert [c["text"] for c in tree["children"]] == ["集合结构", "x" * mm.MAX_TEXT_LEN]
    assert tree["children"][1]["children"] == [{"text": "超限孙", "children": []}]


def test_normalize_tree_caps_depth():
    def chain(depth):
        node = {"text": "leaf"}
        for _ in range(depth):
            node = {"text": "mid", "children": [node]}
        return node

    tree = mm.normalize_tree({"root": chain(10)})

    def depth_of(t, d=0):
        return d if not t["children"] else max(depth_of(c, d + 1) for c in t["children"])

    assert depth_of(tree) <= mm.MAX_DEPTH


def test_normalize_tree_rejects_empty():
    with pytest.raises(mm.MindmapError):
        mm.normalize_tree({})
    with pytest.raises(mm.MindmapError):
        mm.normalize_tree({"root": {"text": "  "}})


def test_generate_mindmap_uses_page_text_first(monkeypatch):
    captured = {}

    monkeypatch.setattr(mm, "_load_page_text",
                        lambda subject_id, file_name, page: "栈：后进先出；出栈序列判定。")

    def fake_llm(prompt, **kwargs):
        captured["prompt"] = prompt
        return json.dumps({"root": {"text": "栈", "children": [{"text": "后进先出"}]}},
                          ensure_ascii=False)

    monkeypatch.setattr(mm, "call_llm", fake_llm)
    data = mm.generate_mindmap("s1", "u1", "数据结构.pdf", 3)
    assert data["root"]["text"] == "栈"
    assert data["source"]["page"] == 3
    assert "栈：后进先出" in captured["prompt"]
    assert "第 4 页" in captured["prompt"]  # 0 基页码 → 展示 +1


def test_generate_mindmap_falls_back_to_retrieval(monkeypatch):
    monkeypatch.setattr(mm, "_load_page_text", lambda *a, **k: "")
    monkeypatch.setattr(mm, "call_llm",
                        lambda prompt, **kwargs: json.dumps({"root": {"text": "主题"}},
                                                            ensure_ascii=False))

    from services import ai_service
    monkeypatch.setattr(ai_service, "retrieve_and_build_context_scoped",
                        lambda **kwargs: {"context": "检索到的片段"})

    data = mm.generate_mindmap("s1", "u1", "课件.pdf", 2, topic="栈")
    assert data["root"]["text"] == "主题"
    assert data["source"]["ref"] == "课件检索片段"


def test_generate_mindmap_raises_when_no_content(monkeypatch):
    monkeypatch.setattr(mm, "_load_page_text", lambda *a, **k: "")
    from services import ai_service
    monkeypatch.setattr(ai_service, "retrieve_and_build_context_scoped",
                        lambda **kwargs: {"context": ""})
    with pytest.raises(mm.MindmapError) as exc:
        mm.generate_mindmap("s1", "u1", "课件.pdf", 0)
    assert exc.value.error_type == "no_content"


def test_generate_mindmap_model_error_wrapped(monkeypatch):
    monkeypatch.setattr(mm, "_load_page_text", lambda *a, **k: "有内容")

    def boom(prompt, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(mm, "call_llm", boom)
    with pytest.raises(mm.MindmapError) as exc:
        mm.generate_mindmap("s1", "u1", "课件.pdf", 0)
    assert exc.value.error_type == "model_error"
