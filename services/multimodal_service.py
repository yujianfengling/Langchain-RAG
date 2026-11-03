"""
多模态服务模块
提供统一的多模态内容处理接口，支持图片、音频、视频等
"""

import base64
import json
import logging
import os
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
from services import capability_status
from services.model_service import ModelProvider, get_model_provider

logger = logging.getLogger(__name__)

# 图片安全限制（提供方**硬上限**，超过会被拒绝）
MAX_IMAGE_DIMENSION = 4096  # 最大边长（像素）
MAX_IMAGE_PIXELS = 2048 * 2048  # 最大像素数（约400万像素）

# 视觉单次调用超时（秒）。可用 VISION_IMAGE_TIMEOUT_SECONDS 覆盖。
# 2026-10-07：实测同一份 deck 的 11 张图中只有 1 张未命中缓存，整体仍要 61.8s，
# 单次真实调用约 40s+。没有超时就会出现"一张图拖死一整份资料"。
def _vision_image_timeout() -> float:
    try:
        value = float(os.getenv("VISION_IMAGE_TIMEOUT_SECONDS", "30"))
        return value if value > 0 else 30.0
    except (TypeError, ValueError):
        return 30.0


def _scale_to_limits(width: int, height: int) -> float:
    """返回把 (width,height) 压到提供方上限内所需的缩放系数（<=1）

    注意：这里只守**提供方硬上限**（4096px / 400 万像素）。
    2026-10-03 曾加过"送审软上限 1536px"想省传输，但实测 350MB / 16 份 PPTX
    的总耗时只从 281.8s 降到 271.2s（-3.8%）——瓶颈是模型推理本身，
    不是图片传输。收益不抵复杂度，已撤回。真正有效的是并发（见
    multimodal_enricher._vision_max_concurrency）。
    """
    scale = 1.0
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        scale = min(scale,
                    MAX_IMAGE_DIMENSION / float(width or 1),
                    MAX_IMAGE_DIMENSION / float(height or 1))
    pixels = float(width or 1) * float(height or 1)
    if pixels * scale * scale > MAX_IMAGE_PIXELS:
        scale = min(scale, (MAX_IMAGE_PIXELS / pixels) ** 0.5)
    return max(scale, 1e-6)


def _cleanup_scaled_copy(path: str) -> None:
    """删除缩放送审副本（绝不删原图）"""
    try:
        p = Path(path)
        if p.exists() and p.is_file():
            p.unlink()
    except Exception as e:
        logger.warning(f"清理缩放副本失败: {path}: {e}")


def _scaled_copy(image_path: str, width: int, height: int) -> Dict[str, Any]:
    """
    生成一张等比缩放到提供方上限内的送审副本（原图文件不动）。

    Returns:
        {"path", "scaled_size", "max_side"} 或 {"error", "error_type"}
    """
    import io
    import uuid

    try:
        from PIL import Image
    except ImportError:
        return {"error": "PIL 未安装，无法缩放超大图", "error_type": "pil_unavailable"}

    scale = _scale_to_limits(width, height)
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    try:
        with Image.open(image_path) as img:
            resized = img.resize((new_w, new_h), Image.LANCZOS)
            fmt = (img.format or "PNG").upper()
            buf = io.BytesIO()
            if fmt in ("JPG", "JPEG"):
                if resized.mode not in ("RGB", "L"):
                    resized = resized.convert("RGB")
                resized.save(buf, format="JPEG", quality=85, optimize=True)
                ext = ".jpg"
            else:
                resized.save(buf, format="PNG", optimize=True)
                ext = ".png"
        data = buf.getvalue()
        try:
            from config.path_config import get_data_dir
            out_dir = get_data_dir() / "import_tmp" / "vision_scaled"
        except Exception:
            out_dir = Path(image_path).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{uuid.uuid4().hex[:12]}{ext}"
        out.write_bytes(data)
        return {"path": str(out),
                "scaled_size": f"{new_w}x{new_h}",
                "max_side": max(new_w, new_h)}
    except Exception as e:
        return {"error": f"超大图缩放失败: {e}", "error_type": "resize_failed"}

