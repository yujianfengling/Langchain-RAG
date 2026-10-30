import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Popconfirm, Progress, Tag, Typography, message } from 'antd';
import {
  DownOutlined, FileTextOutlined, InboxOutlined, PauseCircleOutlined, PlayCircleOutlined,
  ReloadOutlined, RightOutlined,
} from '@ant-design/icons';
import {
  ImportCapabilities, ImportItem, ImportItemStatus, ImportItemTimings, ImportJob, ImportJobStatus,
  cancelImport, capabilityUnavailableReason, continueImport, effectiveJobStatus, formatBytes,
  getImportCapabilities, getImportJob, hasRetryableFailed, isJobPaused, isJobRunning, isTerminalStatus,
  jobCanCancel, jobCanPause, jobCanResume, jobNeedsContinue,
  listImports, pauseImport, resolveLimits, resolveTotals, resumeImport, retryImport, startImport,
  validateSelection,
} from '../../api/import';
import './upload.css';

const { Text } = Typography;

export interface UploadDropzoneProps {
  subjectId: string;
  /** 是否有写权限；false 时只渲染只读提示，不渲染可操作上传框 */
  writable: boolean;
  /** 任务进入终态时回调一次 */
  onFinished?: (job: ImportJob) => void;
  /** 点击「开始伴学」；未提供时回退跳 /student?subject=<sid> */
  onOpenSubject?: (subjectId: string) => void;
  /** 学科已有资料时把拖拽区收窄为紧凑样式 */
  compactWhenHasFiles?: boolean;
}

type RejectedNote = { filename: string; reason: string };
type UploadError = { message: string; files: File[] };
/** 队列主操作互斥：同一时刻只允许一个动作在途 */
type JobAction = 'pause' | 'resume' | 'continue' | 'cancel';

interface UploadState {
  active: boolean;
  /** 真实 onUploadProgress 百分比；-1 表示 total 未知（不显示百分比） */
  pct: number;
  count: number;
}

const ITEM_STATUS_META: Record<ImportItemStatus, { label: string; color: string }> = {
  pending: { label: '排队中', color: 'default' },
  parsing: { label: '解析中', color: 'processing' },
  indexing: { label: '建库中', color: 'processing' },
  ready: { label: '可学习', color: 'success' },
  failed: { label: '失败', color: 'error' },
  skipped: { label: '已跳过', color: 'warning' },
  unprocessed: { label: '未处理', color: 'warning' },
};

/** 阶段中文：后端 stage 更细（extracting/parsing/indexing/ready …），未知值原样展示 */
const STAGE_LABEL: Record<string, string> = {
  pending: '排队中',
  uploading: '上传中',
  extracting: '解包中',
  parsing: '解析中',
  indexing: '建库中',
  ready: '已入库',
  failed: '失败',
  skipped: '已跳过',
  unprocessed: '未处理',
};

const stageText = (stage?: string | null): string => (stage ? (STAGE_LABEL[stage] || stage) : '');

const formatMs = (ms: number): string =>
  (ms < 1000 ? Math.round(ms) + 'ms' : (ms / 1000).toFixed(1) + 's');

/** 耗时明细：只用后端真实字段，缺失不编造 */
const formatTimings = (t?: ImportItemTimings): string => {
  if (!t) return '';
  const parts: string[] = [];
  if (typeof t.parse_ms === 'number') parts.push('解析 ' + formatMs(t.parse_ms));
  if (typeof t.vision_ms === 'number') parts.push('识图 ' + formatMs(t.vision_ms));
  if (typeof t.vision_calls === 'number' && t.vision_calls > 0) {
    parts.push('识图 ' + t.vision_calls + ' 次'
      + (typeof t.vision_cached === 'number' && t.vision_cached > 0 ? '（缓存 ' + t.vision_cached + '）' : ''));
  }
  if (typeof t.images_total === 'number' && t.images_total > 0) {
    parts.push('图片 ' + t.images_total + ' 张');
  }
  return parts.join(' · ');
};

