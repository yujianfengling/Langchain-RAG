# 模型配置文件
# 统一管理所有AI模型的配置信息
# 优先级：环境变量(.env) > 本地未跟踪配置 config/local_secrets.json
# 本文件**不含任何真实密钥**（2026-09-26 清理，详见 docs/CREDENTIALS.md）

import os
from typing import Dict, Optional

# 默认**文本**模型
# 2026-09-25 变更：MiMo 会员到期（凭证被供应商拒绝），默认文本模型统一为 DeepSeek。
# 可用 LLM_MODEL 覆盖，但只接受下方 TEXT_MODELS 中的文本模型；
# 未知取值（含已不可用的 mimo-asr/vision 等）一律回落到 DEFAULT_MODEL，
# 且**不会**自动回退到 MiMo。
DEFAULT_MODEL = "deepseek"

# 可作为"默认文本模型"的提供方（不含视觉 / 语音专用提供方）
TEXT_MODELS = ("deepseek", "mimo", "qwen")

# 模型配置（API密钥优先从环境变量读取，内置值作为团队共享后备）
MODEL_CONFIG = {
    "mimo": {
        "name": "小米MiMo",
        "base_url": os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"),
        "api_key": "",
        "model_name": os.getenv("MIMO_MODEL", "mimo-v2.5-pro"),
        "vision_model_name": "mimo-v2.5",  # v2.5支持识图，v2.5-pro不支持
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "api_key": "",
        "model_name": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        # 视觉：DeepSeek 的对话模型同样接受 OpenAI 兼容的 image_url 输入
        # （实测 deepseek-chat / deepseek-flash / deepseek-reasoner 均可识图）。
        # 默认与文本模型同名，可用 DEEPSEEK_VISION_MODEL 单独指定。
        "vision_model_name": os.getenv("DEEPSEEK_VISION_MODEL", "")
                                      or os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    },
    "qwen": {
        "name": "通义千问",
        "base_url": os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "api_key": "",
        "model_name": os.getenv("DASHSCOPE_MODEL", "qwen-plus"),
    },
    "mimo-asr": {
        "name": "小米MiMo语音识别",
        "base_url": os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"),
        "api_key": "",
        "model_name": os.getenv("MIMO_ASR_MODEL", "mimo-v2.5-asr"),
    },
    "qwen-vision": {
        "name": "通义千问视觉",
        "base_url": os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "api_key": "",
        "model_name": os.getenv("DASHSCOPE_VISION_MODEL", "qwen-vl-plus"),
        "is_vision": True,
    },
}

# 环境变量映射
ENV_VAR_MAPPING = {
    "mimo": "MIMO_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "qwen-vision": "DASHSCOPE_API_KEY",
    "mimo-vision": "MIMO_API_KEY",  # 已弃用
    "mimo-asr": "MIMO_API_KEY",     # ASR 复用 MiMo API Key
}

# 本地密钥来源（**不再内置任何真实密钥**）
#
# 安全说明（2026-09-26）：
# 此前本文件内置了团队共享的后备密钥，且本文件被 Git 跟踪 → 密钥进入了仓库与历史。
# 现改为只从下面两处读取，两者都不进仓库：
#   1) 环境变量 / .env（优先；.env 已被 .gitignore 忽略）
#   2) 本地未跟踪配置文件 config/local_secrets.json（见 local_secrets.example.json）
# 仓库中只保留不含密钥的示例配置。
LOCAL_SECRETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "local_secrets.json")

# 兼容旧名称：保留一个**空**的内置表，避免外部代码 import 时报错
_BUILTIN_API_KEYS: Dict[str, str] = {}


def _load_local_secrets() -> Dict[str, str]:
    """
    读取本地未跟踪的密钥文件（不存在则返回空字典）

    文件格式（values 支持 "env:NAME" 间接引用）：
        {"api_keys": {"deepseek": "sk-...", "mimo": "env:MIMO_API_KEY"}}
    """
    try:
        import json
        with open(LOCAL_SECRETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        keys = data.get("api_keys") or {}
        return {k: v for k, v in keys.items() if isinstance(v, str)}
    except FileNotFoundError:
        return {}
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"读取本地密钥文件失败（忽略）：{e}")
        return {}


_LOCAL_SECRETS_CACHE: Optional[Dict[str, str]] = None


def _local_secret(model_name: str) -> str:
    """从本地未跟踪配置里取密钥（进程内缓存）"""
    global _LOCAL_SECRETS_CACHE
    if _LOCAL_SECRETS_CACHE is None:
        _LOCAL_SECRETS_CACHE = _load_local_secrets()
    value = _LOCAL_SECRETS_CACHE.get(model_name, "")
    if value.startswith("env:"):
        return os.getenv(value[4:], "") or ""
    return value


def reload_local_secrets() -> None:
    """清空本地密钥缓存（改完 local_secrets.json 后调用，测试也会用到）"""
    global _LOCAL_SECRETS_CACHE
    _LOCAL_SECRETS_CACHE = None


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

    优先级（两者都不进仓库）：
        1. 环境变量（含 .env，已被 .gitignore 忽略）
        2. 本地未跟踪配置 config/local_secrets.json
    没有配置时返回空串 —— 调用方据此把该提供方标记为"未配置"，
    **不会**再回落到任何内置/共享密钥。
    """
    if model_name is None:
        model_name = DEFAULT_MODEL

    env_var = ENV_VAR_MAPPING.get(model_name)

    # 1) 环境变量
    if env_var:
        api_key = os.getenv(env_var)
        if api_key and _is_valid_api_key(api_key):
            return api_key

    # 2) 本地未跟踪配置
    local = _local_secret(model_name)
    if local and _is_valid_api_key(local):
        return local

    return ""


def _is_valid_api_key(api_key: str) -> bool:
    """
    校验API密钥格式是否合法：
    - 必须为纯ASCII（请求头只能编码latin-1）
    - 排除占位符（如"你的API密钥填这里"）
    """
    if not api_key:
        return False
    try:
        api_key.encode('ascii')
    except UnicodeEncodeError:
        return False
    # 排除常见占位符
    if "你的" in api_key or "your" in api_key.lower() or "xxx" in api_key.lower():
        return False
    return True


def get_default_model() -> str:
    """
    获取默认**文本**模型名称（全后端唯一的默认模型解析入口）

    优先级：环境变量 LLM_MODEL > DEFAULT_MODEL
    - 环境变量为未知值 / 视觉语音专用提供方时，回落到 DEFAULT_MODEL（并打印警告）
    - 不做任何"自动回退到 MiMo"的处理：MiMo 只是可选提供方
    """
    raw = (os.getenv("LLM_MODEL") or "").strip().lower()
    if not raw:
        return DEFAULT_MODEL
    if raw not in TEXT_MODELS:
        import logging
        logging.getLogger(__name__).warning(
            f"LLM_MODEL={raw!r} 不是可用的文本模型（可选：{list(TEXT_MODELS)}），"
            f"已回落到默认文本模型 {DEFAULT_MODEL!r}")
        return DEFAULT_MODEL
    return raw


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
