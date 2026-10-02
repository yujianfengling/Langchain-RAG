"""
PDF 处理器模块

提供视觉处理、公式处理、表格处理等功能。

⚠️ 注意（2026-10-02）：本包的 `VisionProcessor` **未接入生产链路**——
PDF 导入实际走 `rag/enrichment/multimodal_enricher.py` 的 `MultimodalEnricher`
（见 `rag/loaders/pdf_loader.py`）。改视觉行为请改那一套，否则改了看不到效果。
"""

from .vision_processor import VisionProcessor
from .formula_processor import FormulaProcessor
from .table_processor import TableProcessor

__all__ = [
    'VisionProcessor',
    'FormulaProcessor',
    'TableProcessor',
]
