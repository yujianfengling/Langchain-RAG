"""
图片提取器

从 PDF 中提取图片并保存。
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class ImageExtractor:
    """
    图片提取器
    
    从 PDF 中提取图片并保存到指定目录。
    """
    
    def __init__(self, output_dir: Optional[Path] = None):
        """
        初始化图片提取器
        
        Args:
            output_dir: 输出目录 (可选)
        """
        self.output_dir = output_dir
        logger.info("ImageExtractor 初始化完成")
    
    def extract(
        self,
        pdf_path: Path,
        page_num: int,
        image_elements: List[PDFElement]
    ) -> List[Path]:
        """
        提取图片
        
        Args:
            pdf_path: PDF 文件路径
            page_num: 页码
            image_elements: 图片元素列表
            
        Returns:
            提取的图片路径列表
        """
        extracted_paths = []
        
        try:
            import fitz
            
            doc = fitz.open(str(pdf_path))
            page = doc[page_num]
            
            # 获取页面中的图片
            image_list = page.get_images()
            
            for img_element in image_elements:
                if img_element.type != ElementType.IMAGE:
                    continue
                
                try:
                    # 获取图片索引
                    img_index = img_element.content.get('index', 0)
                    
                    if img_index < len(image_list):
                        img = image_list[img_index]
                        xref = img[0]
                        
                        # 提取图片
                        img_path = self._extract_image(doc, xref, page_num, img_index)
                        if img_path:
                            extracted_paths.append(img_path)
                    
                except Exception as e:
                    logger.warning(f"提取图片失败: {str(e)}")
                    continue
            
            doc.close()
            
            logger.info(f"第 {page_num} 页提取了 {len(extracted_paths)} 张图片")
            return extracted_paths
            
        except Exception as e:
            logger.error(f"图片提取失败: {pdf_path}, 第 {page_num} 页, 错误: {str(e)}")
            return []
    
    def _extract_image(
        self,
        doc,
        xref: int,
        page_num: int,
        img_index: int
    ) -> Optional[Path]:
        """
        提取单张图片
        
        Args:
            doc: PDF 文档对象
            xref: 图片的 xref
            page_num: 页码
            img_index: 图片索引
            
        Returns:
            图片保存路径
        """
        try:
            # 获取图片数据
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            
            # 获取图片扩展名
            image_ext = base_image["ext"]
            
            # 确定输出路径
            if self.output_dir:
                output_dir = self.output_dir
            else:
                # 将doc.name转换为Path对象
                pdf_path = Path(doc.name) if isinstance(doc.name, str) else doc.name
                output_dir = pdf_path.parent / "extracted_images"
            
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成文件名
            pdf_stem = Path(doc.name).stem if isinstance(doc.name, str) else doc.name.stem
            filename = f"{pdf_stem}_page{page_num}_img{img_index}.{image_ext}"
            output_path = output_dir / filename
            
            # 保存图片
            with open(output_path, "wb") as f:
                f.write(image_bytes)
            
            logger.debug(f"图片提取成功: {output_path}")
            return output_path
            
        except Exception as e:
            logger.warning(f"提取图片失败: xref={xref}, 错误: {str(e)}")
            return None
    
    def extract_all(
        self,
        pdf_path: Path,
        image_elements: List[PDFElement]
    ) -> List[Path]:
        """
        提取所有图片
        
        Args:
            pdf_path: PDF 文件路径
            image_elements: 所有图片元素列表
            
        Returns:
            提取的图片路径列表
        """
        extracted_paths = []
        
        # 按页码分组
        page_groups = {}
        for element in image_elements:
            if element.type == ElementType.IMAGE:
                page_num = element.page
                if page_num not in page_groups:
                    page_groups[page_num] = []
                page_groups[page_num].append(element)
        
        # 按页提取
        for page_num, elements in page_groups.items():
            paths = self.extract(pdf_path, page_num, elements)
            extracted_paths.extend(paths)
        
        return extracted_paths
