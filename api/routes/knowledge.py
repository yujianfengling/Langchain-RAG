# api/routes/knowledge.py
# 知识库管理接口路由

import logging
import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from api.schemas.knowledge_schema import (
    KnowledgeBuildRequest,
    KnowledgeListResponse,
    KnowledgeStatsResponse,
    DocumentListResponse,
    UploadResponse,
)
from api.deps import (
    require_current_user,
    require_subject_read, require_subject_write,
)
from api.schemas.response_schema import SuccessResponse
from api.exceptions import KnowledgeBaseNotFoundError, RAGServiceError, ValidationError
# 启动优化（2026-10-03）：这里原本是模块级 `from services.knowledge_service import (...)`，
# 它会在进程启动时拉起 services.knowledge_service → rag（torch/transformers，实测 7.5s）。
# 改为**模块级瘦包装函数**：名字仍在模块命名空间里（
# `mock.patch("api.routes.knowledge.list_knowledge_bases")` 之类的既有用法照常生效），
# 但真正的导入发生在首次调用。


def build_subject_knowledge(*args, **kwargs):
    """惰性转发到 services.knowledge_service.build_subject_knowledge（启动优化 2026-10-03）"""
    from services.knowledge_service import build_subject_knowledge as _impl
    return _impl(*args, **kwargs)

def delete_subject_knowledge(*args, **kwargs):
    """惰性转发到 services.knowledge_service.delete_subject_knowledge（启动优化 2026-10-03）"""
    from services.knowledge_service import delete_subject_knowledge as _impl
    return _impl(*args, **kwargs)

def list_knowledge_bases(*args, **kwargs):
    """惰性转发到 services.knowledge_service.list_knowledge_bases（启动优化 2026-10-03）"""
    from services.knowledge_service import list_knowledge_bases as _impl
    return _impl(*args, **kwargs)

def get_knowledge_base_stats(*args, **kwargs):
    """惰性转发到 services.knowledge_service.get_knowledge_base_stats（启动优化 2026-10-03）"""
    from services.knowledge_service import get_knowledge_base_stats as _impl
    return _impl(*args, **kwargs)

def upload_file_to_subject(*args, **kwargs):
    """惰性转发到 services.knowledge_service.upload_file_to_subject（启动优化 2026-10-03）"""
    from services.knowledge_service import upload_file_to_subject as _impl
    return _impl(*args, **kwargs)

def get_subject_documents(*args, **kwargs):
    """惰性转发到 services.knowledge_service.get_subject_documents（启动优化 2026-10-03）"""
    from services.knowledge_service import get_subject_documents as _impl
    return _impl(*args, **kwargs)

def delete_subject_document(*args, **kwargs):
    """惰性转发到 services.knowledge_service.delete_subject_document（启动优化 2026-10-03）"""
    from services.knowledge_service import delete_subject_document as _impl
    return _impl(*args, **kwargs)

def rebuild_subject_knowledge(*args, **kwargs):
    """惰性转发到 services.knowledge_service.rebuild_subject_knowledge（启动优化 2026-10-03）"""
    from services.knowledge_service import rebuild_subject_knowledge as _impl
    return _impl(*args, **kwargs)

def _kb_exc():
    """惰性取 KnowledgeServiceError（在 except 子句里使用，导入放在函数体）"""
    from services.knowledge_service import KnowledgeServiceError
    return KnowledgeServiceError


def _max_file_bytes() -> int:
    """惰性取上传大小上限（避免启动期导入 knowledge_service）"""
    from services.knowledge_service import MAX_FILE_SIZE_BYTES
    return MAX_FILE_SIZE_BYTES

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/knowledge", response_model=KnowledgeListResponse)
async def list_knowledge(user_id: str = Depends(require_current_user)):
    """
    获取知识库列表
    
    返回所有已构建的知识库学科列表，包含 subject_id 和 status
    """
    try:
        subjects = list_knowledge_bases()
        # 课程资料边界：课程学科只对本课程教师/在班学生可见
        subjects = await _readable_subjects(user_id, subjects)
        return KnowledgeListResponse(subjects=subjects)
    except _kb_exc() as e:
        raise RAGServiceError(str(e))


