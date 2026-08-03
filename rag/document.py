"""
文档加载模块
负责加载各种格式的文档并转换为LangChain Document对象
支持格式：txt, md, pdf, docx, jpg, jpeg, png, webp
"""

import os
from pathlib import Path
from typing import List, Optional, Union

# 尝试导入LangChain Document对象
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

# 尝试导入各种文档加载器
LOADER_AVAILABLE = {}

try:
    from langchain_community.document_loaders import TextLoader
    LOADER_AVAILABLE['txt'] = True
except ImportError:
    LOADER_AVAILABLE['txt'] = False

try:
    from langchain_community.document_loaders import UnstructuredMarkdownLoader
    LOADER_AVAILABLE['md'] = True
except ImportError:
    LOADER_AVAILABLE['md'] = False

try:
    from langchain_community.document_loaders import PyPDFLoader
    LOADER_AVAILABLE['pdf'] = True
except ImportError:
    LOADER_AVAILABLE['pdf'] = False

try:
    from langchain_community.document_loaders import Docx2txtLoader
    LOADER_AVAILABLE['docx'] = True
except ImportError:
    LOADER_AVAILABLE['docx'] = False

# 尝试导入多模态图片处理模块
try:
    from .multimodal import MultimodalDocumentConverter
    MULTIMODAL_AVAILABLE = True
except ImportError:
    MULTIMODAL_AVAILABLE = False


