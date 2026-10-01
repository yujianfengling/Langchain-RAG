/**
 * 统一 TTS 播放控制器（2026-09-26：本地 CosyVoice，逐句合成 + 顺序播放）
 *
 * 本轮变更（对应"恢复并升级完整的语音问答"）：
 * - 后端改为 `/api/audio/tts`（逐句返回音频，顺序由后端保证），不再由前端切段后逐段请求
 * - 支持"边生成边朗读"：流式文本形成完整句子后立刻合成，不等全文结束
 * - 队列按 seq 顺序播放，**不漏句、不重复**；切歌/停止时立即清理
 * - 代次令牌（generationToken）：新提问 / 切换会话 / 停止后，旧请求的迟到结果一律丢弃
 * - 暂停 / 继续 / 停止 / 关闭自动朗读
 *
 * 句子切分不在这里做（后端 services/tts_text 负责且保证不丢字）；
 * 这里只做"判断是否已经出现完整句子"以便尽早开始合成。
 */

import { authHeaders } from '../api/axios';

export type PlaybackState = 'idle' | 'requesting' | 'playing' | 'paused' | 'stopped' | 'error';

export interface PlaybackInfo {
  state: PlaybackState;
  msgId: string | null;
  /** 已排队待播的句子数 */
  queued: number;
  /** 已播放完成的句子数 */
  played: number;
  /** 当前播放的句子文本（便于界面提示） */
  currentText?: string;
  error?: string;
}

type StateListener = (info: PlaybackInfo) => void;

export interface VoiceInfo {
  voices: string[];
  default: string;
  available: boolean;
  reason?: string;
  engine?: string;
}

// ===== 流式文本：判断是否出现完整句子 =====

const FENCE = '```';

/** 常见缩写（含点号）：这些点号不是句子结束 */
const ABBREVIATIONS = new Set(['e.g', 'i.e', 'etc', 'vs', 'no', 'fig', 'al', 'approx', 'max', 'min']);

/**
 * 判断 buffer[i] 处的英文句点是否"不该当作句子边界"
 *
 * 两种情况（实测中出现过的坏朗读）：
 * - 列表序号：`要点的第 3 条…` 被切成独立一段 `"3."`，会读出孤零零的一个数字
 * - 缩写：`e.g.` / `etc.` 被切成独立一段，会读出无意义的字母
 */
function isFalseBoundary(buffer: string, i: number): boolean {
  // 取本行内该点号之前的 token（去掉行首空白/列表符号前缀）
  const lineStart = buffer.lastIndexOf('\n', i - 1) + 1;
  const head = buffer.slice(lineStart, i + 1).trim();
  // 列表序号："3." / "12."（行首，仅数字）
  if (/^\d{1,3}\.$/.test(head)) return true;
  // 缩写："e.g." 取最后一个词
  const word = (head.match(/([A-Za-z.]+)\.$/)?.[1] || '').toLowerCase().replace(/\.$/, '');
  if (word && ABBREVIATIONS.has(word)) return true;
  // 单字母加点（a. / i.，常见于小写缩写与编号）不单独成句
  if (/^[a-z]$/.test(word)) return true;
  return false;
}

/**
 * 从流式缓冲里取出"已经完整"的句子，返回 [完整部分, 剩余缓冲]
 *
 * 规则：
 * - 代码块围栏内的内容不朗读、也不在此切分（等围栏闭合后整体交给后端处理成提示）
 * - 中文句末标点（。！？；）与英文句末标点（.!?; 后跟空白或结尾）视为句子边界
 * - 列表序号与常见缩写不算句子边界（见 isFalseBoundary）
 */
export function takeCompleteSentences(buffer: string): [string, string] {
  if (!buffer) return ['', ''];
  // 围栏未闭合：暂不切分（避免把代码块切开后朗读）
  const fenceCount = buffer.split(FENCE).length - 1;
  if (fenceCount % 2 === 1) {
    const lastFence = buffer.lastIndexOf(FENCE);
    return lastFence > 0 ? [buffer.slice(0, lastFence), buffer.slice(lastFence)] : ['', buffer];
  }
  let cut = -1;
  for (let i = 0; i < buffer.length; i += 1) {
    const ch = buffer[i];
    if ('。！？；'.includes(ch)) {
      cut = i + 1;
      continue;
    }
    if ('.!?;'.includes(ch)) {
      const next = buffer[i + 1];
      // 英文句点：后面是空白或已到缓冲末尾才算句子结束（避免切开 3.14 / e.g.）
      const atEnd = next === undefined && i === buffer.length - 1;
      const beforeSpace = next !== undefined && /\s/.test(next);
      if ((atEnd || beforeSpace) && !isFalseBoundary(buffer, i)) cut = i + 1;
    }
  }
  if (cut <= 0) return ['', buffer];
  return [buffer.slice(0, cut), buffer.slice(cut)];
}

