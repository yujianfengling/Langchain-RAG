"""
文本适配器

将文本类型的 PDFElement 转换为 Document。
"""

import logging
from pathlib import Path
from typing import Optional

from langchain_core.documents import Document

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class TextAdapter:
    """
    文本适配器
    
    将文本类型的 PDFElement 转换为 Document。
    """
    
    def adapt(
        self,
        element: PDFElement,
        pdf_path: Optional[Path] = None
    ) -> Optional[Document]:
        """
        转换文本元素
        
        Args:
            element: PDFElement
            pdf_path: PDF 文件路径
            
        Returns:
            Document 或 None
        """
        if element.type != ElementType.TEXT:
            logger.warning(f"不是文本元素: {element.type}")
            return None
        
        try:
            # 获取文本内容
            content = element.content
            
            if not content or not content.strip():
                logger.debug("空文本，跳过")
                return None
            
            # 构建元数据
            metadata = self._build_metadata(element, pdf_path)
            
            # 创建 Document
            doc = Document(
                page_content=content.strip(),
                metadata=metadata
            )
            
            logger.debug(f"文本转换成功: {len(content)} 字符")
            return doc
            
        except Exception as e:
            logger.error(f"文本转换失败: {str(e)}")
            return None
    
    def _build_metadata(
        self,
        element: PDFElement,
        pdf_path: Optional[Path] = None
    ) -> dict:
        """
        构建元数据
        
        Args:
            element: PDFElement
            pdf_path: PDF 文件路径
            
        Returns:
            元数据字典
        """
        metadata = {
            'type': 'text',
            'page': element.page,
            'bbox': list(element.bbox),
            'confidence': element.confidence,
        }
        
        # 添加文件信息
        if pdf_path:
            metadata['source'] = str(pdf_path)
            metadata['file_name'] = pdf_path.name
            metadata['file_type'] = 'pdf'
        
        # 添加额外元数据
        if element.metadata:
            # 添加字体信息
            if 'font_size' in element.metadata:
                metadata['font_size'] = element.metadata['font_size']
            if 'is_bold' in element.metadata:
                metadata['is_bold'] = element.metadata['is_bold']
        
        return metadata
