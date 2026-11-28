"""
知识图谱桥接层（Graph Bridge）

打通 `rag/knowledge_graph` 与学习主链路，消除知识图谱孤岛：
1. 实体对齐：把掌握度/错题/班级学情/学习路线中的知识点名，映射到图谱概念节点（纯本地、确定性、零 LLM 调用）。
2. 前置知识溯因：沿图谱先修/推导/定义/包含边 + 学习路线 prerequisites DAG 反向 1–2 跳，
   定位“表层错题”背后是否由未掌握的前置概念引起，且只引用真实统计数字。
3. 学情热力图叠加：
   - 学生视角：掌握度（最新口径）+ BKT 概率 + FSRS 记忆保持率 + 错题数 + 路线节点映射
   - 教师视角：班级错误率 + 样本量 + 涉及学生 + 样本门槛（严格遵守 enough_data 口径）
4. 轻量 GraphRAG 检索扩展：零额外 LLM、毫秒级内存查表，输出 1 跳扩展概念与精炼关系三元组，
   供混合检索（hybrid_retriever）与伴学 Agent（tutor_tools）使用。
"""

from __future__ import annotations

import re
from collections import deque
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from utils.logger import logger

# 边方向约定（与 KnowledgeGraph/index.tsx 一致）：
# - DEPEND_TO_BASE: source 依赖/属于 target（target 是前置或上位概念）
# - BASE_TO_DERIVED: source 推导/定义/包含 target（source 是前置或上位概念）
_DEPEND_TO_BASE = {"前置条件", "属于", "应用"}
_BASE_TO_DERIVED = {"推导", "定义", "包含"}
_CORE_EXPANSION_RELATIONS = _DEPEND_TO_BASE | _BASE_TO_DERIVED | {"相关", "举例"}

_RELATION_ROLE_LABELS = {
    "前置条件": "先修前置",
    "推导": "推导基础",
    "定义": "定义基础",
    "包含": "上位概念",
    "属于": "所属上位",
    "应用": "基础理论",
    "map_prereq": "学习路线先修",
}

_PUNCT_RE = re.compile(r"[\s\u3000，。、；：！？,.;:!?·\-—_（）()\[\]【】「」“”‘’\"'`]+")
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")


def normalize_concept(name: str) -> str:
    """概念名归一化：去括号注释、空白与标点，统一小写。"""
    text = str(name or "").strip().lower()
    text = _PAREN_RE.sub("", text)
    text = _PUNCT_RE.sub("", text)
    return text


