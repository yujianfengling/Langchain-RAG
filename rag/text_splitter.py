"""
文本分割模块
负责将长文档分割成较小的文本块，便于后续Embedding和检索

v2.0 语义感知切分策略：
优先级：Markdown标题 → 段落 → 句子 → 字符
保留metadata信息、来源文件名、章节信息
"""

import re
import logging
from typing import List, Optional, Union

logger = logging.getLogger(__name__)

# 尝试导入LangChain文本分割器
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    LANGCHAIN_SPLITTER_AVAILABLE = True
except ImportError:
    LANGCHAIN_SPLITTER_AVAILABLE = False
    # 创建一个简单的文本分割器替代类
    class RecursiveCharacterTextSplitter:
        """LangChain RecursiveCharacterTextSplitter的简单替代类"""
        def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50, **kwargs):
            self.chunk_size = chunk_size
            self.chunk_overlap = chunk_overlap
        
        def split_documents(self, documents):
            """简单的文档分割实现"""
            split_docs = []
            for doc in documents:
                text = doc.page_content
                metadata = doc.metadata.copy() if hasattr(doc, 'metadata') else {}
                
                chunks = []
                start = 0
                while start < len(text):
                    end = min(start + self.chunk_size, len(text))
                    chunk = text[start:end]
                    if chunk.strip():
                        chunks.append(chunk)
                    start += self.chunk_size - self.chunk_overlap
                
                for i, chunk in enumerate(chunks):
                    new_metadata = metadata.copy()
                    new_metadata['chunk_index'] = i
                    new_metadata['total_chunks'] = len(chunks)
                    
                    try:
                        from langchain_core.documents import Document
                        split_docs.append(Document(
                            page_content=chunk,
                            metadata=new_metadata
                        ))
                    except ImportError:
                        class SimpleDocument:
                            def __init__(self, page_content, metadata):
                                self.page_content = page_content
                                self.metadata = metadata
                        split_docs.append(SimpleDocument(chunk, new_metadata))
            
            return split_docs


# ========== 语义感知切分器 ==========

# Markdown标题正则
_MD_HEADER_RE = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)

# 中文句子分隔符
_CN_SENTENCE_RE = re.compile(r'(?<=[。！？；\n])\s*')

# 英文句子分隔符
_EN_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')


def _make_doc(page_content: str, metadata: dict):
    """创建兼容的Document对象"""
    try:
        from langchain_core.documents import Document
        return Document(page_content=page_content, metadata=metadata)
    except ImportError:
        class SimpleDocument:
            def __init__(self, page_content, metadata):
                self.page_content = page_content
                self.metadata = metadata
        return SimpleDocument(page_content, metadata)


def _split_by_markdown_headers(text: str, metadata: dict) -> List:
    """
    按Markdown标题切分文本
    
    识别 # ## ### 等标题，将文档按章节切分。
    每个chunk的metadata包含所属章节信息。
    """
    sections = []
    # 查找所有标题位置
    headers = list(_MD_HEADER_RE.finditer(text))
    
    if not headers:
        return [_make_doc(text, metadata)]
    
    # 处理标题前的内容（前言部分）
    if headers[0].start() > 0:
        preamble = text[:headers[0].start()].strip()
        if preamble:
            m = metadata.copy()
            m['section'] = '前言'
            sections.append(_make_doc(preamble, m))
    
    # 按标题切分
    for i, header_match in enumerate(headers):
        header_level = len(header_match.group(1))  # # = 1, ## = 2, ...
        header_title = header_match.group(2).strip()
        
        # 确定当前section的结束位置
        if i + 1 < len(headers):
            end_pos = headers[i + 1].start()
        else:
            end_pos = len(text)
        
        section_text = text[header_match.start():end_pos].strip()
        if section_text:
            m = metadata.copy()
            m['section'] = header_title
            m['header_level'] = header_level
            sections.append(_make_doc(section_text, m))
    
    return sections if sections else [_make_doc(text, metadata)]


def _split_by_paragraphs(text: str, metadata: dict) -> List:
    """按段落切分文本（双换行符分隔）"""
    paragraphs = re.split(r'\n\s*\n', text)
    result = []
    for i, para in enumerate(paragraphs):
        para = para.strip()
        if para:
            m = metadata.copy()
            m['paragraph_index'] = i
            result.append(_make_doc(para, m))
    return result if result else [_make_doc(text, metadata)]


