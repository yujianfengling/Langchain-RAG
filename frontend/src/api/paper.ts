import apiClient from './axios';

export interface PaperItem {
  id: string;
  question: string;
  type: string;
  options?: { id: string; text: string }[];
  knowledge_point?: string;
  estimated_minutes?: number;
}

export interface PaperPreview {
  items: PaperItem[];
  coverage: { knowledge_point: string; selected: number; available: number }[];
  gaps: string[];
  selected_count: number;
  estimated_minutes: number;
  available_count: number;
  duration_min?: number | null;
}

export interface PaperSpec {
  subject_id: string;
  knowledge_points?: string[];
  count?: number;
  types?: string[];
  duration_min?: number | null;
  title?: string;
}

export const previewPaper = async (payload: PaperSpec): Promise<PaperPreview> => {
  const res: any = await apiClient.post('/quiz/paper/preview', payload);
  return (res?.data ?? res) as PaperPreview;
};

export const exportPaper = async (payload: PaperSpec): Promise<Blob> => {
  const res: any = await apiClient.post('/quiz/paper/export', payload, { responseType: 'blob' });
  return (res instanceof Blob ? res : res?.data) as Blob;
};
