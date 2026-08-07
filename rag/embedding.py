"""
Embedding模块
负责管理本地Embedding模型，提供文本向量化功能
使用LangChain Embeddings接口，支持HuggingFace模型

v2.4 更新（P1 修复）：
- 新增 EMBEDDING_LOAD_MODE 加载策略（auto/offline/online，默认 auto）
- 本地缓存优先：local_files_only=True，不依赖手动设置 HF_HUB_OFFLINE
- 线程安全单例 + 状态机（not_loaded/loading/ready/failed）
- 失败进入明确降级状态（bm25_degraded），支持冷却后自动重试与人工强制重载
- 日志不包含用户完整本地路径
"""

import os
import re
import time
import logging
import threading
from pathlib import Path
from typing import List, Optional, Union, Dict, Any

logger = logging.getLogger(__name__)

# 确保.env在模型下载前加载（HF_ENDPOINT等环境变量）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 尝试导入LangChain Embeddings接口
try:
    from langchain_core.embeddings import Embeddings
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    # 创建一个简单的Embeddings替代基类
    class Embeddings:
        """LangChain Embeddings的简单替代基类"""
        def embed_documents(self, texts: List[str]) -> List[List[float]]:
            raise NotImplementedError
        def embed_query(self, text: str) -> List[float]:
            raise NotImplementedError

# 尝试导入HuggingFaceEmbeddings
try:
    from langchain_community.embeddings import HuggingFaceEmbeddings
    HUGGINGFACE_AVAILABLE = True
except ImportError:
    HUGGINGFACE_AVAILABLE = False

# ============ 加载策略常量 ============
LOAD_MODE_AUTO = "auto"
LOAD_MODE_OFFLINE = "offline"
LOAD_MODE_ONLINE = "online"
VALID_LOAD_MODES = (LOAD_MODE_AUTO, LOAD_MODE_OFFLINE, LOAD_MODE_ONLINE)

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"

# 孤立代理字符（lone surrogate）：PyMuPDF 提取数学符号时可能产生残缺的代理对，
# 会导致 sentence-transformers tokenizer 抛出 TextEncodeInput 错误。
# 匹配：单独的高代理（后无低代理）或单独的低代理（前无高代理）。
_LONE_SURROGATE_RE = re.compile(
    r'[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]'
)


def _sanitize_unicode_text(text) -> str:
    """
    清理文本中的孤立代理字符（嵌入前安全处理）

    - 非字符串统一转为 str
    - 移除孤立代理字符（保留合法代理对，如数学符号）
    """
    if not isinstance(text, str):
        text = str(text)
    if _LONE_SURROGATE_RE.search(text):
        return _LONE_SURROGATE_RE.sub('', text)
    return text

# 失败重试冷却（秒）：失败后冷却期内快速失败（不阻塞、不重复加载），冷却后自动重试
DEFAULT_RETRY_COOLDOWN = 120

# ============ 全局状态（线程安全） ============
_embedding_model = None
_state_lock = threading.Lock()
_load_lock = threading.Lock()  # 保证并发首次调用只加载一次
_state: Dict[str, Any] = {
    "status": "not_loaded",          # not_loaded | loading | ready | failed
    "load_mode": LOAD_MODE_AUTO,
    "source": None,                  # explicit_local_dir | local_cache | online | unavailable
    "model": DEFAULT_EMBEDDING_MODEL,
    "dimension": None,
    "error": None,
    "error_type": None,
    "duration": None,
    "last_error_at": None,
}


