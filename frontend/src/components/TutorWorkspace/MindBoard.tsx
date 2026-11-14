import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Popconfirm, Segmented, Tooltip, message as antdMessage } from 'antd';
import {
  ClearOutlined, FilePdfOutlined, PartitionOutlined, UndoOutlined,
} from '@ant-design/icons';
import { motion } from 'framer-motion';
import { jsPDF } from 'jspdf';
import { generateMindMap } from '../../api/tutor';

/**
 * 学生思维板书（2026-11-14）
 *
 * - 思维导图：中心框 + 框左右边缘「+」长出子框（贝塞尔连线、弹簧弹出动画）、
 *   点框内文字就地编辑、节点自由拖拽（拖父带子，连线随位置重算）。
 *   注意：节点上的动画是用户点名要的"跳出来"，不受系统"减少动态效果"开关影响。
 * - AI 起稿：按当前展示的课件页让模型先生成一版导图（/tutor/mindmap），学生在其上修改。
 * - 标注：画笔 / 文本（按住拉出一个框再输入）/ 箭头 / 五角星，覆盖在导图上层。
 * - 导出 PDF：导图（拖拽后位置）+ 标注重绘到离屏 canvas，交给 jsPDF。
 *
 * 数据只存本地 localStorage（按 用户+学科+知识点 分隔），不上传后端。
 */

