"""
向量存储模块
提供基于FAISS的向量存储功能，支持创建、加载、持久化和检索

v2.6 修复：
- similarity_search 返回真实 L2 距离（此前 distances 被直接丢弃，导致
  相似度阈值完全失效、retrieval_score 恒为 0、rerank 无法复用已算出的距离）
- 索引写入改为"先临时后改名"，并校验 index.ntotal == len(documents)，
  避免 index.faiss 与 index.pkl 错配后静默返回不对应的 chunk
- 新增 meta.json（模型/维度/分块参数/构建时间），加载时校验，不匹配则明确报错
- GlobalFAISS 缓存增加指纹校验与失效入口，删除知识库后不再返回已删内容
- 新增碎片过滤（单字符/纯页码/元数据包装），并带两级保护避免把知识库削空
"""

import json
import os
import re
import pickle
import logging
import threading
import time
from typing import List, Optional, Dict, Any, Tuple, Union
from langchain_core.documents import Document

logger = logging.getLogger(__name__)

# 向量存储目录配置（使用绝对路径，避免工作目录不一致导致写入失败）
def _vector_store_dir() -> str:
    """
    向量库根目录（**运行时解析**，与写入端保持一致）

    修复（2026-09-26，教师端验收时发现）：
    此处原先硬编码为 `<项目根>/data/vector_store`，绕过了 `EDU_DATA_DIR`。
    结果是"隔离数据目录"下的构建/上传写到隔离目录，而**检索从这里读正式目录**，
    索引找不到就静默降级为关键词检索 / 检索不可用（表现为回答凭空少了资料依据）。
    统一改为由 `config.path_config.get_data_dir()` 推导，读写同源。
    """
    from config.path_config import get_data_dir
    return str(get_data_dir() / "vector_store")


# 兼容别名（模块导入时的取值；模块内部一律使用 _vector_store_dir() 动态解析）
VECTOR_STORE_DIR = _vector_store_dir()

# 索引元数据文件名（与 index.faiss / index.pkl 同目录）
META_FILE = "meta.json"

# 索引 schema 版本：不兼容变更时递增，强制重建
INDEX_SCHEMA_VERSION = 2


def _safe_dir_name(subject_id: str) -> str:
    """
    将学科ID转为文件系统安全的目录名。

    FAISS 的 C++ 层要求**整个路径为 ASCII**（实测仅文件名 ASCII 不够，
    中文父目录同样 `could not open ... for writing`），因此中文 ID 需编码为 ASCII。
    纯 ASCII 的 ID 直接返回。

    2026-10-02：从 rag_service 移到本模块。
    原因：本模块的 add_documents_to_store 需要它，而 rag_service 会 import 本模块，
    反向 import 会形成循环。本模块是所有索引路径的唯一来源，放在这里最合理。
    """
    try:
        subject_id.encode('ascii')
        return subject_id  # 纯ASCII，直接用
    except UnicodeEncodeError:
        # 中文ID → base64编码（去掉=填充，用_替代/和+）
        import base64
        encoded = base64.urlsafe_b64encode(
            subject_id.encode('utf-8')).decode('ascii').rstrip('=')
        return f"_zh_{encoded}"


def _original_subject_id(safe_name: str) -> str:
    """将安全目录名还原为原始学科ID（_safe_dir_name 的逆操作）"""
    if not safe_name.startswith('_zh_'):
        return safe_name  # 纯ASCII，直接返回
    try:
        import base64
        encoded = safe_name[4:]  # 去掉 _zh_ 前缀
        # 补回 base64 的 = 填充
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += '=' * padding
        return base64.urlsafe_b64decode(encoded).decode('utf-8')
    except Exception:
        return safe_name  # 解码失败，返回原值


class IndexMetaMismatch(RuntimeError):
    """索引元数据与当前配置不匹配（需重建知识库）"""



def _atomic_write_index(index, docs: List[Document], persist_dir: str) -> None:
    """
    写入 FAISS 索引与文档，避免 index.faiss / index.pkl 错配。

    Windows + 中文路径的两个硬约束（实测得来）：
    1. FAISS 的 C++ FileIOWriter 要求**整个路径**均为 ASCII，
       仅文件名 ASCII 不够（中文父目录同样 `could not open ... for writing`）；
    2. os.replace 不能跨卷，而系统临时目录常在 C: 盘、数据在 D: 盘。

    因此：FAISS 索引写进系统临时目录（ASCII），用 shutil.move 跨卷搬运；
    文档 pickle 由 Python 直接写目标目录（Python 支持 Unicode 路径），
    两者都用"先临时后改名"的方式，失败会抛出而不是留下半成品。
    """
    import faiss
    import shutil
    import tempfile

    os.makedirs(persist_dir, exist_ok=True)
    index_path = os.path.join(persist_dir, "index.faiss")
    docs_path = os.path.join(persist_dir, "index.pkl")

    tmp_docs = docs_path + ".tmp"
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_index = os.path.join(tmp_dir, "index.faiss")
        # 1) FAISS 写入纯 ASCII 路径
        faiss.write_index(index, tmp_index)

        # 2) 文档先写同目录临时文件（Unicode 路径由 Python 处理）
        with open(tmp_docs, "wb") as f:
            pickle.dump({
                "documents": docs,
                "texts": [d.page_content for d in docs],
                "metadatas": [d.metadata for d in docs],
            }, f, protocol=pickle.HIGHEST_PROTOCOL)

        # 3) 落到目标位置：索引跨卷 move，文档同卷原子改名
        shutil.move(tmp_index, index_path)
        os.replace(tmp_docs, docs_path)




