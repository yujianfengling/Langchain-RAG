import apiClient from './axios';
import { ApiResponse, CurrentModel, ModelInfo } from '../types';

/**
 * 获取当前模型信息
 */
export const getCurrentModel = async (): Promise<ApiResponse<CurrentModel>> => {
  return apiClient.get('/models');
};

/**
 * 获取模型状态
 */
export const getModelStatus = async (): Promise<ApiResponse<any>> => {
  return apiClient.get('/models/status');
};

/**
 * 获取可用模型列表
 */
export const getAvailableModels = async (): Promise<ApiResponse<ModelInfo[]>> => {
  return apiClient.get('/models/available');
};

/**
 * 切换模型
 */
export const switchModel = async (provider: string): Promise<ApiResponse<any>> => {
  return apiClient.post('/models/switch', { provider });
};