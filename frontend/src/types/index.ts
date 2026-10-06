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
  /** 资料范围（2026-10-06）：仅在这些文件内检索；不传 = 本学科全部资料 */
  source_files?: string[];
}

// 学习模式
export type ChatMode =
  | 'chat' | 'smart_note' | 'summary' | 'explanation' | 'advice' | 'quiz'
  /** 阶段二：学习助手（受约束的 Agent 运行，前端展示步骤进度并可停止） */
  | 'agent';

/** 学习助手执行步骤（仅记录工具调用与结果摘要，不含模型内部思维过程） */
export interface AgentStepView {
  tool: string;
  label?: string;
  status: 'running' | 'done' | 'failed' | 'skipped' | 'cancelled' | 'timeout';
  summary?: string;
  error?: string | null;
  duration_ms?: number;
}

/** 本轮教学策略与依据（画像 → 教学安排的可解释输出） */
/** 本轮实际用上的画像依据（由后端 build_teaching_context 产出，只记录被采用的信息） */
export interface TeachingApplied {
  teaching_mode?: string;
  preferences?: string[];
  memory?: string[];
  knowledge_point?: string | null;
  recent_mistakes?: number;
  has_task_context?: boolean;
}

export interface TeachingInfo {
  mode?: 'foundation' | 'standard' | 'advanced' | string;
  reasons?: string[];
  signals?: Record<string, any>;
  applied?: TeachingApplied;
  /** 一句学生可感知、且对应真实作答记录的变化说明（无证据时为空） */
  adaptation_note?: string | null;
}

// 参考来源信息（与后端 extract_sources 对齐）
export interface SourceInfo {
  id: number;
  file_name: string;
  page?: number | null;
  type?: string;
  /** 真实检索到的原文片段（用于"查看原文"，不拼接、不生成） */
  snippet?: string;
  /** 章节/小节（资料里有才给） */
  section?: string | null;
  /** 检索/重排分数（诊断信息，默认收起） */
  score?: number | null;
  /** 召回来源：vector（向量）/ keyword（关键词） */
  retrieval_type?: string | null;
}

export interface ChatResponse {
  success: boolean;
  answer: string;
  sources: SourceInfo[];
  model: string;
  provider?: string;
  provider_display?: string;
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
  /** 实际提供方键（无则为历史消息，只有 model 标记） */
  provider?: string;
  latency?: number;
  ttft?: number | null;
  image_url?: string; // 图片URL（用于显示）
  image_data?: string; // base64编码的图片数据
  message_type?: string; // 结构化消息类型：chat|smart_note|summary|explanation|advice|quiz
  payload?: any; // 结构化消息负载（V2.5.1，Card恢复渲染用）
  /** 关联的用户交互记录 ID（阶段一：反馈必须关联真实 interaction_id，而不是 message_id） */
  interaction_id?: string | null;
  /** 后端 context_used：检索到课程资料并作为上下文提供给了模型（不等于"回答引用了它"） */
  context_used?: boolean;
  /** 回答中显式标注引用的来源编号（如 [1][2]）；这是"实际引用"的确定性信号 */
  cited_source_ids?: number[];
  /** 学习助手执行步骤（阶段二：展示"正在查看学习记录/正在安排复习…"） */
  agent_steps?: AgentStepView[];
  /** 学习助手运行结果 */
  agent_run?: {
    id: string;
    status: string;
    error?: string | null;
    steps?: number;
    results?: Record<string, any>;
  } | null;
  /** 本轮教学策略与依据（阶段二） */
  teaching?: TeachingInfo | null;
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