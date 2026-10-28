import apiClient, { LONG_TASK_TIMEOUT } from './axios';
import { ApiResponse } from '../types';

export interface LectureSummary {
  title?: string;
  outline?: string[];
  key_points?: string[];
  questions?: string[];
  follow_ups?: string[];
  knowledge_points?: string[];
  ai_generated?: boolean;
}

export interface LectureNoteMeta {
  id: string;
  subject_id: string;
  title: string;
  summary: LectureSummary;
  source_name?: string | null;
  asr_service?: string | null;
  asr_engine?: string | null;
  transcript_chars?: number;
  created_at: string;
}

export interface LectureNote extends LectureNoteMeta {
  transcript: string;
}

export const uploadLectureNote = async (
  subjectId: string, file: File, model?: string,
): Promise<ApiResponse<LectureNoteMeta & { summary: LectureSummary }>> => {
  const form = new FormData();
  form.append('file', file);
  form.append('subject_id', subjectId);
  if (model) form.append('model', model);
  return apiClient.post('/audio/lecture-notes', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: LONG_TASK_TIMEOUT,
  });
};

export const listLectureNotes = async (subjectId?: string): Promise<LectureNoteMeta[]> => {
  const res: any = await apiClient.get('/audio/lecture-notes', {
    params: subjectId ? { subject_id: subjectId } : {},
  });
  return (res?.data ?? res)?.notes ?? [];
};

export const getLectureNote = async (noteId: string): Promise<LectureNote> => {
  const res: any = await apiClient.get('/audio/lecture-notes/' + noteId);
  return (res?.data ?? res) as LectureNote;
};

/**
 * 语音识别（mimo-v2.5-asr）
 */
export const transcribeAudio = async (
  audioBlob: Blob,
  filename?: string,
): Promise<ApiResponse<{ text: string }>> => {
  const formData = new FormData();
  const name = filename || `audio_${Date.now()}.${audioBlob.type.includes('mp3') ? 'mp3' : 'wav'}`;
  formData.append('file', audioBlob, name);
  return apiClient.post('/audio/transcribe', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};
