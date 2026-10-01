import apiClient, { authHeaders } from './axios';
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

/**
 * 语音合成（mimo-v2.5-tts）
 * 返回 Blob 音频（audio/wav）
 */
export const synthesizeSpeech = async (
  text: string,
  voice?: string,
): Promise<Blob> => {
  const resp = await fetch('/api/audio/speech', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ text, voice }),
  });
  if (!resp.ok) {
    let detail = `语音合成失败 (HTTP ${resp.status})`;
    try {
      const body = await resp.json();
      detail = body?.message || body?.detail || detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return await resp.blob();
};
