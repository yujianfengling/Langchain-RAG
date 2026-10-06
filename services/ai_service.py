"""
AI服务模块 - 公共RAG调用逻辑

职责：
1. 获取知识库上下文（RAG检索）
2. 调用LLM生成回答
3. 返回基础回答结构

chat_service: 负责普通聊天逻辑（流式/非流式）
education_service: 负责教育prompt增强（总结/解析/建议）
"""

import re
import time
from typing import List, Dict, Any, Optional

from utils.logger import logger

# 导入RAG模块
# 启动优化（2026-10-03）：不在**导入期**拉 rag（torch/transformers，实测 7.5s）。
# 保留 `RAG_AVAILABLE` 的既有语义（首次真正用到检索时才探测），
# 并在下面用**模块级瘦包装函数**提供 retrieve_context / build_context，
# 这样 `mock.patch("services.ai_service.retrieve_context")` 依旧有效。
RAG_AVAILABLE = None          # None = 尚未探测；True/False = 探测结果
RAG_IMPORT_ERROR = ""
_rag = None


def _load_rag() -> bool:
    """惰性导入 rag 模块（只尝试一次，结果缓存）"""
    global _rag, RAG_AVAILABLE, RAG_IMPORT_ERROR
    if RAG_AVAILABLE is False:
        return False
    if _rag is None:
        try:
            from services.rag_loader import load_rag
            _rag = load_rag()
            RAG_AVAILABLE = True
        except ImportError as e:
            RAG_AVAILABLE = False
            RAG_IMPORT_ERROR = str(e)
            return False
    return True

# 导入LLM模块
try:
    from llm import get_llm, get_llm_provider_info
    LLM_AVAILABLE = True
except ImportError as e:
    LLM_AVAILABLE = False
    LLM_IMPORT_ERROR = str(e)


class AIServiceError(Exception):
    """AI服务异常"""
    pass


def get_rag_context(
    question: str,
    subject_id: Optional[str] = None,
    top_k: int = 5,
    min_score: Optional[float] = None,
    viewer_user_id: Optional[str] = None,
    source_filter: Optional[List[str]] = None
) -> List:
    """
    获取RAG上下文文档

    Args:
        question: 用户问题
        subject_id: 学科ID（可选）
        top_k: 返回文档数量
        min_score: 相似度阈值覆盖（None = 用配置阈值）
        viewer_user_id: 查看者身份（用于课程资料可见性过滤）。
            None = 身份未知，按**学生视角**处理（fail-closed），
            避免漏传身份时把教师草稿资料带进上下文。

    Returns:
        相关文档列表（已按查看者可见范围过滤）

    Raises:
        AIServiceError: RAG检索失败时抛出（不再静默返回空列表）
    """
    if not _load_rag():
        error_msg = f"RAG模块不可用: {RAG_IMPORT_ERROR if not RAG_AVAILABLE else ''}"
        logger.error(error_msg)
        raise AIServiceError(error_msg)

    try:
        # 选定资料范围时扩大候选池，再按真实来源过滤（2026-10-06）：
        # 只把用户勾选的资料送进上下文，避免"前端改了文案、检索仍是全量"。
        fetch_k = max(top_k, top_k * 8) if source_filter else top_k
        context_docs = retrieve_context(
            question=question,
            subject_id=subject_id,
            top_k=fetch_k,
            min_score=min_score
        )
        # 课程资料可见性过滤：必须在进入模型上下文 / "参考来源"之前，
        # 否则 teacher_only（教师草稿）资料会通过问答内容与引用泄露。
        from services import teaching_access
        context_docs = teaching_access.filter_visible_documents(
            viewer_user_id, subject_id, context_docs)
        if source_filter:
            import os as _os
            wanted = {str(s).strip().lower() for s in source_filter if str(s).strip()}
            wanted_base = {_os.path.basename(w) for w in wanted}

            def _src(doc) -> str:
                meta = getattr(doc, "metadata", {}) or {}
                for key in ("source", "file_name", "filename", "name", "doc_name"):
                    v = meta.get(key)
                    if v:
                        return str(v)
                return ""

            kept = []
            for d in context_docs:
                s = _src(d).strip().lower()
                if not s:
                    continue
                if s in wanted or _os.path.basename(s) in wanted_base:
                    kept.append(d)
            context_docs = kept[:top_k]
            logger.info(
                "按资料范围过滤检索结果: 选中=%d 候选=%d 命中=%d subject=%s",
                len(source_filter), fetch_k, len(context_docs), subject_id)
        return context_docs
    except Exception as e:
        error_msg = f"RAG检索失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise AIServiceError(error_msg)


