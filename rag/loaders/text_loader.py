"""
TextLoader - TXT 文件加载器

负责加载 .txt 文件。
"""

from pathlib import Path
from typing import List, Union
import logging

from .base import BaseLoader, LoadResult

logger = logging.getLogger(__name__)


class TextLoader(BaseLoader):
    """
    TXT 文件加载器
    
    支持加载 .txt 文件，使用 LangChain TextLoader 或直接读取。
    保持与 V2.2.1 相同的行为。
    """
    
    def supports(self, file_path: Union[str, Path]) -> bool:
        """判断是否支持该文件"""
        file_path = Path(file_path)
        return file_path.suffix.lower() in ['.txt']
    
    def load(self, file_path: Union[str, Path]) -> LoadResult:
        """
        加载 TXT 文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            LoadResult 包含加载的文档列表
        """
        try:
            file_path = self._validate_file(file_path)
            
            # 检查 LangChain TextLoader 是否可用
            try:
                from langchain_community.document_loaders import TextLoader as LangChainTextLoader
                langchain_available = True
            except ImportError:
                langchain_available = False
            
            # 尝试使用 LangChain TextLoader
            if langchain_available:
                try:
                    loader = LangChainTextLoader(str(file_path), encoding='utf-8')
                    documents = loader.load()
                    # 确保添加 file_name 和 file_type 字段
                    for doc in documents:
                        if 'file_name' not in doc.metadata:
                            doc.metadata['file_name'] = file_path.name
                        if 'file_type' not in doc.metadata:
                            doc.metadata['file_type'] = 'txt'
                    return self._create_success_result(documents)
                except Exception as e:
                    logger.warning(f"LangChain TextLoader failed: {e}, using fallback")
            
            # 降级方案：直接读取文件
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 创建 Document 对象
                from langchain_core.documents import Document
                metadata = {
                    # v2.6：source 只存文件名（避免完整本地路径进入 LLM 提示词）
                    'source': file_path.name,
                    'source_path': str(file_path),
                    'file_name': file_path.name,
                    'file_type': 'txt'
                }
                doc = Document(page_content=content, metadata=metadata)
                return self._create_success_result([doc])
            except Exception as e:
                return self._create_error_result(f"读取TXT文件失败: {str(e)}")
            
        except Exception as e:
            return self._create_error_result(str(e))
    
    def get_supported_extensions(self) -> List[str]:
        """获取支持的文件扩展名"""
        return ['.txt']