async def _guard_subject_write(subject_id: str, user_id: str) -> None:
    """
    非路径参数场景的学科写权限校验（upload 的 subject_id 在表单里，build 在请求体里）。

    课程学科：仅授课教师/管理员；历史全局学科：回退既有 knowledge admin 白名单。
    """
    from api.deps import require_subject_write
    await require_subject_write(subject_id, user_id)


def _student_visible_names(user_id: str, subject_id: str):
    """
    课程学科下"该用户可见的文件名"集合；None = 不做文件级过滤（历史全局学科/授课教师）
    """
    try:
        from services import teaching_access
        return teaching_access.student_visible_document_names(user_id, subject_id)
    except Exception as e:
        # 归属信息异常时不误伤历史数据，但要在日志里留痕
        logging.getLogger(__name__).warning(f"课程资料可见性判定失败: {e}")
        return None


async def _readable_subjects(user_id: str, subjects: list) -> list:
    """过滤出当前用户可读的学科（课程学科只对本课程教师/在班学生可见）"""
    from services import teaching_access
    out = []
    for item in subjects:
        sid = item.get("subject_id") if isinstance(item, dict) else getattr(item, "subject_id", None)
        try:
            if teaching_access.can_read_subject(user_id, sid):
                out.append(item)
        except Exception:
            out.append(item)  # 归属信息读取异常时不误伤历史数据
    return out


def _schedule_quiz_prewarm(subject_id: str) -> None:
    """建库 / 重建成功后，在后台适度预热该学科的通用题库。

    预热失败绝不能影响建库结果，因此这里吞掉所有异常（只记日志）。
    是否预热、预热多少，由 services/quiz_prewarm 依据环境变量决定。
    """
    try:
        from services import quiz_prewarm
        quiz_prewarm.schedule_prewarm(subject_id)
    except Exception as e:
        logger.warning(f"题库预热调度失败（不影响建库结果）: {e}")


@router.post("/knowledge/build", response_model=SuccessResponse)
async def build_knowledge(request: KnowledgeBuildRequest,
                          user_id: str = Depends(require_current_user)):
    """
    构建知识库

    - **subject_id**: 学科ID
    - **path**: 知识库源文件目录路径

    教师端补充：课程所属学科只有授课教师能构建（不因白名单未配置而放行）。
    """
    await _guard_subject_write(request.subject_id, user_id)
    try:
        # V2.5.2：构建知识库为同步重操作（embedding），移出事件循环
        result = await run_in_threadpool(
            build_subject_knowledge,
            subject_id=request.subject_id,
            source_path=request.path,
        )

        # 建库成功后，在后台适度预热该学科的通用题库（不阻塞响应，失败不影响建库）
        _schedule_quiz_prewarm(request.subject_id)

        return SuccessResponse(
            data={
                "subject_id": result["subject_id"],
                "document_count": result["document_count"],
                "status": result["status"],
            },
            message=f"知识库 {request.subject_id} 构建成功",
        )
    except _kb_exc() as e:
        # 区分不同类型的错误：
        # 路径/参数类问题属于调用方输入错误 → 400（ValidationError）
        # 其余（解析、索引、embedding 等）→ 500（RAGServiceError）
        error_msg = str(e)
        input_errors = ("不存在", "不是目录", "没有找到", "受控目录", "不能为空",
                        "格式不合法", "为空")
        if any(k in error_msg for k in input_errors):
            raise ValidationError(error_msg)
        raise RAGServiceError(error_msg)


