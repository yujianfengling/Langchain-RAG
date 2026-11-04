import type { AxiosProgressEvent } from 'axios';
import apiClient, { LONG_TASK_TIMEOUT } from './axios';

/**
 * 资料导入 API（2026-10-07 冻结契约，见 docs/IMPORT_API_CONTRACT.md）
 *
 * 说明：apiClient 的响应拦截器已经解包过一层 `response.data`，
 * 后端契约体是 `{ success, data }` 包装，所以这里再取 `.data` 才是业务负载。
 * 为兼容“未包装”的直返体，统一用 `unwrap` 兜底，不静默伪造数据。
 */

const unwrap = <T,>(res: unknown): T => {
  const body = res as { data?: T } | undefined;
  if (body && typeof body === 'object' && 'data' in body) {
    return body.data as T;
  }
  return res as T;
};

// ============ 能力清单 ============

export interface FormatCapability {
  ext: string;
  kind: string;
  label: string;
  parser?: string;
  requires?: string | null;
  available: boolean;
  note?: string | null;
}

export interface ImportLimits {
  /** 普通文档单个文件上限（MB）。旧字段语义保留 = 普通文档（非压缩包）。 */
  max_file_mb: number;
  /** 压缩包单个上限（MB）。后端新字段；缺失时回退 max_file_mb。 */
  max_archive_mb?: number;
  /** 压缩包解压后单个文件上限（MB）。后端新字段。 */
  max_extract_file_mb?: number;
  /** 压缩包解压后总量上限（MB）。后端新字段。 */
  max_extract_total_mb?: number;
  /** 兼容旧契约的"本批总量"上限（MB）；缺失时回退 max_extract_total_mb。 */
  max_total_mb?: number;
  max_files: number;
  max_depth: number;
  max_seconds?: number;
}

/**
 * 归一化后的限制（数值，单位 MB；0 表示"不校验"）。
 * 只做字段回退，绝不写死数字——所有值都来自 capabilities。
 */
export interface NormalizedImportLimits {
  maxFileMB: number;
  maxArchiveMB: number;
  /** 解压后单个文件上限；后端未下发时为 null（展示时不编造） */
  maxExtractFileMB: number | null;
  /** 每个压缩包解压总量上限；后端未下发时为 null */
  maxExtractTotalMB: number | null;
  /** 本批所选文件总量上限；0 = 不校验 */
  maxTotalMB: number;
  maxFiles: number;
  maxDepth: number;
  /** 单次处理超时（秒）；后端未下发时为 null */
  maxSeconds: number | null;
}

/** 把 capabilities.limits 归一化成前端校验/展示共用的数字 */
export const resolveLimits = (
  caps: ImportCapabilities | null | undefined,
): NormalizedImportLimits => {
  const l = caps?.limits;
  const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  const opt = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  const maxFileMB = num(l?.max_file_mb);
  const maxArchiveMB = opt(l?.max_archive_mb) ?? maxFileMB;
  const maxExtractTotalMB = opt(l?.max_extract_total_mb);
  // 本批总量：优先契约旧字段 max_total_mb，否则用 max_extract_total_mb 兜底
  const maxTotalMB = opt(l?.max_total_mb) ?? maxExtractTotalMB ?? 0;
  return {
    maxFileMB,
    maxArchiveMB,
    maxExtractFileMB: opt(l?.max_extract_file_mb),
    maxExtractTotalMB,
    maxTotalMB,
    maxFiles: num(l?.max_files),
    maxDepth: num(l?.max_depth),
    maxSeconds: opt(l?.max_seconds),
  };
};

export interface ImportCapabilities {
  formats: FormatCapability[];
  archives: FormatCapability[];
  converter: { available: boolean; path?: string | null; kind?: string; note?: string | null };
  limits: ImportLimits;
  accept: string;
}

export const getImportCapabilities = async (): Promise<ImportCapabilities> => {
  const res = await apiClient.get('/knowledge/capabilities');
  return unwrap<ImportCapabilities>(res);
};

// ============ 任务与条目 ============

export type ImportItemStatus =
  | 'pending' | 'parsing' | 'indexing' | 'ready' | 'failed' | 'skipped'
  /** 后端新增语义：已发现但未处理（等待「继续处理」） */
  | 'unprocessed';

