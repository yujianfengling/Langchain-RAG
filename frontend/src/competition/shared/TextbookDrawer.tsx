import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Drawer, Space, Tag, Typography } from 'antd';
import { CloseOutlined, FileTextOutlined } from '@ant-design/icons';
import PdfCanvasViewer from '../../components/PdfViewer/PdfCanvasViewer';
import { DEMO_TEXTBOOK } from './ids';
import type { TextbookSource } from './types';

const { Text, Title } = Typography;

/**
 * 教材抽屉：从任意页面的「出处」打开，侧边显示样例 PDF 的指定页。
 *
 * 复用现有 PdfCanvasViewer（PDF.js 渲染），不改后端、不改权限。
 * 打开时把 data 缓存为稳定 ArrayBuffer（PdfCanvasViewer 依赖引用稳定）。
 * 关闭由 Drawer 的 onClose 触发，调用方的 original context 不丢失——
 * 因为抽屉是浮层，底层页面状态（筛选、选中的考点/题目）原封不动。
 */
const TextbookDrawer: React.FC<{
  open: boolean;
  source: TextbookSource | null;
  onClose: () => void;
}> = ({ open, source, onClose }) => {
  const [data, setData] = useState<ArrayBuffer | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 缓存 PDF 为 ArrayBuffer（引用稳定）；官方教材走受控接口，演示样例走 public/assets
  useEffect(() => {
    let cancelled = false;
    if (!open) return undefined;
    setError(null);
    setData(null);
    const url = source?.pdfUrl || DEMO_TEXTBOOK.url;
    const headers: Record<string, string> = {};
    if (source?.pdfUrl) {
      try {
        const token = localStorage.getItem('edu_user_token');
        if (token) headers['X-User-Token'] = token;
      } catch { /* 隐私模式下无 localStorage，后端会返回 401 并如实展示 */ }
    }
    fetch(url, { headers })
      .then((r) => {
        if (!r.ok) throw new Error(`教材加载失败（${r.status}）${r.status === 401 ? '：请先登录教师账号' : ''}`);
        return r.arrayBuffer();
      })
      .then((buf) => { if (!cancelled) setData(buf); })
      .catch((e) => { if (!cancelled) setError(e?.message || '教材加载失败'); });
    return () => { cancelled = true; };
  }, [open, source?.pdfUrl]);

  const title = useCallback(() => (
    <Space direction="vertical" size={2} style={{ flex: 1 }}>
      <Title level={5} style={{ margin: 0 }}>
        <FileTextOutlined /> 教材出处
      </Title>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {source?.pdfUrl ? '官方教材切片 · 受控读取' : '演示样例'} · 关闭后回到原作答位置
      </Text>
    </Space>
  ), [source?.pdfUrl]);

  return (
    <Drawer
      title={title()}
      open={open}
      onClose={onClose}
      width="min(640px, 92vw)"
      destroyOnClose={false}
      extra={<Button type="text" icon={<CloseOutlined />} onClick={onClose}>关闭</Button>}
    >
      {source ? (
        <>
          <Space wrap style={{ marginBottom: 12 }}>
            <Tag color="blue">{source.docName}</Tag>
            <Tag color="gold">PDF 第 {source.page} 页</Tag>
            {source.printedPage && <Tag>教材印刷页 {source.printedPage}</Tag>}
            {source.standard && (
              <Tag>课标 {source.standard.code}：文件页 {source.standard.pdfPage ?? '待定位'}
                {source.standard.printed != null ? `／正文页 ${source.standard.printed}` : ''}</Tag>
            )}
          </Space>
          <Alert
            type={source.verified === false ? 'warning' : 'info'} showIcon style={{ marginBottom: 12 }}
            message={source.pdfUrl ? (source.verified ? '官方切片 · 已核对具体页' : '官方切片 · 显示可确认的范围')
              : '演示样例'}
            description={source.exampleTitle}
          />
          {error ? (
            <Alert type="error" showIcon message={error} />
          ) : data ? (
            <PdfCanvasViewer data={data} pages={[source.page]} highlightPage={source.page} />
          ) : (
            <div style={{ padding: 48, textAlign: 'center', color: '#737d8f' }}>正在加载教材样例…</div>
          )}
        </>
      ) : (
        <Alert type="warning" showIcon message="没有可显示的教材出处" />
      )}
    </Drawer>
  );
};

export default TextbookDrawer;
