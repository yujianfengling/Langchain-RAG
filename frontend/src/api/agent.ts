import axios from 'axios';

const API_BASE = '/api';

// ============ 反馈接口 ============

export interface FeedbackRequest {
  user_id?: string;
  interaction_id?: string;
  feedback_type?: string;
  feedback_score?: number;
  feedback_detail?: string;
  correction_text?: string;
}

export async function submitFeedback(data: FeedbackRequest) {
  const res = await axios.post(`${API_BASE}/agent/feedback`, data);
  return res.data;
}

export async function getFeedbackStats(userId: string) {
  const res = await axios.get(`${API_BASE}/agent/feedback/stats/${userId}`);
  return res.data;
}

// ============ 用户画像接口 ============

export async function getUserProfile(userId: string) {
  const res = await axios.get(`${API_BASE}/agent/profile/${userId}`);
  return res.data;
}

export async function updateKnowledgeLevel(userId: string, subjectId: string, level: number) {
  const res = await axios.post(`${API_BASE}/agent/knowledge-level`, {
    user_id: userId,
    subject_id: subjectId,
    level,
  });
  return res.data;
}

export async function updateLearningStyle(userId: string, style: string) {
  const res = await axios.post(`${API_BASE}/agent/learning-style`, {
    user_id: userId,
    style,
  });
  return res.data;
}

// ============ 知识点掌握接口 ============

export async function getWeakPoints(userId: string, subjectId: string, threshold?: number) {
  const params = threshold ? `?threshold=${threshold}` : '';
  const res = await axios.get(`${API_BASE}/agent/weak-points/${userId}/${subjectId}${params}`);
  return res.data;
}

export async function getStrongPoints(userId: string, subjectId: string, threshold?: number) {
  const params = threshold ? `?threshold=${threshold}` : '';
  const res = await axios.get(`${API_BASE}/agent/strong-points/${userId}/${subjectId}${params}`);
  return res.data;
}

// ============ 学习推荐接口 ============

export async function getRecommendations(userId: string, subjectId?: string, currentTopic?: string) {
  const params = new URLSearchParams();
  if (subjectId) params.append('subject_id', subjectId);
  if (currentTopic) params.append('current_topic', currentTopic);
  const query = params.toString() ? `?${params.toString()}` : '';
  const res = await axios.get(`${API_BASE}/agent/recommendations/${userId}${query}`);
  return res.data;
}

export async function getKnowledgeGaps(userId: string, subjectId: string) {
  const res = await axios.get(`${API_BASE}/agent/knowledge-gaps/${userId}/${subjectId}`);
  return res.data;
}

// ============ 学习偏好接口 ============

export async function setLearningPreference(userId: string, preferenceType: string, preferenceValue: string) {
  const res = await axios.post(`${API_BASE}/agent/preferences`, {
    user_id: userId,
    preference_type: preferenceType,
    preference_value: preferenceValue,
  });
  return res.data;
}

export async function getLearningPreferences(userId: string) {
  const res = await axios.get(`${API_BASE}/agent/preferences/${userId}`);
  return res.data;
}

// ============ 分析接口 ============

export async function analyzePerformance(userId: string, subjectId: string) {
  const res = await axios.get(`${API_BASE}/agent/analysis/${userId}/${subjectId}`);
  return res.data;
}

// ============ 交互记录接口 ============

export interface RecordInteractionRequest {
  user_id?: string;
  interaction_type: string;
  subject_id?: string;
  question?: string;
  answer?: string;
  context_used?: string;
  model_used?: string;
  latency?: number;
}

export async function recordInteraction(data: RecordInteractionRequest) {
  const res = await axios.post(`${API_BASE}/agent/interactions`, data);
  return res.data;
}

export async function getUserInteractions(userId: string, subjectId?: string, interactionType?: string, limit?: number) {
  const params = new URLSearchParams();
  if (subjectId) params.append('subject_id', subjectId);
  if (interactionType) params.append('interaction_type', interactionType);
  if (limit) params.append('limit', limit.toString());
  const query = params.toString() ? `?${params.toString()}` : '';
  const res = await axios.get(`${API_BASE}/agent/interactions/${userId}${query}`);
  return res.data;
}

// ============ 测验答案接口 ============

export interface QuizAnswerRequest {
  user_id?: string;
  subject_id: string;
  knowledge_point: string;
  is_correct: boolean;
  mastery_delta?: number;
}

export async function submitQuizAnswer(data: QuizAnswerRequest) {
  const res = await axios.post(`${API_BASE}/agent/quiz-answer`, data);
  return res.data;
}

// ============ 学习进度摘要接口 ============

export interface ProgressSummary {
  total_interactions: number;
  knowledge_points_count: number;
  mastery_distribution: {
    strong: number;
    medium: number;
    weak: number;
  };
  recent_activity: any[];
  suggestions: string[];
  positive_rate: number;
}

export async function getProgressSummary(userId: string, subjectId: string) {
  const res = await axios.get(`${API_BASE}/agent/progress-summary/${userId}/${subjectId}`);
  return res.data;
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

export async function getLearningPath(userId: string, subjectId: string) {
  const res = await axios.get(`${API_BASE}/agent/learning-path/${userId}/${subjectId}`);
  return res.data;
}

export interface LearningStats {
  total_knowledge_points: number;
  mastery_distribution: {
    strong: number;
    medium: number;
    weak: number;
  };
  average_mastery: number;
  total_study_time: number;
  learning_efficiency: number;
}

export async function getLearningStats(userId: string, subjectId: string) {
  const res = await axios.get(`${API_BASE}/agent/learning-stats/${userId}/${subjectId}`);
  return res.data;
}

// ============ 知识提取接口 ============

export interface ExtractKnowledgeRequest {
  user_id?: string;
  subject_id: string;
  question: string;
  answer: string;
  feedback_score?: number;
}

export async function extractKnowledge(data: ExtractKnowledgeRequest) {
  const res = await axios.post(`${API_BASE}/agent/extract-knowledge`, data);
  return res.data;
}

export async function validateKnowledge(knowledgeId: string) {
  const res = await axios.post(`${API_BASE}/agent/validate-knowledge/${knowledgeId}`);
  return res.data;
}

export async function addKnowledgeToKB(knowledgeId: string, subjectId: string) {
  const res = await axios.post(`${API_BASE}/agent/add-knowledge-to-kb/${knowledgeId}?subject_id=${subjectId}`);
  return res.data;
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

export async function getPendingKnowledge(subjectId?: string, limit?: number) {
  const params = new URLSearchParams();
  if (subjectId) params.append('subject_id', subjectId);
  if (limit) params.append('limit', limit.toString());
  const query = params.toString() ? `?${params.toString()}` : '';
  const res = await axios.get(`${API_BASE}/agent/pending-knowledge${query}`);
  return res.data;
}

export interface ExtractionStats {
  total: number;
  pending: number;
  validated: number;
  added: number;
  rejected: number;
}

export async function getExtractionStats(subjectId?: string) {
  const params = subjectId ? `?subject_id=${subjectId}` : '';
  const res = await axios.get(`${API_BASE}/agent/knowledge-extraction-stats${params}`);
  return res.data;
}
