"""
多模态服务模块
提供统一的多模态内容处理接口，支持图片、音频、视频等
"""

import base64
import logging
import os
from typing import Optional, Dict, Any, List
from pathlib import Path
from services.model_service import ModelProvider, get_model_provider

logger = logging.getLogger(__name__)

# 图片安全限制
MAX_IMAGE_DIMENSION = 4096  # 最大边长（像素）
MAX_IMAGE_PIXELS = 2048 * 2048  # 最大像素数（约400万像素）

class MultimodalService:
    """
    多模态服务
    负责调用ModelProvider的vision/audio/video方法，管理prompt，处理错误
    """
    
    def __init__(self, vision_provider: Optional[ModelProvider] = None):
        """
        初始化多模态服务
        
        Args:
            vision_provider: 视觉模型提供商，如果为None则自动获取
        """
        self.vision_provider = vision_provider
        self._ensure_vision_provider()
    
    def _ensure_vision_provider(self):
        """
        确保视觉模型提供商已初始化。
        环境变量 VISION_MODE 控制模式：
        - "vision"：使用视觉模型（默认）
        - "ocr"：直接使用OCR，跳过Vision调用
        """
        import os
        mode = os.getenv("VISION_MODE", "vision").lower()
        
        if mode == "ocr":
            logger.info("[Multimodal] VISION_MODE=ocr，跳过Vision模型，直接使用OCR")
            self.vision_provider = None
            return
        
        if self.vision_provider is not None:
            return
        
        # 1. 优先尝试 qwen-vision（专用视觉模型）
        try:
            from config.models import get_model_api_key
            api_key = get_model_api_key("qwen-vision")
            if api_key:
                self.vision_provider = get_model_provider("qwen-vision")
                logger.info("[Multimodal] 使用 qwen-vision 视觉模型")
                return
        except Exception:
            pass
        
        # 2. 回退到默认模型（MiMo等，使用vision_model_name如果有的话）
        try:
            from services.model_service import OpenAICompatibleProvider
            default_provider = get_model_provider()
            vision_model = default_provider.config.get("vision_model_name")
            if vision_model and vision_model != default_provider.model_name:
                vision_config = {**default_provider.config, "model_name": vision_model}
                self.vision_provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
                self.vision_provider.model_name = vision_model
                self.vision_provider.config = vision_config
                self.vision_provider.api_key = default_provider.api_key
                logger.info(f"[Multimodal] qwen-vision 未配置，使用 {vision_model} 作为视觉模型")
            else:
                self.vision_provider = default_provider
                logger.info(f"[Multimodal] qwen-vision 未配置，回退到默认模型 {self.vision_provider.model_name} 尝试视觉功能")
        except Exception as e:
            logger.warning(f"[Multimodal] 无法初始化视觉模型: {e}")
            self.vision_provider = None
    
    def describe_image(self, image_path: str, mime_type: Optional[str] = None, prompt: Optional[str] = None) -> Dict[str, Any]:
        """
        生成图片描述
        
        Args:
            image_path: 图片文件路径
            mime_type: 图片MIME类型，如果为None则自动检测
            prompt: 自定义提示词，如果为None则使用默认提示词
        
        Returns:
            包含 answer, model, latency, retries 的字典
        
        Raises:
            ValueError: 如果视觉模型不可用或调用失败
        """
        if not self.vision_provider:
            raise ValueError("视觉模型不可用，请配置通义千问视觉模型")
        
        # 读取图片并转换为base64
        try:
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        except Exception as e:
            raise ValueError(f"读取图片文件失败: {e}")
        
        # 检测MIME类型
        if mime_type is None:
            mime_type = self._detect_mime_type(image_path)
        
        # 使用默认提示词或自定义提示词
        if prompt is None:
            prompt = self._get_default_image_prompt()
        
        # 构建多模态消息
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{base64_image}"
                        }
                    }
                ]
            }
        ]
        
        # 调用视觉模型（timeout通过provider内部处理）
        result = self.vision_provider.vision(messages)
        return result
    
    def _detect_mime_type(self, image_path: str) -> str:
        """检测图片MIME类型"""
        ext = Path(image_path).suffix.lower()
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.bmp': 'image/bmp',
            '.tiff': 'image/tiff',
            '.tif': 'image/tiff',
        }
        return mime_types.get(ext, 'image/jpeg')
    
    def _get_default_image_prompt(self) -> str:
        """获取默认的图片描述提示词"""
        return (
            "请详细描述这张教育图片的内容，包括：\n"
            "1. 图片类型（图表/公式/实验图/教材插图/示意图等）\n"
            "2. 主要内容和关键信息\n"
            "3. 教育意义和应用场景\n"
            "4. 如果有文字，请提取出来\n"
            "用中文回答，保持客观准确。"
        )
    
    def extract_text_from_image(self, image_path: str) -> Dict[str, Any]:
        """
        从图片中提取文本和描述
        
        Args:
            image_path: 图片文件路径
        
        Returns:
            包含description、ocr_text、model_used、vision_status、retries等的字典
        """
        result = {
            "description": "",
            "ocr_text": "",
            "model_used": self.vision_provider.model_name if self.vision_provider else "unknown",
            "error": None,
            "vision_status": "pending",  # pending / success / failed / skipped
            "retries": 0,
            "error_type": None
        }
        
        # 检查文件是否存在
        if not os.path.exists(image_path):
            result["error"] = "图片文件不存在"
            result["vision_status"] = "skipped"
            result["error_type"] = "file_not_found"
            return result
        
        # 检查文件大小（20MB限制）
        file_size = os.path.getsize(image_path)
        if file_size > 20 * 1024 * 1024:
            result["error"] = f"图片文件过大（{file_size / 1024 / 1024:.1f}MB > 20MB）"
            result["vision_status"] = "skipped"
            result["error_type"] = "file_too_large"
            return result
        
        # 检查图片尺寸（新增安全限制）
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                width, height = img.size
                
                # 检查最大边长
                if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                    result["error"] = f"图片尺寸 {width}x{height} 超过限制（最大边长 {MAX_IMAGE_DIMENSION}px）"
                    result["vision_status"] = "skipped"
                    result["error_type"] = "dimension_exceeded"
                    return result
                
                # 检查总像素数
                if width * height > MAX_IMAGE_PIXELS:
                    result["error"] = f"图片像素数 {width * height} 超过限制（最大 {MAX_IMAGE_PIXELS} 像素）"
                    result["vision_status"] = "skipped"
                    result["error_type"] = "pixels_exceeded"
                    return result
                
                logger.info(f"[Multimodal] image={Path(image_path).name}, size={file_size/1024:.1f}KB, dimensions={width}x{height}")
        except ImportError:
            logger.warning("PIL未安装，跳过图片尺寸检查")
        except Exception as e:
            logger.warning(f"图片尺寸检查失败: {e}")
        
        # 如果Vision模型未配置，直接跳到OCR
        if not self.vision_provider:
            logger.info(f"[Multimodal] Vision模型未配置，直接使用OCR: {Path(image_path).name}")
            result["vision_status"] = "skipped"
            result["error_type"] = "no_vision_provider"
            # 直接走OCR fallback
            try:
                from rag.multimodal.image.extractor import ImageExtractor
                extractor = ImageExtractor()
                result["ocr_text"] = extractor.extract_text(image_path)
            except Exception as ocr_err:
                logger.warning(f"[Multimodal] OCR也失败了: {ocr_err}")
            return result
        
        # 尝试生成图片描述
        import time
        start_time = time.time()
        
        try:
            vision_result = self.describe_image(image_path)
            latency = time.time() - start_time
            
            result["description"] = vision_result.get("answer", "")
            result["retries"] = vision_result.get("retries", 0)
            result["vision_status"] = "success"
            
            logger.info(
                f"[Multimodal] Vision调用成功, image={Path(image_path).name}, "
                f"model={result['model_used']}, latency={latency:.2f}s, "
                f"retries={result['retries']}, caption_length={len(result['description'])}"
            )
        except Exception as e:
            latency = time.time() - start_time
            
            # 区分错误类型
            error_msg = str(e).lower()
            if "timeout" in error_msg or "超时" in error_msg:
                user_error = "图片理解服务响应超时，请稍后重试"
                result["error_type"] = "timeout"
            elif "api_key" in error_msg or "authentication" in error_msg:
                user_error = "图片理解服务认证失败，请检查配置"
                result["error_type"] = "auth_error"
            elif "quota" in error_msg or "limit" in error_msg:
                user_error = "图片理解服务配额已用尽，请稍后重试"
                result["error_type"] = "quota_exceeded"
            elif "429" in error_msg:
                user_error = "图片理解服务请求频率超限，请稍后重试"
                result["error_type"] = "rate_limit"
            elif "网络" in error_msg or "network" in error_msg or "connection" in error_msg:
                user_error = "图片理解服务网络连接失败"
                result["error_type"] = "network_error"
            else:
                user_error = "图片理解服务暂时不可用，请稍后重试"
                result["error_type"] = "unknown"
            
            result["error"] = user_error
            result["vision_status"] = "failed"
            
            logger.warning(
                f"[Multimodal] Vision调用失败, image={Path(image_path).name}, "
                f"error_type={result['error_type']}, latency={latency:.2f}s, "
                f"降级为OCR fallback"
            )
        
        # 尝试OCR提取文本（作为辅助）
        try:
            from rag.multimodal.image.extractor import ImageExtractor
            extractor = ImageExtractor()
            result["ocr_text"] = extractor.extract_text(image_path)
            
            if result["ocr_text"]:
                logger.info(f"[Multimodal] OCR_success, text_length={len(result['ocr_text'])}")
        except Exception as e:
            logger.warning(f"OCR文本提取失败: {e}")
        
        return result
    
    def process_image_to_document(self, image_path: str, source: Optional[str] = None) -> Dict[str, Any]:
        """
        处理图片并生成用于RAG的文档数据
        
        Args:
            image_path: 图片文件路径
            source: 来源标识
        
        Returns:
            包含page_content和metadata的字典
        """
        # 提取文本和描述
        extraction_result = self.extract_text_from_image(image_path)
        
        # 构建page_content
        page_content_parts = []
        if extraction_result["description"]:
            page_content_parts.append(f"图片描述：{extraction_result['description']}")
        if extraction_result["ocr_text"]:
            page_content_parts.append(f"OCR文字：{extraction_result['ocr_text']}")
        
        page_content = "\n\n".join(page_content_parts) if page_content_parts else "图片内容（无法提取文本）"
        
        # 构建metadata
        filename = Path(image_path).name
        metadata = {
            "modality": "image",
            "source": source or image_path,
            "filename": filename,
            "ocr_result": extraction_result["ocr_text"],
            "caption": extraction_result["description"],
            "model_used": extraction_result["model_used"],
            "file_type": self._detect_mime_type(image_path),
            "error": extraction_result["error"],
            "vision_status": extraction_result.get("vision_status", "unknown"),
            "vision_retries": extraction_result.get("retries", 0),
        }
        
        # 仅在失败时记录error_type
        if extraction_result.get("error_type"):
            metadata["vision_error_type"] = extraction_result["error_type"]
        
        return {
            "page_content": page_content,
            "metadata": metadata
        }


# 全局单例
_multimodal_service: Optional[MultimodalService] = None

def get_multimodal_service() -> MultimodalService:
    """获取多模态服务单例"""
    global _multimodal_service
    if _multimodal_service is None:
        _multimodal_service = MultimodalService()
    return _multimodal_service

def describe_image(image_path: str, **kwargs) -> str:
    """便捷函数：生成图片描述"""
    service = get_multimodal_service()
    return service.describe_image(image_path, **kwargs)

def process_image_to_document(image_path: str, **kwargs) -> Dict[str, Any]:
    """便捷函数：处理图片生成文档数据"""
    service = get_multimodal_service()
    return service.process_image_to_document(image_path, **kwargs)