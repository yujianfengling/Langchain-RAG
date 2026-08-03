"""
图片处理模块
提供图片加载、OCR提取、图片描述等功能
"""

from .loader import ImageLoader
from .extractor import ImageExtractor

__all__ = [
    'ImageLoader',
    'ImageExtractor'
]
