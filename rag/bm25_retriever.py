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

# 简单的中文分词：按字符+标点分割
_CN_TOKENIZE_RE = re.compile(
    r'[\u4e00-\u9fff]|[a-zA-Z]+|\d+|[^\s\w\u4e00-\u9fff]'
)


def _tokenize_chinese(text: str) -> List[str]:
    """
    中文文本分词
    
    使用简单的字符级分割 + 英文单词分割
    对于教育场景足够使用，无需引入jieba等重型分词库
    """
    text = text.lower().strip()
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
        
        Args:
            documents: Document对象列表
        """
        for doc in documents:
            text = doc.page_content if hasattr(doc, 'page_content') else str(doc)
            tokens = _tokenize_chinese(text)
            if tokens:  # 只添加非空文档
                self._documents.append(doc)
                self._tokenized_corpus.append(tokens)
        
        # 重建BM25索引
        self._rebuild_index()
        logger.info(f"BM25索引已构建: {len(self._documents)}个文档")
    
    def _rebuild_index(self) -> None:
        """重建BM25索引"""
        if not self._tokenized_corpus:
            self._bm25 = None
            return
        
        if RANK_BM25_AVAILABLE:
            self._bm25 = BM25Okapi(self._tokenized_corpus)
        else:
            self._bm25 = _BuiltinBM25(self._tokenized_corpus)
    
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
        
        # 排序取top_k
        indexed_scores = [(i, s) for i, s in enumerate(scores) if s > 0]
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