@router.delete("/knowledge/{subject_id}", response_model=SuccessResponse)
async def delete_knowledge(subject_id: str,
                            user_id: str = Depends(require_subject_write)):
    """
    删除知识库
    
    - **subject_id**: 学科ID
    """
    try:
        result = delete_subject_knowledge(subject_id=subject_id)
        
        return SuccessResponse(
            data={
                "subject_id": result["subject_id"],
                "status": result["status"],
            },
            message=f"知识库 {subject_id} 已删除",
        )
    except _kb_exc() as e:
        error_msg = str(e)
        if "不存在" in error_msg:
            raise KnowledgeBaseNotFoundError(subject_id)
        else:
            raise RAGServiceError(error_msg)


@router.get("/knowledge/{subject_id}/stats", response_model=KnowledgeStatsResponse)
async def get_stats(subject_id: str,
                    user_id: str = Depends(require_subject_read)):
    """
    获取知识库统计信息
    
    - **subject_id**: 学科ID
    """
    try:
        stats = get_knowledge_base_stats(subject_id=subject_id)
        
        return KnowledgeStatsResponse(
            subject_id=stats["subject_id"],
            status=stats["status"],
            stats={
                "has_knowledge_base": stats["has_knowledge_base"],
                "document_count": stats["document_count"],
                "chunk_count": stats["chunk_count"],
                "total_size_bytes": stats["total_size_bytes"],
                "total_size_mb": stats["total_size_mb"],
            },
        )
    except _kb_exc() as e:
        error_msg = str(e)
        if "不存在" in error_msg:
            raise KnowledgeBaseNotFoundError(subject_id)
        else:
            raise RAGServiceError(error_msg)


@router.post("/knowledge/upload", response_model=UploadResponse)
async def upload_file(
    subject_id: str = Form(...),
    file: UploadFile = File(...),
    user_id: str = Depends(require_current_user),
):
    """
    上传文件到知识库
    
    - **subject_id**: 学科ID
    - **file**: 上传的文件（支持: pdf, docx, txt, md，最大20MB）
    """
    try:
        # 1. 参数基本校验
        if not subject_id or not subject_id.strip():
            raise ValidationError("学科ID不能为空")

        if not file.filename:
            raise ValidationError("文件名不能为空")

        # 2. 文件名预校验（在读取内容前快速拒绝非法请求）
        import re as _re
        if not _re.match(r'^[\w\u4e00-\u9fff-]+$', subject_id):
            raise ValidationError("学科ID格式不合法（支持中文、英文、数字、下划线、连字符）")

        # 2.5 课程归属校验（教师端）：subject_id 是表单字段，用不了路径依赖，
        #     因此在读取文件内容之前显式校验；课程学科仅授课教师可写。
        await _guard_subject_write(subject_id, user_id)

        # 3. 读取文件内容
        file_content = await file.read()

        # 4. 文件大小预检查（避免调用 service 层的额外开销）
        if len(file_content) > _max_file_bytes():
            size_mb = round(len(file_content) / (1024 * 1024), 2)
            raise ValidationError(
                f"文件大小 {size_mb}MB 超过限制（最大 {_max_file_bytes() // (1024*1024)}MB）"
            )

        # 5. 调用 service 层处理
        result = upload_file_to_subject(
            subject_id=subject_id,
            file_content=file_content,
            filename=file.filename,
        )
        
        logger.info(f"文件上传成功: subject={subject_id}, file={file.filename}")
        
        return UploadResponse(
            subject_id=result["subject_id"],
            filename=result["filename"],
            size=result["size"],
            status=result["status"],
            message=result["message"],
        )
    except (ValidationError, _kb_exc()) as e:
        raise ValidationError(str(e))


@router.get("/knowledge/{subject_id}/documents", response_model=DocumentListResponse)
async def get_documents(subject_id: str,
                        user_id: str = Depends(require_subject_read)):
    """
    获取学科文档列表

    - **subject_id**: 学科ID

    课程资料边界：课程学科下**仅教师可见**的资料不会出现在学生看到的列表里
    （历史全局学科保持既有行为）。
    """
    try:
        documents = get_subject_documents(subject_id=subject_id)
        allowed = _student_visible_names(user_id, subject_id)
        if allowed is not None:
            documents = [d for d in documents
                         if (d.get("name") if isinstance(d, dict)
                             else getattr(d, "name", None)) in allowed]
        return DocumentListResponse(
            subject_id=subject_id,
            documents=documents,
        )
    except _kb_exc() as e:
        raise RAGServiceError(str(e))


