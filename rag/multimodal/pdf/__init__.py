"""
PDF 处理模块

提供 PDF 元素检测、提取和处理功能。

V2.3.0 新增：
- PDFElement 数据模型
- PDFAnalyzer 元素分析器
- 各种元素检测器
"""

from .analyzer import PDFAnalyzer
from .models import PDFElement, ElementType

__all__ = [
    'PDFAnalyzer',
    'PDFElement',
    'ElementType',
]
