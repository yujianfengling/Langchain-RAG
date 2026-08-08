import apiClient from './axios';
import { ApiResponse, KnowledgeBase, KnowledgeBuildRequest } from '../types';

/**
 * 获取知识库列表
 * 后端返回: {subjects: [...]}（无success/data包装）
 */
export const getKnowledgeBases = async (): Promise<{ subjects: KnowledgeBase[] }> => {
  return apiClient.get('/knowledge');
};

/**
 * 获取知识库统计信息
 * 后端返回: {subject_id, status, stats}（无success/data包装）
 */
export const getKnowledgeStats = async (subjectId: string): Promise<KnowledgeBase> => {
  return apiClient.get(`/knowledge/${subjectId}/stats`);
};

/**
 * 构建知识库
 */
export const buildKnowledge = async (request: KnowledgeBuildRequest): Promise<ApiResponse<any>> => {
  return apiClient.post('/knowledge/build', request);
};

/**
 * 删除知识库
 */
export const deleteKnowledge = async (subjectId: string): Promise<ApiResponse<any>> => {
  return apiClient.delete(`/knowledge/${subjectId}`);
};

/**
 * 上传文件到知识库
 */
export const uploadFile = async (subjectId: string, file: File): Promise<ApiResponse<any>> => {
  const formData = new FormData();
  formData.append('subject_id', subjectId);
  formData.append('file', file);
  
  return apiClient.post('/knowledge/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
};

/**
 * 获取学科文档列表
 * 后端返回: {subject_id, documents}（无success/data包装）
 */
export const getDocuments = async (subjectId: string): Promise<{ subject_id: string; documents: any[] }> => {
  return apiClient.get(`/knowledge/${subjectId}/documents`);
};

/**
 * 删除学科文档
 */
export const deleteDocument = async (subjectId: string, filename: string): Promise<ApiResponse<any>> => {
  return apiClient.delete(`/knowledge/${subjectId}/documents/${filename}`);
};

/**
 * 重新构建知识库
 */
export const rebuildKnowledge = async (subjectId: string): Promise<ApiResponse<any>> => {
  return apiClient.post(`/knowledge/${subjectId}/rebuild`);
};

/**
 * 获取知识图谱
 */
export const getKnowledgeGraph = async (subjectId: string): Promise<ApiResponse<any>> => {
  return apiClient.get(`/knowledge/${subjectId}/graph`);
};

/**
 * 提交知识图谱构建任务（后台异步，返回 202 Accepted）
 */
export const buildKnowledgeGraph = async (subjectId: string): Promise<ApiResponse<any>> => {
  return apiClient.post(`/knowledge/${subjectId}/graph/build`);
};

/**
 * 查询知识图谱构建任务状态（idle/queued/building/success/failed）
 */
export const getKnowledgeGraphStatus = async (subjectId: string): Promise<ApiResponse<any>> => {
  return apiClient.get(`/knowledge/${subjectId}/graph/status`);
};

/**
 * 获取图谱列表
 */
export const getKnowledgeGraphs = async (): Promise<ApiResponse<{ graphs: any[] }>> => {
  return apiClient.get('/knowledge/graphs');
};