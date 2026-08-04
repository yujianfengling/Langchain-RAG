"""
PDF 文档适配器模块

将 PDFElement 转换为 LangChain Document。
"""

from .document_adapter import DocumentAdapter
from .text_adapter import TextAdapter
from .image_adapter import ImageAdapter
from .formula_adapter import FormulaAdapter
from .table_adapter import TableAdapter

__all__ = [
    'DocumentAdapter',
    'TextAdapter',
    'ImageAdapter',
    'FormulaAdapter',
    'TableAdapter',
]
