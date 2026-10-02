"""
联网题库搜索（Tavily）

用途：知识库证据不足时，从公开网页补充**通用**题目素材，
缓解"每个学生教材不同、本地知识库覆盖不到"的问题。

与项目"证据驱动"原则的关系（重要）：
- 搜索结果只是**素材**，必须再交给 LLM 抽取/重算，才能变成题目。
  绝不把网页正文直接当作答案——实测 Tavily 的 include_answer 语言不稳定
  （中文查询常返回英文摘要），而且公开网页上的答案本身也可能是错的。
- 每道题保留来源 URL，前端标注"来自网络资料"，与"你的资料"区分开。
- 任何失败（未配 key / 网络异常 / 额度耗尽 / 超时）都只返回空列表并记日志，
  由调用方降级到"AI 通用知识出题"，绝不阻断出题链路。
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from services import capability_status, usage_ledger
from utils.logger import logger

# config 包在导入时会加载 .env；这里再显式兜底一次，保证脚本单独跑也生效
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:  # pragma: no cover - dotenv 是声明依赖，缺失时不影响主流程
    pass

TAVILY_ENDPOINT = "https://api.tavily.com/search"

# 正文片段过短的结果没有出题价值（实测有 70~80 字的纯摘要行）
_MIN_CONTENT_CHARS = 120
# 单条素材截断上限，避免把整个网页塞进 prompt
_MAX_CONTENT_CHARS = 4000
_DEFAULT_TIMEOUT = 25


def _api_key() -> str:
    return (os.getenv("TAVILY_API_KEY") or "").strip()


def is_enabled() -> bool:
    """联网题库是否可用（有 key 且开关打开）"""
    if not _api_key():
        return False
    flag = (os.getenv("WEB_SEARCH_ENABLED", "true") or "").strip().lower()
    return flag not in ("0", "false", "no", "off")


def _max_results() -> int:
    try:
        return max(1, min(10, int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))))
    except (TypeError, ValueError):
        return 5


def build_query(topic: str, subject_name: Optional[str] = None) -> str:
    """构造搜题查询词：学科名 + 知识点 + 题目类关键词"""
    topic = (topic or "").strip()
    subject = (subject_name or "").strip()
    core = f"{topic} 例题 习题 答案 解析"
    return f"{subject} {core}".strip() if subject else core


def search_question_material(
    query: str,
    max_results: Optional[int] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    """
    搜索题目素材。

    Returns:
        [{"title", "url", "content"}]；失败或未启用时返回 []（调用方据此降级）
    """
    if not is_enabled():
        logger.info("联网题库未启用（缺少 TAVILY_API_KEY 或 WEB_SEARCH_ENABLED=false）")
        return []

    query = (query or "").strip()
    if not query:
        return []

    payload = {
        "api_key": _api_key(),
        "query": query,
        "max_results": max_results or _max_results(),
        "search_depth": "advanced",
        # 只要正文片段：include_answer 语言不稳定，不作为答案来源
        "include_answer": False,
    }
    try:
        resp = requests.post(TAVILY_ENDPOINT, json=payload, timeout=timeout)
    except Exception as e:
        logger.warning(f"联网搜题请求失败（降级为通用知识出题）: {e}")
        capability_status.safe_report(capability_status.CAP_WEB_SEARCH, False,
                                      reason=f"{type(e).__name__}: {e}")
        return []

    if resp.status_code != 200:
        # 401=key 失效，429=额度/频率，其他为服务侧问题
        logger.warning(f"联网搜题返回 {resp.status_code}（降级为通用知识出题）: {resp.text[:160]}")
        capability_status.safe_report(
            capability_status.CAP_WEB_SEARCH, False,
            reason=f"HTTP {resp.status_code}: {resp.text[:120]}")
        return []

    try:
        data = resp.json()
    except ValueError:
        logger.warning("联网搜题返回内容不是合法 JSON（降级为通用知识出题）")
        capability_status.safe_report(capability_status.CAP_WEB_SEARCH, False,
                                      reason="返回内容不是合法 JSON")
        return []

    material: List[Dict[str, Any]] = []
    for item in (data.get("results") or []):
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip()
        if len(content) < _MIN_CONTENT_CHARS:
            continue
        material.append({
            "title": (item.get("title") or "").strip()[:200],
            "url": (item.get("url") or "").strip()[:500],
            "content": content[:_MAX_CONTENT_CHARS],
        })
    logger.info(f"联网搜题完成: query={query!r}, 命中素材 {len(material)} 条")
    capability_status.safe_report(capability_status.CAP_WEB_SEARCH, True,
                                  reason=f"命中素材 {len(material)} 条")
    usage_ledger.record(usage_ledger.KIND_WEB_SEARCH)
    return material


__all__ = ["is_enabled", "build_query", "search_question_material"]
