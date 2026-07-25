/**
 * 全局类型定义
 */

// API响应基础类型
export interface ApiResponse<T = any> {
  success: boolean;
  data: T;
  message: string;
}

// 聊天相关类型
export interface ChatRequest {
  question: string;
  subject_id: string;
  history?: Array<{ role: string; content: string }>;
}

export interface ChatResponse {
  success: boolean;
  answer: string;
  sources: string[];
  model: string;
  latency: number;
}

// 学科相关类型
export interface Subject {
  subject_id: string;
  name: string;
  description?: string;
}

// 知识库相关类型
export interface KnowledgeStats {
  path: string;
  total_size_bytes: number;
  total_size_mb: number;
  file_count: number;
}

export interface KnowledgeBase {
  subject_id: string;
  stats?: KnowledgeStats;
}

export interface KnowledgeBuildRequest {
  subject_id: string;
  path: string;
}

// 模型相关类型
export interface ModelInfo {
  provider: string;
  model: string;
  available: boolean;
  status?: string;
  error?: string;
}

export interface CurrentModel {
  provider: string;
  model: string;
  config?: {
    temperature?: number;
    max_tokens?: number;
  };
  provider_info?: {
    provider: string;
    supported_providers: string[];
    is_supported: boolean;
    model: string;
    api_base: string;
    temperature: number;
    max_tokens: number;
    api_key_configured: boolean;
    langchain_available: boolean;
  };
}

export interface SwitchModelRequest {
  provider: string;
}

// 聊天消息类型
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  sources?: string[];
  model?: string;
  latency?: number;
}