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


# ========== 原文摘录（公式优先，其次定义） ==========

_FORMULA_BLOCK_RE = re.compile(r"\$\$[\s\S]{1,600}?\$\$|\\\[[\s\S]{1,600}?\\\]")
_INLINE_MATH_RE = re.compile(r"(?<!\$)\$[^$\n]{1,220}\$(?!\$)|\\\([\s\S]{1,220}?\\\)")
_DEF_CUE_RE = re.compile(r"定义为|的定义|是指|称为|叫做|称作|指的是|定义\s*[:：]")
_EQ_NOISE_RE = re.compile(r"=>|<=|>=|==|->|→|⇒")
_OUTLINE_RE = re.compile(r"\d+\.\d+")
_SENTENCE_END_RE = re.compile(r"[。！？；;]")
_EXCERPT_LIMIT = 280
_EXCERPTS_PER_KIND = 2
# 摘录挑选规则变过。版本不同就从原文重摘，避免空摘录、目录或抽丢的符号留在文件里。
_EXCERPT_VERSION = 7
# 幻灯片标题常比正文多或少一个「的」，或在概念中间插「先进的」这类短修饰。
_FLEX_CHARS = "的和与或及力"
# 幻灯片常把 σ₃ 画在句子的空档上，抽取后空档还在，符号落到文末。
_SYMBOL_HOLE_RE = re.compile(r"(?<=[\u4e00-\u9fff])[ \t\u00a0]{2,}(?=[\u4e00-\u9fff])")
_DETACHED_SYMBOL_RE = re.compile(
    r"^[0-9]{0,2}[σΣετφμπλθγβα][0-9]{0,2}$"
)
_SUB_DIGITS = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def _doc_text(doc: Any) -> str:
    if isinstance(doc, dict):
        return str(doc.get("page_content") or doc.get("content") or "")
    return str(getattr(doc, "page_content", "") or "")


def _doc_meta(doc: Any) -> Dict[str, Any]:
    if isinstance(doc, dict):
        meta = doc.get("metadata") or {}
    else:
        meta = getattr(doc, "metadata", None) or {}
    return meta if isinstance(meta, dict) else {}


def _normalize_detached_symbol(token: str) -> str:
    """把抽反的「3σ」收成「σ₃」。"""
    match = re.fullmatch(r"([0-9]{0,2})([σΣετφμπλθγβα])([0-9]{0,2})", token)
    if not match:
        return token
    base = match.group(2)
    sub = (match.group(1) or "") + (match.group(3) or "")
    if not sub:
        return base
    return base + sub.translate(_SUB_DIGITS)


def _repair_detached_symbols(text: str) -> str:
    """文末单独成行的 σ₃，按出现顺序填回中文之间的空档。对不上就保持原样。"""
    lines = text.splitlines()
    end = len(lines)
    trailing = []
    while end > 0:
        stripped = lines[end - 1].strip()
        if not stripped:
            end -= 1
            continue
        if not _DETACHED_SYMBOL_RE.match(stripped):
            break
        trailing.append(stripped)
        end -= 1
    if not trailing:
        return text
    trailing.reverse()
    body = "\n".join(lines[:end]).strip()
    holes = list(_SYMBOL_HOLE_RE.finditer(body))
    if len(holes) != len(trailing):
        return text
    for hole, token in zip(reversed(holes), reversed(trailing)):
        body = body[:hole.start()] + _normalize_detached_symbol(token) + body[hole.end():]
    return body


def _source_text(doc: Any) -> str:
    """摘抄用原文。检索前缀不算原文，有 original_content 时用它。"""
    meta = _doc_meta(doc)
    original = str(meta.get("original_content") or "").strip()
    text = original or _doc_text(doc).strip()
    if not text:
        return ""
    return _repair_detached_symbols(text)


def _label_compact(label: str) -> str:
    return re.sub(r"\s+", "", label)


