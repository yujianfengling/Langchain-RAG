"""
图片提取器
负责从图片中提取文本（OCR）和生成图片描述
"""

import os
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# 尝试导入pytesseract（OCR）
try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False


class ImageExtractor:
    """
    图片提取器
    负责从图片中提取文本和生成描述
    """
    
    def __init__(self, vision_model_service=None, multimodal_service=None):
        """
        初始化图片提取器
        
        Args:
            vision_model_service: 视觉模型服务实例（已弃用，请使用multimodal_service）
            multimodal_service: 多模态服务实例，用于生成图片描述
        """
        self.vision_model_service = vision_model_service
        self.multimodal_service = multimodal_service
    
    def extract_text(self, image_path: str) -> str:
        """
        从图片中提取文本（OCR）
        
        Args:
            image_path: 图片文件路径
            
        Returns:
            提取的文本内容
        """
        if not TESSERACT_AVAILABLE:
            return ""
        
        try:
            img = Image.open(image_path)
            text = pytesseract.image_to_string(img, lang='chi_sim+eng')
            return text.strip()
        except Exception as e:
            logger.warning(f"OCR提取失败: {e}")
            return ""
    
    def extract_text_from_base64(self, base64_data: str, mime_type: str = "image/jpeg") -> str:
        """
        从base64编码的图片中提取文本（OCR）
        
        Args:
            base64_data: base64编码的图片数据
            mime_type: MIME类型
            
        Returns:
            提取的文本内容
        """
        if not TESSERACT_AVAILABLE:
            return ""
        
        try:
            import base64
            from io import BytesIO
            
            # 解码base64数据
            image_data = base64.b64decode(base64_data)
            img = Image.open(BytesIO(image_data))
            
            # 提取文本
            text = pytesseract.image_to_string(img, lang='chi_sim+eng')
            return text.strip()
        except Exception as e:
            logger.warning(f"OCR提取失败: {e}")
            return ""
    
    def generate_description(self, base64_data: str, mime_type: str = "image/jpeg") -> str:
        """
        生成图片描述（调用视觉模型）
        
        Args:
            base64_data: base64编码的图片数据
            mime_type: MIME类型
            
        Returns:
            图片描述文本
        """
        # 优先使用MultimodalService
        if self.multimodal_service:
            try:
                # 构建多模态消息
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": self.multimodal_service._get_default_image_prompt()
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_data}"
                                }
                            }
                        ]
                    }
                ]
                
                # 调用视觉模型
                result = self.multimodal_service.vision_provider.vision(messages)
                return result.get("answer", "")
                
            except Exception as e:
                logger.warning(f"MultimodalService生成图片描述失败: {e}")
                # 回退到vision_model_service
        
        # 使用旧的vision_model_service（向后兼容）
        if self.vision_model_service:
            try:
                # 构建多模态消息
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "请详细描述这张图片的内容，包括图片中的文字、物体、场景等信息。"
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_data}"
                                }
                            }
                        ]
                    }
                ]
                
                # 调用视觉模型
                response = self.vision_model_service.generate_response(messages)
                return response
                
            except Exception as e:
                logger.warning(f"vision_model_service生成图片描述失败: {e}")
        
        return "图片内容（无法生成描述：未配置视觉模型）"
    
    def extract_all(self, image_path: str, base64_data: str = None, mime_type: str = "image/jpeg") -> Dict[str, Any]:
        """
        提取图片的所有信息（OCR + 描述）
        
        Args:
            image_path: 图片文件路径
            base64_data: base64编码的图片数据（可选）
            mime_type: MIME类型
            
        Returns:
            包含提取结果的字典：
            - ocr_text: OCR提取的文本
            - description: 图片描述
            - combined_text: 合并的文本内容
        """
        # OCR提取
        ocr_text = self.extract_text(image_path)
        
        # 生成描述
        if base64_data:
            description = self.generate_description(base64_data, mime_type)
        else:
            description = "图片内容"
        
        # 合并文本
        combined_text = description
        if ocr_text:
            combined_text += f"\n\n图片中的文字内容：\n{ocr_text}"
        
        return {
            'ocr_text': ocr_text,
            'description': description,
            'combined_text': combined_text
        }
