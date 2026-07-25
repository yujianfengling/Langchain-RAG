import apiClient from './axios';
import { ApiResponse, KnowledgeBase, KnowledgeBuildRequest } from '../types';

/**
 * 获取知识库列表
 */
export const getKnowledgeBases = async (): Promise<ApiResponse<{ subjects: KnowledgeBase[] }>> => {
  return apiClient.get('/knowledge');
};

/**
 * 获取知识库统计信息
 */
export const getKnowledgeStats = async (subjectId: string): Promise<ApiResponse<KnowledgeBase>> => {
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
 */
export const getDocuments = async (subjectId: string): Promise<ApiResponse<{ subject_id: string; documents: any[] }>> => {
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