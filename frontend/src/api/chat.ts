import apiClient from './axios';
import { ChatRequest, ChatResponse } from '../types';

/**
 * 发送聊天消息
 */
export const sendMessage = async (request: ChatRequest): Promise<ChatResponse> => {
  return apiClient.post('/chat', request);
};

/**
 * 发送流式聊天消息（SSE）
 */
export const sendMessageStream = async (
  request: ChatRequest,
  onMessage: (data: string) => void,
  onError?: (error: Error) => void
): Promise<void> => {
  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('无法获取响应流');
    }

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      
      // 处理SSE格式
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data === '[DONE]') {
            return;
          }
          onMessage(data);
        }
      }
    }
  } catch (error) {
    console.error('流式请求错误:', error);
    onError?.(error as Error);
  }
};