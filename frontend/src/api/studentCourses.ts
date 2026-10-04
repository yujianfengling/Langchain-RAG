import apiClient from './axios';
import { ApiResponse } from '../types';

/**
 * 学生侧"我的课程"接口（教师端联动，2026-09-26）
 *
 * 后端权威：未加入的课程、未发布的作业一律拿不到（403/404）。
 */

export interface MyCourse {
  course_id: string;
  title: string;
  description?: string | null;
  subject_id: string;
  teacher_name: string;
  class_id: string;
  class_name: string;
  joined_at?: string | null;
  assignment_count?: number;
}

export interface QuestionOption {
  id: string;
  text: string;
}

export interface AssignmentQuestion {
  id: string;
  question_type?: string;
  type?: string;
  question?: string;
  stem?: string;
  options?: QuestionOption[];
  knowledge_point_name?: string;
  /** 依据来源与渠道（2026-10-04 起后端分别下发） */
  source?: string | null;
  channel?: string | null;
  channel_label?: string | null;
}

export interface StudentAssignment {
  id: string;
  title: string;
  description?: string | null;
  due_date?: string | null;
  status: 'published' | 'closed';
  course_id: string;
  course_title?: string | null;
  class_id: string;
  class_name?: string | null;
  knowledge_points?: string[];
  questions: AssignmentQuestion[];
  question_count?: number;
  submitted?: number;
  published_at?: string | null;
  /** 每题我的判分结果（question_id → 结果） */
  results?: Record<string, { is_correct: boolean; attempt_no: number; created_at: string }>;
}

export interface AssignmentDetail extends StudentAssignment {
  my_tasks?: Array<{ id: string; title: string; status: string; planned_date?: string | null }>;
}

/** 我加入的课程与班级 */
export const listMyCourses = async (): Promise<ApiResponse<{ courses: MyCourse[] }>> =>
  apiClient.get('/student/courses');

/** 用班级邀请码加入课程 */
export const joinCourseByCode = async (code: string): Promise<ApiResponse<any>> =>
  apiClient.post('/student/courses/join', { code });

/** 退出班级（保留历史记录） */
export const leaveCourse = async (classId: string): Promise<ApiResponse<any>> =>
  apiClient.post(`/student/classes/${classId}/leave`);

/** 老师布置给我的作业（只含已发布，不含答案） */
export const listMyAssignments = async (params?: {
  course_id?: string; class_id?: string;
}): Promise<ApiResponse<{ assignments: StudentAssignment[] }>> =>
  apiClient.get('/student/assignments', { params });

/** 打开一次作业（含题目，不含标准答案） */
export const getAssignment = async (assignmentId: string): Promise<ApiResponse<AssignmentDetail>> =>
  apiClient.get(`/student/assignments/${assignmentId}`);

/** 标记作业完成（题目都要作答过，否则后端会拒绝并给出原因） */
export const completeAssignment = async (assignmentId: string): Promise<ApiResponse<any>> =>
  apiClient.post(`/student/assignments/${assignmentId}/complete`);

/** 本课程已发布的教案（草稿不可见） */
export const listCourseLessonPlans = async (courseId: string): Promise<ApiResponse<any>> =>
  apiClient.get(`/student/courses/${courseId}/lesson-plans`);

/** 本课程对学生可见的资料 */
export const listCourseMaterials = async (courseId: string): Promise<ApiResponse<any>> =>
  apiClient.get(`/student/courses/${courseId}/materials`);

/** 从后端错误里取可展示的中文原因 */
export const describeCourseError = (error: any): string => {
  const data = error?.response?.data;
  if (data?.message) return data.message;
  if (data?.detail) return typeof data.detail === 'string' ? data.detail : '请求被拒绝';
  const status = error?.response?.status;
  if (status === 401) return '请先登录后再查看课程';
  if (status === 403) return '你没有权限查看该课程内容';
  if (status === 404) return '课程或作业不存在（可能尚未发布）';
  return error?.message || '请求失败，请稍后重试';
};
