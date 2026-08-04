"""
PDF 数据模型

定义 PDF 元素的数据结构。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, Optional


class ElementType(Enum):
    """PDF 元素类型"""
    TEXT = "text"
    IMAGE = "image"
    TABLE = "table"
    FORMULA = "formula"


@dataclass
class PDFElement:
    """
    PDF 元素数据模型
    
    Attributes:
        type: 元素类型 (text/image/table/formula)
        content: 元素内容 (文本内容、图片路径、表格数据等)
        page: 所在页码
        bbox: 边界框 (x0, y0, x1, y1)
        confidence: 检测置信度
        metadata: 额外元数据
    """
    type: ElementType
    content: Any
    page: int
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    confidence: float = 1.0
    metadata: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'type': self.type.value,
            'content': self.content,
            'page': self.page,
            'bbox': list(self.bbox),
            'confidence': self.confidence,
            'metadata': self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PDFElement':
        """从字典创建 PDFElement"""
        return cls(
            type=ElementType(data['type']),
            content=data['content'],
            page=data['page'],
            bbox=tuple(data['bbox']),
            confidence=data.get('confidence', 1.0),
            metadata=data.get('metadata', {})
        )
