import apiClient from './axios';
import { ApiResponse } from '../types';

// 学习计划与任务类型
export type TaskType = 'READ' | 'REVIEW' | 'QUIZ' | 'PRACTICE';
export type TaskStatus = 'TODO' | 'IN_PROGRESS' | 'COMPLETED';
export type PlanStatus = 'ACTIVE' | 'COMPLETED' | 'ARCHIVED';

export interface LearningTask {
  id: string;
  plan_id: string;
  knowledge_point: string | null;
  task_type: TaskType;
  title: string;
  description: string | null;
  priority: number;
  estimated_minutes: number;
  status: TaskStatus;
  completed_at: string | null;
  actual_minutes: number | null;
  created_at: string;
}

export interface LearningPlan {
  id: string;
  subject_id: string;
  title: string;
  goal: string | null;
  start_date: string | null;
  end_date: string | null;
  status: PlanStatus;
  created_at: string;
  updated_at: string;
  tasks: LearningTask[];
}

// 生成计划请求
export interface GeneratePlanRequest {
  subject_id: string;
  goal: string;
}

/**
 * AI 生成学习计划
 */
export const generatePlan = async (data: GeneratePlanRequest): Promise<ApiResponse<LearningPlan>> => {
  return apiClient.post('/learning/plans/generate', data);
};

/**
 * 获取学习计划列表
 */
export const getPlans = async (subjectId?: string): Promise<ApiResponse<LearningPlan[]>> => {
  return apiClient.get('/learning/plans', { params: subjectId ? { subject_id: subjectId } : {} });
};

/**
 * 获取单个计划（含任务）
 */
export const getPlan = async (planId: string): Promise<ApiResponse<LearningPlan>> => {
  return apiClient.get(`/learning/plans/${planId}`);
};

/**
 * 删除计划
 */
export const deletePlan = async (planId: string): Promise<ApiResponse<any>> => {
  return apiClient.delete(`/learning/plans/${planId}`);
};

/**
 * 更新任务状态
 */
export const updateTaskStatus = async (taskId: string, status: TaskStatus): Promise<ApiResponse<LearningTask>> => {
  return apiClient.put(`/learning/tasks/${taskId}/status`, { status });
};
