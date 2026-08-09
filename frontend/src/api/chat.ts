import apiClient from './axios';
import { ChatRequest, ChatResponse, SourceInfo } from '../types';

/**
 * 发送聊天消息（非流式，保留兼容）
 */
export const sendMessage = async (request: ChatRequest): Promise<ChatResponse> => {
  return apiClient.post('/chat', request);
};

// 流式事件协议（与后端 chat_stream 对齐）
export interface StreamStartEvent {
  type: 'start';
  model?: string;
  subject_id?: string;
  context_used?: boolean;
}
export interface StreamDeltaEvent {
  type: 'delta';
  content: string;
}
export interface StreamDoneEvent {
  type: 'done';
  sources?: SourceInfo[];
  model?: string;
  elapsed?: number;
  ttft?: number | null;
  full_answer?: string;
  message_type?: string; // 结构化模式：smart_note|summary|explanation|advice|quiz
  payload?: any; // 结构化负载（前端据此渲染 Card）
}
export interface StreamErrorEvent {
  type: 'error';
  message?: string;
}
export type StreamEvent = StreamStartEvent | StreamDeltaEvent | StreamDoneEvent | StreamErrorEvent;

export interface StreamCallbacks {
  onStart?: (ev: StreamStartEvent) => void;
  onDelta?: (content: string) => void;
  onDone?: (ev: StreamDoneEvent) => void;
  onError?: (message: string) => void;
}

/**
 * 发送流式聊天消息（SSE / 真流式）
 *
 * - 使用 fetch + ReadableStream，TextDecoder 处理 UTF-8 跨 chunk 字节边界
 * - 返回 AbortController，组件卸载或用户点"停止生成"时可中止
 */
export const sendMessageStream = (
  request: ChatRequest,
  callbacks: StreamCallbacks
): AbortController => {
  const controller = new AbortController();

  (async () => {
    let response: Response;
    try {
      response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: controller.signal,
      });
    } catch (error: any) {
      if (error?.name === 'AbortError') return; // 用户主动停止，不报错
      callbacks.onError?.('网络请求失败，请检查后端是否启动');
      return;
    }

    if (!response.ok) {
      let detail = `请求失败 (HTTP ${response.status})`;
      try {
        const body = await response.json();
        detail = body?.detail || body?.message || detail;
      } catch { /* ignore */ }
      callbacks.onError?.(detail);
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) {
      callbacks.onError?.('无法获取响应流');
      return;
    }

    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        // 关键：stream:true 保留未完成的字节，避免中文被拆分乱码
        buffer += decoder.decode(value, { stream: true });

        // 按 \n 拆分完整行；SSE 事件以 data: 开头，两条事件间有空行
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const dataStr = trimmed.slice(5).trim();
          if (!dataStr) continue;

          let ev: StreamEvent;
          try {
            ev = JSON.parse(dataStr);
          } catch {
            continue; // 忽略无法解析的行
          }

          switch (ev.type) {
            case 'start':
              callbacks.onStart?.(ev);
              break;
            case 'delta':
              if (ev.content) callbacks.onDelta?.(ev.content);
              break;
            case 'done':
              callbacks.onDone?.(ev);
              return; // 结束
            case 'error':
              callbacks.onError?.(ev.message || '模型响应失败');
              return;
            default:
              break;
          }
        }
      }
      // 流正常结束但无 done 事件（异常断开）
      if (buffer.trim()) {
        const trimmed = buffer.trim();
        if (trimmed.startsWith('data:')) {
          const dataStr = trimmed.slice(5).trim();
          try {
            const ev = JSON.parse(dataStr) as StreamEvent;
            if (ev.type === 'done') { callbacks.onDone?.(ev); return; }
            if (ev.type === 'error') { callbacks.onError?.(ev.message || '模型响应失败'); return; }
          } catch { /* ignore */ }
        }
      }
    } catch (error: any) {
      if (error?.name === 'AbortError') return; // 用户主动停止
      callbacks.onError?.('回答生成中断，请重试');
    }
  })();

  return controller;
};
