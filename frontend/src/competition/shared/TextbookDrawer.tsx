import { printedPageLabel } from './textbookPage';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Drawer, Spin, Typography } from 'antd';
import PdfCanvasViewer from '../../components/PdfViewer/PdfCanvasViewer';
import { openPdf } from '../../components/PdfViewer/pdfjs';
import { DEMO_TEXTBOOK } from './ids';
import { getBundle } from '../dataSource';
import type { TextbookSource } from './types';

const { Text, Paragraph } = Typography;

function textbookTitle(source: TextbookSource | null): string {
  if (!source) return '教材资料';
  if (source.docId === DEMO_TEXTBOOK.docId) return '演示教材';
  if (source.docId === 'CR') return source.docName;
  const printed = source.printedPage?.trim();
  if (!printed) return '教材原页（印刷页待定位）';
  return printedPageLabel(printed);
}

/** 浮层关闭不修改调用方的题目、筛选或滚动位置。 */
const TextbookDrawer: React.FC<{
  open: boolean;
  source: TextbookSource | null;
  onClose: () => void;
}> = ({ open, source, onClose }) => {
  const [result, setResult] = useState<{ key: string; data?: ArrayBuffer; error?: string } | null>(null);
  /** 受控翻页：null 表示回到被引页；totalPages 来自 PDF numPages。 */
  const [viewPage, setViewPage] = useState<number | null>(null);
  const [totalPages, setTotalPages] = useState<number | null>(null);
  const pdfAreaRef = useRef<HTMLDivElement | null>(null);
  const demo = source?.docId === DEMO_TEXTBOOK.docId;
  const url = source?.pdfUrl || (demo ? DEMO_TEXTBOOK.url : null);
  const pageRef = getBundle()?.curriculumMap?.indicators
    .flatMap((indicator) => indicator.textbook_pages)
    .find((reference) => reference.material_id === source?.docId && reference.pdf_page === source?.page);
  const page = source?.page;
  const validPage = page != null && Number.isInteger(page) && page > 0;
  const requestKey = `${url || ''}#${page ?? ''}`;
  const authenticated = !!source?.pdfUrl;

  useEffect(() => {
    setResult(null);
    if (!open || !url || !validPage) return undefined;
    const controller = new AbortController();
    let cancelled = false;
    const headers: Record<string, string> = {};
    if (authenticated) {
      try {
        const token = localStorage.getItem('edu_user_token');
        if (token) headers['X-User-Token'] = token;
      } catch { /* 后端仍按教师权限校验，失败时如实显示。 */ }
    }
    fetch(url, { headers, signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`教材加载失败（${response.status}）${response.status === 401 ? '：请先登录教师账号' : response.status === 403 ? '：当前账号无权读取教材' : ''}`);
        }
        return response.arrayBuffer();
      })
      .then((data) => { if (!cancelled) setResult({ key: requestKey, data }); })
      .catch((error) => {
        if (!cancelled) setResult({ key: requestKey, error: error?.message || '教材加载失败' });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [open, url, authenticated, validPage, requestKey]);

  const currentResult = result?.key === requestKey ? result : null;

  // 换出处或关闭时回到被引页，避免上一次的翻页位置串到下一份教材。
  useEffect(() => {
    setViewPage(null);
    setTotalPages(null);
  }, [requestKey, open]);

  // 复用现有 openPdf 读取总页数（内部会复制 buffer，不影响渲染用的同一份数据）。
  const resultData = currentResult?.data;
  useEffect(() => {
    if (!open || !resultData) return undefined;
    let cancelled = false;
    openPdf(resultData)
      .then((pdf) => {
        const n = pdf.numPages;
        try { pdf.destroy(); } catch { /* 忽略 */ }
        if (!cancelled && Number.isInteger(n) && n > 0) setTotalPages(n);
      })
      .catch(() => { /* 总页数未知时仅显示文件页，翻页按钮保持禁用 */ });
    return () => { cancelled = true; };
  }, [open, resultData]);

  // 印刷页换算：沿用 textbookPage 的「上册/下册 + 数字」口径。
  // 优先精确命中的已核对页；其余页仅在被两个同册、同偏移的已核对页夹住时才内插，避免跨册误标。
  const printedRefs = useMemo(() => {
    const refs = getBundle()?.curriculumMap?.indicators.flatMap((i) => i.textbook_pages) || [];
    return refs
      .filter((r) => r.material_id === source?.docId && Number.isInteger(r.pdf_page) && r.pdf_page > 0)
      .map((r) => ({ page: r.pdf_page, m: r.printed?.trim().match(/^(上册|下册)?\s*(\d+)$/) }))
      .filter((r): r is { page: number; m: RegExpMatchArray } => !!r.m)
      .sort((a, b) => a.page - b.page);
  }, [source?.docId]);

  const effectivePage = Math.min(Math.max(viewPage ?? page ?? 1, 1), totalPages ?? Math.max(viewPage ?? page ?? 1, 1));
  const printedFor = (n: number): string | undefined => {
    if (n === source?.page && source?.printedPage) return source.printedPage;
    const exact = printedRefs.find((r) => r.page === n);
    if (exact) return `${exact.m[1] || ''}${exact.m[2]}`;
    const lo = [...printedRefs].reverse().find((r) => r.page < n);
    const hi = printedRefs.find((r) => r.page > n);
    if (lo && hi && (lo.m[1] || '') === (hi.m[1] || '')
      && Number(lo.m[2]) - lo.page === Number(hi.m[2]) - hi.page) {
      return `${lo.m[1] || ''}${n + Number(lo.m[2]) - lo.page}`;
    }
    return undefined;
  };
  const printedNow = printedFor(effectivePage);
  const goPage = (next: number) => {
    const clamped = Math.min(Math.max(next, 1), totalPages ?? Math.max(next, 1));
    setViewPage(clamped);
    requestAnimationFrame(() => pdfAreaRef.current?.scrollIntoView({ block: 'start' }));
  };

  return (
    <Drawer title={textbookTitle(source)} open={open} onClose={onClose}
      width="min(880px, 100vw)" destroyOnClose={false}>
      {source ? (
        <>
          <Paragraph style={{ marginBottom: 8 }}>{pageRef?.anchor || source.exampleTitle}</Paragraph>
          <Text className="teacher-muted">
            {demo ? '演示样例 · 非正式教材' : source.pdfUrl ? '教材原页' : '教材出处待定位'} · 关闭后返回当前题或教材条目
          </Text>
          <details key={`details-${requestKey}`} style={{ margin: '12px 0 20px' }}>
            <summary style={{ cursor: 'pointer' }}>出处详情</summary>
            <div className="teacher-muted" style={{ marginTop: 8, lineHeight: 1.9, overflowWrap: 'anywhere' }}>
              <div>{demo ? source.docName : printedPageLabel(source.printedPage)}</div>
              <div>{pageRef?.anchor || source.exampleTitle}</div>
              {source.standard?.printed && <div>课程标准 · 正文第 {source.standard.printed} 页</div>}
            </div>
          </details>
          {!url || !validPage ? (
            <Alert type="warning" showIcon message="尚未定位到教材文件页，暂不能打开原页" />
          ) : currentResult?.error ? (
            <Alert type="error" showIcon message={currentResult.error} />
          ) : open && currentResult?.data ? (
            <div ref={pdfAreaRef}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', margin: '0 0 10px' }}>
                <Button size="small" data-testid="textbook-prev-page" aria-label="上一页"
                  disabled={effectivePage <= 1} onClick={() => goPage(effectivePage - 1)}>上一页</Button>
                <Button size="small" data-testid="textbook-next-page" aria-label="下一页"
                  disabled={totalPages == null || effectivePage >= totalPages} onClick={() => goPage(effectivePage + 1)}>下一页</Button>
                <Text type="secondary" data-testid="textbook-page-indicator" aria-live="polite">
                  PDF 文件页 {effectivePage} / 共 {totalPages ?? '…'} 页{printedNow ? ` · 印刷页 ${printedPageLabel(printedNow)}` : ''}
                </Text>
              </div>
              <PdfCanvasViewer key={requestKey} data={currentResult.data} pages={[effectivePage]}
                pageLabel={(n) => { const p = printedFor(n); return p ? printedPageLabel(p) : '第 ' + n + ' 页'; }}
                highlightPage={source.page} />
            </div>
          ) : (
            <div role="status" aria-live="polite" style={{ padding: '48px 16px', textAlign: 'center' }}>
              <Spin /> <Text type="secondary">正在加载教材原页…</Text>
            </div>
          )}
        </>
      ) : open ? (
        <Alert type="warning" showIcon message="没有可显示的教材出处" />
      ) : null}
    </Drawer>
  );
};

export default TextbookDrawer;