def load_document(file_path: Union[str, Path]) -> List[Document]:
    """
    加载单个文档文件
    
    Args:
        file_path: 文档文件路径（绝对路径或相对路径）
        
    Returns:
        Document对象列表（一个文档可能被分割成多个Document）
        
    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 不支持的文件格式
        Exception: 加载失败
    """
    file_path = Path(file_path)
    
    # 检查文件是否存在
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    
    # 获取文件扩展名
    file_ext = file_path.suffix.lower()
    
    # 根据文件类型选择加载器
    if file_ext == '.txt':
        return _load_txt(file_path)
    elif file_ext == '.md':
        return _load_markdown(file_path)
    elif file_ext == '.pdf':
        return _load_pdf(file_path)
    elif file_ext == '.docx':
        return _load_docx(file_path)
    elif file_ext in {'.jpg', '.jpeg', '.png', '.webp'}:
        return _load_image(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {file_ext}")


def load_documents(directory: Union[str, Path]) -> List[Document]:
    """
    加载目录下的所有支持格式的文档
    
    Args:
        directory: 目录路径（绝对路径或相对路径）
        
    Returns:
        所有文档的Document对象列表
        
    Raises:
        FileNotFoundError: 目录不存在
        ValueError: 目录中没有支持格式的文件
    """
    directory = Path(directory)
    
    # 检查目录是否存在
    if not directory.exists():
        raise FileNotFoundError(f"目录不存在: {directory}")
    
    # 支持的文件扩展名
    supported_extensions = {'.txt', '.md', '.pdf', '.docx', '.jpg', '.jpeg', '.png', '.webp'}
    
    # 查找所有支持的文件
    all_documents = []
    found_files = False
    
    # 遍历目录中的所有文件
    for file_path in directory.rglob('*'):
        if file_path.is_file() and file_path.suffix.lower() in supported_extensions:
            found_files = True
            try:
                documents = load_document(file_path)
                all_documents.extend(documents)
                print(f"[OK] 成功加载: {file_path.name} ({len(documents)}个文档)")
            except Exception as e:
                print(f"[FAIL] 加载失败: {file_path.name} - {str(e)}")
    
    if not found_files:
        raise ValueError(f"目录中没有找到支持格式的文件 (支持: {', '.join(supported_extensions)})")
    
    return all_documents


def _load_txt(file_path: Path) -> List[Document]:
    """
    加载TXT文件
    
    Args:
        file_path: TXT文件路径
        
    Returns:
        Document对象列表
    """
    if LOADER_AVAILABLE.get('txt'):
        # 使用LangChain的TextLoader
        loader = TextLoader(str(file_path), encoding='utf-8')
        return loader.load()
    else:
        # 降级方案：直接读取文件
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            metadata = {
                'source': str(file_path),
                'file_name': file_path.name,
                'file_type': 'txt'
            }
            
            return [Document(page_content=content, metadata=metadata)]
        except Exception as e:
            raise Exception(f"读取TXT文件失败: {str(e)}")


def _load_markdown(file_path: Path) -> List[Document]:
    """
    加载Markdown文件
    
    Args:
        file_path: Markdown文件路径
        
    Returns:
        Document对象列表
    """
    if LOADER_AVAILABLE.get('md'):
        try:
            # 使用LangChain的UnstructuredMarkdownLoader
            loader = UnstructuredMarkdownLoader(str(file_path))
            return loader.load()
        except Exception as e:
            # 降级方案：直接读取文件
            print(f"Markdown加载器失败，使用降级方案: {e}")
            return _load_txt(file_path)
    else:
        # 降级方案：直接读取文件
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            metadata = {
                'source': str(file_path),
                'file_name': file_path.name,
                'file_type': 'md'
            }
            
            return [Document(page_content=content, metadata=metadata)]
        except Exception as e:
            raise Exception(f"读取Markdown文件失败: {str(e)}")


def _load_pdf(file_path: Path) -> List[Document]:
    """
    加载PDF文件
    
    Args:
        file_path: PDF文件路径
        
    Returns:
        Document对象列表（每页一个Document）
    """
    if LOADER_AVAILABLE.get('pdf'):
        # 使用LangChain的PyPDFLoader
        loader = PyPDFLoader(str(file_path))
        return loader.load()
    else:
        # 降级方案：尝试使用pdfplumber
        try:
            import pdfplumber
            
            documents = []
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    content = page.extract_text()
                    if content:
                        metadata = {
                            'source': str(file_path),
                            'file_name': file_path.name,
                            'file_type': 'pdf',
                            'page': i + 1,
                            'total_pages': len(pdf.pages)
                        }
                        documents.append(Document(page_content=content, metadata=metadata))
            
            if not documents:
                raise Exception("PDF文件无法提取文本内容")
            
            return documents
            
        except ImportError:
            raise Exception("PDF加载需要安装langchain-community或pdfplumber包")
        except Exception as e:
            raise Exception(f"读取PDF文件失败: {str(e)}")


def _load_docx(file_path: Path) -> List[Document]:
    """
    加载DOCX文件
    
    Args:
        file_path: DOCX文件路径
        
    Returns:
        Document对象列表
    """
    if LOADER_AVAILABLE.get('docx'):
        # 使用LangChain的Docx2txtLoader
        loader = Docx2txtLoader(str(file_path))
        return loader.load()
    else:
        # 降级方案：尝试使用docx2txt
        try:
            import docx2txt
            
            content = docx2txt.process(str(file_path))
            if not content:
                raise Exception("DOCX文件无法提取文本内容")
            
            metadata = {
                'source': str(file_path),
                'file_name': file_path.name,
                'file_type': 'docx'
            }
            
            return [Document(page_content=content, metadata=metadata)]
            
        except ImportError:
            raise Exception("DOCX加载需要安装langchain-community或docx2txt包")
        except Exception as e:
            raise Exception(f"读取DOCX文件失败: {str(e)}")


def _load_image(file_path: Path) -> List[Document]:
    """
    加载图片文件
    
    Args:
        file_path: 图片文件路径
        
    Returns:
        Document对象列表
        
    Raises:
        Exception: 加载失败
    """
    if not MULTIMODAL_AVAILABLE:
        raise Exception("图片加载需要多模态模块（rag.multimodal）")
    
    try:
        # 尝试使用MultimodalService（推荐）
        try:
            from services.multimodal_service import get_multimodal_service
            multimodal_service = get_multimodal_service()
            converter = MultimodalDocumentConverter(multimodal_service=multimodal_service)
        except Exception as e:
            print(f"无法初始化MultimodalService，使用传统方法: {e}")
            converter = MultimodalDocumentConverter()
        
        return converter.convert_image(file_path)
    except Exception as e:
        raise Exception(f"读取图片文件失败: {str(e)}")


def get_supported_formats() -> List[str]:
    """
    获取支持的文件格式列表
    
    Returns:
        支持的文件扩展名列表
    """
    return ['.txt', '.md', '.pdf', '.docx', '.jpg', '.jpeg', '.png', '.webp']


def check_loader_status() -> dict:
    """
    检查文档加载器的可用状态
    
    Returns:
        各格式加载器的状态字典
    """
    status = {}
    for fmt, available in LOADER_AVAILABLE.items():
        status[fmt] = {
            'available': available,
            'loader': f"{fmt.upper()}Loader" if available else "降级方案"
        }
    
    # 添加图片格式状态
    image_formats = ['.jpg', '.jpeg', '.png', '.webp']
    for fmt in image_formats:
        status[fmt] = {
            'available': MULTIMODAL_AVAILABLE,
            'loader': 'MultimodalImageLoader' if MULTIMODAL_AVAILABLE else '不可用'
        }
    
    return status