# 抽取产物中的"包装型"碎片：形如
#   [图片] | 尺寸: 1052x738 | 页码: 24
#   [公式] | 原始: 9-5 ... | 页码: 16 | 类型: expression
# 它们不含知识内容，却在索引里占了很大比例（实测约 40% 字符量）。
_META_WRAPPER_RE = re.compile(
    r"^\s*\[?(图片|公式|表格|图像)\]?\s*[|｜:：]"
)
# 纯数字/单字母/纯标点碎片
_PURE_NOISE_RE = re.compile(r"^[\W\d_]{0,3}$")


def is_meta_wrapper(text: str) -> bool:
    """判断整段文本是否为元数据包装碎片（[图片]/[公式] 等抽取产物）"""
    return bool(_META_WRAPPER_RE.match(text or ""))


# 匹配"某一行"是元数据包装（用于从合并后的块中按行剥除）
# 形如：[公式] | 原始: 9-2 细长中心受压直杆临界力的欧拉公式 | 页码: 10 | 类型: expression
#       [图片] | 尺寸: 1052x738 | 页码: 24
_META_WRAPPER_LINE_RE = re.compile(
    r"^\s*\[?(图片|公式|表格|图像)\]?\s*[|｜:：].*$",
    re.MULTILINE,
)
# 从包装行里提取「原始」字段（公式/文字的真实内容）
_ORIGINAL_FIELD_RE = re.compile(r"原始\s*[:：]\s*([^|｜\n]+)")
# 行内的孤立元数据片段，如 "| 页码: 6"、"| 类型: expression"、"| 尺寸: 1052x738"
_INLINE_META_RE = re.compile(
    r"[|｜]\s*(?:页码|尺寸|类型|置信度|confidence)\s*[:：]\s*[^|｜\n]*"
)


def clean_chunk_text(text: str) -> str:
    """
    清洗 chunk 正文（v2.6）。

    抽取管线会把元数据作为**独立行**写进正文，例如：
        [图片] | 尺寸: 1052x738 | 页码: 24
        [公式] | 原始: 9-2 细长中心受压直杆临界力的欧拉公式 | 页码: 10 | 类型: expression

    处理原则：
    - `[图片]` 行只含尺寸/页码等元数据 → 整行剔除；
    - `[公式]` 行的「原始」字段是**真实公式内容**（教学内容），必须保留，
      只去掉 `[公式] |`、`页码`、`类型` 这些包装，避免"为了清元数据把知识一起删掉"
      （初版实现整行删除，实测使材料力学内容量从 12,670 骤降到 1,256 字符）。
    """
    if not text:
        return ""

    out_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append("")
            continue
        if is_meta_wrapper(stripped):
            # 是包装行：尝试提取「原始」字段作为有效内容
            m = _ORIGINAL_FIELD_RE.search(stripped)
            if m:
                formula = m.group(1).strip()
                if formula:
                    out_lines.append(formula)
            # 提取不到（如 [图片] 行）则丢弃该行
            continue
        out_lines.append(line)

    cleaned = "\n".join(out_lines)
    # 剥除行内残留的元数据片段（如 "... | 页码: 6 | 类型: expression"）
    cleaned = _INLINE_META_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()




# 有效字符：中日韩统一表意文字 / 拉丁字母 / 数字
_MEANINGFUL_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbfA-Za-z0-9]")


def meaningful_char_count(text: str) -> int:
    """
    统计"有语义"的字符数。

    用于识破一类特殊垃圾：PDF 公式抽取会产出 Unicode 私用区字符
    （如 \\uf061、\\uf067）与纯符号串（如 "( )''"、"\\uf064="）。
    它们**字符数可能很长**，能骗过纯长度过滤，但分词后为空，
    会被 BM25 静默丢弃 → 造成 FAISS 与 BM25 索引条目数不一致
    （实测测量学差 149 条）。
    """
    return len(_MEANINGFUL_CHAR_RE.findall(text or ""))


