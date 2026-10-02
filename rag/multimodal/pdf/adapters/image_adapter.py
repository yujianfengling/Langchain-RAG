"""
图片适配器

将图片类型的 PDFElement 转换为 Document。
"""

import logging
from pathlib import Path
from typing import Optional

from langchain_core.documents import Document

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class ImageAdapter:
    """
    图片适配器
    
    将图片类型的 PDFElement 转换为 Document。
    """
    
    def adapt(
        self,
        element: PDFElement,
        pdf_path: Optional[Path] = None
    ) -> Optional[Document]:
        """
        转换图片元素
        
        Args:
            element: PDFElement
            pdf_path: PDF 文件路径
            
        Returns:
            Document 或 None
        """
        if element.type != ElementType.IMAGE:
            logger.warning(f"不是图片元素: {element.type}")
            return None
        
        try:
            # 构建图片描述
            description = self._build_description(element)
            
            # 构建元数据
            metadata = self._build_metadata(element, pdf_path)
            
            # 创建 Document
            doc = Document(
                page_content=description,
                metadata=metadata
            )
            
            logger.debug(f"图片转换成功: {description[:50]}...")
            return doc
            
        except Exception as e:
            logger.error(f"图片转换失败: {str(e)}")
            return None
    
    def _build_description(self, element: PDFElement) -> str:
        """
        构建图片描述
        
        Args:
            element: PDFElement
            
        Returns:
            图片描述
        """
        content = element.content
        
        # 基本信息
        width = content.get('width', 0)
        height = content.get('height', 0)
        
        # 构建描述
        parts = []
        
        # 添加类型标识
        parts.append("[图片]")
        
        # 添加尺寸信息
        if width and height:
            parts.append(f"尺寸: {width}x{height}")
        
        # 添加页码信息
        parts.append(f"页码: {element.page + 1}")
        
        # 如果有额外描述，添加
        if element.metadata and 'description' in element.metadata:
            parts.append(f"描述: {element.metadata['description']}")
        
        return " | ".join(parts)
    
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
            'type': 'image',
            'modality': 'image',
            'page': element.page,
            'bbox': list(element.bbox),
            'confidence': element.confidence,
        }
        
        # 添加文件信息
        if pdf_path:
            metadata['source'] = str(pdf_path)
            metadata['file_name'] = pdf_path.name
            metadata['file_type'] = 'pdf'
        
        # 添加图片信息
        content = element.content
        if isinstance(content, dict):
            metadata['image_width'] = content.get('width', 0)
            metadata['image_height'] = content.get('height', 0)
            metadata['image_xref'] = content.get('xref', 0)
            # 添加 image_path 字段（如果存在）
            if 'image_path' in content:
                metadata['image_path'] = content['image_path']
        
        # 添加额外元数据
        if element.metadata:
            if 'image_type' in element.metadata:
                metadata['image_type'] = element.metadata['image_type']
            if 'size' in element.metadata:
                metadata['image_size'] = element.metadata['size']
        
        return metadata
