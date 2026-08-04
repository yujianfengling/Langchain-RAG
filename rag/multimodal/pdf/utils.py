"""
PDF 处理工具函数

提供 PDF 处理的辅助功能。
"""

import logging
from pathlib import Path
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


def validate_pdf_file(file_path: Path) -> bool:
    """
    验证 PDF 文件
    
    Args:
        file_path: 文件路径
        
    Returns:
        是否为有效的 PDF 文件
    """
    if not file_path.exists():
        logger.error(f"文件不存在: {file_path}")
        return False
    
    if file_path.suffix.lower() != '.pdf':
        logger.error(f"不是 PDF 文件: {file_path}")
        return False
    
    # 检查文件大小
    if file_path.stat().st_size == 0:
        logger.error(f"文件为空: {file_path}")
        return False
    
    return True


def get_page_count(pdf_path: Path) -> int:
    """
    获取 PDF 页数
    
    Args:
        pdf_path: PDF 文件路径
        
    Returns:
        页数
    """
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        count = len(doc)
        doc.close()
        return count
    except Exception as e:
        logger.error(f"获取页数失败: {pdf_path}, 错误: {str(e)}")
        return 0


def extract_page_as_image(
    pdf_path: Path,
    page_num: int,
    output_path: Optional[Path] = None,
    dpi: int = 150
) -> Optional[Path]:
    """
    将 PDF 页面导出为图片
    
    Args:
        pdf_path: PDF 文件路径
        page_num: 页码 (从 0 开始)
        output_path: 输出路径 (可选)
        dpi: 分辨率
        
    Returns:
        输出文件路径
    """
    try:
        import fitz
        
        doc = fitz.open(str(pdf_path))
        
        if page_num < 0 or page_num >= len(doc):
            logger.error(f"页码超出范围: {page_num} (总共 {len(doc)} 页)")
            doc.close()
            return None
        
        page = doc[page_num]
        
        # 设置缩放比例
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        
        # 渲染页面
        pix = page.get_pixmap(matrix=mat)
        
        # 确定输出路径
        if output_path is None:
            output_path = pdf_path.parent / f"{pdf_path.stem}_page{page_num}.png"
        
        # 保存图片
        pix.save(str(output_path))
        
        doc.close()
        
        logger.info(f"页面导出成功: {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"页面导出失败: {pdf_path}, 第 {page_num} 页, 错误: {str(e)}")
        return None


def extract_text_from_page(pdf_path: Path, page_num: int) -> str:
    """
    从 PDF 页面提取文本
    
    Args:
        pdf_path: PDF 文件路径
        page_num: 页码 (从 0 开始)
        
    Returns:
        文本内容
    """
    try:
        import fitz
        
        doc = fitz.open(str(pdf_path))
        
        if page_num < 0 or page_num >= len(doc):
            logger.error(f"页码超出范围: {page_num} (总共 {len(doc)} 页)")
            doc.close()
            return ""
        
        page = doc[page_num]
        text = page.get_text()
        
        doc.close()
        
        return text
        
    except Exception as e:
        logger.error(f"文本提取失败: {pdf_path}, 第 {page_num} 页, 错误: {str(e)}")
        return ""


def calculate_bbox_area(bbox: Tuple[float, float, float, float]) -> float:
    """
    计算边界框面积
    
    Args:
        bbox: 边界框 (x0, y0, x1, y1)
        
    Returns:
        面积
    """
    x0, y0, x1, y1 = bbox
    return (x1 - x0) * (y1 - y0)


def merge_bboxes(
    bbox1: Tuple[float, float, float, float],
    bbox2: Tuple[float, float, float, float]
) -> Tuple[float, float, float, float]:
    """
    合并两个边界框
    
    Args:
        bbox1: 边界框 1
        bbox2: 边界框 2
        
    Returns:
        合并后的边界框
    """
    x0_1, y0_1, x1_1, y1_1 = bbox1
    x0_2, y0_2, x1_2, y1_2 = bbox2
    
    return (
        min(x0_1, x0_2),
        min(y0_1, y0_2),
        max(x1_1, x1_2),
        max(y1_1, y1_2)
    )
