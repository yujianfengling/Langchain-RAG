"""
知识图谱构建模块
负责从文档中提取知识三元组并构建知识图谱

数据模型（参考Operit记忆库设计）：
- nodes: 节点 = 记忆/知识点
  {id, label, content, type, tags, source, created_at}
- edges: 边 = 语义链接
  {source, target, relation, weight, description}

v2.1: 三道质量防线
  防线1：Prompt优化（正负例+粒度控制）
  防线2：后处理过滤（长度/数值/停用词/孤立节点）
  防线3：实体归一化（去括号/空格后去重）
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
NODE_TYPES = ["概念", "公式", "定理", "方法", "人物"]
RELATION_TYPES = ["包含", "推导", "应用", "属于", "定义", "举例", "前置条件", "相关"]

# 存储目录
_GRAPH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "knowledge_graph")

# ========== 防线2：后处理过滤 ==========

# 停用词：太泛的实体不应进入图谱
STOPWORD_ENTITIES = {
    "方法", "问题", "内容", "情况", "部分", "方面", "条件", "过程", "结果",
    "形式", "方式", "类型", "阶段", "状态", "现象", "原理", "基础", "概念",
    "教材", "课本", "章节", "知识", "学生", "教师", "老师", "课程",
}

# 数值+单位模式：匹配具体数据而非概念
_NUMERIC_PATTERN = re.compile(
    r'^[\d\s\.\,\×\*\/\-\+\(\)]+'
    r'(公斤|kg|cm|mm|m|米|厘米|毫米|千米|km|页|学时|分|秒|度|°|%|倍|个|次|元)$',
    re.IGNORECASE
)

# 纯数字
_PURE_NUMBER = re.compile(r'^[\d\s\.\,\×\*\/\-\+\(\)]+$')


def _normalize_entity(name: str) -> str:
    """
    实体归一化（防线3）
    - 去除括号内容（如 "欧拉公式（Euler）" → "欧拉公式"）
    - 去除多余空格
    - 去除首尾标点
    """
    name = name.strip()
    # 去除中文括号及其内容
    name = re.sub(r'（[^）]*）', '', name)
    # 去除英文括号及其内容
    name = re.sub(r'\([^)]*\)', '', name)
    # 去除多余空格
    name = re.sub(r'\s+', '', name)
    # 去除首尾标点
    name = name.strip('，。、；：""''《》【】[]{}|/\\·')
    return name


def _is_valid_entity(name: str) -> bool:
    """
    判断实体是否有效（防线2）
    过滤噪音实体，保留真正有意义的知识概念
    """
    normalized = _normalize_entity(name)
    
    # 太短（<2字符）或太长（>20字符）→ 过滤
    if len(normalized) < 2 or len(normalized) > 20:
        return False
    
    # 停用词 → 过滤
    if normalized in STOPWORD_ENTITIES:
        return False
    
    # 纯数字/数值+单位 → 过滤
    if _PURE_NUMBER.match(normalized):
        return False
    if _NUMERIC_PATTERN.match(normalized):
        return False
    
    # 包含明显碎片特征 → 过滤
    # 纯英文缩写（少于4个字符且全是大写字母，如 "Dept" "Mech"）
    if re.match(r'^[A-Z]{2,4}$', normalized):
        return False
    
    # 包含电话/邮箱/日期特征 → 过滤
    if re.search(r'\d{5,}|@|电话|邮箱|教授|副教授|讲师', normalized):
        return False
    
    return True


def _filter_and_normalize_graph(nodes: List[Dict], edges: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """
    后处理过滤（防线2+3）
    
    1. 归一化实体名（去括号、去空格）
    2. 合并归一化后重复的实体
    3. 过滤无效实体
    4. 移除孤立节点（无任何边连接）
    5. 移除自环边（source == target）
    """
    # 第1步：建立归一化映射 raw_name → normalized_name
    name_map: Dict[str, str] = {}
    for node in nodes:
        raw = node["id"]
        normalized = _normalize_entity(raw)
        if normalized and _is_valid_entity(normalized):
            # 优先保留较短的标准名称
            if normalized not in name_map.values():
                name_map[raw] = normalized
            else:
                name_map[raw] = normalized  # 归一化后已存在，合并
    
    # 第2步：重建节点（按归一化名去重，合并权重）
    merged_nodes: Dict[str, Dict] = {}
    for node in nodes:
        normalized = name_map.get(node["id"])
        if not normalized:
            continue  # 过滤无效实体
        
        if normalized in merged_nodes:
            # 合并权重
            merged_nodes[normalized]["weight"] = merged_nodes[normalized].get("weight", 1.0) + node.get("weight", 1.0)
        else:
            node_copy = node.copy()
            node_copy["id"] = normalized
            node_copy["label"] = normalized
            merged_nodes[normalized] = node_copy
    
    # 第3步：重建边（用归一化名，过滤自环）
    valid_node_ids = set(merged_nodes.keys())
    filtered_edges = []
    edge_set = set()
    
    for edge in edges:
        src = name_map.get(edge["source"], edge["source"])
        tgt = name_map.get(edge["target"], edge["target"])
        
        # 自环过滤
        if src == tgt:
            continue
        # 无效节点过滤
        if src not in valid_node_ids or tgt not in valid_node_ids:
            continue
        # 去重
        edge_key = f"{src}|{edge['relation']}|{tgt}"
        if edge_key in edge_set:
            # 已存在，加权
            for e in filtered_edges:
                if f"{e['source']}|{e['relation']}|{e['target']}" == edge_key:
                    e["weight"] = e.get("weight", 1.0) + 1.0
                    break
            continue
        
        edge_set.add(edge_key)
        new_edge = edge.copy()
        new_edge["source"] = src
        new_edge["target"] = tgt
        new_edge["description"] = f"{src} {edge['relation']} {tgt}"
        filtered_edges.append(new_edge)
    
    # 第4步：移除孤立节点（没有任何边连接的节点）
    connected_nodes = set()
    for e in filtered_edges:
        connected_nodes.add(e["source"])
        connected_nodes.add(e["target"])
    
    final_nodes = [n for n in merged_nodes.values() if n["id"] in connected_nodes]
    
    return final_nodes, filtered_edges


# ========== 防线1：优化的三元组抽取Prompt ==========

TRIPLE_EXTRACTION_PROMPT = """你是一名教育知识图谱专家。请从教材内容中提取**学科核心概念**之间的关系。

