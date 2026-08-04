"""
视觉处理器

使用视觉模型处理图片、表格等视觉元素。
"""

import logging
import hashlib
import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class VisionProcessor:
    """
    视觉处理器
    
    使用视觉模型（如Qwen Vision）处理图片、表格等视觉元素，
    生成文本描述和结构化数据。
    """
    
    # 缓存配置
    CACHE_DIR = Path("data/cache/vision_descriptions")
    CACHE_EXPIRY_DAYS = 30
    
    def __init__(self, vision_provider=None):
        """
        初始化视觉处理器
        
        Args:
            vision_provider: 视觉模型提供商（可选）
        """
        self.vision_provider = vision_provider
        self._cache = {}
        
        # 确保缓存目录存在
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        
        logger.info("VisionProcessor 初始化完成")
    
    def process_image(
        self,
        image_path: Path,
        prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        处理单张图片
        
        Args:
            image_path: 图片路径
            prompt: 自定义提示词
            
        Returns:
            处理结果，包含描述和元数据
        """
        # 检查缓存
        cached = self._get_from_cache(image_path)
        if cached:
            logger.info(f"[Vision] 缓存命中: {image_path.name}")
            return cached
        
        # 生成描述
        try:
            description = self._generate_description(image_path, prompt)
            
            result = {
                'description': description,
                'image_path': str(image_path),
                'image_name': image_path.name,
                'timestamp': datetime.now().isoformat(),
                'model_used': 'qwen-vision',
            }
            
            # 保存到缓存
            self._save_to_cache(image_path, result)
            
            logger.info(f"[Vision] 图片处理完成: {image_path.name}, 描述长度: {len(description)}")
            return result
            
        except Exception as e:
            logger.error(f"[Vision] 图片处理失败: {image_path.name}, 错误: {str(e)}")
            return {
                'description': f"图片描述生成失败: {str(e)}",
                'image_path': str(image_path),
                'image_name': image_path.name,
                'error': str(e),
            }
    
    def process_element(
        self,
        element: PDFElement,
        pdf_path: Path
    ) -> Dict[str, Any]:
        """
        处理 PDF 元素
        
        Args:
            element: PDF 元素
            pdf_path: PDF 文件路径
            
        Returns:
            处理结果
        """
        if element.type == ElementType.IMAGE:
            # 处理图片元素
            return self._process_image_element(element, pdf_path)
        elif element.type == ElementType.TABLE:
            # 处理表格元素
            return self._process_table_element(element, pdf_path)
        elif element.type == ElementType.FORMULA:
            # 处理公式元素
            return self._process_formula_element(element)
        else:
            # 文本元素不需要视觉处理
            return {
                'type': element.type.value,
                'content': element.content,
                'processed': False,
            }
    
    def _process_image_element(
        self,
        element: PDFElement,
        pdf_path: Path
    ) -> Dict[str, Any]:
        """处理图片元素"""
        # 这里需要先提取图片，然后调用视觉模型
        # 暂时返回占位符
        return {
            'type': 'image',
            'description': '图片描述待生成',
            'page': element.page,
            'bbox': element.bbox,
            'processed': True,
        }
    
    def _process_table_element(
        self,
        element: PDFElement,
        pdf_path: Path
    ) -> Dict[str, Any]:
        """处理表格元素"""
        # 表格通常不需要视觉处理，直接使用提取的数据
        table_data = element.content.get('data', [])
        
        return {
            'type': 'table',
            'data': table_data,
            'row_count': element.content.get('rows', 0),
            'col_count': element.content.get('cols', 0),
            'page': element.page,
            'bbox': element.bbox,
            'processed': True,
        }
    
    def _process_formula_element(
        self,
        element: PDFElement
    ) -> Dict[str, Any]:
        """处理公式元素"""
        # 公式可以使用视觉模型来识别
        # 暂时返回原始文本
        return {
            'type': 'formula',
            'content': element.content,
            'latex': element.metadata.get('latex', ''),
            'page': element.page,
            'bbox': element.bbox,
            'processed': True,
        }
    
    def _generate_description(
        self,
        image_path: Path,
        prompt: Optional[str] = None
    ) -> str:
        """
        生成图片描述
        
        Args:
            image_path: 图片路径
            prompt: 自定义提示词
            
        Returns:
            图片描述
        """
        if self.vision_provider is None:
            # 如果没有视觉模型，使用默认描述
            return f"图片: {image_path.name}"
        
        try:
            # 调用视觉模型
            if prompt is None:
                prompt = "请详细描述这张图片的内容，包括主要元素、颜色、布局等。"
            
            # 这里需要调用实际的视觉模型
            # 暂时返回占位符
            description = f"图片 {image_path.name} 的视觉描述"
            
            return description
            
        except Exception as e:
            logger.error(f"生成图片描述失败: {str(e)}")
            return f"图片描述生成失败: {str(e)}"
    
    def _get_cache_key(self, image_path: Path) -> str:
        """生成缓存键"""
        # 使用文件路径和修改时间生成缓存键
        stat = image_path.stat()
        key_data = f"{image_path}:{stat.st_mtime}:{stat.st_size}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _get_from_cache(self, image_path: Path) -> Optional[Dict[str, Any]]:
        """从缓存获取"""
        try:
            cache_key = self._get_cache_key(image_path)
            cache_file = self.CACHE_DIR / f"{cache_key}.json"
            
            if cache_file.exists():
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                
                # 检查是否过期
                cached_time = datetime.fromisoformat(cached['timestamp'])
                if datetime.now() - cached_time < timedelta(days=self.CACHE_EXPIRY_DAYS):
                    return cached
                else:
                    # 删除过期缓存
                    cache_file.unlink()
            
            return None
            
        except Exception as e:
            logger.warning(f"读取缓存失败: {str(e)}")
            return None
    
    def _save_to_cache(self, image_path: Path, result: Dict[str, Any]):
        """保存到缓存"""
        try:
            cache_key = self._get_cache_key(image_path)
            cache_file = self.CACHE_DIR / f"{cache_key}.json"
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"缓存保存成功: {cache_file}")
            
        except Exception as e:
            logger.warning(f"保存缓存失败: {str(e)}")
