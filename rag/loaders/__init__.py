"""
rag.loaders - 文档加载器模块

提供统一的文档加载接口，支持多种文件格式。
"""

from .base import BaseLoader, LoadResult
from .text_loader import TextLoader
from .markdown_loader import MarkdownLoader
from .pdf_loader import PdfLoader
from .docx_loader import DocxLoader
from .image_loader import ImageLoader

__all__ = [
    'BaseLoader',
    'LoadResult',
    'TextLoader',
    'MarkdownLoader',
    'PdfLoader',
    'DocxLoader',
    'ImageLoader',
    'get_loader_for_file',
]

def get_loader_for_file(file_path):
    """
    根据文件路径获取对应的 Loader 实例
    
    Args:
        file_path: 文件路径
        
    Returns:
        对应的 Loader 实例，如果不支持则返回 None
    """
    from pathlib import Path
    
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()
    
    # 文件扩展名到 Loader 的映射
    loader_map = {
        '.txt': TextLoader,
        '.md': MarkdownLoader,
        '.markdown': MarkdownLoader,
        '.pdf': PdfLoader,
        '.docx': DocxLoader,
        '.doc': DocxLoader,
        '.jpg': ImageLoader,
        '.jpeg': ImageLoader,
        '.png': ImageLoader,
        '.webp': ImageLoader,
    }
    
    loader_class = loader_map.get(suffix)
    if loader_class is None:
        return None
    
    return loader_class()
