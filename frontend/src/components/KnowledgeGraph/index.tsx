import React, { useEffect, useRef, useState, useCallback } from 'react';
import * as echarts from 'echarts';
import { Button, Card, Empty, Popconfirm, Segmented, Spin, Tag, Space, Typography, message } from 'antd';
import { ApiOutlined, ReloadOutlined, PlayCircleOutlined } from '@ant-design/icons';
import katex from 'katex';
import { SkeletonCard } from '../AnimateMotion';
import { MarkdownText } from '../TutorWorkspace/markdown';
import { getKnowledgeGraph, buildKnowledgeGraph, getKnowledgeGraphStatus } from '../../api/knowledge';

const { Text } = Typography;

interface GraphExcerpt {
  kind: 'formula' | 'definition' | string;
  text: string;
  file_name?: string;
  page?: number | string | null;
}

interface GraphNode {
  id: string;
  label: string;
  type?: string;
  weight?: number;
  content?: string;
  source?: string;
  excerpts?: GraphExcerpt[];
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

// 节点类型颜色映射（CiteSpace风格）
const TYPE_COLORS: Record<string, string> = {
  '概念': '#5470c6',
  '公式': '#91cc75',
  '定理': '#fac858',
  '例题': '#ee6666',
  '方法': '#73c0de',
  '人物': '#3ba272',
  '其他': '#999999',
};

/** 节点直径仍按权重线性拉开，不压小高权重节点。 */
function symbolSizeForWeight(weight: number | undefined, minWeight: number, maxWeight: number): number {
  const span = maxWeight - minWeight || 1;
  return 20 + ((weight || 1) - minWeight) / span * 30;
}

/** 线宽仍按边权重。球线图里权重越大，边越短。 */
function edgeWidthForWeight(weight: number | undefined): number {
  return (weight || 1) * 1.5;
}

type GraphView = 'force' | 'tree';

const TREE_ROW = 64;
const TREE_COL = 220;
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

interface TreeLayout {
  positions: Map<string, { x: number; y: number }>;
  links: GraphEdge[];
  width: number;
  height: number;
}

/** 每个节点只挂到一条主线上，从左到右铺开。方块大小仍由调用方按权重计算。 */
function buildTreeLayout(nodes: GraphNode[], edges: GraphEdge[]): TreeLayout {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const incoming = new Map<string, GraphEdge[]>();
  for (const n of nodes) incoming.set(n.id, []);
  for (const edge of edges) {
    if (!byId.has(edge.source) || !byId.has(edge.target) || edge.source === edge.target) continue;
    // 包含/定义/推导/举例：左边是总概念。属于/前置条件/应用：原文箭头指向总概念，树里反过来。
    let parentId = edge.source;
    let childId = edge.target;
    if (edge.relation === '属于' || edge.relation === '前置条件' || edge.relation === '应用') {
      parentId = edge.target;
      childId = edge.source;
    } else if (edge.relation === '相关') {
      const targetWeight = byId.get(edge.target)?.weight || 1;
      const sourceWeight = byId.get(edge.source)?.weight || 1;
      if (targetWeight > sourceWeight) {
        parentId = edge.target;
        childId = edge.source;
      }
    }
    incoming.get(childId)!.push({
      source: parentId,
      target: childId,
      relation: edge.relation,
      weight: edge.weight,
    });
  }
  for (const list of incoming.values()) {
    list.sort((a, b) => {
      const rank = (REL_RANK[a.relation] ?? 9) - (REL_RANK[b.relation] ?? 9);
      if (rank !== 0) return rank;
      const parentWeight = (byId.get(b.source)?.weight || 1) - (byId.get(a.source)?.weight || 1);
      if (parentWeight !== 0) return parentWeight;
      return (b.weight || 1) - (a.weight || 1);
    });
  }

  const parent = new Map<string, string>();
  const parentEdge = new Map<string, GraphEdge>();
  const wouldCycle = (id: string, candidate: string) => {
    let cursor: string | undefined = candidate;
    const seen = new Set<string>();
    while (cursor) {
      if (cursor === id || seen.has(cursor)) return true;
      seen.add(cursor);
      cursor = parent.get(cursor);
    }
    return false;
  };
  const pending = new Set(nodes.map((n) => n.id));
  let guard = pending.size + 2;
  while (pending.size && guard-- > 0) {
    let progressed = false;
    for (const id of [...pending]) {
      const options = incoming.get(id) || [];
      if (!options.length) {
        pending.delete(id);
        progressed = true;
        continue;
      }
      const ready = options.find((edge) => !pending.has(edge.source) && !wouldCycle(id, edge.source));
      const settled = options.some((edge) => pending.has(edge.source));
      const chosen = ready || (!settled ? options.find((edge) => !wouldCycle(id, edge.source)) : undefined);
      if (!chosen && settled) continue;
      if (chosen) {
        parent.set(id, chosen.source);
        parentEdge.set(id, chosen);
      }
      pending.delete(id);
      progressed = true;
    }
    if (!progressed) {
      for (const id of [...pending]) {
        const chosen = (incoming.get(id) || []).find((edge) => !wouldCycle(id, edge.source));
        if (chosen) {
          parent.set(id, chosen.source);
          parentEdge.set(id, chosen);
        }
        pending.delete(id);
      }
    }
  }

  const children = new Map<string, string[]>();
  for (const n of nodes) children.set(n.id, []);
  for (const [id, source] of parent) children.get(source)?.push(id);
  const byWeight = (a: string, b: string) => {
    const delta = (byId.get(b)?.weight || 1) - (byId.get(a)?.weight || 1);
    if (delta !== 0) return delta;
    return (byId.get(a)?.label || a).localeCompare(byId.get(b)?.label || b, 'zh');
  };
  for (const list of children.values()) list.sort(byWeight);

  const roots = nodes.map((n) => n.id).filter((id) => !parent.has(id)).sort(byWeight);
  const rows = new Map<string, number>();
  const measure = (id: string): number => {
    const kids = children.get(id) || [];
    const count = kids.length ? kids.reduce((sum, kid) => sum + measure(kid), 0) : 1;
    rows.set(id, count);
    return count;
  };
  for (const id of roots) measure(id);

  const positions = new Map<string, { x: number; y: number }>();
  let maxDepth = 0;
  const place = (id: string, depth: number, topRow: number) => {
    maxDepth = Math.max(maxDepth, depth);
    const kids = children.get(id) || [];
    const x = 36 + depth * TREE_COL;
    if (!kids.length) {
      positions.set(id, { x, y: 28 + topRow * TREE_ROW + TREE_ROW / 2 });
      return;
    }
    let cursor = topRow;
    for (const kid of kids) {
      place(kid, depth + 1, cursor);
      cursor += rows.get(kid) || 1;
    }
    const first = positions.get(kids[0]);
    const last = positions.get(kids[kids.length - 1]);
    positions.set(id, { x, y: first && last ? (first.y + last.y) / 2 : 28 + TREE_ROW / 2 });
  };
  let top = 0;
  for (const id of roots) {
    place(id, 0, top);
    top += rows.get(id) || 1;
  }

  return {
    positions,
    links: [...parentEdge.values()],
    width: 36 + maxDepth * TREE_COL + 210,
    height: Math.max(TREE_ROW + 48, top * TREE_ROW + 48),
  };
}

function chartLayout(nodeCount: number): {
  height: number;
  repulsion: number;
  edgeLength: number | [number, number];
  gravity: number;
} {
  if (nodeCount >= 80) {
    return { height: 640, repulsion: 560, edgeLength: [90, 200], gravity: 0.035 };
  }
  if (nodeCount >= 30) {
    return { height: 520, repulsion: 340, edgeLength: [70, 160], gravity: 0.07 };
  }
  return { height: 420, repulsion: 180, edgeLength: 110, gravity: 0.12 };
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
  // 幻灯片抽文本常把公式挤成一长串，没有运算符，排出来也不是公式。
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
  node, left, top, onClose,
}: {
  node: GraphNode;
  left: number;
  top: number;
  onClose: () => void;
}) {
  const excerpts = node.excerpts || [];
  const formulas = excerpts.filter((item) => item.kind === 'formula' && item.text);
  const definitions = excerpts.filter((item) => item.kind === 'definition' && item.text);
  const passages = excerpts.filter((item) => item.kind === 'passage' && item.text);
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
    <div className="kg-node-pop" style={{ left, top }} role="dialog" aria-label={node.label}>
      <div className="kg-node-pop-head">
        <strong>{node.label}</strong>
        <button type="button" className="kg-node-pop-close" onClick={onClose} aria-label="关闭">×</button>
      </div>
      {formulas.length || definitions.length || passages.length ? (
        <>
          {renderGroup('公式', formulas)}
          {renderGroup('定义', definitions)}
          {renderGroup('原文', passages)}
        </>
      ) : (
        <div className="kg-node-pop-empty">原文里没有直接出现这个概念。</div>
      )}
    </div>
  );
}

