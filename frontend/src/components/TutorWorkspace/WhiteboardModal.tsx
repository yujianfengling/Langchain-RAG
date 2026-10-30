import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, Input, Modal, Select, Space, Spin, Tag, Tooltip, Typography } from 'antd';
import { BulbOutlined, ReloadOutlined } from '@ant-design/icons';
import apiClient from '../../api/axios';
import {
  generateWhiteboard, type TutorSource, type WhiteboardScene,
} from '../../api/tutor';

const { Text } = Typography;

const WHITEBOARD_CACHE = 'edu.whiteboard.v1';

const cacheKeyOf = (subjectId: string, template: string, topic: string) =>
  `${WHITEBOARD_CACHE}.${subjectId}.${template}.${(topic.trim() || '__default__')}`;

const readSceneCache = (subjectId: string, template: string, topic: string): WhiteboardScene | null => {
  try {
    const raw = localStorage.getItem(cacheKeyOf(subjectId, template, topic));
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (!data || !Array.isArray(data.elements)) return null;
    return data as WhiteboardScene;
  } catch {
    return null;
  }
};

const writeSceneCache = (subjectId: string, template: string, topic: string, scene: WhiteboardScene) => {
  try {
    localStorage.setItem(cacheKeyOf(subjectId, template, topic), JSON.stringify(scene));
  } catch {
    // 隐私模式/配额满时忽略，白板本身仍可用
  }
};

/**
 * AI 白板：模型只返回结构化场景 JSON，这里用 SVG 渲染。
 * 不执行模型生成的任何 HTML/JS；坐标已在后端做过边界校验。
 */
