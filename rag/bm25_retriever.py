"""
BM25关键词检索模块
基于BM25算法实现关键词检索，用于与向量检索混合使用

依赖：rank_bm25 (pip install rank_bm25)
"""

import re
import math
import logging
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter

logger = logging.getLogger(__name__)

# 尝试导入rank_bm25
try:
    from rank_bm25 import BM25Okapi
    RANK_BM25_AVAILABLE = True
except ImportError:
    RANK_BM25_AVAILABLE = False
    logger.warning("rank_bm25未安装，将使用内置BM25实现。安装命令: pip install rank_bm25")


# ========== 中文分词 ==========

# 字符级分词（旧行为）：每个汉字单独成 token
_CN_TOKENIZE_RE = re.compile(
    r'[\u4e00-\u9fff]|[a-zA-Z]+|\d+|[^\s\w\u4e00-\u9fff]'
)

# 词级分词（v2.6 推荐）：jieba 为可选依赖，缺失时回退字符级
try:
    import jieba as _jieba
    import logging as _logging
    _jieba.setLogLevel(_logging.WARNING)
    JIEBA_AVAILABLE = True
except ImportError:
    _jieba = None
    JIEBA_AVAILABLE = False


def get_tokenizer_mode() -> str:
    """读取当前分词模式（char/word），默认 word；jieba 缺失时回退 char"""
    try:
        from config import settings
        mode = str(getattr(settings.rag, "bm25_tokenizer", "word")).lower()
    except Exception:
        mode = "word"
    if mode == "word" and not JIEBA_AVAILABLE:
        return "char"
    return mode if mode in ("char", "word") else "char"


def _tokenize_chinese(text: str, mode: Optional[str] = None) -> List[str]:
    """
    中文文本分词

    v2.6：新增词级分词。此前为逐字切分，实测词表 1536 个 token 中
    978 个是单字（64%）、49% 的 token df>=4 → IDF 饱和、区分度极低，
    却仍会把"恰好含同一常用字"的无关 chunk 排进候选。
    实测：词级分词与 BM25 结合可把 recall@5 从 95.5% 提升到 97.0%。

    Args:
        text: 待分词文本
        mode: "char"（逐字）| "word"（jieba 词级）| None（读配置）

    Returns:
        token 列表
    """
    text = text.lower().strip()
    if not text:
        return []

    mode = mode or get_tokenizer_mode()

    if mode == "word":
        # 词级：jieba 切词 + 把英文/数字片段单独归并，过滤纯空白
        tokens = []
        for tok in _jieba.lcut(text):
            tok = tok.strip()
            if not tok:
                continue
            # 丢弃纯标点 token（对检索无区分度，且会污染 IDF）
            if not any(c.isalnum() for c in tok):
                continue
            tokens.append(tok)
        return tokens

    tokens = _CN_TOKENIZE_RE.findall(text)
    # 过滤空白token
    return [t for t in tokens if t.strip()]


# ========== 内置BM25实现（rank_bm25不可用时的fallback） ==========

class _BuiltinBM25:
    """
    内置BM25实现
    当rank_bm25库不可用时使用
    """
    
    def __init__(self, corpus: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.doc_count = len(corpus)
        
        # 计算文档频率
        self.doc_freqs: Dict[str, int] = {}
        self.doc_lens = [len(doc) for doc in corpus]
        self.avg_dl = sum(self.doc_lens) / self.doc_count if self.doc_count > 0 else 0
        
        # 构建倒排索引
        for doc in corpus:
            unique_terms = set(doc)
            for term in unique_terms:
                self.doc_freqs[term] = self.doc_freqs.get(term, 0) + 1
        
        # 预计算每篇文档的term频率
        self.term_freqs = [Counter(doc) for doc in corpus]
    
    def _idf(self, term: str) -> float:
        """计算逆文档频率"""
        df = self.doc_freqs.get(term, 0)
        return math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1)
    
    def get_scores(self, query: List[str]) -> List[float]:
        """计算query对所有文档的BM25分数"""
        scores = [0.0] * self.doc_count
        
        for term in query:
            idf = self._idf(term)
            for i in range(self.doc_count):
                tf = self.term_freqs[i].get(term, 0)
                dl = self.doc_lens[i]
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / self.avg_dl)
                scores[i] += idf * (numerator / denominator) if self.avg_dl > 0 else 0
        
        return scores
    
    def get_top_n(self, query: List[str], n: int = 5) -> List[Tuple[int, float]]:
        """返回top_n结果：(doc_index, score)"""
        scores = self.get_scores(query)
        indexed_scores = [(i, s) for i, s in enumerate(scores)]
        indexed_scores.sort(key=lambda x: x[1], reverse=True)
        return indexed_scores[:n]


