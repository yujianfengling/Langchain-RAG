import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Dropdown, Popconfirm, Segmented, Tooltip, message as antdMessage } from 'antd';
import {
  ClearOutlined, DownloadOutlined, PartitionOutlined, RedoOutlined, UndoOutlined,
} from '@ant-design/icons';
import { motion } from 'framer-motion';
import { jsPDF } from 'jspdf';
import { generateMindMap, loadMindBoard, saveMindBoard } from '../../api/tutor';

/**
 * 学生思维板书（2026-11-14）
 *
 * - 思维导图：中心框 + 框左右边缘「+」长出子框（贝塞尔连线、弹簧弹出动画）、
 *   点框内文字就地编辑、节点自由拖拽（拖父带子，连线随位置重算）。
 *   节点上的动画是用户点名要的"跳出来"，不受系统"减少动态效果"开关影响。
 * - 导图操作撤销/重做：快照栈（上限 50 步），Ctrl+Z / Ctrl+Shift+Z，覆盖
 *   加/删/改字/拖拽/换色/便签/图片/AI 起稿；标注另有独立的单步撤销。
 * - AI 起稿：按当前展示的课件页让模型先生成一版导图（/tutor/mindmap），学生在其上修改。
 * - 标注：画笔 / 文本（按住拉出一个框再输入）/ 箭头 / 五角星，覆盖在导图上层。
 * - 节点变体：颜色标记 / 便签样式 / 节点图片（框上「⋯」菜单）。
 * - 存档：localStorage（即时）+ 云端 /tutor/mindboard（防抖跟随），按 updatedAt 取新。
 * - 导出：PDF / PNG / Markdown 大纲 / OPML（XMind、幕布可导入）。
 */

interface MapNode {
  id: string;
  text: string;
  /** 相对父节点的伸展方向；只有根的孩子才允许 left/right 之分，深层沿用父级方向。 */
  side: 'left' | 'right';
  children: MapNode[];
  /** 节点变体（2026-11-14）：自定义底色 / 便签样式 / 节点图片。 */
  color?: string;
  variant?: 'note';
  image?: string;
}

type Mark =
  | { kind: 'stroke'; color: string; width: number; points: Array<{ x: number; y: number }> }
  | { kind: 'text'; id: string; color: string; x: number; y: number; w: number; text: string }
  | { kind: 'arrow'; color: string; width: number; x1: number; y1: number; x2: number; y2: number }
  | { kind: 'star'; color: string; x: number; y: number; r: number };

interface RectBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 撤销/重做的快照：只覆盖导图（结构/文字/位置），标注走自己的单步撤销。 */
interface Snapshot {
  map: MapNode;
  deltas: Record<string, { dx: number; dy: number }>;
}

interface LaidNode {
  id: string;
  node: MapNode;
  cx: number;
  cy: number;
  w: number;
  h: number;
  depth: number;
  side: 'left' | 'right';
}

interface LinkPair {
  from: string;
  to: string;
}

interface LaidEdge {
  id: string;
  d: string;
}

const BASE_W = 1600;
const BASE_H = 1000;
const H_GAP = 64;
const V_GAP = 16;
const MAX_TEXT_W = 208;
const TEXT_FONT = '500 15px system-ui, sans-serif';
const DEFAULT_TEXT_W = 180;
const NODE_COLORS = ['#ef4444', '#f59e0b', '#22c55e', '#3b82f6', '#8b5cf6', '#ec4899'];

let idSeed = 0;
const newNodeId = () => {
  idSeed += 1;
  return `n${Date.now().toString(36)}-${idSeed}`;
};

const makeRoot = (text: string): MapNode => ({
  id: newNodeId(), text, side: 'right', children: [],
});

const PEN_COLORS = ['#1f2937', '#dc2626', '#2563eb'];
const PEN_WIDTHS = [2.5, 5];

/** 每个节点框的样式分级：根 / 一级 / 更深。 */
const nodeSkin = (depth: number) => {
  if (depth === 0) return { fill: '#4f46e5', border: '#4338ca', color: '#ffffff', font: 15, bold: true };
  if (depth === 1) return { fill: '#eef2ff', border: '#818cf8', color: '#1e1b4b', font: 14, bold: true };
  return { fill: '#ffffff', border: '#94a3b8', color: '#0f172a', font: 14, bold: false };
};

/** 节点变体的最终配色：便签 > 自定义色 > 分级默认。 */
const nodeLook = (item: { node: MapNode; depth: number }) => {
  const skin = nodeSkin(item.depth);
  if (item.node.variant === 'note') {
    return { fill: '#fef9c3', border: '#eab308', color: '#713f12' };
  }
  if (item.node.color) {
    return { fill: item.node.color, border: item.node.color, color: textColorFor(item.node.color) };
  }
  return { fill: skin.fill, border: skin.border, color: skin.color };
};

/** 按 hex 底色亮度挑黑/白文字，保证自定义颜色下仍可读。 */
const textColorFor = (bg: string): string => {
  const m = /^#([0-9a-f]{6})$/i.exec(bg.trim());
  if (!m) return '#ffffff';
  const v = parseInt(m[1], 16);
  const r = (v >> 16) & 255;
  const g = (v >> 8) & 255;
  const b = v & 255;
  return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? '#111827' : '#ffffff';
};

let measureCtx: CanvasRenderingContext2D | null = null;
const getMeasureCtx = () => {
  if (!measureCtx) measureCtx = document.createElement('canvas').getContext('2d');
  return measureCtx;
};

/** 中英混排的换行：CJK 逐字断行，足够思维导图框用了。 */
const wrapText = (text: string, maxWidth: number, font: string): string[] => {
  const ctx = getMeasureCtx();
  if (!ctx) return [text];
  ctx.font = font;
  const lines: string[] = [];
  for (const rawLine of (text || '').split('\n')) {
    let line = '';
    for (const ch of rawLine) {
      if (line && ctx.measureText(line + ch).width > maxWidth) {
        lines.push(line);
        line = ch;
      } else {
        line += ch;
      }
    }
    lines.push(line);
  }
  return lines.length ? lines : [''];
};