@router.delete("/knowledge/{subject_id}/documents/{filename}", response_model=SuccessResponse)
async def delete_document(subject_id: str, filename: str,
                              user_id: str = Depends(require_subject_write)):
    """
    删除学科文档
    
    - **subject_id**: 学科ID
    - **filename**: 文件名
    """
    try:
        result = delete_subject_document(
            subject_id=subject_id,
            filename=filename,
        )
        
        return SuccessResponse(
            data={
                "subject_id": result["subject_id"],
                "filename": result["filename"],
                "status": result["status"],
            },
            message=result["message"],
        )
    except _kb_exc() as e:
        error_msg = str(e)
        if "不存在" in error_msg:
            raise KnowledgeBaseNotFoundError(subject_id)
        else:
            raise RAGServiceError(error_msg)


@router.post("/knowledge/{subject_id}/rebuild", response_model=SuccessResponse)
async def rebuild_knowledge(subject_id: str,
                                 user_id: str = Depends(require_subject_write)):
    """
    重新构建学科知识库
    
    - **subject_id**: 学科ID
    """
    try:
        # V2.5.2：重建为同步重操作，移出事件循环
        result = await run_in_threadpool(rebuild_subject_knowledge, subject_id=subject_id)

        # 重建同样触发一次预热（进程内同一学科只跑一次）
        _schedule_quiz_prewarm(subject_id)

        return SuccessResponse(
            data={
                "subject_id": result["subject_id"],
                "document_count": result["document_count"],
                "status": result["status"],
            },
            message=result["message"],
        )
    except _kb_exc() as e:
        error_msg = str(e)
        if "不存在" in error_msg:
            raise KnowledgeBaseNotFoundError(subject_id)
        else:
            raise RAGServiceError(error_msg)

# ============ 知识图谱接口 ============

@router.get("/knowledge/{subject_id}/graph", response_model=SuccessResponse)
async def get_knowledge_graph(subject_id: str, node: str = None, depth: int = 1,
                              user_id: str = Depends(require_subject_read)):
    """
    获取学科知识图谱
    
    - **subject_id**: 学科ID
    - **node**: 起始节点（可选，None返回全部）
    - **depth**: 展开深度（默认1）
    """
    def _load_and_query(sid, _node, _depth):
        """加载图谱 + 查询（同步，移入线程池）"""
        from rag.knowledge_graph import load_knowledge_graph, query_graph
        existing = load_knowledge_graph(sid)
        if existing is None:
            return None, None
        return existing, query_graph(sid, node=_node, depth=_depth)

    try:
        # V2.5.2：图谱加载/查询为同步文件+图遍历，移出事件循环
        existing, result = await run_in_threadpool(
            _load_and_query, subject_id, node, depth)

        if existing is None:
            raise KnowledgeBaseNotFoundError(subject_id)

        return SuccessResponse(
            data=result,
            message="获取知识图谱成功",
        )
    except KnowledgeBaseNotFoundError:
        raise
    except Exception as e:
        raise RAGServiceError(f"获取知识图谱失败: {str(e)}")


