"""
多模态文档增强器
提供VisionProvider抽象接口和MultimodalEnricher实现
V2.3.1 Phase 2.4.4: 添加Vision结果持久化缓存
"""

import json
import hashlib
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Vision缓存版本标识
VISION_PROMPT_VERSION = "v1.0"

# 图片描述缓存目录（与DocumentConverter共享）
#
# 修复（2026-10-01）：原先写成相对路径 `Path("data/cache/image_descriptions")`，
# 依赖进程工作目录——在仓库根运行测试/服务时会把缓存写进**正式 data/**（验收时
# 观察到 data/cache/vision_descriptions 被创建）。现改为按 `EDU_DATA_DIR` 运行时推导；
# 该名字保留为**显式覆盖**（既有测试通过 patch 它指向临时目录）。
IMAGE_DESCRIPTION_CACHE_DIR = None


def image_description_cache_dir() -> Path:
    """图片描述缓存目录（覆盖值优先，否则按数据根目录运行时推导）"""
    if IMAGE_DESCRIPTION_CACHE_DIR:
        return Path(IMAGE_DESCRIPTION_CACHE_DIR)
    from config.path_config import get_data_dir
    return get_data_dir() / "cache" / "image_descriptions"


class VisionProvider(ABC):
    """
    视觉提供者抽象接口
    定义图像分析的标准接口
    """
    
    @abstractmethod
    def analyze_image(self, image_path: str) -> Dict[str, Any]:
        """
        分析图像并返回结果
        
        Args:
            image_path: 图像文件路径
            
        Returns:
            包含分析结果的字典，至少包含：
            - description: 图像描述
            - ocr_text: OCR提取的文本（可选）
            - model_used: 使用的模型名称
            - error: 错误信息（如果有）
        """
        pass


class QwenVisionProvider(VisionProvider):
    """
    通义千问视觉提供者适配器
    作为services.multimodal_service.MultimodalService的适配器
    不复制DashScope调用代码，不重新实现缓存
    """
    
    def __init__(self, multimodal_service=None):
        """
        初始化QwenVisionProvider
        
        Args:
            multimodal_service: MultimodalService实例，如果为None则自动获取
        """
        self._multimodal_service = multimodal_service
        self._ensure_service()
    
    def _ensure_service(self):
        """确保MultimodalService已初始化"""
        if self._multimodal_service is None:
            try:
                from services.multimodal_service import get_multimodal_service
                self._multimodal_service = get_multimodal_service()
                logger.info("自动初始化MultimodalService")
            except Exception as e:
                logger.warning(f"无法初始化MultimodalService: {e}")
                self._multimodal_service = None
    
    def analyze_image(self, image_path: str) -> Dict[str, Any]:
        """
        分析图像并返回结果
        
        Args:
            image_path: 图像文件路径
            
        Returns:
            包含分析结果的字典
        """
        result = {
            "description": "",
            "ocr_text": "",
            "provider_used": "qwen-vision",
            "model_used": "qwen-vl-plus",
            "error": None
        }

        # 检查MultimodalService是否可用
        if not self._multimodal_service:
            result["error"] = "MultimodalService不可用"
            return result

        # 检查文件是否存在
        if not Path(image_path).exists():
            result["error"] = f"图像文件不存在: {image_path}"
            return result

        try:
            # 调用MultimodalService的process_image_to_document方法
            doc_data = self._multimodal_service.process_image_to_document(image_path)

            # 提取结果
            metadata = doc_data.get("metadata", {})
            result["description"] = metadata.get("caption", "")
            result["ocr_text"] = metadata.get("ocr_result", "")
            # provider_used/model_used 从实际处理结果取真实值（不做模糊硬编码覆盖）
            result["provider_used"] = metadata.get("provider_used") or "qwen-vision"
            result["model_used"] = metadata.get("model_used") or "qwen-vl-plus"
            result["vision_status"] = metadata.get("vision_status", "unknown")
            result["vision_retries"] = metadata.get("vision_retries", 0)
            result["http_status"] = metadata.get("http_status")

            if metadata.get("vision_error_type"):
                result["vision_error_type"] = metadata["vision_error_type"]

            if metadata.get("error"):
                result["error"] = metadata["error"]

        except Exception as e:
            logger.error(f"QwenVisionProvider分析图像失败: {e}")
            result["error"] = str(e)
            result["vision_status"] = "failed"
            result["vision_error_type"] = "exception"
            result["http_status"] = getattr(e, "status_code", None)

        return result


