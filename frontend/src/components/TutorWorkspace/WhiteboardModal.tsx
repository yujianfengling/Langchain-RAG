import React, { useEffect, useRef, useState } from 'react';
import { Button, Empty, Modal, Spin, Tag, Tooltip, Typography } from 'antd';
import apiClient from '../../api/axios';
import PdfCanvasViewer from '../PdfViewer/PdfCanvasViewer';
import { useAuth } from '../../context/AuthContext';
import type { TutorSource } from '../../api/tutor';
import MindBoard from './MindBoard';

const { Text } = Typography;

/** 引用页码可能是 "1-2" 这样的范围；PDF.js 取第一页即可。 */
const firstPageNumber = (value: unknown): number | null => {
  const matched = String(value ?? '').match(/\d+/);
  return matched ? Number(matched[0]) : null;
};

/**
 * 白板（2026-11-14 重做）：
 * 主区是学生的思维板书（MindBoard：思维导图 + 画笔随手记 + 导出 PDF），
 * 右栏是本轮回答引用的课件页——点聊天里的 [1] 角标就带着对应页码打开这里。
 * 原「AI 生成板书」已按需求移除，/tutor/whiteboard 后端接口保留但前端不再调用。
 */
const WhiteboardModal: React.FC<{
  open: boolean;
  onClose: () => void;
  subjectId: string;
  nodeId?: string;
  defaultTopic?: string;
  sources?: TutorSource[];
  citedIds?: number[];
  /** 点引用角标进来时带上的来源 id；nonce 变化即重新定位，即使 id 相同。 */
  focusSource?: { id: number; nonce: number } | null;
}> = ({ open, onClose, subjectId, nodeId, defaultTopic, sources = [], citedIds = [], focusSource = null }) => {
  const { user } = useAuth();
  const userId = user?.id || '';
  const [pos, setPos] = useState({ x: 80, y: 48 });
  const [size, setSize] = useState({ w: 1200, h: 700 });
  const [preview, setPreview] = useState<{
    url: string; bbox?: number[]; width?: number; height?: number;
  } | null>(null);
  const [pageImages, setPageImages] = useState<Array<{
    id: number; url: string; file_name: string; page?: number | null;
    bbox?: number[]; width?: number; height?: number; cited: boolean;
    isPdf: boolean;
  }>>([]);
  const [activePage, setActivePage] = useState(0);
  const [activePdf, setActivePdf] = useState<{
    fileName: string; owner: string; version: string; data: ArrayBuffer;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sourceKey = JSON.stringify(sources);
  const owner = JSON.stringify([userId, subjectId]);
  const windowDragRef = useRef<{
    startX: number; startY: number; origX: number; origY: number;
  } | null>(null);
  const resizeRef = useRef<{
    dir: string; startX: number; startY: number;
    origX: number; origY: number; origW: number; origH: number;
  } | null>(null);

  // 思维板书按 用户+学科+知识点 分隔存档；key 变化时 MindBoard 用同名 key 重挂载读档
  const persistKey = `edu.mindboard.v1.${userId || 'anon'}.${subjectId || 'none'}.${nodeId || defaultTopic || 'free'}`;

  useEffect(() => {
    if (!open) return;
    const width = Math.min(1200, window.innerWidth - 24);
    setPos({ x: Math.max(12, (window.innerWidth - width) / 2), y: 48 });
    setSize((prev) => ({
      w: Math.min(prev.w, width),
      h: Math.min(prev.h, Math.max(480, window.innerHeight - 80)),
    }));
  }, [open]);

  useEffect(() => {
    if (!open) {
      setPreview(null);
      setActivePdf(null);
      setError(null);
    }
  }, [open]);

  useEffect(() => {
    let cancelled = false;
    setPageImages([]);
    setActivePage(0);
    const urls: string[] = [];
    const load = async () => {
      const loaded: typeof pageImages = [];
      for (const source of sources) {
        const isPdf = /\.pdf$/i.test(source.file_name || '');
        if (isPdf) {
          // PDF 交给浏览器 PDF.js 渲染，不再请求后端整页图
          if (source.page == null) continue;
          loaded.push({
            id: source.id,
            url: '',
            file_name: source.file_name,
            page: source.page,
            bbox: Array.isArray(source.bbox) ? source.bbox.map(Number) : undefined,
            width: Number(source.image_width) || undefined,
            height: Number(source.image_height) || undefined,
            cited: citedIds.includes(source.id),
            isPdf: true,
          });
          continue;
        }
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
            isPdf: false,
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
  }, [sourceKey, citedIds.join(','), subjectId, userId, open]);

  // 引用角标点进来：定位到那一号资料对应的课件页（页图异步加载完也要能落位）
  useEffect(() => {
    if (!open || !focusSource?.id) return;
    const idx = pageImages.findIndex((page) => page.id === focusSource.id);
    if (idx >= 0) setActivePage(idx);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusSource?.nonce, open, pageImages.length]);

  const activePdfName = pageImages[activePage]?.isPdf
    ? pageImages[activePage].file_name : '';

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setActivePdf(null);
    setError(null);
    if (!open || !activePdfName) return;
    const load = async () => {
      try {
        const res = await apiClient.get(
          `/knowledge/${encodeURIComponent(subjectId)}/documents/${encodeURIComponent(activePdfName)}/file`,
          { responseType: 'blob', signal: controller.signal },
        );
        const blob = res instanceof Blob ? res : (res as any)?.data;
        if (!(blob instanceof Blob)) throw new Error('PDF 内容为空');
        const data = await blob.arrayBuffer();
        const hash = await crypto.subtle.digest('SHA-256', data);
        const version = Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, '0')).join('');
        if (!cancelled) {
          setActivePdf({ fileName: activePdfName, owner, version, data });
        }
      } catch {
        if (!cancelled) setError('PDF 课件加载失败');
      }
    };
    void load();
    return () => { cancelled = true; controller.abort(); };
  }, [activePdfName, subjectId, owner, open, sourceKey]);

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const resize = resizeRef.current;
      if (resize) {
        const dx = event.clientX - resize.startX;
        const dy = event.clientY - resize.startY;
        const maxW = window.innerWidth - 24;
        const maxH = window.innerHeight - 40;
        const minW = Math.min(880, maxW);
        const minH = 480;
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
      const width = Math.min(1200, window.innerWidth - 24);
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

  const activeImage = pageImages[activePage];

  // AI 起稿的页面上下文：优先右栏正在展示的那页；页面图没建（docx 等）时
  // 退回第一个来源的 file_name+page，后端会按页取文本、取不到再检索兜底
  const activeSource = (activeImage && sources.find((s) => s.id === activeImage.id))
    || sources[0]
    || null;
  const pageContext = activeSource ? {
    file_name: activeSource.file_name,
    page: activeSource.page ?? null,
    label: defaultTopic,
  } : null;

  return (
    <>
      <Modal
        title={(
          <div className="whiteboard-drag-title" onPointerDown={startWindowDrag}>
            <span>白板</span>
            <span className="whiteboard-drag-desc">课件对照 · 思维导图 · 随手记，可导出 PDF</span>
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
        <div className="whiteboard-layout">
          <div className="whiteboard-workarea">
            <MindBoard key={persistKey} persistKey={persistKey}
                       boardKey={nodeId || defaultTopic || 'free'}
                       subjectId={subjectId}
                       pageContext={pageContext}
                       defaultRootText={defaultTopic || '中心主题'} />
          </div>

          <aside className="whiteboard-sources">
            <div className="whiteboard-sources-head">
              <span>课件页</span>
              <Tag>{sources.length}</Tag>
            </div>
            {activeImage ? (
              <>
                <div className="whiteboard-pdf-frame">
                  {activeImage.isPdf ? (
                    activePdf?.owner === owner && activePdf?.fileName === activeImage.file_name && firstPageNumber(activeImage.page) != null ? (
                      <PdfCanvasViewer
                        key={JSON.stringify([owner, activePdf.fileName, activePdf.version])}
                        data={activePdf.data}
                        pages={[firstPageNumber(activeImage.page) as number]}
                        highlightPage={firstPageNumber(activeImage.page) as number}
                        highlight={{
                          bbox: activeImage.bbox,
                          imageWidth: activeImage.width,
                          imageHeight: activeImage.height,
                        }}
                      />
                    ) : (
                      <div style={{ padding: 32, textAlign: 'center' }}>
                        {error ? <Text type="danger">原文加载失败，请关闭后重试</Text> : <Spin />}
                      </div>
                    )
                  ) : (
                    <img src={activeImage.url} alt={activeImage.file_name}
                         onClick={() => activeImage.url && setPreview({
                           url: activeImage.url,
                           bbox: activeImage.bbox,
                           width: activeImage.width,
                           height: activeImage.height,
                         })} />
                  )}
                  {!activeImage.isPdf && activeImage.bbox && (
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
                  编号对应 AI 回答里的 [x]；黄色框为引用位置。
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

        {/* 8 个缩放把手：鼠标移到任意边界/角落都能拉大缩小 */}
        {(['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'] as const).map((dir) => (
          <div key={dir} className={`wb-resize wb-resize-${dir}`} aria-hidden
               onPointerDown={(event) => startResize(event, dir)} />
        ))}
      </Modal>

      <Modal title="课件原文" open={!!preview} onCancel={() => setPreview(null)} footer={null} width={900}>
        {preview && (
          <div style={{ position: 'relative', width: '100%' }}>
            <img src={preview.url} alt="课件原文" style={{ width: '100%', display: 'block' }} />
            {bboxStyleOf(preview.bbox, preview.width, preview.height)
              && <div style={bboxStyleOf(preview.bbox, preview.width, preview.height)} />}
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
