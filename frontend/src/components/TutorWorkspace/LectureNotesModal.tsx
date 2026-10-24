import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, List, Modal, Space, Spin, Tag, Typography, message } from 'antd';
import { AudioOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  getLectureNote, listLectureNotes, uploadLectureNote,
  type LectureNote, type LectureNoteMeta,
} from '../../api/audio';

const { Text, Paragraph } = Typography;
const MAX_RECORD_SECONDS = 15 * 60;

/** AudioBuffer → 16-bit PCM WAV。浏览器录音多为 webm/ogg，后端 ASR 只收 wav/mp3。 */
const encodeWav = (buffer: AudioBuffer): Blob => {
  const channels = Math.min(buffer.numberOfChannels, 2);
  const frames = buffer.length;
  const view = new DataView(new ArrayBuffer(44 + frames * channels * 2));
  const writeString = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };
  writeString(0, 'RIFF');
  view.setUint32(4, 36 + frames * channels * 2, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, buffer.sampleRate, true);
  view.setUint32(28, buffer.sampleRate * channels * 2, true);
  view.setUint16(32, channels * 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, frames * channels * 2, true);
  const data = [];
  for (let channel = 0; channel < channels; channel += 1) data.push(buffer.getChannelData(channel));
  let offset = 44;
  for (let i = 0; i < frames; i += 1) {
    for (let channel = 0; channel < channels; channel += 1) {
      const sample = Math.max(-1, Math.min(1, data[channel][i] || 0));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
  }
  return new Blob([view], { type: 'audio/wav' });
};

/**
 * 课堂录音笔记：上传录音 → 后端 ASR + 整理 → 列表/详情。
 * 音频只在用户点击后上传；ASR 不可用时后端如实报错，不伪造转写。
 */
