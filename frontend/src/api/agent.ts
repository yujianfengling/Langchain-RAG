import apiClient from './axios';

// 说明（阶段一）：
// - 学习数据一律归属"后端当前用户"，请求体不再传 user_id
// - 用户域路径统一使用保留字 me（后端 api.deps.resolve_user_scope 解析为当前登录用户）
// - 统一走 apiClient，以便自动携带登录令牌
const ME = 'me';

// ============ 反馈接口 ============

export interface FeedbackRequest {
  /** 真实用户交互记录 ID（由后端回写到消息上），不要传 message_id */
  interaction_id?: string;
  feedback_type?: string;
  feedback_score?: number;
  feedback_detail?: string;
  correction_text?: string;
}

export async function submitFeedback(data: FeedbackRequest) {
  return apiClient.post('/agent/feedback', data);
}

export async function getFeedbackStats() {
  return apiClient.get(`/agent/feedback/stats/${ME}`);
}

// ============ 用户画像接口 ============

export async function getUserProfile() {
  return apiClient.get(`/agent/profile/${ME}`);
}

export async function updateKnowledgeLevel(subjectId: string, level: number) {
  return apiClient.post('/agent/knowledge-level', { subject_id: subjectId, level });
}

export async function updateLearningStyle(style: string) {
  return apiClient.post('/agent/learning-style', { style });
}

// ============ 知识点掌握接口 ============

export async function getWeakPoints(subjectId: string, threshold?: number) {
  return apiClient.get(`/agent/weak-points/${ME}/${subjectId}`,
    { params: threshold ? { threshold } : {} });
}

export async function getStrongPoints(subjectId: string, threshold?: number) {
  return apiClient.get(`/agent/strong-points/${ME}/${subjectId}`,
    { params: threshold ? { threshold } : {} });
}

// ============ 学习推荐接口 ============

export async function getRecommendations(subjectId?: string, currentTopic?: string) {
  const params: Record<string, string> = {};
  if (subjectId) params.subject_id = subjectId;
  if (currentTopic) params.current_topic = currentTopic;
  return apiClient.get(`/agent/recommendations/${ME}`, { params });
}

export async function getKnowledgeGaps(subjectId: string) {
  return apiClient.get(`/agent/knowledge-gaps/${ME}/${subjectId}`);
}

// ============ 学习偏好接口 ============

export async function setLearningPreference(preferenceType: string, preferenceValue: string) {
  return apiClient.post('/agent/preferences', {
    preference_type: preferenceType,
    preference_value: preferenceValue,
  });
}

export async function getLearningPreferences() {
  return apiClient.get(`/agent/preferences/${ME}`);
}

// ============ 分析接口 ============

export async function analyzePerformance(subjectId: string) {
  return apiClient.get(`/agent/analysis/${ME}/${subjectId}`);
}

// ============ 交互记录接口 ============

export interface RecordInteractionRequest {
  interaction_type: string;
  subject_id?: string;
  question?: string;
  answer?: string;
  context_used?: string;
  model_used?: string;
  latency?: number;
}

export async function recordInteraction(data: RecordInteractionRequest) {
  return apiClient.post('/agent/interactions', data);
}

export async function getInteractions(subjectId?: string,
                                      interactionType?: string,
                                      limit = 50) {
  const params: Record<string, any> = { limit };
  if (subjectId) params.subject_id = subjectId;
  if (interactionType) params.interaction_type = interactionType;
  return apiClient.get(`/agent/interactions/${ME}`, { params });
}

/**
 * 提交测验作答（服务端权威判分）
 *
 * 阶段一：不接受 is_correct —— 对错由后端依据题库标准答案判定；
 * 用户身份由登录令牌决定，前端不提交 user_id。
 */
export interface QuizAnswerRequest {
  question_id: string;
  user_answer?: string | null;
  subject_id?: string;
  client_token?: string;
  source_task_id?: string;
  conversation_id?: string;
  duration_sec?: number;
  used_hint?: boolean;
}

export async function submitQuizAnswer(data: QuizAnswerRequest) {
  return apiClient.post('/agent/quiz-answer', data);
}

// ============ 学习进度摘要接口 ============

export interface ProgressSummary {
  total_interactions: number;
  knowledge_points_count: number;
  mastery_distribution: {
    strong: number;
    medium: number;
    weak: number;
    /** 待评估（无独立作答证据） */
    pending?: number;
  };
  evidence_total?: number;
  accuracy?: number | null;
  recent_activity: any[];
  suggestions: string[];
  positive_rate: number;
}

export async function getProgressSummary(subjectId: string): Promise<{ data: ProgressSummary }> {
  return apiClient.get(`/agent/progress-summary/${ME}/${subjectId}`);
}

// ============ 学习路径接口 ============

export interface LearningPathItem {
  step: number;
  knowledge_point: string;
  current_mastery: number;
  target_mastery: number;
  suggested_activity: string;
  estimated_minutes: number;
  status: 'pending' | 'in_progress' | 'completed';
  priority: 'high' | 'medium' | 'low';
}

export async function getLearningPath(subjectId: string): Promise<{ data: LearningPathItem[] }> {
  return apiClient.get(`/agent/learning-path/${ME}/${subjectId}`);
}

export interface LearningStats {
  total_knowledge_points: number;
  mastery_distribution: {
    strong: number;
    medium: number;
    weak: number;
    /** 待评估（无独立作答证据），阶段一新增 */
    pending?: number;
  };
  average_mastery: number;
  total_study_time: number;
  learning_efficiency: number;
}

export async function getLearningStats(subjectId: string) {
  return apiClient.get(`/agent/learning-stats/${ME}/${subjectId}`);
}

// ============ 知识提取接口 ============

export interface ExtractKnowledgeRequest {
  subject_id: string;
  question: string;
  answer: string;
  feedback_score?: number;
}

export async function extractKnowledge(data: ExtractKnowledgeRequest) {
  return apiClient.post('/agent/extract-knowledge', data);
}

export async function validateKnowledge(knowledgeId: string) {
  return apiClient.post(`/agent/validate-knowledge/${knowledgeId}`);
}

export async function addKnowledgeToKB(knowledgeId: string, subjectId: string) {
  return apiClient.post(`/agent/add-knowledge-to-kb/${knowledgeId}`,
    null, { params: { subject_id: subjectId } });
}

export interface PendingKnowledge {
  id: string;
  subject_id: string;
  knowledge_point: string;
  content: string;
  confidence: number;
  status: string;
  created_at: string;
}

export async function getPendingKnowledge(subjectId?: string, limit?: number)
  : Promise<{ data: PendingKnowledge[] }> {
  const params: Record<string, any> = {};
  if (subjectId) params.subject_id = subjectId;
  if (limit) params.limit = limit;
  return apiClient.get('/agent/pending-knowledge', { params });
}

export interface ExtractionStats {
  total: number;
  pending: number;
  validated: number;
  added: number;
  rejected: number;
}

export async function getExtractionStats(subjectId?: string)
  : Promise<{ data: ExtractionStats | null }> {
  return apiClient.get('/agent/knowledge-extraction-stats',
    { params: subjectId ? { subject_id: subjectId } : {} });
}
