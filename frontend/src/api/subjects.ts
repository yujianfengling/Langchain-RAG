import apiClient from './axios';
import { ApiResponse, Subject } from '../types';

/**
 * 获取学科列表
 */
export const getSubjects = async (): Promise<Subject[]> => {
  return apiClient.get('/subjects');
};

/**
 * 获取单个学科信息
 */
export const getSubject = async (subjectId: string): Promise<Subject> => {
  return apiClient.get(`/subjects/${subjectId}`);
};

/**
 * 创建新学科
 */
export const createSubject = async (subjectId: string, name: string, description?: string): Promise<Subject> => {
  return apiClient.post('/subjects', null, {
    params: { subject_id: subjectId, name, description }
  });
};

/**
 * 删除学科
 */
export const deleteSubject = async (subjectId: string): Promise<ApiResponse<any>> => {
  return apiClient.delete(`/subjects/${subjectId}`);
};