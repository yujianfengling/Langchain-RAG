"""
Embedding模块
负责管理本地Embedding模型，提供文本向量化功能
使用LangChain Embeddings接口，支持HuggingFace模型
"""

import os
import logging
from typing import List, Optional, Union

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

# 全局模型缓存
_embedding_model = None


class BatchedHuggingFaceEmbeddings(Embeddings):
    """
    支持批量处理的HuggingFace Embeddings包装器
    解决sentence-transformers 5.6.0与transformers 5.14.1的兼容性问题
    """
    
    def __init__(self, model_name: str, model_kwargs: dict = None, encode_kwargs: dict = None, batch_size: int = 64):
        """
        初始化批量Embeddings包装器
        
        Args:
            model_name: 模型名称或路径
            model_kwargs: 模型参数
            encode_kwargs: 编码参数
            batch_size: 批量处理大小
        """
        try:
            from langchain_community.embeddings import HuggingFaceEmbeddings
            
            self._batch_size = batch_size
            self._model = HuggingFaceEmbeddings(
                model_name=model_name,
                model_kwargs=model_kwargs or {},
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
        
        # 清理文本：确保都是字符串且非空
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
        
        return self._model.embed_query(text)


def get_embedding_model() -> Embeddings:
    """
    获取Embedding模型实例（单例模式）
    
    Returns:
        Embeddings模型实例
        
    Raises:
        ImportError: 缺少必要的依赖包
        RuntimeError: 模型加载失败
    """
    global _embedding_model
    
    # 如果模型已加载，直接返回
    if _embedding_model is not None:
        return _embedding_model
    
    # 从环境变量获取模型名称
    model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    
    # 优先使用本地模型目录（离线可用）
    local_model_dir = os.getenv("EMBEDDING_LOCAL_DIR", "")
    if local_model_dir:
        local_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), local_model_dir)
        if os.path.isdir(local_path):
            model_name = local_path
            print(f"[Embedding] 使用本地模型: {model_name}")
    
    # 检查依赖
    if not HUGGINGFACE_AVAILABLE:
        raise ImportError(
            "缺少langchain-community包，请运行: pip install langchain-community\n"
            "或者: pip install langchain[community]"
        )
    
    try:
        # 创建模型实例
        model_kwargs = {'device': 'cpu'}  # 默认使用CPU
        encode_kwargs = {
            'normalize_embeddings': True,  # 归一化嵌入
        }
        
        # 使用批量包装器，避免sentence-transformers兼容性问题
        _embedding_model = BatchedHuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs=model_kwargs,
            encode_kwargs=encode_kwargs,
            batch_size=64  # 限制批量大小
        )
        
        # 验证模型是否可用
        _embedding_model.embed_query("测试")
        
        return _embedding_model
        
    except Exception as e:
        # 清除可能的部分加载
        _embedding_model = None
        
        # 提供详细的错误信息
        error_msg = f"加载Embedding模型失败: {str(e)}\n"
        error_msg += "可能的原因:\n"
        error_msg += "1. 模型下载失败，请检查网络连接\n"
        error_msg += "2. transformers包未安装: pip install transformers\n"
        error_msg += "3. torch包未安装: pip install torch\n"
        error_msg += f"4. 模型名称错误: {model_name}\n"
        
        raise RuntimeError(error_msg)


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
        # 获取模型
        model = get_embedding_model()
        
        # 提取文本内容
        texts = []
        for doc in documents:
            if hasattr(doc, 'page_content'):
                # Document对象
                texts.append(doc.page_content)
            elif isinstance(doc, str):
                # 字符串
                texts.append(doc)
            else:
                raise ValueError(f"不支持的文档类型: {type(doc)}")
        
        # 批量向量化
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
        # 获取模型
        model = get_embedding_model()
        
        # 向量化查询
        embedding = model.embed_query(query)
        
        return embedding
        
    except Exception as e:
        raise RuntimeError(f"查询向量化失败: {str(e)}")


def get_embedding_dimension() -> Optional[int]:
    """
    获取Embedding向量的维度
    
    Returns:
        向量维度，如果模型未加载则返回None
    """
    try:
        model = get_embedding_model()
        # 使用测试文本获取维度
        test_embedding = model.embed_query("测试")
        return len(test_embedding)
    except Exception:
        return None


def check_embedding_status() -> dict:
    """
    检查Embedding模块的状态
    
    Returns:
        状态信息字典
    """
    status = {
        'langchain_available': LANGCHAIN_AVAILABLE,
        'huggingface_available': HUGGINGFACE_AVAILABLE,
        'model_loaded': _embedding_model is not None,
        'model_name': os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"),
        'dimension': get_embedding_dimension()
    }
    
    # 检查torch和transformers
    try:
        import torch
        status['torch_available'] = True
        status['torch_version'] = torch.__version__
    except ImportError:
        status['torch_available'] = False
    
    try:
        import transformers
        status['transformers_available'] = True
        status['transformers_version'] = transformers.__version__
    except ImportError:
        status['transformers_available'] = False
    
    return status


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