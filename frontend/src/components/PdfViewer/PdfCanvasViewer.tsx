import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Spin, Typography } from 'antd';
import { openPdf } from './pdfjs';

const { Text } = Typography;

export interface PdfHighlight {
  bbox?: number[];
  imageWidth?: number;
  imageHeight?: number;
}

/**
 * 用 PDF.js 在浏览器里渲染 PDF，不再依赖后端 page_images。
 * pages 为空时连续渲染整本；传 pages 时只渲染指定页（白板引用页）。
 */
const PdfCanvasViewer: React.FC<{
  data: ArrayBuffer;
  pages?: number[];
  highlightPage?: number;
  highlight?: PdfHighlight;
  className?: string;
}> = ({ data, pages, highlightPage, highlight, className }) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRefs = useRef<Record<number, HTMLCanvasElement | null>>({});
  const renderedRef = useRef<Set<number>>(new Set());
  const generationRef = useRef(0);
  const renderTasksRef = useRef<Set<any>>(new Set());
  const [pdfDoc, setPdfDoc] = useState<any>(null);
  const [pageNumbers, setPageNumbers] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const pageKey = (pages || []).join(',');

  useEffect(() => {
    generationRef.current += 1;
    const renderTasks = renderTasksRef.current;
    let cancelled = false;
    let doc: any = null;
    setLoading(true);
    setError(null);
    setPdfDoc(null);
    setPageNumbers([]);
    renderedRef.current = new Set();
    openPdf(data)
      .then((pdf) => {
        if (cancelled) {
          pdf.destroy();
          return;
        }
        doc = pdf;
        const nums = pages && pages.length
          ? pages
          : Array.from({ length: pdf.numPages }, (_, i) => i + 1);
        if (nums.some((n) => !Number.isInteger(n) || n < 1 || n > pdf.numPages)) {
          throw new Error(`引用页码无效，当前文档共 ${pdf.numPages} 页`);
        }
        setPdfDoc(pdf);
        setPageNumbers(nums);
      })
      .catch((e: any) => {
        if (!cancelled) setError(e?.message || 'PDF 加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      generationRef.current += 1;
      renderTasks.forEach((task) => task.cancel());
      renderTasks.clear();
      if (doc) {
        try { doc.destroy(); } catch { /* 忽略 */ }
      }
    };
    // pageKey 覆盖 pages 数组变化；data 是 ArrayBuffer，父组件需保持引用稳定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, pageKey]);

  const renderPage = useCallback(async (pageNo: number) => {
    if (!pdfDoc || renderedRef.current.has(pageNo)) return;
    const canvas = canvasRefs.current[pageNo];
    if (!canvas) return;
    renderedRef.current.add(pageNo);
    const generation = generationRef.current;
    try {
      const page = await pdfDoc.getPage(pageNo);
      if (generation !== generationRef.current) return;
      const base = page.getViewport({ scale: 1 });
      const available = Math.max(320, (containerRef.current?.clientWidth || 900) - 4);
      const scale = Math.min(2.2, Math.max(0.6, available / base.width));
      const viewport = page.getViewport({ scale });
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      canvas.style.width = '100%';
      canvas.style.height = 'auto';
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('浏览器不支持 Canvas');
      const task = page.render({ canvasContext: ctx, viewport });
      renderTasksRef.current.add(task);
      try { await task.promise; } finally { renderTasksRef.current.delete(task); }
    } catch (e: any) {
      if (generation !== generationRef.current) return;
      renderedRef.current.delete(pageNo);
      setError(e?.message || 'PDF 页面渲染失败');
    }
  }, [pdfDoc]);

  useEffect(() => {
    if (!pdfDoc || !pageNumbers.length) return;
    // 白板只渲染指定页：直接画
    if (pages && pages.length) {
      pageNumbers.forEach((n) => { void renderPage(n); });
      return;
    }
    const container = containerRef.current;
    if (!container) return;
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const n = Number((entry.target as HTMLElement).dataset.pdfPage);
        if (n) void renderPage(n);
      });
    }, { root: null, rootMargin: '500px 0px' });
    container.querySelectorAll<HTMLElement>('[data-pdf-page]').forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [pdfDoc, pageNumbers, pages, renderPage]);

  const highlightStyle = (): React.CSSProperties | undefined => {
    const bbox = highlight?.bbox;
    const width = highlight?.imageWidth;
    const height = highlight?.imageHeight;
    if (!bbox || bbox.length !== 4 || !width || !height) return undefined;
    const [x1, y1, x2, y2] = bbox;
    return {
      position: 'absolute',
      left: (x1 / width) * 100 + '%',
      top: (y1 / height) * 100 + '%',
      width: ((x2 - x1) / width) * 100 + '%',
      height: ((y2 - y1) / height) * 100 + '%',
      border: '3px solid #f59e0b',
      background: 'rgba(245, 158, 11, 0.16)',
      pointerEvents: 'none',
      zIndex: 2,
    };
  };

  return (
    <div ref={containerRef} className={'pdf-canvas-viewer ' + (className || '')}>
      {loading && (
        <div style={{ padding: 48, textAlign: 'center' }}>
          <Spin tip="正在加载 PDF…" />
        </div>
      )}
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 10 }} />}
      {pageNumbers.map((n) => (
        <div key={n} className="pdf-page-shell" data-pdf-page={n}>
          <Text type="secondary" className="pdf-page-label">第 {n} 页</Text>
          <div className="pdf-page-canvas-wrap">
            <canvas ref={(el) => { canvasRefs.current[n] = el; }} />
            {highlightPage === n && highlightStyle() && <div style={highlightStyle()} />}
          </div>
        </div>
      ))}
    </div>
  );
};

export default PdfCanvasViewer;
