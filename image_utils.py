#!/usr/bin/env python3
"""
图像工具模块
提供图像编码相关的工具函数
"""

import base64
import io
from typing import Optional


def encode_image_to_base64(image_path: str) -> Optional[str]:
    """
    将图像文件编码为base64字符串
    
    Args:
        image_path: 图像文件路径
        
    Returns:
        base64编码的字符串，如果失败则返回None
    """
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
            return encoded_string
    except (IOError, OSError):
        return None


def encode_image_to_base64_from_pil(image) -> Optional[str]:
    """
    将PIL图像对象编码为base64字符串
    
    Args:
        image: PIL图像对象
        
    Returns:
        base64编码的字符串，如果失败则返回None
    """
    try:
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        encoded_string = base64.b64encode(buffered.getvalue()).decode("utf-8")
        return encoded_string
    except Exception:
        return None