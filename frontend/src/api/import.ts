import type { AxiosProgressEvent } from 'axios';
import apiClient from './axios';

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
  | 'pending' | 'parsing' | 'indexing' | 'ready' | 'failed' | 'skipped';

export type ImportJobStatus =
  | 'queued' | 'running' | 'succeeded' | 'partial' | 'failed' | 'cancelled';

export interface ImportItem {
  item_id: string;
  display_name: string;
  source_path?: string | null;
  origin_archive?: string | null;
  size?: number;
  status: ImportItemStatus;
  stage?: string;
  progress?: number | null;
  message?: string;
  doc_id?: string | null;
  error_type?: string | null;
  retryable?: boolean;
}

export interface ArchiveSummary {
  archive: string;
  extracted: number;
  skipped: number;
  note?: string;
}

export interface ImportJob {
  job_id: string;
  subject_id: string;
  status: ImportJobStatus;
  created_at?: string;
  updated_at?: string;
  finished_at?: string | null;
  totals: { files: number; ready: number; failed: number; skipped: number; bytes?: number };
  items: ImportItem[];
  archive_summaries?: ArchiveSummary[];
  kb?: { status?: string; ready?: boolean; document_count?: number; chunk_count?: number };
  summary_text?: string;
  can_retry?: boolean;
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

export const cancelImport = async (jobId: string): Promise<boolean> => {
  const res = await apiClient.delete(`/knowledge/imports/${encodeURIComponent(jobId)}`);
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