class BatchedHuggingFaceEmbeddings(Embeddings):
    """
    支持批量处理的HuggingFace Embeddings包装器
    解决sentence-transformers 5.6.0与transformers 5.14.1的兼容性问题
    """

    def __init__(self, model_name: str, model_kwargs: dict = None,
                 encode_kwargs: dict = None, batch_size: int = 64,
                 local_files_only: Optional[bool] = None):
        """
        初始化批量Embeddings包装器

        Args:
            model_name: 模型名称或本地路径
            model_kwargs: 模型参数（会透传给SentenceTransformer）
            encode_kwargs: 编码参数
            batch_size: 批量处理大小
            local_files_only: 仅使用本地文件，不访问网络（透传给SentenceTransformer）
        """
        try:
            from langchain_community.embeddings import HuggingFaceEmbeddings

            kwargs = dict(model_kwargs or {})
            if local_files_only is not None:
                kwargs["local_files_only"] = local_files_only

            self._batch_size = batch_size
            self._model = HuggingFaceEmbeddings(
                model_name=model_name,
                model_kwargs=kwargs,
                encode_kwargs=encode_kwargs or {},
                show_progress=False
            )
            logger.info(f"[Embedding] 创建批量包装器: batch_size={batch_size}")
        except Exception as e:
            logger.error(f"[Embedding] 创建包装器失败: {e}")
            raise

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        批量embedding文档，自动分批处理

        Args:
            texts: 文本列表

        Returns:
            向量列表
        """
        if not texts:
            return []

        # 清理文本：确保都是字符串、非空且无孤立代理字符
        cleaned_texts = []
        for i, text in enumerate(texts):
            if text is None:
                cleaned_texts.append("")
                logger.warning(f"[Embedding] 文档[{i}]为None，替换为空字符串")
            elif not isinstance(text, str):
                cleaned_texts.append(str(text))
                logger.warning(f"[Embedding] 文档[{i}]非字符串类型，已转换")
            else:
                cleaned_texts.append(text)
        # 移除孤立代理字符（PyMuPDF数学符号提取可能产生），避免tokenizer报错
        for i, text in enumerate(cleaned_texts):
            if _LONE_SURROGATE_RE.search(text):
                cleaned_texts[i] = _LONE_SURROGATE_RE.sub('', text)

        # 分批处理
        all_embeddings = []
        total = len(cleaned_texts)

        for start in range(0, total, self._batch_size):
            end = min(start + self._batch_size, total)
            batch = cleaned_texts[start:end]

            try:
                batch_embeddings = self._model.embed_documents(batch)
                all_embeddings.extend(batch_embeddings)

                if start % (self._batch_size * 5) == 0:  # 每5批输出一次进度
                    logger.debug(f"[Embedding] 进度: {end}/{total}")

            except Exception as e:
                logger.error(f"[Embedding] 批量处理失败 [{start}:{end}]: {e}")
                # 尝试逐个处理
                for text in batch:
                    try:
                        single_embedding = self._model.embed_query(text)
                        all_embeddings.append(single_embedding)
                    except Exception as e2:
                        logger.error(f"[Embedding] 单文档处理失败: {e2}")
                        # 使用零向量作为fallback
                        all_embeddings.append([0.0] * 512)

        return all_embeddings

    def embed_query(self, text: str) -> List[float]:
        """
        Embedding单个查询

        Args:
            text: 查询文本

        Returns:
            向量
        """
        if text is None:
            text = ""
        elif not isinstance(text, str):
            text = str(text)
        # 移除孤立代理字符，避免tokenizer报错
        if _LONE_SURROGATE_RE.search(text):
            text = _LONE_SURROGATE_RE.sub('', text)

        return self._model.embed_query(text)


# ============ 加载策略辅助函数 ============

def get_load_mode() -> str:
    """获取Embedding加载模式（auto/offline/online），默认auto"""
    mode = os.getenv("EMBEDDING_LOAD_MODE", LOAD_MODE_AUTO).strip().lower()
    if mode not in VALID_LOAD_MODES:
        logger.warning(f"EMBEDDING_LOAD_MODE无效，使用默认auto")
        return LOAD_MODE_AUTO
    return mode


def _get_model_name() -> str:
    """获取当前配置的Embedding模型名"""
    return os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def _hf_hub_cache_root() -> Path:
    """获取Hugging Face本地缓存根目录（hub）"""
    cache = os.getenv("HF_HUB_CACHE") or os.path.join(
        os.getenv("HF_HOME", os.path.join(os.path.expanduser("~"), ".cache", "huggingface")),
        "hub")
    return Path(cache)


def _local_cache_snapshot(model_name: str) -> Optional[str]:
    """
    检查Hugging Face本地缓存中是否已有完整模型快照

    Returns:
        快照目录路径（含模型权重），无则返回None
    """
    if "/" not in model_name:
        return None
    org, name = model_name.split("/", 1)
    model_dir = _hf_hub_cache_root() / f"models--{org}--{name}" / "snapshots"
    if not model_dir.is_dir():
        return None
    weight_names = ("model.safetensors", "pytorch_model.bin", "model.bin")
    try:
        for snap in model_dir.iterdir():
            if snap.is_dir() and any((snap / w).exists() for w in weight_names):
                return str(snap)
    except OSError:
        pass
    return None


def _resolve_local_dir() -> Optional[str]:
    """
    解析EMBEDDING_LOCAL_DIR配置并校验完整性

    Returns:
        有效本地模型目录，无效则返回None（不打印完整路径到日志）
    """
    local_dir = os.getenv("EMBEDDING_LOCAL_DIR", "").strip()
    if not local_dir:
        return None
    p = Path(local_dir)
    if not p.is_absolute():
        p = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / p

    if not p.is_dir() or not (p / "config.json").exists():
        logger.warning("EMBEDDING_LOCAL_DIR无效或缺少config.json")
        return None
    has_weight = any(
        (p / f).exists()
        for f in ("model.safetensors", "pytorch_model.bin", "model.bin", "pytorch_model.safetensors")
    )
    if not has_weight:
        logger.warning("EMBEDDING_LOCAL_DIR缺少模型权重文件")
        return None
    return str(p)


def _determine_source(load_mode: str) -> str:
    """
    确定加载来源：
    explicit_local_dir > local_cache > online（模式允许时）> unavailable
    """
    if _resolve_local_dir():
        return "explicit_local_dir"
    if _local_cache_snapshot(_get_model_name()):
        return "local_cache"
    if load_mode in (LOAD_MODE_AUTO, LOAD_MODE_ONLINE):
        return "online"
    return "unavailable"


def _build_embeddings(model_name: str, local_files_only: bool) -> BatchedHuggingFaceEmbeddings:
    """构造Embedding实例"""
    model_kwargs = {"device": "cpu"}
    encode_kwargs = {"normalize_embeddings": True}
    return BatchedHuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs=model_kwargs,
        encode_kwargs=encode_kwargs,
        batch_size=64,
        local_files_only=local_files_only,
    )


def _classify_load_error(exc: Exception) -> str:
    """将加载异常归类为简短、安全的错误类型"""
    msg = str(exc).lower()
    if any(k in msg for k in ("cannot find", "no cached", "not found", "offline")):
        return "local_model_not_found"
    if any(k in msg for k in ("cannot send a request", "connection", "network", "connect")):
        return "network_error"
    if any(k in msg for k in ("timeout", "timed out")):
        return "timeout"
    return "embedding_load_failed"


def _sanitize_error_message(msg: str) -> str:
    """清理错误消息：去除本地路径等敏感信息"""
    if not msg:
        return ""
    msg = re.sub(r"[A-Za-z]:\\[^\s'\"]*", "[PATH]", msg)          # Windows路径
    msg = re.sub(r"/(?:[A-Za-z0-9_./-]*?\.cache|home|Users)/[^\s'\"]*", "[PATH]", msg)  # 常见缓存/家目录
    return msg[:200]


def _load_with_strategy(load_mode: str):
    """
    按策略加载Embedding模型

    Returns:
        (model, source)

    Raises:
        RuntimeError: 加载失败
    """
    model_name = _get_model_name()

    # 1. 显式本地目录优先
    local_dir = _resolve_local_dir()
    if local_dir:
        logger.info(f"Embedding loading: model={model_name} load_mode={load_mode} source=explicit_local_dir")
        return _build_embeddings(local_dir, local_files_only=True), "explicit_local_dir"

    # 2. 本地缓存优先（auto/offline/online 均优先复用缓存）
    snapshot = _local_cache_snapshot(model_name)
    if snapshot:
        logger.info(f"Embedding loading: model={model_name} load_mode={load_mode} source=local_cache")
        return _build_embeddings(snapshot, local_files_only=True), "local_cache"

    # 3. offline模式：本地不存在则立即失败
    if load_mode == LOAD_MODE_OFFLINE:
        raise RuntimeError("offline模式下本地模型不存在，无法加载Embedding")

    # 4. auto/online：在线加载
    logger.info(f"Embedding loading: model={model_name} load_mode={load_mode} source=online")
    return _build_embeddings(model_name, local_files_only=False), "online"


def get_embedding_model(force_reload: bool = False) -> Embeddings:
    """
    获取Embedding模型实例（线程安全单例）

    加载策略由 EMBEDDING_LOAD_MODE 控制：
    - auto（默认）：显式本地目录 > 本地缓存 > 在线
    - offline：仅本地目录/本地缓存，不访问网络
    - online：允许在线加载（仍优先本地缓存）

    Args:
        force_reload: 强制重新加载（失败后的明确人工重试入口）

    Returns:
        Embeddings模型实例

    Raises:
        ImportError: 缺少必要的依赖包
        RuntimeError: 模型加载失败（进入bm25_degraded降级）
    """
    global _embedding_model
    load_mode = get_load_mode()

    if not HUGGINGFACE_AVAILABLE:
        raise ImportError("缺少langchain-community包")

    # 已加载且非强制重载 → 直接返回
    if _embedding_model is not None and not force_reload:
        return _embedding_model

    with _load_lock:
        # 双重检查：等待期间可能已被其他线程加载
        if _embedding_model is not None and not force_reload:
            return _embedding_model

        # 失败冷却：failed 且未超过冷却 → 快速失败，不重复加载
        if not force_reload:
            with _state_lock:
                st = dict(_state)
            if st["status"] == "failed" and st.get("last_error_at"):
                elapsed = time.time() - st["last_error_at"]
                if elapsed < DEFAULT_RETRY_COOLDOWN:
                    raise RuntimeError(
                        f"Embedding模型暂不可用（{st.get('error_type', 'unknown')}），"
                        f"将在{int(DEFAULT_RETRY_COOLDOWN - elapsed)}s后自动重试")
            if st["status"] == "loading":
                raise RuntimeError("Embedding模型正在加载中，请稍后重试")

        # 标记 loading
        with _state_lock:
            _state["status"] = "loading"
            _state["load_mode"] = load_mode
            _state["error"] = None
            _state["error_type"] = None
            _state["duration"] = None

        start = time.time()
        try:
            model, source = _load_with_strategy(load_mode)
            dimension = len(model.embed_query("测试"))
            duration = time.time() - start
            model_name = _get_model_name()
            with _state_lock:
                _state.update({
                    "status": "ready",
                    "source": source,
                    "model": model_name,
                    "dimension": dimension,
                    "error": None,
                    "error_type": None,
                    "duration": duration,
                    "last_error_at": None,
                })
            _embedding_model = model
            logger.info(f"Embedding ready: model={model_name} source={source} duration={duration:.1f}s")
            return model
        except Exception as e:
            duration = time.time() - start
            error_type = _classify_load_error(e)
            safe_error = _sanitize_error_message(str(e))
            with _state_lock:
                _state.update({
                    "status": "failed",
                    "source": "unavailable",
                    "error": safe_error,
                    "error_type": error_type,
                    "duration": duration,
                    "last_error_at": time.time(),
                })
            _embedding_model = None
            logger.warning(
                f"Embedding unavailable; retrieval degraded to BM25 only: "
                f"error_type={error_type} duration={duration:.1f}s")
            raise RuntimeError(f"加载Embedding模型失败: {error_type}")


def try_get_embedding_model():
    """
    尝试获取Embedding模型，失败不抛异常

    Returns:
        (model, None) 或 (None, error_type)
    """
    try:
        return get_embedding_model(), None
    except Exception:
        with _state_lock:
            return None, _state.get("error_type") or "embedding_load_failed"


def get_embedding_status() -> Dict[str, Any]:
    """
    获取Embedding加载状态（线程安全，不触发模型加载）

    Returns:
        {
            "status": not_loaded|loading|ready|failed,
            "load_mode": auto|offline|online,
            "source": explicit_local_dir|local_cache|online|unavailable,
            "model": 模型名,
            "dimension": 向量维度,
            "error": 简短安全错误信息,
            "error_type": 错误类型,
            "duration": 最近一次加载耗时,
        }
    """
    with _state_lock:
        return {
            "status": _state["status"],
            "load_mode": _state["load_mode"],
            "source": _state["source"],
            "model": _state["model"],
            "dimension": _state["dimension"],
            "error": _state["error"],
            "error_type": _state["error_type"],
            "duration": _state["duration"],
        }


def reset_embedding_loader() -> Dict[str, Any]:
    """
    重置Embedding加载器（明确的人工重试入口）

    清除单例与失败状态，下一次调用将重新加载。
    由 /health/ready 或运维接口在修复环境后触发。
    """
    global _embedding_model
    with _load_lock, _state_lock:
        _embedding_model = None
        _state.update({
            "status": "not_loaded",
            "source": None,
            "error": None,
            "error_type": None,
            "dimension": None,
            "duration": None,
            "last_error_at": None,
        })
    logger.info("Embedding loader reset; next call will reload")
    return dict(_state)


def embed_documents(documents: List[Union['Document', str]]) -> List[List[float]]:
    """
    将文档列表转换为向量

    Args:
        documents: Document对象列表或字符串列表

    Returns:
        向量列表，每个向量是浮点数列表

    Raises:
        ValueError: 输入列表为空
        RuntimeError: 向量化失败
    """
    if not documents:
        raise ValueError("文档列表不能为空")

    try:
        model = get_embedding_model()

        texts = []
        for doc in documents:
            if hasattr(doc, 'page_content'):
                texts.append(doc.page_content)
            elif isinstance(doc, str):
                texts.append(doc)
            else:
                raise ValueError(f"不支持的文档类型: {type(doc)}")

        embeddings = model.embed_documents(texts)
        return embeddings

    except Exception as e:
        raise RuntimeError(f"文档向量化失败: {str(e)}")


def embed_query(query: str) -> List[float]:
    """
    将用户问题转换为向量

    Args:
        query: 用户问题字符串

    Returns:
        问题向量（浮点数列表）

    Raises:
        ValueError: 查询字符串为空
        RuntimeError: 向量化失败
    """
    if not query or not query.strip():
        raise ValueError("查询字符串不能为空")

    try:
        model = get_embedding_model()
        embedding = model.embed_query(query)
        return embedding
    except Exception as e:
        raise RuntimeError(f"查询向量化失败: {str(e)}")


def get_embedding_dimension() -> Optional[int]:
    """
    获取Embedding向量的维度（会触发模型加载）

    Returns:
        向量维度，如果模型未加载则返回None
    """
    try:
        model = get_embedding_model()
        test_embedding = model.embed_query("测试")
        return len(test_embedding)
    except Exception:
        return None


def check_embedding_status() -> dict:
    """
    检查Embedding模块的状态（只读，不触发模型加载）

    Returns:
        状态信息字典
    """
    emb_state = get_embedding_status()
    return {
        'langchain_available': LANGCHAIN_AVAILABLE,
        'huggingface_available': HUGGINGFACE_AVAILABLE,
        'model_loaded': _embedding_model is not None,
        'model_name': emb_state["model"],
        'dimension': emb_state["dimension"],
        'status': emb_state["status"],
        'load_mode': emb_state["load_mode"],
        'source': emb_state["source"],
        'error_type': emb_state["error_type"],
    }


# 便捷函数
def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    将文本列表转换为向量（便捷函数）

    Args:
        texts: 文本字符串列表

    Returns:
        向量列表
    """
    return embed_documents(texts)


__all__ = [
    "BatchedHuggingFaceEmbeddings",
    "get_embedding_model",
    "try_get_embedding_model",
    "get_embedding_status",
    "reset_embedding_loader",
    "get_load_mode",
    "embed_documents",
    "embed_query",
    "get_embedding_dimension",
    "check_embedding_status",
    "embed_texts",
    "LOAD_MODE_AUTO",
    "LOAD_MODE_OFFLINE",
    "LOAD_MODE_ONLINE",
]
