"""
PdfLoader - PDF 文件加载器

负责加载 .pdf 文件。
V2.3.0: 集成新的 PDF 分析器架构，支持元素检测和提取。
"""

from pathlib import Path
from typing import List, Union, Optional
import logging

from .base import BaseLoader, LoadResult

logger = logging.getLogger(__name__)


class PdfLoader(BaseLoader):
    """
    PDF 文件加载器
    
    支持加载 .pdf 文件，使用新的 PDF 分析器架构。
    
    V2.3.0 更新：
    - 集成 PDFAnalyzer 进行元素检测
    - 使用 DocumentAdapter 转换为 Document
    - 保持向后兼容性
    """
    
    def __init__(self, use_new_architecture: bool = True):
        """
        初始化 PDF 加载器
        
        Args:
            use_new_architecture: 是否使用新架构（默认 True）
        """
        self.use_new_architecture = use_new_architecture
        self._analyzer = None
        self._adapter = None
    
    @property
    def analyzer(self):
        """获取 PDF 分析器（延迟加载）"""
        if self._analyzer is None:
            from ..multimodal.pdf import PDFAnalyzer
            self._analyzer = PDFAnalyzer()
        return self._analyzer
    
    @property
    def adapter(self):
        """获取文档适配器（延迟加载）"""
        if self._adapter is None:
            from ..multimodal.pdf.adapters import DocumentAdapter
            self._adapter = DocumentAdapter()
        return self._adapter
    
    def supports(self, file_path: Union[str, Path]) -> bool:
        """判断是否支持该文件"""
        file_path = Path(file_path)
        return file_path.suffix.lower() in ['.pdf']
    
    def load(self, file_path: Union[str, Path]) -> LoadResult:
        """
        加载 PDF 文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            LoadResult 包含加载的文档列表
        """
        try:
            file_path = self._validate_file(file_path)
            
            # 使用新架构
            if self.use_new_architecture:
                return self._load_with_new_architecture(file_path)
            else:
                return self._load_with_legacy_architecture(file_path)
            
        except Exception as e:
            return self._create_error_result(str(e))
    
    def _load_with_new_architecture(self, file_path: Path) -> LoadResult:
        """
        使用新架构加载 PDF
        
        Args:
            file_path: 文件路径
            
        Returns:
            LoadResult
        """
        try:
            # 使用 PDFAnalyzer 分析 PDF
            elements = self.analyzer.analyze(file_path)
            
            if not elements:
                logger.warning(f"PDF 分析未检测到任何元素: {file_path}")
                # 降级到旧架构
                return self._load_with_legacy_architecture(file_path)
            
            # 使用 DocumentAdapter 转换为 Document
            documents = self.adapter.adapt(elements, file_path)
            
            if not documents:
                logger.warning(f"文档转换失败: {file_path}")
                # 降级到旧架构
                return self._load_with_legacy_architecture(file_path)
            
            logger.info(f"PDF 加载成功（新架构）: {file_path}, {len(documents)} 个文档")
            return self._create_success_result(documents)
            
        except Exception as e:
            logger.warning(f"新架构加载失败，降级到旧架构: {str(e)}")
            return self._load_with_legacy_architecture(file_path)
    
    def _load_with_legacy_architecture(self, file_path: Path) -> LoadResult:
        """
        使用旧架构加载 PDF（向后兼容）
        
        Args:
            file_path: 文件路径
            
        Returns:
            LoadResult
        """
        try:
            # 检查 LangChain PyPDFLoader 是否可用
            try:
                from langchain_community.document_loaders import PyPDFLoader
                langchain_available = True
            except ImportError:
                langchain_available = False
            
            # 尝试使用 LangChain PyPDFLoader
            if langchain_available:
                try:
                    loader = PyPDFLoader(str(file_path))
                    documents = loader.load()
                    return self._create_success_result(documents)
                except Exception as e:
                    logger.warning(f"LangChain PyPDFLoader failed: {e}, using fallback")
            
            # 降级方案：使用 pdfplumber
            try:
                import pdfplumber
                
                documents = []
                with pdfplumber.open(file_path) as pdf:
                    for i, page in enumerate(pdf.pages):
                        content = page.extract_text()
                        if content:
                            # 创建 Document 对象
                            from langchain_core.documents import Document
                            metadata = {
                                'source': str(file_path),
                                'file_name': file_path.name,
                                'file_type': 'pdf',
                                'page': i + 1,
                                'total_pages': len(pdf.pages)
                            }
                            documents.append(Document(page_content=content, metadata=metadata))
                
                if not documents:
                    return self._create_error_result("PDF文件无法提取文本内容")
                
                return self._create_success_result(documents)
                
            except ImportError:
                return self._create_error_result("PDF加载需要安装langchain-community或pdfplumber包")
            except Exception as e:
                return self._create_error_result(f"读取PDF文件失败: {str(e)}")
            
        except Exception as e:
            return self._create_error_result(str(e))
    
    def get_supported_extensions(self) -> List[str]:
        """获取支持的文件扩展名"""
        return ['.pdf']
    
    def get_elements(self, file_path: Union[str, Path]) -> List:
        """
        获取 PDF 元素（新架构特有）
        
        Args:
            file_path: 文件路径
            
        Returns:
            PDFElement 列表
        """
        file_path = Path(file_path)
        return self.analyzer.analyze(file_path)
