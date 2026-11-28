import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import * as echarts from 'echarts';
import {
  Alert, Button, Card, Empty, Popconfirm, Segmented, Select, Spin, Tag, Tooltip, Space, Typography, message,
} from 'antd';
import {
  AimOutlined, ApiOutlined, BookOutlined, CompressOutlined, ExpandOutlined, FileAddOutlined,
  FormOutlined, NodeExpandOutlined, PlayCircleOutlined, ReloadOutlined, SearchOutlined,
  ThunderboltOutlined, ZoomInOutlined, ZoomOutOutlined,
} from '@ant-design/icons';
import katex from 'katex';
import { SkeletonCard } from '../AnimateMotion';
import { MarkdownText } from '../TutorWorkspace/markdown';
import { getKnowledgeGraph, buildKnowledgeGraph, getKnowledgeGraphStatus } from '../../api/knowledge';
import { getStudentKnowledgeGraph, type PrerequisiteDiagnosis } from '../../api/student';
import { getClassAnalyticsGraph } from '../../api/teacher';
import { isLoggedIn } from '../../api/auth';

const { Text } = Typography;

interface GraphExcerpt {
  kind: 'formula' | 'definition' | string;
  text: string;
  file_name?: string;
  page?: number | string | null;
}

interface StudentLearningState {
  status: 'weak' | 'forgetting' | 'developing' | 'mastered' | 'unassessed' | string;
  status_label: string;
  mastery_level: number | null;
  bkt_p_known?: number | null;
  evidence_count: number;
  wrong_count: number;
  retrievability?: number | null;
  due_date?: string | null;
  overdue?: boolean;
  consolidated?: boolean;
  matched_kps?: string[];
  map_node_id?: string | null;
  prerequisite_diagnosis?: PrerequisiteDiagnosis | null;
}

interface ClassLearningState {
  status: 'high_error' | 'medium_error' | 'low_error' | 'insufficient' | 'unassessed' | string;
  status_label: string;
  attempt_count: number;
  wrong_count: number;
  wrong_rate: number | null;
  student_count: number;
  enough_data: boolean;
  kp_id?: string | null;
  kp_name?: string | null;
  matched_kps?: string[];
  prerequisite_diagnosis?: PrerequisiteDiagnosis | null;
}

interface GraphNode {
  id: string;
  label: string;
  type?: string;
  weight?: number;
  content?: string;
  source?: string;
  excerpts?: GraphExcerpt[];
  learning_state?: StudentLearningState;
  class_state?: ClassLearningState;
}

interface GraphEdge {
  source: string;
  target: string;
  relation: string;
  weight?: number;
}

interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats?: {
    node_count: number;
    edge_count: number;
    created_at?: string;
  };
}

interface RootCauseItem {
  concept: string;
  summary: string;
  kp_id?: string | null;
  weak_prerequisites?: { concept: string }[];
}

export interface KnowledgeGraphAction {
  type: 'explain' | 'practice' | 'prereq' | 'mistakes' | 'teacher_drill' | 'teacher_assign' | 'teacher_plan';
  label: string;
  nodeId?: string | null;
  kpId?: string | null;
}

export interface KnowledgeGraphProps {
  subjectId: string;
  mode?: 'concept' | 'student' | 'teacher';
  classId?: string;
  days?: number;
  compact?: boolean;
  onAction?: (action: KnowledgeGraphAction) => void;
}

// 现代协调的节点类型色板
const TYPE_COLORS: Record<string, string> = {
  '概念': '#4f46e5',
  '公式': '#0d9488',
  '定理': '#d97706',
  '例题': '#e11d48',
  '方法': '#0284c7',
  '人物': '#7c3aed',
  '其他': '#64748b',
};

// 学生掌握热力配色（与个人学情四色完全对齐）
const STUDENT_HEAT_META: Record<string, { color: string; label: string; border?: string }> = {
  weak: { color: '#ef4444', label: '薄弱', border: '#fecaca' },
  forgetting: { color: '#f59e0b', label: '遗忘预警', border: '#fde68a' },
  developing: { color: '#3b82f6', label: '巩固中', border: '#bfdbfe' },
  mastered: { color: '#10b981', label: '已巩固', border: '#a7f3d0' },
  unassessed: { color: '#94a3b8', label: '未评估', border: '#e2e8f0' },
};

// 教师班级学情热力配色
const TEACHER_HEAT_META: Record<string, { color: string; label: string }> = {
  high_error: { color: '#ef4444', label: '高频薄弱 (≥45%)' },
  medium_error: { color: '#f59e0b', label: '存在分化 (25-44%)' },
  low_error: { color: '#10b981', label: '整体较好 (<25%)' },
  insufficient: { color: '#8b5cf6', label: '样本不足' },
  unassessed: { color: '#94a3b8', label: '暂无作答' },
};

type GraphView = 'force' | 'tree';
type ColorMode = 'heat' | 'type';
type ExpandMode = 'focus' | 'all';

const CORE_THRESHOLD = 24;
const CORE_TARGET = 20;
const CORE_TARGET_COMPACT = 15;

/** 节点直径克制拉开（18..38），视觉有主次但不喧宾夺主。 */
function symbolSizeForWeight(weight: number | undefined, minWeight: number, maxWeight: number, isHub?: boolean): number {
  const span = maxWeight - minWeight || 1;
  const base = 18 + ((weight || 1) - minWeight) / span * 18;
  return isHub ? Math.max(base, 36) : base;
}

/** 优雅折行概念名称，拒绝截断成 “xxx (...” */
function formatNodeLabel(label: string, maxPerLine = 7): string {
  const s = (label || '').trim();
  if (s.length <= maxPerLine) return s;
  if (s.length <= maxPerLine * 2) {
    const mid = Math.ceil(s.length / 2);
    return `${s.slice(0, mid)}\n${s.slice(mid)}`;
  }
  return `${s.slice(0, maxPerLine)}\n${s.slice(maxPerLine, maxPerLine * 2 - 1)}…`;
}

const REL_RANK: Record<string, number> = {
  包含: 0,
  属于: 1,
  前置条件: 2,
  定义: 3,
  推导: 4,
  应用: 5,
  举例: 6,
  相关: 8,
};

/** 构图无向邻接表，用于连通子图采样和按需展开邻域。 */
function buildAdjacency(nodes: GraphNode[], edges: GraphEdge[]): Map<string, Set<string>> {
  const valid = new Set(nodes.map((n) => n.id));
  const adj = new Map<string, Set<string>>();
  for (const n of nodes) adj.set(n.id, new Set());
  for (const e of edges) {
    if (!valid.has(e.source) || !valid.has(e.target) || e.source === e.target) continue;
    adj.get(e.source)!.add(e.target);
    adj.get(e.target)!.add(e.source);
  }
  return adj;
}

/**
 * 连通聚焦子图算法：
 * 从最大核心枢纽节点（及薄弱/预警节点）出发按连通 BFS 向外生长，
 * 彻底杜绝旧版按全局度数挑点导致的「孤岛浮球、没有连线」问题。
 */
