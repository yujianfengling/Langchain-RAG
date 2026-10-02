import apiClient from './axios';

export interface EmbeddingState {
  status: string;      // not_loaded | loading | ready | failed
  source: string | null;
  model: string;
  error_type: string | null;
}

/** 外部能力的**运行态**（最近一次真实调用结果），区别于 /health 的配置态能力 */
export interface ExternalCapability {
  capability: string;
  /** null = 本次进程还没调用过（不等于不可用） */
  ok: boolean | null;
  reason: string;
  success_count: number;
  fail_count: number;
  last_ok_at: string | null;
  last_fail_at: string | null;
  last_error: string;
}

/** 当日外部调用用量（成本可见） */
export interface UsageKind {
  used: number;
  /** 0 = 不限制 */
  limit: number;
  over: boolean;
}

export interface UsageToday {
  day: string;
  total: number;
  kinds: Record<string, UsageKind>;
}

export interface ReadyStatus {
  status: string;      // ok | degraded | loading | unavailable
  retrieval_mode: string;  // hybrid_ready | vector_only | bm25_degraded | not_loaded | loading | unavailable
  embedding: EmbeddingState;
  faiss: { available: boolean };
  bm25: { available: boolean };
  /** 降级可见：llm / web_search / vision / speech 的真实调用状态 */
  external_capabilities?: Record<string, ExternalCapability>;
  /** 当日调用用量与预算；超限时后台预生成自动暂停 */
  usage_today?: UsageToday;
}

/** 基础健康信息（/health）：版本、服务状态、配置态能力 */
export interface HealthInfo {
  status?: string;
  version?: string;
  services?: Record<string, boolean>;
  [k: string]: any;
}

/**
 * 获取基础健康信息（/health）
 * 与 /health/ready 的分工：这里报告"是什么版本、哪些服务在"，
 * ready 报告"检索/外部能力此刻能不能用"。
 */
export const getHealth = async (): Promise<HealthInfo> => {
  const res: any = await apiClient.get('/health');
  return (res?.data || res) as HealthInfo;
};

/**
 * 获取服务就绪状态（/health/ready）
 * 用于前端区分：混合检索就绪 / BM25降级 / 组件加载中 / 服务不可用
 */
export const getReadyStatus = async (): Promise<ReadyStatus> => {
  const res: any = await apiClient.get('/health/ready');
  return res?.data as ReadyStatus;
};
