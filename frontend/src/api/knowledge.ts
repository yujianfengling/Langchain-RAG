import apiClient, { LONG_TASK_TIMEOUT } from './axios';
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
  // 建库要分块 + 向量化 + 建索引，大库远超默认 180s
  return apiClient.post('/knowledge/build', request, { timeout: LONG_TASK_TIMEOUT });
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
    timeout: LONG_TASK_TIMEOUT,
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
 * 建立页面级索引（图片型课件检索兜底；PDF/PPT）。
 * 后台异步执行，立即返回 queued；进度轮询 getPageIndexStatus。
 * 继续处理超上限的剩余页：传 docId + pageFrom。
 */
export const buildPageIndex = async (
  subjectId: string,
  opts: { force?: boolean; docId?: string; pageFrom?: number } = {},
): Promise<ApiResponse<any>> => {
  return apiClient.post(`/knowledge/${subjectId}/pages/build`, null, {
    params: {
      force: opts.force ?? false,
      doc_id: opts.docId || undefined,
      page_from: opts.pageFrom ?? 1,
      background: true,
    },
    timeout: LONG_TASK_TIMEOUT,
  });
};

/** 页面索引状态：已建页数、每份资料覆盖范围、正在进行的任务与页级进度 */
export const getPageIndexStatus = async (subjectId: string): Promise<ApiResponse<any>> => {
  return apiClient.get(`/knowledge/${subjectId}/pages/status`);
};

/** 某份资料已建立的页面列表（页面图地址由服务端返回，前端不拼 doc_id） */
export const getDocumentPages = async (
  subjectId: string, filename: string,
): Promise<ApiResponse<any>> => {
  return apiClient.get(`/knowledge/${subjectId}/documents/${encodeURIComponent(filename)}/pages`);
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