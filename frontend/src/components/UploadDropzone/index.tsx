import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Progress, Tag, Typography, message } from 'antd';
import {
  DownOutlined, FileTextOutlined, InboxOutlined, ReloadOutlined, RightOutlined,
} from '@ant-design/icons';
import {
  ImportCapabilities, ImportItem, ImportItemStatus, ImportJob, ImportJobStatus,
  cancelImport, capabilityUnavailableReason, formatBytes, getImportCapabilities, getImportJob,
  isTerminalStatus, listImports, resolveLimits, retryImport, startImport, validateSelection,
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
        const activeJob = list.find((j) => !isTerminalStatus(j.status));
        if (alive && activeJob) setJob(activeJob);
      } catch {
        // 没有历史记录时后端可能报错，不打扰用户
      }
    })();
    return () => { alive = false; };
  }, [subjectId]);

  // 轮询：1.5s，终态停止，卸载清理
  useEffect(() => {
    if (!job || isTerminalStatus(job.status)) return;
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

  const handleCancel = async (jobId: string) => {
    try {
      await cancelImport(jobId);
      setJob(await getImportJob(jobId));
    } catch (e) {
      message.error(describeError(e, '取消失败，请稍后重试'));
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
  const readyCount = job?.totals?.ready ?? 0;
  const showItems = !terminal || detailsOpen;
  const jobMeta = job ? JOB_STATUS_META[job.status] : null;

  const renderItem = (item: ImportItem) => {
    const meta = ITEM_STATUS_META[item.status] || { label: item.status, color: 'default' };
    const realProgress = typeof item.progress === 'number' ? Math.round(item.progress * 100) : null;
    return (
      <div className="udz-row" key={item.item_id}>
        <FileTextOutlined className="udz-row-icon" />
        <div className="udz-row-main">
          <div className="udz-row-name" title={item.display_name}>{item.display_name}</div>
          <div className="udz-row-sub">
            {item.origin_archive ? '来自 ' + item.origin_archive : ''}
            {item.origin_archive && item.size ? ' · ' : ''}
            {item.size ? formatBytes(item.size) : ''}
            {item.stage ? ' · ' + item.stage : ''}
          </div>
          {item.status === 'failed' && (
            <div className="udz-row-msg">
              {item.message || '导入失败'}
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
            {!terminal && (
              <span className="udz-queue-progress">
                {job.totals.ready} 可学习 · {job.totals.failed} 失败 · {job.totals.skipped} 跳过
              </span>
            )}
            {!terminal && (
              <Button type="link" size="small" className="udz-cancel" onClick={() => void handleCancel(job.job_id)}>
                取消
              </Button>
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

          {terminal && (
            <div className="udz-summary">
              <div className="udz-summary-text">
                {job.summary_text
                  || ('共 ' + job.totals.files + ' 份：' + readyCount + ' 可学习，'
                    + job.totals.failed + ' 失败，' + job.totals.skipped + ' 跳过')}
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
                {readyCount > 0 && (
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
