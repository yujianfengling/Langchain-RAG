"""
RAG检索质量评估模块
提供检索质量统计和评估功能
"""

import os
import json
import time
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from datetime import datetime

# 导入配置
try:
    from config import settings
    CONFIG_AVAILABLE = True
except ImportError:
    CONFIG_AVAILABLE = False

# 导入RAG服务
try:
    from .rag_service import retrieve_context, build_context, list_available_subjects
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False


class RAGEvaluator:
    """
    RAG检索质量评估器
    """
    
    def __init__(self, subject_id: Optional[str] = None):
        """
        初始化评估器
        
        Args:
            subject_id: 学科ID，如果为None则评估所有学科
        """
        self.subject_id = subject_id
        self.stats = {
            "total_queries": 0,
            "successful_queries": 0,
            "failed_queries": 0,
            "empty_results": 0,
            "total_results": 0,
            "total_latency": 0.0,
            "avg_latency": 0.0,
            "avg_results_per_query": 0.0,
            "empty_result_ratio": 0.0,
            "chunk_sizes": [],
            "chunk_lengths": [],
            "source_files": set(),
            "evaluation_time": datetime.now().isoformat()
        }
    
    def evaluate_query(
        self,
        question: str,
        top_k: int = 5
    ) -> Dict[str, Any]:
        """
        评估单个查询的检索质量
        
        Args:
            question: 查询问题
            top_k: 返回结果数量
            
        Returns:
            评估结果字典
        """
        if not RAG_AVAILABLE:
            return {
                "success": False,
                "error": "RAG模块不可用"
            }
        
        start_time = time.time()
        
        try:
            # 执行检索
            results = retrieve_context(
                question=question,
                subject_id=self.subject_id,
                top_k=top_k
            )
            
            latency = time.time() - start_time
            
            # 更新统计
            self.stats["total_queries"] += 1
            self.stats["successful_queries"] += 1
            self.stats["total_latency"] += latency
            self.stats["total_results"] += len(results)
            
            # 更新chunk统计
            for doc in results:
                chunk_size = len(doc.page_content)
                self.stats["chunk_sizes"].append(chunk_size)
                self.stats["chunk_lengths"].append(chunk_size)
                
                # 提取来源文件
                if hasattr(doc, 'metadata') and 'source' in doc.metadata:
                    self.stats["source_files"].add(doc.metadata['source'])
            
            # 检查是否为空结果
            if len(results) == 0:
                self.stats["empty_results"] += 1
            
            # 计算平均值
            self._update_averages()
            
            return {
                "success": True,
                "question": question,
                "num_results": len(results),
                "latency": latency,
                "chunk_sizes": [len(doc.page_content) for doc in results],
                "sources": [doc.metadata.get('source', 'unknown') for doc in results if hasattr(doc, 'metadata')]
            }
            
        except Exception as e:
            latency = time.time() - start_time
            self.stats["total_queries"] += 1
            self.stats["failed_queries"] += 1
            self.stats["total_latency"] += latency
            
            return {
                "success": False,
                "question": question,
                "error": str(e),
                "latency": latency
            }
    
    def evaluate_batch(
        self,
        questions: List[str],
        top_k: int = 5
    ) -> Dict[str, Any]:
        """
        批量评估检索质量
        
        Args:
            questions: 问题列表
            top_k: 返回结果数量
            
        Returns:
            批量评估结果
        """
        results = []
        
        for question in questions:
            result = self.evaluate_query(question, top_k)
            results.append(result)
        
        # 计算批量统计
        successful = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]
        
        batch_stats = {
            "total_questions": len(questions),
            "successful_queries": len(successful),
            "failed_queries": len(failed),
            "avg_latency": sum(r["latency"] for r in successful) / len(successful) if successful else 0,
            "avg_results": sum(r["num_results"] for r in successful) / len(successful) if successful else 0,
            "results": results
        }
        
        return batch_stats
    
    def _update_averages(self) -> None:
        """
        更新平均值统计
        """
        if self.stats["total_queries"] > 0:
            self.stats["avg_latency"] = self.stats["total_latency"] / self.stats["total_queries"]
            self.stats["avg_results_per_query"] = self.stats["total_results"] / self.stats["total_queries"]
            self.stats["empty_result_ratio"] = self.stats["empty_results"] / self.stats["total_queries"]
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            统计信息字典
        """
        # 计算chunk大小统计
        chunk_stats = {}
        if self.stats["chunk_sizes"]:
            sizes = self.stats["chunk_sizes"]
            chunk_stats = {
                "count": len(sizes),
                "min_size": min(sizes),
                "max_size": max(sizes),
                "avg_size": sum(sizes) / len(sizes),
                "median_size": sorted(sizes)[len(sizes) // 2]
            }
        
        return {
            "summary": {
                "total_queries": self.stats["total_queries"],
                "successful_queries": self.stats["successful_queries"],
                "failed_queries": self.stats["failed_queries"],
                "empty_results": self.stats["empty_results"],
                "empty_result_ratio": self.stats["empty_result_ratio"],
                "avg_latency": self.stats["avg_latency"],
                "avg_results_per_query": self.stats["avg_results_per_query"]
            },
            "chunk_statistics": chunk_stats,
            "source_files": list(self.stats["source_files"]),
            "evaluation_time": self.stats["evaluation_time"]
        }
    
    def save_stats(self, file_path: Optional[str] = None) -> str:
        """
        保存统计信息到文件
        
        Args:
            file_path: 文件路径，如果为None则自动生成
            
        Returns:
            保存的文件路径
        """
        if file_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            subject_suffix = f"_{self.subject_id}" if self.subject_id else ""
            file_path = f"logs/rag_evaluation{subject_suffix}_{timestamp}.json"
        
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # 保存统计信息
        stats = self.get_stats()
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        
        return file_path
    
    def reset_stats(self) -> None:
        """
        重置统计信息
        """
        self.stats = {
            "total_queries": 0,
            "successful_queries": 0,
            "failed_queries": 0,
            "empty_results": 0,
            "total_results": 0,
            "total_latency": 0.0,
            "avg_latency": 0.0,
            "avg_results_per_query": 0.0,
            "empty_result_ratio": 0.0,
            "chunk_sizes": [],
            "chunk_lengths": [],
            "source_files": set(),
            "evaluation_time": datetime.now().isoformat()
        }


def evaluate_rag_quality(
    questions: List[str],
    subject_id: Optional[str] = None,
    top_k: int = 5,
    save_results: bool = True
) -> Dict[str, Any]:
    """
    评估RAG检索质量
    
    Args:
        questions: 问题列表
        subject_id: 学科ID
        top_k: 返回结果数量
        save_results: 是否保存结果
        
    Returns:
        评估结果
    """
    evaluator = RAGEvaluator(subject_id)
    
    # 执行批量评估
    batch_results = evaluator.evaluate_batch(questions, top_k)
    
    # 获取统计信息
    stats = evaluator.get_stats()
    
    # 保存结果
    if save_results:
        file_path = evaluator.save_stats()
        stats["saved_to"] = file_path
    
    return {
        "batch_results": batch_results,
        "statistics": stats
    }


def get_rag_statistics(subject_id: Optional[str] = None) -> Dict[str, Any]:
    """
    获取RAG统计信息
    
    Args:
        subject_id: 学科ID
        
    Returns:
        统计信息字典
    """
    if not RAG_AVAILABLE:
        return {
            "success": False,
            "error": "RAG模块不可用"
        }
    
    try:
        # 获取可用学科列表
        subjects = list_available_subjects()
        
        # 如果没有指定学科，统计所有学科
        if subject_id is None:
            subject_stats = {}
            for subject in subjects:
                subject_stats[subject] = _get_subject_statistics(subject)
            
            return {
                "success": True,
                "total_subjects": len(subjects),
                "subjects": subjects,
                "subject_statistics": subject_stats
            }
        else:
            # 统计指定学科
            if subject_id not in subjects:
                return {
                    "success": False,
                    "error": f"学科 '{subject_id}' 不存在"
                }
            
            subject_stats = _get_subject_statistics(subject_id)
            
            return {
                "success": True,
                "subject_id": subject_id,
                "statistics": subject_stats
            }
            
    except Exception as e:
        return {
            "success": False,
            "error": f"获取统计信息失败: {str(e)}"
        }


def _get_subject_statistics(subject_id: str) -> Dict[str, Any]:
    """
    获取单个学科的统计信息
    
    Args:
        subject_id: 学科ID
        
    Returns:
        统计信息字典
    """
    stats = {
        "subject_id": subject_id,
        "documents_count": 0,
        "vector_store_exists": False,
        "source_files": []
    }
    
    try:
        # 检查向量存储是否存在
        if CONFIG_AVAILABLE:
            vector_store_path = settings.VECTOR_STORE_PATH / subject_id
        else:
            vector_store_path = Path(f"data/vector_store/{subject_id}")
        
        stats["vector_store_exists"] = vector_store_path.exists()
        
        # 检查源数据目录
        if CONFIG_AVAILABLE:
            subjects_data_path = settings.SUBJECTS_DATA_PATH / subject_id
        else:
            subjects_data_path = Path(f"data/subjects/{subject_id}")
        
        if subjects_data_path.exists():
            # 统计文件数量
            source_files = []
            for file_path in subjects_data_path.rglob("*"):
                if file_path.is_file():
                    source_files.append(str(file_path))
            
            stats["source_files"] = source_files
            stats["documents_count"] = len(source_files)
        
    except Exception as e:
        stats["error"] = str(e)
    
    return stats


# 公开API
__all__ = [
    "RAGEvaluator",
    "evaluate_rag_quality",
    "get_rag_statistics"
]