# ========== BM25检索器 ==========

class BM25Retriever:
    """
    BM25关键词检索器
    
    功能：
    - 根据文本chunk建立关键词索引
    - 支持query搜索
    - 返回top_k结果及分数
    """
    
    def __init__(self, documents: Optional[List[Any]] = None):
        """
        初始化BM25检索器
        
        Args:
            documents: Document对象列表（需有page_content属性）
        """
        self._documents: List[Any] = []
        self._tokenized_corpus: List[List[str]] = []
        self._bm25 = None
        
        if documents:
            self.add_documents(documents)
    
    def add_documents(self, documents: List[Any]) -> None:
        """
        添加文档到索引

        v2.6：分词后为空的文档**保留占位**（而非跳过）。
        此前 `if tokens:` 会静默丢弃这类文档，造成 BM25 与 FAISS 条目数不一致
        （实测测量学差 149 条，来自 PDF 公式抽取的私用区字符如 \\uf061）。
        它们多为公式/符号内容，对 STEM 教学有价值，不应因分词器处理不了就删掉。
        占位文档不会命中任何查询（空 token 列表），但保证两索引一一对应。
        """
        placeholder = 0
        # 2026-10-08：整批分词并走缓存。此前逐条 _tokenize_chinese（jieba），
        # 实测 1,560 块热构建中 BM25 占 1.69s（约热构建 89%）；缓存后未变化的分块
        # 直接复用 token，13k 块大库的热构建不再重复分词。
        texts = [doc.page_content if hasattr(doc, 'page_content') else str(doc)
                 for doc in documents]
        try:
            from .token_cache import tokenize_many
            token_lists, token_stats = tokenize_many(texts)
        except Exception as e:      # 缓存不可用不影响正确性
            logger.warning(f"分词缓存不可用，回退逐条分词: {e}")
            token_stats = {}
            token_lists = [_tokenize_chinese(t) for t in texts]
        for doc, tokens in zip(documents, token_lists):
            if not tokens:
                placeholder += 1
            self._documents.append(doc)
            self._tokenized_corpus.append(list(tokens))
        if token_stats:
            self.token_stats = token_stats
            logger.info(f"BM25 分词: 共 {token_stats.get('total')} 条，命中缓存 "
                        f"{token_stats.get('hits')} 条，实分词 {token_stats.get('misses')} 条"
                        f"（{token_stats.get('tokenize_ms')}ms）")

        # 重建BM25索引
        self._rebuild_index()
        logger.info(f"BM25索引已构建: {len(self._documents)}个文档"
                    + (f"（其中 {placeholder} 个为空分词占位）" if placeholder else ""))
    
    def _rebuild_index(self) -> None:
        """重建BM25索引"""
        if not self._tokenized_corpus:
            self._bm25 = None
            return
        
        if RANK_BM25_AVAILABLE:
            self._bm25 = BM25Okapi(self._tokenized_corpus)
            # rank_bm25 的 Robertson IDF 在 df == N / 2 时恰好为 0，且只修正负值。
            # 小型知识库很容易因此出现“关键词明确存在却全部 0 分”。沿用其 epsilon
            # 兜底思路给零权重一个正值；其它词的权重与既有大库排序保持不变。
            zero_idf_terms = [term for term, value in self._bm25.idf.items() if value == 0]
            if zero_idf_terms:
                floor = self._bm25.epsilon * max(self._bm25.average_idf, 1.0)
                for term in zero_idf_terms:
                    self._bm25.idf[term] = floor
        else:
            self._bm25 = _BuiltinBM25(self._tokenized_corpus)

    @classmethod
    def from_persisted(cls, documents: List[Any],
                       tokenized_corpus: List[List[str]]) -> "BM25Retriever":
        """
        从已持久化的语料恢复实例（v2.6）。

        tokenized_corpus 已是保存时的分词结果，此处**不再重新分词**
        （分词器版本一致性由 hybrid_retriever._load_bm25_index 负责校验）。

        此前 hybrid_retriever 用 `BM25Retriever.__new__` 绕过 __init__ 手动赋私有属性，
        任何给该类新增字段的改动都会在加载路径上静默失效。
        """
        obj = cls.__new__(cls)
        obj._documents = list(documents)
        obj._tokenized_corpus = [list(t) for t in tokenized_corpus]
        obj._bm25 = None
        obj._rebuild_index()
        return obj
    
    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        执行BM25检索
        
        Args:
            query: 查询文本
            top_k: 返回结果数量
            
        Returns:
            结果列表，每个元素包含：
            - document: Document对象
            - score: BM25分数
            - retrieval_type: "bm25"
        """
        if not self._bm25 or not self._documents:
            return []
        
        # 对query分词
        query_tokens = _tokenize_chinese(query)
        if not query_tokens:
            return []
        
        # 获取分数
        scores = self._bm25.get_scores(query_tokens)
        
        # 排序取top_k（跳过空分词占位文档，它们不可能命中）
        indexed_scores = [
            (i, s) for i, s in enumerate(scores)
            if s > 0 and self._tokenized_corpus[i]
        ]
        indexed_scores.sort(key=lambda x: x[1], reverse=True)
        indexed_scores = indexed_scores[:top_k]
        
        # 构建结果
        results = []
        for idx, score in indexed_scores:
            results.append({
                'document': self._documents[idx],
                'score': float(score),
                'retrieval_type': 'bm25'
            })
        
        return results
    
    def clear(self) -> None:
        """清空索引"""
        self._documents.clear()
        self._tokenized_corpus.clear()
        self._bm25 = None
    
    @property
    def doc_count(self) -> int:
        """索引中的文档数量"""
        return len(self._documents)
    
    @property
    def is_empty(self) -> bool:
        """索引是否为空"""
        return len(self._documents) == 0
    
    def get_status(self) -> Dict[str, Any]:
        """获取检索器状态"""
        return {
            'document_count': self.doc_count,
            'rank_bm25_available': RANK_BM25_AVAILABLE,
            'implementation': 'rank_bm25' if RANK_BM25_AVAILABLE else 'builtin',
        }


# ========== RRF融合 ==========

def reciprocal_rank_fusion(
    result_lists: List[List[Dict[str, Any]]],
    k: int = 60
) -> List[Dict[str, Any]]:
    """
    Reciprocal Rank Fusion (RRF) 融合多路检索结果
    
    RRF分数 = Σ 1 / (k + rank_i)
    
    Args:
        result_lists: 多路检索结果列表，每路为一个结果列表
        k: RRF参数，默认60（论文推荐值）
        
    Returns:
        融合后的结果列表，按RRF分数降序排列
    """
    # 收集所有文档的RRF分数
    doc_scores: Dict[str, float] = {}
    doc_map: Dict[str, Dict[str, Any]] = {}
    
    for results in result_lists:
        for rank, result in enumerate(results):
            doc = result.get('document')
            if not doc:
                continue
            
            # 使用文档内容的hash作为唯一标识
            content = doc.page_content if hasattr(doc, 'page_content') else str(doc)
            doc_key = str(hash(content))
            
            # RRF公式
            rrf_score = 1.0 / (k + rank + 1)
            doc_scores[doc_key] = doc_scores.get(doc_key, 0) + rrf_score
            
            # 保留文档引用和原始检索类型
            if doc_key not in doc_map:
                doc_map[doc_key] = {
                    'document': doc,
                    'score': 0.0,
                    'retrieval_type': result.get('retrieval_type', 'unknown'),
                    'original_scores': {}
                }
            
            # 记录原始分数
            retrieval_type = result.get('retrieval_type', 'unknown')
            doc_map[doc_key]['original_scores'][retrieval_type] = result.get('score', 0)
    
    # 更新RRF分数
    for doc_key, rrf_score in doc_scores.items():
        doc_map[doc_key]['score'] = rrf_score
    
    # 按RRF分数排序
    fused = sorted(doc_map.values(), key=lambda x: x['score'], reverse=True)
    
    # 更新retrieval_type为"hybrid"（如果来自多路）
    for item in fused:
        original_types = item.get('original_scores', {})
        if len(original_types) > 1:
            item['retrieval_type'] = 'hybrid'
    
    return fused


def check_bm25_status() -> Dict[str, Any]:
    """检查BM25模块状态"""
    return {
        'rank_bm25_available': RANK_BM25_AVAILABLE,
        'builtin_available': True,
        'recommended_install': 'pip install rank_bm25' if not RANK_BM25_AVAILABLE else None,
    }