const KnowledgeGraph: React.FC<{ subjectId: string }> = ({ subjectId }) => {
  const chartWrapRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState(false);
  const [progress, setProgress] = useState<{ current: number; total: number } | null>(null);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [view, setView] = useState<GraphView>('force');
  const [popup, setPopup] = useState<{ node: GraphNode; left: number; top: number } | null>(null);
  const chartReady = useRef(false);
  const treeLayout = graph?.nodes?.length ? buildTreeLayout(graph.nodes, graph.edges || []) : null;

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const loadGraph = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getKnowledgeGraph(subjectId);
      // 兼容 axios 拦截器解包后的格式
      const data = res?.data ?? res;
      if (data?.nodes) {
        setGraph(data);
      } else {
        setGraph(null);
      }
    } catch {
      setGraph(null);
    } finally {
      setLoading(false);
    }
  }, [subjectId]);

  // 轮询图谱构建状态
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
        // queued / building：展示进度（不显示假百分比）
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

  // 提交图谱构建任务
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
        // 409 已构建中 / busy 等
        setBuilding(false);
        message.warning(data?.message || '任务未能提交，请稍后再试');
      }
    } catch (error: any) {
      setBuilding(false);
      const detail = error?.response?.data?.message
        || error?.response?.data?.detail
        || '提交知识图谱生成任务失败';
      // 409 = 已有任务进行中，提示即可
      if (error?.response?.status === 409) {
        message.warning(detail);
      } else {
        message.error(detail);
      }
    }
  };

  useEffect(() => {
    loadGraph();
    return () => stopPolling();
  }, [loadGraph, stopPolling]);

  // 渲染图谱（首次延迟，避开打开详情页的动画；切换视图立即重画）
  useEffect(() => {
    if (!chartRef.current || !graph?.nodes?.length) return;
    const placed = view === 'tree' ? buildTreeLayout(graph.nodes, graph.edges || []) : null;

    const delay = chartReady.current ? 0 : 400;
    const timer = setTimeout(() => {
      if (!chartRef.current) return;
      chartReady.current = true;

      if (!chartInstance.current) {
        chartInstance.current = echarts.init(chartRef.current);
      }

      const maxWeight = Math.max(...graph.nodes.map(n => n.weight || 1));
      const minWeight = Math.min(...graph.nodes.map(n => n.weight || 1));
      const nodeColor = (n: GraphNode) => TYPE_COLORS[n.type || '其他'] || TYPE_COLORS['其他'];
      const tooltip = {
        trigger: 'item' as const,
        formatter: (params: any) => {
          // 节点业务数据在 node / value 上；data.label 是 echarts 的标签样式对象。
          const node = (params.data?.node || params.data?.value) as GraphNode | undefined;
          if (node?.label) {
            return `<b>${node.label}</b><br/>点击查看原文里的公式和定义`;
          }
          if (params.dataType === 'edge') {
            const edge = params.data;
            return `${edge.source} —[${edge.relation}]→ ${edge.target}`;
          }
          return '';
        },
      };

      const option: echarts.EChartsOption = view === 'tree' && placed
        ? {
            tooltip,
            animationDuration: 400,
            series: [
              {
                type: 'graph' as const,
                layout: 'none' as const,
                roam: false,
                draggable: false,
                left: 0,
                right: 0,
                top: 0,
                bottom: 0,
                data: graph.nodes.map(n => {
                  const at = placed.positions.get(n.id) || { x: 36, y: 36 };
                  const size = symbolSizeForWeight(n.weight, minWeight, maxWeight);
                  return {
                    id: n.id,
                    name: n.label,
                    x: at.x,
                    y: at.y,
                    symbol: 'rect',
                    symbolSize: [size, size],
                    itemStyle: { color: nodeColor(n), borderWidth: 0 },
                    node: n,
                    value: n,
                  };
                }) as any,
                links: placed.links.map(e => ({
                  source: e.source,
                  target: e.target,
                  relation: e.relation,
                  label: { show: false, formatter: e.relation, fontSize: 11, color: '#5c6570' },
                  lineStyle: { width: edgeWidthForWeight(e.weight), curveness: 0 },
                })) as any,
                label: {
                  show: true,
                  position: 'right',
                  distance: 8,
                  fontSize: 12,
                  color: '#3d4450',
                  width: 136,
                  overflow: 'truncate',
                  ellipsis: '…',
                },
                edgeLabel: { show: false },
                lineStyle: { color: '#b7bec8', opacity: 0.9 },
                emphasis: {
                  focus: 'adjacency' as const,
                  label: { show: true },
                  edgeLabel: { show: true, fontSize: 11, color: '#4b5563' },
                  lineStyle: { opacity: 1 },
                },
                blur: {
                  itemStyle: { opacity: 0.18 },
                  label: { opacity: 0.12 },
                  lineStyle: { opacity: 0.06 },
                },
              },
            ],
          }
        : {
            tooltip,
            animationDuration: 800,
            animationEasingUpdate: 'quinticInOut' as any,
            series: [
              {
                type: 'graph' as const,
                layout: 'force' as const,
                roam: true,
                draggable: true,
                scaleLimit: { min: 0.25, max: 6 },
                left: 20,
                right: 20,
                top: 16,
                bottom: 24,
                autoCurveness: 0.16,
                data: graph.nodes.map(n => ({
                  id: n.id,
                  name: n.label,
                  symbolSize: symbolSizeForWeight(n.weight, minWeight, maxWeight),
                  itemStyle: { color: nodeColor(n) },
                  node: n,
                  value: n,
                })) as any,
                links: graph.edges.map(e => ({
                  source: e.source,
                  target: e.target,
                  relation: e.relation,
                  value: e.weight || 1,
                  label: { show: false, formatter: e.relation, fontSize: 11, color: '#5c6570' },
                  lineStyle: { width: edgeWidthForWeight(e.weight) },
                })) as any,
                label: {
                  show: true,
                  position: 'bottom',
                  distance: 4,
                  fontSize: 11,
                  color: '#3d4450',
                },
                labelLayout: {
                  hideOverlap: true,
                  moveOverlap: 'shiftY',
                },
                edgeLabel: { show: false },
                lineStyle: { color: '#c3c8d0', opacity: 0.72 },
                force: {
                  initLayout: 'circular',
                  repulsion: chartLayout(graph.nodes.length).repulsion,
                  edgeLength: chartLayout(graph.nodes.length).edgeLength,
                  gravity: chartLayout(graph.nodes.length).gravity,
                  friction: 0.6,
                  layoutAnimation: true,
                },
                emphasis: {
                  focus: 'adjacency' as const,
                  scale: 1.08,
                  label: { show: true },
                  edgeLabel: { show: true, fontSize: 11, color: '#4b5563' },
                  lineStyle: { opacity: 1 },
                },
                blur: {
                  itemStyle: { opacity: 0.16 },
                  label: { opacity: 0.08 },
                  lineStyle: { opacity: 0.05 },
                },
              },
            ],
          };

      chartInstance.current.setOption(option, true);
      chartInstance.current.resize();

      chartInstance.current.on('click', (params: any) => {
        const node = (params.data?.node || params.data?.value) as GraphNode | undefined;
        if (!node?.label) {
          setPopup(null);
          return;
        }
        const ev = params.event?.event || params.event;
        const wrap = chartWrapRef.current;
        const viewLeft = wrap?.scrollLeft || 0;
        const viewTop = wrap?.scrollTop || 0;
        const viewW = wrap?.clientWidth || 640;
        const viewH = wrap?.clientHeight || 480;
        const ox = Number(ev?.offsetX ?? 24);
        const oy = Number(ev?.offsetY ?? 24);
        const boxW = 320;
        const boxH = 260;
        let left = ox + 12;
        let top = oy + 12;
        if (left + boxW > viewLeft + viewW - 8) left = Math.max(viewLeft + 8, ox - boxW - 12);
        if (top + boxH > viewTop + viewH - 8) top = Math.max(viewTop + 8, oy - boxH - 12);
        setPopup({ node, left, top });
      });
    }, delay);

    return () => {
      clearTimeout(timer);
      chartInstance.current?.dispose();
      chartInstance.current = null;
    };
  }, [graph, view]);

  // 窗口resize适配
  useEffect(() => {
    const handleResize = () => chartInstance.current?.resize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return (
    <Card
      size="small"
      title={
        <Space>
          <ApiOutlined />
          知识图谱
          {graph?.stats && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {graph.stats.node_count}节点 / {graph.stats.edge_count}边
            </Text>
          )}
        </Space>
      }
      extra={
        <Space>
          {building ? (
            <Button size="small" disabled icon={<Spin size="small" />}>
              {progress && progress.total > 0
                ? `正在抽取知识关系：${progress.current} / ${progress.total}`
                : '知识图谱生成中...'}
            </Button>
          ) : graph?.nodes?.length ? (
            <Popconfirm
              title="生成知识图谱需要调用大模型并可能产生API费用，是否继续？"
              onConfirm={handleBuild}
              okText="生成"
              cancelText="取消"
            >
              <Button size="small" icon={<PlayCircleOutlined />}>
                重新生成
              </Button>
            </Popconfirm>
          ) : (
            <Popconfirm
              title="生成知识图谱需要调用大模型并可能产生API费用，是否继续？"
              onConfirm={handleBuild}
              okText="生成"
              cancelText="取消"
            >
              <Button size="small" type="primary" icon={<PlayCircleOutlined />}>
                生成知识图谱
              </Button>
            </Popconfirm>
          )}
          <Button size="small" icon={<ReloadOutlined />} onClick={loadGraph} loading={loading}>
            刷新
          </Button>
        </Space>
      }
    >
      {loading ? (
        <div style={{ padding: 24 }}>
          <SkeletonCard height={40} />
          <div style={{ height: 12 }} />
          <SkeletonCard height={280} />
          <div style={{ height: 12 }} />
          <SkeletonCard height={30} width="60%" />
        </div>
      ) : !graph?.nodes?.length ? (
        <Empty
          description={
            <div>
              <div>暂无知识图谱</div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                点击右上角"生成知识图谱"手动生成（需先构建知识库）
              </Text>
            </div>
          }
          style={{ padding: 40 }}
        />
      ) : (
        <>
          <div className="kg-view-bar">
            <Segmented
              size="small"
              value={view}
              onChange={(next) => {
                setPopup(null);
                setView(next as GraphView);
                if (chartWrapRef.current) {
                  chartWrapRef.current.scrollTop = 0;
                  chartWrapRef.current.scrollLeft = 0;
                }
              }}
              options={[
                { label: '球线', value: 'force' },
                { label: '树状', value: 'tree' },
              ]}
            />
          </div>
          <div className={`kg-chart-wrap ${view === 'tree' ? 'is-tree' : 'is-force'}`} ref={chartWrapRef}>
            <div
              ref={chartRef}
              className="kg-chart"
              style={view === 'tree' && treeLayout
                ? { width: treeLayout.width, height: treeLayout.height }
                : { height: chartLayout(graph.nodes.length).height }}
            />
            {popup && (
              <NodePopup node={popup.node} left={popup.left} top={popup.top} onClose={() => setPopup(null)} />
            )}
          </div>
          <div style={{ marginTop: 12 }}>
            <Space size={4} wrap>
              {Object.entries(TYPE_COLORS).filter(([k]) => graph.nodes.some(n => (n.type || '其他') === k)).map(([type, color]) => (
                <Tag key={type} color={color}>{type}</Tag>
              ))}
            </Space>
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              {view === 'tree'
                ? '方块从左到右。滚动查看，悬停看相邻关系，点击看原文'
                : '滚轮缩放，拖拽平移。悬停看关系，点击看原文'}
            </Text>
          </div>
        </>
      )}
    </Card>
  );
};

export default KnowledgeGraph;
