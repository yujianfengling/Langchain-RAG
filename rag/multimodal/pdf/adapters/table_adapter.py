"""
表格适配器

将表格类型的 PDFElement 转换为 Document。
"""

import logging
from pathlib import Path
from typing import Optional

from langchain_core.documents import Document

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class TableAdapter:
    """
    表格适配器
    
    将表格类型的 PDFElement 转换为 Document。
    """
    
    def adapt(
        self,
        element: PDFElement,
        pdf_path: Optional[Path] = None
    ) -> Optional[Document]:
        """
        转换表格元素
        
        Args:
            element: PDFElement
            pdf_path: PDF 文件路径
            
        Returns:
            Document 或 None
        """
        if element.type != ElementType.TABLE:
            logger.warning(f"不是表格元素: {element.type}")
            return None
        
        try:
            # 构建表格描述
            description = self._build_description(element)
            
            # 构建元数据
            metadata = self._build_metadata(element, pdf_path)
            
            # 创建 Document
            doc = Document(
                page_content=description,
                metadata=metadata
            )
            
            logger.debug(f"表格转换成功: {description[:50]}...")
            return doc
            
        except Exception as e:
            logger.error(f"表格转换失败: {str(e)}")
            return None
    
    def _build_description(self, element: PDFElement) -> str:
        """
        构建表格描述
        
        Args:
            element: PDFElement
            
        Returns:
            表格描述
        """
        content = element.content
        
        # 基本信息
        parts = []
        
        # 添加类型标识
        parts.append("[表格]")
        
        # 添加表格尺寸
        rows = content.get('rows', 0)
        cols = content.get('cols', 0)
        parts.append(f"尺寸: {rows}行x{cols}列")
        
        # 添加页码信息
        parts.append(f"页码: {element.page + 1}")
        
        # 添加表格数据预览
        data = content.get('data', [])
        if data:
            # 只显示前几行
            preview_rows = data[:3]  # 最多显示3行
            preview_lines = []
            for row in preview_rows:
                preview_lines.append(" | ".join(str(cell) for cell in row))
            preview = "; ".join(preview_lines)
            parts.append(f"预览: {preview}")
        
        # 如果有表头信息
        if element.metadata and element.metadata.get('has_header'):
            parts.append("有表头")
        
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
            'type': 'table',
            'modality': 'table',
            'page': element.page,
            'bbox': list(element.bbox),
            'confidence': element.confidence,
        }
        
        # 添加文件信息
        if pdf_path:
            metadata['source'] = str(pdf_path)
            metadata['file_name'] = pdf_path.name
            metadata['file_type'] = 'pdf'
        
        # 添加表格信息
        content = element.content
        if isinstance(content, dict):
            metadata['table_rows'] = content.get('rows', 0)
            metadata['table_cols'] = content.get('cols', 0)
        
        # 添加额外元数据
        if element.metadata:
            if 'has_header' in element.metadata:
                metadata['has_header'] = element.metadata['has_header']
            if 'is_empty' in element.metadata:
                metadata['is_empty'] = element.metadata['is_empty']
        
        return metadata
