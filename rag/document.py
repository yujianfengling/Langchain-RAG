"""
RAG 模块 - 文档处理
重构版本：作为路由层，调用 loaders 中的各个 Loader
保持 V2.2.1 的公共接口完全兼容
"""

from typing import List, Optional, Dict, Any, Union
from pathlib import Path
import logging

# 导入新的 Loader 架构
from .loaders import (
    get_loader_for_file,
    BaseLoader,
    LoadResult,
    TextLoader,
    PdfLoader,
    ImageLoader,
)

logger = logging.getLogger(__name__)

# 为了保持兼容性，重新导出这些类
__all__ = [
    'BaseLoader',
    'LoadResult',
    'TextLoader',
    'PdfLoader',
    'ImageLoader',
    'load_document',
    'load_document_result',
    'load_documents',
    'load_documents_from_directory',
    'get_supported_extensions',
]


def load_document_result(file_path: Union[str, Path]) -> LoadResult:
    """
    加载单个文件并**原样返回 LoadResult**（不抛异常）。

    2026-10-07 新增：导入任务需要区分失败类型（unsupported_format /
    parse_error / converter_unavailable / empty_content ...），而 load_document
    只抛一个 ValueError，类型信息会丢失。这里保留完整结果。
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    try:
        loader = get_loader_for_file(path)
    except Exception as e:
        return LoadResult(documents=[], success=False,
                          error_message=f"加载器初始化失败: {e}", error_type="parse_error")

    if loader is None:
        from rag import formats
        spec = formats.get_format(ext)
        if spec is not None and not spec.available:
            reason = formats.unavailable_reason(ext)
        else:
            reason = f"不支持的文件格式: {ext}"
        return LoadResult(documents=[], success=False,
                          error_message=reason, error_type="unsupported_format")

    if not path.exists():
        return LoadResult(documents=[], success=False,
                          error_message=f"文件不存在: {path}", error_type="io_error")
    if not path.is_file():
        return LoadResult(documents=[], success=False,
                          error_message=f"不是文件: {path}", error_type="io_error")

    try:
        return loader.load(path)
    except Exception as e:
        logger.error(f"加载文档异常: {path}, 错误: {e}", exc_info=True)
        return LoadResult(documents=[], success=False,
                          error_message=str(e), error_type="parse_error")


def load_document(file_path: Union[str, Path]) -> List[Any]:
    """
    加载单个文档文件
    
    保持 V2.2.1 的接口兼容性。
    
    Args:
        file_path: 文件路径
        
    Returns:
        文档列表
        
    Raises:
        ValueError: 不支持的文件格式
        FileNotFoundError: 文件不存在
    """
    result = load_document_result(file_path)

    if result.success:
        logger.info(f"成功加载文档: {file_path}, {len(result.documents)} 个文档")
        return result.documents

    logger.error(f"加载文档失败: {file_path}, 错误: {result.error_message}")
    raise ValueError(result.error_message)


def load_documents(file_paths: List[Union[str, Path]]) -> List[Any]:
    """
    批量加载多个文档文件
    
    保持 V2.2.1 的接口兼容性。
    
    Args:
        file_paths: 文件路径列表
        
    Returns:
        所有文档的列表
    """
    all_documents = []
    
    for file_path in file_paths:
        try:
            documents = load_document(file_path)
            all_documents.extend(documents)
        except Exception as e:
            logger.warning(f"跳过文件 {file_path}: {str(e)}")
            continue
    
    logger.info(f"批量加载完成: {len(file_paths)} 个文件, {len(all_documents)} 个文档")
    return all_documents


def load_documents_from_directory(
    directory: Union[str, Path],
    recursive: bool = True
) -> List[Any]:
    """
    从目录加载所有支持的文档
    
    保持 V2.2.1 的接口兼容性。
    
    Args:
        directory: 目录路径
        recursive: 是否递归搜索子目录
        
    Returns:
        所有文档的列表
    """
    directory = Path(directory)
    
    if not directory.exists():
        logger.warning(f"目录不存在: {directory}")
        return []
    
    if not directory.is_dir():
        logger.warning(f"不是目录: {directory}")
        return []
    
    all_documents = []
    supported_extensions = get_supported_extensions()
    
    # 获取所有支持的文件
    if recursive:
        files = [f for f in directory.rglob("*") if f.is_file() and f.suffix.lower() in supported_extensions]
    else:
        files = [f for f in directory.glob("*") if f.is_file() and f.suffix.lower() in supported_extensions]
    
    # 加载每个文件
    for file_path in files:
        try:
            documents = load_document(file_path)
            all_documents.extend(documents)
        except Exception as e:
            logger.warning(f"跳过文件 {file_path}: {str(e)}")
            continue
    
    logger.info(f"从目录 {directory} 加载了 {len(all_documents)} 个文档")
    return all_documents


def get_supported_extensions() -> List[str]:
    """
    获取所有支持的文件扩展名
    
    保持 V2.2.1 的接口兼容性。
    
    Returns:
        支持的扩展名列表（来自 rag.formats 单一事实来源）
    """
    try:
        from rag import formats
        return formats.parseable_extensions()
    except Exception:  # pragma: no cover - 理论不可达
        return ['.txt', '.pdf', '.docx', '.pptx', '.xlsx', '.xls', '.csv',
                '.jpg', '.jpeg', '.png', '.webp']
