"""AI 白板场景校验测试（2026-10-24）

模型只产出受控 JSON；后端必须做白名单、边界与文本长度校验，
不能把任意 HTML/JS 或越界坐标直接丢给前端。
"""

import json

import pytest

from services import whiteboard_service as wb


def test_validate_scene_keeps_allowed_elements_and_clamps():
    raw = {
        "title": "二次函数",
        "elements": [
            {"type": "text", "x": 5000, "y": -20, "text": "顶点", "size": 999},
            {"type": "rect", "x": 100, "y": 100, "w": 10, "h": 10, "text": "概念"},
            {"type": "circle", "x": 200, "y": 200, "r": 500, "text": "例"},
            {"type": "curve", "points": [[0, 0], [2000, 900]]},
            {"type": "script", "text": "alert(1)"},
            {"type": "text", "text": ""},
        ],
    }
    scene = wb.validate_scene(raw)
    assert scene["title"] == "二次函数"
    assert scene["width"] == 1000 and scene["height"] == 700
    kinds = [e["type"] for e in scene["elements"]]
    assert kinds == ["text", "rect", "circle", "curve"]
    text = scene["elements"][0]
    assert text["x"] == 1000 and text["y"] == 0
    assert text["size"] == 48
    circle = scene["elements"][2]
    assert circle["r"] == 200
    curve = scene["elements"][3]
    assert curve["points"][1][0] == 1000


def test_validate_scene_rejects_empty_or_invalid():
    with pytest.raises(wb.WhiteboardError):
        wb.validate_scene({})
    with pytest.raises(wb.WhiteboardError):
        wb.validate_scene({"elements": [{"type": "script"}]})


def test_generate_scene_parses_json_and_injects_topic(monkeypatch):
    captured = {}

    def fake_llm(prompt, **kwargs):
        captured["prompt"] = prompt
        return json.dumps({
            "title": "勾股定理",
            "elements": [
                {"type": "text", "x": 100, "y": 100, "text": "a²+b²=c²"},
                {"type": "arrow", "x1": 100, "y1": 160, "x2": 300, "y2": 160},
            ],
        }, ensure_ascii=False)

    monkeypatch.setattr(wb, "call_llm", fake_llm)
    scene = wb.generate_scene("勾股定理")
    assert scene["topic"] == "勾股定理"
    assert scene["elements"][0]["text"] == "a²+b²=c²"
    assert "勾股定理" in captured["prompt"]


def test_generate_scene_rejects_non_json(monkeypatch):
    monkeypatch.setattr(wb, "call_llm", lambda *a, **k: "这不是 JSON")
    with pytest.raises(wb.WhiteboardError) as exc:
        wb.generate_scene("函数")
    assert exc.value.error_type == "structured_output_error"


def test_template_scenes_are_deterministic():
    for name in ("function", "geometry", "force"):
        scene = wb.template_scene(name, "演示")
        assert "通用示意" in scene["title"]
        assert scene["elements"]
        assert all(0 <= e.get("x", 0) <= 1000 for e in scene["elements"] if "x" in e)
    function_scene = wb.template_scene("function")
    curves = [e for e in function_scene["elements"] if e["type"] == "curve"]
    assert curves and len(curves[0]["points"]) > 10


def test_concept_prompt_is_topic_specific(monkeypatch):
    captured = {}

    def fake_llm(prompt, **kwargs):
        captured["prompt"] = prompt
        return json.dumps({
            "title": "压杆稳定性",
            "elements": [
                {"type": "text", "x": 300, "y": 40, "text": "压杆稳定性", "size": 24},
                {"type": "rect", "x": 100, "y": 100, "w": 220, "h": 80, "text": "临界力 Fcr"},
            ],
        }, ensure_ascii=False)

    monkeypatch.setattr(wb, "call_llm", fake_llm)
    scene = wb.generate_scene("压杆稳定性的概念", template="concept")
    assert scene["title"] == "压杆稳定性"
    assert "压杆稳定性的概念" in captured["prompt"]
    assert "禁止画与主题无关" in captured["prompt"]


def test_template_does_not_call_model(monkeypatch):
    monkeypatch.setattr(wb, "call_llm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    scene = wb.generate_scene("", template="force")
    assert scene["elements"]
    assert any(e["type"] == "arrow" for e in scene["elements"])


def test_generate_scene_requires_topic():
    with pytest.raises(wb.WhiteboardError) as exc:
        wb.generate_scene("   ")
    assert exc.value.error_type == "invalid_request"
