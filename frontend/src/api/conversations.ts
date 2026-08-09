import apiClient from './axios';
import { ApiResponse, Conversation } from '../types';

// 会话消息（含结构化负载）
export interface ConversationMessage {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  message_type: string;
  payload: any;
  sources: any[];
  model: string | null;
  latency: number | null;
  created_at: string;
}

/**
 * 获取会话列表（可按学科过滤，按更新时间倒序）
 */
export const getConversations = async (subjectId?: string): Promise<ApiResponse<Conversation[]>> => {
  return apiClient.get('/conversations', {
    params: subjectId ? { subject_id: subjectId } : {},
  });
};

/**
 * 创建新会话（绑定学科）
 */
export const createConversation = async (
  subjectId: string,
  model?: string,
  title?: string,
): Promise<ApiResponse<Conversation>> => {
  return apiClient.post('/conversations', { subject_id: subjectId, model, title });
};

/**
 * 获取单个会话
 */
export const getConversation = async (id: string): Promise<ApiResponse<Conversation>> => {
  return apiClient.get(`/conversations/${id}`);
};

/**
 * 删除会话（含全部消息）
 */
export const deleteConversation = async (id: string): Promise<ApiResponse<any>> => {
  return apiClient.delete(`/conversations/${id}`);
};

/**
 * 获取会话的全部消息（按时间正序，含结构化 payload）
 */
export const getConversationMessages = async (
  id: string,
): Promise<ApiResponse<ConversationMessage[]>> => {
  return apiClient.get(`/conversations/${id}/messages`);
};