class MultimodalService:
    """
    多模态服务
    负责调用ModelProvider的vision/audio/video方法，管理prompt，处理错误
    """
    
    def __init__(self, vision_provider: Optional[ModelProvider] = None):
        """
        初始化多模态服务
        
        Args:
            vision_provider: 视觉模型提供商，如果为None则自动获取
        """
        self.vision_provider = vision_provider
        # 视觉能力不可用时的原因（供接口层原样返回，避免只报"不可用"而说不清为什么）
        self.vision_unavailable_reason = ""
        self._ensure_vision_provider()
    
    def _ensure_vision_provider(self):
        """
        确保视觉模型提供商已初始化。
        环境变量 VISION_MODE 控制模式：
        - "vision"：使用视觉模型（默认）
        - "ocr"：直接使用OCR，跳过Vision调用
        """
        import os
        mode = os.getenv("VISION_MODE", "vision").lower()
        
        if mode == "ocr":
            logger.info("[Multimodal] VISION_MODE=ocr，跳过Vision模型，直接使用OCR")
            self.vision_provider = None
            self.vision_unavailable_reason = "VISION_MODE=ocr：已按配置跳过大模型识图，改用 OCR"
            return
        
        if self.vision_provider is not None:
            return
        
        # Respect the existing DeepSeek-first policy. A leftover DashScope key
        # must not override the configured DeepSeek vision target.
        try:
            from config.models import get_model_api_key, get_model_config
            api_key = get_model_api_key("qwen-vision")
            deepseek_configured = bool(get_model_api_key("deepseek") and
                                  get_model_config("deepseek").get("vision_model_name"))
            if api_key and not deepseek_configured:
                self.vision_provider = get_model_provider("qwen-vision")
                self.vision_unavailable_reason = ""
                logger.info("[Multimodal] 使用 qwen-vision 视觉模型")
                return
        except Exception:
            pass
        
        # 2. 回退到**具备视觉能力**的提供方（按顺序取第一个"声明了 vision_model_name
        #    且凭证确实可用"的），而不是拿纯文本模型冒充视觉模型。
        #
        # 2026-09-25：默认文本模型曾是 DeepSeek（当时视为纯文本），因此只认 MiMo。
        # 2026-10-02 实测更正：DeepSeek 的对话模型同样接受 OpenAI 兼容的 image_url
        # 输入；实际支持的模型以官方视觉文档与专用配置为准。
        #
        # 顺序 = deepseek(已验证可用) > mimo(会员到期，key 常在但 401) > qwen-vision。
        # 关键：**"key 存在"不等于"key 可用"**。实测 MiMo 的 MIMO_API_KEY 仍在 .env 里
        # 但调用返回 401 invalid_api_key；若只按"key 非空"选提供方，会让失效凭证
        # 挡住可用凭证。因此这里对候选做一次极轻量的探活（1 token），通过才采用。
        #
        # 同时修复原实现的一个缺陷：它用 OpenAICompatibleProvider.__new__ 手动拼装
        # 实例，且 config["model_name"] 与 provider.model_name 语义不一致，
        # 导致回退路径实际请求的模型名不确定。
        try:
            from services.model_service import OpenAICompatibleProvider
            from config.models import get_model_config, get_model_api_key

            candidates = []
            for provider_name in ("deepseek", "mimo", "qwen"):
                try:
                    cfg = get_model_config(provider_name)
                except Exception:
                    continue
                vm = (cfg.get("vision_model_name") or "").strip()
                if not vm:
                    continue
                key = get_model_api_key(provider_name)
                if not key:
                    continue
                candidates.append((provider_name, cfg, vm, key))

            last_reason = ""
            for provider_name, cfg, vm, key in candidates:
                if provider_name != "deepseek" and not _provider_looks_usable(
                        provider_name, cfg, vm, key):
                    last_reason = f"{provider_name} 凭证不可用（探活失败）"
                    logger.info(f"[Multimodal] 跳过视觉候选 {provider_name}：探活失败")
                    continue

                provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
                provider.model_name = provider_name
                provider.config = {**cfg, "model_name": vm, "is_vision": True}
                provider.api_key = key
                self.vision_provider = provider
                self.vision_unavailable_reason = ""
                logger.info(
                    f"[Multimodal] 使用视觉模型 provider={provider_name} model={vm}")
                return

            self.vision_provider = None
            self.vision_unavailable_reason = (
                "视觉能力当前不可用：未找到可用的视觉模型凭证。"
                "请配置 DeepSeek 的 DEEPSEEK_API_KEY（其对话模型支持识图），"
                "或配置 qwen-vision 的 DASHSCOPE_API_KEY。"
                + (f"（最后检查：{last_reason}）" if last_reason else "")
                + "文本问答与学习功能不受影响。")
            logger.warning(f"[Multimodal] {self.vision_unavailable_reason}")
        except Exception as e:
            logger.warning(f"[Multimodal] 无法初始化视觉模型: {e}")
            self.vision_provider = None
            self.vision_unavailable_reason = f"视觉能力初始化失败：{e}"
    
    def describe_image(self, image_path: str, mime_type: Optional[str] = None, prompt: Optional[str] = None) -> Dict[str, Any]:
        """
        生成图片描述
        
        Args:
            image_path: 图片文件路径
            mime_type: 图片MIME类型，如果为None则自动检测
            prompt: 自定义提示词，如果为None则使用默认提示词
        
        Returns:
            包含 answer, model, latency, retries 的字典
        
        Raises:
            ValueError: 如果视觉模型不可用或调用失败
        """
        if not self.vision_provider:
            # 视觉不可用必须可见：VISION_MODE=ocr 曾让课件图片永远进不了知识库，
            # 而当时只写日志，界面上和健康检查里都看不出来。
            capability_status.safe_report(
                capability_status.CAP_VISION, False,
                reason=self.vision_unavailable_reason or "未配置可用的视觉提供方")
            raise ValueError(self.vision_unavailable_reason
                             or "视觉模型不可用，请配置通义千问视觉模型"
                                "（DASHSCOPE_API_KEY 或可用的视觉提供方凭证）")
        
        # 读取图片并转换为base64
        try:
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        except Exception as e:
            raise ValueError(f"读取图片文件失败: {e}")
        
        # 检测MIME类型
        if mime_type is None:
            mime_type = self._detect_mime_type(image_path)
        
        # 使用默认提示词或自定义提示词
        if prompt is None:
            prompt = self._get_default_image_prompt()
        
        # 构建多模态消息
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{base64_image}"
                        }
                    }
                ]
            }
        ]
        
        # 调用视觉模型。单次调用超时可配置（VISION_IMAGE_TIMEOUT_SECONDS，默认 30s），
        # 由 provider 的 requests timeout 强制执行，避免"一张图无限等"。
        try:
            vision_kwargs = {"timeout": _vision_image_timeout()}
            if getattr(self.vision_provider, "model_name", "") == "deepseek":
                # 图片描述/文字提取需要正式 content；不改变 chat/Agent 的思考策略。
                # requests 兼容客户端通过 extra_payload 合并到最终 JSON 请求体。
                vision_kwargs["extra_payload"] = {"thinking": {"type": "disabled"}}
            result = self.vision_provider.vision(
                messages, **vision_kwargs)
        except Exception as e:
            capability_status.safe_report(
                capability_status.CAP_VISION, False,
                reason=f"{type(e).__name__}: {e}")
            raise
        capability_status.safe_report(capability_status.CAP_VISION, True, reason="识图成功")
        return result
    
    def _detect_mime_type(self, image_path: str) -> str:
        """检测图片MIME类型"""
        ext = Path(image_path).suffix.lower()
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.bmp': 'image/bmp',
            '.tiff': 'image/tiff',
            '.tif': 'image/tiff',
        }
        return mime_types.get(ext, 'image/jpeg')
    
    def _get_default_image_prompt(self) -> str:
        """
        获取默认的图片描述提示词（要求结构化JSON输出）

        2026-10-02：新增 is_content 判定。
        实测课件里的图片约 56% 是噪声（校徽、PPT 模板底色、装饰边框、
        与课程无关的书法/广告照片），只有约 31% 含真实教学内容。
        把噪声图当知识入库会污染检索，因此让模型同时判定内容属性，
        由调用方决定是否入库。
        """
        return (
            "请分析这张教育图片，仅返回一个JSON对象，不要输出任何其他内容。\n"
            '格式：{"is_content": true/false, '
            '"kind": "公式|示意图|数据表|专业文字|校徽/模板|广告/照片|装饰/背景|其他", '
            '"description": "图片内容的中文描述，包含类型与关键信息", '
            '"ocr_text": "图片中出现的文字，逐字提取，没有文字则为空字符串"}\n'
            "要求：\n"
            "1. is_content 表示这张图是否包含**学科知识内容**（公式、定理、数据表格、"
            "专业示意图、专业文字）。校徽、PPT 模板底色、装饰边框、纯背景纹理、"
            "与课程无关的照片/书法/广告，一律 is_content=false。\n"
            "2. description 需描述图片类型与主要内容；即使 is_content=false 也要简要说明是什么。\n"
            "3. ocr_text 逐字提取图中文字。"
        )


    def extract_text_from_image(self, image_path: str) -> Dict[str, Any]:
        """
        从图片中提取文本和描述
        
        Args:
            image_path: 图片文件路径
        
        Returns:
            包含description、ocr_text、model_used、vision_status、retries等的字典
        """
        # provider_used: 供应商键名（如 qwen-vision）；model_used: 真实模型名（如 qwen-vl-plus）
        provider_used = ""
        model_used = ""
        if self.vision_provider:
            provider_used = getattr(self.vision_provider, "model_name", "") or ""
            _cfg = getattr(self.vision_provider, "config", None) or {}
            model_used = _cfg.get("model_name", "") or provider_used

        result = {
            "description": "",
            "ocr_text": "",
            "provider_used": provider_used,
            "model_used": model_used,
            "error": None,
            "vision_status": "pending",  # pending / success / failed / skipped
            "retries": 0,
            "error_type": None,
            # 内容判定（2026-10-02）：None=未判定/无法判定，True=含学科内容，False=噪声
            "is_content": None,
            "kind": "",
        }
        
        # 检查文件是否存在
        if not os.path.exists(image_path):
            result["error"] = "图片文件不存在"
            result["vision_status"] = "skipped"
            result["error_type"] = "file_not_found"
            return result
        
        # 检查文件大小（20MB限制）
        file_size = os.path.getsize(image_path)
        if file_size > 20 * 1024 * 1024:
            result["error"] = f"图片文件过大（{file_size / 1024 / 1024:.1f}MB > 20MB）"
            result["vision_status"] = "skipped"
            result["error_type"] = "file_too_large"
            return result
        
        # 检查图片尺寸：超大图先等比缩放出送审副本，绝不"必然失败还送"
        #
        # 2026-10-07 实测：slide001_img2.png 5439x4501、slide003_img1.jpg 8294400 像素
        # 之前直接 skipped，用户看到的是"图片白识别了"。现在缩放后送审，
        # 原图文件不动，并记录原始/缩放尺寸便于说明"内容没丢，是缩小后识别的"。
        vision_image_path = image_path
        scaled_copy_path = None
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                width, height = img.size
            result["image_original_size"] = f"{width}x{height}"
            if (width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION
                    or width * height > MAX_IMAGE_PIXELS):
                scaled = _scaled_copy(image_path, width, height)
                if scaled.get("error"):
                    result["error"] = scaled["error"]
                    result["vision_status"] = "skipped"
                    result["error_type"] = scaled.get("error_type") or "resize_failed"
                    return result
                vision_image_path = scaled["path"]
                scaled_copy_path = scaled["path"]
                result["image_scaled_size"] = scaled["scaled_size"]
                result["image_max_side"] = scaled["max_side"]
                result["image_downscaled"] = True
                logger.info(
                    f"[Multimodal] 超大图已缩放送审: {Path(image_path).name} "
                    f"{width}x{height} -> {scaled['scaled_size']}（内容不丢，缩小后识别）")
            else:
                logger.info(f"[Multimodal] image={Path(image_path).name}, size={file_size/1024:.1f}KB, dimensions={width}x{height}")
        except ImportError:
            logger.warning("PIL未安装，跳过图片尺寸检查")
        except Exception as e:
            logger.warning(f"图片尺寸检查失败: {e}")
        
        # 如果Vision模型未配置，直接跳到OCR
        if not self.vision_provider:
            logger.info(f"[Multimodal] Vision模型未配置，直接使用OCR: {Path(image_path).name}")
            result["vision_status"] = "skipped"
            result["error_type"] = "no_vision_provider"
            # 直接走OCR fallback
            try:
                from rag.multimodal.image.extractor import ImageExtractor
                extractor = ImageExtractor()
                result["ocr_text"] = extractor.extract_text(vision_image_path)
            except Exception as ocr_err:
                logger.warning(f"[Multimodal] OCR也失败了: {ocr_err}")
            if scaled_copy_path:
                _cleanup_scaled_copy(scaled_copy_path)
            return result
        
        # 尝试生成图片描述
        import time
        start_time = time.time()
        
        try:
            vision_result = self.describe_image(vision_image_path)
            latency = time.time() - start_time

            # 解析结构化JSON响应（description + ocr_text + is_content）
            model_desc, model_ocr = parse_vision_json_response(vision_result.get("answer", ""))
            result["description"] = model_desc
            result["ocr_text"] = model_ocr

            # 内容判定（2026-10-02）：区分"学科内容"与"校徽/模板/装饰"。
            # 解析不出时留 None，由调用方按"不因判定失败而丢内容"的原则处理。
            _d = _parse_vision_dict(vision_result.get("answer", ""))
            result["is_content"] = _as_bool(_d.get("is_content"))
            result["kind"] = str(_d.get("kind") or "")

            result["retries"] = vision_result.get("retries", 0)
            result["vision_status"] = "success"

            logger.info(
                f"[Multimodal] Vision调用成功, image={Path(image_path).name}, "
                f"provider={result['provider_used']}, model={result['model_used']}, "
                f"latency={latency:.2f}s, retries={result['retries']}, "
                f"is_content={result['is_content']}, kind={result['kind'] or '-'}, "
                f"caption_length={len(model_desc)}, ocr_length={len(model_ocr)}"
            )
        except Exception as e:
            latency = time.time() - start_time

            # 优先读取结构化错误（ModelAPIError 保留 status_code/error_type），避免字符串猜测丢失信息
            status_code = getattr(e, "status_code", None)
            error_type = getattr(e, "error_type", None)

            # 区分错误类型（关键词兜底，仅当无结构化信息时）
            if error_type is None:
                error_msg = str(e).lower()
                if "timeout" in error_msg or "超时" in error_msg:
                    error_type = "timeout"
                elif "api_key" in error_msg or "authentication" in error_msg or "unauthorized" in error_msg:
                    error_type = "invalid_api_key"
                elif "quota" in error_msg or "limit" in error_msg:
                    error_type = "quota_exceeded"
                elif "429" in error_msg:
                    error_type = "rate_limited"
                elif "403" in error_msg or "forbidden" in error_msg:
                    error_type = "forbidden"
                elif "404" in error_msg or "not found" in error_msg:
                    error_type = "model_not_found"
                elif "400" in error_msg or "invalid" in error_msg:
                    error_type = "invalid_request"
                elif "连接" in error_msg or "connection" in error_msg or "network" in error_msg:
                    error_type = "connection_error"
                else:
                    error_type = "unknown"

            result["error_type"] = error_type
            result["http_status"] = status_code
            user_error = "图片理解服务暂时不可用，请稍后重试"
            result["error"] = user_error
            result["vision_status"] = "failed"

            logger.warning(
                f"[Multimodal] Vision调用失败, image={Path(image_path).name}, "
                f"error_type={error_type}, http_status={status_code}, "
                f"provider={result['provider_used']}, model={result['model_used']}, "
                f"latency={latency:.2f}s, 降级为OCR fallback"
            )
        
        # 尝试OCR提取文本（仅当模型未返回ocr_text时作为辅助）
        if not result["ocr_text"]:
            try:
                from rag.multimodal.image.extractor import ImageExtractor
                extractor = ImageExtractor()
                result["ocr_text"] = extractor.extract_text(vision_image_path)

                if result["ocr_text"]:
                    logger.info(f"[Multimodal] OCR_success, text_length={len(result['ocr_text'])}")
            except Exception as e:
                logger.warning(f"OCR文本提取失败: {e}")

        if scaled_copy_path:
            _cleanup_scaled_copy(scaled_copy_path)
        return result
    
    def process_image_to_document(self, image_path: str, source: Optional[str] = None) -> Dict[str, Any]:
        """
        处理图片并生成用于RAG的文档数据
        
        Args:
            image_path: 图片文件路径
            source: 来源标识
        
        Returns:
            包含page_content和metadata的字典
        """
        # 提取文本和描述
        extraction_result = self.extract_text_from_image(image_path)
        
        # 构建page_content
        page_content_parts = []
        if extraction_result["description"]:
            page_content_parts.append(f"图片描述：{extraction_result['description']}")
        if extraction_result["ocr_text"]:
            page_content_parts.append(f"OCR文字：{extraction_result['ocr_text']}")
        
        page_content = "\n\n".join(page_content_parts) if page_content_parts else "图片内容（无法提取文本）"
        
        # 构建metadata
        filename = Path(image_path).name
        metadata = {
            "modality": "image",
            "source": source or image_path,
            "filename": filename,
            "ocr_result": extraction_result["ocr_text"],
            "caption": extraction_result["description"],
            "provider_used": extraction_result.get("provider_used") or "",
            "model_used": extraction_result.get("model_used") or "",
            "file_type": self._detect_mime_type(image_path),
            "error": extraction_result["error"],
            "vision_status": extraction_result.get("vision_status", "unknown"),
            "vision_retries": extraction_result.get("retries", 0),
            "http_status": extraction_result.get("http_status"),
            # 内容判定（2026-10-02）：透传给增强器，供"噪声图不入库"过滤使用
            "is_content": extraction_result.get("is_content"),
            "content_kind": extraction_result.get("kind") or "",
            # 超大图缩放（2026-10-07）：说明"内容没丢，是缩小后识别的"
            "image_original_size": extraction_result.get("image_original_size"),
            "image_scaled_size": extraction_result.get("image_scaled_size"),
            "image_max_side": extraction_result.get("image_max_side"),
            "image_downscaled": extraction_result.get("image_downscaled", False),
        }

        # 仅在失败时记录error_type
        if extraction_result.get("error_type"):
            metadata["vision_error_type"] = extraction_result["error_type"]
        
        return {
            "page_content": page_content,
            "metadata": metadata
        }


