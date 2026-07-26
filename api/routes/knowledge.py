# api/routes/knowledge.py
# 知识库管理接口路由

import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Form

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
        result = build_subject_knowledge(
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
        result = rebuild_subject_knowledge(subject_id=subject_id)
        
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