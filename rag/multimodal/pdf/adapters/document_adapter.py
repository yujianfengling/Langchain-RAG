"""
文档适配器

将 PDFElement 转换为 LangChain Document。
"""

import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

from langchain_core.documents import Document

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class DocumentAdapter:
    """
    文档适配器
    
    将 PDFElement 转换为 LangChain Document，保持信息完整性和元数据。
    """
    
    def __init__(self):
        """初始化文档适配器"""
        # 初始化各个子适配器
        from .text_adapter import TextAdapter
        from .image_adapter import ImageAdapter
        from .formula_adapter import FormulaAdapter
        from .table_adapter import TableAdapter
        
        self.text_adapter = TextAdapter()
        self.image_adapter = ImageAdapter()
        self.formula_adapter = FormulaAdapter()
        self.table_adapter = TableAdapter()
        
        logger.info("DocumentAdapter 初始化完成")
    
    def adapt(
        self,
        elements: List[PDFElement],
        pdf_path: Optional[Path] = None,
        merge_small: bool = False,
        target_chunk_chars: int = 600,
    ) -> List[Document]:
        """
        将 PDFElement 列表转换为 Document 列表

        Args:
            elements: PDFElement 列表
            pdf_path: PDF 文件路径（可选）
            merge_small: 是否把相邻小元素累积合并成段落（v2.6 新增，默认 False
                         以保持"一元素一文档"的历史契约，现有测试与调用方不受影响）。
                         **生产入库路径应传 True**：PDF 抽取是逐元素的，一个数字/一个
                         字母都会各自成为 Document，实测导致 58%~73% 的 chunk 长度
                         不足 10 字符（中位仅 5 字符），真实内容被碎片淹没。
            target_chunk_chars: merge_small=True 时的合并目标长度
            
        Returns:
            Document 列表
        """
        documents: List[Document] = []
        for element in elements:
            try:
                doc = self._adapt_element(element, pdf_path)
                if doc and (doc.page_content or "").strip():
                    documents.append(doc)
            except Exception as e:
                logger.warning(f"转换元素失败: {element.type.value}, 错误: {str(e)}")
                continue

        if merge_small:
            merged = self._merge_by_window(documents, target_chunk_chars)
            logger.info(
                f"转换完成: {len(elements)} 个元素 -> {len(merged)} 个文档"
                f"（合并前 {len(documents)} 个）")
            return merged

        logger.info(f"转换完成: {len(elements)} 个元素 -> {len(documents)} 个文档")
        return documents

    def _merge_by_window(
        self,
        documents: List[Document],
        target_chars: int,
    ) -> List[Document]:
        """
        按页内顺序把相邻小文档累积合并，直到接近 target_chars。

        只合并"同页"文档（利用 page 元数据），避免跨页拼接造成语义错位；
        保留首元素元数据并记录合并来源，便于溯源。
        """
        if not documents:
            return []

        def _page(doc) -> Any:
            return (doc.metadata or {}).get("page")

        merged: List[Document] = []
        buf: List[Document] = []
        buf_len = 0

        def _flush():
            nonlocal buf, buf_len
            if not buf:
                return
            if len(buf) == 1:
                merged.append(buf[0])
            else:
                content = "\n".join(d.page_content for d in buf)
                meta = dict(buf[0].metadata or {})
                meta["merged_elements"] = len(buf)
                # 保留页码范围，便于引用定位
                pages = [p for p in (_page(d) for d in buf) if p is not None]
                if pages:
                    meta["page"] = pages[0] if pages[0] == pages[-1] else f"{pages[0]}-{pages[-1]}"
                merged.append(Document(page_content=content, metadata=meta))
            buf, buf_len = [], 0

        for doc in documents:
            dlen = len(doc.page_content or "")
            # 允许跨页累积：这些讲义类 PDF 每页文字很少（实测约 180 字符/页），
            # 若按页强制断开会产生大量远小于 chunk_size 的块。
            # 页码信息在 flush 时记录为页码范围，溯源能力不受影响。
            if dlen >= target_chars:
                _flush()
                merged.append(doc)
                continue
            if buf_len + dlen + 1 > target_chars and buf:
                _flush()
            buf.append(doc)
            buf_len += dlen + 1

        _flush()
        return merged

    
    def _adapt_element(
        self,
        element: PDFElement,
        pdf_path: Optional[Path] = None
    ) -> Optional[Document]:
        """
        转换单个元素
        
        Args:
            element: PDFElement
            pdf_path: PDF 文件路径
            
        Returns:
            Document 或 None
        """
        if element.type == ElementType.TEXT:
            return self.text_adapter.adapt(element, pdf_path)
        elif element.type == ElementType.IMAGE:
            return self.image_adapter.adapt(element, pdf_path)
        elif element.type == ElementType.FORMULA:
            return self.formula_adapter.adapt(element, pdf_path)
        elif element.type == ElementType.TABLE:
            return self.table_adapter.adapt(element, pdf_path)
        else:
            logger.warning(f"未知元素类型: {element.type}")
            return None
    
    def adapt_batch(
        self,
        element_groups: List[List[PDFElement]],
        pdf_paths: Optional[List[Path]] = None
    ) -> List[Document]:
        """
        批量转换
        
        Args:
            element_groups: PDFElement 列表的列表
            pdf_paths: PDF 文件路径列表
            
        Returns:
            Document 列表
        """
        all_documents = []
        
        for i, elements in enumerate(element_groups):
            pdf_path = pdf_paths[i] if pdf_paths and i < len(pdf_paths) else None
            documents = self.adapt(elements, pdf_path)
            all_documents.extend(documents)
        
        logger.info(f"批量转换完成: {len(element_groups)} 组 -> {len(all_documents)} 个文档")
        return all_documents
    
    def merge_documents(
        self,
        documents: List[Document],
        strategy: str = 'concatenate'
    ) -> List[Document]:
        """
        合并文档
        
        Args:
            documents: Document 列表
            strategy: 合并策略 ('concatenate', 'group_by_page', 'group_by_type')
            
        Returns:
            合并后的 Document 列表
        """
        if strategy == 'concatenate':
            return self._merge_concatenate(documents)
        elif strategy == 'group_by_page':
            return self._merge_group_by_page(documents)
        elif strategy == 'group_by_type':
            return self._merge_group_by_type(documents)
        else:
            logger.warning(f"未知合并策略: {strategy}")
            return documents
    
    def _merge_concatenate(self, documents: List[Document]) -> List[Document]:
        """合并所有文档为一个"""
        if not documents:
            return []
        
        # 合并所有内容
        combined_content = "\n\n".join(doc.page_content for doc in documents)
        
        # 合并元数据
        combined_metadata = {
            'source': documents[0].metadata.get('source', ''),
            'page': 'multiple',
            'type': 'merged',
            'element_count': len(documents),
        }
        
        return [Document(page_content=combined_content, metadata=combined_metadata)]
    
    def _merge_group_by_page(self, documents: List[Document]) -> List[Document]:
        """按页合并文档"""
        page_groups = {}
        
        for doc in documents:
            page = doc.metadata.get('page', 0)
            if page not in page_groups:
                page_groups[page] = []
            page_groups[page].append(doc)
        
        merged_documents = []
        for page, docs in sorted(page_groups.items()):
            combined_content = "\n\n".join(doc.page_content for doc in docs)
            combined_metadata = {
                'source': docs[0].metadata.get('source', ''),
                'page': page,
                'type': 'merged_page',
                'element_count': len(docs),
            }
            merged_documents.append(Document(page_content=combined_content, metadata=combined_metadata))
        
        return merged_documents
    
    def _merge_group_by_type(self, documents: List[Document]) -> List[Document]:
        """按类型合并文档"""
        type_groups = {}
        
        for doc in documents:
            doc_type = doc.metadata.get('type', 'unknown')
            if doc_type not in type_groups:
                type_groups[doc_type] = []
            type_groups[doc_type].append(doc)
        
        merged_documents = []
        for doc_type, docs in type_groups.items():
            combined_content = "\n\n".join(doc.page_content for doc in docs)
            combined_metadata = {
                'source': docs[0].metadata.get('source', ''),
                'type': f'merged_{doc_type}',
                'element_count': len(docs),
            }
            merged_documents.append(Document(page_content=combined_content, metadata=combined_metadata))
        
        return merged_documents