/** 把整段文本按句切分（仅用于"逐句请求合成"，不丢字） */
export function splitSentencesForSpeech(text: string): string[] {
  const normalized = (text || '').replace(/\r\n/g, '\n').trim();
  if (!normalized) return [];
  const out: string[] = [];
  for (const para of normalized.split(/\n\s*\n/)) {
    let buffer = para.trim();
    while (buffer) {
      const [complete, rest] = takeCompleteSentences(buffer);
      if (complete) {
        out.push(complete);
        buffer = rest.trim();
      } else {
        out.push(buffer);
        buffer = '';
      }
    }
  }
  return out.filter((s) => s.trim().length > 0);
}

// ===== 播放器 =====

interface QueueItem {
  seq: number;
  text: string;
  url: string;
}

class TtsPlayer {
  private audio: HTMLAudioElement | null = null;
  private queue: QueueItem[] = [];
  private playing: QueueItem | null = null;
  private state: PlaybackState = 'idle';
  private activeMsgId: string | null = null;
  private listeners = new Set<StateListener>();
  /** 代次令牌：任何"抢占/停止"都会 +1，使在途请求的结果失效 */
  private generation = 0;
  /** 流式缓冲：msgId → 尚未成句的尾巴 */
  private streamBuffer = new Map<string, string>();
  private seqCounter = new Map<string, number>();
  private inFlight = 0;
  private playedCount = 0;
  private lastError = '';
  private voice: string | undefined;

  subscribe(fn: StateListener): () => void {
    this.listeners.add(fn);
    fn(this.info());
    return () => this.listeners.delete(fn);
  }

  getState(): PlaybackInfo {
    return this.info();
  }

  private info(): PlaybackInfo {
    return {
      state: this.state,
      msgId: this.activeMsgId,
      queued: this.queue.length,
      played: this.playedCount,
      currentText: this.playing?.text,
      error: this.lastError || undefined,
    };
  }

  private emit() {
    const i = this.info();
    this.listeners.forEach((fn) => fn(i));
  }

  private setState(state: PlaybackState, error = '') {
    this.state = state;
    this.lastError = error;
    this.emit();
  }

  // ===== 生命周期 =====

  /** 开始一次"边生成边读"（流式问答开始时调用） */
  beginStream(msgId: string, opts: { voice?: string } = {}) {
    this.generation += 1;
    this.clearQueue();
    this.activeMsgId = msgId;
    this.voice = opts.voice;
    this.streamBuffer.set(msgId, '');
    this.seqCounter.set(msgId, 0);
    this.playedCount = 0;
    this.setState('requesting');
  }

  /** 追加流式文本；出现完整句子就立刻排队合成 */
  pushStreamText(msgId: string, delta: string) {
    if (!delta || this.activeMsgId !== msgId) return;
    const buffer = (this.streamBuffer.get(msgId) || '') + delta;
    const [complete, rest] = takeCompleteSentences(buffer);
    this.streamBuffer.set(msgId, rest);
    if (complete.trim()) this.synthesizeInto(msgId, [complete]);
  }

  /** 流结束：把剩下的尾巴也读完（保证最后一句不漏） */
  endStream(msgId: string, fullText?: string) {
    if (this.activeMsgId !== msgId) return;
    const rest = (this.streamBuffer.get(msgId) || '').trim();
    this.streamBuffer.delete(msgId);
    if (rest) {
      this.synthesizeInto(msgId, [rest]);
    } else if (fullText && this.seqCounter.get(msgId) === 0) {
      // 流式期间没拿到任何完整句子（例如极短回答）：整体合成一次
      this.synthesizeInto(msgId, [fullText]);
    }
  }

  /**
   * 手动朗读整段回答（不依赖流式）
   *
   * 逐句请求合成：第一句合成完即可开始播放，不必等整段回答合成完
   * （实测：整段一次性合成时，长回答要等 20s+ 才有声音）
   */
  async speakFull(msgId: string, text: string, voice?: string) {
    if (!text || !text.trim()) return;
    this.generation += 1;
    const token = this.generation;
    this.clearQueue();
    this.activeMsgId = msgId;
    this.voice = voice;
    this.seqCounter.set(msgId, 0);
    this.playedCount = 0;
    this.setState('requesting');

    const sentences = splitSentencesForSpeech(text);
    if (!sentences.length) return;
    for (const sentence of sentences) {
      if (token !== this.generation) return;   // 已被抢占（新提问/停止）
      await this.synthesizeInto(msgId, [sentence]);
    }
  }

  /**
   * 暂停 / 继续 / 取消（同一消息再次点击）
   *
   * - playing → 暂停
   * - paused  → 继续
   * - requesting（还在合成）→ 取消本次朗读（避免"点了没反应"或重新开始）
   */
  async toggle(msgId: string, text?: string, voice?: string): Promise<'paused' | 'playing' | 'stopped'> {
    if (this.activeMsgId === msgId) {
      if (this.state === 'paused') {
        await this.audio?.play().catch(() => this.setState('error', '播放失败'));
        this.setState('playing');
        return 'playing';
      }
      if (this.state === 'playing') {
        this.audio?.pause();
        this.setState('paused');
        return 'paused';
      }
      if (this.state === 'requesting') {
        this.stop();               // 合成中再点一次 = 取消朗读
        return 'stopped';
      }
    }
    if (text) await this.speakFull(msgId, text, voice);
    return 'playing';
  }

