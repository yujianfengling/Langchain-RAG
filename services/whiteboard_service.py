"""AI 白板场景生成（2026-10-24）

模型只负责产出**结构化场景 JSON**，前端用 SVG 渲染；不让模型生成任意 HTML/JS。
坐标统一 1000×700，元素类型限定为 text/line/arrow/rect/circle/curve，
后端做白名单与边界校验，避免越界、超长文本和脚本注入。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from services.ai_service import call_llm
from utils.logger import logger

CANVAS_WIDTH = 1000
CANVAS_HEIGHT = 700
MAX_ELEMENTS = 80
ALLOWED_TYPES = {"text", "line", "arrow", "rect", "circle", "curve"}

_PROMPT = """你是一名教学白板绘图助手。请把知识点画成一张**结构清晰**的板书。

## 主题
{topic}

## 输出要求（严格 JSON，不要输出任何其他内容）
{{
  "title": "白板标题",
  "elements": [
    {{"type": "text", "x": 60, "y": 60, "text": "标题文字", "size": 24, "color": "#1f2937"}},
    {{"type": "rect", "x": 80, "y": 140, "w": 240, "h": 90, "text": "概念", "color": "#2563eb"}},
    {{"type": "arrow", "x1": 320, "y1": 185, "x2": 430, "y2": 185, "color": "#64748b"}},
    {{"type": "circle", "x": 470, "y": 185, "r": 36, "text": "例子", "color": "#059669"}},
    {{"type": "line", "x1": 80, "y1": 300, "x2": 900, "y2": 300, "color": "#cbd5e1"}},
    {{"type": "curve", "points": [[100, 600], [200, 500], [300, 430]], "color": "#dc2626"}}
  ]
}}

## 规则
1. 画布 1000×700，所有坐标必须落在画布内；元素不超过 60 个。
2. 只使用 text / line / arrow / rect / circle / curve 六种类型。
3. 每个元素尽量带一句短文字（不超过 24 字），不要写长段落。
4. 这是教学板书，不是艺术画：先结论/定义，再关系箭头，最后例子或易错点。
5. 不要输出 Markdown、解释或代码块，只输出 JSON。
"""


class WhiteboardError(Exception):
    def __init__(self, message: str, error_type: str = "invalid_scene"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


def _clamp(value: Any, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def _color(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", text):
        return text
    return "#1f2937"


def _short_text(value: Any, limit: int = 60) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[:limit]


def _point(value: Any) -> Optional[List[float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    return [_clamp(value[0], 0, CANVAS_WIDTH), _clamp(value[1], 0, CANVAS_HEIGHT)]


def validate_scene(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise WhiteboardError("场景不是 JSON 对象")
    elements = raw.get("elements")
    if not isinstance(elements, list) or not elements:
        raise WhiteboardError("场景没有元素")
    cleaned: List[Dict[str, Any]] = []
    for item in elements[:MAX_ELEMENTS]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip().lower()
        if kind not in ALLOWED_TYPES:
            continue
        base = {"type": kind, "color": _color(item.get("color"))}
        if kind == "text":
            text = _short_text(item.get("text"), 80)
            if not text:
                continue
            base.update({"x": _clamp(item.get("x"), 0, CANVAS_WIDTH),
                         "y": _clamp(item.get("y"), 0, CANVAS_HEIGHT),
                         "text": text,
                         "size": _clamp(item.get("size", 18), 10, 48)})
        elif kind in ("line", "arrow"):
            base.update({"x1": _clamp(item.get("x1"), 0, CANVAS_WIDTH),
                         "y1": _clamp(item.get("y1"), 0, CANVAS_HEIGHT),
                         "x2": _clamp(item.get("x2"), 0, CANVAS_WIDTH),
                         "y2": _clamp(item.get("y2"), 0, CANVAS_HEIGHT)})
        elif kind == "rect":
            base.update({"x": _clamp(item.get("x"), 0, CANVAS_WIDTH),
                         "y": _clamp(item.get("y"), 0, CANVAS_HEIGHT),
                         "w": _clamp(item.get("w", 120), 20, CANVAS_WIDTH),
                         "h": _clamp(item.get("h", 60), 20, CANVAS_HEIGHT),
                         "text": _short_text(item.get("text"), 60)})
        elif kind == "circle":
            base.update({"x": _clamp(item.get("x"), 0, CANVAS_WIDTH),
                         "y": _clamp(item.get("y"), 0, CANVAS_HEIGHT),
                         "r": _clamp(item.get("r", 40), 8, 200),
                         "text": _short_text(item.get("text"), 40)})
        elif kind == "curve":
            points = [_point(p) for p in (item.get("points") or []) if _point(p)]
            if len(points) < 2:
                continue
            base.update({"points": points[:200]})
        cleaned.append(base)
    if not cleaned:
        raise WhiteboardError("场景没有可用元素")
    return {"title": _short_text(raw.get("title"), 60) or "白板",
            "width": CANVAS_WIDTH, "height": CANVAS_HEIGHT,
            "elements": cleaned}


TEMPLATES = {
    "concept": "概念图",
    "function": "函数图像",
    "geometry": "平面几何",
    "force": "受力分析",
}

_CONCEPT_PROMPT = """你是一名教学白板绘图助手。请围绕知识点画一张**概念板书**，帮助学生理解，而不是画通用几何图形。

