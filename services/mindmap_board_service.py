"""思维导图板书生成（2026-11-14 白板重做）

AI 只负责产出**结构化导图 JSON**（root/children 树），前端 MindBoard 渲染成
可编辑节点；后端做白名单与限深限宽校验，不允许任何非字符串内容进前端。

页面定位：优先按 file_name + page 从页面索引逐页文本里取"这一页课件"原文
（含扫描件 OCR），取不到再退回 scoped 检索；两路都没有时明确报"无可提取文字"，
不编造学科内容。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from services.ai_service import call_llm
from utils.logger import logger

MAX_DEPTH = 3        # 根算第 0 层，最多 4 级
MAX_CHILDREN = 6     # 每个节点最多 6 个孩子
MAX_TEXT_LEN = 40

_PROMPT = """你是一名教学助手。请把下面这页课件的内容整理成一张**思维导图**。

## 输出要求（严格 JSON，不要输出任何其他内容）
{{
  "root": {{
    "text": "中心主题（不超过12字）",
    "children": [
      {{"text": "一级分支", "children": [{{"text": "细节", "children": []}}]}}
    ]
  }}
}}

## 规则
1. 层级不超过 {max_depth} 层；每个节点孩子不超过 {max_children} 个。
2. 节点文字精炼（一级不超过16字，细节不超过24字），概括这页的关键概念、步骤与结论。
3. 只依据给出的课件内容组织导图，不要补充课件之外的知识。
4. 不要输出 Markdown、解释或代码块，只输出 JSON。
"""


class MindmapError(Exception):
    def __init__(self, message: str, error_type: str = "invalid_mindmap"):
        super().__init__(message)
        self.message = message
        self.error_type = error_type


def _clean_text(value: Any, limit: int = MAX_TEXT_LEN) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _normalize_node(raw: Any, depth: int) -> Optional[Dict[str, Any]]:
    if depth > MAX_DEPTH or not isinstance(raw, dict):
        return None
    text = _clean_text(raw.get("text") or raw.get("name") or raw.get("label"))
    if not text:
        return None
    children_raw = raw.get("children") if isinstance(raw.get("children"), list) else []
    children = []
    for child_raw in children_raw[:MAX_CHILDREN]:
        child = _normalize_node(child_raw, depth + 1)
        if child:
            children.append(child)
    return {"text": text, "children": children}


def normalize_tree(raw: Any) -> Dict[str, Any]:
    """白名单校验：只留 text/children，限深限宽；兼容 {root:{...}} 与 {name:{...}} 两种外层。"""
    if isinstance(raw, dict):
        root_raw = raw["root"] if isinstance(raw.get("root"), dict) else raw
        node = _normalize_node(root_raw, 0)
        if node:
            return node
    raise MindmapError("模型输出不是合法的思维导图", "structured_output_error")


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


def _load_page_text(subject_id: str, file_name: str, page: int) -> str:
    """从页面索引按页取文本（page 为 0 基页码，与 metadata 口径一致）。"""
    from services import page_index
    rows = page_index.list_page_texts(subject_id, file_name)
    target = next((r for r in rows if int(r.get("page", -1)) == int(page)), None)
    return (target.get("text") or "").strip() if target else ""


def generate_mindmap(subject_id: Optional[str], user_id: Optional[str] = None,
                     file_name: Optional[str] = None, page: Optional[int] = None,
                     topic: Optional[str] = None, model: Optional[str] = None,
                     scope=None) -> Dict[str, Any]:
    """把"这一页课件"整理成思维导图树；页面文本缺失时退回 scoped 检索。"""
    page_text = ""
    page_ref = ""
    if subject_id and file_name and page is not None:
        try:
            page_text = _load_page_text(subject_id, file_name, page)
            if page_text:
                page_ref = f"{file_name} 第 {int(page) + 1} 页"
        except Exception as e:
            logger.warning(f"页面文本读取失败（改用检索兜底）: {e}")
    if not page_text and subject_id:
        try:
            from services import ai_service
            ctx = ai_service.retrieve_and_build_context_scoped(
                question=topic or file_name or "这一页课件",
                subject_id=subject_id, viewer_user_id=user_id, scope=scope)
            page_text = (ctx.get("context") or "").strip()
            page_ref = "课件检索片段"
        except Exception as e:
            logger.warning(f"导图课件依据检索失败: {e}")
    if not page_text:
        raise MindmapError("这页课件没有可提取的文字（未建页面索引或扫描件未 OCR）", "no_content")

    prompt = _PROMPT.format(max_depth=MAX_DEPTH + 1, max_children=MAX_CHILDREN)
    prompt += f"\n\n## 课件内容（{page_ref}）\n" + page_text[:6000]
    try:
        answer = call_llm(prompt, system_message="你是教学思维导图助手，只输出 JSON。",
                          model=model)
    except Exception as e:
        logger.warning(f"思维导图生成失败: {type(e).__name__}: {e}")
        raise MindmapError("思维导图生成服务暂时不可用", "model_error")
    root = normalize_tree(_extract_json(answer))
    return {"root": root, "source": {"file_name": file_name, "page": page, "ref": page_ref}}


__all__ = ["MindmapError", "normalize_tree", "generate_mindmap",
           "MAX_DEPTH", "MAX_CHILDREN"]
