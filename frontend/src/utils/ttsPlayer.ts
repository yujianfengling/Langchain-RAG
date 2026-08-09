/**
 * 统一 TTS 播放控制器（V2.5.3）
 *
 * 解决：重复播放、叠音、TTS 慢、切换对话/页面不停止。
 *
 * 设计：
 * - 单一 Audio 实例（Chat 页面最多一个 active Audio）
 * - 状态机：idle / requesting / playing / paused / stopped / error
 * - 自动播放按 message_id 去重（autoPlayedMessageIds Set，仅 streaming→done 触发一次）
 * - 新音频开始前停止旧音频、清理 src、revokeObjectURL
 * - 手动🔊全文朗读：分段（句号/问号/感叹号/分号切分，~150-250字/段）+ 预取 pipeline
 *   生成→播放→预取，首段一准备好立即播放
 * - 切换对话/学科/离开页面时 stop() + 清理队列
 */

// ===== 句子切分与精简 =====

/** 按句子边界切分文本（句号/问号/感叹号/分号），每段约 150~250 字 */
export function splitTtsSegments(text: string, maxPerSegment = 250, minPerSegment = 100): string[] {
  const cleaned = text.replace(/\s+/g, ' ').trim();
  if (!cleaned) return [];
  if (cleaned.length <= maxPerSegment) return [cleaned];

  const sentences = cleaned.split(/(?<=[。！？；.!?;])/);
  const segments: string[] = [];
  let buf = '';

  for (const s of sentences) {
    if (!s) continue;
    if (buf.length + s.length > maxPerSegment && buf.length >= minPerSegment) {
      segments.push(buf.trim());
      buf = s;
    } else {
      buf += s;
    }
  }
  if (buf.trim()) segments.push(buf.trim());

  // 过长单句硬切
  return segments.map((seg) =>
    seg.length > maxPerSegment ? seg.slice(0, maxPerSegment) : seg
  );
}

/** 自动语音精简朗读：取前 ~300 字（句子边界），避免整篇朗读 */
export function getConciseVoiceText(text: string, maxChars = 300): string {
  const cleaned = text.replace(/\s+/g, ' ').trim();
  if (cleaned.length <= maxChars) return cleaned;
  const cut = cleaned.slice(0, maxChars);
  for (const sep of ['。', '！', '？', '.', '!', '?']) {
    const idx = cut.lastIndexOf(sep);
    if (idx > maxChars * 0.6) return cut.slice(0, idx + 1);
  }
  return cut;
}

// ===== 播放状态机 =====

export type PlaybackState = 'idle' | 'requesting' | 'playing' | 'paused' | 'stopped' | 'error';

type StateListener = (state: PlaybackState, msgId: string | null) => void;

class TtsPlayer {
  private audio: HTMLAudioElement | null = null;
  private objectUrls: string[] = [];
  private state: PlaybackState = 'idle';
  private activeMsgId: string | null = null;
  private autoPlayedIds = new Set<string>();
  private listeners = new Set<StateListener>();
  private generationToken = 0; // 用于取消过期 TTS 请求（新任务抢播）

