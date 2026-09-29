import apiClient from './axios';
import { ApiResponse } from '../types';

// ============ 教师端接口 ============

/** 学生信息摘要 */
export interface StudentSummary {
  user_id: string;
  learning_style: string;
  interaction_count: number;
  knowledge_point_count: number;
  average_mastery: number;
  strong_count: number;
  weak_count: number;
  last_active: string;
  created_at: string;
}

/** 学生学情总览 */
export interface StudentOverview {
  user_id: string;
  subjects: string[];
  current_subject?: string;
  weak_points: any[];
  strong_points: any[];
  learning_stats: any;
  knowledge_gaps: any[];
  feedback: any;
}

/** 班级概览 */
export interface ClassOverview {
  total_students: number;
  total_interactions: number;
  total_knowledge_points: number;
  total_weak_points: number;
  total_strong_points: number;
  average_mastery: number;
}

/**
 * 获取所有学生列表
 */
export const getStudents = async (): Promise<ApiResponse<{ students: StudentSummary[] }>> => {
  return apiClient.get('/teacher/students');
};

/**
 * 获取单个学生学情总览
 */
export const getStudentOverview = async (userId: string, subjectId?: string): Promise<ApiResponse<StudentOverview>> => {
  const params = subjectId ? `?subject_id=${encodeURIComponent(subjectId)}` : '';
  return apiClient.get(`/teacher/students/${encodeURIComponent(userId)}/overview${params}`);
};

/**
 * 获取班级整体概览
 */
export const getClassOverview = async (): Promise<ApiResponse<ClassOverview>> => {
  return apiClient.get('/teacher/class/overview');
};
