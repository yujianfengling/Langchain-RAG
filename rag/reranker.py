"""Cross-Encoder 重排（2026-10-24）

混合检索（向量 + BM25 + RRF）先召回候选，再用 Cross-Encoder 对 (query, chunk)
逐对打分，取 top_k。默认关闭，避免测试/小机器无谓下载 2GB 模型：

    RERANK_ENABLED=true
    RERANK_MODEL=BAAI/bge-reranker-v2-m3

模型加载失败时**降级为原顺序**，并在诊断里标明 rerank_available=false；
绝不因为重排不可用而让检索整体失败。
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import logger

DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
_MODEL = None
_LOCK = threading.Lock()
_FAILED = False


def _env_on(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _env_on("RERANK_ENABLED", "0")


def model_name() -> str:
    return (os.getenv("RERANK_MODEL") or DEFAULT_RERANK_MODEL).strip()


def _get_model():
    global _MODEL, _FAILED
    if _MODEL is not None or _FAILED:
        return _MODEL
    with _LOCK:
        if _MODEL is not None or _FAILED:
            return _MODEL
        try:
            from sentence_transformers import CrossEncoder
            _MODEL = CrossEncoder(model_name())
            logger.info(f"Cross-Encoder 已加载: {model_name()}")
        except Exception as e:
            _FAILED = True
            logger.warning(f"Cross-Encoder 加载失败，检索将跳过重排: {type(e).__name__}: {e}")
        return _MODEL


def _content(item: Dict[str, Any]) -> str:
    doc = item.get("document")
    if doc is None:
        return str(item.get("content") or "")
    return str(getattr(doc, "page_content", "") or "")


def rerank(query: str, items: List[Dict[str, Any]],
           top_k: int) -> Tuple[List[Dict[str, Any]], bool]:
    """返回 (重排后的 top_k, 是否真的用了重排)。"""
    if not items:
        return [], False
    if not enabled():
        return items[:top_k], False
    model = _get_model()
    if model is None:
        return items[:top_k], False
    pairs = [(query, _content(item)[:2000]) for item in items]
    try:
        scores = model.predict(pairs, batch_size=16, show_progress_bar=False)
    except Exception as e:
        logger.warning(f"Cross-Encoder 打分失败，跳过重排: {type(e).__name__}: {e}")
        return items[:top_k], False
    ranked = []
    for item, score in zip(items, scores):
        row = dict(item)
        row["rerank_score"] = float(score)
        row["original_fused_score"] = row.get("score")
        ranked.append(row)
    ranked.sort(key=lambda row: float(row.get("rerank_score") or 0), reverse=True)
    return ranked[:top_k], True


__all__ = ["enabled", "model_name", "rerank", "DEFAULT_RERANK_MODEL"]