def _split_by_sentences(text: str, metadata: dict) -> List:
    """按句子切分文本（中英文混合支持）"""
    # 先尝试中文分句
    sentences = _CN_SENTENCE_RE.split(text)
    # 如果没有中文标点，尝试英文分句
    if len(sentences) <= 1:
        sentences = _EN_SENTENCE_RE.split(text)
    
    result = []
    for i, sent in enumerate(sentences):
        sent = sent.strip()
        if sent:
            m = metadata.copy()
            m['sentence_index'] = i
            result.append(_make_doc(sent, m))
    return result if result else [_make_doc(text, metadata)]


def _split_by_characters(text: str, metadata: dict, chunk_size: int, chunk_overlap: int) -> List:
    """按字符切分（最后的fallback策略）"""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]
        if chunk.strip():
            m = metadata.copy()
            m['char_start'] = start
            m['char_end'] = end
            chunks.append(_make_doc(chunk, m))
        start += chunk_size - chunk_overlap
    return chunks


def _recursive_semantic_split(
    text: str,
    metadata: dict,
    chunk_size: int,
    chunk_overlap: int,
    depth: int = 0
) -> List:
    """
    递归语义切分主函数
    
    优先级：
    1. Markdown标题切分
    2. 段落切分
    3. 句子切分
    4. 字符切分（fallback）
    """
    # 如果文本已经在chunk_size内，直接返回
    if len(text) <= chunk_size:
        text = text.strip()
        if text:
            return [_make_doc(text, metadata)]
        return []
    
    # 防止无限递归
    if depth > 10:
        return _split_by_characters(text, metadata, chunk_size, chunk_overlap)
    
    # 优先级1: Markdown标题切分
    if depth == 0:
        md_sections = _split_by_markdown_headers(text, metadata)
        if len(md_sections) > 1:
            result = []
            for section in md_sections:
                if len(section.page_content) > chunk_size:
                    # 递归切分过长的section
                    result.extend(
                        _recursive_semantic_split(
                            section.page_content,
                            section.metadata,
                            chunk_size,
                            chunk_overlap,
                            depth + 1
                        )
                    )
                else:
                    result.append(section)
            return result
    
    # 优先级2: 段落切分
    paragraphs = _split_by_paragraphs(text, metadata)
    if len(paragraphs) > 1:
        result = []
        for para in paragraphs:
            if len(para.page_content) > chunk_size:
                result.extend(
                    _recursive_semantic_split(
                        para.page_content,
                        para.metadata,
                        chunk_size,
                        chunk_overlap,
                        depth + 1
                    )
                )
            else:
                result.append(para)
        return result
    
    # 优先级3: 句子切分
    sentences = _split_by_sentences(text, metadata)
    if len(sentences) > 1:
        # 将短句子合并到chunk_size限制内
        merged = []
        current_text = ""
        current_meta = metadata.copy()
        
        for sent in sentences:
            candidate = (current_text + " " + sent.page_content).strip() if current_text else sent.page_content
            if len(candidate) <= chunk_size:
                current_text = candidate
            else:
                if current_text:
                    merged.append(_make_doc(current_text, current_meta))
                current_text = sent.page_content
                current_meta = sent.metadata.copy()
        
        if current_text:
            merged.append(_make_doc(current_text, current_meta))
        
        return merged
    
    # 优先级4: 字符切分（最终fallback）
    return _split_by_characters(text, metadata, chunk_size, chunk_overlap)