const measureNode = (text: string, depth: number, hasImage = false) => {
  const skin = nodeSkin(depth);
  const font = `${skin.bold ? 600 : 400} ${skin.font}px system-ui, sans-serif`;
  const lines = wrapText(text || '', MAX_TEXT_W - 22, font);
  const widest = Math.max(...lines.map((l) => getMeasureCtx()?.measureText(l).width || 0));
  return {
    w: Math.max(depth === 0 ? 132 : 96, hasImage ? 150 : 0, Math.min(MAX_TEXT_W, widest + 22)),
    h: Math.max(depth === 0 ? 46 : 34, lines.length * 19 + 15) + (hasImage ? 82 : 0),
    lines, font,
  };
};

/**
 * 横向思维导图布局：根固定在基面中心，子树先递归算出高度、再按真实坐标放置。
 * 只输出节点与父子关系；拖拽偏移和连线在拿到最终坐标后再算。
 */
const layoutMindMap = (rootNode: MapNode) => {
  const nodes: LaidNode[] = [];
  const links: LinkPair[] = [];

  interface SubLayout {
    height: number;
    place: (cx: number, cy: number) => void;
  }

  const build = (node: MapNode, depth: number, side: 'left' | 'right'): SubLayout => {
    const m = measureNode(node.text, depth, Boolean(node.image));
    const children = node.children.map((child) => ({
      child,
      childSide: (depth === 0 ? child.side : side) as 'left' | 'right',
      m: measureNode(child.text, depth + 1, Boolean(child.image)),
      sub: build(child, depth + 1, depth === 0 ? child.side : side),
    }));
    const totalH = children.reduce((acc, item) => acc + item.sub.height, 0)
      + Math.max(0, children.length - 1) * V_GAP;
    const height = Math.max(m.h, totalH);
    return {
      height,
      place: (cx: number, cy: number) => {
        nodes.push({ id: node.id, node, cx, cy, w: m.w, h: m.h, depth, side });
        let cursor = cy - totalH / 2;
        for (const item of children) {
          const childCy = cursor + item.sub.height / 2;
          cursor += item.sub.height + V_GAP;
          const sign = item.childSide === 'right' ? 1 : -1;
          const childCx = cx + sign * (m.w / 2 + H_GAP + item.m.w / 2);
          item.sub.place(childCx, childCy);
          links.push({ from: node.id, to: item.child.id });
        }
      },
    };
  };

  build(rootNode, 0, 'right').place(BASE_W / 2, BASE_H / 2);
  return { nodes, links };
};

const findNode = (root: MapNode, id: string): MapNode | null => {
  if (root.id === id) return root;
  for (const child of root.children) {
    const hit = findNode(child, id);
    if (hit) return hit;
  }
  return null;
};

/** 删除节点，孩子上提挂到父级（误删不至于连带整棵子树）。 */
const removeNode = (root: MapNode, id: string): MapNode => {
  const walk = (node: MapNode, isRoot: boolean): MapNode => {
    const kept: MapNode[] = [];
    for (const child of node.children) {
      if (child.id === id) {
        // 上提的孩子挂回本节点：本节点是根时各自保留方向，否则统一改成父方向
        kept.push(...child.children.map((c) => ({ ...c, side: isRoot ? c.side : node.side })));
      } else {
        kept.push(walk(child, false));
      }
    }
    return { ...node, children: kept };
  };
  return walk(root, true);
};

const patchNode = (
  root: MapNode, id: string,
  patch: Partial<Omit<MapNode, 'id' | 'children' | 'side'>>,
): MapNode => {
  if (root.id === id) return { ...root, ...patch };
  return { ...root, children: root.children.map((c) => patchNode(c, id, patch)) };
};

const cloneWithText = (root: MapNode, id: string, text: string): MapNode =>
  patchNode(root, id, { text });

const addChild = (
  root: MapNode, parentId: string, side: 'left' | 'right',
): { tree: MapNode; childId: string } => {
  const parent = findNode(root, parentId);
  if (!parent) return { tree: root, childId: '' };
  const childId = newNodeId();
  const childSide: 'left' | 'right' = parentId === root.id ? side : parent.side;
  const child: MapNode = { id: childId, text: '', side: childSide, children: [] };
  const attach = (node: MapNode): MapNode => {
    if (node.id === parentId) return { ...node, children: [...node.children, child] };
    return { ...node, children: node.children.map(attach) };
  };
  return { tree: attach(root), childId };
};

const drawRoundRect = (
  ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number,
) => {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
};

const drawStroke = (ctx: CanvasRenderingContext2D, mark: Extract<Mark, { kind: 'stroke' }>) => {
  if (mark.points.length < 2) return;
  ctx.strokeStyle = mark.color;
  ctx.lineWidth = mark.width;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  ctx.moveTo(mark.points[0].x, mark.points[0].y);
  for (let i = 1; i < mark.points.length; i += 1) ctx.lineTo(mark.points[i].x, mark.points[i].y);
  ctx.stroke();
};

const drawArrow = (ctx: CanvasRenderingContext2D, mark: Extract<Mark, { kind: 'arrow' }>) => {
  const angle = Math.atan2(mark.y2 - mark.y1, mark.x2 - mark.x1);
  const head = Math.max(10, mark.width * 3.5);
  ctx.strokeStyle = mark.color;
  ctx.lineWidth = mark.width;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(mark.x1, mark.y1);
  ctx.lineTo(mark.x2, mark.y2);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(mark.x2, mark.y2);
  ctx.lineTo(mark.x2 - head * Math.cos(angle - 0.45), mark.y2 - head * Math.sin(angle - 0.45));
  ctx.moveTo(mark.x2, mark.y2);
  ctx.lineTo(mark.x2 - head * Math.cos(angle + 0.45), mark.y2 - head * Math.sin(angle + 0.45));
  ctx.stroke();
};

