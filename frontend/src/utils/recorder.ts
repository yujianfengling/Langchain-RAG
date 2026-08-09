/**
 * 浏览器录音工具（V2.5.1 Checkpoint D）
 *
 * Web Audio API → PCM → WAV
 * Chrome 录制不使用 audio/webm，直接转码为 WAV（MiMo-V2.5-ASR 支持 wav）
 * 不引入 FFmpeg 依赖。
 */

export interface RecorderHandle {
  start: () => Promise<void>;
  stop: () => Promise<Blob>; // 返回 WAV Blob
  cancel: () => void;
  onDurationChange?: (seconds: number) => void;
}

/**
 * 创建录音器
 *
 * 错误处理：
 * - NotAllowedError → microphone_permission_denied（用户拒绝授权）
 * - NotFoundError → 无麦克风设备
 * - 其他 → recording_failed
 */
export function createRecorder(onDurationChange?: (s: number) => void): RecorderHandle {
  let mediaRecorder: MediaRecorder | null = null;
  let stream: MediaStream | null = null;
  let chunks: BlobPart[] = [];
  let startTime = 0;
  let timer: ReturnType<typeof setInterval> | null = null;
  let cancelled = false;

  const start = async (): Promise<void> => {
    cancelled = false;
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    // 优先用 MediaRecorder 录制（WAV 转码由 stop 阶段完成）
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : '';
    mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    chunks = [];
    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunks.push(e.data);
    };
    mediaRecorder.start();
    startTime = Date.now();
    if (onDurationChange) {
      timer = setInterval(() => {
        onDurationChange((Date.now() - startTime) / 1000);
      }, 250);
    }
  };

  const stop = async (): Promise<Blob> => {
    if (!mediaRecorder) throw new Error('recorder not started');
    if (timer) { clearInterval(timer); timer = null; }
    const stopped = new Promise<Blob>((resolve, reject) => {
      mediaRecorder!.onstop = async () => {
        try {
          if (cancelled) return reject(new Error('cancelled'));
          const blob = new Blob(chunks, { type: mediaRecorder!.mimeType || 'audio/webm' });
          const wav = await convertToWav(blob);
          resolve(wav);
        } catch (e) {
          reject(e);
        }
      };
      mediaRecorder!.onerror = (e) => reject(e);
    });
    mediaRecorder.stop();
    stream?.getTracks().forEach((t) => t.stop());
    return stopped;
  };

  const cancel = () => {
    cancelled = true;
    if (timer) { clearInterval(timer); timer = null; }
    try { mediaRecorder?.stop(); } catch { /* ignore */ }
    stream?.getTracks().forEach((t) => t.stop());
    mediaRecorder = null;
    stream = null;
  };

  return { start, stop, cancel, onDurationChange };
}

/**
 * 将任意音频 Blob（webm）解码并转码为 WAV
 * Web Audio API decodeAudioData → AudioBuffer → PCM → WAV 头
 */
async function convertToWav(blob: Blob): Promise<Blob> {
  const arrayBuffer = await blob.arrayBuffer();
  const AudioCtx: typeof AudioContext =
    window.AudioContext || (window as any).webkitAudioContext;
  const ctx = new AudioCtx();

  // 如果已经是 WAV（某些浏览器直接支持），快速路径
  if (isWavBuffer(arrayBuffer)) {
    await ctx.close();
    return new Blob([arrayBuffer], { type: 'audio/wav' });
  }

  const audioBuffer = await ctx.decodeAudioData(arrayBuffer);
  const wavBuffer = audioBufferToWav(audioBuffer);
  await ctx.close();
  return new Blob([wavBuffer], { type: 'audio/wav' });
}

function isWavBuffer(buf: ArrayBuffer): boolean {
  if (buf.byteLength < 12) return false;
  const dv = new DataView(buf);
  return dv.getUint32(0, false) === 0x52494646 /* RIFF */ &&
         dv.getUint32(8, false) === 0x57415645 /* WAVE */;
}

/** AudioBuffer → WAV 16-bit PCM 字节 */
function audioBufferToWav(buffer: AudioBuffer): ArrayBuffer {
  const numChannels = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  const numFrames = buffer.length;
  const bytesPerSample = 2;
  const blockAlign = numChannels * bytesPerSample;
  const dataSize = numFrames * blockAlign;
  const bufferSize = 44 + dataSize;
  const output = new ArrayBuffer(bufferSize);
  const view = new DataView(output);

  // WAV 头
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);            // PCM
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true);           // 16-bit
  writeString(view, 36, 'data');
  view.setUint32(40, dataSize, true);

  // 采样数据（混合多声道 → 单声道）
  const channels: Float32Array[] = [];
  for (let c = 0; c < numChannels; c++) {
    channels.push(buffer.getChannelData(c));
  }
  let offset = 44;
  for (let i = 0; i < numFrames; i++) {
    let sample = 0;
    for (let c = 0; c < numChannels; c++) {
      sample += channels[c][i];
    }
    sample = sample / numChannels;
    // clamp + 16-bit
    sample = Math.max(-1, Math.min(1, sample));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7FFF, true);
    offset += 2;
  }

  return output;
}

function writeString(view: DataView, offset: number, str: string) {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i));
  }
}