  /** 停止并清空（新提问 / 切换会话 / 离开页面） */
  stop() {
    this.generation += 1;      // 让在途请求的结果失效
    this.clearQueue();
    this.streamBuffer.clear();
    this.activeMsgId = null;
    this.inFlight = 0;
    if (this.state !== 'idle') this.setState('stopped');
  }

  setVoice(voice?: string) {
    this.voice = voice;
  }

  private clearQueue() {
    this.queue.forEach((item) => URL.revokeObjectURL(item.url));
    this.queue = [];
    if (this.audio) {
      // 先摘掉事件处理：否则清空 src 会触发 onerror，把状态误判成"播放失败"
      this.audio.onended = null;
      this.audio.onerror = null;
      try { this.audio.pause(); } catch { /* ignore */ }
      this.audio.removeAttribute('src');
      try { this.audio.load(); } catch { /* ignore */ }
      this.audio = null;
    }
    this.playing = null;
  }

  // ===== 合成与播放 =====

  private async synthesizeInto(msgId: string, texts: string[], whole = false) {
    const token = this.generation;
    this.inFlight += 1;
    try {
      const items = await fetchTtsItems(texts, this.voice, whole ? undefined : undefined);
      if (token !== this.generation) return;   // 已被抢占：丢弃迟到结果
      const base = this.seqCounter.get(msgId) || 0;
      let index = 0;
      for (const it of items) {
        if (token !== this.generation) return;
        const url = URL.createObjectURL(base64ToBlob(it.audio_b64, 'audio/wav'));
        this.queue.push({ seq: base + index, text: it.text, url });
        index += 1;
      }
      this.seqCounter.set(msgId, base + index);
      if (!this.playing) this.playNext();
      else this.emit();
    } catch (e: any) {
      if (token === this.generation) {
        this.setState('error', e?.message || '语音合成失败');
      }
    } finally {
      this.inFlight -= 1;
    }
  }

  private playNext() {
    const item = this.queue.shift();
    if (!item) {
      this.playing = null;
      if (this.inFlight === 0) {
        this.activeMsgId = null;
        this.setState('idle');
      } else {
        this.emit();
      }
      return;
    }
    this.playing = item;
    if (!this.audio) {
      this.audio = new Audio();
      this.audio.onended = () => {
        this.playedCount += 1;
        if (this.playing) URL.revokeObjectURL(this.playing.url);
        this.playing = null;
        this.playNext();
      };
      this.audio.onerror = () => {
        this.setState('error', '音频播放失败');
        this.clearQueue();
      };
    }
    this.audio.src = item.url;
    this.audio.play()
      .then(() => this.setState('playing'))
      .catch(() => this.setState('error', '浏览器阻止了自动播放，请点击播放按钮'));
  }
}

// ===== 后端接口 =====

interface TtsItem { seq: number; text: string; audio_b64: string; duration_s?: number; latency_ms?: number }

/** 请求逐句音频（后端会做朗读文本规范化与按句切分，保证不丢字） */
async function fetchTtsItems(texts: string[], voice?: string, maxChars?: number): Promise<TtsItem[]> {
  const resp = await fetch('/api/audio/tts', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ text: texts.join('\n'), voice, max_chars: maxChars }),
  });
  if (!resp.ok) {
    let detail = `语音合成失败 (HTTP ${resp.status})`;
    try {
      const body = await resp.json();
      detail = body?.message || body?.detail || detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  const body = await resp.json();
  const items: TtsItem[] = body?.data?.items || [];
  if (!items.length) throw new Error('语音合成没有返回音频');
  return items;
}

export async function fetchVoiceInfo(): Promise<VoiceInfo> {
  try {
    const resp = await fetch('/api/audio/voices', { headers: authHeaders() });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const body = await resp.json();
    const d = body?.data || {};
    return {
      voices: d.voices || [],
      default: d.default || '',
      available: !!d.available,
      reason: d.reason || '',
      engine: d.engine,
    };
  } catch (e: any) {
    return { voices: [], default: '', available: false, reason: e?.message || '音色列表不可用' };
  }
}

function base64ToBlob(b64: string, mime: string): Blob {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

/** 全局单例（聊天页共用，同一时刻只有一个活动音频） */
export const ttsPlayer = new TtsPlayer();

// 便于在浏览器控制台/自动化验收里检查播放器状态（只读调试入口）
try {
  (window as any).__ttsPlayer = ttsPlayer;
} catch { /* SSR/非浏览器环境忽略 */ }
