# api/routes/notes.py
# 智能笔记接口路由（V2.5 Task B）

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from api.deps import get_current_user, require_subject_read
from api.schemas.note_schema import SmartNoteRequest
from api.schemas.response_schema import SuccessResponse
from api.exceptions import ValidationError, LLMServiceError
from services.note_service import generate_smart_note, NoteServiceError
from services.kb_guard import KBError

router = APIRouter()


@router.post("/notes/generate")
async def generate_note(request: SmartNoteRequest,
                       user_id: str = Depends(get_current_user)):
    """
    生成五段式智能笔记（概念 / 例题 / 易错点 / 课堂提示 / 自测题）

    - **subject_id**: 学科ID
    - **topic**: 知识点主题（如"二叉树"）
    - **user_instruction**: 用户附加要求（可选）

    权限（2026-10-01）：本入口会读取指定学科的知识库并调用付费模型，
    因此必须与知识库接口同一套学科归属规则——课程学科仅授课教师 / 在班学生 /
    管理员可读；历史（公共）学科沿用"登录即可读"。笔记证据同样按查看者
    可见范围过滤，教师草稿资料不会进入笔记。
    """
    if not request.subject_id or not request.subject_id.strip():
        raise ValidationError("学科ID不能为空")
    if not request.topic or not request.topic.strip():
        raise ValidationError("知识点主题不能为空")

    await require_subject_read(request.subject_id, user_id)

    try:
        # V2.5.2：generate_smart_note 为同步阻塞（RAG + LLM），移出事件循环
        note = await run_in_threadpool(
            generate_smart_note,
            subject_id=request.subject_id,
            topic=request.topic.strip(),
            user_instruction=request.user_instruction,
            model=request.model,
            user_id=user_id,
        )
        return SuccessResponse(data=note, message="智能笔记生成成功")
    except KBError as e:
        # 学科不存在 / 知识库未构建 → 409，明确提示，不调 LLM
        return JSONResponse(
            status_code=409,
            content={"success": False, "data": None,
                     "message": e.message, "error_type": e.error_type},
        )
    except NoteServiceError as e:
        if e.error_type == "model_api_error":
            return JSONResponse(
                status_code=503,
                content={"success": False, "data": None,
                         "message": e.message, "error_type": e.error_type},
            )
        if e.error_type == "structured_output_error":
            return JSONResponse(
                status_code=500,
                content={"success": False, "data": None,
                         "message": "智能笔记生成失败：内容格式异常", "error_type": e.error_type},
            )
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": e.message, "error_type": e.error_type},
        )