function computeConnectedCoreIds(
  nodes: GraphNode[],
  adj: Map<string, Set<string>>,
  effectiveMode: 'concept' | 'student' | 'teacher',
  limit: number,
): { ids: Set<string>; hubId: string | null } {
  if (!nodes.length) return { ids: new Set(), hubId: null };
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const importance = (id: string) => {
    const n = byId.get(id);
    return (adj.get(id)?.size || 0) * 3 + (n?.weight || 1);
  };

  const sortedByImp = [...nodes].sort((a, b) => importance(b.id) - importance(a.id));
  const hubId = sortedByImp[0]?.id || null;
  if (nodes.length <= limit) {
    return { ids: new Set(nodes.map((n) => n.id)), hubId };
  }

  const picked = new Set<string>();
  const queue: string[] = [];

  // 1) 主核心枢纽先入队，作为整张网的视觉中心
  if (hubId) {
    picked.add(hubId);
    queue.push(hubId);
  }

  // 2) 若有薄弱/遗忘/高错节点，优先纳入（并尽量拉入与主枢纽相连的桥接点）
  const priorityIds: string[] = [];
  for (const n of nodes) {
    if (effectiveMode === 'student' && (n.learning_state?.status === 'weak' || n.learning_state?.status === 'forgetting')) {
      priorityIds.push(n.id);
    } else if (effectiveMode === 'teacher' && (n.class_state?.status === 'high_error' || n.class_state?.status === 'medium_error')) {
      priorityIds.push(n.id);
    }
  }
  for (const pid of priorityIds.slice(0, 6)) {
    if (picked.size >= limit) break;
    if (!picked.has(pid)) {
      picked.add(pid);
      queue.push(pid);
      // 若与主枢纽有共同邻居，拉入 1 个桥接点保持连通
      if (hubId && !adj.get(pid)?.has(hubId)) {
        const hubNbs = adj.get(hubId);
        const bridge = [...(adj.get(pid) || [])].find((nb) => hubNbs?.has(nb));
        if (bridge && picked.size < limit) picked.add(bridge);
      }
    }
  }

  // 3) 从主枢纽出发按重要性 BFS 扩展直接邻居，保证每个节点都有清晰连线
  let head = 0;
  while (head < queue.length && picked.size < limit) {
    const cur = queue[head++];
    const nbs = [...(adj.get(cur) || [])].sort((a, b) => importance(b) - importance(a));
    // 第一轮从主枢纽取较多分支，二级节点每个最多扩 2 个，形成清晰星状骨架
    const branchCap = cur === hubId ? Math.max(8, Math.floor(limit * 0.65)) : 2;
    let added = 0;
    for (const nb of nbs) {
      if (picked.size >= limit || added >= branchCap) break;
      if (!picked.has(nb)) {
        picked.add(nb);
        queue.push(nb);
        added += 1;
      }
    }
  }

  // 4) 若主连通分量节点不足，按重要性补齐其余连通分量的核心节点及其 1 个邻居
  if (picked.size < limit) {
    for (const n of sortedByImp) {
      if (picked.size >= limit) break;
      if (picked.has(n.id)) continue;
      picked.add(n.id);
      const topNb = [...(adj.get(n.id) || [])].sort((a, b) => importance(b) - importance(a))[0];
      if (topNb && picked.size < limit) picked.add(topNb);
    }
  }

  return { ids: picked, hubId };
}

interface NativeTreeNode {
  name: string;
  id?: string;
  node?: GraphNode;
  relation?: string;
  isGroup?: boolean;
  collapsed?: boolean;
  symbol?: string;
  symbolSize?: number;
  itemStyle?: Record<string, any>;
  children?: NativeTreeNode[];
}

/**
 * 构建原生 ECharts `type: 'tree'` 所需的真正层级树结构：
 * - 以主枢纽为树根，平滑贝塞尔连线 + 圆形节点（永不变形压扁）
 * - 子节点超过 10 个时，将叶子节点按概念类型自动归组，避免一列塞 40 个字重叠
 * - 原生点击展开 / 收起分支，体验接近专业思维导图
 */
function buildNativeTree(
  nodes: GraphNode[],
  edges: GraphEdge[],
  resolveColor: (n: GraphNode) => { color: string; borderColor?: string; borderWidth?: number },
): NativeTreeNode | null {
  if (!nodes.length) return null;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const adj = buildAdjacency(nodes, edges);

  // 记录边关系方便悬浮显示
  const relMap = new Map<string, string>();
  for (const e of edges) {
    if (!byId.has(e.source) || !byId.has(e.target) || e.source === e.target) continue;
    const key1 = `${e.source}::${e.target}`;
    const key2 = `${e.target}::${e.source}`;
    const prevRank = REL_RANK[relMap.get(key1) || ''] ?? 99;
    const nextRank = REL_RANK[e.relation] ?? 9;
    if (nextRank < prevRank) {
      relMap.set(key1, e.relation);
      relMap.set(key2, e.relation);
    }
  }

  const importance = (id: string) => (adj.get(id)?.size || 0) * 3 + (byId.get(id)?.weight || 1);
  const sortedIds = nodes.map((n) => n.id).sort((a, b) => importance(b) - importance(a));

  const visited = new Set<string>();
  const buildBranch = (rootId: string, depth: number): NativeTreeNode => {
    visited.add(rootId);
    const curNode = byId.get(rootId)!;
    const style = resolveColor(curNode);

    const neighbors = [...(adj.get(rootId) || [])]
      .filter((nb) => !visited.has(nb))
      .sort((a, b) => importance(b) - importance(a));

    // 先占位防止同层互抢
    for (const nb of neighbors) visited.add(nb);

    const rawChildren: NativeTreeNode[] = neighbors.map((nb) => {
      const child = buildBranch(nb, depth + 1);
      child.relation = relMap.get(`${rootId}::${nb}`) || '相关';
      return child;
    });

    // 若某枢纽直接挂了过多节点（如主概念直连 30+ 节点），
    // 保留带子树的「骨干分支」在顶层，把纯叶子按类型折叠到分组节点下，保证一屏舒展整洁。
    let children = rawChildren;
    if (rawChildren.length > 10) {
      const withSub = rawChildren.filter((c) => (c.children?.length || 0) > 0);
      const pureLeaves = rawChildren.filter((c) => !(c.children?.length || 0));
      if (pureLeaves.length >= 5) {
        const grouped = new Map<string, NativeTreeNode[]>();
        for (const leaf of pureLeaves) {
          const t = leaf.node?.type || '相关概念';
          const arr = grouped.get(t) || [];
          arr.push(leaf);
          grouped.set(t, arr);
        }
        const groupNodes: NativeTreeNode[] = [];
        for (const [typeName, items] of grouped.entries()) {
          if (items.length <= 2) {
            groupNodes.push(...items);
          } else {
            groupNodes.push({
              name: `${typeName} (${items.length})`,
              isGroup: true,
              collapsed: true,
              symbol: 'circle',
              symbolSize: 11,
              itemStyle: {
                color: TYPE_COLORS[typeName] || '#64748b',
                borderColor: '#ffffff',
                borderWidth: 2,
              },
              children: items,
            });
          }
        }
        children = [...withSub.slice(0, 8), ...groupNodes, ...withSub.slice(8)];
      }
    }

    const hasChildren = children.length > 0;
    return {
      name: curNode.label,
      id: curNode.id,
      node: curNode,
      collapsed: depth >= 1 && hasChildren,
      symbol: 'circle',
      symbolSize: depth === 0 ? 18 : hasChildren ? 13 : 9,
      itemStyle: {
        color: style.color,
        borderColor: '#ffffff',
        borderWidth: 2,
      },
      children: hasChildren ? children : undefined,
    };
  };

  const components: NativeTreeNode[] = [];
  for (const id of sortedIds) {
    if (visited.has(id)) continue;
    components.push(buildBranch(id, 0));
  }

  if (components.length === 1) return components[0];

  // 多个不连通分量：合并到整洁的根主题下
  const main = components[0];
  const rest = components.slice(1);
  if (rest.length <= 4) {
    return {
      name: main.name,
      id: main.id,
      node: main.node,
      symbol: 'circle',
      symbolSize: 18,
      itemStyle: main.itemStyle,
      children: [
        ...(main.children || []),
        ...rest.map((r) => ({ ...r, collapsed: true })),
      ],
    };
  }
  return {
    name: main.name,
    id: main.id,
    node: main.node,
    symbol: 'circle',
    symbolSize: 18,
    itemStyle: main.itemStyle,
    children: [
      ...(main.children || []),
      {
        name: `其他独立主题 (${rest.length})`,
        isGroup: true,
        collapsed: true,
        symbol: 'circle',
        symbolSize: 11,
        itemStyle: { color: '#64748b', borderColor: '#ffffff', borderWidth: 2 },
        children: rest,
      },
    ],
  };
}

