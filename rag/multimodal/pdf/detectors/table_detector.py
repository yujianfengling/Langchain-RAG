"""
表格检测器

检测 PDF 中的表格元素。
"""

import logging
from typing import List

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class TableDetector:
    """
    表格检测器
    
    检测 PDF 中的表格元素，返回表格位置和结构信息。
    """
    
    def detect(self, page, page_num: int) -> List[PDFElement]:
        """
        检测页面中的表格
        
        Args:
            page: PyMuPDF 页面对象
            page_num: 页码
            
        Returns:
            表格元素列表
        """
        elements = []
        
        try:
            # 使用 PyMuPDF 的表格检测功能
            # 注意：PyMuPDF 1.23.0+ 支持表格检测
            tables = page.find_tables()
            
            for table_index, table in enumerate(tables):
                # 获取表格边界框
                bbox = table.bbox
                
                # 获取表格数据
                table_data = table.extract()
                
                # 计算置信度（基于表格结构的完整性）
                confidence = self._calculate_confidence(table_data)
                
                element = PDFElement(
                    type=ElementType.TABLE,
                    content={
                        'data': table_data,
                        'rows': len(table_data),
                        'cols': len(table_data[0]) if table_data else 0,
                        'index': table_index,
                    },
                    page=page_num,
                    bbox=bbox,
                    confidence=confidence,
                    metadata={
                        'has_header': self._has_header(table_data),
                        'is_empty': len(table_data) == 0,
                    }
                )
                elements.append(element)
            
            logger.debug(f"第 {page_num} 页检测到 {len(elements)} 个表格元素")
            return elements
            
        except Exception as e:
            logger.error(f"表格检测失败: 第 {page_num} 页, 错误: {str(e)}")
            return []
    
    def _calculate_confidence(self, table_data: list) -> float:
        """
        计算表格检测的置信度
        
        Args:
            table_data: 表格数据
            
        Returns:
            置信度 (0.0-1.0)
        """
        if not table_data:
            return 0.0
        
        # 检查表格是否为空
        if len(table_data) == 0:
            return 0.0
        
        # 检查每行的列数是否一致
        col_counts = set(len(row) for row in table_data)
        if len(col_counts) != 1:
            return 0.5  # 列数不一致，置信度较低
        
        # 检查是否有空行
        empty_rows = sum(1 for row in table_data if all(cell is None or cell == "" for cell in row))
        if empty_rows > 0:
            return 0.7
        
        return 1.0
    
    def _has_header(self, table_data: list) -> bool:
        """
        判断表格是否有表头
        
        Args:
            table_data: 表格数据
            
        Returns:
            是否有表头
        """
        if not table_data or len(table_data) < 2:
            return False
        
        # 简单判断：第一行是否都是文本
        first_row = table_data[0]
        for cell in first_row:
            if cell is None or cell == "":
                return False
            # 如果是数字，可能不是表头
            try:
                float(cell)
                return False
            except ValueError:
                pass
        
        return True