class MultimodalEnricher:
    """
    多模态文档增强器
    负责补充文档的多模态元数据
    """
    
    def __init__(self, vision_provider: Optional[VisionProvider] = None):
        """
        初始化MultimodalEnricher
        
        Args:
            vision_provider: 视觉增强提供者（依赖注入），默认使用QwenVisionProvider
        """
        self.vision_provider = vision_provider or QwenVisionProvider()
        logger.info("MultimodalEnricher初始化完成")
    
    def _compute_cache_key(self, image_path: Path) -> str:
        """
        计算复合缓存键
        
        缓存键 = hash(file_content + provider + model + prompt_version)
        """
        try:
            # 计算文件MD5
            hash_md5 = hashlib.md5()
            with open(image_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            file_hash = hash_md5.hexdigest()
        except Exception:
            file_hash = "unknown"
        
        # 获取Provider和模型信息
        provider_name = self.vision_provider.__class__.__name__ if self.vision_provider else "none"
        model_name = getattr(self.vision_provider, 'model_name', 'unknown') if self.vision_provider else "none"
        
        # 组合生成最终缓存键
        composite_key = f"{file_hash}_{provider_name}_{model_name}_{VISION_PROMPT_VERSION}"
        return hashlib.md5(composite_key.encode()).hexdigest()
    
    def _load_from_cache(self, cache_key: str, image_path: Path) -> Optional[Dict[str, Any]]:
        """
        从缓存加载Vision结果
        
        Returns:
            缓存的Vision结果字典，或None（缓存未命中）
        """
        try:
            cache_file = image_description_cache_dir() / f"{cache_key}.json"
            if not cache_file.exists():
                return None
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 验证缓存数据完整性
            if not cache_data.get('page_content') or not cache_data.get('metadata'):
                return None
            
            # 检查缓存版本
            cached_version = cache_data.get('metadata', {}).get('cache_prompt_version')
            if cached_version != VISION_PROMPT_VERSION:
                return None
            
            # 检查缓存是否过期（30天）
            created_at = cache_data.get('created_at', '')
            if created_at:
                try:
                    cache_date = datetime.fromisoformat(created_at)
                    if (datetime.now() - cache_date).days > 30:
                        return None
                except ValueError:
                    logger.warning(f"[Multimodal] 缓存日期解析失败: {created_at}")
            
            logger.info(
                f"[Multimodal] Vision缓存命中, image={image_path.name}, "
                f"model={cache_data['metadata'].get('model_used', 'unknown')}"
            )
            return cache_data
        except Exception as e:
            logger.warning(f"[Multimodal] 读取缓存失败: {e}")
            return None
    
    def _save_to_cache(self, cache_key: str, image_path: Path, page_content: str, metadata: Dict[str, Any]) -> None:
        """
        保存Vision结果到缓存
        仅缓存成功的结果
        """
        try:
            vision_status = metadata.get('vision_status', 'unknown')
            logger.debug(f"[Multimodal] _save_to_cache called: status={vision_status}, image={image_path.name}")

            # 仅缓存成功且 description 非空的结果
            if vision_status != 'success':
                logger.info(f"[Multimodal] 跳过缓存: status={vision_status}, image={image_path.name}")
                return
            if not metadata.get('description'):
                logger.info(f"[Multimodal] 跳过缓存: description为空, image={image_path.name}")
                return
            
            image_description_cache_dir().mkdir(parents=True, exist_ok=True)
            logger.info(f"[Multimodal] 创建缓存目录: {image_description_cache_dir()}")
            
            # 添加缓存版本
            metadata_copy = dict(metadata)
            metadata_copy['cache_prompt_version'] = VISION_PROMPT_VERSION
            
            cache_file = image_description_cache_dir() / f"{cache_key}.json"
            cache_data = {
                'cache_key': cache_key,
                'filename': image_path.name,
                'model': metadata.get('model_used', ''),
                'page_content': page_content,
                'metadata': metadata_copy,
                'created_at': datetime.now().isoformat()
            }
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            
            logger.info(
                f"[Multimodal] Vision结果已缓存, image={image_path.name}, "
                f"model={metadata.get('model_used', 'unknown')}, cache_key={cache_key}"
            )
        except Exception as e:
            logger.warning(f"[Multimodal] 保存缓存失败: {e}")
            import traceback
            traceback.print_exc()
    
    def enrich(self, documents: List[Any]) -> List[Any]:
        """
        增强文档列表，补充多模态元数据
        
        Args:
            documents: Document对象列表
            
        Returns:
            增强后的Document对象列表
        """
        if not documents:
            return documents
        
        enriched_documents = []
        for doc in documents:
            try:
                enriched_doc = self._enrich_single_document(doc)
                enriched_documents.append(enriched_doc)
            except Exception as e:
                logger.warning(f"文档增强失败，保留原始文档: {e}")
                enriched_documents.append(doc)
        
        logger.info(f"文档增强完成: {len(documents)} 个文档")
        return enriched_documents
    
    def _enrich_single_document(self, doc: Any) -> Any:
        """
        增强单个文档
        
        Args:
            doc: Document对象
            
        Returns:
            增强后的Document对象
        """
        # 确保metadata存在
        if not hasattr(doc, 'metadata') or doc.metadata is None:
            doc.metadata = {}
        
        # 根据文档类型进行增强
        doc_type = self._detect_document_type(doc)
        
        if doc_type == "image":
            return self._enrich_image_document(doc)
        elif doc_type == "table":
            return self._enrich_table_document(doc)
        else:
            # 文本文档，只添加modality字段
            return self._enrich_text_document(doc)
    
    def _detect_document_type(self, doc: Any) -> str:
        """
        检测文档类型
        
        Args:
            doc: Document对象
            
        Returns:
            文档类型: "text", "image", "table"
        """
        # 检查是否已有modality字段
        if "modality" in doc.metadata:
            return doc.metadata["modality"]
        
        # 检查内容特征
        content = doc.page_content if hasattr(doc, 'page_content') else ""
        metadata = doc.metadata if hasattr(doc, 'metadata') else {}
        
        # 检查是否为图像文档
        if any(key in metadata for key in ["image_path", "original_path"]):
            return "image"
        
        # 检查文件类型
        file_type = metadata.get("file_type", "").lower()
        if file_type in ["jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff"]:
            return "image"
        
        # 检查内容是否包含图像描述特征
        content_str = str(content)
        if "图片描述" in content_str or "OCR文字" in content_str:
            return "image"
        
        # 检查元数据是否为表格
        if metadata.get("source") == "table":
            return "table"
        
        # 检查内容是否包含表格特征
        if "表格" in content_str or "table" in content_str.lower():
            return "table"
        
        return "text"
    
    def _enrich_text_document(self, doc: Any) -> Any:
        """
        增强文本文档
        
        Args:
            doc: Document对象
            
        Returns:
            增强后的Document对象
        """
        # 如果已有modality字段（如formula），保留它
        if "modality" not in doc.metadata:
            # 根据type字段决定modality
            doc_type = doc.metadata.get("type", "text")
            if doc_type == "formula":
                doc.metadata["modality"] = "formula"
            else:
                doc.metadata["modality"] = "text"
        return doc
    
    def _enrich_image_document(self, doc: Any) -> Any:
        """
        增强图像文档（带持久化缓存）
        
        Args:
            doc: Document对象
            
        Returns:
            增强后的Document对象
        """
        # 确保有modality字段
        if "modality" not in doc.metadata:
            doc.metadata["modality"] = "image"
        
        # 检查modality是否为image
        if doc.metadata.get("modality") != "image":
            return doc
        
        # 获取image_path
        image_path = self._extract_image_path(doc)
        if not image_path:
            logger.warning("图像文档缺少image_path，跳过增强")
            doc.metadata["vision_status"] = "skipped"
            return doc
        
        image_path_obj = Path(image_path)
        
        # Step 1: 检查持久化缓存
        cache_key = self._compute_cache_key(image_path_obj)
        cached_data = self._load_from_cache(cache_key, image_path_obj)
        
        if cached_data:
            # 缓存命中，直接使用缓存数据
            cached_metadata = cached_data.get('metadata', {})
            doc.page_content = cached_data['page_content']
            doc.metadata["description"] = cached_metadata.get("description", "")
            doc.metadata["ocr_text"] = cached_metadata.get("ocr_text", "")
            doc.metadata["provider_used"] = cached_metadata.get("provider_used") or "qwen-vision"
            doc.metadata["model_used"] = cached_metadata.get("model_used") or "qwen-vl-plus"
            doc.metadata["vision_status"] = "cached"
            doc.metadata["vision_retries"] = 0
            return doc

        # Step 2: 缓存未命中，调用VisionProvider
        if self.vision_provider:
            try:
                vision_result = self.vision_provider.analyze_image(image_path)

                # 统一记录 provider_used / model_used（真实值，失败时也不缺失）
                doc.metadata["provider_used"] = vision_result.get("provider_used") or "qwen-vision"
                doc.metadata["model_used"] = vision_result.get("model_used") or "qwen-vl-plus"

                # 记录vision_status
                vision_status = vision_result.get("vision_status", "unknown")
                doc.metadata["vision_status"] = vision_status
                doc.metadata["vision_retries"] = vision_result.get("vision_retries", 0)
                if vision_result.get("http_status") is not None:
                    doc.metadata["vision_http_status"] = vision_result["http_status"]

                if vision_result.get("vision_error_type"):
                    doc.metadata["vision_error_type"] = vision_result["vision_error_type"]

                # 检查是否有错误
                if vision_result.get("error"):
                    logger.warning(
                        f"[Multimodal] Vision分析失败, image={image_path_obj.name}, "
                        f"status={vision_status}, error={vision_result['error']}"
                    )
                    return doc

                # 将结果写回metadata
                doc.metadata["description"] = vision_result.get("description", "")
                doc.metadata["ocr_text"] = vision_result.get("ocr_text", "")

                # 更新page_content
                self._update_page_content_with_vision(doc, vision_result)

                # Step 3: 保存成功结果到缓存
                self._save_to_cache(cache_key, image_path_obj, doc.page_content, doc.metadata)

                logger.info(
                    f"[Multimodal] Vision增强完成, image={image_path_obj.name}, "
                    f"status={vision_status}, retries={vision_result.get('vision_retries', 0)}"
                )

            except Exception as e:
                logger.warning(f"[Multimodal] 调用VisionProvider异常: {e}")
                doc.metadata["vision_status"] = "failed"
                doc.metadata["vision_error_type"] = "exception"
                doc.metadata["provider_used"] = doc.metadata.get("provider_used") or "qwen-vision"
                doc.metadata["model_used"] = doc.metadata.get("model_used") or "qwen-vl-plus"
                return doc

        return doc
    
    def _enrich_table_document(self, doc: Any) -> Any:
        """
        增强表格文档
        
        Args:
            doc: Document对象
            
        Returns:
            增强后的Document对象
        """
        # 确保有modality字段
        if "modality" not in doc.metadata:
            doc.metadata["modality"] = "table"
        
        # 检查modality是否为table
        if doc.metadata.get("modality") != "table":
            return doc
        
        # 保存原始page_content用于错误处理
        original_page_content = doc.page_content
        
        try:
            # 表格内容规范化
            self._normalize_table_content(doc)
            
            # 获取规范化后的内容
            normalized_content = doc.page_content
            
            # 写入metadata
            doc.metadata["description"] = "表格内容"
            doc.metadata["ocr_text"] = normalized_content
            
            # 更新page_content格式
            doc.page_content = f"表格内容:\n{normalized_content}"
            
        except Exception as e:
            logger.warning(f"表格内容转换失败: {e}")
            # 恢复原始内容，确保是字符串格式
            if isinstance(original_page_content, dict):
                # 如果是字典，尝试转换为Markdown表格
                try:
                    doc.page_content = self._dict_to_markdown_table(original_page_content)
                except Exception:
                    logger.warning(f"[Enrichment] 表格转Markdown失败")
                    doc.page_content = "| (表格转换失败) |"
            elif isinstance(original_page_content, str):
                doc.page_content = original_page_content if original_page_content.strip() else "| (空表格) |"
            else:
                doc.page_content = "| (表格转换失败) |"
            
            # 设置默认metadata
            doc.metadata["description"] = "表格内容（转换失败）"
            doc.metadata["ocr_text"] = doc.page_content
        
        return doc
    
    def _extract_image_path(self, doc: Any) -> Optional[str]:
        """
        从文档中提取图像路径
        
        Args:
            doc: Document对象
            
        Returns:
            图像路径，如果无法提取则返回None
        """
        metadata = doc.metadata if hasattr(doc, 'metadata') else {}
        
        # 尝试从不同字段获取图像路径
        for field in ["image_path", "original_path", "source"]:
            if field in metadata:
                path = metadata[field]
                if path and Path(path).exists():
                    return path
        
        return None
    
    def _update_page_content_with_vision(self, doc: Any, vision_result: Dict[str, Any]) -> None:
        """
        使用视觉分析结果更新page_content
        
        Args:
            doc: Document对象
            vision_result: VisionProvider返回的结果
        """
        if not hasattr(doc, 'page_content'):
            return
        
        description = vision_result.get("description", "")
        ocr_text = vision_result.get("ocr_text", "")
        
        # 构建新的page_content
        content_parts = []
        if description:
            content_parts.append(f"图片描述:\n{description}")
        if ocr_text:
            content_parts.append(f"OCR:\n{ocr_text}")
        
        if content_parts:
            # 保留原有内容（如果不是占位符）
            original_content = doc.page_content
            if original_content and not original_content.startswith("[图片]"):
                content_parts.insert(0, original_content)
            
            doc.page_content = "\n\n".join(content_parts)
    
    def _normalize_table_content(self, doc: Any) -> None:
        """
        规范化表格内容
        
        支持三种输入格式：
        1. dict格式：{"headers": ["A", "B"], "rows": [["1", "2"]]}
        2. str格式：保持原文本
        3. 空内容：返回"| (空表格) |"
        
        Args:
            doc: Document对象
        """
        if not hasattr(doc, 'page_content'):
            doc.page_content = "| (空表格) |"
            return
        
        content = doc.page_content
        
        # 如果是字典格式，检查是否包含headers和rows
        if isinstance(content, dict):
            if "headers" in content and "rows" in content:
                # 用户要求的格式：{"headers": ["A", "B"], "rows": [["1", "2"]]}
                doc.page_content = self._dict_to_markdown_table(content)
            else:
                # 旧格式：简单键值对字典
                doc.page_content = self._dict_to_markdown_table(content)
        elif isinstance(content, str):
            # 如果是字符串，检查是否为空
            if not content.strip():
                # 空内容，使用fallback
                doc.page_content = "| (空表格) |"
            else:
                # 保持原样
                pass
        else:
            # 其他格式，使用fallback
            doc.page_content = "| (空表格) |"
    
    def _dict_to_markdown_table(self, data: Dict[str, Any]) -> str:
        """
        将字典转换为Markdown表格
        
        支持两种字典格式：
        1. 新格式：{"headers": ["A", "B"], "rows": [["1", "2"]]}
        2. 旧格式：{"A": "1", "B": "2"}（简单键值对）
        
        Args:
            data: 字典数据
            
        Returns:
            Markdown表格字符串
        """
        if not data:
            return "| (空表格) |"
        
        # 检查是否为新格式（包含headers和rows）
        if "headers" in data and "rows" in data:
            headers = data["headers"]
            rows = data["rows"]
            
            if not headers:
                return "| (空表格) |"
            
            # 构建表格
            table_lines = []
            table_lines.append("| " + " | ".join(str(h) for h in headers) + " |")
            table_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            
            for row in rows:
                # 确保行长度与表头一致
                if len(row) < len(headers):
                    row = list(row) + [""] * (len(headers) - len(row))
                elif len(row) > len(headers):
                    row = row[:len(headers)]
                
                table_lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
            
            return "\n".join(table_lines)
        else:
            # 旧格式：简单键值对字典
            headers = list(data.keys())
            values = list(data.values())
            
            # 构建表格
            table_lines = []
            table_lines.append("| " + " | ".join(str(h) for h in headers) + " |")
            table_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            table_lines.append("| " + " | ".join(str(v) for v in values) + " |")
            
            return "\n".join(table_lines)