const WhiteboardModal: React.FC<{
  open: boolean;
  onClose: () => void;
  subjectId: string;
  defaultTopic?: string;
  sources?: TutorSource[];
  citedIds?: number[];
}> = ({ open, onClose, subjectId, defaultTopic, sources = [], citedIds = [] }) => {
  const [topic, setTopic] = useState(defaultTopic || '');
  const [template, setTemplate] = useState('concept');
  const [scene, setScene] = useState<WhiteboardScene | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pos, setPos] = useState({ x: 80, y: 64 });
  const [size, setSize] = useState({ w: 1040, h: 620 });
  const [preview, setPreview] = useState<{
    url: string; bbox?: number[]; width?: number; height?: number;
  } | null>(null);
  const [pageImages, setPageImages] = useState<Array<{
    id: number; url: string; file_name: string; page?: number | null;
    bbox?: number[]; width?: number; height?: number; cited: boolean;
  }>>([]);
  const [activePage, setActivePage] = useState(0);
  const sourceKey = sources.map((s) => `${s.id}:${s.page_image_url || ''}`).join('|');
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<{ index: number; startX: number; startY: number; orig: any } | null>(null);
  const windowDragRef = useRef<{
    startX: number; startY: number; origX: number; origY: number;
  } | null>(null);
  const resizeRef = useRef<{
    dir: string; startX: number; startY: number;
    origX: number; origY: number; origW: number; origH: number;
  } | null>(null);

  useEffect(() => {
    if (!open) return;
    const nextTopic = topic || defaultTopic || '';
    if (!topic && defaultTopic) setTopic(defaultTopic);
    // 同一学科 + 模板 + 主题直接恢复上次结果，不再重复调用模型
    setScene(readSceneCache(subjectId, template, nextTopic));
    setError(null);
    const width = Math.min(1040, window.innerWidth - 24);
    setPos({ x: Math.max(12, (window.innerWidth - width) / 2), y: 64 });
    setSize((prev) => ({
      w: Math.min(prev.w, width),
      h: Math.min(prev.h, Math.max(360, window.innerHeight - 100)),
    }));
    // 只在打开/切学科时恢复，不覆盖用户正在编辑的输入
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, subjectId]);

  useEffect(() => {
    let cancelled = false;
    const urls: string[] = [];
    const load = async () => {
      const loaded: typeof pageImages = [];
      for (const source of sources) {
        let sourceUrl = source.page_image_url || '';
        if (!sourceUrl && source.page != null && source.file_name) {
          // 历史回答的 sources 来自文本检索，只有页码、没有 page_image_url；
          // 用文件名反推页面索引的 doc_id，仍能把对应 PDF 页显示出来。
          const qs = new URLSearchParams({
            doc_id: `f:${source.file_name}`, page: String(source.page),
          });
          sourceUrl = `/knowledge/${encodeURIComponent(subjectId)}/pages/image?${qs.toString()}`;
        }
        if (!sourceUrl) continue;
        try {
          const res = await apiClient.get(
            sourceUrl.replace(/^\/api/, ''),
            { responseType: 'blob' },
          );
          const blob = res instanceof Blob ? res : (res as any)?.data;
          const url = URL.createObjectURL(blob as Blob);
          urls.push(url);
          loaded.push({
            id: source.id,
            url,
            file_name: source.file_name,
            page: source.page,
            bbox: Array.isArray(source.bbox) ? source.bbox.map(Number) : undefined,
            width: Number(source.image_width) || undefined,
            height: Number(source.image_height) || undefined,
            cited: citedIds.includes(source.id),
          });
        } catch {
          // 单页失败不影响其他页；列表里仍保留文字片段
        }
      }
      if (!cancelled) {
        setPageImages(loaded);
        const firstCited = loaded.findIndex((page) => page.cited);
        setActivePage(firstCited >= 0 ? firstCited : 0);
      }
    };
    void load();
    return () => {
      cancelled = true;
      urls.forEach((url) => URL.revokeObjectURL(url));
    };
    // sourceKey 已经覆盖 sources 的 id/page 变化
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey, citedIds.join(','), subjectId]);

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const resize = resizeRef.current;
      if (resize) {
        const dx = event.clientX - resize.startX;
        const dy = event.clientY - resize.startY;
        const maxW = window.innerWidth - 24;
        const maxH = window.innerHeight - 40;
        const minW = Math.min(620, maxW);
        const minH = 360;
        let nextW = resize.origW;
        let nextH = resize.origH;
        let nextX = resize.origX;
        let nextY = resize.origY;
        if (resize.dir.includes('e')) nextW = resize.origW + dx;
        if (resize.dir.includes('w')) { nextW = resize.origW - dx; nextX = resize.origX + dx; }
        if (resize.dir.includes('s')) nextH = resize.origH + dy;
        if (resize.dir.includes('n')) { nextH = resize.origH - dy; nextY = resize.origY + dy; }
        nextW = Math.max(minW, Math.min(maxW, nextW));
        nextH = Math.max(minH, Math.min(maxH, nextH));
        if (resize.dir.includes('w')) nextX = resize.origX + resize.origW - nextW;
        if (resize.dir.includes('n')) nextY = resize.origY + resize.origH - nextH;
        nextX = Math.max(12, Math.min(window.innerWidth - nextW - 12, nextX));
        nextY = Math.max(12, Math.min(window.innerHeight - nextH - 12, nextY));
        setSize({ w: nextW, h: nextH });
        setPos({ x: nextX, y: nextY });
        return;
      }
      const drag = windowDragRef.current;
      if (!drag) return;
      const width = Math.min(1040, window.innerWidth - 24);
      const maxX = Math.max(12, window.innerWidth - width - 12);
      const maxY = Math.max(12, window.innerHeight - 120);
      setPos({
        x: Math.max(12, Math.min(maxX, drag.origX + event.clientX - drag.startX)),
        y: Math.max(12, Math.min(maxY, drag.origY + event.clientY - drag.startY)),
      });
    };
    const up = () => {
      windowDragRef.current = null;
      resizeRef.current = null;
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
  }, []);

  const startWindowDrag = (event: React.PointerEvent) => {
    if ((event.target as HTMLElement).closest('button')) return;
    windowDragRef.current = {
      startX: event.clientX, startY: event.clientY,
      origX: pos.x, origY: pos.y,
    };
    event.preventDefault();
  };

  const startResize = (event: React.PointerEvent, dir: string) => {
    event.stopPropagation();
    event.preventDefault();
    resizeRef.current = {
      dir, startX: event.clientX, startY: event.clientY,
      origX: pos.x, origY: pos.y, origW: size.w, origH: size.h,
    };
  };

  const generate = async (templateOverride?: string, force = false) => {
    const effectiveTemplate = templateOverride || template;
    const value = topic.trim();
    if ((!value && (effectiveTemplate === 'ai' || effectiveTemplate === 'concept')) || !subjectId) return;
    if (!force) {
      const cached = readSceneCache(subjectId, effectiveTemplate, value);
      if (cached) {
        setScene(cached);
        setError(null);
        return;
      }
    }
    setLoading(true);
    setError(null);
    try {
      const data = await generateWhiteboard({
        subject_id: subjectId, topic: value,
        template: effectiveTemplate === 'ai' ? undefined : effectiveTemplate,
      });
      setScene(data);
      writeSceneCache(subjectId, effectiveTemplate, value, data);
    } catch (e: any) {
      setError(e?.response?.data?.detail || '白板生成失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  const clearScene = () => {
    try { localStorage.removeItem(cacheKeyOf(subjectId, template, topic)); } catch { /* 忽略 */ }
    setScene(null);
    setError(null);
  };

  const openPageImage = (page: any) => {
    if (!page?.url) return;
    setPreview({
      url: page.url,
      bbox: page.bbox,
      width: page.width,
      height: page.height,
    });
  };

  const closePreview = () => setPreview(null);

  const bboxStyleOf = (bbox?: number[], width?: number,
                       height?: number): React.CSSProperties | undefined => {
    if (!bbox || !width || !height) return undefined;
    const [x1, y1, x2, y2] = bbox;
    return {
      position: 'absolute',
      left: (x1 / width) * 100 + '%',
      top: (y1 / height) * 100 + '%',
      width: ((x2 - x1) / width) * 100 + '%',
      height: ((y2 - y1) / height) * 100 + '%',
      border: '3px solid #f59e0b',
      background: 'rgba(245, 158, 11, 0.18)',
      pointerEvents: 'none',
    };
  };

  const bboxStyle = () => bboxStyleOf(preview?.bbox, preview?.width, preview?.height);

  const toSvgPoint = (event: React.PointerEvent) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect || !scene) return { x: 0, y: 0 };
    return {
      x: ((event.clientX - rect.left) / rect.width) * scene.width,
      y: ((event.clientY - rect.top) / rect.height) * scene.height,
    };
  };

  const startDrag = (event: React.PointerEvent, index: number, el: any) => {
    event.stopPropagation();
    const point = toSvgPoint(event);
    dragRef.current = {
      index, startX: point.x, startY: point.y,
      orig: JSON.parse(JSON.stringify(el)),
    };
    (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
  };

  const moveElement = (orig: any, dx: number, dy: number) => {
    const maxX = scene?.width || 1000;
    const maxY = scene?.height || 700;
    const clamp = (value: number, max: number) => Math.max(0, Math.min(max, value));
    const next = { ...orig };
    if (orig.type === 'text' || orig.type === 'rect' || orig.type === 'circle') {
      next.x = clamp((orig.x || 0) + dx, maxX);
      next.y = clamp((orig.y || 0) + dy, maxY);
    } else if (orig.type === 'line' || orig.type === 'arrow') {
      next.x1 = clamp((orig.x1 || 0) + dx, maxX);
      next.y1 = clamp((orig.y1 || 0) + dy, maxY);
      next.x2 = clamp((orig.x2 || 0) + dx, maxX);
      next.y2 = clamp((orig.y2 || 0) + dy, maxY);
    } else if (orig.type === 'curve') {
      next.points = (orig.points || []).map((point: number[]) =>
        [clamp((point[0] || 0) + dx, maxX), clamp((point[1] || 0) + dy, maxY)]);
    }
    return next;
  };

  const onPointerMove = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag || !scene) return;
    const point = toSvgPoint(event);
    const dx = point.x - drag.startX;
    const dy = point.y - drag.startY;
    setScene((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        elements: prev.elements.map((item, index) =>
          index === drag.index ? moveElement(drag.orig, dx, dy) : item),
      };
    });
  };

  const renderElement = (el: any) => {
    const color = el.color || '#1f2937';
    switch (el.type) {
      case 'text':
        return (
          <text x={el.x} y={el.y} fill={color}
                fontSize={el.size || 18} fontFamily="system-ui, sans-serif">
            {el.text}
          </text>
        );
      case 'line':
        return <line x1={el.x1} y1={el.y1} x2={el.x2} y2={el.y2}
                     stroke={color} strokeWidth={2} />;
      case 'arrow':
        return <line x1={el.x1} y1={el.y1} x2={el.x2} y2={el.y2}
                     stroke={color} strokeWidth={2} markerEnd="url(#wb-arrow)" />;
      case 'rect':
        return (
          <g>
            <rect x={el.x} y={el.y} width={el.w} height={el.h} rx={10}
                  fill="#ffffff" stroke={color} strokeWidth={2} />
            {el.text && (
              <foreignObject x={el.x} y={el.y} width={el.w} height={el.h}
                             style={{ pointerEvents: 'none' }}>
                <div {...({ xmlns: 'http://www.w3.org/1999/xhtml' } as any)} className="wb-rect-text"
                     style={{ color, fontSize: (el.text || '').length > 40 ? 13 : 15 }}>
                  {el.text}
                </div>
              </foreignObject>
            )}
          </g>
        );
      case 'circle':
        return (
          <g>
            <circle cx={el.x} cy={el.y} r={el.r} fill="none" stroke={color} strokeWidth={2} />
            {el.text && (
              <text x={el.x} y={el.y} fill={color} fontSize={15}
                    textAnchor="middle" dominantBaseline="middle">
                {el.text}
              </text>
            )}
          </g>
        );
      case 'curve':
        return <polyline points={(el.points || []).map((p: number[]) => p.join(',')).join(' ')}
                         fill="none" stroke={color} strokeWidth={2.5} />;
      default:
        return null;
    }
  };

  const activeImage = pageImages[activePage];

  return (
    <>
      <Modal
        title={(
          <div className="whiteboard-drag-title" onPointerDown={startWindowDrag}>
            <span>AI 白板</span>
            <span className="whiteboard-drag-desc">依据本轮资料生成板书，分步讲、可展开</span>
            <span className="whiteboard-drag-hint">拖动标题移动</span>
          </div>
        )}
        open={open}
        onCancel={onClose}
        width={size.w}
        mask={false}
        centered={false}
        footer={null}
        destroyOnClose
        wrapClassName="whiteboard-float-wrap"
        style={{
          top: pos.y, left: pos.x, margin: 0, position: 'fixed',
          maxWidth: 'calc(100vw - 24px)',
        }}
        styles={{
          body: {
            height: size.h - 54, overflow: 'hidden',
            display: 'flex', flexDirection: 'column',
          },
        }}
      >
        <Space.Compact style={{ width: '100%', marginBottom: 10 }}>
          <Select
            value={template}
            onChange={(value) => {
              setTemplate(value);
              // 先恢复该模板的缓存；没有缓存才调用模型
              void generate(value);
            }}
            style={{ width: 132 }}
            options={[
              { value: 'concept', label: '概念图' },
              { value: 'ai', label: 'AI 自由生成' },
              { value: 'function', label: '函数图像' },
              { value: 'geometry', label: '平面几何' },
              { value: 'force', label: '受力分析' },
            ]}
          />
          <Input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            onPressEnter={() => void generate(undefined, true)}
            placeholder="例如：二叉树的前中后序遍历、勾股定理、受力分析"
            disabled={loading}
          />
          <Button type="primary" icon={<BulbOutlined />} loading={loading}
                  onClick={() => void generate(undefined, true)}>
            {scene ? '重新生成' : '生成'}
          </Button>
          <Button icon={<ReloadOutlined />} disabled={!scene || loading}
                  onClick={clearScene}>
            清空
          </Button>
        </Space.Compact>

        {['function', 'geometry', 'force'].includes(template) && (
          <Alert type="info" showIcon style={{ marginBottom: 10 }}
                 message="这是通用图形模板，只画示意形状，不解释当前知识点；要生成概念讲解请切「概念图」。" />
        )}
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 10 }} />}

        <div className="whiteboard-layout">
          <div className="whiteboard-canvas">
            {loading ? (
              <Spin tip="正在画…" />
            ) : scene ? (
              <svg ref={svgRef} viewBox={`0 0 ${scene.width} ${scene.height}`} width="100%"
                   onPointerMove={onPointerMove}
                   onPointerUp={() => { dragRef.current = null; }}
                   onPointerLeave={() => { dragRef.current = null; }}
                   style={{ maxHeight: 560, touchAction: 'none', userSelect: 'none' }}>
                <defs>
                  <marker id="wb-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3"
                          orient="auto" markerUnits="strokeWidth">
                    <path d="M0,0 L0,6 L9,3 z" fill="#64748b" />
                  </marker>
                </defs>
                <rect x="0" y="0" width={scene.width} height={scene.height} fill="#ffffff" />
                {scene.elements.map((el, index) => (
                  <g key={index} style={{ cursor: 'move' }}
                     onPointerDown={(event) => startDrag(event, index, el)}>
                    {renderElement(el)}
                  </g>
                ))}
              </svg>
            ) : (
              <Text type="secondary">输入一个知识点，让 AI 画一张板书</Text>
            )}
          </div>

          <aside className="whiteboard-sources">
            <div className="whiteboard-sources-head">
              <span>课件原文</span>
              <Tag>{sources.length}</Tag>
            </div>
            {activeImage ? (
              <>
                <div className="whiteboard-pdf-frame">
                  <img src={activeImage.url} alt={activeImage.file_name}
                       onClick={() => openPageImage(activeImage)} />
                  {activeImage.bbox && (
                    <div style={bboxStyleOf(activeImage.bbox, activeImage.width, activeImage.height)} />
                  )}
                </div>
                <div className="whiteboard-pdf-meta">
                  <Tooltip title={activeImage.file_name}>
                    <span className="whiteboard-source-name">{activeImage.file_name}</span>
                  </Tooltip>
                  {activeImage.page != null && (
                    <Tag>[{activeImage.id}] 第 {activeImage.page} 页</Tag>
                  )}
                  {activeImage.cited && <Tag color="green">已引用</Tag>}
                </div>
                {pageImages.length > 1 && (
                  <div className="whiteboard-pdf-pages">
                    {pageImages.map((page, index) => (
                      <Button key={page.id + '-' + index} size="small"
                              type={index === activePage ? 'primary' : 'default'}
                              onClick={() => setActivePage(index)}>
                        {page.page != null ? `[${page.id}] 第 ${page.page} 页` : `[${page.id}] 片段`}
                      </Button>
                    ))}
                  </div>
                )}
                <div className="whiteboard-sources-hint">
                  编号对应 AI 回答里的 [x]；黄色框为引用位置，点图片可放大。
                </div>
              </>
            ) : sources.length > 0 ? (
              <div className="whiteboard-sources-hint">
                该资料尚未建立页面图，暂无法展示课件原文。请到资料库点「建立页面索引」。
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                     description={<Text type="secondary" style={{ fontSize: 12 }}>暂无课件引用</Text>} />
            )}
          </aside>
        </div>

        {scene && (
          <Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
            {scene.title} · 共 {scene.elements.length} 个元素（SVG 渲染，不执行模型脚本）· 可拖动元素调整位置 · 已保留上次结果，点「重新生成」才会重新调用模型
          </Text>
        )}

        {/* 8 个缩放把手：鼠标移到任意边界/角落都能拉大缩小 */}
        {(['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'] as const).map((dir) => (
          <div key={dir} className={`wb-resize wb-resize-${dir}`} aria-hidden
               onPointerDown={(event) => startResize(event, dir)} />
        ))}
      </Modal>

      <Modal title="课件原文" open={!!preview} onCancel={closePreview} footer={null} width={900}>
        {preview && (
          <div style={{ position: 'relative', width: '100%' }}>
            <img src={preview.url} alt="课件原文" style={{ width: '100%', display: 'block' }} />
            {bboxStyle() && <div style={bboxStyle()} />}
          </div>
        )}
        <Text type="secondary" style={{ fontSize: 12 }}>
          高亮框对应检索命中的段落；页面图由后端按页渲染，坐标已按图片尺寸换算。
        </Text>
      </Modal>
    </>
  );
};

export default WhiteboardModal;