/** 条目状态：只有 status=ready 才写"可学习"；indexing/建库中单独标示 */
const itemStatusMeta = (item: ImportItem, terminal: boolean): { label: string; color: string } => {
  if (item.status === 'indexing' || item.stage === 'indexing') {
    return { label: '建库中', color: 'processing' };
  }
  if (item.status === 'ready') return ITEM_STATUS_META.ready;
  if (item.status === 'unprocessed' || (item.status === 'pending' && terminal)) {
    return { label: '未处理', color: 'warning' };
  }
  return ITEM_STATUS_META[item.status] || { label: item.status, color: 'default' };
};

const JOB_STATUS_META: Record<ImportJobStatus, { label: string; color: string }> = {
  queued: { label: '排队中', color: 'default' },
  running: { label: '处理中', color: 'processing' },
  succeeded: { label: '已完成', color: 'success' },
  partial: { label: '部分完成', color: 'warning' },
  failed: { label: '失败', color: 'error' },
  cancelled: { label: '已取消', color: 'default' },
};

/** 能力 kind → 简短中文词（用于次文案，动态生成，不写死格式清单） */
const KIND_WORD: Record<string, string> = {
  presentation: 'PPT',
  pdf: 'PDF',
  document: '文档',
  doc: '文档',
  spreadsheet: '表格',
  excel: '表格',
  image: '图片',
  text: '文本',
  markdown: '文本',
  ebook: '电子书',
  archive: '压缩包',
};

const supportedWords = (caps: ImportCapabilities | null): string[] => {
  if (!caps) return [];
  const words: string[] = [];
  const seen = new Set<string>();
  (caps.formats || []).filter((f) => f.available).forEach((f) => {
    const word = KIND_WORD[f.kind] || (f.ext ? f.ext.replace('.', '').toUpperCase() : '');
    if (word && !seen.has(word)) { seen.add(word); words.push(word); }
  });
  if ((caps.archives || []).some((a) => a.available) && !seen.has('压缩包')) {
    words.push('压缩包');
  }
  return words;
};

const joinWords = (words: string[]): string => {
  if (words.length === 0) return '';
  if (words.length === 1) return words[0];
  return words.slice(0, -1).join('、') + '和' + words[words.length - 1];
};

const describeError = (e: unknown, fallback = '操作失败，请重试'): string => {
  const err = e as {
    response?: { data?: { message?: string; detail?: string } };
    message?: string;
  };
  const detail = err?.response?.data?.detail;
  return err?.response?.data?.message
    || (typeof detail === 'string' ? detail : '')
    || err?.message
    || fallback;
};

