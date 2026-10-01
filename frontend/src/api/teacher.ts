import apiClient from './axios';
import { ApiResponse } from '../types';

/**
 * 教师端接口封装（2026-09-26）
 *
 * 说明：
 * - 权限**全部由后端判定**，前端这里的任何判断只影响展示，不构成安全边界。
 * - 后端失败返回非 2xx，`error.response.data.message` 是可直接展示的中文原因，
 *   `error.response.data.error_type` 为机器可读类型（forbidden / not_found / conflict 等）。
 * - axios 拦截器已把 `response.data` 拆出来，因此这里的返回值就是
 *   `{ success, data, message }` 结构。
 */

/** ==================== 通用 ==================== */

export type MaterialVisibility = 'students' | 'teacher_only';
export type AssignmentType = 'quiz' | 'task';
export type PlanStatus = 'draft' | 'published';

/** 统一的错误描述（不吞掉后端给的中文原因） */
export interface ApiFailure {
  status: number | null;
  message: string;
  errorType: string | null;
}

export const describeTeacherError = (e: unknown, fallback = '请求失败，请稍后重试'): ApiFailure => {
  const err = e as {
    response?: { status?: number; data?: { message?: string; error_type?: string } };
    message?: string;
  };
  return {
    status: err?.response?.status ?? null,
    message: err?.response?.data?.message || err?.message || fallback,
    errorType: err?.response?.data?.error_type ?? null,
  };
};

/** 是否为权限不足（403，例如"你不是该课程的授课教师"） */
export const isForbiddenError = (e: unknown): boolean => describeTeacherError(e).status === 403;

/**
 * 下载类接口（responseType: 'blob'）失败时，错误体也是 Blob，
 * 需要先读文本再解析，才能拿到后端的中文原因（而不是笼统的 status code）。
 */
export const describeBlobError = async (e: unknown, fallback = '请求失败，请稍后重试'): Promise<string> => {
  const data = (e as { response?: { data?: unknown } })?.response?.data;
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text()) as { message?: string };
      if (parsed?.message) return parsed.message;
    } catch {
      /* 非 JSON：退回通用描述 */
    }
  }
  return describeTeacherError(e, fallback).message;
};

/** ==================== 工作台 ==================== */

export type DashboardCourse = TeacherCourse;

export interface PendingLessonPlan {
  plan_id: string;
  title: string;
  course_id: string;
  course_title: string;
  updated_at: string;
}

export interface PublishedAssignmentBrief {
  assignment_id: string;
  title: string;
  course_id: string;
  course_title: string;
  class_name: string | null;
  due_date: string | null;
  published_at: string | null;
}

export interface FocusKnowledgePoint {
  class_id: string;
  class_name: string;
  course_id: string;
  course_title: string;
  knowledge_point_id: string | null;
  knowledge_point: string | null;
  wrong_count: number | null;
  attempt_count: number | null;
  wrong_rate: number | null;
  enough_data: boolean | null;
  range?: { start: string; end: string };
}

export interface TeacherDashboard {
  role: string;
  courses: DashboardCourse[];
  pending_lesson_plans: PendingLessonPlan[];
  published_assignments: PublishedAssignmentBrief[];
  focus_knowledge_points: FocusKnowledgePoint[];
}

export const getTeacherDashboard = async (): Promise<ApiResponse<TeacherDashboard>> =>
  apiClient.get('/teacher/dashboard');

/** ==================== 课程 ==================== */

export interface TeacherCourse {
  id: string;
  title: string;
  description?: string | null;
  subject_id: string;
  status?: string;
  owner_user_id?: string;
  created_at?: string;
  updated_at?: string;
  class_count?: number;
  student_count?: number;
  material_count?: number;
  draft_plan_count?: number;
  published_plan_count?: number;
  draft_assignment_count?: number;
  published_assignment_count?: number;
}

export interface KnowledgeBaseStatus {
  subject_id: string;
  /** false 时表示知识库不可用，界面必须展示 error 原因 */
  available: boolean;
  stats?: Record<string, unknown> | null;
  document_count: number;
  error?: string | null;
}

export interface TeacherClass {
  id: string;
  course_id: string;
  name: string;
  join_code: string;
  status: string;
  member_count?: number;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
}

export interface CourseMaterial {
  id: string;
  course_id?: string;
  subject_id?: string;
  filename: string;
  size_bytes: number | null;
  status: string;
  error_message?: string | null;
  visibility: MaterialVisibility | string;
  uploaded_by?: string;
  created_at?: string;
  updated_at?: string;
}