/** 根据当前可见节点数计算均衡的力导参数与初始缩放比。 */
function chartLayout(nodeCount: number, compact?: boolean): {
  height: number;
  repulsion: number;
  edgeLength: [number, number];
  gravity: number;
  initialZoom: number;
} {
  if (compact) {
    return {
      height: 430,
      repulsion: nodeCount > 30 ? 220 : 250,
      edgeLength: [68, 120],
      gravity: 0.16,
      initialZoom: nodeCount > 35 ? 0.62 : nodeCount > 18 ? 0.8 : 0.94,
    };
  }
  if (nodeCount >= 75) {
    return { height: 560, repulsion: 260, edgeLength: [75, 140], gravity: 0.12, initialZoom: 0.54 };
  }
  if (nodeCount >= 35) {
    return { height: 520, repulsion: 260, edgeLength: [75, 138], gravity: 0.14, initialZoom: 0.7 };
  }
  return { height: 480, repulsion: 290, edgeLength: [82, 145], gravity: 0.14, initialZoom: 0.92 };
}

function excerptSource(item: GraphExcerpt): string {
  const bits: string[] = [];
  if (item.file_name) bits.push(item.file_name);
  if (item.page !== undefined && item.page !== null && item.page !== '') {
    bits.push(`第 ${item.page} 页`);
  }
  return bits.join(' · ');
}

