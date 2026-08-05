import apiClient from './axios';
import { ApiResponse } from '../types';

// 教育功能API接口

// 知识点总结请求
export interface EducationSummaryRequest {
  text: string;
}

// 知识点总结响应（data字段的实际结构）
export interface EducationSummaryResponse {
  key_points: Array<{
    id: number;
    title: string;
    description: string;
  }>;
  summary: string;
  model: string;
}

// 题目解析请求
export interface EducationExplainRequest {
  question: string;
}

// 题目解析响应（data字段的实际结构）
export interface EducationExplainResponse {
  question: string;
  explanation: string;
  model: string;
}

// 学习建议请求
export interface EducationAdviceRequest {
  history?: Array<{
    question: string;
    [key: string]: any;
  }>;
}

// 学习建议响应（data字段的实际结构）
export interface EducationAdviceResponse {
  advice: string;
  model: string;
}

// 生成知识点总结
export const generateSummary = async (data: EducationSummaryRequest): Promise<ApiResponse<EducationSummaryResponse>> => {
  return apiClient.post('/education/summary', data);
};

// 生成题目解析
export const generateExplanation = async (data: EducationExplainRequest): Promise<ApiResponse<EducationExplainResponse>> => {
  return apiClient.post('/education/explain', data);
};

// 生成学习建议
export const generateAdvice = async (data: EducationAdviceRequest = {}): Promise<ApiResponse<EducationAdviceResponse>> => {
  return apiClient.post('/education/advice', data);
};
