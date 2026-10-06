"""
多模态文档增强器
提供VisionProvider抽象接口和MultimodalEnricher实现
V2.3.1 Phase 2.4.4: 添加Vision结果持久化缓存
"""

import json
import os
import hashlib
import logging
import time
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Vision缓存版本标识
#
# 2026-10-02 升到 v1.2：v1.1 虽然改了提示词，但那批缓存里的 is_content
# 在数据流上是断的（service→enricher 未透传），落盘的都是 null，
# 沿用会让噪声过滤失效。v1.2 修正透传，故必须让 v1.1 缓存失效。
VISION_PROMPT_VERSION = "v1.2"

# 过小图片阈值（字节）：低于此值视为装饰/校徽/边框，直接跳过识图。
# 依据：实测课件 <8KB 的图占 44%~59%，抽检几乎全部为模板类噪声。
#
# 可用 VISION_MIN_IMAGE_BYTES 覆盖：
#   - 测试环境（conftest.py）设为 0，让合成小图仍能走识图路径，保证该分支可测；
#   - 若某些学科确实存在"小图但有内容"的情况（如纯公式截图），也可调低。
def _min_content_image_bytes() -> int:
    try:
        return int(os.getenv("VISION_MIN_IMAGE_BYTES", str(8 * 1024)))
    except (TypeError, ValueError):
        return 8 * 1024


def _vision_max_concurrency() -> int:
    """受控并发的视觉调用数（默认 3）。过大反而会被提供方限流。

    2026-10-08（任务 6）：额外受 IMPORT_MAX_INFLIGHT 约束（若设置），
    让导入任务可以按“实测内存/提供方限流”统一压这个上限，而不必同时改两处配置。
    """
    try:
        value = int(os.getenv("VISION_MAX_CONCURRENCY", "3"))
    except (TypeError, ValueError):
        value = 3
    value = max(1, value)
    try:
        inflight = int(os.getenv("IMPORT_MAX_INFLIGHT", "0"))
    except (TypeError, ValueError):
        inflight = 0
    if inflight > 0:
        value = min(value, max(1, inflight))
    return value


def _vision_image_timeout() -> float:
    """单张图片视觉调用的墙钟上限（秒），默认 30；0/无效则回退默认。"""
    try:
        value = float(os.getenv("VISION_IMAGE_TIMEOUT_SECONDS", "30"))
    except (TypeError, ValueError):
        return 30.0
    return value if value > 0 else 30.0


def _vision_abort_requested() -> bool:
    """当前解析线程是否已被放弃（导入任务单文件超时后置位）。

    惰性导入 import_service 以避免模块级循环依赖；该模块在导入任务运行时必定已加载。
    独立使用增强器（不经过导入任务）时没有活动 abort，返回 False。
    """
    try:
        from services import import_service
        return import_service._vision_abort_requested()
    except Exception:
        return False


def _zero_vision_stats() -> Dict[str, int]:
    return {"images_total": 0, "images_unique": 0, "images_deduped": 0,
            "vision_calls": 0, "vision_cached": 0, "vision_failed": 0,
            "vision_skipped": 0, "vision_timeout": 0, "vision_ms": 0}


