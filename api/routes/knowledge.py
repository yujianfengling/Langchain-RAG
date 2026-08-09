# api/routes/knowledge.py
# 知识库管理接口路由

import logging
import os
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from api.schemas.knowledge_schema import (
    KnowledgeBuildRequest,
    KnowledgeListResponse,
    KnowledgeStatsResponse,
    DocumentListResponse,
    UploadResponse,
)
from api.schemas.response_schema import SuccessResponse
from api.exceptions import KnowledgeBaseNotFoundError, RAGServiceError, ValidationError
from services.knowledge_service import (
    build_subject_knowledge,
    delete_subject_knowledge,
    list_knowledge_bases,
    get_knowledge_base_stats,
    upload_file_to_subject,
    get_subject_documents,
    delete_subject_document,
    rebuild_subject_knowledge,
    KnowledgeServiceError,
    MAX_FILE_SIZE_BYTES,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/knowledge", response_model=KnowledgeListResponse)
async def list_knowledge():
    """
    获取知识库列表
    
    返回所有已构建的知识库学科列表，包含 subject_id 和 status
    """
    try:
        subjects = list_knowledge_bases()
        return KnowledgeListResponse(subjects=subjects)
    except KnowledgeServiceError as e:
        raise RAGServiceError(str(e))


@router.post("/knowledge/build", response_model=SuccessResponse)
async def build_knowledge(request: KnowledgeBuildRequest):
    """
    构建知识库
    
    - **subject_id**: 学科ID
    - **path**: 知识库源文件目录路径
    """
    try:
        # V2.5.2：构建知识库为同步重操作（embedding），移出事件循环
        result = await run_in_threadpool(
            build_subject_knowledge,
            subject_id=request.subject_id,
            source_path=request.path,
        )

        return SuccessResponse(
            data={
                "subject_id": result["subject_id"],
                "document_count": result["document_count"],
                "status": result["status"],
            },
            message=f"知识库 {request.subject_id} 构建成功",
        )
    except KnowledgeServiceError as e:
        # 区分不同类型的错误
        error_msg = str(e)
        if "不存在" in error_msg or "不是目录" in error_msg:
            raise ValidationError(error_msg)
        elif "没有找到" in error_msg:
            raise ValidationError(error_msg)
        else:
            raise RAGServiceError(error_msg)


@router.delete("/knowledge/{subject_id}", response_model=SuccessResponse)
async def delete_knowledge(subject_id: str):
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
    except KnowledgeServiceError as e:
        error_msg = str(e)
        if "不存在" in error_msg:
            raise KnowledgeBaseNotFoundError(subject_id)
        else:
            raise RAGServiceError(error_msg)


@router.get("/knowledge/{subject_id}/stats", response_model=KnowledgeStatsResponse)
async def get_stats(subject_id: str):
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
    except KnowledgeServiceError as e:
        error_msg = str(e)
        if "不存在" in error_msg:
            raise KnowledgeBaseNotFoundError(subject_id)
        else:
            raise RAGServiceError(error_msg)


@router.post("/knowledge/upload", response_model=UploadResponse)
async def upload_file(
    subject_id: str = Form(...),
    file: UploadFile = File(...),
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

        # 3. 读取文件内容
        file_content = await file.read()

        # 4. 文件大小预检查（避免调用 service 层的额外开销）
        if len(file_content) > MAX_FILE_SIZE_BYTES:
            size_mb = round(len(file_content) / (1024 * 1024), 2)
            raise ValidationError(
                f"文件大小 {size_mb}MB 超过限制（最大 {MAX_FILE_SIZE_BYTES // (1024*1024)}MB）"
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
    except (ValidationError, KnowledgeServiceError) as e:
        raise ValidationError(str(e))


@router.get("/knowledge/{subject_id}/documents", response_model=DocumentListResponse)
async def get_documents(subject_id: str):
    """
    获取学科文档列表
    
    - **subject_id**: 学科ID
    """
    try:
        documents = get_subject_documents(subject_id=subject_id)
        
        return DocumentListResponse(
            subject_id=subject_id,
            documents=documents,
        )
    except KnowledgeServiceError as e:
        raise RAGServiceError(str(e))


@router.delete("/knowledge/{subject_id}/documents/{filename}", response_model=SuccessResponse)
async def delete_document(subject_id: str, filename: str):
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
    except KnowledgeServiceError as e:
        error_msg = str(e)
        if "不存在" in error_msg:
            raise KnowledgeBaseNotFoundError(subject_id)
        else:
            raise RAGServiceError(error_msg)


@router.post("/knowledge/{subject_id}/rebuild", response_model=SuccessResponse)
async def rebuild_knowledge(subject_id: str):
    """
    重新构建学科知识库
    
    - **subject_id**: 学科ID
    """
    try:
        # V2.5.2：重建为同步重操作，移出事件循环
        result = await run_in_threadpool(rebuild_subject_knowledge, subject_id=subject_id)

        return SuccessResponse(
            data={
                "subject_id": result["subject_id"],
                "document_count": result["document_count"],
                "status": result["status"],
            },
            message=result["message"],
        )
    except KnowledgeServiceError as e:
        error_msg = str(e)
        if "不存在" in error_msg:
            raise KnowledgeBaseNotFoundError(subject_id)
        else:
            raise RAGServiceError(error_msg)

# ============ 知识图谱接口 ============

@router.get("/knowledge/{subject_id}/graph", response_model=SuccessResponse)
async def get_knowledge_graph(subject_id: str, node: str = None, depth: int = 1):
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
async def build_knowledge_graph_api(subject_id: str):
    """
    提交知识图谱构建任务（后台异步执行，不阻塞事件循环）

    - **subject_id**: 学科ID

    返回 202 Accepted：任务已提交；图谱在后台线程池中生成。
    构建期间普通 Chat / RAG / 健康检查 / 知识库接口不受影响。
    """
    # 快速校验知识库已构建（提交前失败，避免后台任务白跑）
    try:
        from rag.rag_service import _safe_dir_name
        index_pkl = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "vector_store", _safe_dir_name(subject_id), "index.pkl")
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
async def get_knowledge_graph_status(subject_id: str):
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
async def list_knowledge_graphs():
    """
    列出所有已构建的知识图谱
    """
    try:
        from rag.knowledge_graph import list_graphs
        return SuccessResponse(
            data={"graphs": list_graphs()},
            message="获取图谱列表成功",
        )
    except Exception as e:
        raise RAGServiceError(f"获取图谱列表失败: {str(e)}")


# ============ 文档查看接口 ============

@router.get("/knowledge/{subject_id}/documents/{filename}/file")
async def get_document_file(subject_id: str, filename: str):
    """
    查看上传的文档（PDF/Word/TXT/MD）
    
    - **subject_id**: 学科ID
    - **filename**: 文件名
    """
    from fastapi.responses import FileResponse
    from services.knowledge_service import get_subject_document_path, KnowledgeServiceError
    from api.exceptions import ValidationError
    
    try:
        file_path = get_subject_document_path(subject_id, filename)
        return FileResponse(
            path=str(file_path),
            filename=file_path.name,
            media_type=_guess_mime_type(file_path.suffix),
            content_disposition_type="inline",  # 内联显示（PDF在浏览器中直接查看）
        )
    except KnowledgeServiceError as e:
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