实体类型：{node_types}
关系类型：{relation_types}

## 提取规则（严格遵守）

1. **只提取概念/定理/公式/方法级别的实体**，不要提取：
   - ❌ 具体数值（如"600公斤"、"1×2cm"、"79个字符"）
   - ❌ 例题中的具体物体描述（如"短木杆"、"长木杆"、"矩形薄板"）
   - ❌ 太泛的词（如"方法"、"问题"、"内容"、"情况"）
   - ❌ 章节标题/页码/教师信息

2. **实体名称要求**：
   - 用最简短的标准名称（如"欧拉公式"而非"欧拉公式·压杆的长度因数"）
   - 不带括号注释（如"临界力"而非"临界力（Critical Force）"）

3. **每个chunk最多提取5个三元组**，宁缺毋滥

4. **如果内容主要是例题/数据/图片描述/封面信息，返回空数组**

## 正例 ✅
("欧拉公式", "定义", "临界力") — 两个都是学科核心概念
("应力状态", "包含", "主应力") — 概念间的层级关系
("压杆稳定", "前置条件", "材料力学") — 学科依赖关系

## 负例 ❌（不要提取这种）
("短木杆", "应用", "支承重物") — ❌ 具体例子，不是概念
("600公斤", "相关", "重量") — ❌ 具体数值
("横截面为1×2cm", "包含", "高度只有3cm") — ❌ 碎片描述

## 输出格式（严格JSON）：
{{"triples": [{{"subject": "...", "predicate": "...", "object": "...", "subject_type": "...", "object_type": "..."}}]}}