def _compact_index(text: str) -> tuple:
    """去掉空白后的正文，以及每个字在原文里的位置。换行也算空白，概念可以跨行。"""
    mapping: List[int] = []
    chars: List[str] = []
    for index, ch in enumerate(text):
        if ch.isspace():
            continue
        mapping.append(index)
        chars.append(ch)
    return "".join(chars), mapping


def _coverage_needles(label: str) -> List[tuple]:
    """要在原文里找的连续片段。(片段, 覆盖字数, 加分)。长的优先。"""
    compact = _label_compact(label)
    if len(compact) < 2:
        return []
    items = [(compact, len(compact), 12)]
    for index, ch in enumerate(compact):
        if ch in _FLEX_CHARS and len(compact) - 1 >= 4:
            items.append((compact[:index] + compact[index + 1:], len(compact) - 1, 4))
    if compact.endswith("现象") or compact.endswith("问题"):
        stem = compact[:-2]
        if len(stem) >= 2:
            items.append((stem, len(stem), 0))
    for suffix in ("研究", "方法"):
        if compact.endswith(suffix) and len(compact) - len(suffix) >= 6:
            stem = compact[:-len(suffix)]
            items.append((stem, len(stem), 2))
    # 只从概念开头往下缩短。从尾巴截会把「力学的研究方法」错配到别的学科。
    floor = 6 if len(compact) >= 10 else 4
    if len(compact) > floor:
        for length in range(len(compact) - 1, floor - 1, -1):
            items.append((compact[:length], length, 0))
    best: Dict[str, tuple] = {}
    for needle, matched, bonus in items:
        prev = best.get(needle)
        if prev is None or (matched, bonus) > prev:
            best[needle] = (matched, bonus)
    ordered = sorted(best.items(), key=lambda item: (item[1][0], item[1][1]), reverse=True)
    return [(needle, matched, bonus) for needle, (matched, bonus) in ordered]


def _find_exact(compact: str, needle: str) -> List[tuple]:
    found = []
    start = 0
    while True:
        index = compact.find(needle, start)
        if index < 0:
            return found
        found.append((index, index + len(needle)))
        start = index + 1


def _find_flex(compact: str, label: str) -> List[tuple]:
    """正文比概念多一个「的 / 和 / 力」也算对上，摘出来的仍是原文。"""
    if len(label) < 4:
        return []
    found = []
    head = label[:2]
    start = 0
    while True:
        index = compact.find(head, start)
        if index < 0:
            return found
        window = compact[index:index + len(label) + 1]
        if len(window) == len(label) + 1:
            for pos, ch in enumerate(window):
                if ch in _FLEX_CHARS and window[:pos] + window[pos + 1:] == label:
                    found.append((index, index + len(label) + 1))
                    break
        start = index + 1


def _find_gap(compact: str, label: str) -> List[tuple]:
    """概念两边都能对上，中间只隔最多 3 个字（如「配置先进的闭环控制系统」）。"""
    if len(label) < 8:
        return []
    found = []
    for cut in range(2, len(label) - 1):
        pre, suf = label[:cut], label[cut:]
        if min(len(pre), len(suf)) < 2 or max(len(pre), len(suf)) < 6:
            continue
        start = 0
        while True:
            index = compact.find(pre, start)
            if index < 0:
                break
            after = index + len(pre)
            for gap in (1, 2, 3):
                end = after + gap
                if compact[end:end + len(suf)] == suf:
                    found.append((index, end + len(suf)))
                    break
            start = index + 1
    return found


def _line_spans(text: str) -> List[tuple]:
    offset = 0
    spans = []
    for line in text.splitlines(keepends=True):
        spans.append((offset, offset + len(line), line.strip()))
        offset += len(line)
    if text and not spans:
        spans.append((0, len(text), text.strip()))
    return spans


