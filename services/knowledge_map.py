"""
知识地图与节点学习（2026-10-07 统一伴学改造）

明确区分两个东西（不要混用）：
- **学习地图**（本模块）：面向学生的章节 → 知识点路线，节点带稳定 ID、先修关系与来源片段，
  进度可跨重建保留。落在 get_data_dir()/learning_map.db。
- **检索知识图谱**（services/knowledge_service 的 graph）：面向概念关系的图分析，
  节点由资料实体抽取而来，不做学习进度。两者互不改写。

参考实现（采用方式见 docs/research/learning-flow-and-knowledge-map.md）：
- DeepTutor learning/models.py:80-108,532-582（KnowledgePoint: type / prerequisite_ids /
  topic_source_ids）、topic_generation.py:308-582（草稿宽松、确认严格、coverage 交代）
- Study-Mate curriculum.schema.json:11-55（node+edge.reason、状态词表单一来源）
- Archify validator.mjs:38-85（结构诊断：code/subject/evidence/fix）
本模块用 Python 重新实现，只借鉴设计与字段语义。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from config.path_config import get_data_dir
from services import chapter_outline
from utils.logger import logger

DB_PATH = os.path.join(str(get_data_dir()), "learning_map.db")

# 节点状态词表（单一来源：前端样式与后端校验都从这里对齐）
STATUS_NEW = "new"
STATUS_LEARNING = "learning"
STATUS_PRACTICING = "practicing"
STATUS_PASSED = "passed"
STATUS_REVIEW = "needs_review"
NODE_STATUSES = (STATUS_NEW, STATUS_LEARNING, STATUS_PRACTICING, STATUS_PASSED, STATUS_REVIEW)

STATUS_LABELS = {
    STATUS_NEW: "未开始",
    STATUS_LEARNING: "学习中",
    STATUS_PRACTICING: "练习中",
    STATUS_PASSED: "本次检测通过",
    STATUS_REVIEW: "需要复习",
}

_DDL_MAPS = """
CREATE TABLE IF NOT EXISTS learning_maps (
    subject_id TEXT PRIMARY KEY,
    map_id TEXT NOT NULL,
    title TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    source_signature TEXT,
    diagnostics_json TEXT DEFAULT '[]',
    updated_at TEXT NOT NULL
)
"""

_DDL_PROGRESS = """
CREATE TABLE IF NOT EXISTS map_node_progress (
    user_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, subject_id, node_id)
)
"""

_DDL_VERSIONS = """
CREATE TABLE IF NOT EXISTS map_node_versions (
    id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    map_id TEXT NOT NULL,
    label TEXT,
    fingerprint TEXT,
    entity_version INTEGER DEFAULT 1,
    source_signature TEXT,
    created_at TEXT NOT NULL
)
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_DDL_MAPS)
    conn.execute(_DDL_PROGRESS)
    conn.execute(_DDL_VERSIONS)
    conn.commit()
    return conn