const drawStar = (ctx: CanvasRenderingContext2D, mark: Extract<Mark, { kind: 'star' }>) => {
  ctx.beginPath();
  for (let i = 0; i < 10; i += 1) {
    const rad = i % 2 === 0 ? mark.r : mark.r * 0.42;
    const angle = -Math.PI / 2 + i * Math.PI / 5;
    const px = mark.x + Math.cos(angle) * rad;
    const py = mark.y + Math.sin(angle) * rad;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
  ctx.fillStyle = mark.color;
  ctx.fill();
};

const drawTextMark = (ctx: CanvasRenderingContext2D, mark: Extract<Mark, { kind: 'text' }>) => {
  const w = mark.w || DEFAULT_TEXT_W;
  const lines = wrapText(mark.text, w - 18, TEXT_FONT);
  const h = lines.length * 19 + 14;
  // 框 + 文字，和板面上看到的文本框保持一致
  ctx.strokeStyle = mark.color;
  ctx.lineWidth = 1.5;
  drawRoundRect(ctx, mark.x, mark.y, w, h, 8);
  ctx.stroke();
  ctx.fillStyle = mark.color;
  ctx.font = TEXT_FONT;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  lines.forEach((line, i) => ctx.fillText(line, mark.x + 9, mark.y + 7 + i * 19));
};

const drawMark = (ctx: CanvasRenderingContext2D, mark: Mark) => {
  if (mark.kind === 'stroke') drawStroke(ctx, mark);
  else if (mark.kind === 'arrow') drawArrow(ctx, mark);
  else if (mark.kind === 'star') drawStar(ctx, mark);
  else drawTextMark(ctx, mark);
};

const downloadBlob = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
};