export type ImportJobStatus =
  | 'queued' | 'running' | 'succeeded' | 'partial' | 'failed' | 'cancelled';

/** 单条目的耗时/调用明细（后端新增，字段只增不改，缺失即不展示） */
export interface ImportItemTimings {
  parse_ms?: number | null;
  vision_ms?: number | null;
  vision_calls?: number | null;
  vision_cached?: number | null;
  images_total?: number | null;
  [key: string]: number | null | undefined;
}

export interface ImportItem {
  item_id: string;
  display_name: string;
  source_path?: string | null;
  origin_archive?: string | null;
  size?: number;
  status: ImportItemStatus;
  /** 更细阶段：extracting / parsing / indexing / ready ... */
  stage?: string;
  progress?: number | null;
  message?: string;
  doc_id?: string | null;
  error_type?: string | null;
  retryable?: boolean;
  /** 新增字段：耗时明细；缺失时前端不编造 */
  timings?: ImportItemTimings;
}

export interface ArchiveSummary {
  archive: string;
  extracted: number;
  skipped: number;
  note?: string;
}

/** 任务计数（后端新增 total/processing/unprocessed，保留 files 兼容） */
export interface ImportTotals {
  /** 兼容旧契约：条目数 */
  files: number;
  /** 有效资料总数（新）；缺失时前端按各项之和回退 */
  total?: number;
  ready: number;
  /** 处理中（新）；缺失视为 0 */
  processing?: number;
  failed: number;
  /** 发现但未处理（新）；缺失视为 0 */
  unprocessed?: number;
  skipped: number;
  bytes?: number;
  /** 本次任务发现的条目总数（归档成员 + 跳过项 + 嵌套包本身；不含目录）。不得当成可学习资料数。 */
  members_discovered?: number;
  /** 处理的压缩包容器数（外层 + 嵌套），与"资料"分开计数。 */
  containers?: number;
  /** 压缩包内目录成员数（只计数，绝不作为资料读取/报错）。 */
  directories?: number;
  /** 有效学习资料数（支持格式的候选成员）。缺失时前端回退 total，绝不用 members_discovered 冒充。 */
  materials?: number;
}

export interface ImportJob {
  image_enhancement?: { total: number; counts: Record<string, number>; filtered_by_source_check: number };
  job_id: string;
  subject_id: string;
  status: ImportJobStatus;
  created_at?: string;
  updated_at?: string;
  finished_at?: string | null;
  totals: ImportTotals;
  items: ImportItem[];
  archive_summaries?: ArchiveSummary[];
  kb?: { status?: string; ready?: boolean; document_count?: number; chunk_count?: number };
  summary_text?: string;
  can_retry?: boolean;
  /** 后端权威判定：该任务是否可以「继续处理剩余资料」（旧任务也能为 true） */
  can_continue?: boolean;
  /** 用户是否主动暂停：true 时任务停在批边界，绝不自动重启（resume 是唯一恢复入口） */
  paused?: boolean;
  /** 已请求暂停、等待当前批边界生效 */
  pause_requested?: boolean;
  can_pause?: boolean;
  can_resume?: boolean;
  can_cancel?: boolean;
  /** 已完成批次数（自动分批进度） */
  batch_index?: number;
  warnings?: string[];
}

export interface StartImportResult {
  job_id: string;
  accepted: number;
  rejected: { filename: string; reason: string }[];
}

/** 任务是否已到终态（终态后停止轮询） */
export const isTerminalStatus = (status: ImportJobStatus | undefined | null): boolean =>
  status === 'succeeded' || status === 'partial' || status === 'failed' || status === 'cancelled';

/** 归一化后的计数；sum 与 consistent 供前端自检不变式 */
export interface ResolvedImportTotals {
  total: number;
  ready: number;
  processing: number;
  failed: number;
  unprocessed: number;
  skipped: number;
  files: number;
  bytes?: number;
  /** 有效学习资料数（后端 materials；缺失回退 total，绝不用 members_discovered 冒充） */
  materials: number;
  /** 发现条目数（含跳过项/嵌套包；仅诊断展示，不等于资料数） */
  membersDiscovered: number;
  /** 压缩包容器数（外层 + 嵌套） */
  containers: number;
  /** 目录成员数 */
  directories: number;
  /** ready + processing + failed + unprocessed + skipped */
  sum: number;
  /** 后端不变式 total === sum 是否成立（不成立时界面明确提示） */
  consistent: boolean;
}