def build_context_string(context_docs: List) -> str:
    """
    将文档列表构建为上下文字符串
    
    Args:
        context_docs: 文档列表
        
    Returns:
        上下文字符串
    """
    if not context_docs:
        return ""
    
    try:
        return build_context(context_docs)
    except Exception as e:
        logger.warning(f"构建上下文字符串失败: {e}")
        return ""


def extract_sources(context_docs: List) -> List[Dict[str, Any]]:
    """
    从上下文文档中提取来源信息（API 边界统一结构）

    返回稳定结构：
    {
        "id": 1,
        "file_name": "数据结构.pdf",   # 优先取 metadata.file_name，避免绝对路径
        "page": 12,                    # 无页码时为 None
        "type": "text",                # modality/type，无则 text
    }

    Args:
        context_docs: 上下文文档列表

    Returns:
        来源信息列表
    """
    sources = []
    for i, doc in enumerate(context_docs):
        metadata = doc.metadata if hasattr(doc, 'metadata') else {}

        # 优先取 file_name（不含路径）；回退到取 source 的最后一段；再回退到占位
        file_name = metadata.get('file_name')
        if not file_name:
            src = metadata.get('source') or metadata.get('filename') or ''
            file_name = str(src).split('\\')[-1].split('/')[-1] if src else f'文档_{i+1}'

        page = metadata.get('page')
        doc_type = metadata.get('type') or metadata.get('modality') or 'text'

        # 原文片段：让学生能点开核对"回答依据的到底是哪段话"。
        # 只截取真实检索到的 chunk，不拼接、不生成，杜绝编造引用。
        content = getattr(doc, 'page_content', '') or ''
        snippet = re.sub(r'\s+', ' ', content).strip()[:300]

        # 章节/小节（有则给，没有不编）
        section = metadata.get('section') or metadata.get('chapter') or metadata.get('heading')

        # 诊断用分数：优先 rerank，其次检索分数（主界面不展示，放可展开详情）
        score = metadata.get('rerank_score')
        if score is None:
            score = metadata.get('retrieval_score')
        try:
            score = round(float(score), 4) if score is not None else None
        except (TypeError, ValueError):
            score = None

        sources.append({
            "id": i + 1,
            "file_name": file_name,
            "page": page,
            "type": doc_type,
            "snippet": snippet,
            "section": section,
            "score": score,
            "retrieval_type": metadata.get('retrieval_type'),
        })

    return sources


def call_llm(
    prompt: str,
    system_message: str = "你是一名智慧教育助手。",
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None
) -> str:
    """
    调用LLM生成回答（非流式）
    
    Args:
        prompt: 用户提示词
        system_message: 系统消息
        model: 模型名称（可选，默认使用配置中的默认模型）
        temperature: 温度参数
        max_tokens: 最大token数
        
    Returns:
        LLM生成的回答内容
        
    Raises:
        AIServiceError: LLM调用失败
    """
    if not LLM_AVAILABLE:
        raise AIServiceError(f"LLM模块不可用: {LLM_IMPORT_ERROR if not LLM_AVAILABLE else ''}")
    
    try:
        # 使用新的ModelProvider架构
        from services.model_service import get_model_provider
        
        provider = get_model_provider(model)
        
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ]
        
        # 能力上报与用量记账已下沉到 ModelProvider.chat（那里同时覆盖流式），
        # 此处不再重复，否则同一次调用会被记两遍
        result = provider.chat(messages, temperature=temperature, max_tokens=max_tokens)
        return result.get("answer", "")
        
    except Exception as e:
        # 失败上报同样在 ModelProvider.chat 里完成，这里只负责转换异常类型
        logger.error(f"LLM调用失败: {e}", exc_info=True)
        raise AIServiceError(f"LLM调用失败: {str(e)}")


def resolve_model_info(model: Optional[str] = None) -> Dict[str, str]:
    """
    解析"本次实际使用的模型"信息（单一入口，供各文本服务如实在结果里标注）

    Returns:
        {"provider": 提供方键如 deepseek, "model": 实际模型名如 deepseek-chat,
         "display_name": 展示名如 DeepSeek}
    """
    try:
        from config.models import get_default_model, get_model_config
        from services.model_service import get_model_provider
        provider_key = model or get_default_model()
        try:
            api_model = get_model_provider(provider_key).config.get("model_name") or provider_key
        except Exception:
            api_model = get_model_config(provider_key).get("model_name") or provider_key
        return {
            "provider": provider_key,
            "model": api_model,
            "display_name": get_model_config(provider_key).get("name", provider_key),
        }
    except Exception as e:
        logger.warning(f"解析模型信息失败: {e}")
        return {"provider": model or "", "model": model or "", "display_name": model or ""}


