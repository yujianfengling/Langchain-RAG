import apiClient from './axios';
import { ApiResponse } from '../types';

// ============ 学习记忆（阶段二） ============

export interface MemoryItem {
  id: string;
  scope: string;
  memory_type: 'preference' | 'inference' | 'curriculum';
  type_label?: string;
  key: string;
  value: string;
  source: 'explicit_user' | 'inferred' | 'teacher' | 'curriculum';
  confidence?: number | null;
  status: string;
  evidence?: Record<string, any>;
  note?: string | null;
  created_at: string;
  updated_at: string;
  expires_at?: string | null;
  is_expired?: boolean;
}

export const getMemory = async (subjectId?: string, includeInactive = false)
  : Promise<ApiResponse<MemoryItem[]>> => {
  return apiClient.get('/memory', {
    params: { subject_id: subjectId, include_inactive: includeInactive },
  });
};

export const getEffectiveMemory = async (subjectId?: string)
  : Promise<ApiResponse<{ items: MemoryItem[]; shadowed: MemoryItem[]; count: number }>> => {
  return apiClient.get('/memory/effective', { params: { subject_id: subjectId } });
};

export const createMemory = async (data: { key: string; value: string; scope?: string; note?: string })
  : Promise<ApiResponse<MemoryItem>> => {
  return apiClient.post('/memory', data);
};

export const correctMemory = async (id: string, value: string, reason?: string)
  : Promise<ApiResponse<MemoryItem>> => {
  return apiClient.put(`/memory/${id}`, { value, reason });
};

export const setMemoryStatus = async (id: string, status: 'active' | 'disabled', reason?: string)
  : Promise<ApiResponse<MemoryItem>> => {
  return apiClient.post(`/memory/${id}/status`, { status, reason });
};

export const deleteMemory = async (id: string): Promise<ApiResponse<null>> => {
  return apiClient.delete(`/memory/${id}`);
};

export const getMemoryHistory = async (id: string): Promise<ApiResponse<any[]>> => {
  return apiClient.get(`/memory/${id}/history`);
};

export const refreshInference = async (subjectId: string): Promise<ApiResponse<any[]>> => {
  return apiClient.post('/memory/infer', null, { params: { subject_id: subjectId } });
};

// ============ 计划约束与调整（阶段二） ============

export interface PlanConstraints {
  user_id: string;
  subject_id: string;
  deadline?: string | null;
  daily_minutes: number;
  teacher_deadline?: string | null;
  effective_deadline?: string | null;
  auto_adjust: boolean;
  configured: boolean;
}

export interface PlanBudget {
  daily_minutes: number;
  deadline?: string | null;
  teacher_deadline?: string | null;
  days_left?: number | null;
  remaining_tasks: number;
  remaining_minutes: number;
  capacity_minutes?: number | null;
  fits: boolean;
  conflicts: string[];
  suggestions: string[];
  overdue_tasks: number;
}

export interface PlanAdjustment {
  id: string;
  subject_id: string;
  plan_id?: string | null;
  source: string;
  reason?: string | null;
  summary?: string | null;
  changes: any[];
  status: 'PROPOSED' | 'APPLIED' | 'UNDONE' | 'REJECTED';
  major: boolean;
  requires_confirmation: boolean;
  created_at: string;
  applied_at?: string | null;
  undone_at?: string | null;
  duplicate?: boolean;
  applied_changes?: any[];
  skipped_changes?: any[];
}

export const getConstraints = async (subjectId: string): Promise<ApiResponse<PlanConstraints>> => {
  return apiClient.get('/plan/constraints', { params: { subject_id: subjectId } });
};

export const saveConstraints = async (data: {
  subject_id: string;
  deadline?: string | null;
  daily_minutes?: number | null;
  teacher_deadline?: string | null;
  auto_adjust?: boolean | null;
}): Promise<ApiResponse<PlanConstraints>> => {
  return apiClient.put('/plan/constraints', data);
};

export const getBudget = async (subjectId: string, planId?: string): Promise<ApiResponse<PlanBudget>> => {
  return apiClient.get('/plan/budget', {
    params: { subject_id: subjectId, ...(planId ? { plan_id: planId } : {}) },
  });
};

export const proposeAdjustment = async (data: {
  subject_id: string; plan_id?: string; reason?: string; idempotency_key?: string;
}): Promise<ApiResponse<PlanAdjustment>> => {
  return apiClient.post('/plan/adjustments/propose', data);
};

export const listAdjustments = async (subjectId?: string, status?: string)
  : Promise<ApiResponse<PlanAdjustment[]>> => {
  return apiClient.get('/plan/adjustments', {
    params: { subject_id: subjectId, status },
  });
};

export const applyAdjustment = async (id: string, confirm = false)
  : Promise<ApiResponse<PlanAdjustment>> => {
  return apiClient.post(`/plan/adjustments/${id}/apply`, { confirm });
};

export const undoAdjustment = async (id: string): Promise<ApiResponse<PlanAdjustment>> => {
  return apiClient.post(`/plan/adjustments/${id}/undo`);
};

export const rejectAdjustment = async (id: string): Promise<ApiResponse<PlanAdjustment>> => {
  return apiClient.post(`/plan/adjustments/${id}/reject`);
};