export interface CourseCounts {
  classes: number;
  students: number;
  materials: number;
  draft_plans: number;
  published_plans: number;
  draft_assignments: number;
  published_assignments: number;
}

export interface TeacherCourseDetail extends TeacherCourse {
  my_role: string;
  classes: TeacherClass[];
  materials: CourseMaterial[];
  lesson_plans: LessonPlan[];
  assignments: Assignment[];
  knowledge_base: KnowledgeBaseStatus;
  counts: CourseCounts;
}

export interface CreateCourseRequest {
  title: string;
  description?: string;
  subject_name?: string;
}

export const createCourse = async (data: CreateCourseRequest): Promise<ApiResponse<TeacherCourse>> =>
  apiClient.post('/teacher/courses', data);

export const listTeacherCourses = async (): Promise<ApiResponse<{ courses: TeacherCourse[] }>> =>
  apiClient.get('/teacher/courses');

export const getCourseDetail = async (courseId: string): Promise<ApiResponse<TeacherCourseDetail>> =>
  apiClient.get(`/teacher/courses/${courseId}`);

export const updateCourse = async (
  courseId: string,
  data: { title?: string; description?: string; status?: 'active' | 'archived' },
): Promise<ApiResponse<TeacherCourse>> => apiClient.patch(`/teacher/courses/${courseId}`, data);

/** ==================== 班级与成员 ==================== */

export interface ClassMember {
  member_id: string;
  user_id: string;
  display_name: string;
  username?: string | null;
  status: 'active' | 'left' | 'removed' | string;
  joined_at: string;
  left_at?: string | null;
  removed_at?: string | null;
}

export interface MemberEvent {
  event_type: string;
  user_id: string;
  created_at: string;
  detail?: Record<string, unknown> | null;
}

export interface ClassMembersResult {
  class: TeacherClass;
  members: ClassMember[];
  active_count: number;
  events: MemberEvent[];
}

export const createClass = async (courseId: string, name: string): Promise<ApiResponse<TeacherClass>> =>
  apiClient.post(`/teacher/courses/${courseId}/classes`, { name });

export const listClasses = async (courseId: string): Promise<ApiResponse<{ classes: TeacherClass[] }>> =>
  apiClient.get(`/teacher/courses/${courseId}/classes`);

export const listClassMembers = async (classId: string): Promise<ApiResponse<ClassMembersResult>> =>
  apiClient.get(`/teacher/classes/${classId}/members`);

/** 重置邀请码（旧码立即失效） */
export const resetClassJoinCode = async (classId: string): Promise<ApiResponse<TeacherClass>> =>
  apiClient.post(`/teacher/classes/${classId}/reset-code`);

/** 把学生移出班级（保留历史记录） */
export const removeClassMember = async (
  classId: string,
  memberId: string,
  reason?: string,
): Promise<ApiResponse<ClassMember>> =>
  apiClient.post(`/teacher/classes/${classId}/members/${memberId}/remove`, null, {
    params: reason ? { reason } : {},
  });

/** ==================== 课程资料 ==================== */

