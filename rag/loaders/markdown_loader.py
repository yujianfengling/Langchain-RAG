"""
MarkdownLoader - Markdown 文件加载器

负责加载 .md 文件。
"""

from pathlib import Path
from typing import List, Union
import logging

from .base import BaseLoader, LoadResult

logger = logging.getLogger(__name__)


class MarkdownLoader(BaseLoader):
    """
    Markdown 文件加载器
    
    支持加载 .md 文件，使用 LangChain UnstructuredMarkdownLoader 或直接读取。
    保持与 V2.2.1 相同的行为。
    """
    
    def supports(self, file_path: Union[str, Path]) -> bool:
        """判断是否支持该文件"""
        file_path = Path(file_path)
        return file_path.suffix.lower() in ['.md', '.markdown']
    
    def load(self, file_path: Union[str, Path]) -> LoadResult:
        """
        加载 Markdown 文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            LoadResult 包含加载的文档列表
        """
        try:
            file_path = self._validate_file(file_path)
            
            # 检查 LangChain UnstructuredMarkdownLoader 是否可用
            try:
                from langchain_community.document_loaders import UnstructuredMarkdownLoader
                langchain_available = True
            except ImportError:
                langchain_available = False
            
            # 尝试使用 LangChain UnstructuredMarkdownLoader
            if langchain_available:
                try:
                    loader = UnstructuredMarkdownLoader(str(file_path))
                    documents = loader.load()
                    return self._create_success_result(documents)
                except Exception as e:
                    logger.warning(f"Markdown加载器失败，使用降级方案: {e}")
                    # 降级到 TextLoader
                    from .text_loader import TextLoader
                    text_loader = TextLoader()
                    return text_loader.load(file_path)
            
            # 降级方案：直接读取文件
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 创建 Document 对象
                from langchain_core.documents import Document
                metadata = {
                    'source': str(file_path),
                    'file_name': file_path.name,
                    'file_type': 'md'
                }
                doc = Document(page_content=content, metadata=metadata)
                return self._create_success_result([doc])
            except Exception as e:
                return self._create_error_result(f"读取Markdown文件失败: {str(e)}")
            
        except Exception as e:
            return self._create_error_result(str(e))
    
    def get_supported_extensions(self) -> List[str]:
        """获取支持的文件扩展名"""
        return ['.md', '.markdown']
