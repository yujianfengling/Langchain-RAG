"""
DocxLoader - DOCX 文件加载器

负责加载 .docx 文件。
"""

from pathlib import Path
from typing import List, Union
import logging

from .base import BaseLoader, LoadResult

logger = logging.getLogger(__name__)


class DocxLoader(BaseLoader):
    """
    DOCX 文件加载器
    
    支持加载 .docx 文件，使用 LangChain Docx2txtLoader 或 docx2txt。
    保持与 V2.2.1 相同的行为。
    """
    
    def supports(self, file_path: Union[str, Path]) -> bool:
        """判断是否支持该文件"""
        file_path = Path(file_path)
        # 2026-10-07：.doc 是二进制旧格式，docx2txt 无法真实解析；
        # 旧格式统一由 LegacyOfficeLoader（LibreOffice 转换）处理。
        return file_path.suffix.lower() in ['.docx']
    
    def load(self, file_path: Union[str, Path]) -> LoadResult:
        """
        加载 DOCX 文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            LoadResult 包含加载的文档列表
        """
        try:
            file_path = self._validate_file(file_path)
            
            # 检查 LangChain Docx2txtLoader 是否可用
            try:
                from langchain_community.document_loaders import Docx2txtLoader
                langchain_available = True
            except ImportError:
                langchain_available = False
            
            # 尝试使用 LangChain Docx2txtLoader
            if langchain_available:
                try:
                    loader = Docx2txtLoader(str(file_path))
                    documents = loader.load()
                    return self._create_success_result(documents)
                except Exception as e:
                    logger.warning(f"LangChain Docx2txtLoader failed: {e}, using fallback")
            
            # 降级方案：使用 docx2txt
            try:
                import docx2txt
                
                content = docx2txt.process(str(file_path))
                if not content:
                    return self._create_error_result("DOCX文件无法提取文本内容")
                
                # 创建 Document 对象
                from langchain_core.documents import Document
                metadata = {
                    # v2.6：source 只存文件名。此前存完整绝对路径，
                    # 会被 build_context 拼进 LLM 提示词（"来源: D:\...\xxx.docx"），
                    # 既泄露本地路径也浪费 token。
                    'source': file_path.name,
                    'source_path': str(file_path),
                    'file_name': file_path.name,
                    'file_type': 'docx'
                }
                doc = Document(page_content=content, metadata=metadata)
                return self._create_success_result([doc])
                
            except ImportError:
                return self._create_error_result(
                    "DOCX加载需要安装langchain-community或docx2txt包",
                    error_type="unsupported_format")
            except Exception as e:
                return self._create_error_result(f"读取DOCX文件失败: {str(e)}",
                                                 error_type="parse_error")
            
        except Exception as e:
            return self._create_error_result(str(e), error_type="io_error")
    
    def get_supported_extensions(self) -> List[str]:
        """获取支持的文件扩展名"""
        return ['.docx']
