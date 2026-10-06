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
  max_file_mb: number;
  max_total_mb: number;
  max_files: number;
  max_depth: number;
  max_seconds?: number;
}

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

/** 按 caps.accept 拆分支持/不支持（大小写不敏感）；不支持项带原因，提交前即可提示 */
export const splitSupported = (
  files: File[],
  caps: ImportCapabilities | null | undefined,
): { supported: File[]; unsupported: UnsupportedFile[] } => {
  const supported: File[] = [];
  const unsupported: UnsupportedFile[] = [];
  const accepted = caps ? parseAccept(caps.accept) : new Set<string>();
  files.forEach((file) => {
    const ext = extOf(file.name);
    if (accepted.size > 0 && accepted.has(ext)) {
      supported.push(file);
    } else {
      const reason = ext
        ? `不支持的文件类型（${ext}）`
        : '无法识别文件类型（缺少扩展名）';
      unsupported.push({ file, filename: file.name, reason });
    }
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
