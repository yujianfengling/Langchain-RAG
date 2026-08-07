import apiClient from './axios';

export interface EmbeddingState {
  status: string;      // not_loaded | loading | ready | failed
  source: string | null;
  model: string;
  error_type: string | null;
}

export interface ReadyStatus {
  status: string;      // ok | degraded | loading | unavailable
  retrieval_mode: string;  // hybrid_ready | vector_only | bm25_degraded | not_loaded | loading | unavailable
  embedding: EmbeddingState;
  faiss: { available: boolean };
  bm25: { available: boolean };
}

/**
 * 获取服务就绪状态（/health/ready）
 * 用于前端区分：混合检索就绪 / BM25降级 / 组件加载中 / 服务不可用
 */
export const getReadyStatus = async (): Promise<ReadyStatus> => {
  const res: any = await apiClient.get('/health/ready');
  return res?.data as ReadyStatus;
};
