"""
公式适配器

将公式类型的 PDFElement 转换为 Document。
"""

import logging
from pathlib import Path
from typing import Optional

from langchain_core.documents import Document

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class FormulaAdapter:
    """
    公式适配器
    
    将公式类型的 PDFElement 转换为 Document。
    """
    
    def adapt(
        self,
        element: PDFElement,
        pdf_path: Optional[Path] = None
    ) -> Optional[Document]:
        """
        转换公式元素
        
        Args:
            element: PDFElement
            pdf_path: PDF 文件路径
            
        Returns:
            Document 或 None
        """
        if element.type != ElementType.FORMULA:
            logger.warning(f"不是公式元素: {element.type}")
            return None
        
        try:
            # 获取公式内容
            content = element.content
            
            if not content or not content.strip():
                logger.debug("空公式，跳过")
                return None
            
            # 构建公式描述
            description = self._build_description(element)
            
            # 构建元数据
            metadata = self._build_metadata(element, pdf_path)
            
            # 创建 Document
            doc = Document(
                page_content=description,
                metadata=metadata
            )
            
            logger.debug(f"公式转换成功: {description[:50]}...")
            return doc
            
        except Exception as e:
            logger.error(f"公式转换失败: {str(e)}")
            return None
    
    def _build_description(self, element: PDFElement) -> str:
        """
        构建公式描述
        
        Args:
            element: PDFElement
            
        Returns:
            公式描述
        """
        content = element.content
        
        # 基本信息
        parts = []
        
        # 添加类型标识
        parts.append("[公式]")
        
        # 添加原始公式
        parts.append(f"原始: {content}")
        
        # 如果有 LaTeX，添加
        if element.metadata and 'latex' in element.metadata:
            parts.append(f"LaTeX: {element.metadata['latex']}")
        
        # 添加页码信息
        parts.append(f"页码: {element.page + 1}")
        
        # 添加公式类型
        if element.metadata and 'formula_type' in element.metadata:
            parts.append(f"类型: {element.metadata['formula_type']}")
        
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
            'type': 'formula',
            'page': element.page,
            'bbox': list(element.bbox),
            'confidence': element.confidence,
        }
        
        # 添加文件信息
        if pdf_path:
            metadata['source'] = str(pdf_path)
            metadata['file_name'] = pdf_path.name
            metadata['file_type'] = 'pdf'
        
        # 添加公式信息
        if element.metadata:
            if 'formula_type' in element.metadata:
                metadata['formula_type'] = element.metadata['formula_type']
            if 'is_inline' in element.metadata:
                metadata['is_inline'] = element.metadata['is_inline']
            if 'latex' in element.metadata:
                metadata['latex'] = element.metadata['latex']
        
        return metadata