## 知识点
{topic}

## 输出要求（严格 JSON，不要输出任何其他内容）
{{
  "title": "白板标题",
  "elements": [
    {{"type": "text", "x": 370, "y": 60, "text": "中心主题", "size": 26, "color": "#1f2937"}},
    {{"type": "rect", "x": 350, "y": 100, "w": 280, "h": 80, "text": "核心定义", "color": "#2563eb"}},
    {{"type": "arrow", "x1": 490, "y1": 180, "x2": 490, "y2": 270, "color": "#64748b"}},
    {{"type": "rect", "x": 80, "y": 280, "w": 250, "h": 90, "text": "关键条件", "color": "#059669"}},
    {{"type": "rect", "x": 370, "y": 280, "w": 250, "h": 90, "text": "公式/判据", "color": "#d97706"}},
    {{"type": "rect", "x": 660, "y": 280, "w": 250, "h": 90, "text": "典型例子", "color": "#7c3aed"}}
  ]
}}

## 规则
1. 画布 1000×700，元素不超过 40 个，坐标必须在画布内。
2. 必须画当前知识点的**具体内容**：定义、关键条件、公式/判据、典型例子、易错点；不能只写“定义/例子”这种空标签。
3. 用矩形框 + 箭头组织关系；文字短句，每条不超过 24 字。
4. 禁止画与主题无关的三角形、坐标轴或通用示意图。
5. 只输出 JSON，不要 Markdown/解释/代码块。
"""


def template_scene(template: str, topic: str = "") -> Dict[str, Any]:
    """不调用模型的确定性模板：函数图 / 几何 / 受力。"""
    name = str(template or "").strip().lower()
    if name == "function":
        points = []
        for index in range(41):
            x = 100 + index * 20
            y = 600 - 0.0025 * (x - 500) ** 2
            points.append([x, round(y, 1)])
        elements = [
            {"type": "text", "x": 60, "y": 40, "text": topic or "函数图像", "size": 24},
            {"type": "line", "x1": 80, "y1": 620, "x2": 940, "y2": 620, "color": "#94a3b8"},
            {"type": "line", "x1": 500, "y1": 80, "x2": 500, "y2": 660, "color": "#94a3b8"},
            {"type": "text", "x": 920, "y": 650, "text": "x", "size": 18},
            {"type": "text", "x": 515, "y": 90, "text": "y", "size": 18},
            {"type": "curve", "points": points, "color": "#2563eb"},
            {"type": "text", "x": 530, "y": 585, "text": "顶点", "size": 16, "color": "#2563eb"},
            {"type": "circle", "x": 500, "y": 600, "r": 6, "color": "#2563eb"},
        ]
    elif name == "geometry":
        elements = [
            {"type": "text", "x": 60, "y": 40, "text": topic or "平面几何", "size": 24},
            {"type": "line", "x1": 200, "y1": 600, "x2": 700, "y2": 600, "color": "#2563eb"},
            {"type": "line", "x1": 700, "y1": 600, "x2": 200, "y2": 200, "color": "#2563eb"},
            {"type": "line", "x1": 200, "y1": 200, "x2": 200, "y2": 600, "color": "#2563eb"},
            {"type": "text", "x": 185, "y": 625, "text": "A", "size": 18},
            {"type": "text", "x": 710, "y": 625, "text": "B", "size": 18},
            {"type": "text", "x": 175, "y": 195, "text": "C", "size": 18},
            {"type": "text", "x": 440, "y": 630, "text": "c", "size": 16},
            {"type": "text", "x": 450, "y": 390, "text": "a", "size": 16},
            {"type": "text", "x": 190, "y": 410, "text": "b", "size": 16},
        ]
    elif name == "force":
        elements = [
            {"type": "text", "x": 60, "y": 40, "text": topic or "受力分析", "size": 24},
            {"type": "rect", "x": 400, "y": 300, "w": 180, "h": 90, "text": "物块", "color": "#2563eb"},
            {"type": "arrow", "x1": 490, "y1": 390, "x2": 490, "y2": 540, "color": "#dc2626"},
            {"type": "text", "x": 505, "y": 520, "text": "G", "size": 18, "color": "#dc2626"},
            {"type": "arrow", "x1": 490, "y1": 300, "x2": 490, "y2": 160, "color": "#059669"},
            {"type": "text", "x": 505, "y": 180, "text": "N", "size": 18, "color": "#059669"},
            {"type": "arrow", "x1": 580, "y1": 345, "x2": 710, "y2": 345, "color": "#d97706"},
            {"type": "text", "x": 690, "y": 320, "text": "f", "size": 18, "color": "#d97706"},
        ]
    else:
        raise WhiteboardError("未知白板模板", "invalid_request")
    title = TEMPLATES[name] + ("（通用示意）" if topic else "")
    return validate_scene({"title": title, "elements": elements})


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except ValueError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def generate_scene(topic: str, subject_name: str = "",
                   model: Optional[str] = None,
                   template: Optional[str] = None,
                   subject_id: Optional[str] = None,
                   user_id: Optional[str] = None) -> Dict[str, Any]:
    topic = (topic or "").strip()
    # 2026-10-31 白板落地修正：此前只拿自由文本 topic 直接调模型，
    # 课件内容完全不参与——"软件工程+第1章"被模型凭常识画成"有理数"。
    # 现在先检索当前学科课件真实片段作为绘图依据；检索不到就明确
    # 输出"课件未覆盖"，不再编造。
    course_evidence = ""
    if subject_id and topic:
        try:
            from services import ai_service
            _ctx = ai_service.retrieve_and_build_context(
                question=topic, subject_id=subject_id,
                viewer_user_id=user_id)
            course_evidence = (_ctx.get("context") or "").strip()
        except Exception as e:
            logger.warning(f"白板课件依据检索失败（继续，但会标注无依据）: {e}")
            course_evidence = ""
    if template == "concept":
        if not topic:
            raise WhiteboardError("概念图需要主题", "invalid_request")
        try:
            concept_prompt = _CONCEPT_PROMPT.format(topic=topic[:200])
            if course_evidence:
                concept_prompt += (
                    "\n\n## 当前学科课件中的真实依据（绘图中涉及的定义/例子/易错点"
                    "**必须出自这里**，不得使用课件之外的学科内容）\n"
                    + course_evidence[:6000])
            else:
                concept_prompt += (
                    "\n\n## 注意：当前学科课件中没有检索到与该主题相关的依据。"
                    "输出一个 title 为「课件未覆盖该主题」的画布"
                    "（elements 只含一条居中提示文本），不要凭通用知识编造。")
            answer = call_llm(concept_prompt,
                              system_message="你是教学白板绘图助手，只输出 JSON。",
                              model=model)
        except Exception as e:
            logger.warning(f"概念图生成失败: {type(e).__name__}: {e}")
            raise WhiteboardError("概念图生成服务暂时不可用", "model_error")
        raw = _extract_json(answer)
        if raw is None:
            raise WhiteboardError("模型输出不是合法 JSON", "structured_output_error")
        scene = validate_scene(raw)
        scene["topic"] = topic
        return scene
    if template:
        return template_scene(template, topic)
    if not topic:
        raise WhiteboardError("缺少白板主题", "invalid_request")
    prompt = _PROMPT.format(topic=topic[:200])
    if subject_name:
        prompt = f"学科：{subject_name[:40]}\n" + prompt
    if course_evidence:
        prompt += (
            "\n\n## 当前学科课件中的真实依据（内容必须出自这里，不得编造）\n"
            + course_evidence[:6000])
    else:
        prompt += (
            "\n\n## 注意：当前学科课件中没有检索到与该主题相关的依据。"
            "title 必须为「课件未覆盖该主题」，elements 只放一条居中提示文本，"
            "不要凭通用知识编造任何学科内容。")
    try:
        answer = call_llm(prompt, system_message="你是教学白板绘图助手，只输出 JSON。",
                          model=model)
    except Exception as e:
        logger.warning(f"白板生成失败: {type(e).__name__}: {e}")
        raise WhiteboardError("白板生成服务暂时不可用", "model_error")
    raw = _extract_json(answer)
    if raw is None:
        raise WhiteboardError("模型输出不是合法 JSON", "structured_output_error")
    scene = validate_scene(raw)
    scene["topic"] = topic
    return scene


__all__ = ["WhiteboardError", "validate_scene", "generate_scene", "template_scene",
           "TEMPLATES", "CANVAS_WIDTH", "CANVAS_HEIGHT"]