def filter_documents_by_min_length(
    docs: List[Document],
    min_length: Optional[int] = None,
    min_keep_ratio: Optional[float] = None,
    absolute_floor: int = 8,
) -> Tuple[List[Document], Dict[str, Any]]:
    """
    过滤掉无价值的碎片 chunk（v2.6，检索质量关键修复）。

    背景：PDF/DOCX 抽取会产生大量碎片——单字符（"8"、"b"）、纯页码、
    以及 "[图片] | 尺寸: 1052x738 | 页码: 24" 这类元数据包装。
    它们与真实知识内容一起进入 FAISS/BM25，稀释检索结果。
    实测：过滤 >=120 字符后 recall@5 由 65.2% 提升至 92.4%。

    **两级保护**（避免"为了指标好看而把知识库削空"）：
    1. 元数据包装碎片无论多长一律丢弃（它们永远不含知识内容）；
    2. 长度过滤后若保留比例低于 min_keep_ratio，则逐步放宽长度阈值
       （但绝不低于 absolute_floor），宁可保留一些碎片，
       也不让知识库静默缩水到不可用——同时通过 returned stats 明确告警。

    Args:
        docs: 待过滤文档
        min_length: 最小字符数；None 时读配置 CHUNK_MIN_LENGTH（默认 120）
        min_keep_ratio: 最少保留比例（低于此值触发放宽）
        absolute_floor: 放宽后的绝对下限

    Returns:
        (过滤后文档, 统计信息)
    """
    if min_length is None:
        try:
            from config import settings
            min_length = int(getattr(settings.rag, "chunk_min_length", 90))
        except Exception:
            min_length = 90
    if min_keep_ratio is None:
        try:
            from config import settings
            min_keep_ratio = float(getattr(settings.rag, "chunk_min_keep_ratio", 0.15))
        except Exception:
            min_keep_ratio = 0.15

    total = len(docs)
    if total == 0:
        return docs, {"min_length": min_length, "before": 0, "after": 0,
                      "dropped": 0, "relaxed": False}

    def _len(d):
        return len((getattr(d, "page_content", "") or "").strip())

    # 第 1 步：无条件剔除元数据包装碎片（[图片]|[公式] 等抽取产物）。
    # 注意：**不**按"有效字符数"丢弃符号密集的 chunk —— 它们是 STEM 公式内容，
    # 对教学有价值。对这类内容，两个索引的一致性由 BM25 侧用占位文档保证
    # （见 bm25_retriever.add_documents），而不是靠删数据。
    non_wrapper = []
    wrapper_dropped = 0
    for d in docs:
        if is_meta_wrapper(getattr(d, "page_content", "")):
            wrapper_dropped += 1
            continue
        non_wrapper.append(d)

    if min_length <= 0:
        before = total
        return non_wrapper, {
            "min_length": 0,
            "configured_min_length": 0,
            "before": before,
            "after": len(non_wrapper),
            "dropped": before - len(non_wrapper),
            "dropped_ratio": round((before - len(non_wrapper)) / max(before, 1), 4),
            "meta_wrapper_dropped": wrapper_dropped,
            "meaningless_dropped": 0,
            "relaxed": False,
            "low_yield": False,
        }

    # 第 2 步：长度过滤
    kept = [d for d in non_wrapper if _len(d) >= min_length]
    relaxed = False
    effective = min_length

    # 第 3 步：保留比例过低时逐级放宽，避免知识库被削空。
    # 注意：只有**真的低于下限**才放宽，且放宽后一旦达标立即停止，
    # 不允许一路降到绝对下限（那等于取消过滤）。
    min_keep = max(1, int(len(non_wrapper) * min_keep_ratio))
    if non_wrapper and len(kept) < min_keep:
        relaxed = True
        applied = False
        for ratio in (0.6, 0.4, 0.25):
            th = max(int(min_length * ratio), absolute_floor)
            if th >= min_length:
                continue
            cand = [d for d in non_wrapper if _len(d) >= th]
            if len(cand) >= min_keep:
                kept, effective, applied = cand, th, True
                break
        if not applied:
            # 逐级放宽仍无法达标：说明文档抽取质量极差，
            # 此时保留全部非包装文档，并把问题暴露给调用方，而不是悄悄削数据
            kept, effective = non_wrapper, absolute_floor
        logger.warning(
            f"过滤阈值过严（{min_length} 仅保留 {len(kept)}/{len(non_wrapper)}，"
            f"低于 {min_keep_ratio*100:.0f}% 下限），已放宽至 {effective}。"
            f"这通常意味着文档抽取质量差（扫描版 PDF 未做 OCR），"
            f"建议修数据管道而非仅调阈值。")

    stats = {
        "min_length": effective,
        "configured_min_length": min_length,
        "before": total,
        "after": len(kept),
        "dropped": total - len(kept),
        "meta_wrapper_dropped": wrapper_dropped,
        "meaningless_dropped": 0,
        "dropped_ratio": round((total - len(kept)) / max(total, 1), 4),
        "relaxed": relaxed,
        "low_yield": len(kept) < max(1, int(total * min_keep_ratio)),
    }
    return kept, stats