interface MapNode {
  id: string;
  text: string;
  /** 相对父节点的伸展方向；只有根的孩子才允许 left/right 之分，深层沿用父级方向。 */
  side: 'left' | 'right';
  children: MapNode[];
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

const measureNode = (text: string, depth: number) => {
  const skin = nodeSkin(depth);
  const font = `${skin.bold ? 600 : 400} ${skin.font}px system-ui, sans-serif`;
  const lines = wrapText(text || '', MAX_TEXT_W - 22, font);
  const widest = Math.max(...lines.map((l) => getMeasureCtx()?.measureText(l).width || 0));
  return {
    w: Math.max(depth === 0 ? 132 : 96, Math.min(MAX_TEXT_W, widest + 22)),
    h: Math.max(depth === 0 ? 46 : 34, lines.length * 19 + 15),
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
    const m = measureNode(node.text, depth);
    const children = node.children.map((child) => ({
      child,
      childSide: (depth === 0 ? child.side : side) as 'left' | 'right',
      m: measureNode(child.text, depth + 1),
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

const cloneWithText = (root: MapNode, id: string, text: string): MapNode => {
  if (root.id === id) return { ...root, text };
  return { ...root, children: root.children.map((c) => cloneWithText(c, id, text)) };
};

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

const MindBoard: React.FC<{
  defaultRootText: string;
  persistKey: string;
  subjectId: string;
  /** 右栏当前展示的课件页：AI 起稿就按这一页来。 */
  pageContext: { file_name?: string; page?: number | null; label?: string } | null;
}> = ({ defaultRootText, persistKey, subjectId, pageContext }) => {
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
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef<Mark | null>(null);
  /** 文本工具：按住拉框的实时预览（虚线框） */
  const rectPreviewRef = useRef<RectBox | null>(null);
  const dragRef = useRef<{
    id: string; startX: number; startY: number; orig: { dx: number; dy: number };
  } | null>(null);
  const dragMovedRef = useRef(false);
  const loadedKeyRef = useRef<string | null>(null);
  const didCenterRef = useRef(false);

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

  // ---------- 本地持久化：按 persistKey 读一次，随改动防抖写回 ----------
  useEffect(() => {
    if (loadedKeyRef.current === persistKey) return;
    loadedKeyRef.current = persistKey;
    didCenterRef.current = false;
    let restored = false;
    try {
      const raw = localStorage.getItem(persistKey);
      if (raw) {
        const data = JSON.parse(raw);
        if (data?.root?.id && Array.isArray(data.root.children)) {
          setMap(data.root as MapNode);
          // v1 存的是 strokes；统一升级为 marks
          const legacy: Mark[] = Array.isArray(data.strokes)
            ? data.strokes.map((s: any) => ({ kind: 'stroke', color: s.color, width: s.width, points: s.points }))
            : [];
          setMarks(Array.isArray(data.marks) ? data.marks : legacy);
          restored = true;
        }
      }
    } catch { /* 坏数据当作没有存档 */ }
    if (!restored) {
      setMap(makeRoot(defaultRootText));
      setMarks([]);
    }
    setDeltas({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persistKey]);

  useEffect(() => {
    if (loadedKeyRef.current !== persistKey) return;
    const timer = window.setTimeout(() => {
      try {
        localStorage.setItem(persistKey, JSON.stringify({
          root: map, marks, updatedAt: Date.now(),
        }));
      } catch { /* 隐私模式/配额满时忽略 */ }
    }, 400);
    return () => window.clearTimeout(timer);
  }, [map, marks, persistKey]);

  // ---------- 首次渲染把视口滚到中心框 ----------
  useEffect(() => {
    if (didCenterRef.current || !scrollRef.current) return;
    didCenterRef.current = true;
    scrollRef.current.scrollLeft = Math.max(0, BASE_W / 2 - scrollRef.current.clientWidth / 2);
    scrollRef.current.scrollTop = Math.max(0, BASE_H / 2 - scrollRef.current.clientHeight / 2);
  }, [nodes.length]);

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
    if (tool === 'text') {
      // 按住拉出一个框：起点记下，虚线预览跟着拖
      (event.currentTarget as HTMLCanvasElement).setPointerCapture?.(event.pointerId);
      rectPreviewRef.current = { x: point.x, y: point.y, w: 0, h: 0 };
      return;
    }
    (event.currentTarget as HTMLCanvasElement).setPointerCapture?.(event.pointerId);
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

  const onMarkUp = (event: React.PointerEvent) => {
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
    void event;
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
  // pointerdown 不 preventDefault、不立刻捕获——否则 click 和输入框焦点会被吞掉；
  // 拖动超过阈值才接管指针。
  const onNodeDown = (event: React.PointerEvent, item: LaidNode) => {
    if (mode !== 'map') return;
    if ((event.target as HTMLElement).closest('button,input')) return;
    dragMovedRef.current = false;
    dragRef.current = {
      id: item.id, startX: event.clientX, startY: event.clientY,
      orig: deltas[item.id] || { dx: 0, dy: 0 },
    };
  };

  const onNodeMove = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!dragMovedRef.current) {
      if (Math.abs(dx) + Math.abs(dy) <= 4) return;
      dragMovedRef.current = true;
      (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
    }
    setDeltas((prev) => ({ ...prev, [drag.id]: { dx: drag.orig.dx + dx, dy: drag.orig.dy + dy } }));
  };

  const onNodeUp = () => { dragRef.current = null; };

  // ---------- 节点交互（都是点击驱动，直接基于当前 map 计算） ----------
  const startEdit = (item: LaidNode) => {
    if (mode !== 'map' || dragMovedRef.current) return;
    dragMovedRef.current = false;
    setEditingId(item.id);
    setDraft(item.node.text);
  };

  const commitEdit = () => {
    if (!editingId) return;
    setMap(cloneWithText(map, editingId, draft.trim()));
    setEditingId(null);
  };

  const grow = (item: LaidNode, side: 'left' | 'right') => {
    const { tree, childId } = addChild(map, item.id, side);
    if (!childId) return;
    setMap(tree);
    setEditingId(childId);
    setDraft('');
  };

  const removeById = (id: string) => {
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

  // ---------- 导出 PDF：导图（拖拽后位置）+ 标注重绘到离屏 canvas ----------
  const exportPdf = () => {
    const scale = Math.min(1, 2200 / Math.max(contentW, contentH));
    const w = Math.round(contentW * scale);
    const h = Math.round(contentH * scale);
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, w, h);
    ctx.scale(scale, scale);
    ctx.strokeStyle = '#94a3b8';
    ctx.lineWidth = 2;
    for (const edge of edges) {
      ctx.stroke(new Path2D(edge.d));
    }
    for (const item of nodes) {
      const skin = nodeSkin(item.depth);
      const m = measureNode(item.node.text, item.depth);
      const x = item.cx - item.w / 2;
      const y = item.cy - item.h / 2;
      ctx.fillStyle = skin.fill;
      ctx.strokeStyle = skin.border;
      ctx.lineWidth = item.depth === 0 ? 2 : 1.5;
      drawRoundRect(ctx, x, y, item.w, item.h, 10);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = skin.color;
      ctx.font = m.font;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const lineH = 19;
      const startY = item.cy - ((m.lines.length - 1) * lineH) / 2;
      m.lines.forEach((line, i) => ctx.fillText(line, item.cx, startY + i * lineH));
    }
    for (const mark of marks) drawMark(ctx, mark);
    setExporting(true);
    try {
      const pdf = new jsPDF({
        orientation: w >= h ? 'landscape' : 'portrait',
        unit: 'px',
        format: [w, h],
      });
      pdf.addImage(canvas.toDataURL('image/jpeg', 0.92), 'JPEG', 0, 0, w, h);
      const stamp = new Date().toISOString().slice(0, 10);
      pdf.save(`白板-${stamp}.pdf`);
    } catch {
      // 导出失败不打断书写
    } finally {
      setExporting(false);
    }
  };

  const markActive = mode === 'mark';
  const hasMapContent = map.children.length > 0 || Object.keys(deltas).length > 0;
  const canAiGenerate = Boolean(subjectId && pageContext?.file_name);

  const aiButton = (
    <Button size="small" icon={<PartitionOutlined />} loading={aiLoading}
            disabled={!canAiGenerate}
            onClick={() => void aiGenerate()}>
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
        {markActive ? (
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
        ) : (
          <span className="mindboard-hint">
            拖框移动（拖父带子）；点框边「+」长出子框；点框内文字直接改写
          </span>
        )}
        <span className="mindboard-spacer" />
        <Tooltip title={canAiGenerate
          ? `按「${pageContext?.file_name}${pageContext?.page != null ? ` 第 ${pageContext.page + 1} 页` : ''}」让 AI 先生成一版导图，再自己改`
          : '右栏还没有展示课件页，先点开一轮回答的引用'}>
          {hasMapContent ? (
            <Popconfirm title="AI 会替换当前导图（标注保留）？" okText="生成" cancelText="算了"
                        onConfirm={() => void aiGenerate()}>
              {aiButton}
            </Popconfirm>
          ) : aiButton}
        </Tooltip>
        <Tooltip title="把思维导图和标注一起导出成 PDF">
          <Button size="small" icon={<FilePdfOutlined />} loading={exporting} onClick={exportPdf}>
            导出 PDF
          </Button>
        </Tooltip>
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
            const skin = nodeSkin(item.depth);
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
                            background: skin.fill,
                            borderColor: skin.border,
                            color: skin.color,
                          }}
                          initial={{ opacity: 0, scale: 0 }}
                          animate={{ opacity: 1, scale: 1 }}
                          transition={{ type: 'spring', stiffness: 460, damping: 13, mass: 0.9 }}
                          onPointerDown={(e) => onNodeDown(e, item)}
                          onPointerMove={onNodeMove}
                          onPointerUp={onNodeUp}>
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
        </div>
      </div>
    </div>
  );
};

export default MindBoard;