const MindBoard: React.FC<{
  defaultRootText: string;
  persistKey: string;
  /** 云端存档键（知识点 node_id 或名称），与 persistKey 末段一致。 */
  boardKey: string;
  subjectId: string;
  /** 右栏当前展示的课件页：AI 起稿就按这一页来。 */
  pageContext: { file_name?: string; page?: number | null; label?: string } | null;
}> = ({ defaultRootText, persistKey, boardKey, subjectId, pageContext }) => {
  const [map, setMap] = useState<MapNode>(() => makeRoot(defaultRootText));
  /** 拖拽偏移：相对自动布局的位移；拖父带子靠累积祖先偏移实现。 */
  const [deltas, setDeltas] = useState<Record<string, { dx: number; dy: number }>>({});
  const [marks, setMarks] = useState<Mark[]>([]);
  const [mode, setMode] = useState<'map' | 'mark'>('map');
  const [tool, setTool] = useState<'pen' | 'text' | 'arrow' | 'star'>('pen');
  const [penColor, setPenColor] = useState(PEN_COLORS[0]);
  const [penWidth, setPenWidth] = useState(PEN_WIDTHS[0]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [textDraft, setTextDraft] = useState<{
    id?: string; x: number; y: number; w: number; value: string;
  } | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [past, setPast] = useState<Snapshot[]>([]);
  const [future, setFuture] = useState<Snapshot[]>([]);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef<Mark | null>(null);
  /** 文本工具：按住拉框的实时预览（虚线框） */
  const rectPreviewRef = useRef<RectBox | null>(null);
  const dragRef = useRef<{
    id: string; startX: number; startY: number; orig: { dx: number; dy: number };
  } | null>(null);
  const dragHistoryRef = useRef<Snapshot | null>(null);
  const dragMovedRef = useRef(false);
  const loadedKeyRef = useRef<string | null>(null);
  const didCenterRef = useRef(false);
  const lastContentRef = useRef<string | null>(null);
  const stateRef = useRef({ map, marks, deltas });
  const fileRef = useRef<HTMLInputElement | null>(null);
  const pendingImageNodeRef = useRef<string | null>(null);

  useEffect(() => { stateRef.current = { map, marks, deltas }; });

  // ---------- 布局 + 拖拽偏移 → 最终坐标与连线 ----------
  const { nodes, edges, contentW, contentH } = useMemo(() => {
    const laid = layoutMindMap(map);
    const parentOf = new Map<string, string>();
    for (const link of laid.links) parentOf.set(link.to, link.from);
    const finalNodes = laid.nodes.map((n) => {
      let dx = 0;
      let dy = 0;
      let cursor: string | undefined = n.id;
      while (cursor) {
        const d = deltas[cursor];
        if (d) { dx += d.dx; dy += d.dy; }
        cursor = parentOf.get(cursor);
      }
      return { ...n, cx: n.cx + dx, cy: n.cy + dy };
    });
    const byId = new Map(finalNodes.map((n) => [n.id, n]));
    const edgesBuilt: LaidEdge[] = [];
    for (const link of laid.links) {
      const a = byId.get(link.from);
      const b = byId.get(link.to);
      if (!a || !b) continue;
      const sign = b.cx >= a.cx ? 1 : -1;
      const x1 = a.cx + (sign * a.w) / 2;
      const x2 = b.cx - (sign * b.w) / 2;
      const bend = sign * Math.max(18, Math.abs(x2 - x1) * 0.45);
      edgesBuilt.push({
        id: `${link.from}-${link.to}`,
        d: `M ${x1} ${a.cy} C ${x1 + bend} ${a.cy}, ${x2 - bend} ${b.cy}, ${x2} ${b.cy}`,
      });
    }
    let right = BASE_W;
    let bottom = BASE_H;
    for (const n of finalNodes) {
      right = Math.max(right, n.cx + n.w / 2 + 160);
      bottom = Math.max(bottom, n.cy + n.h / 2 + 160);
    }
    return { nodes: finalNodes, edges: edgesBuilt, contentW: right, contentH: bottom };
  }, [map, deltas]);

  // ---------- 撤销/重做（导图操作） ----------
  const pushHistory = useCallback((snapshot: Snapshot) => {
    setPast((prev) => [...prev.slice(-49), snapshot]);
    setFuture([]);
  }, []);

  const undo = () => {
    if (!past.length) return;
    const snap = past[past.length - 1];
    setPast(past.slice(0, -1));
    setFuture([...future, { map, deltas }]);
    setMap(snap.map);
    setDeltas(snap.deltas);
  };

  const redo = () => {
    if (!future.length) return;
    const snap = future[future.length - 1];
    setFuture(future.slice(0, -1));
    setPast([...past, { map, deltas }]);
    setMap(snap.map);
    setDeltas(snap.deltas);
  };

  // 每次渲染重绑，保证快捷键拿到最新的撤销栈；输入框里不抢 Ctrl+Z
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const el = event.target as HTMLElement | null;
      if (el?.closest?.('input,textarea,[contenteditable="true"]')) return;
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'z') return;
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  // ---------- 本地 + 云端存档：按 persistKey/boardKey 读一次，改动防抖双写 ----------
  useEffect(() => {
    if (loadedKeyRef.current === persistKey) return;
    loadedKeyRef.current = persistKey;
    didCenterRef.current = false;
    let local: Snapshot & { marks: Mark[]; updatedAt: number } | null = null;
    try {
      const raw = localStorage.getItem(persistKey);
      if (raw) {
        const data = JSON.parse(raw);
        if (data?.root?.id && Array.isArray(data.root.children)) {
          // v1 存的是 strokes；统一升级为 marks
          const legacy: Mark[] = Array.isArray(data.strokes)
            ? data.strokes.map((s: any) => ({ kind: 'stroke', color: s.color, width: s.width, points: s.points }))
            : [];
          local = {
            map: data.root as MapNode,
            marks: Array.isArray(data.marks) ? data.marks : legacy,
            deltas: (data.deltas && typeof data.deltas === 'object') ? data.deltas : {},
            updatedAt: Date.parse(data.updatedAt || '') || 0,
          };
        }
      }
    } catch { /* 坏数据当作没有存档 */ }
    const applied = local ?? { map: makeRoot(defaultRootText), marks: [] as Mark[], deltas: {} };
    setMap(applied.map);
    setMarks(applied.marks);
    setDeltas(applied.deltas);
    setPast([]);
    setFuture([]);
    const initialContent = JSON.stringify({
      root: applied.map, marks: applied.marks, deltas: applied.deltas,
    });
    lastContentRef.current = initialContent;
    // 云端存档：本地没有或云端更新才采用；用户已经动了就不闪改
    if (subjectId) {
      void loadMindBoard(subjectId, boardKey).then((cloud) => {
        if (!cloud?.root?.id || !Array.isArray(cloud.root.children)) return;
        const cloudTime = Date.parse(cloud.updatedAt || '') || 0;
        if (local && cloudTime < local.updatedAt) return; // 本地较新，云端只当备份
        const cur = stateRef.current;
        const curContent = JSON.stringify({ root: cur.map, marks: cur.marks, deltas: cur.deltas });
        if (curContent !== initialContent) return;
        const marks = Array.isArray(cloud.marks) ? cloud.marks : [];
        const deltas = (cloud.deltas && typeof cloud.deltas === 'object') ? cloud.deltas : {};
        setMap(cloud.root);
        setMarks(marks);
        setDeltas(deltas);
        setPast([]);
        setFuture([]);
        lastContentRef.current = JSON.stringify({ root: cloud.root, marks, deltas });
      }).catch(() => { /* 云端不可达就用本地 */ });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persistKey, boardKey]);

  useEffect(() => {
    if (loadedKeyRef.current !== persistKey) return;
    const content = JSON.stringify({ root: map, marks, deltas });
    if (content === lastContentRef.current) return;
    lastContentRef.current = content;
    const updatedAt = new Date().toISOString();
    const timer = window.setTimeout(() => {
      try {
        localStorage.setItem(persistKey, JSON.stringify({ root: map, marks, deltas, updatedAt }));
      } catch { /* 隐私模式/配额满时忽略 */ }
      if (subjectId) {
        void saveMindBoard({
          subject_id: subjectId, key: boardKey,
          data: { root: map, marks, deltas, updatedAt },
        }).catch(() => { /* 离线时本地仍是权威 */ });
      }
    }, 500);
    return () => window.clearTimeout(timer);
  }, [map, marks, deltas, persistKey, subjectId, boardKey]);

  // ---------- 首次渲染把视口滚到中心框 ----------
  // antd 弹窗有开启动画，刚挂载时容器可能还没布局尺寸，用 rAF 重试直到能滚为止
  useEffect(() => {
    if (didCenterRef.current) return;
    let frame = 0;
    let cancelled = false;
    const attempt = () => {
      if (cancelled) return;
      const el = scrollRef.current;
      if (el && el.clientWidth > 0 && el.scrollWidth > el.clientWidth) {
        el.scrollLeft = Math.max(0, BASE_W / 2 - el.clientWidth / 2);
        el.scrollTop = Math.max(0, BASE_H / 2 - el.clientHeight / 2);
        didCenterRef.current = true;
        return;
      }
      if (frame++ < 60) requestAnimationFrame(attempt);
    };
    requestAnimationFrame(attempt);
    return () => { cancelled = true; };
  }, [persistKey]);

  // ---------- 标注画布 ----------
  const paint = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(contentW * dpr) || canvas.height !== Math.round(contentH * dpr)) {
      canvas.width = Math.round(contentW * dpr);
      canvas.height = Math.round(contentH * dpr);
    }
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, contentW, contentH);
    for (const mark of marks) {
      if (mark.kind === 'text') continue; // 文本框用 DOM 渲染（可点回编辑）
      drawMark(ctx, mark);
    }
    const active = drawingRef.current;
    if (active && active.kind !== 'text') drawMark(ctx, active);
    const rect = rectPreviewRef.current;
    if (rect && rect.w > 2 && rect.h > 2) {
      // 文本框拉取中的虚线预览
      ctx.strokeStyle = penColor;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 4]);
      drawRoundRect(ctx, rect.x, rect.y, rect.w, rect.h, 8);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }, [marks, contentW, contentH, penColor]);

  useEffect(() => { paint(); }, [paint]);

  const markPointOf = (event: React.PointerEvent) => {
    const rect = (event.currentTarget as HTMLCanvasElement).getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const onMarkDown = (event: React.PointerEvent) => {
    if (mode !== 'mark') return;
    const point = markPointOf(event);
    (event.currentTarget as HTMLCanvasElement).setPointerCapture?.(event.pointerId);
    if (tool === 'text') {
      // 按住拉出一个框：起点记下，虚线预览跟着拖
      rectPreviewRef.current = { x: point.x, y: point.y, w: 0, h: 0 };
      return;
    }
    if (tool === 'pen') {
      drawingRef.current = { kind: 'stroke', color: penColor, width: penWidth, points: [point] };
    } else if (tool === 'arrow') {
      drawingRef.current = { kind: 'arrow', color: penColor, width: penWidth, x1: point.x, y1: point.y, x2: point.x, y2: point.y };
    } else {
      drawingRef.current = { kind: 'star', color: penColor, x: point.x, y: point.y, r: 24 };
    }
  };

  const onMarkMove = (event: React.PointerEvent) => {
    const point = markPointOf(event);
    if (rectPreviewRef.current) {
      const start = rectPreviewRef.current;
      rectPreviewRef.current = {
        x: Math.min(start.x, point.x),
        y: Math.min(start.y, point.y),
        w: Math.abs(point.x - start.x),
        h: Math.abs(point.y - start.y),
      };
      paint();
      return;
    }
    const active = drawingRef.current;
    if (!active) return;
    if (active.kind === 'stroke') {
      active.points.push(point);
      const ctx = canvasRef.current?.getContext('2d');
      const pts = active.points;
      if (!ctx || pts.length < 2) return;
      // 增量画最后一段，落笔全程不整帧重绘
      const dpr = window.devicePixelRatio || 1;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.strokeStyle = active.color;
      ctx.lineWidth = active.width;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(pts[pts.length - 2].x, pts[pts.length - 2].y);
      ctx.lineTo(pts[pts.length - 1].x, pts[pts.length - 1].y);
      ctx.stroke();
      return;
    }
    if (active.kind === 'arrow') {
      active.x2 = point.x;
      active.y2 = point.y;
    } else if (active.kind === 'star') {
      const dist = Math.hypot(point.x - active.x, point.y - active.y);
      active.r = Math.min(200, Math.max(12, dist));
    }
    paint();
  };

  const onMarkUp = () => {
    if (rectPreviewRef.current) {
      const rect = rectPreviewRef.current;
      rectPreviewRef.current = null;
      // 轻点一下也给一个默认大小的框，直接开输
      const box: RectBox = rect.w > 24 || rect.h > 18 ? rect
        : { x: rect.x, y: rect.y, w: DEFAULT_TEXT_W, h: 40 };
      setTextDraft({ x: box.x, y: box.y, w: Math.max(120, box.w), value: '' });
      paint();
      return;
    }
    const active = drawingRef.current;
    drawingRef.current = null;
    if (!active) return;
    if (active.kind === 'stroke') {
      if (active.points.length > 1) setMarks((prev) => [...prev, active]);
      else paint();
    } else if (active.kind === 'arrow') {
      if (Math.hypot(active.x2 - active.x1, active.y2 - active.y1) > 10) {
        setMarks((prev) => [...prev, active]);
      } else paint();
    } else if (active.kind === 'star') {
      setMarks((prev) => [...prev, active]);
    }
  };

  const commitTextDraft = () => {
    if (!textDraft) return;
    const value = textDraft.value.trim();
    if (value) {
      setMarks((prev) => (textDraft.id
        ? prev.map((m) => (m.kind === 'text' && m.id === textDraft.id ? { ...m, w: textDraft.w, text: value } : m))
        : [...prev, {
          kind: 'text', id: newNodeId(), color: penColor,
          x: textDraft.x, y: textDraft.y, w: textDraft.w, text: value,
        }]));
    }
    setTextDraft(null);
  };

  // ---------- 节点自由拖拽：拖父带子，连线随后重算 ----------
  // pointerdown 立即捕获指针但不 preventDefault——拖拽过程不丢事件，
  // click 和输入框焦点也不受影响（此前吞 click 的是 preventDefault）。
  const onNodeDown = (event: React.PointerEvent, item: LaidNode) => {
    if (mode !== 'map') return;
    if ((event.target as HTMLElement).closest('button,input')) return;
    dragMovedRef.current = false;
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
    dragRef.current = {
      id: item.id, startX: event.clientX, startY: event.clientY,
      orig: deltas[item.id] || { dx: 0, dy: 0 },
    };
    // 拖拽前的快照：松手时真的拖动了才进撤销栈
    dragHistoryRef.current = { map, deltas };
  };

  const onNodeMove = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!dragMovedRef.current && Math.abs(dx) + Math.abs(dy) <= 4) return;
    dragMovedRef.current = true;
    setDeltas((prev) => ({ ...prev, [drag.id]: { dx: drag.orig.dx + dx, dy: drag.orig.dy + dy } }));
  };

  const onNodeUp = () => {
    if (dragMovedRef.current && dragHistoryRef.current) {
      pushHistory(dragHistoryRef.current);
    }
    dragRef.current = null;
    dragHistoryRef.current = null;
    if (dragMovedRef.current) {
      // click 在 pointerup 后同步派发：用宏任务放行"拖过了"标记，
      // 拦住拖拽后的误点击进入编辑，又不影响下一次正常点击
      window.setTimeout(() => { dragMovedRef.current = false; }, 0);
    }
  };

  // ---------- 节点交互（都是点击驱动，直接基于当前 map 计算） ----------
  const startEdit = (item: LaidNode) => {
    if (mode !== 'map' || dragMovedRef.current) return;
    dragMovedRef.current = false;
    setEditingId(item.id);
    setDraft(item.node.text);
  };

  const commitEdit = () => {
    if (!editingId) return;
    const next = draft.trim();
    const original = findNode(map, editingId)?.text || '';
    // 空提交一律视为取消（保留原文字）：清空节点用「×」删除，误触 blur 不会吃掉文字
    if (next && next !== original) {
      pushHistory({ map, deltas });
      setMap(cloneWithText(map, editingId, next));
    }
    setEditingId(null);
  };

  const grow = (item: LaidNode, side: 'left' | 'right') => {
    const { tree, childId } = addChild(map, item.id, side);
    if (!childId) return;
    pushHistory({ map, deltas });
    setMap(tree);
    setEditingId(childId);
    setDraft('');
  };

  const removeById = (id: string) => {
    pushHistory({ map, deltas });
    setMap(removeNode(map, id));
    setDeltas((prev) => {
      if (!prev[id]) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
    if (editingId === id) setEditingId(null);
  };

  // ---------- AI 起稿：按当前展示的课件页生成一版导图 ----------
  const aiTreeToMap = (raw: any, depth = 0): MapNode => {
    const node: MapNode = {
      id: newNodeId(),
      text: String(raw?.text || raw?.name || '').slice(0, 40),
      side: 'right',
      children: [],
    };
    const kids = Array.isArray(raw?.children) ? raw.children.slice(0, 6) : [];
    node.children = kids.map((c: any, i: number) => {
      const child = aiTreeToMap(c, depth + 1);
      if (depth === 0) child.side = i % 2 === 0 ? 'right' : 'left';
      return child;
    });
    return node;
  };

  const aiGenerate = async () => {
    if (!subjectId || !pageContext?.file_name) return;
    setAiLoading(true);
    try {
      const data = await generateMindMap({
        subject_id: subjectId,
        file_name: pageContext.file_name,
        page: pageContext.page ?? null,
        topic: pageContext.label || null,
      });
      pushHistory({ map, deltas });
      setMap(aiTreeToMap(data?.root));
      setDeltas({});
      setEditingId(null);
      antdMessage.success('已按这页课件生成导图，可以直接改');
    } catch (e: any) {
      antdMessage.error(e?.response?.data?.detail || '思维导图生成失败，请稍后重试');
    } finally {
      setAiLoading(false);
    }
  };

  // ---------- 节点变体：颜色 / 便签 / 图片 ----------
  const handleVariant = (key: string, item: LaidNode) => {
    if (key.startsWith('color:')) {
      pushHistory({ map, deltas });
      setMap(patchNode(map, item.id, { color: key.slice(6) }));
    } else if (key === 'color-default') {
      pushHistory({ map, deltas });
      setMap(patchNode(map, item.id, { color: undefined }));
    } else if (key === 'note') {
      pushHistory({ map, deltas });
      setMap(patchNode(map, item.id, {
        variant: item.node.variant === 'note' ? undefined : 'note',
      }));
    } else if (key === 'image') {
      pendingImageNodeRef.current = item.id;
      fileRef.current?.click();
    } else if (key === 'image-clear') {
      pushHistory({ map, deltas });
      setMap(patchNode(map, item.id, { image: undefined }));
    }
  };

  const variantItems = (item: LaidNode) => [
    { key: 'color-default', label: '恢复默认颜色' },
    ...NODE_COLORS.map((c) => ({
      key: `color:${c}`,
      label: (<span className="mb-swatch" style={{ background: c }} />),
    })),
    { type: 'divider' as const },
    { key: 'note', label: item.node.variant === 'note' ? '✓ 便签样式' : '便签样式' },
    { key: 'image', label: item.node.image ? '更换图片' : '插入图片' },
    { key: 'image-clear', label: '移除图片', disabled: !item.node.image },
  ];

  const onPickImage = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    const nodeId = pendingImageNodeRef.current;
    event.target.value = '';
    if (!file || !nodeId) return;
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      // 压到 320px 内再存，免得 localStorage/云端存档被大图撑爆
      const scale = Math.min(1, 320 / Math.max(img.width, img.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(img.width * scale));
      canvas.height = Math.max(1, Math.round(img.height * scale));
      canvas.getContext('2d')?.drawImage(img, 0, 0, canvas.width, canvas.height);
      pushHistory({ map, deltas });
      setMap(patchNode(map, nodeId, { image: canvas.toDataURL('image/jpeg', 0.78) }));
    };
    img.onerror = () => URL.revokeObjectURL(url);
    img.src = url;
  };

  // ---------- 导出：PDF / PNG / Markdown / OPML ----------
  const collectImages = async (): Promise<Map<string, HTMLImageElement>> => {
    const entries = await Promise.all(nodes
      .filter((n) => n.node.image)
      .map(async (n) => {
        const img = new Image();
        img.src = n.node.image as string;
        try {
          await img.decode();
          return [n.id, img] as const;
        } catch {
          return [n.id, null] as const; // 图片坏了就跳过
        }
      }));
    const found = new Map<string, HTMLImageElement>();
    for (const [id, img] of entries) if (img) found.set(id, img);
    return found;
  };

  const buildExportCanvas = async () => {
    const scale = Math.min(1, 2200 / Math.max(contentW, contentH));
    const w = Math.round(contentW * scale);
    const h = Math.round(contentH * scale);
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, w, h);
    ctx.scale(scale, scale);
    ctx.strokeStyle = '#94a3b8';
    ctx.lineWidth = 2;
    for (const edge of edges) {
      ctx.stroke(new Path2D(edge.d));
    }
    const images = await collectImages();
    for (const item of nodes) {
      const look = nodeLook(item);
      const m = measureNode(item.node.text, item.depth, Boolean(item.node.image));
      const x = item.cx - item.w / 2;
      const y = item.cy - item.h / 2;
      ctx.fillStyle = look.fill;
      ctx.strokeStyle = look.border;
      ctx.lineWidth = item.depth === 0 ? 2 : 1.5;
      drawRoundRect(ctx, x, y, item.w, item.h, 10);
      ctx.fill();
      ctx.stroke();
      const img = images.get(item.id);
      if (img) {
        const ratio = Math.min((item.w - 20) / img.width, 74 / img.height);
        const dw = img.width * ratio;
        const dh = img.height * ratio;
        ctx.save();
        drawRoundRect(ctx, item.cx - dw / 2, y + 8, dw, dh, 6);
        ctx.clip();
        ctx.drawImage(img, item.cx - dw / 2, y + 8, dw, dh);
        ctx.restore();
      }
      ctx.fillStyle = look.color;
      ctx.font = m.font;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const lineH = 19;
      const offset = item.node.image ? 41 : 0;
      const startY = item.cy + offset - ((m.lines.length - 1) * lineH) / 2;
      m.lines.forEach((line, i) => ctx.fillText(line, item.cx, startY + i * lineH));
    }
    for (const mark of marks) drawMark(ctx, mark);
    return { canvas, w, h };
  };

  const exportStamp = () => new Date().toISOString().slice(0, 10);

  const exportPdf = async () => {
    const out = await buildExportCanvas();
    if (!out) return;
    setExporting(true);
    try {
      const pdf = new jsPDF({
        orientation: out.w >= out.h ? 'landscape' : 'portrait',
        unit: 'px',
        format: [out.w, out.h],
      });
      pdf.addImage(out.canvas.toDataURL('image/jpeg', 0.92), 'JPEG', 0, 0, out.w, out.h);
      pdf.save(`白板-${exportStamp()}.pdf`);
    } catch {
      // 导出失败不打断书写
    } finally {
      setExporting(false);
    }
  };

  const exportPng = async () => {
    const out = await buildExportCanvas();
    if (!out) return;
    setExporting(true);
    try {
      const blob = await new Promise<Blob | null>(
        (resolve) => out.canvas.toBlob(resolve, 'image/png'));
      if (blob) downloadBlob(blob, `白板-${exportStamp()}.png`);
    } catch {
      // 导出失败不打断书写
    } finally {
      setExporting(false);
    }
  };

  const exportMarkdown = () => {
    const walk = (node: MapNode, depth: number): string => {
      const indent = '  '.repeat(depth);
      const line = depth === 0
        ? `# ${node.text || '白板导图'}\n`
        : `${indent}- ${node.text || '（空）'}\n`;
      return line + node.children.map((c) => walk(c, depth + 1)).join('');
    };
    downloadBlob(new Blob([walk(map, 0)], { type: 'text/markdown;charset=utf-8' }),
      `导图-${exportStamp()}.md`);
  };

  const exportOpml = () => {
    const esc = (s: string) => s
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const walk = (node: MapNode, indent: string): string =>
      `${indent}<outline text="${esc(node.text || '节点')}">`
      + (node.children.length
        ? `\n${node.children.map((c) => walk(c, `${indent}  `)).join('')}${indent}`
        : '')
      + `</outline>\n`;
    const doc = `<?xml version="1.0" encoding="UTF-8"?>\n<opml version="2.0">\n`
      + `  <head><title>${esc(map.text || '白板导图')}</title></head>\n`
      + `  <body>\n${walk(map, '    ')}  </body>\n</opml>\n`;
    downloadBlob(new Blob([doc], { type: 'text/x-opml;charset=utf-8' }),
      `导图-${exportStamp()}.opml`);
  };

  const markActive = mode === 'mark';
  const hasMapContent = map.children.length > 0 || Object.keys(deltas).length > 0;
  const canAiGenerate = Boolean(subjectId && pageContext?.file_name);

  const aiButton = (
    // 注意：onClick 由外层 Popconfirm 的 onConfirm 统一触发；
    // antd 会保留子按钮自身的 onClick，这里再绑会绕过确认框直接生成
    <Button size="small" icon={<PartitionOutlined />} loading={aiLoading}
            disabled={!canAiGenerate}>
      AI 生成导图
    </Button>
  );

  return (
    <div className="mindboard">
      <div className="mindboard-toolbar">
        <Segmented
          size="small"
          value={mode}
          onChange={(v) => setMode(v as 'map' | 'mark')}
          options={[
            { value: 'map', label: '思维导图' },
            { value: 'mark', label: '标注' },
          ]}
        />
        {!markActive && (
          <>
            <Tooltip title="撤销导图操作（Ctrl+Z）">
              <Button size="small" icon={<UndoOutlined />} disabled={!past.length} onClick={undo} />
            </Tooltip>
            <Tooltip title="重做（Ctrl+Shift+Z）">
              <Button size="small" icon={<RedoOutlined />} disabled={!future.length} onClick={redo} />
            </Tooltip>
            <span className="mindboard-hint">
              拖框移动（拖父带子）；点框边「+」长出子框；点框内文字直接改写；「⋯」换样式
            </span>
          </>
        )}
        {markActive && (
          <div className="mindboard-pen-tools">
            <Segmented size="small" value={tool}
                       onChange={(v) => setTool(v as 'pen' | 'text' | 'arrow' | 'star')}
                       options={[
                         { value: 'pen', label: '画笔' },
                         { value: 'text', label: '文本' },
                         { value: 'arrow', label: '箭头' },
                         { value: 'star', label: '五角星' },
                       ]} />
            {PEN_COLORS.map((color) => (
              <button key={color} type="button" aria-label={`颜色 ${color}`}
                      className={'mindboard-color' + (penColor === color ? ' is-active' : '')}
                      style={{ background: color }}
                      onClick={() => setPenColor(color)} />
            ))}
            {(tool === 'pen' || tool === 'arrow') && (
              <Segmented size="small" value={penWidth}
                         onChange={(v) => setPenWidth(v as number)}
                         options={[
                           { value: PEN_WIDTHS[0], label: '细' },
                           { value: PEN_WIDTHS[1], label: '粗' },
                         ]} />
            )}
            <Button size="small" icon={<UndoOutlined />} disabled={!marks.length}
                    onClick={() => setMarks((prev) => prev.slice(0, -1))}>撤销</Button>
            <Popconfirm title="清掉所有标注？" okText="清空" cancelText="算了"
                        onConfirm={() => setMarks([])}
                        disabled={!marks.length}>
              <Button size="small" icon={<ClearOutlined />} disabled={!marks.length}>清空标注</Button>
            </Popconfirm>
            <span className="mindboard-hint">
              {tool === 'text' ? '在白板上按住拉出一个框，松手即可输入' : '按住拖动即可书写/指向'}
            </span>
          </div>
        )}
        <span className="mindboard-spacer" />
        <Tooltip title={canAiGenerate
          ? `按「${pageContext?.file_name}${pageContext?.page != null ? ` 第 ${pageContext.page + 1} 页` : ''}」让 AI 先生成一版导图，再自己改`
          : '右栏还没有展示课件页，先点开一轮回答的引用'}>
          {hasMapContent ? (
            <Popconfirm title="AI 会替换当前导图（可撤销；标注保留）？" okText="生成" cancelText="算了"
                        onConfirm={() => void aiGenerate()}>
              {aiButton}
            </Popconfirm>
          ) : aiButton}
        </Tooltip>
        <Dropdown trigger={['click']}
                  menu={{
                    items: [
                      { key: 'pdf', label: 'PDF 文档' },
                      { key: 'png', label: 'PNG 图片' },
                      { key: 'md', label: 'Markdown 大纲' },
                      { key: 'opml', label: 'OPML（XMind/幕布）' },
                    ],
                    onClick: ({ key }) => {
                      if (key === 'pdf') void exportPdf();
                      if (key === 'png') void exportPng();
                      if (key === 'md') exportMarkdown();
                      if (key === 'opml') exportOpml();
                    },
                  }}>
          <Button size="small" icon={<DownloadOutlined />} loading={exporting}>
            导出
          </Button>
        </Dropdown>
      </div>

      <div className="mindboard-scroll" ref={scrollRef}>
        <div className="mindboard-content" style={{ width: contentW, height: contentH }}>
          <svg className="mindboard-edges" width={contentW} height={contentH}>
            {edges.map((edge) => (
              <motion.path key={edge.id} d={edge.d} fill="none" stroke="#94a3b8" strokeWidth={2}
                           initial={{ pathLength: 0, opacity: 0 }}
                           animate={{ pathLength: 1, opacity: 1 }}
                           transition={{ duration: 0.2, ease: 'easeOut' }} />
            ))}
          </svg>

          {nodes.map((item) => {
            const look = nodeLook(item);
            const isEditing = editingId === item.id;
            const isRoot = item.depth === 0;
            const canGrowSides: Array<'left' | 'right'> = isRoot
              ? ['left', 'right'] : [item.side];
            return (
              <motion.div key={item.id}
                          className={'mindboard-node' + (isRoot ? ' is-root' : '') + (mode === 'map' ? ' is-draggable' : '')}
                          style={{
                            left: item.cx - item.w / 2,
                            top: item.cy - item.h / 2,
                            width: item.w,
                            minHeight: item.h,
                            flexDirection: item.node.image ? 'column' : 'row',
                            background: look.fill,
                            borderColor: look.border,
                            color: look.color,
                          }}
                          initial={{ opacity: 0, scale: 0 }}
                          animate={{ opacity: 1, scale: 1 }}
                          transition={{ type: 'spring', stiffness: 460, damping: 13, mass: 0.9 }}
                          onPointerDown={(e) => onNodeDown(e, item)}
                          onPointerMove={onNodeMove}
                          onPointerUp={onNodeUp}>
                <Dropdown trigger={['click']}
                          menu={{
                            items: variantItems(item),
                            onClick: ({ key }) => handleVariant(key, item),
                          }}>
                  <button type="button" aria-label="节点样式" className="mindboard-more"
                          onClick={(e) => e.stopPropagation()}>
                    ⋯
                  </button>
                </Dropdown>
                {canGrowSides.map((side) => (
                  <button key={side} type="button"
                          aria-label={side === 'right' ? '向右生长子节点' : '向左生长子节点'}
                          className={`mindboard-grow mindboard-grow-${side}`}
                          onClick={(e) => { e.stopPropagation(); grow(item, side); }}>
                    +
                  </button>
                ))}
                {!isRoot && (
                  <button type="button" aria-label="删除该节点（子级上提）"
                          className="mindboard-del"
                          onClick={(e) => { e.stopPropagation(); removeById(item.id); }}>
                    ×
                  </button>
                )}
                {item.node.image && (
                  <img className="mindboard-node-img" src={item.node.image} alt=""
                       onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
                )}
                {isEditing ? (
                  <input className="mindboard-input" value={draft} autoFocus
                         onChange={(e) => setDraft(e.target.value)}
                         onBlur={commitEdit}
                         onKeyDown={(e) => {
                           if (e.key === 'Enter') { e.preventDefault(); commitEdit(); }
                           if (e.key === 'Escape') setEditingId(null);
                         }} />
                ) : (
                  <div className={'mindboard-text' + (item.node.text ? '' : ' is-empty')}
                       onClick={() => startEdit(item)}>
                    {item.node.text || '写点什么…'}
                  </div>
                )}
              </motion.div>
            );
          })}

          {/* 文本标注框：标注模式选「文本」时可点回编辑 */}
          {marks.filter((m) => m.kind === 'text').map((m) => (
            m.kind === 'text' && (
              <div key={m.id} className="mindboard-anno"
                   style={{
                     left: m.x, top: m.y, width: m.w || DEFAULT_TEXT_W,
                     color: m.color, borderColor: m.color,
                     pointerEvents: markActive && tool === 'text' ? 'auto' : 'none',
                   }}
                   onClick={() => setTextDraft({
                     id: m.id, x: m.x, y: m.y, w: m.w || DEFAULT_TEXT_W, value: m.text,
                   })}>
                {m.text}
              </div>
            )
          ))}

          <canvas ref={canvasRef} className={'mindboard-pen' + (markActive ? ' is-active' : '')}
                  style={{ width: contentW, height: contentH }}
                  onPointerDown={onMarkDown}
                  onPointerMove={onMarkMove}
                  onPointerUp={onMarkUp} />

          {textDraft && (
            <div className="mindboard-text-draft"
                 style={{ left: textDraft.x, top: textDraft.y, width: textDraft.w }}>
              <input autoFocus value={textDraft.value}
                     onChange={(e) => setTextDraft({ ...textDraft, value: e.target.value })}
                     onBlur={commitTextDraft}
                     onKeyDown={(e) => {
                       if (e.key === 'Enter') { e.preventDefault(); commitTextDraft(); }
                       if (e.key === 'Escape') setTextDraft(null);
                     }} />
            </div>
          )}

          <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }}
                 onChange={onPickImage} />
        </div>
      </div>
    </div>
  );
};

export default MindBoard;
