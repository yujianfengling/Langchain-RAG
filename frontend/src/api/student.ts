import apiClient from './axios';
import { ApiResponse } from '../types';

// ============ 类型定义（与后端阶段一接口一致） ============

export type TaskType = 'READ' | 'REVIEW' | 'QUIZ' | 'PRACTICE';
export type TaskStatus = 'TODO' | 'IN_PROGRESS' | 'COMPLETED';
export type TaskBucket = 'today' | 'todo' | 'completed' | 'overdue';

export interface StudentTask {
  id: string;
  plan_id: string;
  user_id?: string | null;
  subject_id?: string | null;
  knowledge_point: string | null;
  knowledge_point_id?: string | null;
  task_type: TaskType;
  title: string;
  description: string | null;
  priority: number;
  estimated_minutes: number;
  status: TaskStatus;
  planned_date?: string | null;
  original_planned_date?: string | null;
  defer_count?: number;
  source?: string | null;
  source_attempt_id?: string | null;
  completed_at: string | null;
  actual_minutes: number | null;
  created_at: string;
  /** 后端计算：是否已逾期（只读，前端不自行判断） */
  overdue?: boolean;
  /** 测验任务是否已有真实作答（区分"完成"与"通过"） */
  has_attempt?: boolean;
  attempt_count?: number;
  correct_count?: number;
}

export interface TaskBuckets {
  today: StudentTask[];
  todo: StudentTask[];
  completed: StudentTask[];
  overdue: StudentTask[];
  counts: { today: number; todo: number; completed: number; overdue: number };
}

export interface QuizOption {
  id: string;
  text: string;
}

/** 公开题目：不含标准答案（答案只能通过提交作答或显式查看答案获得） */
export interface PublicQuestion {
  id: string;
  question: string;
  type: 'single_choice' | 'judgement' | 'short_answer';
  options: QuizOption[];
  knowledge_point?: string | null;
  knowledge_point_id?: string | null;
  server_persisted?: boolean;
  /** 题目来源：knowledge_base|note|quiz(你的资料) / web_search(网络资料) / general_knowledge(AI 通用知识) */
  source?: string | null;
}

export interface AttemptResult {
  attempt_id: string;
  question_id: string;
  knowledge_point?: string | null;
  knowledge_point_id?: string | null;
  is_correct: boolean | null;
  grade_status: 'graded' | 'ai_graded' | 'pending_self_eval' | 'self_evaluated' | string;
  /** AI 判分简答题的评判理由（grade_status=ai_graded 时有值） */
  ai_feedback?: string | null;
  user_answer?: string | null;
  user_answer_text?: string | null;
  correct_option_id?: string | null;
  correct_option_text?: string | null;
  reference_answer?: string | null;
  explanation?: string | null;
  attempt_no: number;
  attempt_kind?: 'independent' | 'review' | 'after_reveal';
  created_at?: string;
  linkage?: any;
}

export interface WrongQuestion {
  question_id: string;
  stem: string;
  options: QuizOption[];
  knowledge_point?: string | null;
  knowledge_point_id?: string | null;
  user_answer?: string | null;
  user_answer_text?: string | null;
  correct_option_id?: string | null;
  correct_option_text?: string | null;
  explanation?: string | null;
  reference_answer?: string | null;
  last_attempt_at?: string;
  attempt_count?: number;
  /** 该题答案此前是否已被显式查看过（查看后后端会直接带上答案） */
  revealed?: boolean;
  /** 本次响应是否已包含答案与解析 */
  answers_included?: boolean;
  /** AI 判分简答题的评判理由（不泄露标准答案，可随列表下发） */
  ai_feedback?: string | null;
}

export interface ReviewTask {
  id: string;
  subject_id: string;
  knowledge_point_id: string;
  knowledge_point_name: string;
  due_date: string;
  status: string;
  reason?: string | null;
  merged_count?: number;
  created_at?: string;
  completed_at?: string | null;
}

export interface MasteryPoint {
  knowledge_point: string;
  knowledge_point_id?: string | null;
  mastery_status: 'pending' | 'estimated' | string;
  /** 待评估时为 null —— 前端必须显示"待评估"而不是 0 */
  mastery_level: number | null;
  evidence_count: number;
  independent_correct_count: number;
  independent_wrong_count: number;
  behavior_count: number;
  reason?: string | null;
  last_evidence_at?: string | null;
  last_behavior_at?: string | null;
}

export interface LearningHome {
  today_tasks: StudentTask[];
  overdue_tasks: StudentTask[];
  todo_count: number;
  pending_reviews: ReviewTask[];
  wrong_questions: WrongQuestion[];
  wrong_count: number;
  recent_attempts: any[];
  quiz_stats: {
    total_attempts: number;
    correct: number;
    wrong: number;
    pending_self_eval: number;
    accuracy: number | null;
  };
  last_position: {
    knowledge_point?: string | null;
    knowledge_point_id?: string | null;
    question_id?: string | null;
    at?: string | null;
  } | null;
}

