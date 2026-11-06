"""
统一“资料范围”模块（2026-10-07 统一伴学改造）

解决的问题（上一轮资料范围功能的可靠缺口）：
- 只按 basename 识别文件：同名资料会互相串；课程资料的稳定标识是 material_id
  （建库时写入分块 metadata），个人学科则没有 material_id。
- top_k*8 后过滤不保证所选资料能被召回：这里改成先算范围、再做范围检索，
  并在检索为空时给出明确的“所选资料内未命中”而不是静默扩大到全部。
- 区分「全部资料 / 明确选择若干资料 / 选择为空 / 资料已失效」四种状态。
- 缓存键包含用户、学科、范围与资料版本，避免跨范围复用。

对外只暴露稳定数据结构：
    {
      "mode": "all" | "selected" | "empty",
      "subject_id": str,
      "course_id": Optional[str],
      "selected": [ {"doc_id","material_id","file_name","status"} ],
      "invalid":  [ {"doc_id"|"file_name","reason"} ],   # 失效/无权/已删除
      "signature": str,     # 稳定缓存键
      "label": str,         # 给用户看的中文说明
    }
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List, Optional, Set

from utils.logger import logger

# 资料标识前缀：
#   m:<material_id>  课程资料（稳定标识，重建索引后不变）
#   f:<filename>     个人/历史学科的目录内文件（同一学科内文件名唯一）
DOC_PREFIX_MATERIAL = "m:"
DOC_PREFIX_FILE = "f:"


def _norm_name(name: Any) -> str:
    return str(name or "").strip().lower().replace("\\", "/").split("/")[-1]


def _file_doc_id(filename: str) -> str:
    return DOC_PREFIX_FILE + str(filename)


def _material_doc_id(material_id: str) -> str:
    return DOC_PREFIX_MATERIAL + str(material_id)


def _course_id_for_subject(subject_id: str) -> Optional[str]:
    from services import teaching_access
    try:
        bound = teaching_access.subject_scope(subject_id)
    except Exception as e:
        logger.debug(f"读取学科归属失败: {e}")
        return None
    return bound.get("course_id")


def list_scope_materials(user_id: Optional[str], subject_id: str) -> List[Dict[str, Any]]:
    """
    当前用户在该学科有权使用的资料清单（课程资料用 material_id，个人资料用文件名）。

    绝不吞异常成空列表：权限失败向上抛（由接口层转成 403），
    真正没有资料时返回 [] 并由调用方区分“无资料”与“加载失败”。
    """
    course_id = _course_id_for_subject(subject_id)
    items: List[Dict[str, Any]] = []
    if course_id:
        from services import teaching_access
        materials = teaching_access.visible_materials(user_id, course_id)
        for m in materials:
            items.append({
                "doc_id": _material_doc_id(m["id"]),
                "material_id": m["id"],
                "file_name": m.get("filename") or m.get("name") or "",
                "status": m.get("status") or "ready",
                "visibility": m.get("visibility"),
                "course_id": course_id,
                "source": "course",
            })
    else:
        from services import knowledge_service
        documents = knowledge_service.get_subject_documents(subject_id)
        for d in documents:
            name = d.get("name") or ""
            items.append({
                "doc_id": _file_doc_id(name),
                "material_id": None,
                "file_name": name,
                "status": "ready",
                "size": d.get("size"),
                "upload_time": d.get("upload_time"),
                "course_id": None,
                "source": "personal",
            })
    from services.knowledge_service import natural_filename_key
    items.sort(key=lambda item: natural_filename_key(item.get("file_name") or ""))
    return items


def _doc_version_token(item: Dict[str, Any]) -> str:
    """资料版本变化的粗略指纹：课程资料取状态+可见性，个人资料取大小+上传时间。"""
    if item.get("material_id"):
        return f"{item['material_id']}:{item.get('status')}:{item.get('visibility')}"
    return f"{item['file_name']}:{item.get('size')}:{item.get('upload_time')}"


def build_scope(user_id: Optional[str], subject_id: str,
                selected_doc_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """
    解析资料范围。

    - selected_doc_ids 为 None/空 → mode="all"（用户显式选择全部）
    - 传入的 doc_id 在当前可见清单中找不到 → 记入 invalid（资料已删除/失去权限），
      并不会因此退化为 all；若最终一个都不剩，mode="empty"（明确表示“无可用依据”）。
    """
    materials = list_scope_materials(user_id, subject_id)
    by_id = {m["doc_id"]: m for m in materials}
    by_name = {_norm_name(m["file_name"]): m for m in materials if m.get("file_name")}
    course_id = _course_id_for_subject(subject_id)

    selected_ids = [str(x).strip() for x in (selected_doc_ids or []) if str(x).strip()]
    invalid: List[Dict[str, Any]] = []

    if not selected_ids:
        mode = "all" if materials else "empty"
        chosen = list(materials)
    else:
        mode = "selected"
        chosen = []
        seen: Set[str] = set()
        for raw in selected_ids:
            hit = by_id.get(raw)
            if not hit and not raw.startswith((DOC_PREFIX_MATERIAL, DOC_PREFIX_FILE)):
                # 兼容旧前端/旧调用传文件名：按文件名解析到稳定 doc_id
                hit = by_name.get(_norm_name(raw))
            if not hit:
                invalid.append({"doc_id": raw, "reason": "资料不存在或你已无权使用"})
                continue
            if hit["doc_id"] in seen:
                continue
            seen.add(hit["doc_id"])
            chosen.append(hit)
        if not chosen:
            mode = "empty"

    version_tokens = sorted(_doc_version_token(m) for m in chosen)
    signature_raw = "|".join([
        str(user_id or "anonymous"), str(subject_id),
        str(course_id or "-"), mode,
        ",".join(sorted(m["doc_id"] for m in chosen)),
        ",".join(version_tokens),
    ])
    signature = hashlib.sha256(signature_raw.encode("utf-8")).hexdigest()[:24]

    if mode == "all":
        label = f"全部资料（{len(chosen)} 份）"
    elif mode == "selected":
        label = f"所选资料（{len(chosen)} 份）"
    else:
        label = "未选择可用资料"

    return {
        "mode": mode,
        "subject_id": subject_id,
        "course_id": course_id,
        "selected": chosen,
        "invalid": invalid,
        "signature": signature,
        "label": label,
        "all_count": len(materials),
    }


def _chunk_doc_id(doc: Any, name_index: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """把一个检索到的 chunk 归到某个 doc_id（稳定标识优先，文件名回退）。"""
    meta = getattr(doc, "metadata", None) or {}
    material_id = meta.get("material_id")
    if material_id:
        return _material_doc_id(material_id)
    for key in ("file_name", "filename", "doc_name", "name", "source"):
        value = meta.get(key)
        if not value:
            continue
        norm = _norm_name(value)
        hit = name_index.get(norm)
        if hit:
            return hit["doc_id"]
        return _file_doc_id(norm)
    return None


def filter_documents_for_scope(scope: Dict[str, Any], docs: List[Any]) -> List[Any]:
    """按资料范围过滤检索结果（进入模型上下文 / 引用之前必须调用）。"""
    mode = scope.get("mode")
    if mode == "all":
        return list(docs or [])
    if mode == "empty":
        return []
    allowed: Set[str] = {m["doc_id"] for m in scope.get("selected") or []}
    name_index = {_norm_name(m["file_name"]): m for m in scope.get("selected") or [] if m.get("file_name")}
    kept = []
    for doc in docs or []:
        did = _chunk_doc_id(doc, name_index)
        if did in allowed:
            kept.append(doc)
    return kept


def scope_allows_doc_id(scope: Dict[str, Any], doc_id: Optional[str]) -> bool:
    if not doc_id:
        return False
    if scope.get("mode") == "all":
        return True
    if scope.get("mode") != "selected":
        return False
    return doc_id in {m["doc_id"] for m in scope.get("selected") or []}


def scope_to_public(scope: Dict[str, Any]) -> Dict[str, Any]:
    """给前端的稳定结构（便于用户核对自己选了什么）。"""
    return {
        "mode": scope.get("mode"),
        "label": scope.get("label"),
        "signature": scope.get("signature"),
        "selected": [
            {"doc_id": m["doc_id"], "file_name": m["file_name"], "status": m.get("status"),
             "source": m.get("source")}
            for m in scope.get("selected") or []
        ],
        "invalid": scope.get("invalid") or [],
        "all_count": scope.get("all_count", 0),
        "course_id": scope.get("course_id"),
    }


def scope_source_names(scope: Dict[str, Any]) -> List[str]:
    """兼容旧接口（ai_service 的 source_filter 用的就是文件名集合）。"""
    return [m["file_name"] for m in scope.get("selected") or [] if m.get("file_name")]


__all__ = [
    "DOC_PREFIX_MATERIAL",
    "DOC_PREFIX_FILE",
    "build_scope",
    "filter_documents_for_scope",
    "list_scope_materials",
    "scope_allows_doc_id",
    "scope_source_names",
    "scope_to_public",
]