/**
 * 归一化 job.totals。缺失的新字段按 0 处理，total 缺失时按各项之和回退；
 * 绝不编造数据——total 与各项之和不一致时 consistent=false，交由界面提示。
 */
export const resolveTotals = (job: ImportJob | null | undefined): ResolvedImportTotals => {
  const t = job?.totals;
  const items = job?.items || [];
  const has = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
  const n = (v: unknown): number => (has(v) ? Math.max(0, v) : 0);
  const fromItems = (pred: (s: ImportItemStatus) => boolean): number =>
    items.filter((it) => pred(it.status)).length;

  // 新字段缺失（旧后端）时按 items 的真实状态回退计数，不编造也不漏计
  const ready = has(t?.ready) ? n(t.ready) : fromItems((s) => s === 'ready');
  const processing = has(t?.processing)
    ? n(t.processing)
    : fromItems((s) => s === 'pending' || s === 'parsing' || s === 'indexing');
  const failed = has(t?.failed) ? n(t.failed) : fromItems((s) => s === 'failed');
  const unprocessed = has(t?.unprocessed) ? n(t.unprocessed) : fromItems((s) => s === 'unprocessed');
  const skipped = has(t?.skipped) ? n(t.skipped) : fromItems((s) => s === 'skipped');
  const files = has(t?.files) ? n(t.files) : items.length;

  const sum = ready + processing + failed + unprocessed + skipped;
  const total = has(t?.total) ? n(t.total) : sum;
  // materials 是"有效学习资料"；缺失时回退 total，绝不用 members_discovered 冒充
  const materials = has(t?.materials) ? n(t.materials) : total;
  return {
    total, ready, processing, failed, unprocessed, skipped, files,
    bytes: t?.bytes,
    materials,
    membersDiscovered: has(t?.members_discovered) ? n(t.members_discovered) : 0,
    containers: has(t?.containers) ? n(t.containers) : 0,
    directories: has(t?.directories) ? n(t.directories) : 0,
    sum, consistent: total === sum,
  };
};

/**
 * 诚实状态：只要存在未处理或失败项，就绝不能显示"已完成"。
 * 后端不变式保证此时 status=partial，此函数是前端兜底，防止旧后端/缓存导致误报。
 */
export const effectiveJobStatus = (
  job: ImportJob | null | undefined,
): ImportJobStatus | null => {
  if (!job) return null;
  const t = resolveTotals(job);
  if (job.status === 'succeeded' && (t.unprocessed > 0 || t.failed > 0)) return 'partial';
  return job.status;
};

/** 任务是否被用户暂停（paused=true 时停在批边界，绝不自动重启） */
export const isJobPaused = (job: ImportJob | null | undefined): boolean => job?.paused === true;

/** 是否处于自动分批运行中（queued/running 且未被暂停）；pause_requested 仍算运行中，等批边界生效 */
export const isJobRunning = (job: ImportJob | null | undefined): boolean => {
  if (!job || isJobPaused(job)) return false;
  const s = effectiveJobStatus(job);
  return s === 'queued' || s === 'running';
};

/** 运行中才允许暂停（后端未下发 can_pause 时按运行态兜底） */
export const jobCanPause = (job: ImportJob | null | undefined): boolean => {
  if (!job || isJobPaused(job)) return false;
  return isJobRunning(job) && job.can_pause !== false;
};

/** 仅 paused 可恢复（与后端 resume 约束一致） */
export const jobCanResume = (job: ImportJob | null | undefined): boolean =>
  Boolean(job && isJobPaused(job) && job.can_resume !== false);

/** 是否允许取消：未到 succeeded/cancelled/failed 终态时；未下发 can_cancel 时按状态兜底 */
export const jobCanCancel = (job: ImportJob | null | undefined): boolean => {
  if (!job) return false;
  const s = effectiveJobStatus(job);
  if (s === 'succeeded' || s === 'cancelled' || s === 'failed') return false;
  return job.can_cancel !== false;
};

/** 是否存在可重试的失败项 */
export const hasRetryableFailed = (job: ImportJob | null | undefined): boolean =>
  Boolean(job?.items?.some((it) => it.status === 'failed' && it.retryable));

