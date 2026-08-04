"""
ImageLoader - 图片文件加载器

负责加载图片文件（.jpg, .jpeg, .png, .webp）。
保持现有流程：图片 -> MultimodalService -> Qwen-VL -> Document
"""

from pathlib import Path
from typing import List, Union
import logging

from .base import BaseLoader, LoadResult

logger = logging.getLogger(__name__)

# 检查多模态模块是否可用
try:
    from rag.multimodal.base import MultimodalDocument
    from rag.multimodal.document_converter import MultimodalDocumentConverter
    MULTIMODAL_AVAILABLE = True
except ImportError:
    MULTIMODAL_AVAILABLE = False


class ImageLoader(BaseLoader):
    """
    图片文件加载器
    
    支持加载图片文件，使用现有的 MultimodalService 流程。
    
    注意：保持现有行为，不破坏：
    - 图片缓存
    - OCR fallback
    - metadata 格式
    """
    
    def supports(self, file_path: Union[str, Path]) -> bool:
        """判断是否支持该文件"""
        file_path = Path(file_path)
        return file_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp']
    
    def load(self, file_path: Union[str, Path]) -> LoadResult:
        """
        加载图片文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            LoadResult 包含加载的文档列表
        """
        try:
            file_path = self._validate_file(file_path)
            
            # 检查多模态模块是否可用
            if not MULTIMODAL_AVAILABLE:
                return self._create_error_result("图片加载需要多模态模块（rag.multimodal）")
            
            # 使用现有的 MultimodalDocumentConverter
            try:
                # 尝试使用MultimodalService（推荐）
                try:
                    from services.multimodal_service import get_multimodal_service
                    multimodal_service = get_multimodal_service()
                    converter = MultimodalDocumentConverter(multimodal_service=multimodal_service)
                except Exception as e:
                    logger.warning(f"无法初始化MultimodalService，使用传统方法: {e}")
                    converter = MultimodalDocumentConverter()
                
                # 转换图片
                documents = converter.convert_image(file_path)
                return self._create_success_result(documents)
                
            except Exception as e:
                return self._create_error_result(f"读取图片文件失败: {str(e)}")
            
        except Exception as e:
            return self._create_error_result(str(e))
    
    def get_supported_extensions(self) -> List[str]:
        """获取支持的文件扩展名"""
        return ['.jpg', '.jpeg', '.png', '.webp']