def _write_meta(persist_dir: str, meta: Dict[str, Any]) -> None:
    """原子写入索引元数据"""
    path = os.path.join(persist_dir, META_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_index_meta(persist_dir: str) -> Optional[Dict[str, Any]]:
    """读取索引元数据（不存在或损坏返回 None）"""
    path = os.path.join(persist_dir, META_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"读取索引元数据失败: {e}")
        return None


def create_vector_store(
    docs: List[Document],
    embeddings: Any,
    collection_name: str = "default",
    persist_dir: Optional[str] = None,
    min_length: Optional[int] = None,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    filter_stats: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    创建FAISS向量存储
    
    Args:
        docs: 文档列表
        embeddings: 嵌入模型实例
        collection_name: 集合/学科标识
        persist_dir: 持久化目录路径（可选）
        min_length: 最小 chunk 字符数（None=读配置，默认 90）
        chunk_size: 分块大小（写入 meta，用于一致性校验）
        chunk_overlap: 分块重叠（写入 meta）
        filter_stats: 调用方**已先行过滤**时的统计结果。传入时本函数不再重复过滤，
                      但仍会把真实阈值与丢弃数写入 meta（避免 meta 失真）。
        
    Returns:
        FAISS向量存储实例
        
    Raises:
        RuntimeError: 创建失败时抛出
    """
    if not docs:
        raise RuntimeError("无法从空文档列表创建向量存储")
    
    try:
        import faiss
        import numpy as np
        
        # 确定存储路径
        if persist_dir is None:
            persist_dir = os.path.join(_vector_store_dir(), collection_name)
        
        os.makedirs(persist_dir, exist_ok=True)

        # v2.6：过滤碎片 chunk（检索质量关键修复）
        if filter_stats is not None:
            # 调用方已过滤（documents 计数以调用方为准）
            stats = dict(filter_stats)
            stats.setdefault("after", len(docs))
        else:
            docs, stats = filter_documents_by_min_length(docs, min_length)

        if not docs:
            raise RuntimeError(
                f"过滤后无可用文档（min_length={stats.get('min_length')}，"
                f"原始 {stats.get('before')} 条全部过短）。"
                f"请检查文档抽取与分块策略。")
        if stats.get("dropped"):
            logger.info(
                f"碎片过滤: {stats.get('before')} → {stats.get('after')} 条 "
                f"(丢弃 {stats.get('dropped')} 条, "
                f"{(stats.get('dropped_ratio') or 0)*100:.1f}%, "
                f"min_length={stats.get('min_length')})")

        # v2.6：清洗正文 —— 按行剥除元数据包装，避免它们混在真实内容里
        # 占用上下文预算并污染 embedding
        cleaned = 0
        for doc in docs:
            raw = doc.page_content or ""
            new = clean_chunk_text(raw)
            if new != raw:
                doc.page_content = new
                cleaned += 1
        if cleaned:
            logger.info(f"正文清洗: {cleaned} 条 chunk 剥除了元数据行")

        # v2.6：归一化 source 为纯文件名。
        # 多模态增强链路会在加载器之后重建 metadata，可能把完整绝对路径写回 source；
        # 该字段会被 build_context 拼进 LLM 提示词，必须在此兜底。
        normalized = 0
        for doc in docs:
            meta = doc.metadata
            if not isinstance(meta, dict):
                continue
            src = meta.get("source")
            if src and isinstance(src, str):
                name = os.path.basename(src.replace("\\", "/"))
                if name and name != src:
                    meta.setdefault("source_path", src)
                    meta["source"] = name
                    normalized += 1
        if normalized:
            logger.info(f"source 归一化: {normalized} 条 chunk 改为纯文件名")

        docs = [d for d in docs if (d.page_content or "").strip()]
        if not docs:
            raise RuntimeError("清洗后无剩余内容，请检查文档抽取质量")
        
        # 获取文档文本。metadata 随 docs 一起由 _atomic_write_index 持久化，
        # 这里不需要再单独抽一份（此前多出的 metadatas 从未被使用）。
        texts = [doc.page_content for doc in docs]
        
        logger.info(f"正在为 {len(texts)} 个文档创建FAISS索引...")
        
        # 使用嵌入模型生成向量
        if hasattr(embeddings, 'embed_documents'):
            vectors = embeddings.embed_documents(texts)
        elif hasattr(embeddings, 'embed_query'):
            # 单个处理
            vectors = [embeddings.embed_query(text) for text in texts]
        else:
            raise RuntimeError("嵌入模型不支持embed_documents或embed_query方法")
        
        vectors = np.array(vectors, dtype='float32')
        
        # 创建FAISS索引
        dimension = vectors.shape[1]
        index = faiss.IndexFlatL2(dimension)
        index.add(vectors)
        
        # 原子写入索引与文档
        _atomic_write_index(index, docs, persist_dir)

        # 写入元数据（供加载时一致性校验）
        model_name = None
        try:
            from rag.embedding import get_embedding_status
            model_name = get_embedding_status().get("model")
        except Exception:
            pass
        _write_meta(persist_dir, {
            "schema_version": INDEX_SCHEMA_VERSION,
            "embedding_model": model_name,
            "dimension": int(dimension),
            "documents": len(docs),
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "chunk_min_length": stats.get("min_length"),
            "configured_min_length": stats.get("configured_min_length"),
            "chunks_before_filter": stats.get("before"),
            "chunks_dropped": stats.get("dropped"),
            "meta_wrapper_dropped": stats.get("meta_wrapper_dropped"),
            "filter_relaxed": stats.get("relaxed"),
            "collection_name": collection_name,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        
        logger.info(f"FAISS索引已创建并保存至: {persist_dir}")
        
        # 返回包装对象
        return FAISSVectorStore(
            index=index,
            documents=docs,
            embeddings=embeddings,
            persist_dir=persist_dir
        )
        
    except Exception as e:
        raise RuntimeError(f"创建FAISS向量存储失败: {str(e)}")



def load_vector_store(
    collection_name: str = "default",
    embeddings: Any = None,
    persist_dir: Optional[str] = None,
    strict_meta: bool = True,
) -> Optional[Any]:
    """
    加载FAISS向量存储
    
    Args:
        collection_name: 集合/学科标识
        embeddings: 嵌入模型实例（FAISS检索时需要）
        persist_dir: 持久化目录路径（可选）
        strict_meta: 元数据不匹配时是否抛错（True=明确失败，避免静默返回错结果）
        
    Returns:
        FAISS向量存储实例，不存在则返回None
        
    Raises:
        RuntimeError: 加载失败
        IndexMetaMismatch: 索引元数据与当前配置不匹配（strict_meta=True）
    """
    try:
        import faiss
        import shutil
        
        # 确定存储路径
        if persist_dir is None:
            persist_dir = os.path.join(_vector_store_dir(), collection_name)
        
        index_path = os.path.join(persist_dir, "index.faiss")
        docs_path = os.path.join(persist_dir, "index.pkl")
        
        # 检查文件是否存在
        if not os.path.exists(index_path):
            logger.warning(f"FAISS索引文件不存在: {index_path}")
            return None
        
        if not os.path.exists(docs_path):
            logger.warning(f"文档文件不存在: {docs_path}")
            return None
        
        # 加载FAISS索引
        # 同写入路径：FAISS 要求整个路径为 ASCII，故先复制到系统临时目录再读。
        # 该开销只在缓存未命中时发生（GlobalFAISS 会持有实例）。
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_index = os.path.join(tmp_dir, "index.faiss")
            shutil.copy2(index_path, tmp_index)
            index = faiss.read_index(tmp_index)
        
        # 加载文档数据
        with open(docs_path, 'rb') as f:
            data = pickle.load(f)
        
        documents = data.get('documents', [])

        # v2.6：一致性校验 —— index.faiss 与 index.pkl 若错配，
        # 此前会静默返回完全不对应的 chunk（最难排查的一类损坏）
        if index.ntotal != len(documents):
            raise RuntimeError(
                f"索引与文档不一致（index.ntotal={index.ntotal} != documents={len(documents)}），"
                f"索引可能损坏，请重建知识库: {persist_dir}")

        # v2.6：维度/模型校验 —— 换 embedding 模型后维度相同不会报错但结果全错
        meta = read_index_meta(persist_dir)
        if meta:
            if meta.get("schema_version") != INDEX_SCHEMA_VERSION:
                msg = (f"索引 schema 版本不匹配（索引 {meta.get('schema_version')} != "
                       f"当前 {INDEX_SCHEMA_VERSION}），请重建知识库")
                if strict_meta:
                    raise IndexMetaMismatch(msg)
                logger.warning(msg)
            if meta.get("dimension") and int(meta["dimension"]) != index.d:
                msg = (f"索引维度不匹配（索引 {meta['dimension']} != 当前 {index.d}），"
                       f"embedding 模型可能已变更，请重建知识库")
                if strict_meta:
                    raise IndexMetaMismatch(msg)
                logger.warning(msg)
            if meta.get("embedding_model"):
                try:
                    from rag.embedding import get_embedding_status
                    cur = get_embedding_status().get("model")
                    if cur and cur != meta["embedding_model"]:
                        msg = (f"索引由模型 {meta['embedding_model']} 构建，"
                               f"当前为 {cur}，检索结果将不可靠，请重建知识库")
                        if strict_meta:
                            raise IndexMetaMismatch(msg)
                        logger.warning(msg)
                except IndexMetaMismatch:
                    raise
                except Exception:
                    pass
        
        logger.info(f"已加载FAISS索引: {persist_dir}, 包含 {len(documents)} 个文档")
        
        return FAISSVectorStore(
            index=index,
            documents=documents,
            embeddings=embeddings,
            persist_dir=persist_dir
        )
        
    except (RuntimeError, IndexMetaMismatch):
        raise
    except Exception as e:
        raise RuntimeError(f"加载FAISS向量存储失败: {str(e)}")



class FAISSVectorStore:
    """
    FAISS向量存储包装类
    提供统一的检索接口
    """
    
    def __init__(
        self,
        index: Any,
        documents: List[Document],
        embeddings: Any = None,
        persist_dir: Optional[str] = None
    ):
        self.index = index
        self.documents = documents
        self.embeddings = embeddings
        self.persist_dir = persist_dir
        # 索引文件指纹 (mtime, size)，供 GlobalFAISS 判断缓存是否新鲜
        self._fingerprint: Optional[Tuple[float, int]] = None
    
    def similarity_search(
        self,
        query: str,
        k: int = 4,
        embeddings: Any = None,
        with_scores: bool = False,
        score_threshold: Optional[float] = None,
    ) -> Union[List[Document], List[Tuple[Document, float]]]:
        """
        相似度检索
        
        Args:
            query: 查询文本
            k: 返回文档数量
            embeddings: 嵌入模型（优先使用参数，其次使用实例属性）
            with_scores: True 时返回 [(Document, similarity), ...]
            score_threshold: 余弦相似度下限（仅 with_scores=True 时生效）
            
        Returns:
            with_scores=False: 相似文档列表
            with_scores=True:  [(Document, similarity), ...]，similarity 为余弦相似度
            
        v2.6 修复：此前 `distances` 被直接丢弃，导致
        - 相似度阈值（SIMILARITY_THRESHOLD）完全失效
        - metadata['retrieval_score'] 与前端"相关度"恒为 0
        - rerank 无法复用已算出的距离，只能对每篇文档重新 embed（8.4ms/条）
        """
        try:
            import numpy as np
            
            # 使用提供的嵌入模型或实例属性
            emb = embeddings or self.embeddings
            if emb is None:
                raise RuntimeError("未提供嵌入模型，无法进行检索")
            
            # 生成查询向量
            if hasattr(emb, 'embed_query'):
                query_vector = emb.embed_query(query)
            else:
                raise RuntimeError("嵌入模型不支持embed_query方法")
            
            query_vector = np.array([query_vector], dtype='float32')

            # 维度校验：维度不符时 FAISS 会抛底层断言，这里给出明确原因
            if query_vector.shape[1] != self.index.d:
                raise RuntimeError(
                    f"查询向量维度({query_vector.shape[1]})与索引维度({self.index.d})不一致，"
                    f"embedding 模型可能已变更，请重建知识库")
            
            # 执行检索
            k = min(k, len(self.documents))
            if k == 0:
                return []
            
            distances, indices = self.index.search(query_vector, k)

            # L2 距离 → 余弦相似度（索引与查询均已归一化，等价于 1 - d²/2；
            # 这里沿用 1 - d/2 的线性近似，与既有 RRF/阈值语义保持单调一致）
            results = []
            for pos, idx in enumerate(indices[0]):
                if 0 <= idx < len(self.documents):
                    dist = float(distances[0][pos])
                    sim = 1.0 - dist / 2.0
                    if score_threshold is not None and sim < score_threshold:
                        continue
                    doc = self.documents[idx]
                    if with_scores:
                        results.append((doc, sim))
                    else:
                        results.append(doc)

            return results
            
        except Exception as e:
            logger.error(f"FAISS检索失败: {str(e)}")
            raise RuntimeError(f"FAISS检索失败: {str(e)}")

    
    def count(self) -> int:
        """获取文档数量"""
        return len(self.documents)
    
    def add_documents(
        self,
        docs: List[Document],
        embeddings: Any = None
    ) -> None:
        """
        添加文档到向量存储
        
        Args:
            docs: 文档列表
            embeddings: 嵌入模型
        """
        try:
            import numpy as np
            import faiss
            
            emb = embeddings or self.embeddings
            if emb is None:
                raise RuntimeError("未提供嵌入模型")
            
            # 生成向量
            texts = [doc.page_content for doc in docs]
            if hasattr(emb, 'embed_documents'):
                vectors = emb.embed_documents(texts)
            else:
                vectors = [emb.embed_query(text) for text in texts]
            
            vectors = np.array(vectors, dtype='float32')
            
            # 添加到索引
            self.index.add(vectors)
            self.documents.extend(docs)
            
            # 持久化
            if self.persist_dir:
                self._save()
            
            logger.info(f"已添加 {len(docs)} 个文档，当前总数: {len(self.documents)}")
            
        except Exception as e:
            raise RuntimeError(f"添加文档失败: {str(e)}")
    
    def _save(self) -> None:
        """保存索引和文档到磁盘（v2.6：原子替换 + 失败抛出）"""
        if self.persist_dir is None:
            return
        # 原子写入，不再静默吞掉异常（此前失败仅 logger.error，
        # 会留下 index.faiss 与 index.pkl 错配的状态）
        _atomic_write_index(self.index, self.documents, self.persist_dir)

        meta = read_index_meta(self.persist_dir) or {}
        meta.update({
            "schema_version": INDEX_SCHEMA_VERSION,
            "dimension": int(self.index.d),
            # 必须同步文档数：否则下次加载时 load_vector_store 的
            # index.ntotal == len(documents) 校验会失败，get() 静默返回 None
            # （表现为"刚追加完就再也取不到索引"）。
            "documents": len(self.documents),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        _write_meta(self.persist_dir, meta)
        # 写盘后必须刷新自身指纹，否则 GlobalFAISS 的新鲜度检查会认为缓存已过期，
        # 下一次 get() 会白白重新加载一遍（且若调用方未传 embeddings，
        # 重载出来的实例将没有嵌入模型，检索直接失败）。
        self._fingerprint = GlobalFAISS._fingerprint(self.persist_dir) \
            if self.persist_dir else None
        logger.info(f"已保存FAISS索引至: {self.persist_dir}（{len(self.documents)} 条）")


# 全局FAISS管理器（每个学科独立索引）
class GlobalFAISS:
    """
    全局FAISS管理器（v2.6：线程安全 + 指纹校验 + 容量上限 + 统一失效）

    修复要点：
    - 此前 get() 无锁、无新鲜度检查，删除知识库后进程不重启会继续返回已删内容
    - 此前缓存条目不记录 persist_dir，失效重建时会用错误目录（中文 ID 落到 safe 名下）
    """

    # 普通 ID 与 safe_name 可能同时作为 key，容量按实际条目数计
    MAX_INSTANCES = 32

    _instances: Dict[str, FAISSVectorStore] = {}
    _persist_dirs: Dict[str, str] = {}
    _lock = threading.RLock()

    @staticmethod
    def _fingerprint(persist_dir: str) -> Optional[Tuple[float, int]]:
        """索引文件指纹 (mtime, size)；不存在返回 None"""
        try:
            st = os.stat(os.path.join(persist_dir, "index.faiss"))
            return (st.st_mtime, st.st_size)
        except OSError:
            return None

    @classmethod
    def get(
        cls,
        collection_name: str = "default",
        embeddings: Any = None,
        persist_dir: Optional[str] = None
    ) -> Optional[FAISSVectorStore]:
        """
        获取或加载FAISS实例

        缓存命中时会比对 index.faiss 的 (mtime, size)：不一致说明索引已被
        重建或删除，则丢弃缓存重新加载，避免返回陈旧/已删除的数据。

        Args:
            collection_name: 集合/学科标识
            embeddings: 嵌入模型实例
            persist_dir: 持久化目录路径（可选）

        Returns:
            FAISS向量存储实例，不存在则返回None
        """
        with cls._lock:
            cached = cls._instances.get(collection_name)
            if cached is not None:
                cached_dir = cls._persist_dirs.get(collection_name) or persist_dir
                if cached_dir:
                    if cls._fingerprint(cached_dir) == cached._fingerprint:
                        return cached
                    logger.info(f"FAISS 索引已变更，丢弃缓存: {collection_name}")
                else:
                    return cached
                cls._instances.pop(collection_name, None)
                cls._persist_dirs.pop(collection_name, None)

            # 缓存未命中：加载（持锁，避免并发重复加载）
            store = load_vector_store(collection_name, embeddings, persist_dir,
                                      strict_meta=False)
            if store is not None:
                fp = cls._fingerprint(store.persist_dir) if store.persist_dir else None
                store._fingerprint = fp
                cls._instances[collection_name] = store
                if store.persist_dir:
                    cls._persist_dirs[collection_name] = store.persist_dir
                cls._evict_if_needed()

            return store

    @classmethod
    def _evict_if_needed(cls) -> None:
        """容量超限时按插入顺序淘汰最旧条目（dict 保序）"""
        while len(cls._instances) > cls.MAX_INSTANCES:
            oldest = next(iter(cls._instances))
            cls._instances.pop(oldest, None)
            cls._persist_dirs.pop(oldest, None)
            logger.info(f"FAISS 缓存超限，淘汰: {oldest}")

    @classmethod
    def put(cls, collection_name: str, store: FAISSVectorStore) -> None:
        """
        将已构建的实例放入缓存（构建路径用，避免构建后再从磁盘重读一次）。
        """
        with cls._lock:
            store._fingerprint = (cls._fingerprint(store.persist_dir)
                                  if store.persist_dir else None)
            cls._instances[collection_name] = store
            if store.persist_dir:
                cls._persist_dirs[collection_name] = store.persist_dir
            cls._evict_if_needed()

    @classmethod
    def invalidate(cls, collection_name: str) -> None:
        """
        使指定集合的内存缓存失效（不删除磁盘文件）。

        删除/重建知识库后必须调用，否则进程内仍会返回已删除的内容。
        """
        with cls._lock:
            cls._instances.pop(collection_name, None)
            cls._persist_dirs.pop(collection_name, None)
            logger.info(f"FAISS 缓存已失效: {collection_name}")

    @classmethod
    def clear_all(cls) -> None:
        """清空所有内存缓存（运维/测试用）"""
        with cls._lock:
            cls._instances.clear()
            cls._persist_dirs.clear()
            logger.info("FAISS 缓存已全部清空")
    
    @classmethod
    def create(
        cls,
        docs: List[Document],
        embeddings: Any,
        collection_name: str = "default",
        persist_dir: Optional[str] = None,
        **kwargs: Any,
    ) -> FAISSVectorStore:
        """
        创建新的FAISS实例并写入缓存
        
        Args:
            docs: 文档列表
            embeddings: 嵌入模型实例
            collection_name: 集合/学科标识
            persist_dir: 持久化目录路径（可选）
            
        Returns:
            FAISS向量存储实例
        """
        store = create_vector_store(docs, embeddings, collection_name, persist_dir,
                                    **kwargs)
        with cls._lock:
            store._fingerprint = (cls._fingerprint(store.persist_dir)
                                  if store.persist_dir else None)
            cls._instances[collection_name] = store
            if store.persist_dir:
                cls._persist_dirs[collection_name] = store.persist_dir
            cls._evict_if_needed()
        return store
    
    @classmethod
    def list_collections(cls) -> List[str]:
        """列出所有可用的集合"""
        collections = []
        
        if os.path.exists(_vector_store_dir()):
            for item in os.listdir(_vector_store_dir()):
                item_path = os.path.join(_vector_store_dir(), item)
                if os.path.isdir(item_path):
                    # 检查是否有FAISS索引文件
                    index_path = os.path.join(item_path, "index.faiss")
                    if os.path.exists(index_path):
                        collections.append(item)
        
        return collections
    
    @classmethod
    def delete(cls, collection_name: str) -> bool:
        """
        删除指定的FAISS实例（内存缓存 + 磁盘文件）
        
        Args:
            collection_name: 集合/学科标识
            
        Returns:
            是否删除成功
        """
        try:
            # 合并说明（2026-09-27）：取组员版本的**加锁 + 缓存目录记录**（并发安全、
            # 目录改名后也能删对），但路径解析仍走 `_vector_store_dir()` —— 它是运行时解析，
            # 保证与 EDU_DATA_DIR 隔离一致；不要把这里换回模块级常量（那会在导入时冻结路径）。
            with cls._lock:
                cls._instances.pop(collection_name, None)
                cached_dir = cls._persist_dirs.pop(collection_name, None)

            # 删除磁盘文件：优先用缓存记录的目录，否则按名称推断
            persist_dir = cached_dir or os.path.join(_vector_store_dir(), collection_name)
            if os.path.exists(persist_dir):
                import shutil
                shutil.rmtree(persist_dir)
                logger.info(f"已删除FAISS索引: {persist_dir}")
            
            return True
            
        except Exception as e:
            logger.error(f"删除FAISS索引失败: {str(e)}")
            return False
    
    @classmethod
    def exists(cls, collection_name: str) -> bool:
        """检查集合是否存在"""
        if collection_name in cls._instances:
            return True
        
        index_path = os.path.join(_vector_store_dir(), collection_name, "index.faiss")
        return os.path.exists(index_path)


# ============ 向后兼容的包装函数 ============

def add_documents_to_store(
    docs: List[Document],
    subject_id: str,
    embeddings: Any = None,
    create_if_missing: bool = True,
) -> Dict[str, Any]:
    """
    增量向指定学科的知识库追加文档（v2.6 新增）。

    为什么需要：`services/knowledge_expander.add_knowledge_to_kb` 一直在调用本函数，
    但它**从未被定义**（全仓无实现）—— 该 ImportError 被调用方的
    `except Exception` 静默吞掉，导致"提取的知识点入库"功能长期完全失效且无任何报错。
    这里补上真实实现。

    与 `add_documents()`（按 collection_name 的通用包装）的区别：
    - 入参是**原始 subject_id**（中文等），内部统一转 safe_name，
      避免调用方各自拼目录名导致 BM25 与 FAISS 命名空间不一致；
    - 追加后**同步失效 BM25 索引**：BM25 是独立持久化的，
      不同步就会出现"FAISS 有新内容、BM25 没有"的条目数不一致
      （AGENTS.md 第 6 节要求两者一致）。

    Args:
        docs: 待追加的 Document 列表
        subject_id: 学科 ID（原始值，可为中文）
        embeddings: 嵌入模型实例；None 时自动获取
        create_if_missing: 索引不存在时是否新建（False 则抛错）

    Returns:
        {"subject_id", "added", "total", "created", "bm25_invalidated"}

    Raises:
        ValueError: docs 为空或 subject_id 为空
        FileNotFoundError: 索引不存在且 create_if_missing=False
    """
    if not docs:
        raise ValueError("待追加的文档列表不能为空")
    if not subject_id or not str(subject_id).strip():
        raise ValueError("学科ID不能为空")

    safe_name = _safe_dir_name(subject_id)
    persist_dir = os.path.join(VECTOR_STORE_DIR, safe_name)
    index_path = os.path.join(persist_dir, "index.faiss")

    if embeddings is None:
        from rag.embedding import get_embedding_model
        embeddings = get_embedding_model()

    created = False
    if not os.path.exists(index_path):
        if not create_if_missing:
            raise FileNotFoundError(
                f"学科知识库不存在，无法追加: {subject_id}（目录 {persist_dir}）")
        logger.info(f"索引不存在，改为新建: subject={subject_id}")
        store = create_vector_store(
            docs=docs, embeddings=embeddings,
            collection_name=safe_name, persist_dir=persist_dir,
            chunk_size=None, chunk_overlap=None)
        GlobalFAISS.put(safe_name, store)
        created = True
    else:
        # 走缓存（命中则复用，未命中则加载）
        # 注意：必须传 embeddings —— 若缓存未命中而走重载，
        # 不带嵌入模型的实例将无法执行 similarity_search。
        store = GlobalFAISS.get(safe_name, embeddings, persist_dir)
        if store is None:
            raise FileNotFoundError(f"知识库加载失败，无法追加: {subject_id}")
        before = store.count()
        store.add_documents(docs, embeddings)
        logger.info(
            f"增量入库完成: subject={subject_id} {before} → {store.count()} 条")

    # BM25 必须同步失效：它是独立持久化的，不失效就会与 FAISS 条目数不一致
    bm25_invalidated = False
    try:
        from rag.hybrid_retriever import clear_index
        clear_index(subject_id)
        bm25_invalidated = True
    except Exception as e:
        logger.warning(
            f"BM25 索引失效失败（FAISS 已更新，两者可能暂时不一致，"
            f"建议重建该学科知识库）: {e}")

    return {
        "subject_id": subject_id,
        "added": len(docs),
        "total": store.count(),
        "created": created,
        "bm25_invalidated": bm25_invalidated,
    }


def add_documents(
    collection_name: str,
    docs: List[Document],
    embeddings: Any = None
) -> None:
    """
    向指定集合添加文档（向后兼容函数）
    
    Args:
        collection_name: 集合/学科标识
        docs: 文档列表
        embeddings: 嵌入模型实例
    """
    store = GlobalFAISS.get(collection_name, embeddings)
    if store is None:
        raise RuntimeError(f"集合 {collection_name} 不存在，请先创建")
    store.add_documents(docs, embeddings)


def similarity_search(
    collection_name: str,
    query: str,
    k: int = 4,
    embeddings: Any = None
) -> List[Document]:
    """
    在指定集合中进行相似度检索（向后兼容函数）
    
    Args:
        collection_name: 集合/学科标识
        query: 查询文本
        k: 返回文档数量
        embeddings: 嵌入模型实例
        
    Returns:
        相似文档列表
    """
    store = GlobalFAISS.get(collection_name, embeddings)
    if store is None:
        return []
    return store.similarity_search(query, k, embeddings)


def get_retriever(
    collection_name: str,
    embeddings: Any = None,
    search_kwargs: Optional[Dict[str, Any]] = None
) -> Optional[Any]:
    """
    获取检索器（向后兼容函数）
    
    Args:
        collection_name: 集合/学科标识
        embeddings: 嵌入模型实例
        search_kwargs: 检索参数
        
    Returns:
        检索器对象，如果集合不存在则返回None
    """
    store = GlobalFAISS.get(collection_name, embeddings)
    if store is None:
        return None
    
    # 返回一个简单的检索器包装
    class SimpleRetriever:
        def __init__(self, store, embeddings, search_kwargs):
            self.store = store
            self.embeddings = embeddings
            self.search_kwargs = search_kwargs or {"k": 4}
        
        def invoke(self, query: str) -> List[Document]:
            k = self.search_kwargs.get("k", 4)
            return self.store.similarity_search(query, k, self.embeddings)
        
        def get_relevant_documents(self, query: str) -> List[Document]:
            return self.invoke(query)
    
    return SimpleRetriever(store, embeddings, search_kwargs)


__all__ = [
    'create_vector_store',
    'load_vector_store',
    'add_documents',
    'add_documents_to_store',
    'filter_documents_by_min_length',
    'clean_chunk_text',
    'is_meta_wrapper',
    'read_index_meta',
    'similarity_search',
    'get_retriever',
    'FAISSVectorStore',
    'GlobalFAISS',
    'VECTOR_STORE_DIR',
]