/**
 * 是否需要「继续处理剩余资料」。
 *
 * 以**后端 can_continue 为准**（后端会为"有未处理成员"或"整体失败但原包仍在、可重跑"的任务置位，
 * 包括本改动之前创建的旧任务）；缺失该字段时再按前端兜底规则判断。
 * 2026-10-07 真实事故：被误判 archive_corrupt 的 7z 任务整体 failed、但原包仍在可重跑，
 * 只按 status=partial 判断会让用户找不到「继续处理」入口。
 */
export const jobNeedsContinue = (job: ImportJob | null | undefined): boolean => {
  if (!job) return false;
  // paused 的正确入口是「继续（resume）」，不是「继续处理剩余资料」
  if (isJobPaused(job)) return false;
  // 用户已经取消的任务不再劝继续
  if (effectiveJobStatus(job) === 'cancelled') return false;
  if (job.can_continue === true) return true;
  const t = resolveTotals(job);
  return effectiveJobStatus(job) === 'partial' && (t.unprocessed > 0 || hasRetryableFailed(job));
};

// ============ 发起 / 查询 / 重试 / 取消 ============

export const startImport = async (
  subjectId: string,
  files: File[],
  onUploadProgress?: (pct: number) => void,
): Promise<StartImportResult> => {
  const formData = new FormData();
  // 契约字段名：files（可重复）
  files.forEach((f) => formData.append('files', f));

  const res = await apiClient.post(
    `/knowledge/${encodeURIComponent(subjectId)}/import`,
    formData,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      // 大包上传可能好几分钟（367MB 实测 244s），默认 180s 会在这里就断
      timeout: LONG_TASK_TIMEOUT,
      onUploadProgress: (evt: AxiosProgressEvent) => {
        if (!onUploadProgress) return;
        // 只用真实的 loaded/total；total 未知时不报进度（不编造百分比）
        const total = evt.total ?? 0;
        if (total > 0) {
          onUploadProgress(Math.min(100, Math.round((evt.loaded / total) * 100)));
        } else {
          onUploadProgress(-1);
        }
      },
    },
  );
  return unwrap<StartImportResult>(res);
};

export const getImportJob = async (jobId: string): Promise<ImportJob> => {
  const res = await apiClient.get(`/knowledge/imports/${encodeURIComponent(jobId)}`);
  return unwrap<ImportJob>(res);
};

export const listImports = async (subjectId: string, limit = 5): Promise<ImportJob[]> => {
  const res = await apiClient.get(`/knowledge/${encodeURIComponent(subjectId)}/imports`, {
    params: { limit },
  });
  const data = unwrap<unknown>(res);
  if (Array.isArray(data)) return data as ImportJob[];
  // 容错：后端若包成 { jobs: [...] } / { imports: [...] } 也能恢复任务
  const wrap = data as { jobs?: ImportJob[]; imports?: ImportJob[] } | undefined;
  if (Array.isArray(wrap?.jobs)) return wrap!.jobs as ImportJob[];
  if (Array.isArray(wrap?.imports)) return wrap!.imports as ImportJob[];
  return [];
};

export const retryImport = async (jobId: string): Promise<{ job_id: string }> => {
  const res = await apiClient.post(`/knowledge/imports/${encodeURIComponent(jobId)}/retry`);
  return unwrap<{ job_id: string }>(res);
};

/**
 * 继续处理剩余/失败项（后端契约 2026-10-07 新增）。
 * 已完成的资料会复用，不会重复导入；返回新的 job_id 继续轮询。
 */
export const continueImport = async (jobId: string): Promise<{ job_id: string }> => {
  const res = await apiClient.post(`/knowledge/imports/${encodeURIComponent(jobId)}/continue`);
  return unwrap<{ job_id: string }>(res);
};

/**
 * 暂停任务（2026-10-08 新增）：在下一个**批边界**停止，状态置 partial 且 paused=true，
 * **不会自动重启**；resume 是唯一恢复入口。
 */
export const pauseImport = async (
  jobId: string,
): Promise<{ job_id: string; paused: boolean }> => {
  const res = await apiClient.post(`/knowledge/imports/${encodeURIComponent(jobId)}/pause`);
  return unwrap<{ job_id: string; paused: boolean }>(res);
};