const UploadDropzone: React.FC<UploadDropzoneProps> = ({
  subjectId, writable, onFinished, onOpenSubject, compactWhenHasFiles,
}) => {
  const navigate = useNavigate();
  const [caps, setCaps] = useState<ImportCapabilities | null>(null);
  const [capsError, setCapsError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [upload, setUpload] = useState<UploadState>({ active: false, pct: 0, count: 0 });
  const [job, setJob] = useState<ImportJob | null>(null);
  const [localRejected, setLocalRejected] = useState<RejectedNote[]>([]);
  const [backendRejected, setBackendRejected] = useState<RejectedNote[]>([]);
  const [pollError, setPollError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<UploadError | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [busy, setBusy] = useState<JobAction | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const finishedRef = useRef<string | null>(null);
  const onFinishedRef = useRef(onFinished);

  useEffect(() => { onFinishedRef.current = onFinished; }, [onFinished]);

  // 能力清单：进页面拉一次
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const c = await getImportCapabilities();
        if (alive) { setCaps(c); setCapsError(null); }
      } catch (e) {
        if (alive) setCapsError(describeError(e, '无法获取支持的格式'));
      }
    })();
    return () => { alive = false; };
  }, []);

  // 阻止浏览器把拖入窗口的文件直接打开（只拦文件拖拽，不影响页面内其它拖拽）
  useEffect(() => {
    const prevent = (e: DragEvent) => {
      if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) {
        e.preventDefault();
      }
    };
    window.addEventListener('dragover', prevent);
    window.addEventListener('drop', prevent);
    return () => {
      window.removeEventListener('dragover', prevent);
      window.removeEventListener('drop', prevent);
    };
  }, []);

  // 切换学科 / 刷新恢复：重置状态并接管最近一个未完成任务
  useEffect(() => {
    let alive = true;
    setJob(null);
    setLocalRejected([]);
    setBackendRejected([]);
    setPollError(null);
    setUploadError(null);
    setDetailsOpen(false);
    finishedRef.current = null;
    setUpload({ active: false, pct: 0, count: 0 });
    (async () => {
      try {
        const list = await listImports(subjectId, 5);
        // 恢复最近任务：进行中的任务，以及"部分完成/仍有未处理项"的任务——
        // 刷新或服务重启后必须仍能自动出现「继续处理剩余资料」入口。
        // paused 任务状态为 partial（终态），必须显式匹配，刷新后才能恢复出「继续」入口
        const activeJob = list.find((j) => !isTerminalStatus(j.status) || isJobPaused(j))
          || list.find((j) => j.status === 'partial' || jobNeedsContinue(j) || isJobPaused(j));
        if (alive && activeJob) setJob(activeJob);
      } catch {
        // 没有历史记录时后端可能报错，不打扰用户
      }
    })();
    return () => { alive = false; };
  }, [subjectId]);

  // 轮询：1.5s，终态或 paused 停止，卸载清理（paused 时绝不每隔几秒打服务端）
  useEffect(() => {
    if (!job || isTerminalStatus(job.status) || isJobPaused(job)) return;
    const jobId = job.job_id;
    const timer = window.setInterval(async () => {
      try {
        const next = await getImportJob(jobId);
        setJob(next);
        setPollError(null);
      } catch (e) {
        setPollError(describeError(e, '进度刷新失败，仍在重试'));
      }
    }, 1500);
    return () => { window.clearInterval(timer); };
  }, [job]);

  // 终态回调（同一 job 只回调一次）
  useEffect(() => {
    if (job && isTerminalStatus(job.status) && finishedRef.current !== job.job_id) {
      finishedRef.current = job.job_id;
      onFinishedRef.current?.(job);
    }
  }, [job]);

  const ensureCaps = useCallback(async (): Promise<ImportCapabilities | null> => {
    if (caps) return caps;
    try {
      const c = await getImportCapabilities();
      setCaps(c);
      setCapsError(null);
      return c;
    } catch (e) {
      setCapsError(describeError(e, '无法获取支持的格式'));
      message.error('无法获取支持的格式，请稍后重试');
      return null;
    }
  }, [caps]);

  const submit = useCallback(async (incoming: File[]) => {
    if (!writable || incoming.length === 0 || upload.active) return;
    const c = await ensureCaps();
    if (!c) return;

    // 统一走 api 层的提交前校验：格式（含不可用格式/需要转换组件的 note）、
    // 按类型单文件上限（压缩包 max_archive_mb / 其它 max_file_mb）、
    // 文件数 max_files、本批总量（压缩包按自身大小）。被拒项带具体原因。
    const { accepted, rejected } = validateSelection(incoming, c);
    const notes: RejectedNote[] = rejected.map((r) => ({ filename: r.filename, reason: r.reason }));

    setLocalRejected(notes);
    if (accepted.length === 0) {
      if (notes.length > 0) message.warning('所选文件都不符合导入要求');
      return;
    }

    setJob(null);
    setBackendRejected([]);
    setUploadError(null);
    setDetailsOpen(false);
    finishedRef.current = null;
    setUpload({ active: true, pct: 0, count: accepted.length });

    try {
      const res = await startImport(subjectId, accepted, (pct) => {
        setUpload((u) => ({ ...u, pct }));
      });
      setBackendRejected((res.rejected || []).map((r) => ({ filename: r.filename, reason: r.reason })));
      setUpload({ active: false, pct: 100, count: 0 });
      if (res.job_id) {
        const initial = await getImportJob(res.job_id);
        setJob(initial);
      }
    } catch (e) {
      setUpload({ active: false, pct: 0, count: 0 });
      const msg = describeError(e, '导入失败，请重试');
      // 保留这批文件，给出可点的重试入口（不静默丢弃）
      setUploadError({ message: msg, files: accepted });
      message.error(msg);
    }
  }, [writable, upload.active, ensureCaps, subjectId]);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (files.length > 0) void submit(files);
  };

  const onDragEnter = (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    dragDepth.current += 1;
    if (writable) setDragActive(true);
  };
  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = writable ? 'copy' : 'none';
  };
  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragActive(false);
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    dragDepth.current = 0;
    setDragActive(false);
    if (!writable) return;
    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length > 0) void submit(files);
  };

  const handleRetry = async (jobId: string) => {
    try {
      const res = await retryImport(jobId);
      finishedRef.current = null;
      setLocalRejected([]);
      setBackendRejected([]);
      if (res.job_id) setJob(await getImportJob(res.job_id));
    } catch (e) {
      message.error(describeError(e, '重试失败，请稍后重试'));
    }
  };

  /** 暂停：请求后仍在跑，要等下一个批边界才真正 paused，轮询会继续到那一刻 */
  const handlePause = async (jobId: string) => {
    setBusy('pause');
    try {
      await pauseImport(jobId);
      setJob(await getImportJob(jobId));
      message.info('已请求暂停，将在当前批次结束后停止');
    } catch (e) {
      message.error(describeError(e, '暂停失败，请稍后重试'));
    } finally {
      setBusy(null);
    }
  };

  /** 继续已暂停的任务：同一 job 从剩余成员自动分批，已完成的资料复用 */
  const handleResume = async (jobId: string) => {
    setBusy('resume');
    try {
      const res = await resumeImport(jobId);
      finishedRef.current = null;
      setLocalRejected([]);
      setBackendRejected([]);
      setUploadError(null);
      const nextId = res.job_id || jobId;
      setJob(await getImportJob(nextId));
    } catch (e) {
      message.error(describeError(e, '继续失败，请稍后重试'));
    } finally {
      setBusy(null);
    }
  };

  /** 兜底：终态且仍有未处理/可重试失败时，从保留的原包继续（正常自动分批下不会再出现） */
  const handleContinue = async (jobId: string) => {
    setBusy('continue');
    try {
      const res = await continueImport(jobId);
      finishedRef.current = null;
      setLocalRejected([]);
      setBackendRejected([]);
      setUploadError(null);
      if (res.job_id) setJob(await getImportJob(res.job_id));
    } catch (e) {
      message.error(describeError(e, '继续处理失败，请稍后重试'));
    } finally {
      setBusy(null);
    }
  };

  const handleCancel = async (jobId: string) => {
    setBusy('cancel');
    try {
      await cancelImport(jobId);
      setJob(await getImportJob(jobId));
    } catch (e) {
      message.error(describeError(e, '取消失败，请稍后重试'));
    } finally {
      setBusy(null);
    }
  };

  const handleOpenSubject = () => {
    if (onOpenSubject) onOpenSubject(subjectId);
    else navigate('/student?subject=' + encodeURIComponent(subjectId));
  };

  const words = useMemo(() => supportedWords(caps), [caps]);
  const subText = words.length > 0
    ? '支持 ' + joinWords(words) + '，可多选或整包拖入'
    : (caps ? '暂未检测到可导入格式，请查看下方说明' : '正在获取支持的格式…');

  // 限制与格式清单全部来自 capabilities，不写死数字
  const limits = useMemo(() => resolveLimits(caps), [caps]);
  const allFormats = useMemo(
    () => (caps ? [...(caps.formats || []), ...(caps.archives || [])] : []),
    [caps],
  );
  const availableFormats = useMemo(() => allFormats.filter((f) => f.available), [allFormats]);
  const unavailableFormats = useMemo(() => allFormats.filter((f) => !f.available), [allFormats]);

  const items = job?.items || [];
  const terminal = job ? isTerminalStatus(job.status) : false;
  const compact = Boolean(compactWhenHasFiles) || items.length > 0;
  const rejected = [...localRejected, ...backendRejected];
  const totals = resolveTotals(job);
  // 诚实状态：有未处理/失败项时即使后端误报 succeeded 也按 partial 展示
  const effectiveStatus = effectiveJobStatus(job);
  const showItems = !terminal || detailsOpen;
  // paused 覆盖 tag 文案；它是"停在批边界"而非终态完成
  const paused = isJobPaused(job);
  const running = isJobRunning(job);
  const jobMeta = paused
    ? { label: '已暂停', color: 'warning' }
    : (effectiveStatus ? JOB_STATUS_META[effectiveStatus] : null);
  const retryableFailed = hasRetryableFailed(job);
  const canPause = jobCanPause(job);
  const canResume = jobCanResume(job);
  const canCancel = jobCanCancel(job);
  // jobNeedsContinue 已排除 paused：paused 的正确入口是「继续(resume)」
  const canContinue = jobNeedsContinue(job);
  const processed = totals.ready + totals.failed + totals.skipped;
  const processedPct = totals.total > 0 ? Math.round((processed / totals.total) * 100) : 0;

  const renderItem = (item: ImportItem) => {
    const meta = itemStatusMeta(item, terminal);
    const realProgress = typeof item.progress === 'number' ? Math.round(item.progress * 100) : null;
    const timings = formatTimings(item.timings);
    const stage = stageText(item.stage);
    const subParts: string[] = [];
    if (item.origin_archive) subParts.push('来自 ' + item.origin_archive);
    if (item.size) subParts.push(formatBytes(item.size));
    const isUnprocessed = item.status === 'unprocessed' || (item.status === 'pending' && terminal);
    return (
      <div className="udz-row" key={item.item_id}>
        <FileTextOutlined className="udz-row-icon" />
        <div className="udz-row-main">
          <div className="udz-row-name" title={item.display_name}>{item.display_name}</div>
          <div
            className="udz-row-sub"
            title={[subParts.join(' · '), stage ? '阶段 ' + stage : ''].filter(Boolean).join(' · ')}
          >
            {subParts.join(' · ')}
            {subParts.length > 0 && stage ? ' · ' : ''}
            {stage ? '阶段 ' + stage : ''}
          </div>
          {item.status === 'failed' && (
            <div className="udz-row-msg">
              {item.message || '导入失败'}{item.error_type ? '（' + item.error_type + '）' : ''}
              {item.retryable && (
                <Button
                  type="link" size="small" className="udz-row-retry"
                  onClick={() => job && void handleRetry(job.job_id)}
                >
                  重试
                </Button>
              )}
            </div>
          )}
          {item.status === 'skipped' && item.message && (
            <div className="udz-row-msg udz-row-msg--muted">{item.message}</div>
          )}
          {isUnprocessed && (
            <div className="udz-row-msg udz-row-msg--muted">
              未处理{paused ? '（已暂停，等待继续）' : '（后台自动分批中）'}
              {item.message && item.message !== '未处理' ? '：' + item.message : ''}
            </div>
          )}
          {timings && <div className="udz-row-timing">耗时：{timings}</div>}
          {(item.status === 'parsing' || item.status === 'indexing') && realProgress !== null && (
            <Progress percent={realProgress} size="small" showInfo={false} strokeColor="var(--color-primary)" />
          )}
        </div>
        <Tag color={meta.color} className="udz-row-state">{meta.label}</Tag>
      </div>
    );
  };

  return (
    <section className="udz-root">
      {writable ? (
        <div
          className={
            'udz-drop'
            + (dragActive ? ' udz-drop--active' : '')
            + (compact ? ' udz-drop--compact' : '')
          }
          role="button"
          tabIndex={0}
          aria-label="拖拽或点击选择要导入的课件与资料包"
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current?.click(); }
          }}
          onDragEnter={onDragEnter}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={caps?.accept}
            className="udz-input"
            onChange={handleInputChange}
            onClick={(e) => e.stopPropagation()}
          />
          <InboxOutlined className="udz-icon" />
          <div className="udz-title">把课件或资料包拖到这里</div>
          <div className="udz-sub">{subText}</div>
          {dragActive && <div className="udz-drop-hint">松开即可导入</div>}
        </div>
      ) : (
        <div className="udz-drop udz-drop--readonly">
          <InboxOutlined className="udz-icon" />
          <div className="udz-title">这里是只读课程资料</div>
          <div className="udz-sub">课程资料由授课教师维护，你可以阅读并用于伴学，但不能导入。</div>
        </div>
      )}

      {capsError && (
        <Alert
          className="udz-caps-error"
          type="warning" showIcon
          message={capsError}
          action={<Button size="small" onClick={() => { setCapsError(null); void ensureCaps(); }}>重试</Button>}
        />
      )}

      {/* 上传阶段：真实的 onUploadProgress，独立于解析/建库 */}
      {upload.active && (
        <div className="udz-uploading">
          <div className="udz-row-name">
            正在上传 {upload.count} 个文件
            {upload.pct >= 0 ? '（' + upload.pct + '%）' : '…'}
          </div>
          {upload.pct >= 0 && (
            <Progress percent={upload.pct} size="small" showInfo={false} strokeColor="var(--color-primary)" />
          )}
        </div>
      )}

      {/* 上传失败：保留本批文件，给出可点的重试入口 */}
      {uploadError && (
        <div className="udz-upload-error">
          <span className="udz-upload-error-msg">{uploadError.message}</span>
          <Button
            size="small" type="primary" ghost icon={<ReloadOutlined />}
            onClick={() => void submit(uploadError.files)}
          >
            重试上传
          </Button>
        </div>
      )}

      {pollError && <div className="udz-poll-error">{pollError}</div>}

      {/* 队列面板 */}
      {job && (
        <div className="udz-queue">
          <div className="udz-queue-head">
            <Text strong style={{ fontSize: 13 }}>导入队列</Text>
            {jobMeta && <Tag color={jobMeta.color}>{jobMeta.label}</Tag>}
            <span className="udz-queue-progress">
              已可学习 {totals.ready} / 共 {totals.total}
            </span>
            {canCancel && (
              <Popconfirm
                title="取消本次导入？"
                description="已完成的资料会保留；未处理的部分需要重新导入。"
                okText="取消导入" cancelText="再等等"
                okButtonProps={{ danger: true }}
                onConfirm={() => void handleCancel(job.job_id)}
              >
                <Button
                  type="link" size="small" className="udz-cancel"
                  loading={busy === 'cancel'}
                >
                  取消
                </Button>
              </Popconfirm>
            )}
            {terminal && items.length > 0 && (
              <Button
                type="link" size="small" className="udz-details-toggle"
                icon={detailsOpen ? <DownOutlined /> : <RightOutlined />}
                onClick={() => setDetailsOpen((v) => !v)}
              >
                {detailsOpen ? '收起详情' : '查看详情'}
              </Button>
            )}
          </div>

          {/* 真实计数：有效资料 = materials（缺失回退 total），目录/压缩包为 0 时不显示 */}
          <div className="udz-meta">
            <span>有效资料 {totals.materials} 份</span>
            {totals.directories > 0 && <span>目录 {totals.directories} 个</span>}
            {totals.containers > 0 && <span>压缩包 {totals.containers} 个</span>}
          </div>

          {/* 自动分批的真实行为说明：服务端会一直跑到终态，用户可关闭页面 */}
          {running && (
            <div className="udz-autobatch">
              {job.pause_requested
                ? '已请求暂停，当前批次结束后停止（不会自动继续）。'
                : '后台自动处理中，可关闭页面；服务端会一直处理到完成。'}
            </div>
          )}
          {paused && (
            <div className="udz-paused-note">
              任务已暂停，不会自动继续。点击「继续」会在同一任务上接着处理，已完成的资料会复用。
            </div>
          )}

          {/* 计数卡片：总数 / 可学习 / 处理中 / 失败 / 未处理 / 已跳过。
              为 0 的隐藏，但 unprocessed 与 failed 不为 0 时必须可见。 */}
          <div className="udz-counts">
            <div className="udz-count">
              <span className="udz-count-value">{totals.total}</span>
              <span className="udz-count-label">总数</span>
            </div>
            <div className="udz-count udz-count--ok">
              <span className="udz-count-value">{totals.ready}</span>
              <span className="udz-count-label">可学习</span>
            </div>
            {totals.processing > 0 && (
              <div className="udz-count udz-count--processing">
                <span className="udz-count-value">{totals.processing}</span>
                <span className="udz-count-label">处理中</span>
              </div>
            )}
            {totals.failed > 0 && (
              <div className="udz-count udz-count--error">
                <span className="udz-count-value">{totals.failed}</span>
                <span className="udz-count-label">失败</span>
              </div>
            )}
            {totals.unprocessed > 0 && (
              <div className="udz-count udz-count--warn">
                <span className="udz-count-value">{totals.unprocessed}</span>
                <span className="udz-count-label">未处理</span>
              </div>
            )}
            {totals.skipped > 0 && (
              <div className="udz-count udz-count--muted">
                <span className="udz-count-value">{totals.skipped}</span>
                <span className="udz-count-label">已跳过</span>
              </div>
            )}
          </div>

          {totals.total > 0 && (
            <div className="udz-progress">
              <Progress
                percent={processedPct} size="small" showInfo={false}
                strokeColor={effectiveStatus === 'partial' ? '#f59e0b' : 'var(--color-primary)'}
              />
              <span className="udz-progress-text">
                已处理 {processed}/{totals.total} 份
                {/* 2026-10-30 修复：此前只报「已处理 + 未处理」，把「处理中」漏掉。
                    实测总数 3 / 处理中 1 / 未处理 2 时，文案读起来是「0/3，未处理 2」，
                    0+2≠3，用户会以为计数错了。三个数必须都出现。 */}
                {totals.processing > 0 ? '，处理中 ' + totals.processing + ' 份' : ''}
                {totals.unprocessed > 0 ? '，未处理 ' + totals.unprocessed + ' 份' : ''}
              </span>
            </div>
          )}

          {/* 前端自检：total 必须等于各计数之和，不一致时明确提示（不静默） */}
          {!totals.consistent && (
            <div className="udz-invariant">
              计数自检异常：总数 {totals.total} 与各项之和 {totals.sum} 不一致（按后端 total 展示）
            </div>
          )}

          {/* 主操作：运行中「暂停」，暂停后「继续」（取消在头部，需二次确认） */}
          {(canPause || canResume) && (
            <div className="udz-controls">
              {canResume && (
                <Button
                  type="primary" icon={<PlayCircleOutlined />} loading={busy === 'resume'}
                  onClick={() => void handleResume(job.job_id)}
                >
                  继续
                </Button>
              )}
              {canPause && (
                <Button
                  type="primary" icon={<PauseCircleOutlined />}
                  loading={busy === 'pause'}
                  disabled={Boolean(job.pause_requested) || (busy !== null && busy !== 'pause')}
                  onClick={() => void handlePause(job.job_id)}
                >
                  {job.pause_requested ? '暂停中…' : '暂停'}
                </Button>
              )}
            </div>
          )}

          {/* 兜底：终态且仍有未处理/可重试失败时，显著位置提供「继续处理」（paused 已排除） */}
          {canContinue && (
            <div className="udz-continue">
              <div className="udz-continue-text">
                <div className="udz-continue-title">
                  {totals.unprocessed > 0
                    ? '还有 ' + totals.unprocessed + ' 份资料未处理'
                    : '还有失败项可继续处理'}
                  {totals.unprocessed > 0 && retryableFailed ? '，另有可重试的失败项' : ''}
                </div>
                <div className="udz-continue-hint">已完成的资料会复用，不会重复导入。</div>
              </div>
              <Button
                type="primary" icon={<ReloadOutlined />} loading={busy === 'continue'}
                className="udz-continue-btn"
                onClick={() => void handleContinue(job.job_id)}
              >
                继续处理剩余资料
              </Button>
            </div>
          )}

          {terminal && (
            <div className="udz-summary">
              <div className="udz-summary-text">
                {job.summary_text
                  || ('共 ' + totals.total + ' 份：' + totals.ready + ' 可学习，'
                    + totals.failed + ' 失败，' + totals.unprocessed + ' 未处理，'
                    + totals.skipped + ' 跳过')}
              </div>
              <div className="udz-actions">
                {job.can_retry && (
                  <Button
                    size="small" icon={<ReloadOutlined />}
                    onClick={() => void handleRetry(job.job_id)}
                  >
                    重试失败项
                  </Button>
                )}
                {totals.ready > 0 && (
                  <Button type="primary" size="small" onClick={handleOpenSubject}>
                    开始伴学
                  </Button>
                )}
              </div>
            </div>
          )}

          {showItems && items.length > 0 && (
            <div className="udz-rows">{items.map(renderItem)}</div>
          )}

          {(job.archive_summaries || []).length > 0 && showItems && (
            <div className="udz-archive">
              {(job.archive_summaries || []).map((a) => (
                <div key={a.archive} className="udz-archive-row">
                  资料包 {a.archive}：解出 {a.extracted} 份，跳过 {a.skipped} 份
                  {a.note ? '（' + a.note + '）' : ''}
                </div>
              ))}
            </div>
          )}

          {(job.warnings || []).length > 0 && (
            <div className="udz-warnings">
              {(job.warnings || []).map((w, i) => <div key={i}>{w}</div>)}
            </div>
          )}
        </div>
      )}

      {/* 不受支持 / 超限 / 被拒绝的文件：提交前就给出具体原因 */}
      {rejected.length > 0 && (
        <div className="udz-rejected">
          {rejected.map((r, i) => (
            <div className="udz-rejected-row" key={r.filename + i}>
              <span className="udz-rejected-name" title={r.filename}>{r.filename}</span>
              <span className="udz-rejected-reason">{r.reason}</span>
            </div>
          ))}
          <div className="udz-rejected-foot">
            <span className="udz-rejected-reason">
              共 {rejected.length} 个文件未提交，可调整后重新选择
            </span>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => inputRef.current?.click()}>
              重新选择文件
            </Button>
          </div>
        </div>
      )}

      {/* 长说明按需展开：数字全部来自 capabilities（resolveLimits），不写死 */}
      <details className="udz-details">
        <summary>支持格式与大小限制</summary>
        <div className="udz-details-body">
          {caps ? (
            <>
              <div className="udz-limits-title">大小与数量上限</div>
              <ul className="udz-limit-list">
                <li>普通文档：单个文件 ≤ {limits.maxFileMB} MB</li>
                <li>压缩包：单个 ≤ {limits.maxArchiveMB} MB（按压缩包自身大小）</li>
                {limits.maxExtractFileMB !== null && (
                  <li>解压后：单个成员 ≤ {limits.maxExtractFileMB} MB</li>
                )}
                <li>
                  本批：最多 {limits.maxFiles} 个文件
                  {limits.maxTotalMB > 0 ? ' · 展开总量 ≤ ' + limits.maxTotalMB + ' MB' : ''}
                </li>
                <li>
                  压缩包最多 {limits.maxDepth} 层
                  {limits.maxSeconds !== null ? ' · 单次处理 ≤ ' + limits.maxSeconds + ' 秒' : ''}
                </li>
              </ul>

              <div className="udz-limits-title">可用格式</div>
              <ul className="udz-format-list">
                {availableFormats.map((f) => (
                  <li key={f.ext + f.kind}>
                    <span className="udz-format-ext">.{f.ext}</span>
                    <span>{f.label}</span>
                    <span className="udz-ok">可用</span>
                  </li>
                ))}
              </ul>

              {unavailableFormats.length > 0 && (
                <>
                  <div className="udz-limits-title">暂不可用格式与原因</div>
                  <ul className="udz-format-list udz-format-list--off">
                    {unavailableFormats.map((f) => (
                      <li key={f.ext + f.kind}>
                        <span className="udz-format-ext">.{f.ext}</span>
                        <span>{f.label}</span>
                        <span className="udz-off">不可用</span>
                        <span className="udz-format-note">{capabilityUnavailableReason(f)}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          ) : (
            <div>正在加载格式信息…</div>
          )}
        </div>
      </details>
    </section>
  );
};


export default UploadDropzone;