const LectureNotesModal: React.FC<{
  open: boolean;
  onClose: () => void;
  subjectId: string;
}> = ({ open, onClose, subjectId }) => {
  const [notes, setNotes] = useState<LectureNoteMeta[]>([]);
  const [detail, setDetail] = useState<LectureNote | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const autoStopRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    if (!subjectId) return;
    setLoading(true);
    setError(null);
    try {
      setNotes(await listLectureNotes(subjectId));
    } catch (e: any) {
      setError(e?.response?.data?.detail || '课堂笔记加载失败');
    } finally {
      setLoading(false);
    }
  }, [subjectId]);

  useEffect(() => {
    if (open) {
      setDetail(null);
      void load();
    }
  }, [open, load]);

  const onPick = async (file?: File) => {
    if (!file || !subjectId) return;
    setUploading(true);
    setError(null);
    try {
      const res: any = await uploadLectureNote(subjectId, file);
      const created = res?.data ?? res;
      message.success('课堂笔记已生成');
      await load();
      if (created?.id) {
        setDetail(await getLectureNote(created.id));
      }
    } catch (e: any) {
      setError(e?.response?.data?.message || e?.response?.data?.detail || '录音处理失败');
    } finally {
      setUploading(false);
    }
  };

  const clearRecordingTimers = () => {
    if (timerRef.current) { window.clearInterval(timerRef.current); timerRef.current = null; }
    if (autoStopRef.current) { window.clearTimeout(autoStopRef.current); autoStopRef.current = null; }
  };

  const stopRecording = () => {
    clearRecordingTimers();
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    setRecording(false);
  };

  const startRecording = async () => {
    if (!subjectId || uploading) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      message.error('当前浏览器不支持录音，请改用上传音频');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : (MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' : '');
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        if (!blob.size) {
          setError('没有录到声音，请检查麦克风权限');
          return;
        }
        try {
          const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
          const ctx = new AudioCtx();
          const buffer = await ctx.decodeAudioData(await blob.arrayBuffer());
          const wav = encodeWav(buffer);
          await ctx.close();
          await onPick(new File([wav], `classroom-${Date.now()}.wav`, { type: 'audio/wav' }));
        } catch {
          setError('录音转 WAV 失败，请改用上传 WAV/MP3');
        }
      };
      recorder.start(1000);
      setRecording(true);
      setSeconds(0);
      setError(null);
      timerRef.current = window.setInterval(() => setSeconds((value) => value + 1), 1000);
      autoStopRef.current = window.setTimeout(() => stopRecording(), MAX_RECORD_SECONDS * 1000);
    } catch {
      message.error('无法访问麦克风，请检查浏览器权限');
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
  };

  const handleClose = () => {
    if (recording) stopRecording();
    onClose();
  };

  useEffect(() => () => {
    clearRecordingTimers();
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    streamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  return (
    <Modal title="课堂录音笔记" open={open} onCancel={handleClose} width={780} footer={null}
           destroyOnClose>
      <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
        {!recording ? (
          <Button danger type="primary" icon={<AudioOutlined />} disabled={uploading}
                  onClick={() => void startRecording()}>
            开始录音
          </Button>
        ) : (
          <Button danger icon={<AudioOutlined />} onClick={stopRecording}>
            停止并生成笔记
          </Button>
        )}
        {recording && (
          <Tag color="red" style={{ alignSelf: 'center' }}>
            录音中 {String(Math.floor(seconds / 60)).padStart(2, '0')}:
            {String(seconds % 60).padStart(2, '0')}
          </Tag>
        )}
        <label className="ant-btn" style={{ cursor: uploading || recording ? 'not-allowed' : 'pointer' }}>
          <AudioOutlined /> {uploading ? '正在转写…' : '上传录音'}
          <input type="file" accept="audio/*" style={{ display: 'none' }}
                 disabled={uploading || recording}
                 onChange={(e) => { void onPick(e.target.files?.[0]); e.currentTarget.value = ''; }} />
        </label>
        <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>刷新</Button>
        <Text type="secondary" style={{ alignSelf: 'center', fontSize: 12 }}>
          可直接录音（自动转 WAV）或上传 WAV/MP3；本地 ASR 未启动时会明确报错
        </Text>
      </div>

      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 10 }} />}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
      ) : notes.length === 0 ? (
        <Empty description="还没有课堂笔记，上传一段录音试试" />
      ) : (
        <List
          size="small"
          dataSource={notes}
          renderItem={(note) => (
            <List.Item
              actions={[<Button key="open" type="link" size="small"
                                onClick={async () => setDetail(await getLectureNote(note.id))}>
                查看
              </Button>]}
            >
              <List.Item.Meta
                title={<span>{note.title} <Tag>{note.transcript_chars || 0} 字</Tag></span>}
                description={String(note.created_at || '').replace('T', ' ').slice(0, 16)
                  + (note.summary?.ai_generated === false ? ' · 兜底提纲' : '')}
              />
            </List.Item>
          )}
        />
      )}

      {detail && (
        <div style={{ marginTop: 12, borderTop: '1px solid #eee', paddingTop: 10 }}>
          <Text strong>{detail.title}</Text>
          {(detail.summary?.knowledge_points || []).length > 0 && (
            <div style={{ marginTop: 6 }}>
              <Space size={4} wrap>
                <Text type="secondary" style={{ fontSize: 12 }}>关联知识点：</Text>
                {(detail.summary?.knowledge_points || []).map((point) => (
                  <Tag key={point} color="blue">{point}</Tag>
                ))}
              </Space>
            </div>
          )}
          {detail.summary?.ai_generated === false && (
            <Alert style={{ marginTop: 6 }} type="warning" showIcon
                   message="模型整理不可用，下面是按转写生成的兜底提纲" />
          )}
          {(['outline', 'key_points', 'questions', 'follow_ups'] as const).map((key) => {
            const items = detail.summary?.[key] || [];
            if (!items.length) return null;
            const label = { outline: '提纲', key_points: '重点', questions: '待解决问题',
              follow_ups: '课后建议' }[key];
            return (
              <div key={key} style={{ marginTop: 8 }}>
                <Text type="secondary">{label}</Text>
                <ul style={{ margin: '4px 0 0 18px' }}>
                  {items.map((item, index) => <li key={index}>{item}</li>)}
                </ul>
              </div>
            );
          })}
          <details style={{ marginTop: 8 }}>
            <summary style={{ cursor: 'pointer', color: '#64748b' }}>查看完整转写</summary>
            <Paragraph style={{ marginTop: 6, maxHeight: 220, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
              {detail.transcript}
            </Paragraph>
          </details>
        </div>
      )}
    </Modal>
  );
};

export default LectureNotesModal;
