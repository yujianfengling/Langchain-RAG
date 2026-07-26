#!/usr/bin/env python3
"""
思维导图服务模块
负责思维导图生成的核心业务逻辑
使用LangChain框架调用DeepSeek API生成思维导图结构
"""

import json
import os
from typing import Dict, Any, Optional
from data_manager import get_all_content

# 尝试导入LangChain相关库
try:
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import JsonOutputParser
    from langchain_core.messages import HumanMessage, SystemMessage
    LANGCHAIN_AVAILABLE = True
except ImportError:
    ChatPromptTemplate = None  # 保证函数签名不炸
    JsonOutputParser = None
    HumanMessage = None
    SystemMessage = None
    ChatOpenAI = None
    LANGCHAIN_AVAILABLE = False
    print("警告: langchain相关库未安装，请运行 'pip install langchain langchain-openai' 安装")


def generate_mindmap(subject_id: Optional[str] = None) -> Dict[str, Any]:
    """
    生成思维导图
    
    Args:
        subject_id: 学科ID，如果提供则只生成该学科的思维导图
        
    Returns:
        思维导图树结构数据，包含节点和子节点
    """
    # 1. 知识内容读取逻辑
    content = get_all_content(subject_id)
    if not content:
        return {"error": "没有找到知识内容"}
    
    # 2. LangChain/LLM调用逻辑
    try:
        mindmap_data = _call_llm_for_mindmap(content)
    except Exception as e:
        return {"error": f"LLM调用失败: {str(e)}"}
    
    # 3. JSON解析逻辑
    try:
        if isinstance(mindmap_data, str):
            mindmap_data = json.loads(mindmap_data)
    except json.JSONDecodeError as e:
        return {"error": f"无法解析思维导图数据: {str(e)}"}
    
    # 4. 思维导图树结构生成逻辑
    mindmap_tree = _build_mindmap_tree(mindmap_data)
    
    return mindmap_tree


def _call_llm_for_mindmap(content: str) -> Dict[str, Any]:
    """
    使用LangChain调用DeepSeek API生成思维导图数据
    
    Args:
        content: 知识内容
        
    Returns:
        LLM返回的思维导图数据
        
    Raises:
        Exception: LLM调用失败时抛出异常
    """
    if not LANGCHAIN_AVAILABLE:
        raise ImportError("langchain相关库未安装")
    
    # 从环境变量读取DeepSeek API配置
    api_key = os.getenv("DEEPSEEK_API_KEY")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    temperature = float(os.getenv("DEEPSEEK_TEMPERATURE", "0.7"))
    
    if not api_key:
        raise ValueError("未设置DEEPSEEK_API_KEY环境变量")
    
    # 创建ChatOpenAI实例，使用DeepSeek API端点
    api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
    llm = ChatOpenAI(
        model=model,
        temperature=temperature,
        max_tokens=2000,
        openai_api_key=api_key,
        openai_api_base=api_base  # 从环境变量读取DeepSeek API端点
    )
    
    # 构造prompt模板
    prompt_template = _build_prompt_template()
    
    # 构造chain
    chain = prompt_template | llm
    
    # 调用LLM
    response = chain.invoke({"content": content})
    
    # 解析响应
    response_content = response.content
    
    # 尝试解析JSON
    try:
        return json.loads(response_content)
    except json.JSONDecodeError:
        # 如果LLM返回的不是有效JSON，尝试提取JSON部分
        import re
        json_match = re.search(r'\{.*\}', response_content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        else:
            raise ValueError("LLM返回的内容无法解析为JSON")


def _build_prompt_template() -> ChatPromptTemplate:
    """
    构造思维导图生成的prompt模板
    
    Returns:
        LangChain ChatPromptTemplate对象
    """
    system_message = "你是一个教育专家，擅长将知识内容整理成思维导图结构。请严格按照JSON格式返回。"
    
    human_message = """请将以下知识内容整理成思维导图结构。

要求：
1. 返回严格的JSON格式
2. 结构为：{{"name": "主题", "children": [{{"name": "子主题1", "children": [...]}}, {{"name": "子主题2", "children": [...]}}]}}
3. 主题应简洁明了，不超过20个字
4. 子主题应涵盖内容的关键点
5. 每个子主题可以有更细的子节点，但层级不超过3层
6. 节点名称应准确反映内容

知识内容：
{content}

请返回思维导图JSON结构："""
    
    return ChatPromptTemplate.from_messages([
        ("system", system_message),
        ("human", human_message)
    ])


def _build_mindmap_tree(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    构建思维导图树结构
    
    Args:
        data: 原始思维导图数据
        
    Returns:
        格式化后的思维导图树结构
    """
    def build_node(node_data: Dict[str, Any]) -> Dict[str, Any]:
        """递归构建节点"""
        node = {
            "name": node_data.get("name", "未命名节点"),
            "children": []
        }
        
        for child_data in node_data.get("children", []):
            child_node = build_node(child_data)
            node["children"].append(child_node)
        
        return node
    
    return build_node(data)