def _excerpt_around(text: str, start: int, end: int) -> str:
    """从命中的那几行起摘，断行的句子再往后补，直到句号。"""
    spans = _line_spans(text)
    indexes = [
        index for index, (begin, stop, stripped) in enumerate(spans)
        if stripped and not (stop <= start or begin >= end)
    ]
    if not indexes:
        return text[start:end].strip()
    first = indexes[0]
    last = indexes[-1]
    stop = spans[last][1]
    taken = 0
    opening = spans[first][2]
    if len(opening) <= 24 or "定义" in opening or not re.search(r"[。！？]$", spans[last][2]):
        for follow in range(last + 1, len(spans)):
            if not spans[follow][2]:
                continue
            if len(text[spans[first][0]:spans[follow][1]].strip()) > _EXCERPT_LIMIT:
                break
            stop = spans[follow][1]
            taken += 1
            if taken >= 3 or re.search(r"[。！？]$", spans[follow][2]):
                break
    snippet = text[spans[first][0]:stop].strip()
    if len(snippet) <= _EXCERPT_LIMIT:
        return snippet
    left = spans[first][0]
    while left < start and len(text[left:stop].strip()) > _EXCERPT_LIMIT:
        nxt = text.find("\n", left)
        if nxt < 0 or nxt + 1 > start:
            break
        left = nxt + 1
    snippet = text[left:stop].strip()
    if len(snippet) <= _EXCERPT_LIMIT:
        return snippet
    return _clip(text[start:min(len(text), end + 160)])


def _clip(text: str, limit: int = _EXCERPT_LIMIT) -> str:
    snippet = text.strip()
    if len(snippet) <= limit:
        return snippet
    return snippet[:limit].rstrip()


def _excerpt_file_name(meta: Dict[str, Any]) -> str:
    name = str(meta.get("file_name") or meta.get("filename") or "").strip()
    if name:
        return name
    source = str(meta.get("source") or "").replace("\\", "/").split("/")[-1]
    return source


def _excerpt_page(meta: Dict[str, Any]):
    try:
        from rag.loaders.pdf_map import display_page
        page = display_page(meta)
    except Exception:
        page = meta.get("page")
    if page in ("", None):
        return None
    return page


def _chinese_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def _is_formula_text(text: str) -> bool:
    """真公式才算公式。中文里的「甲=乙」、页眉里的 => 不算。"""
    if "[公式]" in text or _FORMULA_BLOCK_RE.search(text) or _INLINE_MATH_RE.search(text):
        return True
    cleaned = _EQ_NOISE_RE.sub(" ", text)
    if "=" not in cleaned and "＝" not in cleaned:
        return False
    if _chinese_count(text) > 12 and "$" not in text:
        return False
    return bool(re.search(r"\\[a-zA-Z]+|[A-Za-zα-ωΑ-Ωπ∑∫√^_]|≤|≥|≈", cleaned))


def _is_outline(text: str) -> bool:
    return len(_OUTLINE_RE.findall(text)) >= 3


def _label_only_in_book_title(text: str, label: str) -> bool:
    outside = re.sub(r"《[^》]*》", "", text)
    return label in text and label not in outside


def _private_use_count(text: str) -> int:
    return sum(1 for ch in text if "\ue000" <= ch <= "\uf8ff")


def _formula_for_label(snippet: str, label: str, core_line: str) -> bool:
    """公式必须是在讲这个概念。书名页眉、符号字体乱码不算。"""
    if _label_only_in_book_title(snippet, label):
        return False
    if re.search(r"20\d{2}[/-]\d{1,2}[/-]\d{1,2}", core_line) and not _is_formula_text(core_line):
        return False
    if not (_is_formula_text(snippet) or _is_formula_text(core_line)):
        return False
    body = snippet if _is_formula_text(snippet) else core_line
    if "$" not in body and "[公式]" not in body and _private_use_count(body) >= 4:
        return False
    return True


