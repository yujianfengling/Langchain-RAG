import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Modal, Switch, Table, Tag, Typography, Upload } from 'antd';
import { InboxOutlined, RobotOutlined, HistoryOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { getToken } from '../../api/authState';
import { probeLegacyBackend } from '../../api/backendCaps';
import './importPanel.css';

const { Text, Paragraph } = Typography;
const ALL_TABLES = ['课程要求', '题目与材料', '模拟学生', '测验节点', '作答记录'];
const ACCEPT = '.csv,.xlsx,.xlsm,.xls,.json,.zip,.txt,.md,.pdf,.png,.jpg,.jpeg,.webp';

interface JobItem { item_id: string; name: string; origin: string; status: string; stage: string; table: string | null; score: number | null; rows: number | null; error: string | null; ms: number }
interface Job { id: string; session: string; status: string; stage: string; created_at: string; items: JobItem[]; present: string[]; eval_id: string | null; counts: Record<string, number>; error: string | null }
interface JobSummary { id: string; session: string; status: string; eval_id: string | null; counts: Record<string, number>; created_at: string; error: string | null; items_total: number; items_ready: number; items_failed: number }

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  pending: { color: 'default', label: '排队' },
  running: { color: 'processing', label: '识别中' },
  ready: { color: 'green', label: '已合并' },
  failed: { color: 'red', label: '失败' },
  skipped: { color: 'default', label: '跳过' },
};

