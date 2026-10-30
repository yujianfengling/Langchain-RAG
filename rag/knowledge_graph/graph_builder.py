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

# 存储目录（**运行时解析**）
#
# 修复（2026-10-01）：此处原先硬编码 `<项目根>/data/knowledge_graph`，绕过了
# `EDU_DATA_DIR`，隔离运行时图谱会落到正式 data/。与向量库/BM25 采用同一套解析方式。
def _graph_dir() -> str:
    from config.path_config import get_data_dir
    return str(get_data_dir() / "knowledge_graph")

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


def _call_llm_for_triples(chunk_text: str, model: str = None, chunk_index: int = None) -> List[Dict[str, Any]]:
    """
    调用LLM抽取三元组（防线1）

    Args:
        chunk_text: 文本块内容
        model: LLM模型（None 时由 build_knowledge_graph 决定 GRAPH_MODEL/默认）
        chunk_index: chunk 编号（用于失败定位，可选）

    Returns:
        三元组列表；失败返回 []（不抛异常，不中断整个任务）
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
        idx = f" chunk={chunk_index}" if chunk_index is not None else ""
        logger.warning(f"LLM三元组抽取失败{idx}: {type(e).__name__}: {str(e)[:200]}")
        return []


def _parse_triple_json(text: str) -> List[Dict[str, Any]]:
    """
    从LLM输出中解析三元组JSON（增强兼容）

    支持：
    - 标准对象：{"triples": [...]} 或 {"triples":[...]} 带前后解释文字
    - 顶层数组：[...]
    - Markdown 代码块包裹（```json ... ```）
    - 解析失败返回 []，不抛异常
    """
    if not text:
        return []

    def _extract(data) -> List[Dict[str, Any]]:
        triples = data if isinstance(data, list) else data.get("triples", [])
        if not isinstance(triples, list):
            return []
        cleaned = []
        for t in triples:
            if not isinstance(t, dict):
                continue
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

    # 1. 剥离 markdown 代码块
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    # 2. 优先尝试顶层数组 [ ... ]
    try:
        data = json.loads(stripped)
        if isinstance(data, (dict, list)):
            return _extract(data)
    except (json.JSONDecodeError, ValueError):
        pass

    # 3. 从文本中提取 JSON 片段（贪婪匹配完整块：对象含内嵌数组/对象）
    #    优先对象 {"triples":[...]}，其次顶层数组 [...]（取最长匹配，避免截断内嵌）
    m = re.search(r'\{[\s\S]*\}', stripped)
    if m:
        try:
            data = json.loads(m.group())
            if isinstance(data, (dict, list)):
                return _extract(data)
        except (json.JSONDecodeError, ValueError):
            pass
    m = re.search(r'\[[\s\S]*\]', stripped)
    if m:
        try:
            data = json.loads(m.group())
            if isinstance(data, list):
                return _extract(data)
        except (json.JSONDecodeError, ValueError):
            pass

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
    max_chunks: int = 50,
    progress_cb=None
) -> Dict[str, Any]:
    """
    构建学科知识图谱（三道质量防线）

    流程：
    1. 直接复用知识库已切分的 chunk（index.pkl 中的 documents 即为 RAG 使用的 chunk，
       保证 KG 与 RAG 数据完全一致；不重新解析 PDF、不触发 Vision、不重建 Embedding）
    2. LLM抽取三元组（防线1：Prompt正负例+粒度控制）
    3. 合并为图谱
    4. 后处理过滤（防线2+3：归一化/停用词/孤立节点/自环）
    5. 原子持久化

    Args:
        subject_id: 学科ID
        model: LLM模型（优先 GRAPH_MODEL 环境变量，其次该参数，最后全局默认）
        max_chunks: 最多处理的文本块数（LLM 调用规模保护）
        progress_cb: 进度回调 (processed_chunks, total_chunks)，可选
    """
    import os
    import pickle
    from rag.rag_service import _safe_dir_name

    # 独立模型配置：GRAPH_MODEL 优先（不误用聊天模型）
    if model is None:
        model = os.getenv("GRAPH_MODEL", None)

    # 1. 复用知识库已切分的 chunk（index.pkl 的 documents 即 RAG 构建时持久化的 split 结果）
    # 2026-10-31：改走 get_data_dir()（EDU_DATA_DIR 感知）。原实现拼项目根的
    # data/vector_store，真实索引在 EDU_DATA_DIR 下时永远"知识库尚未构建"
    # ——知识图谱因此从未在演示环境跑通过。
    from config.path_config import get_data_dir
    vs_base = Path(get_data_dir()) / "vector_store"
    safe_name = _safe_dir_name(subject_id)
    index_pkl = vs_base / safe_name / "index.pkl"

    if not os.path.exists(index_pkl):
        raise FileNotFoundError(f"知识库尚未构建或索引缺失，请先构建知识库: {subject_id}")

    with open(index_pkl, "rb") as f:
        stored = pickle.load(f)
    documents = stored.get("documents", [])
    if not documents:
        raise ValueError("知识库中没有可用文档，请先构建知识库")

    # 2. 直接使用已切分的 chunk（不再二次 split → 与 RAG 完全一致）
    chunks = documents
    logger.info(f"知识图谱复用知识库 chunk: {len(chunks)} 个（与 RAG 一致）")

    # 过滤纯标题/封面碎片（无概念内容）
    def _skip(content: str) -> bool:
        if len(content) < 15:
            return True
        if content.startswith('[图片]') or content.startswith('[公式]'):
            return True
        if len(content) < 30 and ('Dept.' in content or '@' in content or '电话' in content or '学时' in content):
            return True
        return False

    # 3. 逐块抽取三元组（防线1：Prompt + LLM验证）
    all_triples = []
    source_info = {"document": safe_name}
    processed = 0
    total_candidates = len(chunks)
    failed_chunks = []

    for i, chunk in enumerate(chunks):
        if processed >= max_chunks:
            break

        content = chunk.page_content if hasattr(chunk, 'page_content') else str(chunk)
        content = content.strip()

        # 前置过滤：无实质内容的块
        if _skip(content):
            continue

        processed += 1
        triples = _call_llm_for_triples(content, model, chunk_index=processed)
        if not triples:
            failed_chunks.append(processed)
        all_triples.extend(triples)

        if progress_cb is not None:
            progress_cb(processed, total_candidates)

        if processed % 10 == 0 or i == len(chunks) - 1:
            logger.info(f"已处理 {processed}/{max_chunks} 块，累计 {len(all_triples)} 个三元组")

    if failed_chunks:
        logger.warning(f"LLM抽取失败/空结果的 chunk 编号: {failed_chunks}")

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
    """
    原子保存图谱到本地JSON文件

    流程：写 graph_building.tmp → 校验 JSON/结构 → os.replace 原子改名 → graph.json
    失败：删除 tmp，保留旧 graph.json（不污染）
    """
    from rag.rag_service import _safe_dir_name
    graph_dir = Path(_graph_dir())
    graph_dir.mkdir(parents=True, exist_ok=True)

    path = graph_dir / f"{_safe_dir_name(subject_id)}.json"
    tmp_path = graph_dir / f"{_safe_dir_name(subject_id)}_building.tmp"

    try:
        # 1. 写入临时文件
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(graph, f, ensure_ascii=False, indent=2)

        # 2. 校验（可反序列化 + 结构完整）
        with open(tmp_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict) or "nodes" not in data or "edges" not in data:
            raise ValueError("图谱数据不完整（缺 nodes/edges）")

        # 3. 原子替换
        os.replace(str(tmp_path), str(path))
        logger.info(f"知识图谱已保存: {path}")
        return path
    except Exception as e:
        # 失败：删除 tmp，保留旧 graph.json
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        logger.error(f"知识图谱保存失败（旧图谱保留）: {str(e)}")
        raise


def load_knowledge_graph(subject_id: str) -> Optional[Dict[str, Any]]:
    """加载学科知识图谱"""
    from rag.rag_service import _safe_dir_name
    path = Path(_graph_dir()) / f"{_safe_dir_name(subject_id)}.json"
    
    if not path.exists():
        return None
    
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def list_graphs() -> List[Dict[str, Any]]:
    """列出所有已构建的知识图谱"""
    graph_dir = Path(_graph_dir())
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


# ============ 后台任务辅助（供路由层使用） ============

# LLM 调用规模保护：单个知识图谱最多处理的文本块数（可通过环境变量覆盖）
MAX_GRAPH_CHUNKS = int(os.getenv("MAX_GRAPH_CHUNKS", "50"))


def submit_graph_build(subject_id: str, model: str = None):
    """
    提交知识图谱后台构建任务（异步执行，不阻塞事件循环）

    Returns:
        (accepted, status)：
        - accepted=True: 任务已提交（queued/building）
        - accepted=False: 未接受（已构建中 busy，或学科不存在）
    """
    from .task_manager import get_graph_manager

    def _run(sid, progress_cb):
        # 复用知识库持久化数据，不触发 Vision/重新解析
        build_knowledge_graph(sid, model=model, max_chunks=MAX_GRAPH_CHUNKS,
                              progress_cb=progress_cb)

    return get_graph_manager().submit(subject_id, _run)


def get_graph_build_status(subject_id: str):
    """获取知识图谱构建任务状态"""
    from .task_manager import get_graph_manager
    return get_graph_manager().get_status(subject_id)


def reset_graph_build_manager():
    """后端重启时调用：将遗留 building/queued 状态恢复为 interrupted"""
    from .task_manager import get_graph_manager
    get_graph_manager().reset_stale()
