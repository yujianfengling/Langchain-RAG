"""
知识图谱构建模块
负责从文档中抽取知识三元组并构建知识图谱

数据模型（参考Operit记忆库设计）：
- nodes: 节点 = 记忆/知识点
  {id, label, content, type, tags, source, created_at}
- edges: 边 = 语义链接
  {source, target, relation, weight, description}
"""

import json
import os
import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# ========== 配置 ==========
# 教育领域Schema（参考GraphRAG设计）
NODE_TYPES = ["概念", "公式", "定理", "例题", "方法", "人物"]
RELATION_TYPES = ["包含", "推导", "应用", "属于", "定义", "举例", "前置条件", "相关"]

# 存储目录
_GRAPH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "knowledge_graph")

# ========== 三元组抽取 ==========

TRIPLE_EXTRACTION_PROMPT = """你是一名教育领域知识图谱构建专家。请从以下教材内容中提取知识三元组。

实体类型（节点类型）：{node_types}
关系类型（边类型）：{relation_types}

要求：
1. 只提取内容中明确提到的知识概念
2. 三元组格式：(实体A, 关系, 实体B)
3. 每个三元组必须能独立成立
4. 如果内容太少无法提取，返回空数组

输出格式（严格JSON，不要有其他文字）：
{{
  "triples": [
    {{"subject": "实体A", "predicate": "关系", "object": "实体B", "subject_type": "类型", "object_type": "类型"}}
  ]
}}

教材内容：
{chunk_text}
"""


def _call_llm_for_triples(chunk_text: str, model: str = None) -> List[Dict[str, Any]]:
    """
    调用LLM抽取三元组
    
    Args:
        chunk_text: 文本块
        model: 模型名称
    
    Returns:
        三元组列表
    """
    try:
        from services.model_service import get_model_provider
        provider = get_model_provider(model)
        
        prompt = TRIPLE_EXTRACTION_PROMPT.format(
            node_types="、".join(NODE_TYPES),
            relation_types="、".join(RELATION_TYPES),
            chunk_text=chunk_text[:3000]  # 限制长度
        )
        
        messages = [
            {"role": "system", "content": "你是教育知识图谱构建助手，只输出JSON。"},
            {"role": "user", "content": prompt}
        ]
        
        result = provider.chat(messages, temperature=0.1, max_tokens=2000)
        answer = result.get("answer", "")
        
        # 解析JSON
        triples = _parse_triple_json(answer)
        return triples
    except Exception as e:
        logger.warning(f"LLM三元组抽取失败: {e}")
        return []


def _parse_triple_json(text: str) -> List[Dict[str, Any]]:
    """从LLM输出中解析三元组JSON"""
    if not text:
        return []
    
    # 提取JSON部分
    json_match = re.search(r'\{[\s\S]*\}', text)
    if not json_match:
        return []
    
    try:
        data = json.loads(json_match.group())
        triples = data.get("triples", [])
        # 清洗字段
        cleaned = []
        for t in triples:
            subject = str(t.get("subject", "")).strip()
            predicate = str(t.get("predicate", "")).strip()
            obj = str(t.get("object", "")).strip()
            if subject and predicate and obj:
                cleaned.append({
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "subject_type": t.get("subject_type", "概念"),
                    "object_type": t.get("object_type", "概念"),
                })
        return cleaned
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"三元组JSON解析失败: {text[:100]}")
        return []


# ========== 图谱构建 ==========

def _merge_triples_to_graph(
    all_triples: List[Dict[str, Any]],
    source_info: Dict[str, str]
) -> Tuple[List[Dict], List[Dict]]:
    """
    将三元组合并为图谱（节点 + 边，实体消歧去重）
    
    Args:
        all_triples: 所有三元组
        source_info: 来源信息 {document: 页码}
    
    Returns:
        (nodes, edges)
    """
    nodes: Dict[str, Dict] = {}
    edges: List[Dict] = []
    edge_map: Dict[str, Dict] = {}  # edge_key -> edge 的索引，避免线性扫描
    
    for triple in all_triples:
        subj = triple["subject"]
        obj = triple["object"]
        pred = triple["predicate"]
        
        # 节点去重
        for name, ntype in [(subj, triple["subject_type"]), (obj, triple["object_type"])]:
            if name not in nodes:
                nodes[name] = {
                    "id": name,
                    "label": name,
                    "content": "",  # 后续填充摘要
                    "type": ntype if ntype in NODE_TYPES else "概念",
                    "tags": [],
                    "source": source_info.get("document", ""),
                    "created_at": datetime.now().isoformat(),
                    "weight": 1.0,
                }
            else:
                nodes[name]["weight"] = nodes[name].get("weight", 1.0) + 0.5
        
        # 边去重（同方向同关系），用dict索引O(1)查找
        edge_key = f"{subj}|{pred}|{obj}"
        if edge_key not in edge_map:
            edge = {
                "source": subj,
                "target": obj,
                "relation": pred,
                "weight": 1.0,
                "description": f"{subj} {pred} {obj}",
            }
            edge_map[edge_key] = edge
            edges.append(edge)
        else:
            edge_map[edge_key]["weight"] = edge_map[edge_key].get("weight", 1.0) + 1.0
    
    return list(nodes.values()), edges


