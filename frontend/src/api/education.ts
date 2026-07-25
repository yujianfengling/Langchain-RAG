import axios from './axios';

// 教育功能API接口

// 知识点总结请求
export interface EducationSummaryRequest {
  text: string;
}

// 知识点总结响应
export interface EducationSummaryResponse {
  success: boolean;
  data: {
    key_points: Array<{
      id: number;
      title: string;
      description: string;
    }>;
    summary: string;
    model: string;
  };
  message: string;
}

// 题目解析请求
export interface EducationExplainRequest {
  question: string;
}

// 题目解析响应
export interface EducationExplainResponse {
  success: boolean;
  data: {
    question: string;
    explanation: string;
    model: string;
  };
  message: string;
}

// 学习建议请求
export interface EducationAdviceRequest {
  history?: Array<{
    question: string;
    [key: string]: any;
  }>;
}

// 学习建议响应
export interface EducationAdviceResponse {
  success: boolean;
  data: {
    advice: string;
    model: string;
  };
  message: string;
}

// 生成知识点总结
export const generateSummary = async (data: EducationSummaryRequest): Promise<EducationSummaryResponse> => {
  const response = await axios.post<EducationSummaryResponse>('/api/education/summary', data);
  return response.data;
};

// 生成题目解析
export const generateExplanation = async (data: EducationExplainRequest): Promise<EducationExplainResponse> => {
  const response = await axios.post<EducationExplainResponse>('/api/education/explain', data);
  return response.data;
};

// 生成学习建议
export const generateAdvice = async (data: EducationAdviceRequest = {}): Promise<EducationAdviceResponse> => {
  const response = await axios.post<EducationAdviceResponse>('/api/education/advice', data);
  return response.data;
};