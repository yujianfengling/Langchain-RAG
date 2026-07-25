"""
Qwen LLM实现
为支持Qwen模型预留接口
支持本地Ollama部署的Qwen模型
"""

import os
from typing import Optional, Dict, Any, List
from config import settings

# 尝试导入LangChain Ollama（Qwen通过Ollama运行）
try:
    from langchain_community.chat_models import ChatOllama
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    LANGCHAIN_OLLAMA_AVAILABLE = True
except ImportError:
    LANGCHAIN_OLLAMA_AVAILABLE = False


def create_qwen_llm(
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    **kwargs
) -> Any:
    """
    创建Qwen LLM实例（通过Ollama）
    
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
    
    # 从配置中获取默认值
    qwen_config = settings.llm.qwen
    model = model or qwen_config.model
    base_url = base_url or qwen_config.base_url
    
    # 处理温度参数
    temp = temperature
    if temp is None:
        temp = qwen_config.temperature
    
    # 处理max_tokens参数
    tokens = max_tokens
    if tokens is None:
        tokens = qwen_config.max_tokens
    
    # 获取超时配置
    request_timeout = int(os.getenv("QWEN_REQUEST_TIMEOUT", "120"))
    
    try:
        # 创建ChatOllama实例（Qwen通过Ollama运行）
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
        raise RuntimeError(f"创建Qwen LLM实例失败: {str(e)}")


def chat_with_qwen(
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **kwargs
) -> str:
    """
    使用Qwen进行聊天
    
    Args:
        messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
        temperature: 温度参数
        max_tokens: 最大token数
        **kwargs: 其他参数
        
    Returns:
        AI回答内容
    """
    llm = create_qwen_llm(temperature=temperature, max_tokens=max_tokens, **kwargs)
    
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


def get_qwen_config() -> Dict[str, Any]:
    """
    获取Qwen配置信息
    
    Returns:
        配置信息字典
    """
    qwen_config = settings.llm.qwen
    return {
        "model": qwen_config.model,
        "provider": qwen_config.provider,
        "base_url": qwen_config.base_url,
        "temperature": qwen_config.temperature,
        "max_tokens": qwen_config.max_tokens,
        "langchain_ollama_available": LANGCHAIN_OLLAMA_AVAILABLE
    }


def check_qwen_availability() -> Dict[str, Any]:
    """
    检查Qwen可用性
    
    Returns:
        可用性信息字典
    """
    qwen_config = settings.llm.qwen
    base_url = qwen_config.base_url
    
    result = {
        "provider": "qwen",
        "available": False,
        "langchain_ollama_available": LANGCHAIN_OLLAMA_AVAILABLE,
        "ollama_running": False,
        "base_url": base_url,
        "model": qwen_config.model,
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
            # 检查Qwen模型是否可用
            try:
                models_data = response.json()
                available_models = [m["name"] for m in models_data.get("models", [])]
                if qwen_config.model in available_models:
                    result["available"] = True
                else:
                    result["error"] = f"Qwen模型 {qwen_config.model} 未在Ollama中找到"
            except:
                result["error"] = "无法解析Ollama模型列表"
        else:
            result["error"] = f"Ollama服务返回状态码: {response.status_code}"
    except requests.exceptions.ConnectionError:
        result["error"] = "无法连接到Ollama服务，请确保Ollama正在运行"
    except Exception as e:
        result["error"] = f"检查Qwen可用性时出错: {str(e)}"
    
    return result


# 公开API
__all__ = [
    "create_qwen_llm",
    "chat_with_qwen",
    "get_qwen_config",
    "check_qwen_availability"
]