# -*- coding: utf-8 -*-
"""chunk 级向量缓存（2026-10-08）

目标：资料新增/修改/删除时，**未变化的分块直接复用已有向量**，不再调用 Embedding。
这在"每次导入都整库重建"的现有结构下就能省掉绝大部分模型调用——
FAISS/BM25 的索引文件仍需按学科整体重组（见 _build_index_with_rollback 的 staging+切换），
但"向量化"这一步变成了增量：只有新增/变化的分块会被真正编码。

键的构成（**不只按文件名**）：sha256(SCHEMA | 模型签名 | 分块正文)。
- 模型签名变化（换 embedding 模型）→ 键变化 → 全部重新编码；
- 分块正文变化（改了资料/改了切分配置导致分块不同）→ 该分块重新编码；
- 未变化的分块 → 命中缓存，0 次模型调用。

存储：data/cache/embedding_cache.db（SQLite，key PRIMARY KEY, dim, vec BLOB(float32)）。
只读不清理；清掉它只会导致一次全量重编码，不会影响正确性。
"""
import hashlib
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = __import__("logging").getLogger(__name__)

# emb-v2（2026-10-08）：签名加入"供应商 + 维度"。
# 旧 BGE 向量与云端向量**维度可能相同但语义空间不同**，绝不能互相复用；
# 提升 schema 版本可让旧缓存整体失效，杜绝混用。
SCHEMA = "emb-v2"

_LOCK = threading.Lock()
_CACHE: Optional["EmbeddingCache"] = None
_CACHE_PATH: Optional[str] = None


