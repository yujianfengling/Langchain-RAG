# -*- coding: utf-8 -*-
"""分词缓存（2026-10-08）

为什么需要：BM25 建库时对**每个 chunk** 调 `_tokenize_chinese`（jieba）。
实测 1,560 块热构建里 BM25 占 1.69s（约占热构建 89%），
而 13k 块的大库对应约 14s——这是"向量缓存生效后"剩下的主要 warm 成本。

做法：按 (分词模式 | 正文) 缓存已分词结果。未变化的分块直接复用 token，不再分词；
换分词模式（char/word）即整体失效。与 embedding_cache 同一套路，
存 data/cache/token_cache.db，只增不删（清掉只会导致一次重新分词，不影响正确性）。
"""
import hashlib
import os
import sqlite3
import threading
from typing import List, Optional, Sequence

logger = __import__("logging").getLogger(__name__)

SCHEMA = "tok-v1"
SEP = "\x1f"
_LOCK = threading.Lock()
_CACHE: Optional["TokenCache"] = None
_CACHE_PATH: Optional[str] = None


def make_key(text: str, mode: str) -> str:
    h = hashlib.sha256()
    h.update(SCHEMA.encode("utf-8"))
    h.update(b"\x00")
    h.update(mode.encode("utf-8"))
    h.update(b"\x00")
    h.update((text or "").encode("utf-8"))
    return h.hexdigest()[:40]


def _db_path() -> str:
    from config.path_config import get_data_dir
    base = os.path.join(str(get_data_dir()), "cache")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "token_cache.db")


class TokenCache:
    def __init__(self, path: Optional[str] = None):
        self.path = path or _db_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.execute("CREATE TABLE IF NOT EXISTS tokens ("
                         " key TEXT PRIMARY KEY, tokens TEXT NOT NULL)")
            conn.commit()
            self._conn = conn
        return self._conn

    def get_many(self, keys: Sequence[str]):
        out = {}
        if not keys:
            return out
        with self._lock:
            conn = self._connection()
            for start in range(0, len(keys), 400):
                batch = list(keys[start:start + 400])
                placeholders = ",".join("?" * len(batch))
                for key, blob in conn.execute(
                        f"SELECT key, tokens FROM tokens WHERE key IN ({placeholders})", batch):
                    out[key] = blob.split(SEP) if blob else []
        return out

    def put_many(self, items) -> int:
        if not items:
            return 0
        rows = [(k, SEP.join(v)) for k, v in items.items()]
        with self._lock:
            conn = self._connection()
            conn.executemany("INSERT OR REPLACE INTO tokens (key, tokens) VALUES (?,?)", rows)
            conn.commit()
        return len(rows)

    def count(self) -> int:
        with self._lock:
            return int(self._connection().execute("SELECT COUNT(*) FROM tokens").fetchone()[0])


def get_cache() -> TokenCache:
    global _CACHE, _CACHE_PATH
    path = _db_path()
    with _LOCK:
        if _CACHE is None or _CACHE_PATH != path:
            _CACHE = TokenCache(path)
            _CACHE_PATH = path
        return _CACHE


def reset_cache_for_tests() -> None:
    global _CACHE, _CACHE_PATH
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None


def tokenize_many(texts: List[str], mode: Optional[str] = None):
    """批量分词（带缓存）。返回 (token 列表的列表, 统计)。"""
    from .bm25_retriever import _tokenize_chinese, get_tokenizer_mode
    mode = mode or get_tokenizer_mode()
    stats = {"total": len(texts), "hits": 0, "misses": 0, "tokenize_ms": 0, "bypassed": False}
    if not texts:
        return [], stats

    if str(os.getenv("TOKEN_CACHE", "1")).strip().lower() in ("0", "false", "off", "no"):
        import time as _t
        t0 = _t.monotonic()
        result = [_tokenize_chinese(t, mode) for t in texts]
        stats["bypassed"] = True
        stats["misses"] = len(texts)
        stats["tokenize_ms"] = int((_t.monotonic() - t0) * 1000)
        return result, stats

    import time as _t
    cache = get_cache()
    keys = [make_key(t, mode) for t in texts]
    t0 = _t.monotonic()
    found = cache.get_many(keys)
    stats["hits"] = sum(1 for k in keys if k in found)
    stats["misses"] = len(texts) - stats["hits"]
    missing_idx = [i for i, k in enumerate(keys) if k not in found]
    if missing_idx:
        new_tokens = [_tokenize_chinese(texts[i], mode) for i in missing_idx]
        try:
            cache.put_many({keys[i]: v for i, v in zip(missing_idx, new_tokens)})
        except Exception as e:
            logger.warning(f"分词缓存写入失败（不影响本次构建）: {e}")
        for i, v in zip(missing_idx, new_tokens):
            found[keys[i]] = v
    stats["tokenize_ms"] = int((_t.monotonic() - t0) * 1000)
    return [found[k] for k in keys], stats
