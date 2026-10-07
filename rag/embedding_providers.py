# -*- coding: utf-8 -*-
"""云端 Embedding 适配层（2026-10-08）。

默认走阿里云百炼 text-embedding-v4（OpenAI 兼容接口），本地 BGE 保留为显式可选。
官方限制（按百炼文档核对）：单次请求最多 10 行、单行最多 8192 token、
维度可选 2048/1536/1024(默认)/768/512/256/128/64；按输入 token 计费
（新加坡 $0.07 / 1M，北京 $0.072，新账号 1M 免费额度）。
地域不同域名不同，因此 base_url 可配。
本模块只做适配 + 限流 + 重试 + 计量，不改变 RAG/LangChain 其余部分。
"""
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "text-embedding-v4"
DEFAULT_DIMENSIONS = 1024
DEFAULT_MAX_ROWS = 10
DEFAULT_MAX_TOKENS_PER_LINE = 8192


class CloudEmbeddingError(RuntimeError):
    """云端 Embedding 不可用（凭证/网络/配额）。绝不静默降级成本地向量。"""


class CloudEmbeddings:
    """LangChain Embeddings 契约的云端实现（requests，OpenAI 兼容接口）。"""

    provider = "dashscope"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 base_url: Optional[str] = None, dimensions: Optional[int] = None,
                 max_rows: Optional[int] = None, concurrency: Optional[int] = None,
                 max_rpm: Optional[int] = None, max_retries: Optional[int] = None,
                 timeout: Optional[float] = None, budget_tokens: Optional[int] = None,
                 max_tokens_per_line: Optional[int] = None,
                 session: Any = None):
        self.api_key = api_key if api_key is not None else os.getenv("DASHSCOPE_API_KEY", "").strip()
        self.model_name = model or os.getenv("EMBEDDING_CLOUD_MODEL", DEFAULT_MODEL)
        self.base_url = (base_url or os.getenv("EMBEDDING_CLOUD_BASE_URL", "") or os.getenv("DASHSCOPE_BASE_URL", "") or DEFAULT_BASE_URL).rstrip("/")
        dim = dimensions if dimensions is not None else os.getenv("EMBEDDING_CLOUD_DIMENSIONS", str(DEFAULT_DIMENSIONS))
        self.dimensions = int(dim)
        self.max_rows = max(1, int(max_rows or os.getenv("EMBEDDING_CLOUD_MAX_ROWS", str(DEFAULT_MAX_ROWS))))
        mpl = max_tokens_per_line if max_tokens_per_line is not None else os.getenv("EMBEDDING_CLOUD_MAX_TOKENS_PER_LINE", str(DEFAULT_MAX_TOKENS_PER_LINE))
        self.max_tokens_per_line = int(mpl)
        self.concurrency = max(1, int(concurrency if concurrency is not None else os.getenv("EMBEDDING_CLOUD_CONCURRENCY", "4")))
        rpm = max_rpm if max_rpm is not None else os.getenv("EMBEDDING_CLOUD_MAX_RPM", "60")
        self.max_rpm = max(0, int(rpm))
        retries = max_retries if max_retries is not None else os.getenv("EMBEDDING_CLOUD_MAX_RETRIES", "4")
        self.max_retries = max(0, int(retries))
        self.timeout = float(timeout or os.getenv("EMBEDDING_CLOUD_TIMEOUT", "60"))
        budget = budget_tokens if budget_tokens is not None else os.getenv("EMBEDDING_CLOUD_BUDGET_TOKENS", "0")
        self.budget_tokens = int(budget or 0)
        self._session = session
        self._lock = threading.Lock()
        self._last_call = 0.0
        self.stats: Dict[str, Any] = {"requests": 0, "ok": 0, "failed": 0, "input_tokens": 0, "rows": 0, "retries": 0, "budget_exceeded": False}

    # ---------- LangChain Embeddings 契约 ----------
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        vectors, _ = self.embed_documents_with_stats(texts)
        return vectors

    def embed_query(self, text: str) -> List[float]:
        self._require_key()
        return self._embed_batch([text])[0]

    def _require_key(self) -> None:
        if not self.api_key:
            raise CloudEmbeddingError("缺少 DASHSCOPE_API_KEY：云端 Embedding 未配置。请在 .env 设置 DASHSCOPE_API_KEY（阿里云百炼控制台 -> API-KEY 管理），或把 EMBEDDING_PROVIDER 设为 local 使用本地模型。")

    def embed_documents_with_stats(self, texts: List[str]) -> Tuple[List[List[float]], Dict[str, Any]]:
        if not texts:
            return [], dict(self.stats)
        self._require_key()
        prepared = [self._guard_length(t) for t in texts]
        batches = [prepared[i:i + self.max_rows] for i in range(0, len(prepared), self.max_rows)]
        results: List[Optional[List[float]]] = [None] * len(prepared)
        jobs = list(enumerate(batches))
        if self.concurrency > 1 and len(jobs) > 1:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                pairs = list(pool.map(lambda jb: (jb[0], self._embed_batch(jb[1])), jobs))
        else:
            pairs = [(i, self._embed_batch(b)) for i, b in jobs]
        for i, vecs in pairs:
            start = i * self.max_rows
            for j, v in enumerate(vecs):
                if start + j < len(results):
                    results[start + j] = v
        missing = [k for k, v in enumerate(results) if v is None]
        if missing:
            raise CloudEmbeddingError(f"云端返回缺失 {len(missing)} 条向量")
        return [v for v in results if v is not None], dict(self.stats)

    def _guard_length(self, text: str) -> str:
        limit = self.max_tokens_per_line
        if len(text) <= limit:
            return text
        logger.warning(f"云端 Embedding：单条长度 {len(text)} 超过上限 {limit}，已截断")
        return text[:limit]

    def _throttle(self) -> None:
        if self.max_rpm <= 0:
            return
        with self._lock:
            gap = 60.0 / self.max_rpm
            wait = gap - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _embed_batch(self, batch: List[str]) -> List[List[float]]:
        import requests
        if self.budget_tokens and self.stats["input_tokens"] >= self.budget_tokens:
            self.stats["budget_exceeded"] = True
            raise CloudEmbeddingError(f"已达到 Embedding 调用预算（{self.budget_tokens} token），已暂停；可提高 EMBEDDING_CLOUD_BUDGET_TOKENS 后重试（已成功向量已缓存，不会重复计费）")
        url = self.base_url + "/embeddings"
        body = json.dumps({"model": self.model_name, "input": batch, "encoding_format": "float"}).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        last_error = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                with self._lock:
                    self.stats["requests"] += 1
                poster = self._session.post if self._session is not None else __import__("requests").post
                resp = poster(url, data=body, headers=headers, timeout=self.timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    rows = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
                    usage = data.get("usage") or {}
                    with self._lock:
                        self.stats["ok"] += 1
                        self.stats["rows"] += len(rows)
                        self.stats["input_tokens"] += int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
                    return [r.get("embedding") or [] for r in rows]
                detail = (resp.text or "")[:300]
                if resp.status_code in (401, 403):
                    raise CloudEmbeddingError(f"云端 Embedding 鉴权失败（HTTP {resp.status_code}）：请检查 DASHSCOPE_API_KEY 与账号地域/Workspace 是否与接口域名匹配。详情: {detail}")
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {resp.status_code}: {detail}"
                    sleep_s = min(30.0, (2 ** attempt) * 0.5)
                    with self._lock:
                        self.stats["retries"] += 1
                    logger.warning(f"云端 Embedding 限流/服务错误，{sleep_s:.1f}s 后重试（第 {attempt + 1}/{self.max_retries + 1} 次）: {last_error}")
                    time.sleep(sleep_s)
                    continue
                raise CloudEmbeddingError(f"云端 Embedding 请求失败（HTTP {resp.status_code}）: {detail}")
            except CloudEmbeddingError:
                with self._lock:
                    self.stats["failed"] += 1
                raise
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                with self._lock:
                    self.stats["retries"] += 1
                if attempt < self.max_retries:
                    sleep_s = min(30.0, (2 ** attempt) * 0.5)
                    logger.warning(f"云端 Embedding 网络异常，{sleep_s:.1f}s 后重试: {last_error}")
                    time.sleep(sleep_s)
                    continue
        with self._lock:
            self.stats["failed"] += 1
        raise CloudEmbeddingError(f"云端 Embedding 不可用（已重试 {self.max_retries} 次）: {last_error}")


def cloud_signature(embeddings: Any) -> str:
    """缓存签名的一部分：供应商 + 模型 + 维度（向量兼容性取决于这三者）。"""
    provider = getattr(embeddings, "provider", None) or "cloud"
    model = getattr(embeddings, "model_name", None) or "unknown"
    dim = getattr(embeddings, "dimensions", None)
    return f"{provider}/{model}/{dim if dim is not None else '?'}"


def build_cloud_embeddings(**kwargs: Any) -> CloudEmbeddings:
    return CloudEmbeddings(**kwargs)