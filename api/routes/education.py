# api/routes/education.py
# 教育功能接口路由

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from api.schemas.knowledge_schema import (
    EducationSummaryRequest,
    EducationExplainRequest,
    EducationAdviceRequest,
)
from api.schemas.response_schema import SuccessResponse
from api.exceptions import ValidationError
from services.education_service import (
    generate_summary,
    generate_explanation,
    generate_advice,
    EducationServiceError,
)

router = APIRouter()


@router.post("/education/summary", response_model=SuccessResponse)
async def get_summary(request: EducationSummaryRequest):
    """
    生成知识点总结
    
    - **text**: 输入文本内容
    """
    try:
        if not request.text or not request.text.strip():
            raise ValidationError("文本内容不能为空")

        # V2.5.2：同步 LLM 调用移出事件循环
        result = await run_in_threadpool(generate_summary, text=request.text)

        return SuccessResponse(
            data={
                "key_points": result["key_points"],
                "summary": result["summary"],
                "model": result["model"],
            },
            message="知识点总结生成成功",
        )
    except EducationServiceError as e:
        raise ValidationError(str(e))


@router.post("/education/explain", response_model=SuccessResponse)
async def get_explanation(request: EducationExplainRequest):
    """
    生成题目解析
    
    - **question**: 题目内容
    """
    try:
        if not request.question or not request.question.strip():
            raise ValidationError("题目内容不能为空")

        # V2.5.2：同步 LLM 调用移出事件循环
        result = await run_in_threadpool(generate_explanation, question=request.question)

        return SuccessResponse(
            data={
                "question": result["question"],
                "explanation": result["explanation"],
                "model": result["model"],
            },
            message="题目解析生成成功",
        )
    except EducationServiceError as e:
        raise ValidationError(str(e))


@router.post("/education/advice", response_model=SuccessResponse)
async def get_advice(request: EducationAdviceRequest):
    """
    生成学习建议
    
    - **history**: 问题历史列表（可选）
    """
    try:
        # V2.5.2：同步 LLM 调用移出事件循环
        result = await run_in_threadpool(generate_advice, history=request.history)

        return SuccessResponse(
            data={
                "advice": result["advice"],
                "model": result["model"],
            },
            message="学习建议生成成功",
        )
    except EducationServiceError as e:
        raise ValidationError(str(e))