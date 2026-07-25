"""
DeepSeek LLM实现
使用LangChain的ChatOpenAI调用DeepSeek API
"""

import os
from typing import Optional, Dict, Any, List

# 尝试导入LangChain
try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

# 默认配置
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 2000
DEFAULT_API_BASE = "https://api.deepseek.com"


def create_deepseek_llm(
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    **kwargs
) -> Any:
    """
    创建DeepSeek LLM实例
    
    Args:
        temperature: 温度参数
        max_tokens: 最大token数
        model: 模型名称
        api_key: API密钥
        api_base: API基础URL
        **kwargs: 其他参数
        
    Returns:
        ChatOpenAI实例
        
    Raises:
        ImportError: LangChain未安装
        RuntimeError: 配置错误或初始化失败
    """
    if not LANGCHAIN_AVAILABLE:
        raise ImportError("LangChain未安装，请运行: pip install langchain-openai")
    
    # 从环境变量获取配置，参数优先级更高
    api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
    model = model or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    api_base = api_base or os.getenv("DEEPSEEK_API_BASE", DEFAULT_API_BASE)
    
    # 处理温度参数
    temp = temperature
    if temp is None:
        temp_str = os.getenv("DEEPSEEK_TEMPERATURE", str(DEFAULT_TEMPERATURE))
        try:
            temp = float(temp_str)
        except ValueError:
            temp = DEFAULT_TEMPERATURE
    
    # 处理max_tokens参数
    tokens = max_tokens
    if tokens is None:
        tokens_str = os.getenv("DEEPSEEK_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))
        try:
            tokens = int(tokens_str)
        except ValueError:
            tokens = DEFAULT_MAX_TOKENS
    
    # 验证API Key
    if not api_key:
        raise RuntimeError("DeepSeek API Key未配置，请设置环境变量 DEEPSEEK_API_KEY")
    
    # 获取超时配置
    request_timeout = int(os.getenv("DEEPSEEK_REQUEST_TIMEOUT", "120"))
    
    try:
        # 创建ChatOpenAI实例
        llm = ChatOpenAI(
            model=model,
            temperature=temp,
            max_tokens=tokens,
            openai_api_key=api_key,
            openai_api_base=api_base,
            request_timeout=request_timeout,
            **kwargs
        )
        return llm
    except Exception as e:
        raise RuntimeError(f"创建DeepSeek LLM实例失败: {str(e)}")


def chat_with_deepseek(
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **kwargs
) -> str:
    """
    使用DeepSeek进行聊天
    
    Args:
        messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
        temperature: 温度参数
        max_tokens: 最大token数
        **kwargs: 其他参数
        
    Returns:
        AI回答内容
    """
    llm = create_deepseek_llm(temperature=temperature, max_tokens=max_tokens, **kwargs)
    
    # 转换消息格式
    langchain_messages = []
    for msg in messages:
        role = msg.get("role", "").lower()
        content = msg.get("content", "")
        
        if role == "system":
            langchain_messages.append(SystemMessage(content=content))
        elif role == "user":
            langchain_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            langchain_messages.append(AIMessage(content=content))
    
    # 调用LLM
    response = llm.invoke(langchain_messages)
    
    # 提取回答
    if hasattr(response, 'content'):
        return response.content
    else:
        return str(response)


def get_deepseek_config() -> Dict[str, Any]:
    """
    获取DeepSeek配置信息
    
    Returns:
        配置信息字典
    """
    return {
        "model": os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
        "api_base": os.getenv("DEEPSEEK_API_BASE", DEFAULT_API_BASE),
        "temperature": float(os.getenv("DEEPSEEK_TEMPERATURE", str(DEFAULT_TEMPERATURE))),
        "max_tokens": int(os.getenv("DEEPSEEK_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
        "api_key_configured": bool(os.getenv("DEEPSEEK_API_KEY")),
        "langchain_available": LANGCHAIN_AVAILABLE
    }


def check_deepseek_availability() -> Dict[str, Any]:
    """
    检查DeepSeek可用性
    
    Returns:
        可用性信息字典
    """
    result = {
        "provider": "deepseek",
        "available": False,
        "langchain_available": LANGCHAIN_AVAILABLE,
        "api_key_configured": False,
        "error": None
    }
    
    if not LANGCHAIN_AVAILABLE:
        result["error"] = "LangChain未安装"
        return result
    
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        result["error"] = "DEEPSEEK_API_KEY环境变量未设置"
        return result
    
    result["api_key_configured"] = True
    
    try:
        # 尝试创建LLM实例（不实际调用API）
        create_deepseek_llm()
        result["available"] = True
    except Exception as e:
        result["error"] = f"DeepSeek配置错误: {str(e)}"
    
    return result


# 公开API
__all__ = [
    "create_deepseek_llm",
    "chat_with_deepseek",
    "get_deepseek_config",
    "check_deepseek_availability"
]