import apiClient, { authHeaders } from './axios';
import { ApiResponse } from '../types';

// ============ 学习 Agent 运行（阶段二） ============

export interface AgentStep {
  seq?: number;
  tool: string;
  label?: string;
  status: 'running' | 'done' | 'failed' | 'skipped' | 'cancelled' | 'timeout';
  summary?: string;
  error?: string | null;
  duration_ms?: number;
  created_at?: string;
}

export interface AgentRun {
  id: string;
  subject_id?: string | null;
  goal?: string | null;
  intent?: string;
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED' | 'TIMEOUT';
  steps: number;
  llm_calls?: number;
  error?: string | null;
  steps_detail?: AgentStep[];
  created_at?: string;
  finished_at?: string | null;
  cancel_requested?: boolean;
  results?: Record<string, any>;
}

/** 同步执行（等待全部完成） */
export const runAgent = async (payload: {
  subject_id: string;
  goal: string;
  intent?: string;
  model?: string;
}): Promise<ApiResponse<AgentRun>> => {
  return apiClient.post('/agent/run', payload);
};

/** 运行详情（状态 + 工具调用摘要） */
export const getRun = async (runId: string): Promise<ApiResponse<AgentRun>> => {
  return apiClient.get(`/agent/runs/${runId}`);
};

export const listRuns = async (): Promise<ApiResponse<AgentRun[]>> => {
  return apiClient.get('/agent/runs');
};

export const cancelRun = async (runId: string): Promise<ApiResponse<AgentRun>> => {
  return apiClient.post(`/agent/runs/${runId}/cancel`);
};

/**
 * 以 SSE 方式执行并接收进度。
 *
 * 用 fetch 而不是 axios：需要读取流式响应体；中止时 abort 会让后端把
 * 连接断开视为"停止"，运行的 cancel_check 会在下一步生效。
 */
export const runAgentStream = async (
  payload: { subject_id: string; goal: string; intent?: string; model?: string },
  handlers: {
    onStep?: (step: AgentStep & { run_id?: string; steps_done?: number }) => void;
    onDone?: (run: AgentRun) => void;
    onError?: (message: string) => void;
  },
  signal?: AbortSignal,
): Promise<void> => {
  let resp: Response;
  try {
    resp = await fetch('/api/agent/run/stream', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
      signal,
    });
  } catch (e: any) {
    if (e?.name === 'AbortError') return;
    handlers.onError?.('无法连接学习助手服务');
    return;
  }

  if (!resp.ok || !resp.body) {
    let detail = `请求失败（${resp.status}）`;
    try {
      const data = await resp.json();
      detail = data?.message || data?.detail || detail;
    } catch {
      /* 忽略解析失败 */
    }
    handlers.onError?.(detail);
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  while (true) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch (e: any) {
      if (e?.name === 'AbortError') return;
      handlers.onError?.('进度流中断');
      return;
    }
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';
    for (const part of parts) {
      const line = part.split('\n').find((l) => l.startsWith('data: '));
      if (!line) continue;
      let evt: any;
      try {
        evt = JSON.parse(line.slice(6));
      } catch {
        continue;
      }
      if (evt.type === 'step') handlers.onStep?.(evt);
      else if (evt.type === 'done') handlers.onDone?.(evt.data);
      else if (evt.type === 'error') handlers.onError?.(evt.message || '执行失败');
    }
  }
};