def _char_bigrams(text: str) -> Set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _token_overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ga, gb = _char_bigrams(a), _char_bigrams(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    union = len(ga | gb)
    return inter / union if union else 0.0


def load_subject_graph(subject_id: str) -> Optional[Dict[str, Any]]:
    """安全加载学科知识图谱（不存在或读取失败返回 None，不抛异常）。"""
    if not subject_id:
        return None
    try:
        from rag.knowledge_graph import load_knowledge_graph
        graph = load_knowledge_graph(subject_id)
        if not graph or not isinstance(graph.get("nodes"), list):
            return None
        return graph
    except Exception as e:
        logger.debug(f"加载学科知识图谱跳过 subject={subject_id}: {e}")
        return None


def match_concept_to_node(label: str, nodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把单个知识点/查询词对齐到最匹配的图谱节点（无可靠匹配返回 None）。"""
    norm = normalize_concept(label)
    if len(norm) < 2 or not nodes:
        return None

    best_node: Optional[Dict[str, Any]] = None
    best_score = 0.0

    for node in nodes:
        nlabel = str(node.get("label") or node.get("id") or "")
        nnorm = normalize_concept(nlabel)
        if len(nnorm) < 2:
            continue
        if norm == nnorm:
            return node
        # 子串包含：要求较短词至少 2 字符且占比合理，避免单字或泛词误配
        if norm in nnorm or nnorm in norm:
            shorter = min(len(norm), len(nnorm))
            longer = max(len(norm), len(nnorm))
            score = 0.72 + 0.25 * (shorter / longer)
            if score > best_score:
                best_score = score
                best_node = node
            continue
        ov = _token_overlap(norm, nnorm)
        if ov >= 0.45 and ov > best_score:
            best_score = ov
            best_node = node

    return best_node


def align_labels_to_graph(
    labels: List[str],
    nodes: List[Dict[str, Any]],
) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """
    批量对齐：返回 (label -> node_id, node_id -> [labels])。
    """
    label_to_node: Dict[str, str] = {}
    node_to_labels: Dict[str, List[str]] = {}
    for raw in labels:
        label = str(raw or "").strip()
        if not label or label == "待确认":
            continue
        hit = match_concept_to_node(label, nodes)
        if not hit:
            continue
        nid = str(hit.get("id") or hit.get("label") or "")
        if not nid:
            continue
        label_to_node[label] = nid
        bucket = node_to_labels.setdefault(nid, [])
        if label not in bucket:
            bucket.append(label)
    return label_to_node, node_to_labels


def _build_prereq_adjacency(
    graph: Optional[Dict[str, Any]],
    learning_map: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, List[Tuple[str, str]]], Dict[str, List[Tuple[str, str]]], Dict[str, str]]:
    """
    构建概念前置依赖图（融合知识图谱边 + 学习路线 prerequisites DAG）。

    Returns:
        upstream: concept_label -> [(prereq_label, relation)]   # 学 concept 前需要掌握谁
        downstream: concept_label -> [(dependent_label, relation)]  # 掌握 concept 后支撑谁
        norm_to_canonical: normalized -> canonical display label
    """
    upstream: Dict[str, List[Tuple[str, str]]] = {}
    downstream: Dict[str, List[Tuple[str, str]]] = {}
    norm_to_canonical: Dict[str, str] = {}
    seen_pairs: Set[Tuple[str, str, str]] = set()

    def _register(name: str) -> str:
        clean = str(name or "").strip()
        norm = normalize_concept(clean)
        if not norm:
            return clean
        if norm not in norm_to_canonical:
            norm_to_canonical[norm] = clean
        return norm_to_canonical[norm]

    def _add_dep(dependent: str, foundation: str, relation: str) -> None:
        dep = _register(dependent)
        base = _register(foundation)
        if not dep or not base or dep == base:
            return
        key = (dep, base, relation)
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        upstream.setdefault(dep, []).append((base, relation))
        downstream.setdefault(base, []).append((dep, relation))

    if graph:
        node_labels = {
            str(n.get("id") or ""): str(n.get("label") or n.get("id") or "")
            for n in (graph.get("nodes") or [])
        }
        for nlabel in node_labels.values():
            _register(nlabel)
        for edge in (graph.get("edges") or []):
            src = node_labels.get(str(edge.get("source") or ""), str(edge.get("source") or ""))
            tgt = node_labels.get(str(edge.get("target") or ""), str(edge.get("target") or ""))
            rel = str(edge.get("relation") or "").strip()
            if rel in _DEPEND_TO_BASE:
                _add_dep(src, tgt, rel)
            elif rel in _BASE_TO_DERIVED:
                _add_dep(tgt, src, rel)

    if learning_map:
        by_id = {
            str(n.get("node_id") or ""): str(n.get("label") or "")
            for n in (learning_map.get("nodes") or [])
        }
        for node in (learning_map.get("nodes") or []):
            label = str(node.get("label") or "")
            _register(label)
            for pid in (node.get("prerequisites") or []):
                plabel = by_id.get(str(pid), "")
                if plabel:
                    _add_dep(label, plabel, "map_prereq")

    return upstream, downstream, norm_to_canonical


def _lookup_metric(
    concept: str,
    metrics_by_label: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """按归一化精确匹配 → 子串匹配查找某概念的学情指标。"""
    if concept in metrics_by_label:
        return metrics_by_label[concept]
    target_norm = normalize_concept(concept)
    if len(target_norm) < 2:
        return None
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for label, metric in metrics_by_label.items():
        lnorm = normalize_concept(label)
        if len(lnorm) < 2:
            continue
        if target_norm == lnorm:
            return metric
        if target_norm in lnorm or lnorm in target_norm:
            score = min(len(target_norm), len(lnorm)) / max(len(target_norm), len(lnorm))
            if score > best_score:
                best_score = score
                best = metric
    return best


def diagnose_prerequisites(
    target_label: str,
    metrics_by_label: Dict[str, Dict[str, Any]],
    *,
    subject_id: Optional[str] = None,
    graph: Optional[Dict[str, Any]] = None,
    learning_map: Optional[Dict[str, Any]] = None,
    max_hops: int = 2,
    mode: str = "student",
) -> Dict[str, Any]:
    """
    对单个薄弱/待诊断概念沿前置依赖做 1–2 跳溯因。

    metrics_by_label 约定：
    - student 模式：{"mastery_level": float|None, "evidence_count": int, "wrong_count": int, "bkt_p_known": float|None}
    - class 模式：{"wrong_rate": float|None, "attempt_count": int, "wrong_count": int, "enough_data": bool, "kp_id": str|None}
    """
    if graph is None and subject_id:
        graph = load_subject_graph(subject_id)
    if learning_map is None and subject_id:
        try:
            from services import knowledge_map
            learning_map = knowledge_map.get_map(subject_id)
        except Exception:
            learning_map = None

    upstream, downstream, norm_to_canonical = _build_prereq_adjacency(graph, learning_map)
    canonical = norm_to_canonical.get(normalize_concept(target_label))
    if not canonical:
        # 尝试用子串对齐到一个图谱/路线节点
        candidate_nodes = [{"id": c, "label": c} for c in norm_to_canonical.values()]
        hit = match_concept_to_node(target_label, candidate_nodes)
        canonical = hit["label"] if hit else None

    if not canonical or canonical not in upstream:
        return {
            "target": target_label,
            "matched_concept": canonical,
            "has_graph_links": False,
            "root_cause_type": "no_graph_link",
            "weak_prerequisites": [],
            "unassessed_prerequisites": [],
            "solid_prerequisites": [],
            "downstream_concepts": (
                [d for d, _ in downstream.get(canonical, [])[:4]] if canonical else []
            ),
            "summary": None,
        }

    # BFS 反向遍历 1..max_hops 跳
    queue: deque[Tuple[str, int, List[str], str]] = deque()
    visited: Set[str] = {canonical}
    for prereq, rel in upstream.get(canonical, []):
        if prereq not in visited:
            visited.add(prereq)
            queue.append((prereq, 1, [prereq, canonical], rel))

    weak_prereqs: List[Dict[str, Any]] = []
    unassessed_prereqs: List[Dict[str, Any]] = []
    solid_prereqs: List[Dict[str, Any]] = []

    while queue:
        concept, depth, path, rel = queue.popleft()
        metric = _lookup_metric(concept, metrics_by_label)
        rel_label = _RELATION_ROLE_LABELS.get(rel, rel)

        if mode == "class":
            wrong_rate = (metric or {}).get("wrong_rate")
            attempts = int((metric or {}).get("attempt_count") or 0)
            enough = bool((metric or {}).get("enough_data"))
            entry = {
                "concept": concept,
                "depth": depth,
                "relation": rel,
                "relation_label": rel_label,
                "path": path,
                "wrong_rate": wrong_rate,
                "attempt_count": attempts,
                "wrong_count": int((metric or {}).get("wrong_count") or 0),
                "enough_data": enough,
                "kp_id": (metric or {}).get("kp_id"),
            }
            if metric and attempts > 0 and wrong_rate is not None:
                if wrong_rate >= 0.4:
                    weak_prereqs.append(entry)
                else:
                    solid_prereqs.append(entry)
            else:
                unassessed_prereqs.append(entry)
        else:
            mastery = (metric or {}).get("mastery_level")
            evidence = int((metric or {}).get("evidence_count") or 0)
            wrong = int((metric or {}).get("wrong_count") or 0)
            entry = {
                "concept": concept,
                "depth": depth,
                "relation": rel,
                "relation_label": rel_label,
                "path": path,
                "mastery_level": mastery,
                "evidence_count": evidence,
                "wrong_count": wrong,
                "bkt_p_known": (metric or {}).get("bkt_p_known"),
            }
            if metric and mastery is not None and evidence > 0:
                if mastery < 0.6 or wrong > 0 and mastery < 0.7:
                    weak_prereqs.append(entry)
                else:
                    solid_prereqs.append(entry)
            else:
                unassessed_prereqs.append(entry)

        if depth < max_hops:
            for nxt, nxt_rel in upstream.get(concept, []):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, depth + 1, [nxt] + path, nxt_rel))

    # 排序：先近后远，先更薄弱
    if mode == "class":
        weak_prereqs.sort(key=lambda x: (x["depth"], -(x["wrong_rate"] or 0)))
    else:
        weak_prereqs.sort(key=lambda x: (x["depth"], x["mastery_level"] if x["mastery_level"] is not None else 1.0))

    if weak_prereqs:
        root_type = "prerequisite_gap"
        top_names = "、".join(
            f"「{p['concept']}」"
            + (
                f"（错误率 {round((p['wrong_rate'] or 0) * 100)}%）"
                if mode == "class" and p.get("wrong_rate") is not None
                else (
                    f"（掌握度 {round((p['mastery_level'] or 0) * 100)}%）"
                    if mode == "student" and p.get("mastery_level") is not None
                    else ""
                )
            )
            for p in weak_prereqs[:2]
        )
        summary = f"前置知识存在薄弱环节：{top_names}，建议先巩固前置概念再攻克「{target_label}」。"
    elif solid_prereqs:
        root_type = "application_or_error"
        solid_names = "、".join(f"「{p['concept']}」" for p in solid_prereqs[:2])
        summary = f"前置概念（{solid_names}）已有较好基础，卡点更可能出在「{target_label}」本身的应用、公式或审题环节。"
    else:
        root_type = "unverified_prerequisite"
        un_names = "、".join(f"「{p['concept']}」" for p in unassessed_prereqs[:2])
        summary = f"前置概念（{un_names}）尚无作答证据，建议先做 1–2 题排查前置基础是否稳固。"

    return {
        "target": target_label,
        "matched_concept": canonical,
        "has_graph_links": True,
        "root_cause_type": root_type,
        "weak_prerequisites": weak_prereqs[:5],
        "unassessed_prerequisites": unassessed_prereqs[:5],
        "solid_prerequisites": solid_prereqs[:5],
        "downstream_concepts": [d for d, _ in downstream.get(canonical, [])[:4]],
        "summary": summary,
    }


def _effective_mastery(point: Dict[str, Any]) -> Tuple[Optional[float], int, int]:
    """统一读取侧最新口径（与学生面板/教学上下文一致），缺失时回退累计口径。"""
    latest = point.get("latest_view") or {}
    lvl = latest.get("latest_mastery_level")
    if lvl is not None:
        ev = int(latest.get("latest_evidence_count") or 0)
        wrong = int(latest.get("latest_independent_wrong") or 0)
        return float(lvl), ev, wrong
    raw = point.get("mastery_level")
    return (
        float(raw) if raw is not None else None,
        int(point.get("evidence_count") or 0),
        int(point.get("independent_wrong_count") or 0),
    )


def build_student_graph_overlay(user_id: str, subject_id: str) -> Dict[str, Any]:
    """
    学生视角：在学科知识图谱上叠加真实个人掌握状态、BKT、FSRS 保持率与前置溯因。
    """
    graph = load_subject_graph(subject_id)
    if not graph:
        return {
            "exists": False,
            "subject_id": subject_id,
            "graph": None,
            "summary": {
                "total_nodes": 0, "covered_nodes": 0, "weak_count": 0,
                "developing_count": 0, "mastered_count": 0,
                "forgetting_count": 0, "unassessed_count": 0,
            },
            "root_causes": [],
        }

    from services import (
        fsrs_scheduler,
        knowledge_map,
        knowledge_tracing,
        mastery_service,
        quiz_service,
    )

    points = mastery_service.list_mastery(user_id, subject_id) or []
    bkt_data = knowledge_tracing.estimate_user(subject_id, user_id) or {}
    bkt_by_norm = {
        normalize_concept(p["knowledge_point"]): p
        for p in (bkt_data.get("points") or [])
        if p.get("knowledge_point")
    }
    fsrs_cards = fsrs_scheduler.list_cards(user_id, subject_id) or []
    fsrs_by_kp_id = {c["knowledge_point_id"]: c for c in fsrs_cards if c.get("knowledge_point_id")}
    wrongs = quiz_service.list_wrong_questions(user_id, subject_id) or []
    wrong_count_by_norm: Dict[str, int] = {}
    for w in wrongs:
        kn = normalize_concept(w.get("knowledge_point") or "")
        if kn:
            wrong_count_by_norm[kn] = wrong_count_by_norm.get(kn, 0) + 1

    lmap = knowledge_map.get_map(subject_id)
    map_nodes = (lmap or {}).get("nodes") or []
    map_by_norm = {normalize_concept(n.get("label") or ""): n for n in map_nodes if n.get("label")}

    # 收集知识点指标用于节点叠加与前置溯因
    metrics_by_label: Dict[str, Dict[str, Any]] = {}
    kp_records: Dict[str, Dict[str, Any]] = {}
    for p in points:
        kp_name = str(p.get("knowledge_point") or "").strip()
        if not kp_name or kp_name == "待确认":
            continue
        lvl, ev, wrong = _effective_mastery(p)
        kn = normalize_concept(kp_name)
        bkt = bkt_by_norm.get(kn)
        card = fsrs_by_kp_id.get(p.get("knowledge_point_id") or "")
        rec = {
            "knowledge_point": kp_name,
            "knowledge_point_id": p.get("knowledge_point_id"),
            "mastery_level": lvl,
            "evidence_count": ev,
            "wrong_count": max(wrong, wrong_count_by_norm.get(kn, 0)),
            "consolidated": bool(p.get("consolidated")),
            "bkt_p_known": bkt.get("p_known") if bkt else None,
            "bkt_confidence": bkt.get("confidence") if bkt else None,
            "retrievability": card.get("retrievability") if card else None,
            "due_date": card.get("due") if card else None,
            "lapses": card.get("lapses") if card else 0,
        }
        kp_records[kp_name] = rec
        metrics_by_label[kp_name] = rec

    nodes = [dict(n) for n in (graph.get("nodes") or [])]
    _, node_to_kps = align_labels_to_graph(list(kp_records.keys()), nodes)

    today_str = date.today().isoformat()
    soon_str = (date.today() + timedelta(days=2)).isoformat()
    counts = {
        "total_nodes": len(nodes),
        "covered_nodes": 0,
        "weak_count": 0,
        "developing_count": 0,
        "mastered_count": 0,
        "forgetting_count": 0,
        "unassessed_count": 0,
    }
    root_causes: List[Dict[str, Any]] = []

    for node in nodes:
        nid = str(node.get("id") or "")
        nlabel = str(node.get("label") or nid)
        matched_kps = node_to_kps.get(nid, [])
        recs = [kp_records[k] for k in matched_kps if k in kp_records]

        # 同时关联学习路线节点，点击图谱节点可以直接定位到伴学路线
        nnorm = normalize_concept(nlabel)
        map_hit = map_by_norm.get(nnorm)
        if not map_hit:
            for mn in map_nodes:
                mnorm = normalize_concept(mn.get("label") or "")
                if len(mnorm) >= 2 and (nnorm in mnorm or mnorm in nnorm):
                    map_hit = mn
                    break

        assessed = [r for r in recs if r["mastery_level"] is not None and r["evidence_count"] > 0]
        if not assessed:
            counts["unassessed_count"] += 1
            node["learning_state"] = {
                "status": "unassessed",
                "status_label": "未评估",
                "mastery_level": None,
                "bkt_p_known": (bkt_by_norm.get(nnorm) or {}).get("p_known"),
                "evidence_count": 0,
                "wrong_count": wrong_count_by_norm.get(nnorm, 0),
                "retrievability": None,
                "due_date": None,
                "consolidated": False,
                "matched_kps": matched_kps,
                "map_node_id": map_hit.get("node_id") if map_hit else None,
                "prerequisite_diagnosis": None,
            }
            continue

        counts["covered_nodes"] += 1
        # 多知识点映射到同一概念节点时取最薄弱（保守策略，避免高分掩盖漏洞）
        mastery_level = min(r["mastery_level"] for r in assessed if r["mastery_level"] is not None)
        total_ev = sum(r["evidence_count"] for r in assessed)
        total_wrong = sum(r["wrong_count"] for r in assessed)
        consolidated = all(r["consolidated"] for r in assessed)
        bkt_vals = [r["bkt_p_known"] for r in assessed if r["bkt_p_known"] is not None]
        bkt_val = min(bkt_vals) if bkt_vals else None
        ret_vals = [r["retrievability"] for r in assessed if r["retrievability"] is not None]
        ret_val = min(ret_vals) if ret_vals else None
        due_vals = [r["due_date"] for r in assessed if r.get("due_date")]
        due_val = min(due_vals) if due_vals else None

        is_forgetting = (
            (ret_val is not None and ret_val < 0.78)
            or (due_val is not None and due_val <= soon_str and mastery_level >= 0.5)
        )
        if mastery_level < 0.5 or (total_wrong > 0 and mastery_level < 0.65):
            status, status_label = "weak", "薄弱"
            counts["weak_count"] += 1
        elif is_forgetting:
            status, status_label = "forgetting", "待复习（遗忘预警）"
            counts["forgetting_count"] += 1
        elif mastery_level >= 0.7 and consolidated:
            status, status_label = "mastered", "已巩固"
            counts["mastered_count"] += 1
        else:
            status, status_label = "developing", "巩固中"
            counts["developing_count"] += 1

        prereq_diag = None
        if status in ("weak", "forgetting"):
            prereq_diag = diagnose_prerequisites(
                nlabel, metrics_by_label,
                graph=graph, learning_map=lmap, mode="student",
            )
            if prereq_diag.get("root_cause_type") == "prerequisite_gap":
                root_causes.append({
                    "concept": nlabel,
                    "mastery_level": round(mastery_level, 4),
                    "wrong_count": total_wrong,
                    "weak_prerequisites": prereq_diag["weak_prerequisites"],
                    "summary": prereq_diag["summary"],
                })

        node["learning_state"] = {
            "status": status,
            "status_label": status_label,
            "mastery_level": round(mastery_level, 4),
            "bkt_p_known": round(bkt_val, 4) if bkt_val is not None else None,
            "evidence_count": total_ev,
            "wrong_count": total_wrong,
            "retrievability": ret_val,
            "due_date": due_val,
            "overdue": bool(due_val and due_val < today_str),
            "consolidated": consolidated,
            "matched_kps": matched_kps,
            "map_node_id": map_hit.get("node_id") if map_hit else None,
            "prerequisite_diagnosis": prereq_diag,
        }

    root_causes.sort(key=lambda r: (r["mastery_level"], -r["wrong_count"]))
    return {
        "exists": True,
        "subject_id": subject_id,
        "graph": {**graph, "nodes": nodes},
        "summary": counts,
        "root_causes": root_causes[:6],
    }


def build_class_graph_overlay(
    teacher_id: str,
    class_id: str,
    days: int = 30,
) -> Dict[str, Any]:
    """
    教师视角：在课程知识图谱上叠加班级错误率热力与前置知识溯因。
    严格复用 class_analytics.class_overview 的权限检查与样本量门槛。
    """
    from services import class_analytics, knowledge_map

    overview = class_analytics.class_overview(teacher_id, class_id, days=days)
    subject_id = overview.get("subject_id") or ""
    graph = load_subject_graph(subject_id)
    if not graph:
        return {
            "exists": False,
            "class_id": class_id,
            "subject_id": subject_id,
            "enough_data": bool(overview.get("enough_data")),
            "graph": None,
            "summary": {
                "total_nodes": 0, "covered_nodes": 0, "high_error_count": 0,
                "medium_error_count": 0, "low_error_count": 0,
                "insufficient_count": 0, "unassessed_count": 0,
            },
            "root_causes": [],
        }

    lmap = knowledge_map.get_map(subject_id)
    kp_stats = overview.get("knowledge_points") or []
    kp_by_label: Dict[str, Dict[str, Any]] = {}
    for k in kp_stats:
        label = str(k.get("knowledge_point") or "").strip()
        if not label or label in ("未标注知识点", "待确认"):
            continue
        kp_by_label[label] = {
            "kp_id": k.get("knowledge_point_id"),
            "knowledge_point": label,
            "attempt_count": int(k.get("attempt_count") or 0),
            "wrong_count": int(k.get("wrong_count") or 0),
            "wrong_rate": k.get("wrong_rate"),
            "student_count": int(k.get("student_count") or 0),
            "enough_data": bool(k.get("enough_data")),
        }

    nodes = [dict(n) for n in (graph.get("nodes") or [])]
    _, node_to_kps = align_labels_to_graph(list(kp_by_label.keys()), nodes)

    counts = {
        "total_nodes": len(nodes),
        "covered_nodes": 0,
        "high_error_count": 0,
        "medium_error_count": 0,
        "low_error_count": 0,
        "insufficient_count": 0,
        "unassessed_count": 0,
    }
    root_causes: List[Dict[str, Any]] = []

    for node in nodes:
        nid = str(node.get("id") or "")
        nlabel = str(node.get("label") or nid)
        matched_kps = node_to_kps.get(nid, [])
        recs = [kp_by_label[k] for k in matched_kps if k in kp_by_label]

        if not recs:
            counts["unassessed_count"] += 1
            node["class_state"] = {
                "status": "unassessed",
                "status_label": "暂无作答",
                "attempt_count": 0,
                "wrong_count": 0,
                "wrong_rate": None,
                "student_count": 0,
                "enough_data": False,
                "kp_id": None,
                "matched_kps": [],
                "prerequisite_diagnosis": None,
            }
            continue

        counts["covered_nodes"] += 1
        attempts = sum(r["attempt_count"] for r in recs)
        wrongs = sum(r["wrong_count"] for r in recs)
        students = max(r["student_count"] for r in recs)
        wrong_rate = round(wrongs / attempts, 3) if attempts else None
        enough = any(r["enough_data"] for r in recs) or attempts >= class_analytics.MIN_ATTEMPTS_FOR_CONCLUSION
        # 主下钻 KP：挑错得最多的那个
        primary = max(recs, key=lambda r: (r["wrong_count"], r["attempt_count"]))

        if not enough:
            status, status_label = "insufficient", "样本不足"
            counts["insufficient_count"] += 1
        elif wrong_rate is not None and wrong_rate >= 0.45:
            status, status_label = "high_error", "高频薄弱"
            counts["high_error_count"] += 1
        elif wrong_rate is not None and wrong_rate >= 0.25:
            status, status_label = "medium_error", "存在分化"
            counts["medium_error_count"] += 1
        else:
            status, status_label = "low_error", "整体较好"
            counts["low_error_count"] += 1

        prereq_diag = None
        if status in ("high_error", "medium_error"):
            prereq_diag = diagnose_prerequisites(
                nlabel, kp_by_label,
                graph=graph, learning_map=lmap, mode="class",
            )
            if prereq_diag.get("root_cause_type") == "prerequisite_gap":
                root_causes.append({
                    "concept": nlabel,
                    "kp_id": primary.get("kp_id"),
                    "wrong_rate": wrong_rate,
                    "wrong_count": wrongs,
                    "attempt_count": attempts,
                    "weak_prerequisites": prereq_diag["weak_prerequisites"],
                    "summary": prereq_diag["summary"],
                })

        node["class_state"] = {
            "status": status,
            "status_label": status_label,
            "attempt_count": attempts,
            "wrong_count": wrongs,
            "wrong_rate": wrong_rate,
            "student_count": students,
            "enough_data": enough,
            "kp_id": primary.get("kp_id"),
            "kp_name": primary.get("knowledge_point"),
            "matched_kps": matched_kps,
            "prerequisite_diagnosis": prereq_diag,
        }

    root_causes.sort(key=lambda r: (-(r["wrong_rate"] or 0), -r["wrong_count"]))
    return {
        "exists": True,
        "class_id": class_id,
        "subject_id": subject_id,
        "enough_data": bool(overview.get("enough_data")),
        "range": overview.get("range"),
        "graph": {**graph, "nodes": nodes},
        "summary": counts,
        "root_causes": root_causes[:6],
    }


def expand_query_with_graph(
    query: str,
    subject_id: str,
    *,
    max_hops: int = 1,
    max_neighbors: int = 5,
    graph: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    轻量 GraphRAG 检索扩展（零 LLM 调用，毫秒级）。

    从问题中匹配知识图谱概念节点，沿语义边做 1 跳扩展，返回：
    - matched_nodes: 命中的图谱概念名
    - neighbor_nodes: 扩展出的强相关概念名
    - relations: 命中的子图三元组列表
    - expanded_query: 用于二次召回的精炼查询词（无匹配时为空串）
    - context_block: 可注入提示词的精炼概念关系说明（含原文摘录摘要）
    """
    empty = {
        "matched_nodes": [],
        "neighbor_nodes": [],
        "relations": [],
        "expanded_query": "",
        "context_block": "",
    }
    q_norm = normalize_concept(query)
    if len(q_norm) < 2 or not subject_id:
        return empty
    if graph is None:
        graph = load_subject_graph(subject_id)
    if not graph:
        return empty

    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        return empty

    by_id = {str(n.get("id") or ""): n for n in nodes}
    # 1) 在问题中查找命中的图谱实体（优先长概念，按权重排序）
    matched_ids: List[str] = []
    scored_hits: List[Tuple[int, float, str]] = []
    for n in nodes:
        nid = str(n.get("id") or "")
        nlabel = str(n.get("label") or nid)
        nnorm = normalize_concept(nlabel)
        if len(nnorm) < 2:
            continue
        if nnorm in q_norm:
            scored_hits.append((len(nnorm), float(n.get("weight") or 1.0), nid))
    if not scored_hits:
        # 兜底：当问题很短且整体与某节点高度重合时
        hit = match_concept_to_node(query, nodes)
        if hit and hit.get("id"):
            scored_hits.append((len(normalize_concept(hit.get("label") or "")), 1.0, str(hit["id"])))
    if not scored_hits:
        return empty

    scored_hits.sort(key=lambda x: (-x[0], -x[1]))
    matched_ids = [nid for _, _, nid in scored_hits[:3]]
    matched_set = set(matched_ids)

    # 2) BFS 扩展 1..max_hops 跳核心语义边
    neighbor_scores: Dict[str, float] = {}
    relations: List[Dict[str, Any]] = []
    seen_rel: Set[Tuple[str, str, str]] = set()
    frontier = set(matched_ids)
    visited = set(matched_ids)

    for _ in range(max(1, min(max_hops, 2))):
        next_frontier: Set[str] = set()
        for e in edges:
            src = str(e.get("source") or "")
            tgt = str(e.get("target") or "")
            rel = str(e.get("relation") or "").strip()
            if rel and rel not in _CORE_EXPANSION_RELATIONS:
                continue
            w = float(e.get("weight") or 1.0)
            bonus = 1.5 if rel in (_DEPEND_TO_BASE | _BASE_TO_DERIVED) else 1.0
            if src in frontier and tgt in by_id:
                rk = (src, rel, tgt)
                if rk not in seen_rel:
                    seen_rel.add(rk)
                    relations.append({
                        "source": str(by_id[src].get("label") or src),
                        "relation": rel,
                        "target": str(by_id[tgt].get("label") or tgt),
                        "weight": w,
                    })
                if tgt not in visited:
                    next_frontier.add(tgt)
                    neighbor_scores[tgt] = max(neighbor_scores.get(tgt, 0.0), w * bonus)
            elif tgt in frontier and src in by_id:
                rk = (src, rel, tgt)
                if rk not in seen_rel:
                    seen_rel.add(rk)
                    relations.append({
                        "source": str(by_id[src].get("label") or src),
                        "relation": rel,
                        "target": str(by_id[tgt].get("label") or tgt),
                        "weight": w,
                    })
                if src not in visited:
                    next_frontier.add(src)
                    neighbor_scores[src] = max(neighbor_scores.get(src, 0.0), w * bonus)
        visited.update(next_frontier)
        frontier = next_frontier

    top_neighbors = sorted(
        neighbor_scores.items(),
        key=lambda kv: (-kv[1], -float(by_id[kv[0]].get("weight") or 1.0)),
    )[:max_neighbors]
    neighbor_labels = [str(by_id[nid].get("label") or nid) for nid, _ in top_neighbors if nid not in matched_set]
    matched_labels = [str(by_id[nid].get("label") or nid) for nid in matched_ids if nid in by_id]

    relations.sort(key=lambda r: -(r.get("weight") or 1.0))
    relations = relations[:8]

    expanded_terms = matched_labels + neighbor_labels
    expanded_query = " ".join(expanded_terms) if neighbor_labels else ""

    lines: List[str] = []
    if relations:
        lines.append("【知识图谱关联】" + "；".join(
            f"{r['source']}—[{r['relation']}]→{r['target']}" for r in relations[:6]
        ))
    # 附上命中概念已校验的公式/定义摘录（仅取第一条，控制长度）
    for nid in matched_ids[:2]:
        node = by_id.get(nid) or {}
        excerpts = node.get("excerpts") or []
        if excerpts and isinstance(excerpts[0], dict) and excerpts[0].get("text"):
            snippet = re.sub(r"\s+", " ", str(excerpts[0]["text"])).strip()[:120]
            lines.append(f"- {node.get('label', nid)}（原文摘录）：{snippet}")

    return {
        "matched_nodes": matched_labels,
        "neighbor_nodes": neighbor_labels,
        "relations": relations,
        "expanded_query": expanded_query,
        "context_block": "\n".join(lines)[:520],
    }


def query_concept_context(
    subject_id: str,
    concept: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
    depth: int = 1,
) -> Dict[str, Any]:
    """
    供 Tutor Agent 工具调用的概念图谱查询接口。
    - 传 concept：返回该概念的邻居关系、原文摘录，以及（有 user_id 时）该学生的掌握与前置诊断。
    - 不传 concept：返回图谱概览与核心枢纽概念（按度数/权重排序）。
    """
    graph = load_subject_graph(subject_id)
    if not graph:
        return {
            "exists": False,
            "note": "该学科尚未生成知识图谱，可在资料页或学情图谱页点击生成。",
        }

    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    if not concept or not str(concept).strip():
        degree: Dict[str, int] = {}
        for e in edges:
            degree[str(e.get("source") or "")] = degree.get(str(e.get("source") or ""), 0) + 1
            degree[str(e.get("target") or "")] = degree.get(str(e.get("target") or ""), 0) + 1
        hubs = sorted(
            nodes,
            key=lambda n: (-degree.get(str(n.get("id") or ""), 0), -float(n.get("weight") or 1.0)),
        )[:12]
        return {
            "exists": True,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "core_concepts": [
                {
                    "label": h.get("label"),
                    "type": h.get("type") or "概念",
                    "degree": degree.get(str(h.get("id") or ""), 0),
                }
                for h in hubs
            ],
        }

    hit = match_concept_to_node(concept, nodes)
    if not hit:
        return {
            "exists": True,
            "found": False,
            "query": concept,
            "note": f"知识图谱中未找到与「{concept}」匹配的概念节点。",
        }

    expanded = expand_query_with_graph(
        str(hit.get("label") or concept), subject_id,
        max_hops=max(1, min(int(depth or 1), 2)),
        max_neighbors=8, graph=graph,
    )
    out: Dict[str, Any] = {
        "exists": True,
        "found": True,
        "concept": hit.get("label"),
        "type": hit.get("type") or "概念",
        "neighbors": expanded["neighbor_nodes"],
        "relations": expanded["relations"],
        "excerpts": [
            {
                "kind": ex.get("kind"),
                "text": str(ex.get("text") or "")[:240],
                "file_name": ex.get("file_name"),
                "page": ex.get("page"),
            }
            for ex in (hit.get("excerpts") or [])[:3]
            if isinstance(ex, dict) and ex.get("text")
        ],
    }

    if user_id:
        try:
            from services import mastery_service
            points = mastery_service.list_mastery(user_id, subject_id) or []
            metrics: Dict[str, Dict[str, Any]] = {}
            for p in points:
                kp_name = str(p.get("knowledge_point") or "").strip()
                if not kp_name or kp_name == "待确认":
                    continue
                lvl, ev, wrong = _effective_mastery(p)
                metrics[kp_name] = {
                    "mastery_level": lvl, "evidence_count": ev, "wrong_count": wrong,
                }
            diag = diagnose_prerequisites(
                str(hit.get("label") or concept), metrics,
                subject_id=subject_id, graph=graph, mode="student",
            )
            own = _lookup_metric(str(hit.get("label") or concept), metrics)
            out["student_mastery"] = own
            out["prerequisite_diagnosis"] = diag
        except Exception as e:
            logger.debug(f"图谱工具附带掌握度失败: {e}")

    return out


__all__ = [
    "normalize_concept",
    "load_subject_graph",
    "match_concept_to_node",
    "align_labels_to_graph",
    "diagnose_prerequisites",
    "build_student_graph_overlay",
    "build_class_graph_overlay",
    "expand_query_with_graph",
    "query_concept_context",
]