def build_knowledge_graph(
    subject_id: str,
    model: str = None,
    max_chunks: int = 50
) -> Dict[str, Any]:
    """
    构建学科知识图谱
    
    Args:
        subject_id: 学科ID
        model: LLM模型
        max_chunks: 最多处理的文本块数
    
    Returns:
        图谱数据 {nodes, edges, stats}
    """
    from rag.rag_service import _safe_dir_name, _SUBJECTS_DATA_BASE
    from rag.document import load_documents
    from rag.text_splitter import split_documents
    
    subject_dir = Path(os.path.join(_SUBJECTS_DATA_BASE, subject_id))
    if not subject_dir.exists():
        raise FileNotFoundError(f"学科目录不存在: {subject_id}")
    
    # 1. 加载文档
    supported_extensions = {'.txt', '.md', '.pdf', '.docx'}
    file_paths = [
        f for f in subject_dir.iterdir()
        if f.is_file() and f.suffix.lower() in supported_extensions
    ]
    
    if not file_paths:
        raise ValueError(f"学科目录中没有文档: {subject_id}")
    
    documents = load_documents(file_paths)
    if not documents:
        raise ValueError("文档加载失败")
    
    # 2. 切分文档
    chunks = split_documents(documents, chunk_size=800, chunk_overlap=100)
    logger.info(f"文档切分为 {len(chunks)} 个文本块")
    
    # 3. 逐块抽取三元组
    all_triples = []
    source_info = {"document": ", ".join(f.name for f in file_paths)}
    processed = 0
    
    for i, chunk in enumerate(chunks):
        if processed >= max_chunks:
            break
        
        content = chunk.page_content if hasattr(chunk, 'page_content') else str(chunk)
        content = content.strip()
        
        # 过滤无实质内容的块：太短 或 多模态占位符（[图片]/[公式]）
        if len(content) < 15:
            continue
        if content.startswith('[图片]') or content.startswith('[公式]'):
            continue
        # 过滤纯标题/封面碎片（无概念内容）
        if len(content) < 30 and ('Dept.' in content or '@' in content or '电话' in content or '学时' in content):
            continue
        
        processed += 1
        triples = _call_llm_for_triples(content, model)
        all_triples.extend(triples)
        
        if processed % 10 == 0 or i == len(chunks) - 1:
            logger.info(f"已处理 {processed}/{max_chunks} 块，累计 {len(all_triples)} 个三元组")
    
    if not all_triples:
        logger.warning(f"学科 {subject_id} 未能提取到三元组（可能文档以图片/公式为主，文本内容不足）")
    
    # 4. 合并为图谱
    nodes, edges = _merge_triples_to_graph(all_triples, source_info)
    
    graph = {
        "subject_id": subject_id,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "chunks_processed": processed,
            "total_chunks": len(chunks),
            "triples_extracted": len(all_triples),
            "created_at": datetime.now().isoformat(),
        }
    }
    
    # 5. 持久化
    _save_graph(subject_id, graph)
    
    return graph


def _save_graph(subject_id: str, graph: Dict[str, Any]) -> Path:
    """保存图谱到本地JSON文件"""
    from rag.rag_service import _safe_dir_name
    graph_dir = Path(_GRAPH_DIR)
    graph_dir.mkdir(parents=True, exist_ok=True)
    
    path = graph_dir / f"{_safe_dir_name(subject_id)}.json"
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
    
    logger.info(f"知识图谱已保存: {path}")
    return path


def load_knowledge_graph(subject_id: str) -> Optional[Dict[str, Any]]:
    """
    加载学科知识图谱
    
    Args:
        subject_id: 学科ID
    
    Returns:
        图谱数据，不存在返回None
    """
    from rag.rag_service import _safe_dir_name
    path = Path(_GRAPH_DIR) / f"{_safe_dir_name(subject_id)}.json"
    
    if not path.exists():
        return None
    
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def list_graphs() -> List[Dict[str, Any]]:
    """列出所有已构建的知识图谱"""
    graph_dir = Path(_GRAPH_DIR)
    if not graph_dir.exists():
        return []
    
    from rag.rag_service import _original_subject_id
    graphs = []
    for f in graph_dir.glob("*.json"):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            graphs.append({
                "subject_id": data.get("subject_id", _original_subject_id(f.stem)),
                "node_count": data.get("stats", {}).get("node_count", 0),
                "edge_count": data.get("stats", {}).get("edge_count", 0),
                "created_at": data.get("stats", {}).get("created_at", ""),
            })
        except Exception as e:
            logger.warning(f"加载图谱失败: {f}, {e}")
    return graphs


# ========== 查询接口 ==========

def query_graph(subject_id: str, node: str = None, depth: int = 1) -> Dict[str, Any]:
    """
    查询图谱（支持按节点展开）
    
    Args:
        subject_id: 学科ID
        node: 起始节点（可选，None则返回全部）
        depth: 展开深度
    
    Returns:
        子图数据
    """
    graph = load_knowledge_graph(subject_id)
    if graph is None:
        return {"nodes": [], "edges": [], "stats": {"node_count": 0, "edge_count": 0}}
    
    if node is None:
        return graph
    
    # 按节点BFS展开
    visited = {node}
    frontier = {node}
    for _ in range(depth):
        next_frontier = set()
        for e in graph["edges"]:
            if e["source"] in frontier and e["target"] not in visited:
                next_frontier.add(e["target"])
            if e["target"] in frontier and e["source"] not in visited:
                next_frontier.add(e["source"])
        visited.update(next_frontier)
        frontier = next_frontier
    
    sub_nodes = [n for n in graph["nodes"] if n["id"] in visited]
    sub_edges = [e for e in graph["edges"] if e["source"] in visited and e["target"] in visited]
    
    return {
        "subject_id": subject_id,
        "nodes": sub_nodes,
        "edges": sub_edges,
        "stats": {"node_count": len(sub_nodes), "edge_count": len(sub_edges)},
    }