@router.post("/knowledge/{subject_id}/graph/build")
async def build_knowledge_graph_api(subject_id: str,
                                         user_id: str = Depends(require_subject_write)):
    """
    提交知识图谱构建任务（后台异步执行，不阻塞事件循环）

    - **subject_id**: 学科ID

    返回 202 Accepted：任务已提交；图谱在后台线程池中生成。
    构建期间普通 Chat / RAG / 健康检查 / 知识库接口不受影响。
    """
    # 快速校验知识库已构建（提交前失败，避免后台任务白跑）
    # 路径解析统一走 knowledge_service（不再硬编码项目根目录，
    # 避免与其它接口使用不同的路径解析规则）
    try:
        from services.knowledge_service import get_vector_store_dir
        index_pkl = os.path.join(get_vector_store_dir(subject_id), "index.pkl")
        if not os.path.exists(index_pkl):
            return JSONResponse(
                status_code=400,
                content={"success": False, "data": None,
                         "message": "知识库尚未构建或索引缺失，请先构建知识库"},
            )
    except Exception:
        pass

    try:
        from rag.knowledge_graph import submit_graph_build
        accepted, status = submit_graph_build(subject_id)
        if not accepted:
            # 已 queued/building 或全局忙 → 409，防止重复启动任务
            return JSONResponse(
                status_code=409,
                content={"success": False, "data": status,
                         "message": status.get("message", "知识图谱任务已在进行中")},
            )
        return JSONResponse(
            status_code=202,
            content={"success": True, "data": status,
                     "message": "知识图谱生成任务已提交"},
        )
    except Exception as e:
        logger.error(f"提交知识图谱任务失败: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "提交知识图谱任务失败，请稍后重试"},
        )


@router.get("/knowledge/{subject_id}/graph/status")
async def get_knowledge_graph_status(subject_id: str,
                                      user_id: str = Depends(require_subject_read)):
    """
    查询知识图谱构建任务状态

    状态：idle / queued / building / success / failed / interrupted
    """
    try:
        from rag.knowledge_graph import get_graph_build_status
        status = get_graph_build_status(subject_id)
        return {"success": True, "data": status, "message": "ok"}
    except Exception as e:
        logger.error(f"查询知识图谱状态失败: {str(e)}")
        return {"success": False, "data": None,
                "message": "查询知识图谱状态失败，请稍后重试"}


@router.get("/knowledge/graphs", response_model=SuccessResponse)
async def list_knowledge_graphs(user_id: str = Depends(require_current_user)):
    """
    列出所有已构建的知识图谱

    课程资料边界：只返回当前用户可读学科的图谱（课程图谱仅本课程教师/在班学生可见）
    """
    try:
        from rag.knowledge_graph import list_graphs
        from services import teaching_access
        graphs = list_graphs()
        graphs = await _readable_subjects(user_id, graphs)
        return SuccessResponse(
            data={"graphs": graphs},
            message="获取图谱列表成功",
        )
    except Exception as e:
        raise RAGServiceError(f"获取图谱列表失败: {str(e)}")


# ============ 文档查看接口 ============

@router.get("/knowledge/{subject_id}/documents/{filename}/file")
async def get_document_file(subject_id: str, filename: str,
                             user_id: str = Depends(require_subject_read)):
    """
    查看上传的文档（PDF/Word/TXT/MD）

    - **subject_id**: 学科ID
    - **filename**: 文件名

    课程资料边界：学生的"学科可读"不等于"每个文件都可读"——
    `teacher_only`（教师草稿）资料在班学生也拿不到（历史全局学科不受影响）。
    """
    from fastapi.responses import FileResponse
    from services.knowledge_service import get_subject_document_path, KnowledgeServiceError
    from api.exceptions import ValidationError

    allowed = _student_visible_names(user_id, subject_id)
    if allowed is not None and filename not in allowed:
        raise HTTPException(status_code=403, detail="该资料仅授课教师可见")

    try:
        file_path = get_subject_document_path(subject_id, filename)
        return FileResponse(
            path=str(file_path),
            filename=file_path.name,
            media_type=_guess_mime_type(file_path.suffix),
            content_disposition_type="inline",  # 内联显示（PDF在浏览器中直接查看）
        )
    except _kb_exc() as e:
        raise ValidationError(str(e))


def _guess_mime_type(ext: str) -> str:
    """根据文件扩展名猜测MIME类型"""
    mime_map = {
        '.pdf': 'application/pdf',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.doc': 'application/msword',
        '.txt': 'text/plain; charset=utf-8',
        '.md': 'text/markdown; charset=utf-8',
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.webp': 'image/webp',
    }
    return mime_map.get(ext.lower(), 'application/octet-stream')
