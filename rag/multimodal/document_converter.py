"""
多模态文档转换器
负责将多模态内容转换为LangChain Document对象
"""

import json
import hashlib
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# 图片描述缓存目录
IMAGE_DESCRIPTION_CACHE_DIR = Path("data/cache/image_descriptions")

# 尝试导入LangChain Document
try:
    from langchain_core.documents import Document
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    # 创建一个简单的Document替代类
    class Document:
        """LangChain Document的简单替代类"""
        def __init__(self, page_content: str, metadata: Optional[dict] = None):
            self.page_content = page_content
            self.metadata = metadata or {}
        
        def __repr__(self):
            return f"Document(page_content='{self.page_content[:50]}...', metadata={self.metadata})"

from .base import MultimodalDocument
from .image.loader import ImageLoader
from .image.extractor import ImageExtractor


class MultimodalDocumentConverter:
    """
    多模态文档转换器
    负责将多模态文件转换为LangChain Document对象
    """
    
    def __init__(self, vision_model_service=None, multimodal_service=None):
        """
        初始化转换器
        
        Args:
            vision_model_service: 视觉模型服务实例（已弃用，请使用multimodal_service）
            multimodal_service: 多模态服务实例
        """
        self.image_loader = ImageLoader()
        self.image_extractor = ImageExtractor(vision_model_service, multimodal_service)
        
        if multimodal_service:
            self.multimodal_service = multimodal_service
        else:
            try:
                from services.multimodal_service import MultimodalService
                self.multimodal_service = MultimodalService()
                logger.info("自动初始化MultimodalService")
            except Exception as e:
                logger.warning(f"无法自动初始化MultimodalService: {e}")
                self.multimodal_service = None
    
    def can_convert(self, file_path: Path) -> bool:
        """
        检查是否可以转换该文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            是否可以转换
        """
        return self.image_loader.can_load(file_path)
    
    def _compute_file_hash(self, file_path: Path) -> str:
        """计算文件的MD5 hash"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def _load_from_cache(self, file_hash: str, file_path: Path) -> Optional[Document]:
        """从缓存加载图片描述"""
        try:
            cache_file = IMAGE_DESCRIPTION_CACHE_DIR / f"{file_hash}.json"
            if not cache_file.exists():
                return None
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 验证缓存数据完整性
            if not cache_data.get('page_content') or not cache_data.get('metadata'):
                logger.warning(f"缓存数据不完整: {cache_file}")
                return None
            
            # 检查缓存是否过期（可选，例如30天）
            created_at = cache_data.get('created_at', '')
            if created_at:
                try:
                    cache_date = datetime.fromisoformat(created_at)
                    if (datetime.now() - cache_date).days > 30:
                        logger.info(f"缓存已过期: {cache_file}")
                        return None
                except ValueError:
                    pass
            
            logger.info(f"图片描述缓存命中: {file_path.name}")
            return Document(
                page_content=cache_data['page_content'],
                metadata=cache_data['metadata']
            )
        except Exception as e:
            logger.warning(f"读取缓存失败: {e}")
            return None
    
    def _save_to_cache(self, file_hash: str, file_path: Path, doc: Document) -> None:
        """保存图片描述到缓存"""
        try:
            # 确保缓存目录存在
            IMAGE_DESCRIPTION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            
            cache_file = IMAGE_DESCRIPTION_CACHE_DIR / f"{file_hash}.json"
            cache_data = {
                'file_hash': file_hash,
                'filename': file_path.name,
                'model': doc.metadata.get('model_used', ''),
                'page_content': doc.page_content,
                'metadata': doc.metadata,
                'created_at': datetime.now().isoformat()
            }
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"图片描述已缓存: {file_path.name}")
        except Exception as e:
            logger.warning(f"保存缓存失败: {e}")
            # 缓存失败不影响正常流程
    
    def convert_image(self, file_path: Path) -> List[Document]:
        """
        将图片文件转换为Document对象
        
        Args:
            file_path: 图片文件路径
            
        Returns:
            Document对象列表
            
        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 不支持的文件格式
            Exception: 转换失败
        """
        file_path = Path(file_path)
        
        # 优先使用MultimodalService
        if self.multimodal_service:
            try:
                # 计算文件hash并检查缓存
                file_hash = self._compute_file_hash(file_path)
                cached_doc = self._load_from_cache(file_hash, file_path)
                
                if cached_doc:
                    return [cached_doc]
                
                # 缓存未命中，调用MultimodalService处理图片
                doc_data = self.multimodal_service.process_image_to_document(str(file_path))
                
                # 创建Document对象
                doc = Document(
                    page_content=doc_data['page_content'],
                    metadata=doc_data['metadata']
                )
                
                # 保存到缓存
                self._save_to_cache(file_hash, file_path, doc)
                
                return [doc]
                
            except Exception as e:
                logger.warning(f"MultimodalService处理图片失败，回退到传统方法: {e}")
                # 回退到传统方法
        
        # 传统方法：使用ImageExtractor
        # 加载图片
        image_info = self.image_loader.load(file_path)
        
        # 提取信息（OCR + 描述）
        extraction_result = self.image_extractor.extract_all(
            image_path=str(file_path),
            base64_data=image_info['base64'],
            mime_type=image_info['mime_type']
        )
        
        # 获取使用的模型信息（动态获取）
        model_used = ""
        if self.multimodal_service and self.multimodal_service.vision_provider:
            model_used = self.multimodal_service.vision_provider.model_name
        elif self.image_extractor.multimodal_service and self.image_extractor.multimodal_service.vision_provider:
            model_used = self.image_extractor.multimodal_service.vision_provider.model_name
        
        # 构建多模态文档
        multimodal_doc = MultimodalDocument(
            content=extraction_result['combined_text'],
            modality='image',
            file_type=image_info['format'],
            original_path=str(file_path),
            ocr_text=extraction_result['ocr_text'],
            description=extraction_result['description'],
            model_used=model_used,
            metadata={
                'file_name': image_info['file_name'],
                'file_size': image_info['file_size'],
                'width': image_info['width'],
                'height': image_info['height']
            }
        )
        
        # 转换为Document
        doc = multimodal_doc.to_document()
        
        return [doc]
    
    def convert(self, file_path: Path) -> List[Document]:
        """
        将文件转换为Document对象
        
        Args:
            file_path: 文件路径
            
        Returns:
            Document对象列表
        """
        file_path = Path(file_path)
        
        # 检查是否为图片文件
        if self.image_loader.can_load(file_path):
            return self.convert_image(file_path)
        
        raise ValueError(f"不支持的文件格式: {file_path.suffix}")
    
    def get_supported_extensions(self) -> List[str]:
        """
        获取支持的文件扩展名列表
        
        Returns:
            支持的扩展名列表
        """
        return self.image_loader.get_supported_extensions()