# ---- 视觉提供方探活缓存（每进程只探一次）----
_PROBE_CACHE: Dict[str, bool] = {}


def _provider_looks_usable(provider_name: str, cfg: dict, model: str, key: str) -> bool:
    """
    轻量探活：确认该提供方的凭证当前真的可用。

    为什么需要：`.env` 里存在 key ≠ key 有效。实测 MiMo 的 MIMO_API_KEY 仍在配置里，
    但调用返回 401 invalid_api_key。若只按"key 非空"挑选视觉提供方，
    失效凭证会把可用凭证（DeepSeek）挡在后面，表现为"配了 key 却用不了视觉"。

    只发 1 个 token 的最小请求；结果按 provider 缓存，整个进程只探一次。
    探活失败不抛异常，交由调用方跳到下一个候选。
    """
    if provider_name in _PROBE_CACHE:
        return _PROBE_CACHE[provider_name]
    try:
        import json as _json
        import requests

        url = f"{cfg.get('base_url', '').rstrip('/')}/chat/completions"
        body = _json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "ok"}],
            "max_tokens": 1,
        }).encode("utf-8")
        resp = requests.post(
            url, data=body, timeout=15,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        ok = resp.status_code == 200
        if not ok:
            logger.info(f"[Multimodal] {provider_name} 探活 HTTP {resp.status_code}")
        _PROBE_CACHE[provider_name] = ok
        return ok
    except Exception as e:
        logger.info(f"[Multimodal] {provider_name} 探活异常: {type(e).__name__}")
        _PROBE_CACHE[provider_name] = False
        return False


# 全局单例
_multimodal_service: Optional[MultimodalService] = None


def get_multimodal_service() -> MultimodalService:
    """获取多模态服务单例"""
    global _multimodal_service
    if _multimodal_service is None:
        _multimodal_service = MultimodalService()
    return _multimodal_service

def parse_vision_json_response(raw: str) -> Tuple[str, str]:
    """
    解析Vision模型的结构化JSON响应。

    支持：
    - 标准JSON字符串
    - Markdown代码块包裹的JSON（```json ... ```）
    解析失败时安全降级返回 ("", "")，不抛异常。

    Returns:
        (description, ocr_text)
    """
    if not raw:
        return "", ""
    data = _parse_vision_dict(raw)
    if isinstance(data, dict):
        return (str(data.get("description", "") or ""),
                str(data.get("ocr_text", "") or ""))
    return "", ""


def _parse_vision_dict(raw: str) -> Dict[str, Any]:
    """
    解析 Vision 结构化响应为完整字典（含 is_content / kind）。

    与 parse_vision_json_response 分开，是为了不破坏后者"返回两元组"的既有契约
    （多个测试依赖该签名）。解析失败返回空字典。
    """
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _as_bool(v: Any) -> Optional[bool]:
    """把模型返回的 true/false/"true"/"是" 等宽松地转成 bool；无法判断返回 None"""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "1", "是", "有", "内容"):
            return True
        if s in ("false", "no", "0", "否", "无", "噪声"):
            return False
    return None


def describe_image(image_path: str, **kwargs) -> str:
    """便捷函数：生成图片描述"""
    service = get_multimodal_service()
    return service.describe_image(image_path, **kwargs)

def process_image_to_document(image_path: str, **kwargs) -> Dict[str, Any]:
    """便捷函数：处理图片生成文档数据"""
    service = get_multimodal_service()
    return service.process_image_to_document(image_path, **kwargs)