export interface LearningEvent {
  event_id: string;
  event_type: string;
  subject_id?: string | null;
  status: 'PENDING' | 'DONE' | 'FAILED';
  attempts: number;
  last_error?: string | null;
  payload: any;
  created_at: string;
  updated_at: string;
}

// ============ 学生首页与任务 ============

export const getHome = async (subjectId?: string): Promise<ApiResponse<LearningHome>> => {
  return apiClient.get('/student/home', { params: subjectId ? { subject_id: subjectId } : {} });
};

/** 任务视图：不传 bucket 返回四类聚合 */
export const getTasks = async (params?: { subject_id?: string; bucket?: TaskBucket })
  : Promise<ApiResponse<TaskBuckets | StudentTask[]>> => {
  return apiClient.get('/student/tasks', { params: params || {} });
};

/** 完成任务（幂等）；测验任务需先有真实作答 */
export const completeTask = async (taskId: string, actualMinutes?: number)
  : Promise<ApiResponse<any>> => {
  return apiClient.post(`/student/tasks/${taskId}/complete`,
    { actual_minutes: actualMinutes ?? null });
};

/** 调整任务计划日期（保留原日期与调整记录） */
export const rescheduleTask = async (taskId: string, newDate: string, reason?: string)
  : Promise<ApiResponse<StudentTask>> => {
  return apiClient.put(`/student/tasks/${taskId}/schedule`,
    { new_date: newDate, reason: reason || undefined });
};

export const getTaskAdjustments = async (taskId: string): Promise<ApiResponse<any[]>> => {
  return apiClient.get(`/student/tasks/${taskId}/adjustments`);
};

/** 掌握情况（无证据的知识点为"待评估"，mastery_level 为 null） */
export const getMastery = async (subjectId: string)
  : Promise<ApiResponse<{ summary: any; points: MasteryPoint[] }>> => {
  return apiClient.get('/student/mastery', { params: { subject_id: subjectId } });
};

// ============ 自测作答 ============

/**
 * 提交作答：只提交所选项 ID，对错由后端依据题库标准答案判定。
 * client_token 用于幂等（同一题同一次提交重复请求只计一次）。
 */
export const submitAnswer = async (payload: {
  question_id: string;
  user_answer?: string | null;
  subject_id?: string;
  client_token?: string;
  source_task_id?: string;
  conversation_id?: string;
  duration_sec?: number;
  used_hint?: boolean;
}): Promise<ApiResponse<AttemptResult>> => {
  return apiClient.post('/quiz/answer', payload);
};

/** 查看答案（此后该题的作答不计入独立掌握证据） */
export const revealAnswer = async (questionId: string)
  : Promise<ApiResponse<{
    question_id: string;
    correct_option_id?: string | null;
    reference_answer?: string | null;
    explanation?: string | null;
  }>> => {
  return apiClient.post(`/quiz/reveal/${questionId}`);
};

/** 简答题自评（明确标记为自评，不伪装客观判分） */
export const selfEvaluate = async (attemptId: string, isCorrect: boolean)
  : Promise<ApiResponse<any>> => {
  return apiClient.post('/quiz/self-evaluate', { attempt_id: attemptId, is_correct: isCorrect });
};

export const getAttempts = async (params?: { subject_id?: string; question_id?: string; limit?: number })
  : Promise<ApiResponse<any[]>> => {
  return apiClient.get('/quiz/attempts', { params: params || {} });
};

export const getWrongQuestions = async (subjectId?: string): Promise<ApiResponse<WrongQuestion[]>> => {
  return apiClient.get('/quiz/wrong', { params: subjectId ? { subject_id: subjectId } : {} });
};

/** 错题答案与解析。注意：后端会登记"已查看答案"，
 *  该题之后的作答将记为 after_reveal、不计入独立掌握证据。 */
export const getWrongQuestionDetail = async (questionId: string)
  : Promise<ApiResponse<{
    question_id: string;
    stem: string;
    question_type?: string;
    options?: QuizOption[];
    knowledge_point?: string | null;
    user_answer?: string | null;
    user_answer_text?: string | null;
    correct_option_id?: string | null;
    correct_option_text?: string | null;
    reference_answer?: string | null;
    explanation?: string | null;
    revealed: boolean;
    reveal_recorded: boolean;
    note?: string;
  }>> => {
  return apiClient.get(`/quiz/wrong/${questionId}`);
};

export const getQuizStats = async (subjectId?: string): Promise<ApiResponse<any>> => {
  return apiClient.get('/quiz/stats', { params: subjectId ? { subject_id: subjectId } : {} });
};

export const getReviews = async (params?: { subject_id?: string; status?: string })
  : Promise<ApiResponse<ReviewTask[]>> => {
  return apiClient.get('/quiz/reviews', { params: params || {} });
};

// ============ 联动事件（可追溯 / 失败重试） ============

export const getLearningEvents = async (params?: { subject_id?: string; status?: string; limit?: number })
  : Promise<ApiResponse<LearningEvent[]>> => {
  return apiClient.get('/student/learning-events', { params: params || {} });
};

export const retryLearningEvents = async (limit = 50): Promise<ApiResponse<any>> => {
  return apiClient.post('/student/learning-events/retry', null, { params: { limit } });
};