def _model_signature(embeddings: Any) -> str:
    """稳定的模型签名（换供应商/模型/维度必须让缓存失效）。

    云端适配器带 provider/dimensions，签名形如 "dashscope/text-embedding-v4/1024"；
    本地模型形如 "local/BAAI/bge-small-zh-v1.5"。
    """
    provider = getattr(embeddings, "provider", None)
    if provider:
        model = getattr(embeddings, "model_name", None) or getattr(embeddings, "model", None) or "unknown"
        dim = getattr(embeddings, "dimensions", None)
        return f"{provider}/{model}/{dim if dim is not None else '?'}"
    for attr in ("model_name", "model", "model_id", "model_path"):
        value = getattr(embeddings, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    inner = getattr(embeddings, "client", None)
    if inner is not None:
        name = getattr(inner, "model_name", None) or getattr(inner, "model", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
    return type(embeddings).__name__


def make_key(text: str, signature: str) -> str:
    h = hashlib.sha256()
    h.update(SCHEMA.encode("utf-8"))
    h.update(b"\x00")
    h.update(signature.encode("utf-8"))
    h.update(b"\x00")
    h.update((text or "").encode("utf-8"))
    return h.hexdigest()[:40]


def _db_path() -> str:
    from config.path_config import get_data_dir
    base = os.path.join(str(get_data_dir()), "cache")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "embedding_cache.db")


class EmbeddingCache:
    """SQLite 向量缓存（进程内复用连接；跨进程共享同一文件）。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or _db_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._conn_lock = threading.Lock()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS embeddings ("
                " key TEXT PRIMARY KEY, dim INTEGER NOT NULL, vec BLOB NOT NULL)")
            conn.commit()
            self._conn = conn
        return self._conn

    def get_many(self, keys: Sequence[str]) -> Dict[str, Any]:
        import numpy as np
        out: Dict[str, Any] = {}
        if not keys:
            return out
        with self._conn_lock:
            conn = self._connection()
            chunk = 400
            for start in range(0, len(keys), chunk):
                batch = list(keys[start:start + chunk])
                placeholders = ",".join("?" * len(batch))
                cur = conn.execute(
                    f"SELECT key, vec FROM embeddings WHERE key IN ({placeholders})", batch)
                for key, blob in cur.fetchall():
                    out[key] = np.frombuffer(blob, dtype="float32").copy()
        return out

    def put_many(self, items: Dict[str, Any]) -> int:
        import numpy as np
        if not items:
            return 0
        rows: List[Tuple[str, int, bytes]] = []
        for key, vec in items.items():
            arr = np.asarray(vec, dtype="float32")
            rows.append((key, int(arr.shape[-1]), arr.tobytes()))
        with self._conn_lock:
            conn = self._connection()
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings (key, dim, vec) VALUES (?,?,?)", rows)
            conn.commit()
        return len(rows)

    def count(self) -> int:
        with self._conn_lock:
            conn = self._connection()
            return int(conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])


def get_cache() -> EmbeddingCache:
    global _CACHE, _CACHE_PATH
    path = _db_path()
    with _LOCK:
        if _CACHE is None or _CACHE_PATH != path:
            _CACHE = EmbeddingCache(path)
            _CACHE_PATH = path
        return _CACHE


def reset_cache_for_tests() -> None:
    """测试用：丢弃进程内缓存的连接（不删文件）。"""
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def embed_texts_cached(texts: List[str], embeddings: Any,
                       cache: Optional[EmbeddingCache] = None) -> Tuple[List[Any], Dict[str, Any]]:
    """带缓存的批量编码。返回 (按输入顺序排列的向量列表, 统计)。"""
    stats = {"total": len(texts), "hits": 0, "misses": 0,
             "embed_calls": 0, "embedded_texts": 0,
             "lookup_ms": 0, "embed_ms": 0, "bypassed": False}
    if not texts:
        return [], stats

    # A/B 与排障开关：EMBEDDING_CACHE=0 时完全不读写缓存（等价于改动前的"整库重编码"）
    if str(os.getenv("EMBEDDING_CACHE", "1")).strip().lower() in ("0", "false", "off", "no"):
        stats["bypassed"] = True
        t0 = time.monotonic()
        if hasattr(embeddings, "embed_documents"):
            vectors = embeddings.embed_documents(texts)
            stats["embed_calls"] = 1
        else:
            vectors = [embeddings.embed_query(t) for t in texts]
            stats["embed_calls"] = len(texts)
        stats["embed_ms"] = int((time.monotonic() - t0) * 1000)
        stats["embedded_texts"] = len(vectors)
        stats["misses"] = len(texts)
        return vectors, stats

    signature = _model_signature(embeddings)
    cache = cache or get_cache()
    keys = [make_key(t, signature) for t in texts]

    t0 = time.monotonic()
    found = cache.get_many(keys)
    stats["lookup_ms"] = int((time.monotonic() - t0) * 1000)
    stats["hits"] = len(found)

    missing_idx = [i for i, k in enumerate(keys) if k not in found]
    stats["misses"] = len(missing_idx)

    if missing_idx:
        missing_texts = [texts[i] for i in missing_idx]
        t1 = time.monotonic()
        if hasattr(embeddings, "embed_documents"):
            new_vectors = embeddings.embed_documents(missing_texts)
            stats["embed_calls"] = 1
        elif hasattr(embeddings, "embed_query"):
            new_vectors = [embeddings.embed_query(t) for t in missing_texts]
            stats["embed_calls"] = len(missing_texts)
        else:
            raise RuntimeError("嵌入模型不支持 embed_documents 或 embed_query")
        stats["embed_ms"] = int((time.monotonic() - t1) * 1000)
        stats["embedded_texts"] = len(new_vectors)
        try:
            cache.put_many({keys[i]: v for i, v in zip(missing_idx, new_vectors)})
        except Exception as e:          # 缓存写失败不影响本次构建
            logger.warning(f"向量缓存写入失败（不影响本次构建）: {e}")
        for i, v in zip(missing_idx, new_vectors):
            found[keys[i]] = v
    else:
        logger.info(f"向量缓存全命中: {len(texts)} 条，0 次 Embedding 调用")

    ordered = [found[k] for k in keys]
    return ordered, stats
