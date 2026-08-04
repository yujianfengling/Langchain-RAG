"""
BaseLoader - 文档加载器基类

所有 Loader 必须继承此基类并实现抽象方法。
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Union, Optional, Dict, Any
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class LoadResult:
    """
    加载结果数据类
    
    用于封装 Loader 的加载结果，包含文档列表和元数据。
    """
    documents: List[Any]  # List[Document]
    success: bool
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """初始化后处理"""
        if self.metadata is None:
            self.metadata = {}


class BaseLoader(ABC):
    """
    文档加载器基类
    
    所有 Loader 必须继承此类并实现以下方法：
    - supports(): 判断是否支持该文件
    - load(): 加载文件并返回文档列表
    
    使用示例：
        loader = TextLoader()
        if loader.supports("test.txt"):
            result = loader.load("test.txt")
            if result.success:
                documents = result.documents
    """
    
    @abstractmethod
    def supports(self, file_path: Union[str, Path]) -> bool:
        """
        判断是否支持该文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            是否支持该文件格式
        """
        pass
    
    @abstractmethod
    def load(self, file_path: Union[str, Path]) -> LoadResult:
        """
        加载文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            LoadResult 包含加载的文档列表
        """
        pass
    
    def get_supported_extensions(self) -> List[str]:
        """
        获取支持的文件扩展名列表
        
        Returns:
            支持的扩展名列表，例如 ['.txt', '.md']
        """
        return []
    
    def _validate_file(self, file_path: Union[str, Path]) -> Path:
        """
        验证文件是否存在
        
        Args:
            file_path: 文件路径
            
        Returns:
            Path 对象
            
        Raises:
            FileNotFoundError: 文件不存在
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if not file_path.is_file():
            raise ValueError(f"Not a file: {file_path}")
        return file_path
    
    def _create_success_result(self, documents: List[Any], metadata: Optional[Dict] = None) -> LoadResult:
        """
        创建成功结果
        
        Args:
            documents: 文档列表
            metadata: 元数据
            
        Returns:
            LoadResult
        """
        return LoadResult(
            documents=documents,
            success=True,
            metadata=metadata or {}
        )
    
    def _create_error_result(self, error_message: str, metadata: Optional[Dict] = None) -> LoadResult:
        """
        创建错误结果
        
        Args:
            error_message: 错误信息
            metadata: 元数据
            
        Returns:
            LoadResult
        """
        return LoadResult(
            documents=[],
            success=False,
            error_message=error_message,
            metadata=metadata or {}
        )