def _loads(raw: Any, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


# =====================================================================
# 稳定 ID 与语义指纹
# =====================================================================

def normalize_label(label: str) -> str:
    """语义指纹：去空白/标点、统一大小写，用于重建时把新节点匹配回旧节点。"""
    text = str(label or "").strip().lower()
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[，。、；：！？,.;:!?·\-—_（）()\[\]【】「」“”‘’]+", "", text)
    return text


def stable_node_id(label: str, parent_key: str = "") -> str:
    """
    稳定节点 ID：由「父路径 + 语义指纹」决定，不由列表下标决定。

    这样重新生成地图时，只要标题不变，node_id 就不变，学习记录不会归零。
    """
    raw = f"{parent_key}::{normalize_label(label)}"
    return "node_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# =====================================================================
# 证据收集（用真实资料，不用模型想象）
# =====================================================================

def _text_file_excerpt(subject_id: str, filename: str, limit: int = 6000) -> str:
    """纯文本资料直接读原文（地图要覆盖整份资料，不能只靠相似度检索）。"""
    path = os.path.join(str(get_data_dir()), "subjects", subject_id, filename)
    if not os.path.exists(path) or not filename.lower().endswith((".txt", ".md")):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()[:limit]
    except OSError as e:
        logger.debug(f"读取资料原文失败 {filename}: {e}")
        return ""


def collect_evidence(user_id: Optional[str], subject_id: str,
                     scope: Dict[str, Any], max_docs: int = 20,
                     per_doc_chars: int = 1400) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    为每个范围内的资料收集证据片段（保证「每份资料都有交代」）。

    Returns:
        (evidence, outlines)
        evidence: [{"doc_id","file_name","snippet","page","chunk_id","kind"?,"chapter"?}]
        outlines: {文件名: chapter_outline.document_outline(...)}，只含真解析出章节的资料

    2026-10-30：非文本资料原先只做一次相似度检索，一本 252 页的教材最终只有
    3 段 × 1400 字进模型，模型看不见第 2/3/4…章，生成的路线只剩 5 章，
    而 uncovered 还是空的（13 章被静默丢掉）。现在优先用**原文解析出的章节大纲**
    + 每章开头的真实正文作证据：既让模型看得到每一章，也省掉一次检索调用。
    """
    from services import ai_service
    from services import chapter_outline
    from services import material_scope

    materials = scope.get("selected") or []
    evidence: List[Dict[str, Any]] = []
    outlines: Dict[str, Dict[str, Any]] = {}
    for item in materials[:max_docs]:
        filename = item.get("file_name") or ""
        excerpt = _text_file_excerpt(subject_id, filename)
        if excerpt:
            evidence.append({
                "doc_id": item["doc_id"], "file_name": filename,
                "snippet": excerpt[:per_doc_chars], "page": None, "chunk_id": None,
            })
            continue
        # 首选：章节大纲 + 每章开头正文（真实内容，不调模型、不做检索）
        try:
            outline = chapter_outline.document_outline(subject_id, filename)
        except Exception as e:
            logger.debug(f"解析章节大纲失败 {filename}: {e}")
            outline = {"file_name": filename, "chapters": [], "source": "none",
                       "pages": 0, "note": f"章节解析失败: {e}"}
        if outline.get("chapters"):
            rows = chapter_outline.chapter_excerpts(
                subject_id, filename, outline,
                per_chapter_chars=max(240, min(per_doc_chars, 700)))
            if rows:
                outlines[filename] = outline
                evidence.extend(rows)
                continue
        # 退路：没解析出章节（未建页索引 / 无书签）时，仍用资料名做一次检索
        try:
            result = ai_service.retrieve_and_build_context(
                question=f"{filename} 目录 章节 知识点", subject_id=subject_id,
                top_k=4, viewer_user_id=user_id)
        except Exception as e:
            logger.warning(f"为资料 {filename} 取证据失败: {e}")
            continue
        docs = result.get("documents") or []
        docs = material_scope.filter_documents_for_scope(
            {"mode": "selected", "selected": [item], "signature": ""} , docs)
        for doc in docs[:3]:
            meta = getattr(doc, "metadata", {}) or {}
            evidence.append({
                "doc_id": item["doc_id"], "file_name": filename,
                "snippet": (getattr(doc, "page_content", "") or "")[:per_doc_chars],
                "page": meta.get("page"), "chunk_id": meta.get("chunk_id") or meta.get("id"),
            })
    return evidence, outlines


# =====================================================================
# 地图生成（严格 JSON + 一次修复 + 结构性降级）
# =====================================================================

_MAP_SYSTEM = (
    "你是一名课程教学设计专家。你只根据给定的课程资料片段整理学习路线，"
    "不允许编造资料中没有的章节或知识点；资料没覆盖的内容不要写成节点，"
    "而是放进 uncovered 列表。只输出 JSON。"
)

_MAP_PROMPT = """请根据下面的课程资料片段，整理一份**章节 → 知识点**的学习路线。

## 本次范围的**全部资料**（共 {doc_count} 份）
{doc_list}
{chapter_section}
要求：
1. 严格返回 JSON，结构为：
{{"title": "课程名", "nodes": [{{"label": "知识点名", "level": "chapter|section|point",
   "parent": "父节点label或空", "objective": "一句话学习目标（可空）",
   "type": "memory|concept|procedure|application",
   "prerequisites": ["先修知识点label"],
   "source_docs": ["支持该节点的资料文件名"], "coverage_note": "资料覆盖情况（可空）"}}],
 "uncovered": ["没有整理出节点的资料文件名"]}}
2. 层级不超过 3 层，节点总数 **{min_nodes}-{max_nodes} 个**；先给章节，再给该章下的知识点。
3. **上面列出的每一份资料都必须有交代**：要么出现在某个节点的 source_docs 里，
   要么写进 uncovered（确实提炼不出内容时）。**不要静默漏掉任何一份**——
   宁可多给一个节点，也不要让一份资料凭空消失。
4. 先修关系只写资料中确有依赖的；不要为了好看而连边。
5. source_docs 只能填写上面列出的文件名（不要改写或翻译文件名）。
6. 每个知识点名称不超过 20 个字，准确反映资料内容。
7. 若资料文件名里带有章节信息（如"第3章"），**按它组织章节层级**，
   不要自己另起一套编号。
8. 不要为了凑节点数而编造资料里没有的知识点；某一章的片段不足以提炼知识点时，
   **只给该章的章节节点**即可。

资料片段：
{evidence}

请返回 JSON："""

# 有原文解析出的章节清单时插入这一段（清单是权威的，模型必须逐章交代）
_CHAPTER_SECTION = """
## 从原文解析出的**章节清单**（权威，共 {chapter_count} 章）
{chapter_list}

关于这份清单的硬性要求：
- **每一章都必须有一个 level="chapter" 的节点**，label 用清单里的原文标题（不要改写、不要合并相邻章）；
- 章下的知识点（level="point"）只在对应片段确有内容时才写，parent 填该章的 label；
- 清单里没有的章节不要凭空补出来。
"""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except (TypeError, ValueError):
            return None
    return None


def _chapter_entry(chapter: Dict[str, Any]) -> Dict[str, Any]:
    """把 chapter_outline 的一条章节转成对外结构（label 用原文标题，页码 0 基）。"""
    from services import chapter_outline
    number = chapter.get("number")
    title = chapter.get("title") or ""
    return {"number": number, "label": chapter_outline.chapter_label(number, title),
            "page": chapter.get("page"), "source_docs": list(chapter.get("source_docs") or [])}


def _validate_map(raw: Dict[str, Any], allowed_files: List[str],
                  expected_chapters: Optional[List[Dict[str, Any]]] = None,
                  ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    结构校验（借鉴 Archify validator：每条诊断带 code/subject/evidence/fix）。

    返回 (规范化后的地图骨架, diagnostics)。规范化后的节点带稳定 ID 与层级路径。

    2026-10-30：新增 `expected_chapters`（原文解析出的章节清单）后，校验不再只看
    "每份资料有没有被引用"。此前一本 18 章的教材只生成 5 章，uncovered 却是空的——
    章节缺口完全静默。现在漏掉的章会进 `uncovered_chapters` 并报 `chapter_missing`。
    """
    from services import chapter_outline

    diagnostics: List[Dict[str, Any]] = []
    expected = [_chapter_entry(c) for c in (expected_chapters or [])]
    nodes_in = raw.get("nodes") if isinstance(raw, dict) else None
    if not isinstance(nodes_in, list) or not nodes_in:
        diagnostics.append({
            "code": "empty_map", "subject": "nodes",
            "evidence": "模型未返回可用节点", "fix": "重新生成或检查资料是否为空",
        })
        return {"title": raw.get("title") if isinstance(raw, dict) else None,
                "nodes": [], "uncovered": list(allowed_files),
                "uncovered_chapters": expected}, diagnostics

    allowed = set(allowed_files)
    title = str(raw.get("title") or "").strip() or "学习路线"
    label_to_id: Dict[str, str] = {}
    label_to_parent: Dict[str, str] = {}
    nodes: List[Dict[str, Any]] = []
    seen_labels: Dict[str, int] = {}

    # 第一遍：分配稳定 ID（父路径决定层级，保证父先于子出现时不依赖顺序）
    for raw_node in nodes_in:
        if not isinstance(raw_node, dict):
            continue
        label = str(raw_node.get("label") or raw_node.get("name") or "").strip()
        if not label:
            diagnostics.append({"code": "missing_label", "subject": "node",
                                "evidence": str(raw_node)[:120], "fix": "删除或补全节点名"})
            continue
        fingerprint = normalize_label(label)
        if fingerprint in seen_labels:
            diagnostics.append({"code": "duplicate_label", "subject": "node",
                                "evidence": label, "fix": "合并同名节点"})
            continue
        parent_label = str(raw_node.get("parent") or "").strip()
        parent_key = normalize_label(parent_label) if parent_label else "root"
        node_id = stable_node_id(label, parent_key)
        seen_labels[fingerprint] = 1
        label_to_id[fingerprint] = node_id
        label_to_parent[fingerprint] = parent_key

    # 第二遍：组装节点
    # 注意：这里必须再次按指纹去重——第一遍只决定了 label→node_id 的映射，
    # 若此处不去重，同名节点会被原样各加一次（曾经的真实 bug，测试 test_validate_map_structure 锁定）。
    added: set = set()
    for raw_node in nodes_in:
        if not isinstance(raw_node, dict):
            continue
        label = str(raw_node.get("label") or raw_node.get("name") or "").strip()
        fingerprint = normalize_label(label)
        if not fingerprint or fingerprint not in label_to_id or fingerprint in added:
            continue
        added.add(fingerprint)
        parent_label = str(raw_node.get("parent") or "").strip()
        parent_fp = normalize_label(parent_label) if parent_label else ""
        parent_id = label_to_id.get(parent_fp) if parent_fp else None
        level = str(raw_node.get("level") or ("chapter" if not parent_id else "point")).strip()
        if level not in ("chapter", "section", "point"):
            level = "point"
        node_type = str(raw_node.get("type") or "concept").strip()
        if node_type not in ("memory", "concept", "procedure", "application"):
            node_type = "concept"
        source_docs = [str(x) for x in (raw_node.get("source_docs") or []) if str(x) in allowed]
        missing = [str(x) for x in (raw_node.get("source_docs") or []) if str(x) not in allowed]
        if missing:
            diagnostics.append({"code": "unknown_source", "subject": "source_docs",
                                "evidence": ", ".join(missing[:3]),
                                "fix": "只能引用给定资料文件名，已忽略无效项"})
        prereq_ids = []
        for pre in (raw_node.get("prerequisites") or []):
            pid = label_to_id.get(normalize_label(str(pre)))
            if pid and pid != label_to_id[fingerprint]:
                prereq_ids.append(pid)
        nodes.append({
            "node_id": label_to_id[fingerprint],
            "label": label,
            "level": level,
            "parent_id": parent_id,
            "objective": str(raw_node.get("objective") or "").strip()[:200],
            "type": node_type,
            "prerequisites": prereq_ids,
            "source_docs": source_docs,
            "coverage_note": str(raw_node.get("coverage_note") or "").strip()[:200],
            "entity_version": 1,
        })

    # 先修环检测（Study-Mate 的做法：DAG 必须无环）
    cycles = _find_cycles(nodes)
    if cycles:
        diagnostics.append({"code": "prereq_cycle", "subject": "prerequisites",
                            "evidence": " -> ".join(cycles[0]),
                            "fix": "已断开形成环的先修边"})
        for node in nodes:
            node["prerequisites"] = [p for p in node["prerequisites"] if not _in_cycle(p, cycles)]

    uncovered = [str(x) for x in (raw.get("uncovered") or []) if str(x) in allowed]
    covered_docs = {d for n in nodes for d in n["source_docs"]}
    for fname in allowed_files:
        if fname not in covered_docs and fname not in uncovered:
            uncovered.append(fname)

    # 章节覆盖核对（2026-10-30）：原文清单里的每一章，必须有节点真的交代。
    # 判定覆盖：label 与清单完全一致，或章号对上（模型改写了标题也算交代过，
    # 但改写会在下一轮生成时被硬性提示词纠正）。漏掉的章如实上报，不替模型编造。
    covered_labels = {normalize_label(n["label"]) for n in nodes}
    covered_numbers = {chapter_outline.chapter_number_of(n["label"]) for n in nodes}
    uncovered_chapters: List[Dict[str, Any]] = []
    for chapter in expected:
        if (chapter["label"] in covered_labels
                or chapter.get("number") in covered_numbers):
            continue
        uncovered_chapters.append(chapter)
        diagnostics.append({
            "code": "chapter_missing", "subject": "coverage",
            "evidence": chapter["label"],
            "fix": "资料里有这一章但模型没有生成对应节点；可用更少资料重试，或手动补充",
        })

    return {"title": title, "nodes": nodes, "uncovered": uncovered,
            "uncovered_chapters": uncovered_chapters}, diagnostics


def _find_cycles(nodes: List[Dict[str, Any]]) -> List[List[str]]:
    graph = {n["node_id"]: list(n.get("prerequisites") or []) for n in nodes}
    label_by_id = {n["node_id"]: n["label"] for n in nodes}
    cycles: List[List[str]] = []
    visited: Dict[str, int] = {}

    def walk(node_id: str, stack: List[str]) -> None:
        if visited.get(node_id) == 1:
            idx = stack.index(node_id) if node_id in stack else 0
            cycles.append([label_by_id.get(x, x) for x in stack[idx:]] + [label_by_id.get(node_id, node_id)])
            return
        if visited.get(node_id) == 2:
            return
        visited[node_id] = 1
        for nxt in graph.get(node_id, []):
            walk(nxt, stack + [node_id])
        visited[node_id] = 2

    for nid in graph:
        walk(nid, [])
    return cycles


def _in_cycle(prereq_id: str, cycles: List[List[str]]) -> bool:
    return any(prereq_id in c for c in cycles)


def _fallback_map(subject_name: str, evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    结构性降级：模型不可用时，按资料清单生成「每份资料一个章节」的最小可用路线，
    不虚构知识点，明确标注为“未解析成知识点”。
    """
    nodes = []
    for i, ev in enumerate(evidence[:12]):
        label = os.path.splitext(ev["file_name"])[0][:20] or f"资料 {i + 1}"
        nodes.append({
            "node_id": stable_node_id(label, "root"), "label": label, "level": "chapter",
            "parent_id": None, "objective": "阅读并梳理该资料的主要内容", "type": "concept",
            "prerequisites": [], "source_docs": [ev["file_name"]],
            "coverage_note": "模型未可用，按资料清单生成的兜底路线", "entity_version": 1,
        })
    return {"title": subject_name or "学习路线", "nodes": nodes, "uncovered": [],
            "uncovered_chapters": []}


def generate_map(user_id: Optional[str], subject_id: str, scope: Dict[str, Any],
                 subject_name: str = "", model: Optional[str] = None,
                 force: bool = False) -> Dict[str, Any]:
    """
    生成（或返回已有的）学习地图。

    不重复生成：资料版本签名未变化且已有地图时直接返回，避免每次打开页面都调模型。
    """
    existing = get_map(subject_id)
    signature = scope.get("signature") or ""
    if existing and not force and existing.get("source_signature") == signature:
        return {**existing, "reused": True, "generated": False}

    evidence, outlines = collect_evidence(user_id, subject_id, scope)
    allowed_files = [m.get("file_name") for m in (scope.get("selected") or []) if m.get("file_name")]
    # 原文解析出的章节清单（权威）：拼进提示词让模型逐章交代，
    # 并在校验时兜住"模型漏章"——这是"第1章直接跳第6/9章"的根治点（2026-10-30）。
    expected_chapters = chapter_outline.merged_chapters(outlines)
    diagnostics: List[Dict[str, Any]] = []
    if not evidence:
        return {"map_id": None, "subject_id": subject_id, "title": subject_name,
                "nodes": [], "uncovered": allowed_files, "uncovered_chapters": [],
                "diagnostics": [{
                    "code": "no_evidence", "subject": "materials",
                    "evidence": "所选资料没有可用内容", "fix": "先上传资料并建库"}],
                "source_signature": signature, "generated": False, "reused": False}

    # 输入上限（2026-10-03 由 12000 提到 40000）：一份多章资料只送前 12000 字符，
    # 后面的章节模型根本看不到，生成的地图自然少章。
    evidence_text = "\n\n".join(
        f"【{ev['file_name']}】{ev['snippet']}" for ev in evidence)[:40000]
    raw: Optional[Dict[str, Any]] = None
    try:
        from services.model_service import get_model_provider
        provider = get_model_provider(model)
        # 显式告诉模型"总共有哪些资料"，并要求每份都有交代。
        # 2026-10-03：此前提示词只写"节点总数 8-24 个"，16 份课件的课程被压到最多
        # 24 个节点，模型只能砍掉几章；而且它不知道范围内共有几份资料，
        # 也就无从发现自己漏了谁。
        doc_names = [m.get("file_name") for m in (scope.get("selected") or [])
                     if m.get("file_name")]
        if not doc_names:
            doc_names = sorted({ev.get("file_name") for ev in evidence if ev.get("file_name")})
        doc_list = "\n".join("- " + n for n in doc_names) or "（无）"
        # 有原文权威章节清单时，插入逐章交代的硬性要求段；没有就留空（不占提示词）
        if expected_chapters:
            chapter_list = "\n".join(
                f"- {_chapter_entry(c)['label']}（起始页 {int(c['page'] or 0) + 1}）"
                for c in expected_chapters if c.get("page") is not None) or "\n".join(
                    f"- {_chapter_entry(c)['label']}" for c in expected_chapters)
            chapter_section = _CHAPTER_SECTION.format(
                chapter_count=len(expected_chapters), chapter_list=chapter_list)
        else:
            chapter_section = ""
        # 节点数按资料数自适应：每份资料大致对应 1~3 个知识点
        doc_count = max(1, len(doc_names))
        min_nodes = max(8, doc_count)
        max_nodes = max(24, doc_count * 3)
        prompt = (_MAP_PROMPT
                  .replace("{doc_count}", str(doc_count))
                  .replace("{doc_list}", doc_list)
                  .replace("{min_nodes}", str(min_nodes))
                  .replace("{max_nodes}", str(max_nodes))
                  .replace("{chapter_section}", chapter_section)
                  .replace("{evidence}", evidence_text))
        result = provider.chat(
            [{"role": "system", "content": _MAP_SYSTEM}, {"role": "user", "content": prompt}],
            # 输出上限（2026-10-03 由 2600 提到 8000）：2600 tokens 约合 1700 汉字，
            # 十章的路线根本写不完，会被**静默截断**——JSON 仍是合法的，
            # 只是少了后面几章，从结果上看不出任何异常。
            temperature=0.2, max_tokens=8000,
            extra_payload={"response_format": {"type": "json_object"}})
        if str(result.get("finish_reason") or "").lower() == "length":
            diagnostics.append({
                "code": "output_truncated", "subject": "response",
                "evidence": "模型输出达到长度上限被截断（可能少了后面的章节）",
                "fix": "资料章节较多时会出现：可重试，或缩小资料范围后分批生成"})
        raw = _extract_json(result.get("answer") or "")
        if raw is None:
            diagnostics.append({"code": "bad_json", "subject": "response",
                                "evidence": (result.get("answer") or "")[:160],
                                "fix": "已尝试解析，未得到 JSON"})
    except Exception as e:
        logger.warning(f"知识地图生成调用模型失败: {e}")
        diagnostics.append({"code": "model_error", "subject": "llm",
                            "evidence": f"{type(e).__name__}: {e}", "fix": "检查模型配置后重试"})

    if raw:
        skeleton, validation_diags = _validate_map(raw, allowed_files,
                                                   expected_chapters=expected_chapters)
        diagnostics.extend(validation_diags)
    else:
        skeleton = _fallback_map(subject_name, evidence)

    nodes = skeleton.get("nodes") or []
    if not nodes:
        skeleton = _fallback_map(subject_name, evidence)
        nodes = skeleton["nodes"]

    # 与旧地图匹配：保留能匹配上的节点学习记录（不因重建归零）
    previous = existing.get("nodes") if existing else []
    by_fingerprint = {normalize_label(n["label"]): n for n in previous or []}
    for node in nodes:
        old = by_fingerprint.get(normalize_label(node["label"]))
        if old:
            node["node_id"] = old["node_id"]
            node["entity_version"] = int(old.get("entity_version") or 1)
    matched = sum(1 for n in nodes if any(
        n["node_id"] == o["node_id"] for o in previous or []))

    payload = {
        "map_id": "map_" + uuid.uuid4().hex[:10],
        "subject_id": subject_id,
        "title": skeleton.get("title") or subject_name,
        "nodes": nodes,
        "uncovered": skeleton.get("uncovered") or [],
        "uncovered_chapters": skeleton.get("uncovered_chapters") or [],
        "source_signature": signature,
        "diagnostics": diagnostics,
        "generated_at": _now(),
        "matched_previous": matched,
    }
    _save_map(subject_id, payload)
    _record_versions(subject_id, payload)
    return {**payload, "generated": True, "reused": False}


def _save_map(subject_id: str, payload: Dict[str, Any]) -> None:
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO learning_maps (subject_id, map_id, title, payload_json,"
            " source_signature, diagnostics_json, updated_at) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(subject_id) DO UPDATE SET map_id=excluded.map_id,"
            " title=excluded.title, payload_json=excluded.payload_json,"
            " source_signature=excluded.source_signature,"
            " diagnostics_json=excluded.diagnostics_json, updated_at=excluded.updated_at",
            (subject_id, payload.get("map_id"), payload.get("title"),
             json.dumps(payload, ensure_ascii=False), payload.get("source_signature"),
             json.dumps(payload.get("diagnostics") or [], ensure_ascii=False), _now()))
        conn.commit()
    finally:
        conn.close()


def _record_versions(subject_id: str, payload: Dict[str, Any]) -> None:
    conn = _get_db()
    try:
        for node in payload.get("nodes") or []:
            conn.execute(
                "INSERT INTO map_node_versions (id, subject_id, node_id, map_id, label,"
                " fingerprint, entity_version, source_signature, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                ("ver_" + uuid.uuid4().hex[:10], subject_id, node["node_id"],
                 payload.get("map_id"), node.get("label"),
                 normalize_label(node.get("label")), node.get("entity_version", 1),
                 payload.get("source_signature"), _now()))
        conn.commit()
    finally:
        conn.close()


def get_map(subject_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT payload_json FROM learning_maps WHERE subject_id=?", (subject_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _loads(row["payload_json"], None)


def delete_map(subject_id: str) -> bool:
    conn = _get_db()
    try:
        cur = conn.execute("DELETE FROM learning_maps WHERE subject_id=?", (subject_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_node(subject_id: str, node_id: str) -> Optional[Dict[str, Any]]:
    data = get_map(subject_id) or {}
    for node in data.get("nodes") or []:
        if node.get("node_id") == node_id:
            enriched = dict(node)
            enriched["map_id"] = data.get("map_id")
            enriched["map_title"] = data.get("title")
            return enriched
    return None


# =====================================================================
# 学习进度（按用户隔离）
# =====================================================================

def list_progress(user_id: str, subject_id: str) -> Dict[str, Dict[str, Any]]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT node_id, status, updated_at FROM map_node_progress"
            " WHERE user_id=? AND subject_id=?", (user_id, subject_id)).fetchall()
    finally:
        conn.close()
    return {r["node_id"]: {"status": r["status"], "updated_at": r["updated_at"]} for r in rows}


def set_node_status(user_id: str, subject_id: str, node_id: str, status: str) -> Dict[str, Any]:
    if status not in NODE_STATUSES:
        raise ValueError(f"未知的节点状态: {status}")
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO map_node_progress (user_id, subject_id, node_id, status, updated_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT(user_id, subject_id, node_id)"
            " DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
            (user_id, subject_id, node_id, status, _now()))
        conn.commit()
    finally:
        conn.close()
    return {"node_id": node_id, "status": status, "label": STATUS_LABELS.get(status, status)}


def map_with_progress(user_id: str, subject_id: str) -> Optional[Dict[str, Any]]:
    """给前端的地图：节点 + 该用户的学习状态 + 下一步建议。"""
    data = get_map(subject_id)
    if not data:
        return None
    progress = list_progress(user_id, subject_id)
    nodes = []
    for node in data.get("nodes") or []:
        item = dict(node)
        state = progress.get(node["node_id"], {}).get("status", STATUS_NEW)
        item["status"] = state
        item["status_label"] = STATUS_LABELS.get(state, state)
        nodes.append(item)
    result = dict(data)
    result["nodes"] = nodes
    result["progress_summary"] = {
        status: sum(1 for n in nodes if n["status"] == status) for status in NODE_STATUSES
    }
    base_next = recommend_next(nodes)
    # BKT 影子接入：若 BKT 指出的最低掌握点仍在路线中，且当前没有"到期复习"，
    # 优先建议它。BKT 不可用/没有样本时完全保持原逻辑。
    bkt_next = None
    bkt_recommended = None
    try:
        from services import knowledge_tracing
        bkt = knowledge_tracing.estimate_user(subject_id, user_id)
        bkt_recommended = bkt.get("recommended")
        if bkt_recommended:
            target = next((n for n in nodes
                           if normalize_label(n.get("label")) == normalize_label(bkt_recommended)), None)
            if target and target.get("status") != STATUS_PASSED:
                bkt_next = {"node_id": target["node_id"], "label": target["label"],
                            "reason": "BKT 估计这个知识点掌握概率最低，建议优先练"}
    except Exception as e:
        logger.debug(f"BKT 推荐跳过: {e}")
    if bkt_next and not (base_next and base_next.get("reason") == "这个知识点已经到期需要复习"):
        result["next_node"] = bkt_next
    else:
        result["next_node"] = base_next
    result["bkt_recommendation"] = bkt_next
    return result


def recommend_next(nodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    下一步建议（借鉴 DeepTutor next_objective：先修未满足 → 需要复习 → 第一个未通过节点）。
    """
    by_id = {n["node_id"]: n for n in nodes}
    for node in nodes:
        if node.get("status") == STATUS_REVIEW:
            return {"node_id": node["node_id"], "label": node["label"],
                    "reason": "这个知识点已经到期需要复习"}
    for node in nodes:
        if node.get("status") in (STATUS_LEARNING, STATUS_PRACTICING):
            return {"node_id": node["node_id"], "label": node["label"],
                    "reason": "你正在学习这个知识点，可以继续"}
    for node in nodes:
        if node.get("status") != STATUS_PASSED:
            unmet = [by_id.get(p) for p in (node.get("prerequisites") or [])]
            missing = [u for u in unmet if u and u.get("status") != STATUS_PASSED]
            if missing:
                first = missing[0]
                return {"node_id": first["node_id"], "label": first["label"],
                        "reason": f"《{node['label']}》需要先掌握这个知识点"}
            return {"node_id": node["node_id"], "label": node["label"],
                    "reason": "这是路线上第一个还没学的知识点"}
    return None


__all__ = [
    "NODE_STATUSES", "STATUS_LABELS", "STATUS_NEW", "STATUS_LEARNING",
    "STATUS_PRACTICING", "STATUS_PASSED", "STATUS_REVIEW",
    "collect_evidence", "delete_map", "generate_map", "get_map", "get_node",
    "list_progress", "map_with_progress", "normalize_label", "recommend_next",
    "set_node_status", "stable_node_id",
]
