/**
 * 轻盈音效工具
 * 使用 Web Audio API 生成柔和音效，无需外部音频文件
 * 
 * 设计原则：轻盈 = 低音量(0.08-0.15) + 短促(50-150ms) + 柔和波形(sine) + 平滑淡入淡出
 * 
 * 注意：不会改变任何组件逻辑，只在交互点播放音效
 */

// 音频上下文（懒加载，用户首次交互时才创建）
let audioCtx: AudioContext | null = null;

function getAudioContext(): AudioContext | null {
  try {
    if (!audioCtx) {
      const Ctor = window.AudioContext || (window as any).webkitAudioContext;
      if (!Ctor) return null;
      audioCtx = new Ctor();
    }
    if (audioCtx.state === 'suspended') {
      audioCtx.resume().catch(() => {});
    }
    return audioCtx;
  } catch {
    return null;
  }
}

/**
 * 播放一个柔和的音
 * 
 * @param frequency 音高（Hz）
 * @param duration 时长（秒）
 * @param volume 音量（0-1，建议0.08-0.15保持轻盈）
 * @param type 波形（sine最柔和）
 * @param delay 延迟（秒）
 * @param slideTo 滑音终点（可选，让音效更有层次）
 */
export function playTone(
  frequency: number,
  duration: number = 0.12,
  volume: number = 0.1,
  type: OscillatorType = 'sine',
  delay: number = 0,
  slideTo?: number,
): void {
  const ctx = getAudioContext();
  if (!ctx) return;

  const startTime = ctx.currentTime + delay;

  const oscillator = ctx.createOscillator();
  const gainNode = ctx.createGain();

  oscillator.type = type;
  oscillator.frequency.setValueAtTime(frequency, startTime);
  if (slideTo) {
    oscillator.frequency.exponentialRampToValueAtTime(slideTo, startTime + duration);
  }

  // 平滑淡入淡出（关键：避免"咔哒"声，让音效轻盈）
  gainNode.gain.setValueAtTime(0.0001, startTime);
  gainNode.gain.exponentialRampToValueAtTime(volume, startTime + 0.01);
  gainNode.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);

  oscillator.connect(gainNode);
  gainNode.connect(ctx.destination);

  oscillator.start(startTime);
  oscillator.stop(startTime + duration + 0.02);
}

// ============ 预设音效 ============

/** 展开/点击卡片：轻柔上行"叮" */
export function playOpenSound(): void {
  playTone(523.25, 0.12, 0.1, 'sine');      // C5
  playTone(659.25, 0.14, 0.08, 'sine', 0.04); // E5（轻微上行）
}

/** 返回/收起：柔和下行音 */
export function playCloseSound(): void {
  playTone(523.25, 0.12, 0.09, 'sine');      // C5
  playTone(392.0, 0.14, 0.07, 'sine', 0.04); // G4（下行）
}

/** 发送消息：清脆短音 */
export function playSendSound(): void {
  playTone(880.0, 0.06, 0.07, 'sine');       // A5 短促
}

/** 收到回复：柔和双音提示 */
export function playReceiveSound(): void {
  playTone(587.33, 0.1, 0.08, 'sine');       // D5
  playTone(880.0, 0.14, 0.07, 'sine', 0.08); // A5
}

/** 构建成功：上行三音（轻盈庆祝） */
export function playSuccessSound(): void {
  playTone(523.25, 0.1, 0.08, 'sine');       // C5
  playTone(659.25, 0.1, 0.08, 'sine', 0.07); // E5
  playTone(783.99, 0.16, 0.08, 'sine', 0.14); // G5
}

/** 轻触反馈：极短的高频点 */
export function playTapSound(): void {
  playTone(1046.5, 0.04, 0.05, 'sine');      // C6 极短极轻
}
