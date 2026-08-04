"""
PdfLoader - PDF 文件加载器

负责加载 .pdf 文件。
Phase 0: 保持 V2.2.1 行为，只做文本提取。
"""

from pathlib import Path
from typing import List, Union
import logging

from .base import BaseLoader, LoadResult

logger = logging.getLogger(__name__)


class PdfLoader(BaseLoader):
    """
    PDF 文件加载器
    
    支持加载 .pdf 文件，使用 LangChain PyPDFLoader 或 pdfplumber。
    
    注意：Phase 0 只保持 V2.2.1 行为，不实现 PDFElement、公式检测等。
    """
    
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
            LoadResult 包含加载的文档列表（每页一个 Document）
        """
        try:
            file_path = self._validate_file(file_path)
            
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