  /** 订阅播放状态变化，返回取消函数 */
  subscribe(fn: StateListener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  getState(): { state: PlaybackState; msgId: string | null } {
    return { state: this.state, msgId: this.activeMsgId };
  }

  private setState(state: PlaybackState) {
    this.state = state;
    this.listeners.forEach((fn) => fn(state, this.activeMsgId));
  }

  /** 释放所有 object URL 并暂停当前音频 */
  private clearAudio() {
    if (this.audio) {
      try { this.audio.pause(); } catch { /* ignore */ }
      this.audio.src = '';
      this.audio = null;
    }
    this.objectUrls.forEach((u) => URL.revokeObjectURL(u));
    this.objectUrls = [];
  }

  /** 停止所有播放（切换对话/学科/离开页面） */
  stop() {
    this.generationToken++; // 使所有 in-flight TTS 请求失效
    this.clearAudio();
    this.activeMsgId = null;
    if (this.state === 'playing' || this.state === 'requesting' || this.state === 'paused') {
      this.setState('stopped');
    }
  }

  /** 是否已自动播放过该消息 */
  wasAutoPlayed(msgId: string): boolean {
    return this.autoPlayedIds.has(msgId);
  }

  /**
   * 自动播放（仅 streaming→done 触发一次）
   * @returns true=已触发播放
   */
  autoSpeak(msgId: string, text: string, voice?: string): boolean {
    if (!msgId || this.autoPlayedIds.has(msgId)) return false;
    this.autoPlayedIds.add(msgId); // 立即标记，防重复
    const concise = getConciseVoiceText(text);
    if (!concise) return false;
    this.playSegments(concise, msgId, { voice, maxSegments: 1 });
    return true;
  }

  /**
   * 手动🔊播放完整回答（分段 + 预取 pipeline）
   * 播放中再次点击 → 暂停
   */
  async toggleManual(msgId: string, text: string, voice?: string): Promise<'paused' | 'playing'> {
    if (!text || !text.trim()) return 'playing';
    if (this.activeMsgId === msgId && (this.state === 'playing' || this.state === 'paused')) {
      // 同一消息再次点击 → 暂停/恢复
      if (this.state === 'paused') {
        this.audio?.play().catch(() => this.setState('error'));
        this.setState('playing');
        return 'playing';
      }
      this.audio?.pause();
      this.setState('paused');
      return 'paused';
    }
    // 新消息 → 抢播（停止旧的，不管是否同一条已在播）
    this.generationToken++;
    this.clearAudio();
    this.playSegments(text, msgId, { voice });
    return 'playing';
  }

  /**
   * 分段播放 + 预取 pipeline
   * 流程：segment1 → TTS → 准备好立即播放；同时 segment2 预取
   */
  private async playSegments(
    text: string,
    msgId: string,
    opts: { voice?: string; maxSegments?: number } = {}
  ): Promise<void> {
    const segments = splitTtsSegments(text);
    if (!segments.length) return;

    const token = ++this.generationToken;
    this.activeMsgId = msgId;
    this.setState('requesting');

    // 限制自动朗读分段数（自动模式 maxSegments=1，手动全文）
    const targetSegments = opts.maxSegments ? segments.slice(0, opts.maxSegments) : segments;

    // 预取剩余分段
    const prefetch = (i: number) => {
      if (i >= targetSegments.length) return;
      fetchTtsAudio(targetSegments[i], opts.voice)
        .then((blob) => {
          if (token !== this.generationToken) return; // 已过期
          this.enqueueBlob(blob);
          prefetch(i + 1);
        })
        .catch(() => { /* 预取失败跳过该段 */ });
    };

    try {
      const firstBlob = await fetchTtsAudio(targetSegments[0], opts.voice);
      if (token !== this.generationToken) return;
      this.enqueueBlob(firstBlob);
      prefetch(1); // 播放第一段的同时预取后续
    } catch {
      if (token === this.generationToken) this.setState('error');
    }
  }

  /** 将音频 blob 排队播放（单 Audio 实例串行） */
  private enqueueBlob(blob: Blob) {
    const url = URL.createObjectURL(blob);
    this.objectUrls.push(url);

    // 复用或新建单一 Audio 实例
    if (!this.audio) {
      this.audio = new Audio();
      this.audio.onended = () => {
        // 队列中还有后续音频 → 继续；否则 idle
        const next = this.objectUrls[this.objectUrls.length - 1];
        const pending = this.objectUrls.length > 1;
        if (pending && this.audio) {
          this.audio.src = next;
          this.audio.play().catch(() => this.setState('error'));
          this.setState('playing');
        } else {
          this.finishPlayback();
        }
      };
      this.audio.onerror = () => {
        if (this.state === 'playing' || this.state === 'requesting') this.setState('error');
      };
    }

    // 已有音频在播（这是预取排队的后续段）→ 不打断，等 onended
    if (this.objectUrls.length > 1) return;

    this.audio.src = url;
    this.audio.play()
      .then(() => this.setState('playing'))
      .catch(() => {
        this.setState('error');
        this.clearAudio();
      });
  }

  private finishPlayback() {
    // 播放队列清空 → idle
    this.objectUrls.forEach((u) => URL.revokeObjectURL(u));
    this.objectUrls = [];
    this.activeMsgId = null;
    this.setState('idle');
  }
}

/** 请求 TTS 音频（后端 /api/audio/speech，含清洗 + 缓存） */
async function fetchTtsAudio(text: string, voice?: string): Promise<Blob> {
  const resp = await fetch('/api/audio/speech', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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
  return resp.blob();
}

// 全局单例（Chat 页面共用，一个 active Audio）
export const ttsPlayer = new TtsPlayer();