教材内容：
{chunk_text}
"""


def _call_llm_for_triples(chunk_text: str, model: str = None) -> List[Dict[str, Any]]:
    """
    调用LLM抽取三元组（防线1）
    """
    try:
        from services.model_service import get_model_provider
        provider = get_model_provider(model)
        
        prompt = TRIPLE_EXTRACTION_PROMPT.format(
            node_types="、".join(NODE_TYPES),
            relation_types="、".join(RELATION_TYPES),
            chunk_text=chunk_text[:3000]
        )
        
        messages = [
            {"role": "system", "content": "你是教育知识图谱构建助手，只输出JSON。"},
            {"role": "user", "content": prompt}
        ]
        
        result = provider.chat(messages, temperature=0.1, max_tokens=2000)
        answer = result.get("answer", "")
        
        triples = _parse_triple_json(answer)
        
        # 额外过滤：对LLM返回的三元组做实体验证
        filtered = []
        for t in triples:
            subj = _normalize_entity(t["subject"])
            obj = _normalize_entity(t["object"])
            if _is_valid_entity(subj) and _is_valid_entity(obj) and subj != obj:
                t["subject"] = subj
                t["object"] = obj
                filtered.append(t)
        
        return filtered
    except Exception as e:
        logger.warning(f"LLM三元组抽取失败: {e}")
        return []


def _parse_triple_json(text: str) -> List[Dict[str, Any]]:
    """从LLM输出中解析三元组JSON"""
    if not text:
        return []
    
    json_match = re.search(r'\{[\s\S]*\}', text)
    if not json_match:
        return []
    
    try:
        data = json.loads(json_match.group())
        triples = data.get("triples", [])
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
    """
    nodes: Dict[str, Dict] = {}
    edges: List[Dict] = []
    edge_map: Dict[str, Dict] = {}
    
    for triple in all_triples:
        subj = triple["subject"]
        obj = triple["object"]
        pred = triple["predicate"]
        
        for name, ntype in [(subj, triple["subject_type"]), (obj, triple["object_type"])]:
            if name not in nodes:
                nodes[name] = {
                    "id": name,
                    "label": name,
                    "content": "",
                    "type": ntype if ntype in NODE_TYPES else "概念",
                    "tags": [],
                    "source": source_info.get("document", ""),
                    "created_at": datetime.now().isoformat(),
                    "weight": 1.0,
                }
            else:
                nodes[name]["weight"] = nodes[name].get("weight", 1.0) + 0.5
        
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
    构建学科知识图谱（三道质量防线）
    
    流程：
    1. 加载文档 → 切分 → 过滤占位符
    2. LLM抽取三元组（防线1：Prompt正负例+粒度控制）
    3. 合并为图谱
    4. 后处理过滤（防线2+3：归一化/停用词/孤立节点/自环）
    5. 持久化
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
    
    # 3. 逐块抽取三元组（防线1：Prompt + LLM验证）
    all_triples = []
    source_info = {"document": ", ".join(f.name for f in file_paths)}
    processed = 0
    
    for i, chunk in enumerate(chunks):
        if processed >= max_chunks:
            break
        
        content = chunk.page_content if hasattr(chunk, 'page_content') else str(chunk)
        content = content.strip()
        
        # 前置过滤：无实质内容的块
        if len(content) < 15:
            continue
        if content.startswith('[图片]') or content.startswith('[公式]'):
            continue
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
    raw_nodes, raw_edges = _merge_triples_to_graph(all_triples, source_info)
    
    # 5. 后处理过滤（防线2+3）
    nodes, edges = _filter_and_normalize_graph(raw_nodes, raw_edges)
    
    filtered_count = len(raw_nodes) - len(nodes)
    if filtered_count > 0:
        logger.info(f"后处理过滤：原始{len(raw_nodes)}节点 → 过滤后{len(nodes)}节点（移除{filtered_count}个噪音节点）")
    
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
            "nodes_before_filter": len(raw_nodes),
            "nodes_filtered": filtered_count,
            "created_at": datetime.now().isoformat(),
        }
    }
    
    # 6. 持久化
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
    """加载学科知识图谱"""
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


def query_graph(subject_id: str, node: str = None, depth: int = 1) -> Dict[str, Any]:
    """查询图谱（支持按节点展开）"""
    graph = load_knowledge_graph(subject_id)
    if graph is None:
        return {"nodes": [], "edges": [], "stats": {"node_count": 0, "edge_count": 0}}
    
    if node is None:
        return graph
    
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
