import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Steps, Table, Tag, Typography, Upload } from 'antd';
import { InboxOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { getToken } from '../../api/authState';
import { probeLegacyBackend } from '../../api/backendCaps';
import './importPanel.css';

const { Text } = Typography;
const ALL_TABLES = ['课程要求', '题目与材料', '模拟学生', '测验节点', '作答记录'];
const ACCEPT = '.csv,.xlsx,.xlsm,.xls,.json,.zip,.txt,.md';

interface JobItem { item_id: string; name: string; origin: string; status: string; stage: string; table: string | null; score: number | null; rows: number | null; error: string | null; ms: number }
interface Job { id: string; session: string; status: string; stage: string; created_at: string; items: JobItem[]; present: string[]; eval_id: string | null; counts: Record<string, number>; error: string | null }
interface JobSummary { id: string; created_at: string; eval_id: string | null }

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  pending: { color: 'default', label: '排队' },
  running: { color: 'processing', label: '识别中' },
  ready: { color: 'green', label: '已合并' },
  failed: { color: 'red', label: '失败' },
  skipped: { color: 'default', label: '跳过' },
};

/** 统一导入入口：本地识别五张表、分批合并与历史切换。 */
const ImportPanel: React.FC = () => {
  const [job, setJob] = useState<Job | null>(null);
  const [files, setFiles] = useState<string[]>([]);
  const [history, setHistory] = useState<JobSummary[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const uploadInFlight = useRef(false);
  const sessionRef = useRef('');

  const headers = useCallback(() => ({ 'X-User-Token': getToken() || '' }), []);
  const loadHistory = async () => {
    const response = await fetch('/api/competition/import-jobs', { headers: headers() });
    if (response.ok) setHistory((await response.json()).jobs || []);
  };

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

  const startImport = async (picked: File[]) => {
    if (!picked.length || uploadInFlight.current || job?.status === 'running') return;
    uploadInFlight.current = true;
    setFiles(picked.map((file) => file.name));
    setUploading(true); setError(null);
    try {
      const form = new FormData();
      picked.forEach((file) => form.append('files', file, file.name));
      form.append('session', job?.eval_id ? '' : sessionRef.current);
      if (await probeLegacyBackend()) {
        // 旧后端没有统一导入 job 接口：降级到其既有的识别+复算两步流程，界面保持一致。
        const res = await fetch('/api/competition/import-flex', { method: 'POST', headers: headers(), body: form });
        const data = await res.json().catch(() => null);
        if (!res.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`);
        sessionRef.current = data.session;
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
      const jr = await fetch(`/api/competition/import-job/${data.job_id}`, { headers: headers() });
      if (!jr.ok) throw new Error('暂时无法读取导入进度，请重试');
      setJob(await jr.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      uploadInFlight.current = false;
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

  const startAgain = () => {
    sessionRef.current = '';
    setJob(null);
    setFiles([]);
    setError(null);
  };

  const failedItems = (job?.items || []).filter((i) => i.status === 'failed');

  const resultAction = job?.eval_id
    ? <Button type="primary" icon={<CheckCircleOutlined aria-hidden />} onClick={() => switchTo(job.eval_id as string)}>查看本次分析</Button>
    : null;
  const progress = job?.eval_id ? 3 : job?.stage === 'pipeline' || job?.present.length === 5 ? 2 : job ? 1 : 0;
  return <section className="teacher-page import-page" aria-labelledby="import-title">
    <h1 id="import-title">导入评测数据</h1>
    <div className="teacher-section">
    <Steps className="import-steps" size="small" current={progress}
      status={error || (job?.error && job.present.length === 5) ? 'error' : 'process'}
      items={[{ title: '上传材料' }, { title: '识别五张表' }, { title: '统计与分析' }, { title: '查看结果' }]} />
    {!job?.eval_id && <>
    <Upload.Dragger multiple accept={ACCEPT} showUploadList={false} disabled={uploading || job?.status === 'running'}
      beforeUpload={(file, picked) => {
        if (file === picked[0]) void startImport(picked);
        return Upload.LIST_IGNORE;
      }}>
      <p className="ant-upload-drag-icon"><InboxOutlined /></p>
      <p className="ant-upload-text">把赛题材料拖到这里，或点击选择文件</p>
      <p className="ant-upload-hint">ZIP 压缩包最大 100 MB，其他文件最大 30 MB</p>
    </Upload.Dragger>
    <div className="import-toolbar">
      <Text type="secondary">文件在本地识别。请将扫描件先整理为表格。</Text>
      {uploading && <Text type="secondary">正在上传…</Text>}
      {failedItems.length > 0 && job?.status !== 'running' && !job?.id.startsWith('legacy-') && <Button size="small" onClick={() => void retry()}>重试失败文件（{failedItems.length}）</Button>}
    </div>
    </>}
    {files.length > 0 && <p className="import-files">本次上传 · {files.join('、')}</p>}
    {job && <>
      {job.eval_id && <Alert type="success" showIcon className="import-result" message="导入完成，班级统计已算好"
        action={<div className="import-toolbar"><Button onClick={startAgain}>再导入一批</Button>{resultAction}</div>}
        description={<Text type="secondary">{`${job.counts?.students ?? '—'} 名学生 · ${job.counts?.raw_answer_rows ?? '—'} 条作答记录 · ${job.counts?.nodes ?? '—'} 次测验`}</Text>} />}
      <div className="import-present">
        {ALL_TABLES.map((t) => <Tag key={t} color={job.present.includes(t) ? 'green' : 'default'}>{job.present.includes(t) ? '✓ ' : '缺 '}{t}</Tag>)}
        <Tag color={job.status === 'succeeded' ? 'green' : job.status === 'running' ? 'processing' : 'orange'}>
          {job.eval_id ? '统计已算好' : job.status === 'running' ? job.stage === 'pipeline' ? '正在统计与分析' : '正在识别表格' : job.present.length === 5 ? '分析未完成' : '还缺表，可继续上传'}
        </Tag>
      </div>
      <Table size="small" pagination={false} rowKey="item_id" dataSource={job.items}
        columns={[
          { title: '文件', dataIndex: 'name', ellipsis: true },
          { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={STATUS_TAG[s]?.color || 'default'}>{STATUS_TAG[s]?.label || s}</Tag> },
          { title: '识别为', dataIndex: 'table', width: 110, render: (t: string | null) => t ? <Tag color="blue">{t}</Tag> : '—' },
          { title: '行数', dataIndex: 'rows', width: 70, render: (r: number | null) => r ?? '—' },
          { title: '说明', render: (_, r: JobItem) => r.error ? <Text type="danger" style={{ fontSize: 12 }}>{r.error}</Text> : <Text type="secondary" style={{ fontSize: 12 }}>{r.status === 'ready' ? '已识别' : '…'}</Text> },
        ]} />
      {job.status === 'running' && <p role="status" className="teacher-muted">正在认表、算统计，页面会自动刷新…</p>}
      {job.error && <Alert type="warning" showIcon style={{ marginTop: 10 }} message={job.error} />}
    </>}
    {error && <Alert type="error" showIcon style={{ marginTop: 10 }} message={error} />}
    <details className="import-history" onToggle={(event) => {
      if (event.currentTarget.open) void loadHistory().catch(() => setHistory([]));
    }}>
      <summary>查看导入历史</summary>
      {history.filter((entry) => entry.eval_id).map((entry) => <Button key={entry.id} type="link"
        onClick={() => switchTo(entry.eval_id as string)}>{entry.created_at.replace('T', ' ').slice(0, 19)} · 查看分析</Button>)}
    </details>
    </div>
  </section>;
};

export default ImportPanel;
