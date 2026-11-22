import React, { useEffect, useState } from 'react';
import { Alert, Drawer, Spin, Typography } from 'antd';
import PdfCanvasViewer from '../../components/PdfViewer/PdfCanvasViewer';
import { DEMO_TEXTBOOK } from './ids';
import { getBundle } from '../dataSource';
import type { TextbookSource } from './types';

const { Text, Paragraph } = Typography;

function textbookTitle(source: TextbookSource | null): string {
  if (!source) return '教材资料';
  if (source.docId === DEMO_TEXTBOOK.docId) return '演示教材';
  const printed = source.printedPage?.trim();
  if (!printed) return '教材原页（印刷页待定位）';
  const match = printed.match(/^(上册|下册)?\s*(\d+)$/);
  return match ? `教材${match[1] || ''}第${match[2]}页` : `教材${printed}`;
}

/** 浮层关闭不修改调用方的题目、筛选或滚动位置。 */
const TextbookDrawer: React.FC<{
  open: boolean;
  source: TextbookSource | null;
  onClose: () => void;
}> = ({ open, source, onClose }) => {
  const [result, setResult] = useState<{ key: string; data?: ArrayBuffer; error?: string } | null>(null);
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

  return (
    <Drawer title={textbookTitle(source)} open={open} onClose={onClose}
      width="min(880px, 100vw)" destroyOnClose={false}>
      {source ? (
        <>
          <Paragraph style={{ marginBottom: 8 }}>{pageRef?.anchor || source.exampleTitle}</Paragraph>
          <Text className="teacher-muted">
            {demo ? '演示样例 · 非正式教材' : source.pdfUrl ? '教材原页' : '教材出处待定位'} · 关闭后返回当前题或教材条目
          </Text>
          <details key={requestKey} style={{ margin: '12px 0 20px' }}>
            <summary style={{ cursor: 'pointer' }}>出处详情</summary>
            <div className="teacher-muted" style={{ marginTop: 8, lineHeight: 1.9, overflowWrap: 'anywhere' }}>
              <div>教材编号：{source.docId} · {source.docName}</div>
              <div>PDF 文件页：{url && validPage ? source.page : '待定位'}</div>
              <div>实际印刷页：{source.printedPage || '未提供'}</div>
              <div>核对状态：{source.verified === true ? '已核对具体页' : source.verified === false ? '未核对具体页' : '未提供'}</div>
              {source.standard && (
                <details style={{ marginTop: 8 }}>
                  <summary style={{ cursor: 'pointer' }}>课程要求</summary>
                  <div>课标编号：{source.standard.code}</div>
                  <div>课标文件页：{source.standard.pdfPage ?? '待定位'} · 正文页：{source.standard.printed ?? '待定位'}</div>
                </details>
              )}
            </div>
          </details>
          {!url || !validPage ? (
            <Alert type="warning" showIcon message="尚未定位到教材文件页，暂不能打开原页" />
          ) : currentResult?.error ? (
            <Alert type="error" showIcon message={currentResult.error} />
          ) : open && currentResult?.data ? (
            <PdfCanvasViewer key={requestKey} data={currentResult.data} pages={[source.page]} highlightPage={source.page} />
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