def _classify_excerpt(snippet: str, label: str, core_line: str) -> tuple:
    """返回 (kind, score)。目录和书名页眉压低，完整定义句抬高。"""
    if _formula_for_label(snippet, label, core_line):
        return "formula", 100
    explanatory = bool(_SENTENCE_END_RE.search(snippet)) and _chinese_count(snippet) >= 12
    if _DEF_CUE_RE.search(snippet) and explanatory and not _is_outline(snippet):
        kind, score = "definition", 90
    elif explanatory:
        kind, score = "passage", 60
    else:
        kind, score = "passage", 20
    if _SYMBOL_HOLE_RE.search(snippet):
        score -= 40
    if _is_outline(snippet):
        score -= 45
    if _label_only_in_book_title(snippet, label):
        score -= 35
    if re.search(r"20\d{2}[/-]\d{1,2}[/-]\d{1,2}", snippet) and not explanatory:
        score -= 25
    return kind, score


def _collect_chunk_excerpts(text: str, label: str, meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """摘出包含该概念的连续原文。允许断行，也允许少一个「的」或中间隔几个字。"""
    compact_label = _label_compact(label)
    if len(compact_label) < 2:
        return []
    compact, mapping = _compact_index(text)
    if not compact or not mapping:
        return []
    hits = []
    for needle, matched, bonus in _coverage_needles(label):
        for start, end in _find_exact(compact, needle):
            hits.append((start, end, matched, bonus))
    for start, end in _find_flex(compact, compact_label):
        hits.append((start, end, len(compact_label), 6))
    for start, end in _find_gap(compact, compact_label):
        hits.append((start, end, len(compact_label), 6))
    if not hits:
        return []
    best_matched = max(item[2] for item in hits)
    seen_span = set()
    excerpts = []
    seen_text = set()
    for start, end, matched, bonus in hits:
        if matched != best_matched or (start, end) in seen_span:
            continue
        seen_span.add((start, end))
        if start >= len(mapping) or end - 1 >= len(mapping):
            continue
        orig_start = mapping[start]
        orig_end = mapping[end - 1] + 1
        snippet = _excerpt_around(text, orig_start, orig_end)
        if not snippet or snippet in seen_text:
            continue
        seen_text.add(snippet)
        core = text[orig_start:orig_end].strip()
        kind, score = _classify_excerpt(snippet, label, core or snippet)
        excerpts.append({
            "kind": kind,
            "text": snippet,
            "file_name": _excerpt_file_name(meta),
            "page": _excerpt_page(meta),
            "score": score + bonus,
            "matched": matched,
        })
        if len(excerpts) >= 6:
            break
    return excerpts


def _public_excerpt(item: Dict[str, Any]) -> Dict[str, Any]:
    # 幻灯片项目符号经常落在符号字体的私用区，页面上会变成方框。去掉这些符号，正文照抄。
    text = "".join(ch for ch in item["text"] if not ("\ue000" <= ch <= "\uf8ff")).strip()
    return {
        "kind": item["kind"],
        "text": text,
        "file_name": item.get("file_name") or "",
        "page": item.get("page"),
    }


def _keep_best(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """留下分数接近最高分的几条，目录行不要跟在正文后面充数。"""
    ranked = sorted(items, key=lambda item: (-item["score"], -min(len(item["text"]), 180)))
    if not ranked:
        return []
    floor = ranked[0]["score"] - 25
    kept = []
    for item in ranked:
        if item["score"] < floor:
            continue
        public = _public_excerpt(item)
        if public["text"]:
            kept.append(public)
        if len(kept) >= _EXCERPTS_PER_KIND:
            break
    return kept


def attach_source_excerpts(nodes: List[Dict], documents: List[Any]) -> List[Dict]:
    """给每个节点挂上原文摘录。有公式或定义时只留这两类，公式在前；否则留下最像正文的句子。"""
    prepared = []
    for doc in documents or []:
        text = _source_text(doc)
        if len(text) < 2:
            continue
        prepared.append((text, _doc_meta(doc)))

    for node in nodes:
        label = str(node.get("label") or node.get("id") or "").strip()
        found: List[Dict[str, Any]] = []
        seen = set()
        if len(label) >= 2:
            for text, meta in prepared:
                for item in _collect_chunk_excerpts(text, label, meta):
                    if item["text"] in seen:
                        continue
                    seen.add(item["text"])
                    found.append(item)
        if found:
            # 同一概念在别处有更完整的原文时，不用更短的片段充数。
            best_matched = max(item.get("matched") or 0 for item in found)
            found = [item for item in found if (item.get("matched") or 0) == best_matched]
        formulas = _keep_best([item for item in found if item["kind"] == "formula"])
        definitions = _keep_best([item for item in found if item["kind"] == "definition"])
        if formulas or definitions:
            node["excerpts"] = formulas + definitions
        else:
            node["excerpts"] = _keep_best(found)
    return nodes


def _load_index_documents(subject_id: str) -> Optional[List[Any]]:
    """读取知识库里已切好的 chunk。索引不存在时返回 None。"""
    import pickle
    from rag.rag_service import _safe_dir_name
    from config.path_config import get_data_dir

    index_pkl = Path(get_data_dir()) / "vector_store" / _safe_dir_name(subject_id) / "index.pkl"
    if not index_pkl.exists():
        return None
    with open(index_pkl, "rb") as f:
        stored = pickle.load(f)
    if not isinstance(stored, dict):
        return []
    documents = stored.get("documents", [])
    return documents if isinstance(documents, list) else []


def ensure_node_excerpts(subject_id: str, graph: Dict[str, Any]) -> Dict[str, Any]:
    """旧图谱没有摘录时，从知识库原文补一次并写回。补不上就不改文件。"""
    nodes = graph.get("nodes") or []
    stats = graph.setdefault("stats", {})
    version_ok = stats.get("excerpt_version") == _EXCERPT_VERSION
    complete = all(isinstance(node.get("excerpts"), list) for node in nodes)
    if not nodes or (version_ok and complete):
        return graph
    documents = _load_index_documents(subject_id)
    if not documents:
        return graph
    attach_source_excerpts(nodes, documents)
    stats["excerpt_version"] = _EXCERPT_VERSION
    try:
        _save_graph(subject_id, graph)
    except Exception as e:
        logger.warning(f"知识图谱摘录写回失败，本次仍返回摘录: {type(e).__name__}: {e}")
    return graph


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
    from rag.rag_service import _safe_dir_name

    # 独立模型配置：GRAPH_MODEL 优先（不误用聊天模型）
    if model is None:
        model = os.getenv("GRAPH_MODEL", None)

    # 1. 复用知识库已切分的 chunk（index.pkl 的 documents 即 RAG 构建时持久化的 split 结果）
    safe_name = _safe_dir_name(subject_id)
    documents = _load_index_documents(subject_id)
    if documents is None:
        raise FileNotFoundError(f"知识库尚未构建或索引缺失，请先构建知识库: {subject_id}")
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
        # Do not publish an empty graph or replace a previous usable graph after
        # model/parse failures. The task manager propagates this as failed.
        raise ValueError("未生成有效知识关系：请检查模型状态和资料内容；已有图谱保留")
    
    # 4. 合并为图谱
    raw_nodes, raw_edges = _merge_triples_to_graph(all_triples, source_info)
    
    # 5. 后处理过滤（防线2+3）
    nodes, edges = _filter_and_normalize_graph(raw_nodes, raw_edges)

    if not nodes or not edges:
        raise ValueError("抽取结果未形成有效知识关系，已有图谱保留")

    try:
        attach_source_excerpts(nodes, documents)
    except Exception as e:
        logger.warning(f"知识图谱原文摘录失败，节点仍保留: {type(e).__name__}: {e}")
        for node in nodes:
            node.setdefault("excerpts", [])

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
            "excerpt_version": _EXCERPT_VERSION,
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


def query_graph(subject_id: str, node: str = None, depth: int = 1,
                graph: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """查询图谱（支持按节点展开）。传入 graph 时不再读盘。"""
    if graph is None:
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
