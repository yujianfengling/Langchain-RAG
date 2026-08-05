"""
图片加载器
负责加载图片文件，进行预处理，并转换为可处理的格式
"""

import os
import base64
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)

# 尝试导入PIL
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# 支持的图片格式
SUPPORTED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}

# 图片MIME类型映射
IMAGE_MIME_TYPES = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp'
}


class ImageLoader:
    """
    图片加载器
    负责加载和预处理图片文件
    """
    
    def __init__(self, max_size: Tuple[int, int] = (2048, 2048)):
        """
        初始化图片加载器
        
        Args:
            max_size: 图片最大尺寸（宽, 高），超过此尺寸将进行缩放
        """
        self.max_size = max_size
    
    def can_load(self, file_path: Path) -> bool:
        """
        检查是否可以加载该文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            是否可以加载
        """
        if not file_path.exists():
            return False
        
        file_ext = file_path.suffix.lower()
        return file_ext in SUPPORTED_IMAGE_EXTENSIONS
    
    def load(self, file_path: Path) -> Dict[str, Any]:
        """
        加载图片文件
        
        Args:
            file_path: 图片文件路径
            
        Returns:
            包含图片信息的字典：
            - base64: base64编码的图片数据
            - mime_type: MIME类型
            - width: 图片宽度
            - height: 图片高度
            - file_size: 文件大小（字节）
            - format: 图片格式
            
        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 不支持的图片格式
            Exception: 加载失败
        """
        file_path = Path(file_path)
        
        # 检查文件是否存在
        if not file_path.exists():
            raise FileNotFoundError(f"图片文件不存在: {file_path}")
        
        # 检查文件格式
        file_ext = file_path.suffix.lower()
        if file_ext not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ValueError(f"不支持的图片格式: {file_ext}")
        
        # 获取文件大小
        file_size = file_path.stat().st_size
        
        # 读取文件并转换为base64
        with open(file_path, 'rb') as f:
            image_data = f.read()
        
        base64_data = base64.b64encode(image_data).decode('utf-8')
        mime_type = IMAGE_MIME_TYPES.get(file_ext, 'image/jpeg')
        
        # 获取图片尺寸（如果PIL可用）
        width, height = self._get_image_size(file_path)
        
        # 如果图片过大，进行缩放
        if PIL_AVAILABLE and (width > self.max_size[0] or height > self.max_size[1]):
            base64_data, width, height = self._resize_image(file_path)
        
        return {
            'base64': base64_data,
            'mime_type': mime_type,
            'width': width,
            'height': height,
            'file_size': file_size,
            'format': file_ext.lstrip('.'),
            'file_name': file_path.name,
            'file_path': str(file_path)
        }
    
    def _get_image_size(self, file_path: Path) -> Tuple[int, int]:
        """
        获取图片尺寸
        
        Args:
            file_path: 图片文件路径
            
        Returns:
            (宽度, 高度) 元组
        """
        if not PIL_AVAILABLE:
            return (0, 0)
        
        try:
            with Image.open(file_path) as img:
                return img.size
        except Exception:
            return (0, 0)
    
    def _resize_image(self, file_path: Path) -> Tuple[str, int, int]:
        """
        缩放图片
        
        Args:
            file_path: 图片文件路径
            
        Returns:
            (base64数据, 新宽度, 新高度) 元组
        """
        if not PIL_AVAILABLE:
            # 如果没有PIL，直接返回原始数据
            with open(file_path, 'rb') as f:
                image_data = f.read()
            base64_data = base64.b64encode(image_data).decode('utf-8')
            return (base64_data, 0, 0)
        
        try:
            with Image.open(file_path) as img:
                # 计算缩放比例
                ratio = min(self.max_size[0] / img.width, self.max_size[1] / img.height)
                if ratio < 1:
                    new_size = (int(img.width * ratio), int(img.height * ratio))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                
                # 转换为base64
                import io
                buffer = io.BytesIO()
                img_format = img.format or 'JPEG'
                if img_format == 'JPEG':
                    img.save(buffer, format=img_format, quality=85)
                else:
                    img.save(buffer, format=img_format)
                
                base64_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
                return (base64_data, img.width, img.height)
        except Exception as e:
            # 如果缩放失败，返回原始数据
            logger.warning(f"图片缩放失败，使用原始图片: {e}")
            with open(file_path, 'rb') as f:
                image_data = f.read()
            base64_data = base64.b64encode(image_data).decode('utf-8')
            return (base64_data, 0, 0)
    
    def get_supported_extensions(self) -> list:
        """
        获取支持的图片扩展名列表
        
        Returns:
            支持的扩展名列表
        """
        return list(SUPPORTED_IMAGE_EXTENSIONS)
