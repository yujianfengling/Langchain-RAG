import apiClient from './axios';
import { ApiResponse } from '../types';

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