/**
 * 继续已暂停的任务（2026-10-08 新增）：仅 paused=true 可 resume，
 * 同一 job 从剩余成员继续自动分批，已 ready 的成员按内容哈希复用。
 */
export const resumeImport = async (jobId: string): Promise<{ job_id: string }> => {
  const res = await apiClient.post(`/knowledge/imports/${encodeURIComponent(jobId)}/resume`);
  return unwrap<{ job_id: string }>(res);
};

/**
 * 取消任务（2026-10-08：改为契约新增的 POST /cancel，与 DELETE 等价）。
 * 取消后不自动重启；已导入的资料不会删除。
 */
export const cancelImport = async (jobId: string): Promise<boolean> => {
  const res = await apiClient.post(`/knowledge/imports/${encodeURIComponent(jobId)}/cancel`);
  const body = res as { success?: boolean } | undefined;
  return body?.success !== false;
};

// ============ 前端预校验（按 caps.accept，大小写不敏感） ============

const extOf = (name: string): string => {
  const idx = name.lastIndexOf('.');
  if (idx < 0) return '';
  return name.slice(idx).toLowerCase();
};

/** 从 accept 字符串（`.pptx,.pdf` 或 MIME）解析出小写扩展名集合 */
export const parseAccept = (accept: string | undefined | null): Set<string> => {
  const set = new Set<string>();
  if (!accept) return set;
  accept.split(',').forEach((raw) => {
    const token = raw.trim().toLowerCase();
    if (!token) return;
    if (token.startsWith('.')) set.add(token);
  });
  return set;
};

export const isSupportedFile = (name: string, caps: ImportCapabilities | null | undefined): boolean => {
  if (!caps) return false;
  const accepted = parseAccept(caps.accept);
  if (accepted.size === 0) return false; // accept 为空则无可导入格式，交由后端明确拒绝
  return accepted.has(extOf(name));
};

export interface UnsupportedFile {
  file: File;
  filename: string;
  reason: string;
}

export type ImportRejectType =
  | 'unsupported_format'
  | 'converter_unavailable'
  | 'size_exceeded'
  | 'count_exceeded'
  | 'total_size_exceeded';

export interface RejectedFile extends UnsupportedFile {
  error_type: ImportRejectType;
}

/** 在 formats + archives 里按扩展名找能力项（大小写/带点不敏感） */
export const findCapability = (
  caps: ImportCapabilities | null | undefined,
  name: string,
): FormatCapability | undefined => {
  if (!caps) return undefined;
  const ext = extOf(name).replace(/^\./, '');
  if (!ext) return undefined;
  return [...(caps.formats || []), ...(caps.archives || [])]
    .find((f) => (f.ext || '').toLowerCase().replace(/^\./, '') === ext);
};

/** 该文件是否是压缩包（按 capabilities.archives 判定，不靠后缀猜） */
export const isArchiveFile = (
  caps: ImportCapabilities | null | undefined,
  name: string,
): boolean => {
  if (!caps) return false;
  const ext = extOf(name).replace(/^\./, '');
  return (caps.archives || []).some((a) => (a.ext || '').toLowerCase().replace(/^\./, '') === ext);
};

/** 不可用格式的原因：优先用后端 note，绝不自己编造 */
export const capabilityUnavailableReason = (cap: FormatCapability): string => {
  const note = (cap.note || '').trim();
  if (note) return note;
  if (cap.requires) return `需要${cap.requires}，当前不可用`;
  return '暂不支持该格式';
};

/** 单个文件的格式分类；返回 null 表示可继续做大小/数量校验 */
const classifyFormat = (
  file: File,
  caps: ImportCapabilities | null | undefined,
): RejectedFile | null => {
  if (!caps) {
    return { file, filename: file.name, reason: '无法获取支持的格式，请稍后重试', error_type: 'unsupported_format' };
  }
  const cap = findCapability(caps, file.name);
  if (cap && !cap.available) {
    return {
      file, filename: file.name,
      reason: capabilityUnavailableReason(cap),
      error_type: cap.requires === 'libreoffice' ? 'converter_unavailable' : 'unsupported_format',
    };
  }
  if (cap && cap.available) return null;
  const ext = extOf(file.name);
  if (ext && parseAccept(caps.accept).has(ext)) return null;
  return {
    file, filename: file.name,
    reason: ext ? `不支持的文件类型（${ext}）` : '无法识别文件类型（缺少扩展名）',
    error_type: 'unsupported_format',
  };
};