def split_documents(
    documents: List[Union['Document', str]],
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    use_semantic: bool = True
) -> List[Union['Document', 'SimpleDocument']]:
    """
    将文档列表分割成较小的文本块
    
    Args:
        documents: Document对象列表或字符串列表
        chunk_size: 每个文本块的最大字符数（默认500）
        chunk_overlap: 相邻文本块之间的重叠字符数（默认50）
        use_semantic: 是否使用语义感知切分（默认True）
        
    Returns:
        分割后的Document对象列表
        
    Raises:
        ValueError: 输入列表为空或参数无效
        RuntimeError: 分割失败
    """
    # 参数验证
    if not documents:
        raise ValueError("文档列表不能为空")
    
    if chunk_size <= 0:
        raise ValueError("chunk_size必须为正整数")
    
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap不能为负数")
    
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap必须小于chunk_size")
    
    try:
        # 准备文档列表
        prepared_docs = []
        for doc in documents:
            if hasattr(doc, 'page_content'):
                prepared_docs.append(doc)
            elif isinstance(doc, str):
                prepared_docs.append(_make_doc(
                    doc,
                    {'source': 'string_input', 'file_type': 'text'}
                ))
            else:
                raise ValueError(f"不支持的文档类型: {type(doc)}")
        
        # 选择切分策略
        if use_semantic:
            # 语义感知切分（v2.0）
            split_docs = []
            for doc in prepared_docs:
                base_meta = doc.metadata.copy() if hasattr(doc, 'metadata') else {}
                chunks = _recursive_semantic_split(
                    doc.page_content,
                    base_meta,
                    chunk_size,
                    chunk_overlap,
                    depth=0
                )
                split_docs.extend(chunks)
            
            logger.info(f"语义切分完成: {len(prepared_docs)}个文档 → {len(split_docs)}个chunk")
        else:
            # 兼容旧版：使用LangChain的RecursiveCharacterTextSplitter
            if LANGCHAIN_SPLITTER_AVAILABLE:
                separators = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]
                text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    length_function=len,
                    separators=separators,
                    keep_separator=True
                )
                split_docs = text_splitter.split_documents(prepared_docs)
            else:
                text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap
                )
                split_docs = text_splitter.split_documents(prepared_docs)
        
        # 确保metadata正确传递并添加分割信息
        for i, doc in enumerate(split_docs):
            if not hasattr(doc, 'metadata'):
                doc.metadata = {}
            
            doc.metadata['chunk_index'] = i
            doc.metadata['total_chunks'] = len(split_docs)
            doc.metadata['chunk_size'] = chunk_size
            doc.metadata['chunk_overlap'] = chunk_overlap
            doc.metadata['split_method'] = 'semantic' if use_semantic else 'recursive_character'
        
        return split_docs
        
    except Exception as e:
        logger.error(f"文档分割失败: {str(e)}", exc_info=True)
        raise RuntimeError(f"文档分割失败: {str(e)}")


def split_text(
    text: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50
) -> List[str]:
    """
    将单个文本分割成较小的文本块
    
    Args:
        text: 待分割的文本
        chunk_size: 每个文本块的最大字符数（默认500）
        chunk_overlap: 相邻文本块之间的重叠字符数（默认50）
        
    Returns:
        分割后的文本块列表
        
    Raises:
        ValueError: 文本为空或参数无效
    """
    if not text or not text.strip():
        raise ValueError("文本不能为空")
    
    # 创建临时Document对象
    try:
        from langchain_core.documents import Document
        doc = Document(page_content=text, metadata={'source': 'text_input'})
    except ImportError:
        class SimpleDocument:
            def __init__(self, page_content, metadata):
                self.page_content = page_content
                self.metadata = metadata
        doc = SimpleDocument(text, {'source': 'text_input'})
    
    # 分割文档
    split_docs = split_documents([doc], chunk_size, chunk_overlap)
    
    # 提取文本内容
    return [doc.page_content for doc in split_docs]


def get_split_statistics(
    original_docs: List[Union['Document', str]],
    split_docs: List[Union['Document', str]]
) -> dict:
    """
    获取分割统计信息
    
    Args:
        original_docs: 原始文档列表
        split_docs: 分割后的文档列表
        
    Returns:
        统计信息字典
    """
    # 计算原始文档统计
    original_count = len(original_docs)
    original_chars = 0
    for doc in original_docs:
        if hasattr(doc, 'page_content'):
            original_chars += len(doc.page_content)
        elif isinstance(doc, str):
            original_chars += len(doc)
    
    # 计算分割后统计
    split_count = len(split_docs)
    split_chars = 0
    for doc in split_docs:
        if hasattr(doc, 'page_content'):
            split_chars += len(doc.page_content)
        elif isinstance(doc, str):
            split_chars += len(doc)
    
    return {
        'original_documents': original_count,
        'original_characters': original_chars,
        'split_documents': split_count,
        'split_characters': split_chars,
        'split_ratio': split_count / original_count if original_count > 0 else 0,
        'average_chunk_size': split_chars / split_count if split_count > 0 else 0
    }


def check_splitter_status() -> dict:
    """
    检查文本分割器的状态
    
    Returns:
        状态信息字典
    """
    return {
        'langchain_splitter_available': LANGCHAIN_SPLITTER_AVAILABLE,
        'semantic_splitter_available': True,
        'default_chunk_size': 500,
        'default_chunk_overlap': 50,
        'supported_separators': ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        'chinese_optimized': True,
        'split_strategies': ['markdown_header', 'paragraph', 'sentence', 'character'],
        'version': '2.0'
    }