def get_model_info() -> Dict[str, Any]:
    """
    获取当前LLM模型信息
    
    Returns:
        模型信息字典，包含 model, provider 等
    """
    if not LLM_AVAILABLE:
        return {"model": "unknown", "provider": "unknown", "available": False}
    
    try:
        llm_info = get_llm_provider_info()
        return {
            "model": llm_info.get("model", "unknown"),
            "provider": llm_info.get("provider", "unknown"),
            "available": True,
        }
    except Exception as e:
        logger.warning(f"获取模型信息失败: {e}")
        return {"model": "unknown", "provider": "unknown", "available": False}


def retrieve_and_build_context(
    question: str,
    subject_id: Optional[str] = None,
    top_k: int = 5,
    min_score: Optional[float] = None,
    viewer_user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    一站式RAG检索 + 上下文构建
    
    Args:
        question: 用户问题
        subject_id: 学科ID
        top_k: 返回文档数量
        min_score: 相似度阈值覆盖（None = 用配置阈值）
        viewer_user_id: 查看者身份（课程资料可见性过滤；None 按学生视角 fail-closed）
        
    Returns:
        {
            "context": str,          # 构建好的上下文字符串
            "sources": List[Dict],   # 来源信息列表
            "context_used": bool,    # 是否使用了上下文
            "documents": List,       # 原始文档列表
        }
    """
    # 1. 获取RAG上下文（min_score 为 None 时用配置阈值；内部已按可见范围过滤）
    context_docs = get_rag_context(question, subject_id, top_k, min_score=min_score,
                                   viewer_user_id=viewer_user_id)
    context_used = len(context_docs) > 0
    
    # 2. 构建上下文字符串
    context = build_context_string(context_docs) if context_used else ""
    
    # 3. 提取来源信息
    sources = extract_sources(context_docs) if context_used else []
    
    return {
        "context": context,
        "sources": sources,
        "context_used": context_used,
        "documents": context_docs,
    }


def retrieve_and_build_context_scoped(
    question: str,
    subject_id: Optional[str] = None,
    top_k: int = 5,
    viewer_user_id: Optional[str] = None,
    scope: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    按「资料范围」可靠检索（2026-10-07 修复上一轮的召回缺口）。

    上一轮做法是"取 top_k*8 再按 basename 过滤"，问题是**过滤后可能一条都不剩**——
    所选资料确实在库里、也确实相关，只是没进候选池，于是回答显示"资料未覆盖"。

    这里改为三段式：
    1. 先用原问题扩大候选池（top_k*6），按稳定 doc_id 过滤；
    2. 命中不足 top_k 时，对每份所选资料再做一次"资料名 + 原问题"的补充检索；
    3. 合并去重（按 chunk 内容指纹）后重新组装上下文与来源。

    整个过程仍然只在**有权访问**的文档内进行：筛选发生在 teaching_access 可见性过滤之后。
    """
    from services import material_scope

    if not scope or scope.get("mode") == "all":
        result = retrieve_and_build_context(question, subject_id, top_k,
                                            viewer_user_id=viewer_user_id)
        result["scope"] = {"mode": "all", "signature": (scope or {}).get("signature")}
        result["supplemented"] = 0
        return result

    if scope.get("mode") == "empty":
        return {"context": "", "sources": [], "context_used": False, "documents": [],
                "scope": {"mode": "empty", "signature": scope.get("signature")},
                "supplemented": 0}

    fetch_k = max(top_k, top_k * 6)
    base = retrieve_and_build_context(question, subject_id, fetch_k,
                                      viewer_user_id=viewer_user_id)
    docs = material_scope.filter_documents_for_scope(scope, base.get("documents") or [])

    def _fingerprint(doc: Any) -> str:
        content = getattr(doc, "page_content", "") or ""
        meta = getattr(doc, "metadata", {}) or {}
        return f"{meta.get('material_id') or meta.get('source') or ''}::{content[:120]}"

    seen = {_fingerprint(d) for d in docs}
    supplemented = 0
    if len(docs) < top_k:
        for item in scope.get("selected") or []:
            if len(docs) >= top_k:
                break
            name = item.get("file_name") or ""
            if not name:
                continue
            try:
                extra = retrieve_and_build_context(
                    f"{name} {question}", subject_id, max(2, top_k - len(docs)),
                    viewer_user_id=viewer_user_id)
            except Exception as e:
                logger.debug(f"补充检索失败（{name}）: {e}")
                continue
            kept = material_scope.filter_documents_for_scope(
                {"mode": "selected", "selected": [item], "signature": ""},
                extra.get("documents") or [])
            for doc in kept:
                fp = _fingerprint(doc)
                if fp in seen:
                    continue
                seen.add(fp)
                docs.append(doc)
                supplemented += 1

    docs = docs[:top_k]
    context = build_context_string(docs) if docs else ""
    return {
        "context": context,
        "sources": extract_sources(docs) if docs else [],
        "context_used": bool(docs),
        "documents": docs,
        "scope": {"mode": scope.get("mode"), "signature": scope.get("signature"),
                  "label": scope.get("label")},
        "supplemented": supplemented,
    }


def retrieve_for_teaching(
    question: str,
    subject_id: Optional[str] = None,
    top_k: int = 5,
    viewer_user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    教师侧生成用的检索：先按配置阈值检索，为空时**放宽到不过滤**再检索一次。

    为什么需要它（2026-09-27 合并后回归）：
      课程资料入库时会做分块清洗，去掉 `[来源: 文件名]` 这类元信息包装。清洗本身是对的
      （这些包装不该进上下文），但会顺带削掉与**课程标题**的字面重合——而教师在界面上
      不填知识点时，默认查询词就是课程标题，于是严格阈值下检索为空，出题直接报
      "课程资料为空"，把"资料其实在库里"误判成"没资料"。

    这里不改全局相似度阈值（学生问答仍走配置阈值，避免答非所问），只在**教师自己课程的
    知识库**内、且严格检索确实为空时放宽一次，让默认路径能出题；真正没有资料时
    （知识库为空）放宽后依然为空，错误提示照旧。放宽与否通过 "relaxed" 如实上报。

    Returns:
        与 retrieve_and_build_context 同构，另加 "relaxed": bool
    """
    result = retrieve_and_build_context(question, subject_id, top_k,
                                        viewer_user_id=viewer_user_id)
    if (result.get("context") or "").strip():
        result["relaxed"] = False
        return result

    relaxed = retrieve_and_build_context(question, subject_id, top_k, min_score=0.0,
                                         viewer_user_id=viewer_user_id)
    if (relaxed.get("context") or "").strip():
        logger.info(
            "教师侧检索在配置阈值下为空，已放宽阈值命中 %d 条（subject_id=%s）",
            len(relaxed.get("documents") or []), subject_id)
        relaxed["relaxed"] = True
        return relaxed

    # 放宽后仍为空：说明知识库确实没有可用内容，保持原样并如实标记
    result["relaxed"] = False
    return result


def check_ai_service_status() -> Dict[str, Any]:
    """
    检查AI服务状态
    
    Returns:
        状态信息字典
    """
    status = {
        "rag_available": _load_rag(),
        "llm_available": LLM_AVAILABLE,
    }
    
    if not _load_rag():
        status["rag_import_error"] = RAG_IMPORT_ERROR
    
    if not LLM_AVAILABLE:
        status["llm_import_error"] = LLM_IMPORT_ERROR
    
    if LLM_AVAILABLE:
        status["model_info"] = get_model_info()
    
    return status


# 公开API
__all__ = [
    "resolve_model_info",
    "AIServiceError",
    "get_rag_context",
    "build_context_string",
    "extract_sources",
    "call_llm",
    "get_model_info",
    "retrieve_and_build_context",
    "retrieve_and_build_context_scoped",
    "retrieve_for_teaching",
    "check_ai_service_status",
]


def retrieve_context(*args, **kwargs):
    """惰性转发到 `rag.retrieve_context`（启动优化 2026-10-03）

    背景：模块级 `from rag import ...` 会在进程启动时把 rag（torch/transformers，
    实测 7.5s）拉进启动路径。这里把导入放进函数体，**保留模块级名字**以便
    `mock.patch("rag.retrieve_context")` 之类的既有用法继续生效。
    """
    from services.rag_loader import load_rag
    _impl = getattr(load_rag(), "retrieve_context")
    return _impl(*args, **kwargs)


def build_context(*args, **kwargs):
    """惰性转发到 `rag.build_context`（启动优化 2026-10-03）

    背景：模块级 `from rag import ...` 会在进程启动时把 rag（torch/transformers，
    实测 7.5s）拉进启动路径。这里把导入放进函数体，**保留模块级名字**以便
    `mock.patch("rag.build_context")` 之类的既有用法继续生效。
    """
    from services.rag_loader import load_rag
    _impl = getattr(load_rag(), "build_context")
    return _impl(*args, **kwargs)
