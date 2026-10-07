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
from typing import TYPE_CHECKING, List, Optional, Union, Dict, Any

if TYPE_CHECKING:  # pragma: no cover - 仅供静态检查；运行时按需导入 langchain
    from langchain_core.documents import Document

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
                logger.warning(
                    f"[Embedding] 批量处理失败 [{start}:{end}] ({type(e).__name__})，"
                    f"改用二分定位坏样本")
                all_embeddings.extend(self._embed_batch_resilient(batch))

        return all_embeddings

    def _embed_batch_resilient(self, batch: List[str]) -> List[List[float]]:
        """
        批量编码失败后的降级处理（v2.6）。

        此前实现是"整批失败 → 逐条 embed_query"，64 条批失败会产生 64 次前向
        （实测约 0.54s）。现在改为二分定位：只有真正无法编码的样本才会
        退到零向量，其余仍走批量。
        """
        import numpy as np

        try:
            return self._model.embed_documents(batch)
        except Exception:
            pass

        if len(batch) == 1:
            text = batch[0]
            try:
                return [self._model.embed_query(text)]
            except Exception as e2:
                logger.error(f"[Embedding] 单条编码失败，使用零向量: {type(e2).__name__}")
                return [self._zero_vector()]

        mid = len(batch) // 2
        return (self._embed_batch_resilient(batch[:mid])
                + self._embed_batch_resilient(batch[mid:]))

    def _zero_vector(self) -> List[float]:
        """零向量占位：使用模型真实维度，避免换模型后维度不一致导致静默错排"""
        dim = getattr(self, "_dimension", None)
        if not dim:
            try:
                dim = len(self._model.embed_query("测试"))
            except Exception:
                dim = 512
            self._dimension = dim
        return [0.0] * int(dim)

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


def _discover_bundled_model() -> Optional[str]:
    """在项目 models/ 下自动发现随项目分发的模型快照。

    背景：models/ 被 .gitignore 忽略，部署时要手动拷贝；而 snapshot 哈希是
    下载时随机生成的，让部署的人手工填 EMBEDDING_LOCAL_DIR 很容易填错。
    这里按"有 config.json + 有权重文件"判定，找到就用，省掉这一步手工配置。

    兼容两种常见布局：
        models/<name>/snapshots/<hash>/
        models/<org>/<name>/snapshots/<hash>/
    """
    model_name = _get_model_name()
    if "/" not in model_name:
        return None
    org, name = model_name.split("/", 1)
    root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "models"
    weight_names = (
        "model.safetensors", "pytorch_model.bin", "model.bin", "pytorch_model.safetensors",
    )
    for base in (root / name, root / model_name):
        snaps = base / "snapshots"
        if not snaps.is_dir():
            continue
        try:
            for snap in sorted(snaps.iterdir()):
                if not snap.is_dir() or not (snap / "config.json").exists():
                    continue
                if any((snap / w).exists() for w in weight_names):
                    logger.info(f"自动发现随项目分发的 embedding 模型：{base.name}")
                    return str(snap)
        except OSError:
            continue
    return None


def _resolve_local_dir() -> Optional[str]:
    """
    解析EMBEDDING_LOCAL_DIR配置并校验完整性

    Returns:
        有效本地模型目录，无效则返回None（不打印完整路径到日志）
    """
    local_dir = os.getenv("EMBEDDING_LOCAL_DIR", "").strip()
    if not local_dir:
        # 没显式配置时，先看项目里有没有自带模型——部署场景下模型目录
        # 通常是随项目一起拷过去的，不该强迫对方手填 snapshot 哈希。
        return _discover_bundled_model()
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


