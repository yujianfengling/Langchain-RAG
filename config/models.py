# 模型配置文件
# 统一管理所有AI模型的配置信息
# 优先级：环境变量(.env) > 此文件内置默认值

import os

# 默认模型
DEFAULT_MODEL = "mimo"

# 模型配置（内置真实API参数，clone后无需额外配置即可运行）
MODEL_CONFIG = {
    "mimo": {
        "name": "小米MiMo",
        "base_url": os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"),
        "api_key": "",  # 通过 get_model_api_key() 从环境变量或内置默认值读取
        "model_name": os.getenv("MIMO_MODEL", "mimo-v2.5-pro"),
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "api_key": "",
        "model_name": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    },
    "qwen": {
        "name": "通义千问",
        "base_url": os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "api_key": "",
        "model_name": os.getenv("DASHSCOPE_MODEL", "qwen-plus"),
    },
}

# 环境变量映射
ENV_VAR_MAPPING = {
    "mimo": "MIMO_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
}

# 内置默认API密钥（私有仓库，clone后无需配置.env即可运行）
# 优先级：环境变量 > 此处内置值
_BUILTIN_API_KEYS = {
    "mimo": "REDACTED_API_KEY",
    "deepseek": "REDACTED_API_KEY",
    "qwen": "",  # 通义千问暂未配置，需用户自行提供
}


def get_model_config(model_name: str = None) -> dict:
    """获取模型配置"""
    if model_name is None:
        model_name = DEFAULT_MODEL

    if model_name not in MODEL_CONFIG:
        raise ValueError(f"不支持的模型: {model_name}，支持的模型: {list(MODEL_CONFIG.keys())}")

    return MODEL_CONFIG[model_name]


def get_model_api_key(model_name: str = None) -> str:
    """
    获取模型API密钥
    优先级：环境变量(.env) > 内置默认值
    """
    if model_name is None:
        model_name = DEFAULT_MODEL

    env_var = ENV_VAR_MAPPING.get(model_name)

    # 1. 优先从环境变量读取
    if env_var:
        api_key = os.getenv(env_var)
        if api_key:
            return api_key

    # 2. 使用内置默认值
    return _BUILTIN_API_KEYS.get(model_name, "")


def get_default_model() -> str:
    """获取默认模型名称"""
    return os.getenv("LLM_MODEL", DEFAULT_MODEL)


def list_models() -> list:
    """列出所有可用模型"""
    return [
        {
            "id": model_id,
            "name": config["name"],
            "model_name": config["model_name"],
            "has_key": bool(get_model_api_key(model_id)),
        }
        for model_id, config in MODEL_CONFIG.items()
    ]
