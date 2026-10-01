# services/education_service.py
# 教育功能业务服务层
# 使用 ai_service 提供的公共RAG/LLM调用逻辑

import json
import time
from typing import List, Dict, Any

from utils.logger import logger
from services.ai_service import call_llm, AIServiceError


class EducationServiceError(Exception):
    """教育服务异常"""
    pass


def _resolved_model_name(model: str = None) -> str:
    """本次实际调用的模型名（如实标注，替代此前的占位符 "current"）"""
    from services.ai_service import resolve_model_info
    return resolve_model_info(model).get("model") or ""


def generate_summary(text: str, model: str = None) -> Dict[str, Any]:
    """
    生成知识点总结

    Args:
        text: 输入文本
        model: 模型名（可选，V2.5.1 支持 Chat 模式透传）

    Returns:
        总结结果字典，包含知识点列表
    """
    try:
        prompt = f"""请对以下文本进行知识点总结，提取关键知识点：

文本内容：
{text}

请以JSON格式返回，格式如下：
{{
  "key_points": [
    {{
      "id": 1,
      "title": "知识点标题",
      "description": "知识点描述"
    }}
  ],
  "summary": "整体总结"
}}

请确保返回有效的JSON格式。"""

        # 使用ai_service的call_llm
        system_message = "你是一名专业的教育助手，擅长从文本中提取关键知识点。请用中文回答。"
        result = call_llm(
            prompt=prompt,
            system_message=system_message,
            model=model,
        )

        # 尝试解析JSON
        try:
            # 提取JSON部分
            json_start = result.find('{')
            json_end = result.rfind('}') + 1
            if json_start != -1 and json_end > json_start:
                json_str = result[json_start:json_end]
                parsed_result = json.loads(json_str)
                return {
                    "success": True,
                    "key_points": parsed_result.get("key_points", []),
                    "summary": parsed_result.get("summary", ""),
                    "model": _resolved_model_name(model),
                }
        except (json.JSONDecodeError, ValueError):
            pass
        
        # 如果JSON解析失败，返回原始文本
        return {
            "success": True,
            "key_points": [
                {
                    "id": 1,
                    "title": "知识点总结",
                    "description": result
                }
            ],
            "summary": result,
            "model": _resolved_model_name(model),
        }
        
    except AIServiceError as e:
        raise EducationServiceError(f"生成知识点总结失败: {str(e)}")
    except Exception as e:
        logger.error(f"生成知识点总结失败: {e}", exc_info=True)
        raise EducationServiceError(f"生成知识点总结失败: {str(e)}")


def generate_explanation(question: str, model: str = None) -> Dict[str, Any]:
    """
    生成题目解析

    Args:
        question: 题目内容
        model: 模型名（可选，V2.5.1 支持 Chat 模式透传）

    Returns:
        解析结果字典，包含步骤解析
    """
    try:
        prompt = f"""请对以下题目进行详细解析，提供步骤化的解答过程：

题目内容：
{question}

请按照以下格式进行解析：

**题目分析**
分析题目考查的知识点和解题思路。

**解题步骤**
1. 第一步：...
2. 第二步：...
3. 第三步：...

**最终答案**
给出最终答案。

**知识点回顾**
总结本题涉及的关键知识点。

请用中文回答，确保解析清晰、逻辑严密。"""

        # 使用ai_service的call_llm
        system_message = "你是一名专业的教育助手，擅长题目解析和解答。请用中文回答。"
        result = call_llm(
            prompt=prompt,
            system_message=system_message,
            model=model,
        )

        return {
            "success": True,
            "question": question,
            "explanation": result,
            "model": _resolved_model_name(model),
        }
        
    except AIServiceError as e:
        raise EducationServiceError(f"生成题目解析失败: {str(e)}")
    except Exception as e:
        logger.error(f"生成题目解析失败: {e}", exc_info=True)
        raise EducationServiceError(f"生成题目解析失败: {str(e)}")


def generate_advice(history: List[Dict[str, Any]] = None, model: str = None) -> Dict[str, Any]:
    """
    生成学习建议

    Args:
        history: 问题历史列表
        model: 模型名（可选，V2.5.1 支持 Chat 模式透传）

    Returns:
        学习建议字典
    """
    try:
        # 构建历史上下文
        history_context = ""
        if history and len(history) > 0:
            history_context = "\n\n学生历史问题记录：\n"
            for i, item in enumerate(history[-5:], 1):  # 只取最近5个问题
                question = item.get("question", "")
                history_context += f"{i}. {question}\n"
        
        prompt = f"""请根据学生的学习情况，提供个性化的学习建议。

{history_context}

请按照以下格式提供建议：

**学习现状分析**
分析学生当前的学习情况和可能存在的问题。

**学习建议**
1. **基础知识巩固**：建议加强哪些基础知识。
2. **学习方法改进**：建议采用哪些更有效的学习方法。
3. **练习方向**：建议在哪些方面加强练习。

**推荐学习资源**
推荐一些适合的学习资源或练习方式。

请用中文回答，建议要具体、可操作。"""

        # 使用ai_service的call_llm
        system_message = "你是一名专业的教育顾问，擅长根据学生的学习情况提供个性化建议。请用中文回答。"
        result = call_llm(
            prompt=prompt,
            system_message=system_message,
            model=model,
        )

        return {
            "success": True,
            "advice": result,
            "model": _resolved_model_name(model),
        }
        
    except AIServiceError as e:
        raise EducationServiceError(f"生成学习建议失败: {str(e)}")
    except Exception as e:
        logger.error(f"生成学习建议失败: {e}", exc_info=True)
        raise EducationServiceError(f"生成学习建议失败: {str(e)}")
