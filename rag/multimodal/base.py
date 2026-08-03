"""
多模态处理基础模块
定义多模态内容处理的基类和接口
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from pathlib import Path

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


class BaseMultimodalProcessor(ABC):
    """
    多模态处理器基类
    所有多模态处理器都应继承此类
    """
    
    def __init__(self):
        """初始化处理器"""
        pass
    
    @abstractmethod
    def can_process(self, file_path: Path) -> bool:
        """
        检查是否可以处理该文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            是否可以处理
        """
        pass
    
    @abstractmethod
    def process(self, file_path: Path, **kwargs) -> List[Document]:
        """
        处理文件并返回Document列表
        
        Args:
            file_path: 文件路径
            **kwargs: 额外参数
            
        Returns:
            Document对象列表
        """
        pass
    
    def get_supported_extensions(self) -> List[str]:
        """
        获取支持的文件扩展名列表
        
        Returns:
            支持的扩展名列表
        """
        return []


class MultimodalDocument:
    """
    多模态文档数据类
    用于存储多模态处理的中间结果
    """
    
    def __init__(
        self,
        content: str,
        modality: str,
        file_type: str,
        original_path: str,
        ocr_text: str = "",
        description: str = "",
        model_used: str = "",
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        初始化多模态文档
        
        Args:
            content: 主要文本内容（描述或OCR文本）
            modality: 模态类型（image/audio/video）
            file_type: 文件类型（jpg/png/mp3等）
            original_path: 原始文件路径
            ocr_text: OCR提取的文本
            description: 内容描述
            model_used: 使用的模型名称（动态获取）
            metadata: 额外元数据
        """
        self.content = content
        self.modality = modality
        self.file_type = file_type
        self.original_path = original_path
        self.ocr_text = ocr_text
        self.description = description
        self.model_used = model_used
        self.metadata = metadata or {}
    
    def to_document(self) -> Document:
        """
        转换为LangChain Document对象
        
        Returns:
            Document对象
        """
        # 构建page_content（符合新要求）
        page_content_parts = []
        if self.description:
            page_content_parts.append(f"图片描述：{self.description}")
        if self.ocr_text:
            page_content_parts.append(f"OCR文字：{self.ocr_text}")
        if self.content:
            page_content_parts.append(f"视觉内容总结：{self.content}")
        
        page_content = "\n\n".join(page_content_parts) if page_content_parts else "图片内容（无法提取文本）"
        
        # 构建metadata（符合新要求）
        filename = Path(self.original_path).name if self.original_path else ""
        metadata = {
            "modality": self.modality,  # image
            "source": self.original_path,
            "filename": filename,
            "ocr_result": self.ocr_text,
            "caption": self.description,
            "model_used": self.model_used,  # 动态获取，不硬编码
            "file_type": self.file_type,
        }
        metadata.update(self.metadata)
        
        return Document(page_content=page_content, metadata=metadata)
