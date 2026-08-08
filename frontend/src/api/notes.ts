import apiClient from './axios';
import { ApiResponse, SourceInfo } from '../types';

// 自测题类型
export type QuizType = 'single_choice' | 'judgement' | 'short_answer';

// 自测题结构（为未来学情诊断预留 user_answer/is_correct/answered_at）
export interface QuizItem {
  id: string;
  question: string;
  type: QuizType;
  options: string[];
  answer: string;
  explanation: string;
  knowledge_point: string;
  sources?: SourceInfo[];
  user_answer?: string | null;
  is_correct?: boolean | null;
  answered_at?: string | null;
}

// 五段式智能笔记
export interface NoteSection {
  content: string;
  sources: SourceInfo[];
}

export interface SmartNote {
  title: string;
  concept: NoteSection;
  example: NoteSection;
  mistakes: NoteSection;
  class_tips: NoteSection;
  quiz: QuizItem[];
  model: string;
  context_used: boolean;
  evidence_count: number;
}

// 生成智能笔记请求
export interface SmartNoteRequest {
  subject_id: string;
  topic: string;
  user_instruction?: string;
}

/**
 * 生成五段式智能笔记
 */
export const generateSmartNote = async (data: SmartNoteRequest): Promise<ApiResponse<SmartNote>> => {
  return apiClient.post('/notes/generate', data);
};