# 图片内容哈希去重用的短锁（同一批内重复图只识别一次）
_STATS_LOCK = threading.Lock()

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

            # 内容判定（2026-10-02）：上游 MultimodalService 已解析出 is_content/kind，
            # 必须在此透传，否则下游的噪声过滤拿不到判定结果，永远不生效。
            if "is_content" in metadata:
                result["is_content"] = metadata.get("is_content")
            if metadata.get("content_kind"):
                result["kind"] = metadata.get("content_kind")

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
        # 最近一次批量图片增强的统计（供导入任务计时落库，见 import_service timings_json）
        self.last_stats: Dict[str, int] = _zero_vision_stats()
        logger.info("MultimodalEnricher初始化完成")

    # ---------- 图片批处理：去重 + 受控并发 + 单张超时 ----------

    @staticmethod
    def _content_hash(image_path: Optional[str]) -> Optional[str]:
        """图片内容哈希（用于同批去重）；文件不存在/不可读返回 None（按唯一处理）"""
        if not image_path:
            return None
        try:
            h = hashlib.md5()
            with open(image_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return None

    def _analyze_with_timeout(self, image_path: str) -> Dict[str, Any]:
        """
        在墙钟上限内调用视觉提供方；超时不阻塞整份资料。

        注意：底层 provider 自身也有 requests 超时（VISION_IMAGE_TIMEOUT_SECONDS），
        这里再包一层是为了兜住"重试循环/连接挂起"等 provider 内部耗时超过单张上限的情形。
        """
        timeout = _vision_image_timeout()
        provider = self.vision_provider
        provider_name = getattr(provider, "model_name", "") or "qwen-vision"
        if provider is None:
            return {"error": "视觉提供方不可用", "vision_status": "failed",
                    "vision_error_type": "no_vision_provider"}

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-item")
        future = executor.submit(provider.analyze_image, image_path)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            logger.warning(
                f"[Multimodal] 单张视觉调用超时（>{timeout:g}s），标记失败并继续: "
                f"{Path(image_path).name}")
            return {"error": f"视觉调用超时（>{timeout:g}s）",
                    "vision_status": "failed",
                    "vision_error_type": "vision_timeout",
                    "provider_used": provider_name,
                    "model_used": provider_name}
        finally:
            # 不等待被放弃的调用（它已是 daemon 线程池，最终会被 requests 超时终止）
            executor.shutdown(wait=False)

    @staticmethod
    def _copy_vision_result(src_doc: Any, dst_doc: Any) -> None:
        """把代表图的视觉结果复制到内容相同的重复图上（不重复调用）"""
        dst_doc.page_content = src_doc.page_content
        fields = ("description", "ocr_text", "provider_used", "model_used",
                  "vision_status", "vision_retries", "vision_error_type",
                  "vision_skip_reason", "is_content", "content_kind",
                  "image_original_size", "image_scaled_size", "image_max_side",
                  "image_downscaled")
        for field in fields:
            if field in src_doc.metadata:
                dst_doc.metadata[field] = src_doc.metadata[field]

    def _enrich_image_batch(self, image_docs: List[Any]) -> List[Any]:
        """
        批量增强图片文档：按内容哈希去重，同一批内重复图只识别一次；
        对唯一图按 VISION_MAX_CONCURRENCY 受控并发；顺序保持与输入一致。
        """
        stats = _zero_vision_stats()
        stats["images_total"] = len(image_docs)
        if not image_docs:
            self.last_stats = stats
            return image_docs

        groups: Dict[str, List[int]] = {}
        for idx, doc in enumerate(image_docs):
            path = self._extract_image_path(doc)
            key = self._content_hash(path) if path else None
            if key is None:
                key = f"__unique_{idx}"
            groups.setdefault(key, []).append(idx)

        reps = [members[0] for members in groups.values()]
        stats["images_unique"] = len(reps)
        stats["images_deduped"] = len(image_docs) - len(reps)

        def _run(rep_idx: int):
            doc = image_docs[rep_idx]
            # 任务 6：单文件墙钟超时后，被放弃的解析线程不再派发新的视觉工作。
            # （Python 无法强杀线程；这里做协作式中止：下一次派发前检查 abort。）
            if _vision_abort_requested():
                doc.metadata["vision_status"] = "skipped"
                doc.metadata["vision_skip_reason"] = "aborted_after_timeout"
                doc.page_content = ""
                return rep_idx, doc, 0.0
            start = time.monotonic()
            try:
                out = self._enrich_single_document(doc)
            except Exception as e:
                logger.warning(f"图片增强失败，保留原文档: {e}")
                out = doc
            return rep_idx, out, (time.monotonic() - start) * 1000.0

        results: Dict[int, Any] = {}
        if len(reps) == 1:
            seq = [_run(reps[0])]
        else:
            workers = min(_vision_max_concurrency(), len(reps))
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="vision-batch") as pool:
                seq = list(pool.map(_run, reps))

        for rep_idx, out, elapsed_ms in seq:
            results[rep_idx] = out
            stats["vision_ms"] += int(max(0.0, elapsed_ms))
            status = (getattr(out, "metadata", {}) or {}).get("vision_status", "")
            err = (getattr(out, "metadata", {}) or {}).get("vision_error_type", "")
            if err == "vision_timeout":
                stats["vision_timeout"] += 1
            if status == "cached":
                stats["vision_cached"] += 1
            elif status == "success":
                stats["vision_calls"] += 1
            elif status == "failed":
                stats["vision_failed"] += 1
            elif status == "skipped":
                stats["vision_skipped"] += 1

        # 重复图直接复用代表图结果
        for members in groups.values():
            rep_idx = members[0]
            rep_doc = results.get(rep_idx, image_docs[rep_idx])
            for dup_idx in members[1:]:
                self._copy_vision_result(rep_doc, image_docs[dup_idx])
                results[dup_idx] = image_docs[dup_idx]

        self.last_stats = stats
        logger.info(
            f"[Multimodal] 图片批处理完成: total={stats['images_total']} "
            f"unique={stats['images_unique']} deduped={stats['images_deduped']} "
            f"calls={stats['vision_calls']} cached={stats['vision_cached']} "
            f"failed={stats['vision_failed']} timeout={stats['vision_timeout']} "
            f"concurrency={_vision_max_concurrency()}")
        return [results.get(i, image_docs[i]) for i in range(len(image_docs))]

    def enrich_images(self, documents: List[Any]) -> List[Any]:
        """公共入口：对**图片文档**批量增强（去重+并发+单张超时）；非图片原样返回。"""
        if not documents:
            self.last_stats = _zero_vision_stats()
            return documents
        return self._enrich_image_batch(list(documents))
    
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
            self.last_stats = _zero_vision_stats()
            return documents

        # 图片走批量路径（内容去重 + 受控并发 + 单张超时），文本/表格逐元素处理。
        # 顺序保持不变，调用方无需感知内部并发。
        image_idx = set()
        for i, d in enumerate(documents):
            try:
                if self._detect_document_type(d) == "image":
                    image_idx.add(i)
            except Exception:
                # 没有 metadata / metadata=None 的文档交给 _enrich_single_document
                # （它会先补齐 metadata），不在这里因探测失败而中断整批。
                continue
        enriched_documents: List[Any] = list(documents)

        for i, doc in enumerate(documents):
            if i in image_idx:
                continue
            try:
                enriched_documents[i] = self._enrich_single_document(doc)
            except Exception as e:
                logger.warning(f"文档增强失败，保留原始文档: {e}")
                enriched_documents[i] = doc

        if image_idx:
            ordered = [documents[i] for i in sorted(image_idx)]
            batch_result = self._enrich_image_batch(ordered)
            for i, out in zip(sorted(image_idx), batch_result):
                enriched_documents[i] = out
        else:
            self.last_stats = _zero_vision_stats()

        logger.info(f"文档增强完成: {len(documents)} 个文档（图片 {len(image_idx)}）")
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

        # Step 0（2026-10-02）：体积预过滤 —— 调用识图**之前**就跳过明显的装饰图。
        # 实测课件里 <8KB 的图占 44%~59%，几乎都是校徽/模板边框/纯色底纹；
        # 对它们调用视觉模型既浪费额度，又会把"紫色渐变边框"这类描述写进知识库。
        try:
            if image_path_obj.exists() and image_path_obj.stat().st_size < _min_content_image_bytes():
                doc.metadata["vision_status"] = "skipped"
                doc.metadata["vision_skip_reason"] = "tiny_image"
                doc.metadata["is_content"] = False
                doc.page_content = ""
                logger.debug(
                    f"[Multimodal] 跳过过小图片({image_path_obj.stat().st_size}B): "
                    f"{image_path_obj.name}")
                return doc
        except OSError:
            pass
        
        # Step 1: 检查持久化缓存
        cache_key = self._compute_cache_key(image_path_obj)
        cached_data = self._load_from_cache(cache_key, image_path_obj)
        
        if cached_data:
            # 缓存命中，直接使用缓存数据
            cached_metadata = cached_data.get('metadata', {})
            # 缓存里已判定为噪声的图同样不入库（v1.1 起缓存会记录 is_content）
            if cached_metadata.get("is_content") is False:
                doc.metadata["vision_status"] = "skipped"
                doc.metadata["vision_skip_reason"] = "not_content"
                doc.metadata["is_content"] = False
                doc.page_content = ""
                return doc
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
                vision_result = self._analyze_with_timeout(image_path)

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

                # Step 2.5（2026-10-02）：内容判定 —— 噪声图不进知识库。
                # 实测抽检中约 56% 的课件图片是校徽/PPT模板/装饰/无关照片，
                # 把它们当知识入库会稀释检索结果（用户反馈"上传课件效益不高"的来源之一）。
                # 仅当模型**明确**判定为噪声（is_content is False）才丢弃；
                # 判定不出来（None）时保守保留，避免误杀真内容。
                is_content = vision_result.get("is_content")
                doc.metadata["is_content"] = is_content
                if vision_result.get("kind"):
                    doc.metadata["content_kind"] = vision_result["kind"]
                if is_content is False:
                    doc.metadata["vision_status"] = "skipped"
                    doc.metadata["vision_skip_reason"] = "not_content"
                    doc.page_content = ""
                    logger.info(
                        f"[Multimodal] 判定为非学科内容，不入库: "
                        f"{image_path_obj.name} kind={vision_result.get('kind') or '-'}")
                    return doc

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