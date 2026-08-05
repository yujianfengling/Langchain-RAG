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

# Vision缓存版本标识
# 当Prompt、模型或处理逻辑变更时，更新此版本号以使旧缓存失效
VISION_PROMPT_VERSION = "v1.0"

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
    
    def _compute_cache_key(self, file_path: Path) -> str:
        """
        计算复合缓存键
        
        缓存键 = hash(file_content + provider + model + prompt_version)
        
        这确保：
        1. 不同图片文件有不同的缓存
        2. 不同Provider/模型有不同的缓存
        3. Prompt版本变化时缓存失效
        """
        file_hash = self._compute_file_hash(file_path)
        
        # 获取当前Provider和模型信息
        provider_name = "unknown"
        model_name = "unknown"
        if self.multimodal_service and self.multimodal_service.vision_provider:
            provider_name = self.multimodal_service.vision_provider.__class__.__name__
            model_name = getattr(self.multimodal_service.vision_provider, 'model_name', 'unknown')
        
        # 组合所有因素生成最终缓存键
        composite_key = f"{file_hash}_{provider_name}_{model_name}_{VISION_PROMPT_VERSION}"
        return hashlib.md5(composite_key.encode()).hexdigest()
    
    def _load_from_cache(self, cache_key: str, file_path: Path) -> Optional[Document]:
        """
        从缓存加载图片描述
        
        Args:
            cache_key: 复合缓存键
            file_path: 图片文件路径（用于日志）
        
        Returns:
            缓存的Document对象，或None（缓存未命中）
        """
        try:
            cache_file = IMAGE_DESCRIPTION_CACHE_DIR / f"{cache_key}.json"
            if not cache_file.exists():
                return None
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 验证缓存数据完整性
            if not cache_data.get('page_content') or not cache_data.get('metadata'):
                logger.warning(f"[Multimodal] 缓存数据不完整: {cache_file}")
                return None
            
            # 检查缓存版本是否匹配
            cached_version = cache_data.get('metadata', {}).get('cache_prompt_version')
            if cached_version != VISION_PROMPT_VERSION:
                logger.info(
                    f"[Multimodal] 缓存版本不匹配: cached={cached_version}, "
                    f"current={VISION_PROMPT_VERSION}, file={file_path.name}"
                )
                return None
            
            # 检查缓存是否过期（30天）
            created_at = cache_data.get('created_at', '')
            if created_at:
                try:
                    cache_date = datetime.fromisoformat(created_at)
                    if (datetime.now() - cache_date).days > 30:
                        logger.info(f"[Multimodal] 缓存已过期: {file_path.name}")
                        return None
                except ValueError:
                    pass
            
            # 标记为缓存命中
            metadata = cache_data['metadata']
            metadata['vision_status'] = 'cached'
            
            logger.info(
                f"[Multimodal] Vision缓存命中, image={file_path.name}, "
                f"model={metadata.get('model_used', 'unknown')}"
            )
            return Document(
                page_content=cache_data['page_content'],
                metadata=metadata
            )
        except Exception as e:
            logger.warning(f"[Multimodal] 读取缓存失败: {e}")
            return None
    
    def _save_to_cache(self, cache_key: str, file_path: Path, doc: Document) -> None:
        """
        保存图片描述到缓存
        
        Args:
            cache_key: 复合缓存键
            file_path: 图片文件路径
            doc: 要缓存的Document对象
        """
        try:
            # 仅缓存成功的结果
            vision_status = doc.metadata.get('vision_status', 'unknown')
            if vision_status not in ('success',):
                logger.debug(
                    f"[Multimodal] 跳过缓存: status={vision_status}, "
                    f"image={file_path.name}"
                )
                return
            
            # 确保缓存目录存在
            IMAGE_DESCRIPTION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            
            # 在metadata中添加缓存版本标识
            metadata_copy = dict(doc.metadata)
            metadata_copy['cache_prompt_version'] = VISION_PROMPT_VERSION
            
            cache_file = IMAGE_DESCRIPTION_CACHE_DIR / f"{cache_key}.json"
            cache_data = {
                'cache_key': cache_key,
                'filename': file_path.name,
                'model': doc.metadata.get('model_used', ''),
                'page_content': doc.page_content,
                'metadata': metadata_copy,
                'created_at': datetime.now().isoformat()
            }
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            
            logger.info(
                f"[Multimodal] Vision结果已缓存, image={file_path.name}, "
                f"model={metadata_copy.get('model_used', 'unknown')}"
            )
        except Exception as e:
            logger.warning(f"[Multimodal] 保存缓存失败: {e}")
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
                # 计算复合缓存键（包含文件hash + provider + model + prompt_version）
                cache_key = self._compute_cache_key(file_path)
                cached_doc = self._load_from_cache(cache_key, file_path)
                
                if cached_doc:
                    return [cached_doc]
                
                # 缓存未命中，调用MultimodalService处理图片
                doc_data = self.multimodal_service.process_image_to_document(str(file_path))
                
                # 创建Document对象
                doc = Document(
                    page_content=doc_data['page_content'],
                    metadata=doc_data['metadata']
                )
                
                # 仅保存成功结果到缓存
                self._save_to_cache(cache_key, file_path, doc)
                
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
