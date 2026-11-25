import React, { useState } from 'react';
import { Alert, Button, Modal, Switch, Table, Tag, Typography, Upload } from 'antd';
import { InboxOutlined, UploadOutlined, RobotOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd';
import { getToken } from '../../api/authState';
import './importPanel.css';

const { Text, Paragraph } = Typography;
const CHANNELS = [
  { key: 'curriculum', label: '课标与教材资料', hint: '课程要求 / 课标条目 / 教材页（CSV·Excel·JSON·ZIP 均可）' },
  { key: 'paper', label: '试卷与题目', hint: '题目与材料 / 题干 / 评分点 / 出处' },
  { key: 'exam', label: '考试学情', hint: '模拟学生 / 测验节点 / 作答记录' },
] as const;
type ChannelKey = typeof CHANNELS[number]['key'];
const ALL_TABLES = ['课程要求', '题目与材料', '模拟学生', '测验节点', '作答记录'];

interface ReportRow { file: string; table: string | null; score?: number; mapped?: number; missing?: string[]; rows?: number; error?: string }

/** 健壮导入：分通道、任意格式、按表头识别、可分批合并、可选 AI 辅助识别。 */
const ImportPanel: React.FC<{ open: boolean; onClose: () => void }> = ({ open, onClose }) => {
  const [files, setFiles] = useState<Record<ChannelKey, UploadFile[]>>({ curriculum: [], paper: [], exam: [] });
  const [session, setSession] = useState<string>(() => localStorage.getItem('competition.importSession') || '');
  const [report, setReport] = useState<ReportRow[]>([]);
  const [present, setPresent] = useState<string[]>([]);
  const [aiAssist, setAiAssist] = useState(false);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ id: string; counts: Record<string, number> } | null>(null);

  const total = CHANNELS.reduce((n, c) => n + files[c.key].length, 0);
  const complete = present.length === 5;

  const recognize = async () => {
    setBusy(true); setError(null); setDone(null);
    try {
      const form = new FormData();
      CHANNELS.forEach((c) => files[c.key].forEach((f) => { if (f.originFileObj) form.append('files', f.originFileObj, f.name); }));
      form.append('session', session);
      form.append('ai_assist', aiAssist ? '1' : '0');
      const res = await fetch('/api/competition/import-flex', { method: 'POST', headers: { 'X-User-Token': getToken() || '' }, body: form });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`);
      setSession(data.session); localStorage.setItem('competition.importSession', data.session);
      setReport(data.report || []); setPresent(data.present || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const run = async () => {
    setRunning(true); setError(null);
    try {
      const res = await fetch('/api/competition/import-run', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-User-Token': getToken() || '' },
        body: JSON.stringify({ session }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`);
      setDone({ id: data.id, counts: data.counts || {} });
      localStorage.setItem('competition.importedId', data.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  return <Modal open={open} onCancel={onClose} width={860} title="导入评测数据（换材料复算·健壮识别）"
    footer={[
      <Button key="cancel" onClick={onClose}>关闭</Button>,
      done
        ? <Button key="go" type="primary" onClick={() => window.location.assign('/class-analytics?source=imported')}>切换到导入数据</Button>
        : <Button key="run" type="primary" icon={<UploadOutlined />} loading={running} disabled={!complete} onClick={() => void run()}>生成评测数据</Button>,
    ]}>
    <Paragraph type="secondary">
      支持任意文件名的 CSV / Excel / JSON / ZIP，按<strong>表头内容</strong>自动识别五张表并映射列（不依赖文件名）；
      可分通道、分批补充导入到同一会话。表头不认识时可开启 AI 辅助识别。数据在本地私有目录复算，不出域。
    </Paragraph>
    <div className="import-channels">
      {CHANNELS.map((c) => <div className="import-channel" key={c.key}>
        <div className="import-channel-head"><strong>{c.label}</strong><Text type="secondary">{files[c.key].length} 个文件</Text></div>
        <Upload.Dragger multiple accept=".csv,.xlsx,.xlsm,.json,.zip" fileList={files[c.key]} beforeUpload={() => false}
          onChange={({ fileList }) => setFiles((prev) => ({ ...prev, [c.key]: fileList }))}>
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或拖拽文件</p>
          <p className="ant-upload-hint">{c.hint}</p>
        </Upload.Dragger>
      </div>)}
    </div>
    <div className="import-toolbar">
      <Button type="primary" icon={<RobotOutlined />} loading={busy} disabled={total === 0} onClick={() => void recognize()}>识别并合并导入</Button>
      <span className="import-ai"><Switch size="small" checked={aiAssist} onChange={setAiAssist} /> AI 辅助识别未知表头</span>
      <Text type="secondary">已选 {total} 个文件 · 会话 {session || '新建'}</Text>
    </div>
    {present.length > 0 && <div className="import-present">
      {ALL_TABLES.map((t) => <Tag key={t} color={present.includes(t) ? 'green' : 'default'}>{present.includes(t) ? '✓ ' : '缺 '}{t}</Tag>)}
    </div>}
    {report.length > 0 && <Table size="small" pagination={false} rowKey="file" dataSource={report}
      columns={[
        { title: '文件', dataIndex: 'file', ellipsis: true },
        { title: '识别为', dataIndex: 'table', render: (t: string | null) => t ? <Tag color="blue">{t}</Tag> : <Tag color="red">未识别</Tag> },
        { title: '匹配度', dataIndex: 'score', render: (s?: number) => s == null ? '—' : `${Math.round(s * 100)}%` },
        { title: '映射列', dataIndex: 'mapped', render: (m?: number) => m ?? '—' },
        { title: '行数', dataIndex: 'rows', render: (r?: number) => r ?? '—' },
        { title: '缺失/问题', render: (_, r: ReportRow) => r.error ? <Text type="danger">{r.error}</Text> : (r.missing?.length ? r.missing.join('、') : '无') },
      ]} />}
    {busy && <p role="status" className="teacher-muted">正在识别表头并映射列…</p>}
    {running && <p role="status" className="teacher-muted">正在跑正式管线复算，请稍候…</p>}
    {error && <Alert type="error" showIcon style={{ marginTop: 12 }} message={error} />}
    {done && <Alert type="success" showIcon style={{ marginTop: 12 }} message={`导入完成：${done.id}`}
      description={<Text type="secondary">{Object.entries(done.counts).map(([k, v]) => `${k} ${v}`).join(' · ')}</Text>} />}
  </Modal>;
};

export default ImportPanel;