export const uploadCourseMaterial = async (
  courseId: string,
  file: File,
  visibility: MaterialVisibility = 'students',
): Promise<ApiResponse<CourseMaterial & {
  kb: KnowledgeBaseStatus;
  /** 上传后自动构建知识库的结果（attempted=false 表示未触发；失败必须如实展示） */
  kb_rebuild?: { attempted: boolean; ok: boolean | null; error: string | null };
}>> => {
  const form = new FormData();
  form.append('file', file);
  return apiClient.post(`/teacher/courses/${courseId}/materials`, form, {
    params: { visibility },
    // 必须显式声明：axios 实例默认 Content-Type 为 application/json，
    // 会把 FormData 序列化成 JSON；声明为 multipart 后由浏览器补 boundary。
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

export interface MaterialListResult {
  materials: CourseMaterial[];
  knowledge_base: KnowledgeBaseStatus;
}

export const listCourseMaterials = async (courseId: string): Promise<ApiResponse<MaterialListResult>> =>
  apiClient.get(`/teacher/courses/${courseId}/materials`);

export interface DeleteMaterialResult {
  material_id: string;
  deleted_file?: boolean;
  file_error?: string;
  rebuild?: Record<string, unknown> | null;
  rebuild_error?: string;
}

export const deleteCourseMaterial = async (
  materialId: string,
  courseId: string,
): Promise<ApiResponse<DeleteMaterialResult>> =>
  apiClient.delete(`/teacher/materials/${materialId}`, { params: { course_id: courseId } });

export const setMaterialVisibility = async (
  materialId: string,
  courseId: string,
  visibility: MaterialVisibility,
): Promise<ApiResponse<CourseMaterial>> =>
  apiClient.post(`/teacher/materials/${materialId}/visibility`, { visibility }, {
    params: { course_id: courseId },
  });

export interface RebuildKnowledgeResult {
  /** false 表示重建失败，必须把 error 如实展示给教师 */
  ok: boolean;
  result?: Record<string, unknown> | null;
  error?: string | null;
  kb: KnowledgeBaseStatus;
}

export const rebuildCourseKnowledge = async (
  courseId: string,
): Promise<ApiResponse<RebuildKnowledgeResult>> =>
  apiClient.post(`/teacher/courses/${courseId}/knowledge/rebuild`);

/** ==================== 教案 ==================== */

export interface PlanStep {
  name: string;
  minutes: number | null;
  content: string;
  materials?: string[];
}

export interface PlanQA {
  question: string;
  answer: string;
}

export interface LessonPlanContent {
  title: string;
  objectives: string[];
  key_points: string[];
  difficulties: string[];
  steps: PlanStep[];
  examples: PlanQA[];
  homework: PlanQA[];
  evidence_note: string;
}

export interface PlanEvidence {
  file_name?: string | null;
  page?: number | null;
  type?: string | null;
  source?: string | null;
}

export interface PlanClassSummary {
  available: boolean;
  reason?: string | null;
  used?: boolean;
}

export interface LessonPlan {
  id: string;
  course_id: string;
  class_id?: string | null;
  title: string;
  topic?: string | null;
  hours?: number | null;
  status: PlanStatus | string;
  current_version?: number;
  published_version?: number | null;
  published_at?: string | null;
  /** 当前读取到的版本号 */
  version?: number;
  version_count?: number;
  version_note?: string | null;
  version_created_at?: string | null;
  content: LessonPlanContent;
  evidence?: PlanEvidence[];
  ai_generated?: boolean;
  ai_model?: string | null;
  /** true 表示生成时课程资料不足（界面必须明确提示） */
  insufficient_evidence?: boolean;
  created_at?: string;
  updated_at?: string;
  class_summary?: PlanClassSummary;
}

export interface PlanVersion {
  version: number;
  ai_generated: boolean;
  ai_model?: string | null;
  insufficient_evidence: boolean;
  note?: string | null;
  edited_by?: string | null;
  created_at: string;
  content: LessonPlanContent;
  evidence: PlanEvidence[];
}

export interface GeneratePlanRequest {
  topic: string;
  hours?: number;
  objectives?: string;
  class_id?: string;
  material_ids?: string[];
  model?: string;
  title?: string;
}

export const generateLessonPlan = async (
  courseId: string,
  data: GeneratePlanRequest,
): Promise<ApiResponse<LessonPlan>> =>
  apiClient.post(`/teacher/courses/${courseId}/lesson-plans/generate`, data);

export const createLessonPlan = async (
  courseId: string,
  data: { title: string; topic?: string; hours?: number; class_id?: string; content?: Partial<LessonPlanContent> },
): Promise<ApiResponse<LessonPlan>> => apiClient.post(`/teacher/courses/${courseId}/lesson-plans`, data);

export const listLessonPlans = async (
  courseId: string,
  status?: PlanStatus,
): Promise<ApiResponse<{ lesson_plans: LessonPlan[] }>> =>
  apiClient.get(`/teacher/courses/${courseId}/lesson-plans`, { params: status ? { status } : {} });

export const getLessonPlan = async (
  planId: string,
  version?: number,
): Promise<ApiResponse<LessonPlan>> =>
  apiClient.get(`/teacher/lesson-plans/${planId}`, { params: version ? { version } : {} });

/** 保存为**新版本**（历史版本保留，不会被覆盖） */
export const updateLessonPlan = async (
  planId: string,
  data: {
    content: LessonPlanContent;
    title?: string;
    topic?: string;
    hours?: number;
    class_id?: string;
    note?: string;
  },
): Promise<ApiResponse<LessonPlan>> => apiClient.put(`/teacher/lesson-plans/${planId}`, data);

export const listLessonPlanVersions = async (
  planId: string,
): Promise<ApiResponse<{ plan: LessonPlan; versions: PlanVersion[] }>> =>
  apiClient.get(`/teacher/lesson-plans/${planId}/versions`);

export const publishLessonPlan = async (planId: string): Promise<ApiResponse<LessonPlan>> =>
  apiClient.post(`/teacher/lesson-plans/${planId}/publish`);

export const previewLessonPlan = async (
  planId: string,
  version?: number,
): Promise<ApiResponse<{ markdown: string; plan: LessonPlan }>> =>
  apiClient.get(`/teacher/lesson-plans/${planId}/preview`, {
    params: version ? { version } : {},
  });

/** 导出 Markdown 文件流（拦截器已把 response.data 拆出，这里直接是 Blob） */
export const exportLessonPlan = async (planId: string, version?: number): Promise<Blob> =>
  apiClient.get(`/teacher/lesson-plans/${planId}/export`, {
    params: version ? { version } : {},
    responseType: 'blob',
  }) as unknown as Promise<Blob>;

/** ==================== 作业 / 练习 ==================== */

export interface AssignmentQuestion {
  id: string;
  /** 后端公开题目字段：question（题干）/ type（题型） */
  question?: string;
  type?: string;
  /** 兼容 question_type / stem 命名 */
  question_type?: string;
  stem?: string;
  options?: { id: string; text: string }[];
  knowledge_point?: string | null;
  knowledge_point_id?: string | null;
}

export interface PublishStats {
  published: boolean;
  student_count?: number;
  task_completed_count?: number;
  answered_student_count?: number;
  question_count?: number;
}

export interface Assignment {
  id: string;
  course_id: string;
  class_id: string;
  title: string;
  description?: string | null;
  assign_type: AssignmentType | string;
  status: 'draft' | 'published' | 'closed' | string;
  due_date?: string | null;
  estimated_minutes?: number | null;
  knowledge_points?: string[];
  question_count?: number;
  questions?: AssignmentQuestion[];
  publish_stats?: PublishStats;
  created_at?: string;
  published_at?: string | null;
}

export interface CreateAssignmentRequest {
  class_id: string;
  title: string;
  description?: string;
  assign_type: AssignmentType;
  knowledge_points?: string[];
  due_date?: string;
  estimated_minutes?: number;
  question_ids?: string[];
}

export const createAssignment = async (
  courseId: string,
  data: CreateAssignmentRequest,
): Promise<ApiResponse<Assignment>> => apiClient.post(`/teacher/courses/${courseId}/assignments`, data);

export interface GenerateQuestionsResult {
  questions: AssignmentQuestion[];
  question_ids: string[];
  requested: number;
  generated: number;
  knowledge_point: string;
  evidence: PlanEvidence[];
  model: string;
}

/** AI 出题（返回的题目**不含标准答案**，这是刻意的） */
export const generateAssignmentQuestions = async (
  courseId: string,
  data: { assignment_id?: string; knowledge_points?: string[]; count: number; model?: string },
): Promise<ApiResponse<GenerateQuestionsResult>> =>
  apiClient.post(`/teacher/courses/${courseId}/assignments/generate-questions`, data);

export const listAssignments = async (
  courseId: string,
  classId?: string,
): Promise<ApiResponse<{ assignments: Assignment[] }>> =>
  apiClient.get(`/teacher/courses/${courseId}/assignments`, {
    params: classId ? { class_id: classId } : {},
  });

export const updateAssignment = async (
  assignmentId: string,
  data: Partial<Omit<CreateAssignmentRequest, 'class_id' | 'assign_type'>>,
): Promise<ApiResponse<Assignment>> => apiClient.put(`/teacher/assignments/${assignmentId}`, data);

export interface PublishAssignmentResult {
  assignment: Assignment;
  already_published: boolean;
  created: { user_id: string; task_id?: string }[];
  skipped: { user_id: string; task_id?: string }[];
  failed: { user_id: string; reason: string }[];
  student_count?: number;
}

export const publishAssignment = async (
  assignmentId: string,
): Promise<ApiResponse<PublishAssignmentResult>> =>
  apiClient.post(`/teacher/assignments/${assignmentId}/publish`);

export const closeAssignment = async (assignmentId: string): Promise<ApiResponse<Assignment>> =>
  apiClient.post(`/teacher/assignments/${assignmentId}/close`);

/** ==================== 学情分析 ==================== */

export interface AnalyticsRange {
  start: string;
  end: string;
}

export interface ParticipationStats {
  student_count: number;
  answered_student_count: number;
  attempt_count: number;
  graded_count?: number;
  attempt_student_ratio: number | null;
  source?: string;
}

export interface TaskCompletionStats {
  total: number;
  completed: number;
  ratio: number | null;
  source?: string;
}

export interface KnowledgePointStat {
  knowledge_point_id: string | null;
  knowledge_point: string;
  attempt_count: number;
  wrong_count: number;
  student_count: number;
  wrong_rate: number | null;
  enough_data: boolean;
  source?: string;
}

export interface WrongQuestionStat {
  question_id: string;
  stem?: string | null;
  /** 兼容 question 命名 */
  question?: string | null;
  question_type?: string | null;
  type?: string | null;
  options?: { id: string; text: string }[] | null;
  attempt_count: number;
  wrong_count: number;
  student_count?: number;
  wrong_rate: number | null;
  knowledge_point?: string | null;
}

export interface TrendPoint {
  date: string;
  attempt_count: number;
  wrong_count: number;
  student_count: number;
  wrong_rate: number | null;
}

export interface ClassAnalytics {
  class_id?: string;
  class_name: string;
  course_id?: string;
  course_title: string;
  range: AnalyticsRange;
  generated_at: string;
  sources?: string[];
  enough_data: boolean;
  insufficient_reason: string | null;
  participation: ParticipationStats;
  task_completion: TaskCompletionStats | null;
  knowledge_points: KnowledgePointStat[];
  weak_knowledge_points?: KnowledgePointStat[];
  wrong_questions?: WrongQuestionStat[];
  trend: TrendPoint[];
  thresholds?: { min_attempts: number; min_students: number };
}

export const getClassAnalytics = async (
  classId: string,
  days: number,
): Promise<ApiResponse<ClassAnalytics>> =>
  apiClient.get(`/teacher/classes/${classId}/analytics`, { params: { days } });

export interface KnowledgePointDetail {
  class_id: string;
  course_id: string;
  range: AnalyticsRange;
  knowledge_point: KnowledgePointStat;
  questions: {
    question_id: string;
    stem?: string | null;
    question?: string | null;
    options?: { id: string; text: string }[] | null;
    knowledge_point_name?: string | null;
    attempt_count: number;
    wrong_count: number;
  }[];
  materials: { material_id: string; filename: string; visibility: string; matched: boolean }[];
  enough_data: boolean;
  sources?: string[];
  next_actions: string[];
}

export const getKnowledgePointDetail = async (
  classId: string,
  kpId: string,
  days: number,
): Promise<ApiResponse<KnowledgePointDetail>> =>
  apiClient.get(`/teacher/classes/${classId}/analytics/knowledge-points/${kpId}`, {
    params: { days },
  });

export interface ExplainResult {
  enough_data: boolean;
  explanation: string | null;
  suggestions: string[];
  cautions: string[];
  model?: string;
  note?: string;
  reason?: string;
  overview?: ClassAnalytics;
}

/** AI 解读（样本不足时后端不会调用模型，explanation 为 null，界面需如实呈现） */
export const explainClassAnalytics = async (
  classId: string,
  days: number,
  model?: string,
): Promise<ApiResponse<ExplainResult>> =>
  apiClient.post(`/teacher/classes/${classId}/analytics/explain`, null, {
    params: model ? { days, model } : { days },
  });

/** ==================== 学生画像（并入组员实现，2026-09-27 合并）==================== */
/*
 * 来源：组员提交的教师端接口。合并时后端已补上鉴权与数据范围（只返回自己名下班级的在班学生），
 * 前端沿用其字段与函数名，便于他原有界面复用。
 */

/** 学生画像摘要 */
export interface StudentSummary {
  user_id: string;
  learning_style: string;
  interaction_count: number;
  knowledge_point_count: number;
  average_mastery: number;
  strong_count: number;
  weak_count: number;
  last_active: string;
  created_at?: string;
}

/** 单个学生学情总览 */
export interface StudentOverview {
  user_id: string;
  subjects: string[];
  current_subject?: string | null;
  weak_points: Record<string, unknown>[];
  strong_points: Record<string, unknown>[];
  learning_stats: Record<string, unknown>;
  knowledge_gaps: Record<string, unknown>[];
  feedback: Record<string, unknown>;
}

/** 名下学生的整体汇总 */
export interface ClassOverview {
  total_students: number;
  total_interactions: number;
  total_knowledge_points: number;
  total_weak_points: number;
  total_strong_points: number;
  average_mastery: number;
  /** 数据范围说明（后端固定为"自己名下班级的在班学生"） */
  scope?: string;
}

/** 我名下班级的学生画像列表 */
export const getStudents = async (): Promise<ApiResponse<{
  students: StudentSummary[]; scope?: string; total?: number;
}>> => apiClient.get('/teacher/students');

/** 单个学生的画像总览 */
export const getStudentOverview = async (
  userId: string, subjectId?: string,
): Promise<ApiResponse<StudentOverview>> =>
  apiClient.get(`/teacher/students/${encodeURIComponent(userId)}/overview`, {
    params: subjectId ? { subject_id: subjectId } : {},
  });

/** 我名下学生的整体汇总 */
export const getClassOverview = async (): Promise<ApiResponse<ClassOverview>> =>
  apiClient.get('/teacher/class/overview');