/** 按 caps.accept 拆分支持/不支持（大小写不敏感）；不支持项带原因，提交前即可提示 */
export const splitSupported = (
  files: File[],
  caps: ImportCapabilities | null | undefined,
): { supported: File[]; unsupported: UnsupportedFile[] } => {
  const supported: File[] = [];
  const unsupported: UnsupportedFile[] = [];
  files.forEach((file) => {
    const rej = classifyFormat(file, caps);
    if (rej) unsupported.push({ file, filename: rej.filename, reason: rej.reason });
    else supported.push(file);
  });
  return { supported, unsupported };
};

export const formatBytes = (bytes?: number | null): string => {
  if (bytes === undefined || bytes === null || Number.isNaN(bytes)) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

/**
 * 提交前的完整校验（格式 + 按类型大小 + 数量 + 本批总量）。
 *
 * 与 capabilities 的对应（字段名以 docs/IMPORT_API_CONTRACT.md 为准）：
 * - 压缩包用 limits.max_archive_mb，其它用 limits.max_file_mb；
 * - 数量用 limits.max_files；本批总量 = 所选文件之和（压缩包按自身大小），
 *   上限取 limits.max_total_mb，缺失时回退 limits.max_extract_total_mb；
 * - 解压后单文件/总量（max_extract_file_mb / max_extract_total_mb）由后端在解包时校验，
 *   前端不假装能预知压缩包内容。
 * 每条被拒原因必须具体（写明上限与实际值），不返回笼统的"文件过大"。
 */
export const validateSelection = (
  files: File[],
  caps: ImportCapabilities | null | undefined,
): { accepted: File[]; rejected: RejectedFile[] } => {
  const rejected: RejectedFile[] = [];
  if (!caps) {
    files.forEach((file) => rejected.push({
      file, filename: file.name,
      reason: '无法获取支持的格式，请稍后重试', error_type: 'unsupported_format',
    }));
    return { accepted: [], rejected };
  }
  const limits = resolveLimits(caps);

  // 1) 格式：不可用格式用后端 note 说明原因
  const formatOk: File[] = [];
  files.forEach((file) => {
    const rej = classifyFormat(file, caps);
    if (rej) rejected.push(rej);
    else formatOk.push(file);
  });

  // 2) 单文件大小：压缩包用 max_archive_mb，其它用 max_file_mb
  const sizeOk: File[] = [];
  formatOk.forEach((file) => {
    const archive = isArchiveFile(caps, file.name);
    const limit = archive ? limits.maxArchiveMB : limits.maxFileMB;
    if (limit > 0 && file.size > limit * 1024 * 1024) {
      rejected.push({
        file, filename: file.name,
        reason: `${archive ? '单个压缩包' : '单个文件'}上限 ${limit}MB，该文件 ${formatBytes(file.size)}`,
        error_type: 'size_exceeded',
      });
    } else {
      sizeOk.push(file);
    }
  });

  // 3) 文件数
  let pool = sizeOk;
  if (limits.maxFiles > 0 && pool.length > limits.maxFiles) {
    const current = pool.length;
    pool.slice(limits.maxFiles).forEach((f) => rejected.push({
      file: f, filename: f.name,
      reason: `本批最多 ${limits.maxFiles} 个文件，当前 ${current} 个`,
      error_type: 'count_exceeded',
    }));
    pool = pool.slice(0, limits.maxFiles);
  }

  // 4) 本批总量：所选文件之和（压缩包按自身大小）
  if (limits.maxTotalMB > 0) {
    const maxBytes = limits.maxTotalMB * 1024 * 1024;
    let acc = 0;
    const kept: File[] = [];
    pool.forEach((f) => {
      if (acc + f.size > maxBytes) {
        rejected.push({
          file: f, filename: f.name,
          reason: `本批总量上限 ${limits.maxTotalMB}MB，当前 ${formatBytes(acc)}，再加入该文件将达 ${formatBytes(acc + f.size)}`,
          error_type: 'total_size_exceeded',
        });
      } else {
        acc += f.size;
        kept.push(f);
      }
    });
    pool = kept;
  }

  return { accepted: pool, rejected };
};
