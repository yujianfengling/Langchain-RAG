"""
Ollama LLM实现
为未来支持本地Ollama模型预留接口
"""

import os
from typing import Optional, Dict, Any, List
from config import settings

# 尝试导入LangChain Ollama
try:
    from langchain_community.chat_models import ChatOllama
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    LANGCHAIN_OLLAMA_AVAILABLE = True
except ImportError:
    LANGCHAIN_OLLAMA_AVAILABLE = False

# 默认配置
DEFAULT_MODEL = "llama2"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 2000
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


def create_ollama_llm(
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    **kwargs
) -> Any:
    """
    创建Ollama LLM实例
    
    Args:
        temperature: 温度参数
        max_tokens: 最大token数
        model: 模型名称
        base_url: Ollama服务地址
        **kwargs: 其他参数
        
    Returns:
        ChatOllama实例
        
    Raises:
        ImportError: LangChain Ollama未安装
        RuntimeError: 配置错误或初始化失败
    """
    if not LANGCHAIN_OLLAMA_AVAILABLE:
        raise ImportError(
            "LangChain Ollama未安装，请运行: pip install langchain-community"
        )
    
    # 从配置中获取默认值，参数优先级更高
    ollama_config = settings.llm.ollama
    model = model or ollama_config.model
    base_url = base_url or ollama_config.base_url
    
    # 处理温度参数
    temp = temperature
    if temp is None:
        temp = ollama_config.temperature
    
    # 处理max_tokens参数（Ollama使用num_predict）
    tokens = max_tokens
    if tokens is None:
        tokens = ollama_config.max_tokens
    
    # 获取超时配置
    request_timeout = int(os.getenv("OLLAMA_REQUEST_TIMEOUT", "120"))
    
    try:
        # 创建ChatOllama实例
        llm = ChatOllama(
            model=model,
            temperature=temp,
            num_predict=tokens,
            base_url=base_url,
            timeout=request_timeout,
            **kwargs
        )
        return llm
    except Exception as e:
        raise RuntimeError(f"创建Ollama LLM实例失败: {str(e)}")


def chat_with_ollama(
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **kwargs
) -> str:
    """
    使用Ollama进行聊天
    
    Args:
        messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
        temperature: 温度参数
        max_tokens: 最大token数
        **kwargs: 其他参数
        
    Returns:
        AI回答内容
    """
    llm = create_ollama_llm(temperature=temperature, max_tokens=max_tokens, **kwargs)
    
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


def get_ollama_config() -> Dict[str, Any]:
    """
    获取Ollama配置信息
    
    Returns:
        配置信息字典
    """
    ollama_config = settings.llm.ollama
    return {
        "model": ollama_config.model,
        "base_url": ollama_config.base_url,
        "temperature": ollama_config.temperature,
        "max_tokens": ollama_config.max_tokens,
        "langchain_ollama_available": LANGCHAIN_OLLAMA_AVAILABLE
    }


def check_ollama_availability() -> Dict[str, Any]:
    """
    检查Ollama可用性
    
    Returns:
        可用性信息字典
    """
    ollama_config = settings.llm.ollama
    base_url = ollama_config.base_url
    
    result = {
        "provider": "ollama",
        "available": False,
        "langchain_ollama_available": LANGCHAIN_OLLAMA_AVAILABLE,
        "ollama_running": False,
        "base_url": base_url,
        "error": None
    }
    
    if not LANGCHAIN_OLLAMA_AVAILABLE:
        result["error"] = "LangChain Ollama未安装"
        return result
    
    # 检查Ollama服务是否运行
    import requests
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=5)
        if response.status_code == 200:
            result["ollama_running"] = True
            # 尝试获取可用模型列表
            try:
                models_data = response.json()
                result["available_models"] = [m["name"] for m in models_data.get("models", [])]
            except:
                result["available_models"] = []
        else:
            result["error"] = f"Ollama服务返回状态码: {response.status_code}"
    except requests.exceptions.ConnectionError:
        result["error"] = "无法连接到Ollama服务，请确保Ollama正在运行"
    except Exception as e:
        result["error"] = f"检查Ollama服务时出错: {str(e)}"
    
    if result["ollama_running"]:
        try:
            # 尝试创建LLM实例
            create_ollama_llm()
            result["available"] = True
        except Exception as e:
            result["error"] = f"Ollama配置错误: {str(e)}"
    
    return result


# 公开API
__all__ = [
    "create_ollama_llm",
    "chat_with_ollama",
    "get_ollama_config",
    "check_ollama_availability"
]