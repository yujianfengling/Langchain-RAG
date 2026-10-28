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
        try:
            from rag.loaders.pdf_map import display_page
            page = display_page(metadata)
        except Exception:
            pass
        doc_type = metadata.get('type') or metadata.get('modality') or 'text'

        # 原文片段：让学生能点开核对"回答依据的到底是哪段话"。
        # 只截取真实检索到的 chunk，不拼接、不生成，杜绝编造引用。
        content = getattr(doc, 'page_content', '') or ''
        snippet = re.sub(r'\s+', ' ', content).strip()[:300]

        # 章节/小节（有则给，没有不编）；2026-10-28：带父级标题链
        parents = str(metadata.get('parent_headings') or '').strip()
        leaf = metadata.get('section') or metadata.get('chapter') or metadata.get('heading')
        section = " > ".join(p for p in (parents, str(leaf or '').strip()) if p) or None

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
            # 资料有效性：verified = 已核实仍在库且有权；unverified_* = 无法核实（原因见值）。
            # 保留该字段是为了"明确降级原因"，避免把未核实的片段冒充有效依据。
            "validity": metadata.get('validity') or 'unchecked',
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


def _filter_existing_materials(docs: List[Any], subject_id: Optional[str],
                               viewer_user_id: Optional[str] = None) -> List[Any]:
    """检索进入上下文前的**资料有效性校验**（2026-10-08，第二轮加固）。

    为什么不能只依赖"删除后重建成功"：重建可能失败或还没跑完，而磁盘索引里仍然带着
    被删资料的分块；用户删完立刻提问时，旧内容会照样进入上下文与来源列表。

    校验顺序（**身份 + 权限优先，basename 只是兜底**）：
    1. 先用 material_scope.list_scope_materials(viewer, subject) 取"当前仍存在、且对该用户
       有权"的资料的稳定身份（课程资料是 material_id，个人/历史学科是 f:<文件名>）。
       命中 → 保留；未命中 → 丢弃（已删除 / 已被替换 / 已失权）。
    2. 该清单为空（个人学科无登记、或登记不可用）时，退回"学科目录里文件是否还在"的检查。
    3. 学科目录不可访问 → **不静默放行**：保留片段但标记 validity='unverified_no_dir' 并告警；
       调用方可据此降级展示，不会把它当成已核实的有效依据。
    4. 片段连来源标识都没有 → 标记 validity='unverified_no_source' 并告警。

    注意：这里**不做**"目录不可访问就全丢"的一刀切，也不做"没有标识就当有效"的静默放行——
    两种情况都带明确原因标记，避免误删合法历史资料、也避免冒充有效依据。
    """
    if not docs:
        return docs
    if not subject_id:
        for doc in docs:
            meta = getattr(doc, 'metadata', None)
            if isinstance(meta, dict):
                meta.setdefault('validity', 'unverified_no_subject')
        logger.warning('检索有效性校验：未提供学科，无法核实资料身份，全部标记为未核实')
        return docs

    # ---- 第 1 层：稳定身份 + 权限（课程资料有 material_id，个人学科是 f:<文件名>）----
    live_ids: Optional[set] = None
    live_names: Optional[set] = None
    try:
        from services import material_scope
        items = material_scope.list_scope_materials(viewer_user_id, subject_id) or []
        if items:
            live_ids = {(it.get('doc_id') or '') for it in items if it.get('doc_id')}
            live_names = {(it.get('file_name') or '') for it in items if it.get('file_name')}
    except Exception as e:
        logger.warning(f'检索有效性校验：资料身份清单不可用（降级为文件存在性检查）: {e}')

    # ---- 第 2 层：文件是否还在（兜底，且用于第 1 层无法判定的情况）----
    present: Optional[set] = None
    dir_reason = ''
    try:
        import os as _os
        from config.path_config import get_data_dir
        subject_dir = _os.path.join(str(get_data_dir()), 'subjects', str(subject_id))
        if _os.path.isdir(subject_dir):
            present = {name for name in _os.listdir(subject_dir)
                       if _os.path.isfile(_os.path.join(subject_dir, name))}
        else:
            dir_reason = 'unverified_no_dir'
    except Exception as e:
        dir_reason = 'unverified_no_dir'
        logger.warning(f'检索有效性校验：学科目录不可访问，标记为未核实: {e}')

    kept: List[Any] = []
    dropped = 0
    unverified = 0
    for doc in docs:
        meta = getattr(doc, 'metadata', None)
        if not isinstance(meta, dict):
            meta = {}
        material_id = meta.get('material_id')
        name = meta.get('file_name')
        if not name:
            src = meta.get('source') or meta.get('source_path') or ''
            name = str(src).replace(chr(92), '/').split('/')[-1] if src else ''
        doc_id = f'm:{material_id}' if material_id else (f'f:{name}' if name else '')

        if live_ids is not None:
            # 有权威清单：身份必须在清单里（覆盖已删除/已替换/已失权）
            if doc_id and (doc_id in live_ids
                          or (name and live_names and name in live_names)):
                meta['validity'] = 'verified'
                kept.append(doc)
            else:
                dropped += 1
            continue

        if name and present is not None:
            if name in present and not name.startswith('_superseded'):
                meta['validity'] = 'verified'
                kept.append(doc)
            else:
                dropped += 1
            continue

        # 无法核实：保留但**明确标记原因**，不冒充有效依据
        reason = dir_reason or 'unverified_no_source'
        meta['validity'] = reason
        unverified += 1
        kept.append(doc)

    if dropped or unverified:
        logger.info(f'检索有效性校验（subject={subject_id}）: 丢弃 {dropped} 条失效资料片段，'
                    f'标记未核实 {unverified} 条')
    return kept

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
    # 1.5 有效性校验：已删除/已替换掉的资料不得进入上下文与来源
    #     （不依赖"删除后重建成功"，重建失败或未完成时同样生效）
    context_docs = _filter_existing_materials(context_docs, subject_id, viewer_user_id)
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