const FORMULA_HEADER_RE = /^20\d{2}[/-]\d{1,2}[/-]\d{1,2}|^《|^Dept\./;
const FORMULA_CODE_RE = /[{};]|->|\+\+|void\b|\w+\.\w+\[/;
const EQUATION_RE = /(?:\\[a-zA-Z]+|[A-Za-zα-ωΑ-Ωπβφσμτγλθ])[A-Za-zα-ωΑ-Ωπβφσμτγλθ0-9\s_^()[\]{}\\.+*/=＝<>≤≥≈°−⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉-]{2,}/g;

function isUnreadableEquation(eq: string): boolean {
  const compact = eq.replace(/\s/g, '');
  if (FORMULA_CODE_RE.test(eq)) return true;
  if (!/[=＝]/.test(compact) && !/\\[a-zA-Z]/.test(eq)) return true;
  if (/[A-Za-zα-ωΑ-Ω0-9]{6,}/.test(compact) && !/\\[a-zA-Z]/.test(eq)) return true;
  return false;
}

function renderEquation(tex: string): string | null {
  const cleaned = tex.trim();
  if (!cleaned || isUnreadableEquation(cleaned)) return null;
  try {
    return katex.renderToString(cleaned, {
      throwOnError: true,
      strict: false,
      displayMode: true,
    });
  } catch {
    return null;
  }
}

function FormulaBlock({ text }: { text: string }) {
  const rows: React.ReactNode[] = [];
  text.split(/\n/).forEach((raw, index) => {
    const line = raw.trim();
    if (!line || FORMULA_HEADER_RE.test(line)) return;
    const pieces: React.ReactNode[] = [];
    let last = 0;
    let rendered = false;
    for (const match of line.matchAll(EQUATION_RE)) {
      const html = renderEquation(match[0]);
      const at = match.index ?? 0;
      if (!html) continue;
      const gap = line.slice(last, at).trim();
      if (gap) pieces.push(<span key={`${index}-t-${last}`}>{gap}</span>);
      pieces.push(
        <span key={`${index}-m-${at}`} className="kg-formula" dangerouslySetInnerHTML={{ __html: html }} />,
      );
      last = at + match[0].length;
      rendered = true;
    }
    const tail = line.slice(last).trim();
    if (tail) pieces.push(<span key={`${index}-tail`}>{tail}</span>);
    if (rendered) {
      rows.push(<div className="kg-formula-line" key={index}>{pieces}</div>);
    } else if ((line.match(/[\u4e00-\u9fff]/g) || []).length >= 2) {
      rows.push(<div className="kg-node-pop-plain" key={index}>{line}</div>);
    }
  });
  if (!rows.length) return <div className="kg-node-pop-plain">{text}</div>;
  return <>{rows}</>;
}

function ExcerptBody({ text, formula }: { text: string; formula?: boolean }) {
  if (text.includes('$') || text.includes('\\(') || text.includes('\\[')) {
    const normalized = text
      .replace(/\\\[/g, '$$')
      .replace(/\\\]/g, '$$')
      .replace(/\\\(/g, '$')
      .replace(/\\\)/g, '$');
    return <MarkdownText text={normalized} className="kg-node-pop-md" />;
  }
  if (formula) return <FormulaBlock text={text} />;
  return <div className="kg-node-pop-plain">{text}</div>;
}

function NodePopup({
  node,
  left,
  top,
  effectiveMode,
  showExpandBtn,
  hiddenNeighborCount,
  onExpandNeighbors,
  onAction,
  onClose,
}: {
  node: GraphNode;
  left: number;
  top: number;
  effectiveMode: 'concept' | 'student' | 'teacher';
  showExpandBtn: boolean;
  hiddenNeighborCount: number;
  onExpandNeighbors: (nodeId: string) => void;
  onAction: (action: KnowledgeGraphAction) => void;
  onClose: () => void;
}) {
  const excerpts = node.excerpts || [];
  const formulas = excerpts.filter((item) => item.kind === 'formula' && item.text);
  const definitions = excerpts.filter((item) => item.kind === 'definition' && item.text);
  const passages = excerpts.filter((item) => item.kind === 'passage' && item.text);
  const st = node.learning_state;
  const cs = node.class_state;
  const diag = effectiveMode === 'teacher' ? cs?.prerequisite_diagnosis : st?.prerequisite_diagnosis;
  const firstWeakPre = diag?.weak_prerequisites?.[0]?.concept;

  const renderGroup = (title: string, items: GraphExcerpt[]) => (
    items.length ? (
      <div>
        <div className="kg-node-pop-kicker">{title}</div>
        {items.map((item, index) => (
          <div className="kg-node-pop-item" key={title + index}>
            <ExcerptBody text={item.text} formula={title === '公式'} />
            {excerptSource(item) && <div className="kg-node-pop-source">{excerptSource(item)}</div>}
          </div>
        ))}
      </div>
    ) : null
  );

  return (
    <div className="kg-node-pop" style={{ left, top, width: 350, maxHeight: 380, overflowY: 'auto' }} role="dialog" aria-label={node.label}>
      <div className="kg-node-pop-head">
        <Space size={6} wrap>
          <strong>{node.label}</strong>
          {node.type && <Tag style={{ marginInlineEnd: 0 }}>{node.type}</Tag>}
          {effectiveMode === 'student' && st && (
            <Tag color={STUDENT_HEAT_META[st.status]?.color || '#94a3b8'} style={{ marginInlineEnd: 0 }}>
              {st.status_label}
            </Tag>
          )}
          {effectiveMode === 'teacher' && cs && (
            <Tag color={TEACHER_HEAT_META[cs.status]?.color || '#94a3b8'} style={{ marginInlineEnd: 0 }}>
              {cs.status_label}
            </Tag>
          )}
        </Space>
        <button type="button" className="kg-node-pop-close" onClick={onClose} aria-label="关闭">×</button>
      </div>

      {/* 学情状态与前置知识溯因 */}
      {effectiveMode === 'student' && st && (
        <div style={{ padding: '6px 0 8px', borderBottom: '1px solid #f0f0f0', fontSize: 12 }}>
          {st.mastery_level != null ? (
            <Space size={8} wrap>
              <span>掌握估计 <strong>{Math.round(st.mastery_level * 100)}%</strong></span>
              <span>证据 {st.evidence_count} 次</span>
              {st.wrong_count > 0 && <Text type="danger">错 {st.wrong_count} 次</Text>}
              {st.bkt_p_known != null && <Text type="secondary">预测 {Math.round(st.bkt_p_known * 100)}%</Text>}
              {st.retrievability != null && (
                <Text type={st.retrievability < 0.78 ? 'warning' : 'secondary'}>
                  记忆保持 {Math.round(st.retrievability * 100)}%
                </Text>
              )}
            </Space>
          ) : (
            <Text type="secondary">尚无作答证据，点击下方直接开始学习或检测。</Text>
          )}
          {diag?.summary && (
            <div style={{ marginTop: 6, padding: '6px 8px', background: '#fff7ed', borderRadius: 6, color: '#9a3412' }}>
              {diag.summary}
            </div>
          )}
        </div>
      )}

      {effectiveMode === 'teacher' && cs && (
        <div style={{ padding: '6px 0 8px', borderBottom: '1px solid #f0f0f0', fontSize: 12 }}>
          {cs.attempt_count > 0 ? (
            <Space size={8} wrap>
              <span>错误率 <strong>{cs.wrong_rate != null ? `${Math.round(cs.wrong_rate * 100)}%` : '—'}</strong></span>
              <span>错 {cs.wrong_count} / {cs.attempt_count} 次</span>
              <span>涉及 {cs.student_count} 人</span>
              {!cs.enough_data && <Tag color="purple">样本不足</Tag>}
            </Space>
          ) : (
            <Text type="secondary">本时间范围内暂无关联作答记录。</Text>
          )}
          {diag?.summary && (
            <div style={{ marginTop: 6, padding: '6px 8px', background: '#fef2f2', borderRadius: 6, color: '#991b1b' }}>
              {diag.summary}
            </div>
          )}
        </div>
      )}

      {/* 行动入口 + 按需展开关联节点 */}
      <div style={{ padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
        <Space size={6} wrap>
          {showExpandBtn && hiddenNeighborCount > 0 && (
            <Button
              size="small"
              icon={<NodeExpandOutlined />}
              onClick={() => onExpandNeighbors(node.id)}
            >
              展开关联 (+{hiddenNeighborCount})
            </Button>
          )}
          {effectiveMode === 'teacher' ? (
            <>
              {cs?.kp_id && (
                <Button
                  size="small"
                  type="primary"
                  icon={<SearchOutlined />}
                  onClick={() => onAction({ type: 'teacher_drill', label: cs.kp_name || node.label, kpId: cs.kp_id })}
                >
                  下钻错题
                </Button>
              )}
              <Button
                size="small"
                icon={<ThunderboltOutlined />}
                onClick={() => onAction({ type: 'teacher_assign', label: cs?.kp_name || node.label, kpId: cs?.kp_id })}
              >
                布置补练
              </Button>
              <Button
                size="small"
                icon={<FileAddOutlined />}
                onClick={() => onAction({ type: 'teacher_plan', label: cs?.kp_name || node.label, kpId: cs?.kp_id })}
              >
                带入教案
              </Button>
            </>
          ) : (
            <>
              {firstWeakPre && (
                <Button
                  size="small"
                  type="primary"
                  danger
                  icon={<BookOutlined />}
                  onClick={() => onAction({ type: 'prereq', label: firstWeakPre })}
                >
                  先补前置：{firstWeakPre}
                </Button>
              )}
              <Button
                size="small"
                type={firstWeakPre ? 'default' : 'primary'}
                icon={<BookOutlined />}
                onClick={() => onAction({ type: 'explain', label: node.label, nodeId: st?.map_node_id })}
              >
                AI 精讲
              </Button>
              <Button
                size="small"
                icon={<FormOutlined />}
                onClick={() => onAction({ type: 'practice', label: node.label, nodeId: st?.map_node_id })}
              >
                练 2 题
              </Button>
            </>
          )}
        </Space>
      </div>

      {/* 原文公式与定义摘录 */}
      <div style={{ marginTop: 6 }}>
        {formulas.length || definitions.length || passages.length ? (
          <>
            {renderGroup('公式', formulas)}
            {renderGroup('定义', definitions)}
            {renderGroup('原文', passages)}
          </>
        ) : (
          <div className="kg-node-pop-empty">原文里没有直接出现该概念的独立摘录。</div>
        )}
      </div>
    </div>
  );
}

const KnowledgeGraph: React.FC<KnowledgeGraphProps> = ({
  subjectId,
  mode = 'concept',
  classId,
  days = 30,
  compact = false,
  onAction,
}) => {
  const navigate = useNavigate();
  const chartWrapRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentZoomRef = useRef<number>(0.9);

  const effectiveMode: 'concept' | 'student' | 'teacher' =
    mode === 'teacher' ? 'teacher' : (mode === 'student' || isLoggedIn() ? 'student' : 'concept');

  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState(false);
  const [progress, setProgress] = useState<{ current: number; total: number } | null>(null);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [rootCauses, setRootCauses] = useState<RootCauseItem[]>([]);
  const [hasHeatData, setHasHeatData] = useState(false);
  const [view, setView] = useState<GraphView>('force');
  const [colorMode, setColorMode] = useState<ColorMode>('heat');
  const [expandMode, setExpandMode] = useState<ExpandMode>('focus');
  const [extraVisibleIds, setExtraVisibleIds] = useState<string[]>([]);
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
  const [popup, setPopup] = useState<{ node: GraphNode; left: number; top: number } | null>(null);
  const chartReady = useRef(false);

  const adjacency = useMemo(
    () => (graph?.nodes?.length ? buildAdjacency(graph.nodes, graph.edges || []) : new Map<string, Set<string>>()),
    [graph],
  );

  const isLargeGraph = (graph?.nodes?.length || 0) > CORE_THRESHOLD;

  const { visibleNodes, visibleEdges, visibleIdSet, hubId } = useMemo(() => {
    const allNodes = graph?.nodes || [];
    const allEdges = graph?.edges || [];
    if (!allNodes.length) {
      return {
        visibleNodes: [] as GraphNode[],
        visibleEdges: [] as GraphEdge[],
        visibleIdSet: new Set<string>(),
        hubId: null as string | null,
      };
    }
    const core = computeConnectedCoreIds(
      allNodes, adjacency, effectiveMode, compact ? CORE_TARGET_COMPACT : CORE_TARGET,
    );
    if (!isLargeGraph || expandMode === 'all') {
      return {
        visibleNodes: allNodes,
        visibleEdges: allEdges,
        visibleIdSet: new Set(allNodes.map((n) => n.id)),
        hubId: core.hubId,
      };
    }
    const ids = new Set(core.ids);
    for (const id of extraVisibleIds) ids.add(id);
    const vNodes = allNodes.filter((n) => ids.has(n.id));
    const vEdges = allEdges.filter((e) => ids.has(e.source) && ids.has(e.target));
    return { visibleNodes: vNodes, visibleEdges: vEdges, visibleIdSet: ids, hubId: core.hubId };
  }, [graph, isLargeGraph, expandMode, adjacency, effectiveMode, compact, extraVisibleIds]);

  const hiddenCountOf = useCallback((nodeId: string): number => {
    const neighbors = adjacency.get(nodeId);
    if (!neighbors) return 0;
    let hidden = 0;
    neighbors.forEach((nb) => {
      if (!visibleIdSet.has(nb)) hidden += 1;
    });
    return hidden;
  }, [adjacency, visibleIdSet]);

  const expandNodeNeighbors = useCallback((nodeId: string) => {
    const neighbors = [...(adjacency.get(nodeId) || [])];
    const newlyAdded = neighbors.filter((nb) => !visibleIdSet.has(nb));
    if (!newlyAdded.length) {
      message.info('该概念的关联节点已全部展开');
      return;
    }
    // 单次展开最多增加 12 个最高关联邻点，避免点一次主枢纽把画布瞬间撑爆
    const sorted = newlyAdded.sort((a, b) => (adjacency.get(b)?.size || 0) - (adjacency.get(a)?.size || 0));
    const batch = sorted.slice(0, 12);
    setExtraVisibleIds((prev) => Array.from(new Set([...prev, nodeId, ...batch])));
    const label = graph?.nodes?.find((n) => n.id === nodeId)?.label || '';
    const remain = newlyAdded.length - batch.length;
    message.success(
      remain > 0
        ? `已展开「${label}」的 ${batch.length} 个核心关联（还有 ${remain} 个，可再次双击继续展开）`
        : `已展开「${label}」的 ${batch.length} 个关联概念`,
    );
  }, [adjacency, visibleIdSet, graph]);

  const resolveBaseColor = useCallback((n: GraphNode) => {
    if (colorMode === 'heat') {
      if (effectiveMode === 'teacher' && n.class_state) {
        const meta = TEACHER_HEAT_META[n.class_state.status] || TEACHER_HEAT_META.unassessed;
        return {
          color: meta.color,
          borderColor: n.class_state.status === 'high_error' ? '#991b1b' : '#ffffff',
          borderWidth: n.class_state.status === 'high_error' ? 3 : 2.5,
        };
      }
      if (effectiveMode === 'student' && n.learning_state) {
        const meta = STUDENT_HEAT_META[n.learning_state.status] || STUDENT_HEAT_META.unassessed;
        const alert = n.learning_state.status === 'forgetting' || n.learning_state.status === 'weak';
        return {
          color: meta.color,
          borderColor: alert ? (meta.border || '#ffffff') : '#ffffff',
          borderWidth: alert ? 3 : 2.5,
        };
      }
    }
    return {
      color: TYPE_COLORS[n.type || '其他'] || TYPE_COLORS['其他'],
      borderColor: '#ffffff',
      borderWidth: 2.5,
    };
  }, [colorMode, effectiveMode]);

  const resolveForceNodeStyle = useCallback((n: GraphNode, hasHidden: boolean) => {
    const base = resolveBaseColor(n);
    return {
      ...base,
      shadowBlur: hasHidden ? 12 : 7,
      shadowColor: hasHidden ? 'rgba(79, 70, 229, 0.28)' : 'rgba(15, 23, 42, 0.12)',
    };
  }, [resolveBaseColor]);

  const nativeTreeRoot = useMemo(
    () => (graph?.nodes?.length ? buildNativeTree(graph.nodes, graph.edges || [], resolveBaseColor) : null),
    [graph, resolveBaseColor],
  );

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const loadGraph = useCallback(async () => {
    if (!subjectId) {
      setGraph(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setPopup(null);
    setExtraVisibleIds([]);
    setFocusedNodeId(null);
    setExpandMode('focus');
    try {
      if (effectiveMode === 'teacher' && classId) {
        const res = await getClassAnalyticsGraph(classId, days);
        const overlay = res?.data;
        if (overlay?.exists && overlay.graph?.nodes?.length) {
          setGraph(overlay.graph);
          setRootCauses(overlay.root_causes || []);
          const covered = (overlay.summary?.covered_nodes || 0) > 0;
          setHasHeatData(covered);
          setColorMode(covered ? 'heat' : 'type');
          return;
        }
      } else if (effectiveMode === 'student' && isLoggedIn()) {
        const res = await getStudentKnowledgeGraph(subjectId);
        const overlay = res?.data;
        if (overlay?.exists && overlay.graph?.nodes?.length) {
          setGraph(overlay.graph);
          setRootCauses(overlay.root_causes || []);
          const covered = (overlay.summary?.covered_nodes || 0) > 0;
          setHasHeatData(covered);
          setColorMode(covered ? 'heat' : 'type');
          return;
        }
      }
      const res = await getKnowledgeGraph(subjectId);
      const data = res?.data ?? res;
      if (data?.nodes) {
        setGraph(data);
        setRootCauses([]);
        setHasHeatData(false);
        setColorMode('type');
      } else {
        setGraph(null);
        setRootCauses([]);
      }
    } catch {
      try {
        const res = await getKnowledgeGraph(subjectId);
        const data = res?.data ?? res;
        setGraph(data?.nodes ? data : null);
      } catch {
        setGraph(null);
      }
      setRootCauses([]);
      setHasHeatData(false);
    } finally {
      setLoading(false);
    }
  }, [subjectId, effectiveMode, classId, days]);

  const handleAction = useCallback((action: KnowledgeGraphAction) => {
    setPopup(null);
    if (onAction) {
      onAction(action);
      return;
    }
    if (action.type === 'explain' || action.type === 'practice' || action.type === 'prereq') {
      const qs = new URLSearchParams();
      qs.set('subject', subjectId);
      if (action.nodeId) qs.set('node', action.nodeId);
      else qs.set('kp', action.label);
      qs.set('action', action.type);
      navigate(`/student?${qs.toString()}`);
    }
  }, [navigate, onAction, subjectId]);

  const pollBuildStatus = useCallback(async () => {
    try {
      const res: any = await getKnowledgeGraphStatus(subjectId);
      const data = res?.data ?? res;
      const status = data?.status;
      if (status === 'success') {
        stopPolling();
        setBuilding(false);
        setProgress(null);
        message.success('知识图谱生成完成');
        loadGraph();
      } else if (status === 'failed') {
        stopPolling();
        setBuilding(false);
        setProgress(null);
        message.error(data?.message || '知识图谱生成失败，请重试');
      } else {
        const current = data?.processed_chunks;
        const total = data?.total_chunks;
        if (typeof current === 'number' && typeof total === 'number') {
          setProgress({ current, total });
        }
        pollTimer.current = setTimeout(pollBuildStatus, 2500);
      }
    } catch {
      stopPolling();
      setBuilding(false);
      setProgress(null);
      message.error('查询图谱生成状态失败，请刷新后重试');
    }
  }, [subjectId, loadGraph, stopPolling]);

  const handleBuild = async () => {
    setBuilding(true);
    setProgress(null);
    try {
      const res: any = await buildKnowledgeGraph(subjectId);
      const data = res?.data ?? res;
      const status = data?.status;
      if (status === 'queued' || status === 'building') {
        message.info('知识图谱生成任务已提交，正在后台生成...');
        pollTimer.current = setTimeout(pollBuildStatus, 2500);
      } else if (status === 'success') {
        setBuilding(false);
        message.success('知识图谱生成完成');
        loadGraph();
      } else if (status === 'failed') {
        setBuilding(false);
        message.error(data?.message || '知识图谱生成失败');
      } else {
        setBuilding(false);
        message.warning(data?.message || '任务未能提交，请稍后再试');
      }
    } catch (error: any) {
      setBuilding(false);
      const detail = error?.response?.data?.message
        || error?.response?.data?.detail
        || '提交知识图谱生成任务失败';
      if (error?.response?.status === 409) message.warning(detail);
      else message.error(detail);
    }
  };

  useEffect(() => {
    loadGraph();
    return () => stopPolling();
  }, [loadGraph, stopPolling]);

  const applyZoom = useCallback((factor: number) => {
    if (!chartInstance.current) return;
    const nextZoom = Math.max(0.3, Math.min(3.0, Number((currentZoomRef.current * factor).toFixed(2))));
    currentZoomRef.current = nextZoom;
    chartInstance.current.setOption({ series: [{ zoom: nextZoom }] });
  }, []);

  const resetViewport = useCallback(() => {
    if (!chartInstance.current) return;
    const defaultZoom = view === 'tree' ? 1 : chartLayout(visibleNodes.length, compact).initialZoom;
    currentZoomRef.current = defaultZoom;
    chartInstance.current.setOption({ series: [{ zoom: defaultZoom, center: undefined }] });
  }, [visibleNodes.length, compact, view]);

  const handleSearchLocate = useCallback((nodeId: string | undefined) => {
    setFocusedNodeId(nodeId || null);
    if (!nodeId || !graph?.nodes?.length) {
      setPopup(null);
      return;
    }
    const target = graph.nodes.find((n) => n.id === nodeId);
    if (!target) return;
    if (view === 'force' && !visibleIdSet.has(nodeId)) {
      const neighbors = [...(adjacency.get(nodeId) || [])].slice(0, 6);
      setExtraVisibleIds((prev) => Array.from(new Set([...prev, nodeId, ...neighbors])));
    }
    setPopup({ node: target, left: 16, top: 16 });
  }, [graph, view, visibleIdSet, adjacency]);

  useEffect(() => {
    if (!chartRef.current || !graph?.nodes?.length) return;
    if (view === 'force' && !visibleNodes.length) return;
    if (view === 'tree' && !nativeTreeRoot) return;

    const delay = chartReady.current ? 0 : 120;
    const timer = setTimeout(() => {
      if (!chartRef.current) return;
      chartReady.current = true;

      if (!chartInstance.current) {
        chartInstance.current = echarts.init(chartRef.current);
      }

      const maxWeight = Math.max(...visibleNodes.map((n) => n.weight || 1), 1);
      const minWeight = Math.min(...visibleNodes.map((n) => n.weight || 1), 1);
      const tooltip = {
        trigger: 'item' as const,
        backgroundColor: 'rgba(255, 255, 255, 0.96)',
        borderColor: '#e2e8f0',
        borderWidth: 1,
        textStyle: { color: '#1e293b', fontSize: 12 },
        formatter: (params: any) => {
          const dataObj = params.data || {};
          if (dataObj.isGroup) {
            return `<b>${dataObj.name}</b><br/><span style="color:#4f46e5">点击节点可展开 / 收起此分类分支</span>`;
          }
          const node = (dataObj.node || dataObj.value) as GraphNode | undefined;
          if (node?.label) {
            const lines = [`<b>${node.label}</b>${node.type ? ` <span style="color:#64748b">(${node.type})</span>` : ''}`];
            if (effectiveMode === 'student' && node.learning_state) {
              const s = node.learning_state;
              lines.push(s.mastery_level != null
                ? `掌握：${s.status_label}（约 ${Math.round(s.mastery_level * 100)}%）`
                : '掌握：暂未评估');
            } else if (effectiveMode === 'teacher' && node.class_state) {
              const c = node.class_state;
              lines.push(c.attempt_count > 0 && c.wrong_rate != null
                ? `班级：${c.status_label}（错率 ${Math.round(c.wrong_rate * 100)}%，作答 ${c.attempt_count} 次）`
                : '班级：暂无作答');
            }
            if (view === 'force') {
              const hiddenN = hiddenCountOf(node.id);
              if (hiddenN > 0) {
                lines.push(`<span style="color:#4f46e5">还有 ${hiddenN} 个隐藏关联 · 双击逐层展开</span>`);
              }
              lines.push('<span style="color:#64748b">单击查看原文摘录与学习入口</span>');
            } else {
              if (dataObj.relation) {
                lines.push(`关系：${dataObj.relation}`);
              }
              if (dataObj.children?.length) {
                lines.push(`<span style="color:#4f46e5">含 ${dataObj.children.length} 个下级分支 · 单击展开/收起</span>`);
              }
              lines.push('<span style="color:#64748b">双击节点查看诊断与学习入口</span>');
            }
            return lines.join('<br/>');
          }
          if (params.dataType === 'edge') {
            const edge = params.data;
            const sLabel = visibleNodes.find((n) => n.id === edge.source)?.label || edge.source;
            const tLabel = visibleNodes.find((n) => n.id === edge.target)?.label || edge.target;
            return `${sLabel} —[${edge.relation}]→ ${tLabel}`;
          }
          return '';
        },
      };

      const layoutCfg = chartLayout(visibleNodes.length, compact);
      currentZoomRef.current = view === 'tree' ? 1 : layoutCfg.initialZoom;

      const option: echarts.EChartsOption = view === 'tree' && nativeTreeRoot
        ? {
            tooltip,
            animationDuration: 350,
            animationDurationUpdate: 350,
            series: [
              {
                type: 'tree' as const,
                data: [nativeTreeRoot as any],
                orient: 'LR',
                roam: true,
                zoom: 1,
                scaleLimit: { min: 0.4, max: 2.5 },
                top: '6%',
                bottom: '6%',
                left: '9%',
                right: '22%',
                edgeShape: 'curve',
                expandAndCollapse: true,
                initialTreeDepth: 1,
                lineStyle: {
                  color: '#cbd5e1',
                  width: 1.5,
                  curveness: 0.45,
                },
                label: {
                  position: 'top',
                  verticalAlign: 'middle',
                  align: 'center',
                  distance: 8,
                  fontSize: 12,
                  fontWeight: 500,
                  color: '#1e293b',
                  backgroundColor: 'rgba(255,255,255,0.88)',
                  padding: [2, 6],
                  borderRadius: 4,
                },
                leaves: {
                  label: {
                    position: 'right',
                    verticalAlign: 'middle',
                    align: 'left',
                    distance: 8,
                    fontSize: 12,
                    fontWeight: 400,
                    color: '#334155',
                    backgroundColor: 'rgba(255,255,255,0.88)',
                    padding: [2, 6],
                    borderRadius: 4,
                  },
                },
                emphasis: {
                  focus: 'descendant' as const,
                  lineStyle: { color: '#6366f1', width: 2.2 },
                },
              },
            ],
          }
        : {
            tooltip,
            animationDuration: 300,
            series: [
              {
                type: 'graph' as const,
                layout: 'force' as const,
                roam: true,
                draggable: true,
                zoom: layoutCfg.initialZoom,
                scaleLimit: { min: 0.3, max: 3.0 },
                left: 30,
                right: 30,
                top: 24,
                bottom: 24,
                edgeSymbol: ['none', 'arrow'],
                edgeSymbolSize: [0, 6],
                autoCurveness: 0.14,
                data: visibleNodes.map((n) => {
                  const hiddenN = hiddenCountOf(n.id);
                  const isHub = n.id === hubId;
                  return {
                    id: n.id,
                    name: formatNodeLabel(n.label, isHub ? 10 : 7),
                    symbolSize: symbolSizeForWeight(n.weight, minWeight, maxWeight, isHub),
                    itemStyle: resolveForceNodeStyle(n, hiddenN > 0),
                    label: isHub
                      ? { fontSize: 13, fontWeight: 700, color: '#0f172a' }
                      : undefined,
                    node: n,
                    value: n,
                  };
                }) as any,
                links: visibleEdges.map((e) => ({
                  source: e.source,
                  target: e.target,
                  relation: e.relation,
                  value: e.weight || 1,
                  label: { show: false, formatter: e.relation, fontSize: 11, color: '#475569' },
                  lineStyle: { width: Math.min(2.8, 1.2 + (e.weight || 1) * 0.45) },
                })) as any,
                label: {
                  show: true,
                  position: 'bottom',
                  distance: 6,
                  fontSize: 11.5,
                  fontWeight: 500,
                  color: '#1e293b',
                  lineHeight: 15,
                  backgroundColor: 'rgba(255, 255, 255, 0.85)',
                  padding: [2, 5],
                  borderRadius: 4,
                },
                labelLayout: visibleNodes.length > 32
                  ? { hideOverlap: true, moveOverlap: 'shiftY' }
                  : { moveOverlap: 'shiftY' },
                edgeLabel: { show: false },
                lineStyle: { color: '#cbd5e1', opacity: 0.85 },
                force: {
                  initLayout: 'circular',
                  repulsion: layoutCfg.repulsion,
                  edgeLength: layoutCfg.edgeLength,
                  gravity: layoutCfg.gravity,
                  friction: 0.4,
                  layoutAnimation: false,
                },
                emphasis: {
                  focus: 'adjacency' as const,
                  scale: 1.12,
                  label: { show: true, fontWeight: 600 },
                  edgeLabel: { show: true, fontSize: 11, color: '#4f46e5', backgroundColor: '#ffffff', padding: [1, 4], borderRadius: 3 },
                  lineStyle: { color: '#6366f1', opacity: 1, width: 2.2 },
                },
                blur: {
                  itemStyle: { opacity: 0.2 },
                  label: { opacity: 0.15 },
                  lineStyle: { opacity: 0.08 },
                },
              },
            ],
          };

      chartInstance.current.setOption(option, true);
      chartInstance.current.resize();

      if (view === 'force' && focusedNodeId) {
        const idx = visibleNodes.findIndex((n) => n.id === focusedNodeId);
        if (idx >= 0) {
          chartInstance.current.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: idx });
        }
      }

      const openPopupAt = (node: GraphNode, params: any) => {
        const ev = params.event?.event || params.event;
        const wrap = chartWrapRef.current;
        const viewW = wrap?.clientWidth || 640;
        const viewH = wrap?.clientHeight || 480;
        const ox = Number(ev?.offsetX ?? 24);
        const oy = Number(ev?.offsetY ?? 24);
        const boxW = 350;
        const boxH = 300;
        let left = ox + 14;
        let top = oy + 14;
        if (left + boxW > viewW - 8) left = Math.max(8, ox - boxW - 14);
        if (top + boxH > viewH - 8) top = Math.max(8, oy - boxH - 14);
        setPopup({ node, left, top });
      };

      chartInstance.current.off('graphroam');
      chartInstance.current.on('graphroam', (params: any) => {
        if (typeof params?.zoom === 'number') {
          currentZoomRef.current = Math.max(0.3, Math.min(3.0, currentZoomRef.current * params.zoom));
        }
      });

      chartInstance.current.off('click');
      chartInstance.current.on('click', (params: any) => {
        const dataObj = params.data || {};
        if (dataObj.isGroup) return;
        const node = (dataObj.node || dataObj.value) as GraphNode | undefined;
        if (!node?.label) {
          setPopup(null);
          return;
        }
        // 层级树视图里，有子节点的分支单击由 ECharts 原生展开/收起；叶子节点单击直接弹诊断卡
        if (view === 'tree' && dataObj.children?.length) {
          return;
        }
        openPopupAt(node, params);
      });

      chartInstance.current.off('dblclick');
      chartInstance.current.on('dblclick', (params: any) => {
        const dataObj = params.data || {};
        if (dataObj.isGroup) return;
        const node = (dataObj.node || dataObj.value) as GraphNode | undefined;
        if (!node?.id) return;
        if (view === 'force') {
          expandNodeNeighbors(node.id);
        } else {
          openPopupAt(node, params);
        }
      });
    }, delay);

    return () => {
      clearTimeout(timer);
      chartInstance.current?.dispose();
      chartInstance.current = null;
    };
  }, [graph, visibleNodes, visibleEdges, hubId, view, nativeTreeRoot, colorMode, compact, effectiveMode, resolveForceNodeStyle, hiddenCountOf, expandNodeNeighbors, focusedNodeId]);

  useEffect(() => {
    const handleResize = () => chartInstance.current?.resize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const totalNodes = graph?.nodes?.length || 0;
  const shownNodes = visibleNodes.length;
  const chartHeight = view === 'tree' ? (compact ? 450 : 540) : chartLayout(visibleNodes.length, compact).height;

  const body = loading ? (
    <div style={{ padding: 24 }}>
      <SkeletonCard height={40} />
      <div style={{ height: 12 }} />
      <SkeletonCard height={260} />
    </div>
  ) : !graph?.nodes?.length ? (
    <Empty
      description={
        <div>
          <div>暂无知识图谱</div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            点击右上角「生成知识图谱」后，可叠加{effectiveMode === 'teacher' ? '班级错题热力与前置溯因' : '个人掌握度热力、前置溯因与检索增强'}
          </Text>
        </div>
      }
      style={{ padding: 36 }}
    />
  ) : (
    <>
      {rootCauses.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 10 }}
          message={`图谱前置溯因（发现 ${rootCauses.length} 处前置薄弱链）`}
          description={
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              {rootCauses.slice(0, 2).map((rc) => {
                const firstPre = rc.weak_prerequisites?.[0]?.concept;
                return (
                  <Space key={rc.concept} size={6} wrap>
                    <Text style={{ fontSize: 12 }}>{rc.summary}</Text>
                    {firstPre && (
                      <Button
                        size="small"
                        type="link"
                        style={{ padding: 0, height: 'auto' }}
                        onClick={() => handleAction(
                          effectiveMode === 'teacher'
                            ? { type: 'teacher_assign', label: firstPre }
                            : { type: 'prereq', label: firstPre },
                        )}
                      >
                        {effectiveMode === 'teacher' ? `给全班补练「${firstPre}」` : `先补前置「${firstPre}」`}
                      </Button>
                    )}
                  </Space>
                );
              })}
            </Space>
          }
        />
      )}

      {/* 顶部控制条：视图 + 热力配色 + 渐进展开切换 + 搜索定位 + 缩放复位 */}
      <div className="kg-view-bar" style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <Space size={8} wrap>
          <Segmented
            size="small"
            value={view}
            onChange={(next) => {
              setPopup(null);
              setView(next as GraphView);
            }}
            options={[
              { label: '关系网', value: 'force' },
              { label: '层级树', value: 'tree' },
            ]}
          />
          {effectiveMode !== 'concept' && (
            <Segmented
              size="small"
              value={colorMode}
              onChange={(next) => setColorMode(next as ColorMode)}
              options={[
                { label: effectiveMode === 'teacher' ? '班级热力' : '掌握热力', value: 'heat' },
                { label: '概念类型', value: 'type' },
              ]}
            />
          )}
          {view === 'force' && isLargeGraph && (
            <Segmented
              size="small"
              value={expandMode}
              onChange={(next) => {
                setPopup(null);
                const modeNext = next as ExpandMode;
                setExpandMode(modeNext);
                if (modeNext === 'focus') setExtraVisibleIds([]);
              }}
              options={[
                { label: <Space size={4}><CompressOutlined /><span>聚焦核心 ({shownNodes}/{totalNodes})</span></Space>, value: 'focus' },
                { label: <Space size={4}><ExpandOutlined /><span>展开全部 ({totalNodes})</span></Space>, value: 'all' },
              ]}
            />
          )}
        </Space>

        <Space size={6} wrap>
          <Select
            size="small"
            showSearch
            allowClear
            placeholder="搜索定位概念…"
            style={{ width: 175 }}
            value={focusedNodeId || undefined}
            optionFilterProp="label"
            onChange={(val) => handleSearchLocate(val as string | undefined)}
            options={graph.nodes.map((n) => ({ value: n.id, label: n.label }))}
          />
          <Tooltip title="放大">
            <Button size="small" icon={<ZoomInOutlined />} onClick={() => applyZoom(1.22)} />
          </Tooltip>
          <Tooltip title="缩小">
            <Button size="small" icon={<ZoomOutOutlined />} onClick={() => applyZoom(0.82)} />
          </Tooltip>
          <Tooltip title="居中适配视野">
            <Button size="small" icon={<AimOutlined />} onClick={resetViewport}>适配</Button>
          </Tooltip>
        </Space>
      </div>

      <div className="kg-chart-wrap is-force" ref={chartWrapRef}>
        <div
          ref={chartRef}
          className="kg-chart"
          style={{ height: chartHeight }}
        />
        {popup && (
          <NodePopup
            node={popup.node}
            left={popup.left}
            top={popup.top}
            effectiveMode={effectiveMode}
            showExpandBtn={view === 'force'}
            hiddenNeighborCount={hiddenCountOf(popup.node.id)}
            onExpandNeighbors={expandNodeNeighbors}
            onAction={handleAction}
            onClose={() => setPopup(null)}
          />
        )}
      </div>

      <div style={{ marginTop: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <Space size={6} wrap>
          {colorMode === 'heat' && effectiveMode === 'teacher'
            ? Object.entries(TEACHER_HEAT_META).map(([k, v]) => (
                <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#475569' }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: v.color }} />
                  {v.label}
                </span>
              ))
            : colorMode === 'heat' && effectiveMode === 'student'
              ? Object.entries(STUDENT_HEAT_META).map(([k, v]) => (
                  <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#475569' }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: v.color }} />
                    {v.label}
                  </span>
                ))
              : Object.entries(TYPE_COLORS)
                  .filter(([k]) => graph.nodes.some((n) => (n.type || '其他') === k))
                  .map(([type, color]) => (
                    <span key={type} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#475569' }}>
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: color }} />
                      {type}
                    </span>
                  ))}
        </Space>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {effectiveMode !== 'concept' && !hasHeatData && colorMode === 'heat'
            ? '尚无匹配到图谱节点的作答记录，完成练习后节点会自动按掌握情况着色'
            : view === 'tree'
              ? '单击分支节点可平滑展开/收起下级；单击叶子或双击分支查看诊断与学习入口'
              : isLargeGraph && expandMode === 'focus'
                ? '默认从核心主题连通展开；双击带光晕节点可逐层扩出关联，单击查看诊断'
                : '滚轮缩放、拖拽平移；单击节点查看诊断与学习入口，点「适配」回中'}
        </Text>
      </div>
    </>
  );

  const actions = (
    <Space>
      {building ? (
        <Button size="small" disabled icon={<Spin size="small" />}>
          {progress && progress.total > 0
            ? `正在抽取知识关系：${progress.current} / ${progress.total}`
            : '知识图谱生成中...'}
        </Button>
      ) : graph?.nodes?.length ? (
        <Popconfirm
          title="重新生成知识图谱会调用大模型，是否继续？"
          onConfirm={handleBuild}
          okText="生成"
          cancelText="取消"
        >
          <Button size="small" icon={<PlayCircleOutlined />}>重新生成</Button>
        </Popconfirm>
      ) : (
        <Popconfirm
          title="生成知识图谱需要调用大模型，是否继续？"
          onConfirm={handleBuild}
          okText="生成"
          cancelText="取消"
        >
          <Button size="small" type="primary" icon={<PlayCircleOutlined />}>生成知识图谱</Button>
        </Popconfirm>
      )}
      <Button size="small" icon={<ReloadOutlined />} onClick={loadGraph} loading={loading}>
        刷新
      </Button>
    </Space>
  );

  if (compact) {
    return (
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <Space size={6}>
            <ApiOutlined />
            <Text strong>知识图谱</Text>
            {graph?.stats && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {view === 'force' && shownNodes < totalNodes ? `当前 ${shownNodes} / 共 ${totalNodes} 节点` : `${totalNodes} 节点`} · {graph.stats.edge_count} 边
              </Text>
            )}
          </Space>
          {actions}
        </div>
        {body}
      </div>
    );
  }

  return (
    <Card
      size="small"
      title={
        <Space>
          <ApiOutlined />
          {effectiveMode === 'teacher' ? '班级知识图谱热力视图' : '知识图谱（学情热力与概念关系）'}
          {graph?.stats && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {view === 'force' && shownNodes < totalNodes ? `聚焦 ${shownNodes} / 共 ${totalNodes} 节点` : `${totalNodes} 节点`} / {graph.stats.edge_count} 边
            </Text>
          )}
        </Space>
      }
      extra={actions}
    >
      {body}
    </Card>
  );
};

export default KnowledgeGraph;
