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
  model?: string;
  history?: Array<{ role: string; content: string }>;
  image_data?: string; // base64编码的图片数据
  image_mime_type?: string; // 图片MIME类型
  conversation_id?: string; // 会话ID（V2.5.1）
  mode?: string; // 学习模式：chat|smart_note|summary|explanation|advice|quiz
}

// 学习模式
export type ChatMode = 'chat' | 'smart_note' | 'summary' | 'explanation' | 'advice' | 'quiz';

// 参考来源信息（与后端 extract_sources 对齐）
export interface SourceInfo {
  id: number;
  file_name: string;
  page?: number | null;
  type?: string;
}

export interface ChatResponse {
  success: boolean;
  answer: string;
  sources: SourceInfo[];
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
  subject_id: string;
  status: string;
  has_knowledge_base: boolean;
  path: string;
  total_size_bytes: number;
  total_size_mb: number;
  file_count: number;
}

export interface KnowledgeBase {
  subject_id: string;
  status?: string;
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
  sources?: SourceInfo[];
  model?: string;
  latency?: number;
  ttft?: number | null;
  image_url?: string; // 图片URL（用于显示）
  image_data?: string; // base64编码的图片数据
  message_type?: string; // 结构化消息类型：chat|smart_note|summary|explanation|advice|quiz
  payload?: any; // 结构化消息负载（V2.5.1，Card恢复渲染用）
}

// 会话（V2.5.1 Conversation 系统）
export interface Conversation {
  id: string;
  subject_id: string;
  title: string;
  model: string | null;
  created_at: string;
  updated_at: string;
}