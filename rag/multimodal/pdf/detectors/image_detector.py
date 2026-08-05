"""
图片检测器

检测 PDF 中的图片元素。
"""

import logging
from typing import List

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class ImageDetector:
    """
    图片检测器
    
    检测 PDF 中的图片元素，返回图片位置和元数据。
    """
    
    def detect(self, page, page_num: int) -> List[PDFElement]:
        """
        检测页面中的图片
        
        Args:
            page: PyMuPDF 页面对象
            page_num: 页码
            
        Returns:
            图片元素列表
        """
        elements = []
        
        try:
            # 获取图片列表
            image_list = page.get_images()
            
            for img_index, img in enumerate(image_list):
                xref = img[0]
                
                try:
                    # 获取图片边界框
                    bbox = self._get_image_bbox(page, xref)
                    
                    # 获取图片信息
                    image_info_list = page.get_image_info(xref)
                    
                    # get_image_info返回列表，取第一个元素
                    if isinstance(image_info_list, list) and len(image_info_list) > 0:
                        image_info = image_info_list[0]
                    else:
                        image_info = image_info_list
                    
                    element = PDFElement(
                        type=ElementType.IMAGE,
                        content={
                            'xref': xref,
                            'index': img_index,
                            'width': image_info.get('width', 0),
                            'height': image_info.get('height', 0),
                            'colorspace': image_info.get('colorspace', 0),
                            'bpc': image_info.get('bpc', 0),  # bits per component
                        },
                        page=page_num,
                        bbox=bbox,
                        confidence=1.0,
                        metadata={
                            'image_type': image_info.get('ext', ''),
                            'size': image_info.get('width', 0) * image_info.get('height', 0),
                        }
                    )
                    elements.append(element)
                    
                except Exception as e:
                    logger.warning(f"获取图片信息失败: xref={xref}, 错误: {str(e)}")
                    continue
            
            logger.debug(f"第 {page_num} 页检测到 {len(elements)} 个图片元素")
            return elements
            
        except Exception as e:
            logger.error(f"图片检测失败: 第 {page_num} 页, 错误: {str(e)}")
            return []
    
    def _get_image_bbox(self, page, xref: int) -> tuple:
        """
        获取图片的边界框
        
        Args:
            page: PyMuPDF 页面对象
            xref: 图片的 xref
            
        Returns:
            边界框 (x0, y0, x1, y1)
        """
        try:
            # 获取图片在页面中的位置
            # 注意：PyMuPDF 没有直接提供图片位置的方法
            # 这里使用页面的完整尺寸作为默认值
            rect = page.rect
            return (rect.x0, rect.y0, rect.x1, rect.y1)
        except Exception:
            # 如果获取失败，返回页面中心区域
            rect = page.rect
            center_x = (rect.x0 + rect.x1) / 2
            center_y = (rect.y0 + rect.y1) / 2
            return (center_x - 50, center_y - 50, center_x + 50, center_y + 50)