/** 统一导入入口：单拖拽区多格式、自动识别五表、DeepSeek 兜底、job 队列与历史切换。 */
const ImportPanel: React.FC<{ open: boolean; onClose: () => void }> = ({ open, onClose }) => {
  const [job, setJob] = useState<Job | null>(null);
  const [history, setHistory] = useState<JobSummary[]>([]);
  const [aiAssist, setAiAssist] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sentUids = useRef<Set<string>>(new Set());
  const sessionRef = useRef<string>(localStorage.getItem('competition.importSession') || '');

  const headers = useCallback(() => ({ 'X-User-Token': getToken() || '' }), []);

  const loadHistory = useCallback(async () => {
    const res = await fetch('/api/competition/import-jobs', { headers: headers() });
    if (res.ok) setHistory((await res.json()).jobs || []);
  }, [headers]);

  useEffect(() => { if (open) { void loadHistory(); setJob(null); setError(null); sentUids.current.clear(); } }, [open, loadHistory]);

  const jobId = job?.id;
  const jobStatus = job?.status;
  useEffect(() => {
    if (!jobId || jobStatus !== 'running' || jobId.startsWith('legacy-')) return;
    const t = setInterval(async () => {
      const res = await fetch(`/api/competition/import-job/${jobId}`, { headers: headers() });
      if (res.ok) setJob(await res.json());
    }, 1500);
    return () => clearInterval(t);
  }, [jobId, jobStatus, headers]);

  const startImport = async (picked: { uid: string; name: string; originFileObj?: File }[]) => {
    const fresh = picked.filter((f) => f.originFileObj && !sentUids.current.has(f.uid));
    if (!fresh.length) return;
    fresh.forEach((f) => sentUids.current.add(f.uid));
    setUploading(true); setError(null);
    try {
      const form = new FormData();
      fresh.forEach((f) => form.append('files', f.originFileObj as File, f.name));
      form.append('session', sessionRef.current);
      form.append('ai_assist', aiAssist ? '1' : '0');
      if (await probeLegacyBackend()) {
        // 旧后端没有统一导入 job 接口：降级到其既有的识别+复算两步流程，界面保持一致。
        const res = await fetch('/api/competition/import-flex', { method: 'POST', headers: headers(), body: form });
        const data = await res.json().catch(() => null);
        if (!res.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`);
        sessionRef.current = data.session;
        localStorage.setItem('competition.importSession', data.session);
        const present: string[] = data.present || [];
        const items: JobItem[] = (data.report || []).map((r: { file: string; table: string | null; score?: number; rows?: number; error?: string }, i: number) => ({
          item_id: `legacy-${i}`, name: r.file, origin: r.file,
          status: r.error ? 'failed' : 'ready', stage: '', table: r.table || null,
          score: r.score ?? null, rows: r.rows ?? null, error: r.error || null, ms: 0,
        }));
        let legacyJob: Job = {
          id: `legacy-${Date.now()}`, session: data.session,
          status: data.complete ? 'running' : 'partial', stage: 'pipeline',
          created_at: new Date().toISOString(), items, present, eval_id: null, counts: {},
          error: data.complete ? null : '还缺表：' + ALL_TABLES.filter((t) => !present.includes(t)).join('、'),
        };
        if (data.complete) {
          const rr = await fetch('/api/competition/import-run', {
            method: 'POST', headers: { 'Content-Type': 'application/json', ...headers() },
            body: JSON.stringify({ session: data.session }),
          });
          const rd = await rr.json().catch(() => null);
          if (!rr.ok) throw new Error(typeof rd?.detail === 'string' ? rd.detail : `HTTP ${rr.status}`);
          legacyJob = { ...legacyJob, status: 'succeeded', stage: 'done', eval_id: rd.id, counts: rd.counts || {} };
        }
        setJob(legacyJob);
        return;
      }
      const res = await fetch('/api/competition/import-unified', { method: 'POST', headers: headers(), body: form });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`);
      sessionRef.current = data.session;
      localStorage.setItem('competition.importSession', data.session);
      const jr = await fetch(`/api/competition/import-job/${data.job_id}`, { headers: headers() });
      setJob(await jr.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  const retry = async () => {
    if (!job) return;
    const res = await fetch(`/api/competition/import-job/${job.id}/retry`, { method: 'POST', headers: headers() });
    if (res.ok) setJob(await res.json());
    else setError((await res.json().catch(() => null))?.detail || '重试失败');
  };

  const switchTo = (evalId: string) => {
    localStorage.setItem('competition.importedId', evalId);
    window.location.assign('/class-analytics?source=imported');
  };

  const failedItems = (job?.items || []).filter((i) => i.status === 'failed');

  return <Modal open={open} onCancel={onClose} width={920} title="导入评测数据"
    footer={[
      <Button key="cancel" onClick={onClose}>关闭</Button>,
      job?.eval_id
        ? <Button key="go" type="primary" icon={<CheckCircleOutlined aria-hidden />} onClick={() => switchTo(job.eval_id as string)}>切换到本次导入数据</Button>
        : null,
    ]}>
    <Paragraph type="secondary" style={{ marginBottom: 10 }}>
      把文件丢进来就行：Excel、CSV、JSON、ZIP 压缩包，或表格的照片、PDF 都可以。
      系统会自动把内容归到课程要求、题目与材料、学生、测验节点、作答记录五张表，凑齐后自动算出班级统计；认不出的地方会请大模型帮忙认。
    </Paragraph>
    <Upload.Dragger multiple accept={ACCEPT} fileList={[]} showUploadList={false}
      beforeUpload={() => false}
      onChange={({ fileList }) => void startImport(fileList as unknown as { uid: string; name: string; originFileObj?: File }[])}>
      <p className="ant-upload-drag-icon"><InboxOutlined /></p>
      <p className="ant-upload-text">点击或拖拽文件到此处（可多选）</p>
      <p className="ant-upload-hint">不用改文件名，系统按内容认。</p>
    </Upload.Dragger>
    <div className="import-toolbar">
      <span className="import-ai"><Switch size="small" checked={aiAssist} onChange={setAiAssist} /> <RobotOutlined /> 认不出时请大模型帮忙</span>
      {uploading && <Text type="secondary">正在上传…</Text>}
      {failedItems.length > 0 && job?.status !== 'running' && !job?.id.startsWith('legacy-') && <Button size="small" onClick={() => void retry()}>重试失败文件（{failedItems.length}）</Button>}
    </div>
    {job && <>
      <div className="import-present">
        {ALL_TABLES.map((t) => <Tag key={t} color={job.present.includes(t) ? 'green' : 'default'}>{job.present.includes(t) ? '✓ ' : '缺 '}{t}</Tag>)}
        <Tag color={job.status === 'succeeded' ? 'green' : job.status === 'running' ? 'processing' : 'orange'}>
          {job.status === 'succeeded' ? '统计已算好' : job.status === 'running' ? `正在${{ parse: '读文件', recognize: '认表', llm: '请大模型帮忙', vision: '认照片', merge: '合并', pipeline: '算统计' }[job.stage] || '处理'}` : '还缺表，继续丢文件'}
        </Tag>
      </div>
      <Table size="small" pagination={false} rowKey="item_id" dataSource={job.items}
        columns={[
          { title: '文件', dataIndex: 'name', ellipsis: true },
          { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={STATUS_TAG[s]?.color || 'default'}>{STATUS_TAG[s]?.label || s}</Tag> },
          { title: '识别为', dataIndex: 'table', width: 110, render: (t: string | null) => t ? <Tag color="blue">{t}</Tag> : '—' },
          { title: '行数', dataIndex: 'rows', width: 70, render: (r: number | null) => r ?? '—' },
          { title: '说明', render: (_, r: JobItem) => r.error ? <Text type="danger" style={{ fontSize: 12 }}>{r.error}</Text> : <Text type="secondary" style={{ fontSize: 12 }}>{r.stage === 'llm' ? '大模型帮忙认的' : r.stage === 'vision' ? '从照片认的' : r.status === 'ready' ? '自动认出' : '…'}</Text> },
        ]} />
      {job.status === 'running' && <p role="status" className="teacher-muted">正在认表、算统计，页面会自动刷新…</p>}
      {job.error && <Alert type="warning" showIcon style={{ marginTop: 10 }} message={job.error} />}
      {job.eval_id && <Alert type="success" showIcon style={{ marginTop: 10 }} message="导入完成，班级统计已算好"
        description={<Text type="secondary">{`${job.counts?.students ?? '—'} 名学生 · ${job.counts?.raw_answer_rows ?? '—'} 条作答 · ${job.counts?.nodes ?? '—'} 次测验`}</Text>} />}
    </>}
    {error && <Alert type="error" showIcon style={{ marginTop: 10 }} message={error} />}
    {history.length > 0 && <div className="import-history">
      <div className="import-history-head"><HistoryOutlined /> 本机导入历史</div>
      <Table size="small" pagination={false} rowKey="id" dataSource={history}
        columns={[
          { title: '时间', dataIndex: 'created_at', width: 160, render: (t: string) => <Text type="secondary" style={{ fontSize: 12 }}>{(t || '').replace('T', ' ').slice(5, 16)}</Text> },
          { title: '结果', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={s === 'succeeded' ? 'green' : 'orange'}>{s === 'succeeded' ? '成功' : '部分'}</Tag> },
          { title: '文件', width: 90, render: (_, r: JobSummary) => `${r.items_ready}/${r.items_total}` },
          { title: '规模', render: (_, r: JobSummary) => <Text type="secondary" style={{ fontSize: 12 }}>{r.counts ? `${r.counts.students ?? '—'} 学生 · ${r.counts.raw_answer_rows ?? '—'} 作答` : '—'}</Text> },
          { title: '', width: 90, render: (_, r: JobSummary) => r.eval_id ? <Button size="small" type="link" onClick={() => switchTo(r.eval_id as string)}>切换</Button> : null },
        ]} />
    </div>}
  </Modal>;
};

export default ImportPanel;