def _detect_device() -> str:
    """
    解析 embedding 运行设备（v2.6 新增）。

    此前 _build_embeddings 硬编码 device="cpu"，导致：
    - config 里的 EMBEDDING_DEVICE（含 validate 校验 cuda/mps）完全无效
    - 上了 GPU 也拿不到任何收益
    - 并发时 torch 默认线程数超订，实测 8 workers 吞吐反降到 7.7 req/s

    Returns:
        "cuda" | "mps" | "cpu"
    """
    want = os.getenv("EMBEDDING_DEVICE", "auto").strip().lower()
    if want in ("cpu", "cuda", "mps"):
        if want == "cuda":
            try:
                import torch
                if not torch.cuda.is_available():
                    logger.warning("EMBEDDING_DEVICE=cuda 但当前环境无可用 CUDA，回退 CPU")
                    return "cpu"
            except ImportError:
                logger.warning("EMBEDDING_DEVICE=cuda 但未安装 torch，回退 CPU")
                return "cpu"
        return want

    # auto：按可用性探测
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _configure_torch_threads() -> None:
    """
    限制 torch 线程数，避免并发下线程超订（v2.6）。

    实测：8 并发时吞吐从 10.2 req/s 降到 7.7 req/s、单请求延迟从 97ms 涨到 1025ms，
    主因是每个请求都让 torch 开满线程互相争抢。CPU 上限制到物理核数的一半更稳。
    """
    if os.getenv("TORCH_NUM_THREADS_CONFIGURED") == "1":
        return
    try:
        import torch
        env = os.getenv("TORCH_NUM_THREADS")
        if env:
            n = max(1, int(env))
        else:
            n = max(1, (os.cpu_count() or 4) // 2)
        torch.set_num_threads(n)
        os.environ.setdefault("OMP_NUM_THREADS", str(n))
        os.environ["TORCH_NUM_THREADS_CONFIGURED"] = "1"
        logger.info(f"[Embedding] torch 线程数限制为 {n}")
    except Exception:
        pass


def _build_embeddings(model_name: str, local_files_only: bool) -> BatchedHuggingFaceEmbeddings:
    """构造Embedding实例（v2.6：设备由配置/探测决定，不再硬编码 cpu）"""
    _configure_torch_threads()
    device = _detect_device()
    model_kwargs = {"device": device}
    encode_kwargs = {"normalize_embeddings": True}
    logger.info(f"[Embedding] 使用设备: {device}")
    return BatchedHuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs=model_kwargs,
        encode_kwargs=encode_kwargs,
        batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "64")),
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



_CLOUD_MODEL = None


def get_embedding_provider() -> str:
    """当前 Embedding 供应商：cloud（默认，阿里云百炼）| local（本地 BGE）。"""
    value = os.getenv("EMBEDDING_PROVIDER", "cloud").strip().lower()
    return value if value in ("cloud", "local") else "cloud"


def _get_cloud_model(force_reload: bool = False):
    """云端 Embedding 单例。**不会加载/下载任何本地模型。**"""
    global _CLOUD_MODEL
    if _CLOUD_MODEL is not None and not force_reload:
        return _CLOUD_MODEL
    from .embedding_providers import CloudEmbeddings
    model = CloudEmbeddings()
    _CLOUD_MODEL = model
    with _state_lock:
        _state.update({
            "status": "ready",
            "source": "cloud",
            "provider": "dashscope",
            "model": model.model_name,
            "dimension": model.dimensions,
            "error": None,
            "error_type": None,
        })
    logger.info(f"Embedding ready: provider=dashscope model={model.model_name} "
                f"dim={model.dimensions} base_url={model.base_url}")
    return model



def get_embedding_for_index(meta: Any = None):
    """按索引 meta 返回**匹配的**编码器，保证文档向量与查询向量来自同一模型。

    - meta.embedding_provider == "dashscope" → 云端编码器；
    - 其余（含旧索引无该字段）→ 本地编码器（迁移期间旧库继续可用）；
    - 无法判定时返回 None，调用方应降级到 BM25，**不得混用向量**。
    """
    meta = meta if isinstance(meta, dict) else {}
    provider = str(meta.get("embedding_provider") or "").strip().lower()
    model = str(meta.get("embedding_model") or "").strip()
    cloud_model = os.getenv("EMBEDDING_CLOUD_MODEL", "text-embedding-v4")
    if provider == "dashscope" or provider == "cloud" or model == cloud_model:
        return _get_cloud_model()
    if provider == "local" or provider == "":
        prev = os.environ.get("EMBEDDING_PROVIDER")
        os.environ["EMBEDDING_PROVIDER"] = "local"
        try:
            return get_embedding_model()
        finally:
            if prev is None:
                os.environ.pop("EMBEDDING_PROVIDER", None)
            else:
                os.environ["EMBEDDING_PROVIDER"] = prev
    if model:
        prev = os.environ.get("EMBEDDING_PROVIDER")
        os.environ["EMBEDDING_PROVIDER"] = "local"
        try:
            return get_embedding_model()
        finally:
            if prev is None:
                os.environ.pop("EMBEDDING_PROVIDER", None)
            else:
                os.environ["EMBEDDING_PROVIDER"] = prev
    return None


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

    # 云端优先（默认）：直接返回云端适配器，**不加载/下载任何本地模型**。
    # 本地 BGE 只在 EMBEDDING_PROVIDER=local（或按旧索引回退）时加载。
    if get_embedding_provider() == "cloud":
        return _get_cloud_model(force_reload)

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
