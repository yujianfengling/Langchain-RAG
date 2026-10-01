import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Input, Button, Typography, Space, Spin, Select, Tag, message, Switch, Tooltip, List } from 'antd';
import {
  SendOutlined, RobotOutlined, StopOutlined, AudioOutlined,
} from '@ant-design/icons';
import { ChatMessage, Subject, SourceInfo, ChatMode, AgentStepView } from '../../types';
import { sendMessageStream } from '../../api/chat';
import { runAgentStream } from '../../api/agentRun';
import { getReadyStatus } from '../../api/health';
import { getModelStatus } from '../../api/models';
import { playSendSound, playReceiveSound } from '../../utils/sound';
import { getConversationMessages, createConversation, ConversationMessage } from '../../api/conversations';
import { transcribeAudio } from '../../api/audio';
import { createRecorder } from '../../utils/recorder';
import { ttsPlayer, PlaybackState } from '../../utils/ttsPlayer';
import ChatMessageItem from './ChatMessageItem';
import './ChatBox.css';

const { Text, Title } = Typography;
const { TextArea } = Input;

// 流式 delta 批处理间隔（ms）：合并渲染刷新，不丢字符（V2.5.4）
const STREAM_BATCH_MS = 40;

/** 外部带上下文跳转（如从学习计划"去自测"）传入的预设 */
export interface ChatPreset {
  mode?: ChatMode;
  /** 预填到输入框（如知识点名称） */
  input?: string;
  /** 来源任务：作答会关联到该任务 */
  taskId?: string;
}

interface ChatBoxProps {
  subjectId: string;
  subjects: Subject[];
  conversationId?: string;
  onConversationCreated?: (id: string) => void;
  onSubjectChange?: (subjectId: string) => void;
  /** 带上下文跳转的预设（应用后由父组件清理） */
  preset?: ChatPreset;
  /** 预设已应用，父组件可清理以免重复应用 */
  onPresetConsumed?: () => void;
}

// 学习模式选项（V2.5.1：教学辅助整合进 Chat，不跳转独立页面）
const MODE_OPTIONS: { label: string; value: ChatMode }[] = [
  { label: '普通问答', value: 'chat' },
  { label: '智能笔记', value: 'smart_note' },
  { label: '知识总结', value: 'summary' },
  { label: '题目解析', value: 'explanation' },
  { label: '自测', value: 'quiz' },
  { label: '学习建议', value: 'advice' },
  // 阶段二：受约束的学习 Agent（读取真实学习记录 → 给安排 → 可停止）
  { label: '学习助手', value: 'agent' },
];

// 各学习模式颜色（统一 Slate 灰，保持整体低调统一）
const MODE_COLORS: Record<ChatMode, string> = {
  chat: '#475569',
  smart_note: '#475569',
  summary: '#475569',
  explanation: '#475569',
  quiz: '#475569',
  advice: '#475569',
  agent: '#475569',
};

// 各学习模式动态 Placeholder（V2.5.1 统一交互）
const MODE_PLACEHOLDERS: Record<ChatMode, string> = {
  chat: '输入你的问题，例如：什么是二叉树？',
  smart_note: '输入知识点，例如：二叉树、牛顿第二定律',
  summary: '输入要总结的主题，例如：总结二叉树的核心知识点',
  explanation: '输入题目或问题，例如：为什么完全二叉树适合顺序存储？',
  quiz: '输入自测主题，例如：二叉树遍历',
  advice: '输入你的学习困难或目标，例如：我总是搞混DFS和BFS，应该怎么复习？',
  agent: '说出目标与限制，例如：我周五要考这一章，每天只有半小时，帮我复习',
};
// 统一快捷键提示（所有模式一致）
const ENTER_HINT = ' · Enter发送，Shift+Enter换行';

const ChatBox: React.FC<ChatBoxProps> = ({
  subjectId, subjects, conversationId, onConversationCreated,
  preset, onPresetConsumed,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState<ChatMode>('chat');
  // 模型选择持久化：学生切换过模型后，重新进入页面不应默默回到默认模型。
  // 空串表示"未选择" → 由后端默认模型决定（当前为 DeepSeek），不再硬编码 MiMo。
  const [selectedModel, setSelectedModel] = useState<string>(
    () => localStorage.getItem('chat_model') || '');
  // 后端默认文本模型与可用模型列表（/models/status）
  const [serverDefaultModel, setServerDefaultModel] = useState<string>('deepseek');
  const [modelOptions, setModelOptions] = useState<{ value: string; label: string }[]>([
    { value: 'deepseek', label: 'DeepSeek' },
    { value: 'mimo', label: '小米MiMo' },
    { value: 'qwen', label: '通义千问' },
  ]);
  const [retrievalTag, setRetrievalTag] = useState<{ color: string; text: string } | null>(null);
  // 来源学习任务（从计划/复习跳转进入时携带，作答会关联到该任务）
  const [sourceTaskId, setSourceTaskId] = useState<string | undefined>(undefined);
  // 学习助手（阶段二）：运行中的中止控制器与当前步骤
  const agentAbortRef = useRef<AbortController | null>(null);
  const [agentRunning, setAgentRunning] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // 流式回复标记：回复期间屏蔽历史加载，避免覆盖本地消息
  const streamingRef = useRef(false);
  // ===== 流式 delta 批处理（V2.5.4）=====
  // 当前正在流式写入的 assistant 消息 id（避免闭包过期）
  const aiIdRef = useRef('');
  // 待 flush 的 delta 字符缓冲
  const pendingBufferRef = useRef('');
  // flush 定时器
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // ===== 自动滚动（V2.5.4）=====
  // 用户是否仍靠近底部（上滚历史时停止强制跟随）
  const isNearBottomRef = useRef(true);

  // ===== 语音（V2.5.3 统一播放控制器）=====
  const [recording, setRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [transcribing, setTranscribing] = useState(false);
  const recorderRef = useRef<ReturnType<typeof createRecorder> | null>(null);
  // 统一 Audio Controller 状态（单一 active Audio，禁止叠音）
  const [playbackState, setPlaybackState] = useState<PlaybackState>('idle');
  const [playingMsgId, setPlayingMsgId] = useState<string | null>(null);
  const [playbackQueued, setPlaybackQueued] = useState(0);
  const [autoVoice, setAutoVoice] = useState<boolean>(() => {
    // localStorage 保存 UI 偏好（不是聊天数据库）
    return localStorage.getItem('auto_voice_reply') === '1';
  });

  // 订阅统一播放器状态（PlaybackInfo：状态 + 正在播的消息 + 队列进度）
  useEffect(() => {
    return ttsPlayer.subscribe((info) => {
      setPlaybackState(info.state);
      setPlayingMsgId(info.msgId);
      setPlaybackQueued(info.queued);
    });
  }, []);

  // 音色：优先使用学生在设置页选定的音色；未选则用后端默认（CosyVoice 预置音色）
  useEffect(() => {
    const saved = localStorage.getItem('tts_voice') || '';
    ttsPlayer.setVoice(saved || undefined);
  }, []);

  // 带上下文跳转预设（阶段一：从学习计划"去自测/看笔记"进入）
  // 应用后立即通知父组件清理，避免切换学科/重渲染时重复应用
  useEffect(() => {
    if (!preset) return;
    if (preset.mode) setMode(preset.mode);
    if (preset.input) setInputValue(preset.input);
    if (preset.taskId) setSourceTaskId(preset.taskId);
    onPresetConsumed?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset]);

  // 自动语音回复开关持久化
  useEffect(() => {
    localStorage.setItem('auto_voice_reply', autoVoice ? '1' : '0');
  }, [autoVoice]);

  // 上一个会话 id：用于区分"自己新建会话（空→新 id）"与"用户切换会话"
  const lastConvRef = useRef<string | undefined>(conversationId);

  // autoVoice 的 ref：流式回调里需要读到最新值（闭包会拿到旧状态）
  const autoVoiceRef = useRef(autoVoice);
  useEffect(() => {
    autoVoiceRef.current = autoVoice;
  }, [autoVoice]);

  // 模型选择持久化（只在有具体选择时写入；空值表示"跟随后端默认模型"）
  useEffect(() => {
    if (selectedModel) localStorage.setItem('chat_model', selectedModel);
  }, [selectedModel]);

  // 载入后端默认文本模型 + 可用模型列表，并处理旧版本遗留的 MiMo 默认值
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res: any = await getModelStatus();
        const data = res?.data || {};
        const def = data.default_model || 'deepseek';
        const models: { value: string; label: string }[] =
          (data.models || [])
            .filter((m: any) => ['deepseek', 'mimo', 'qwen'].includes(m.name))
            .map((m: any) => ({ value: m.name, label: m.display_name || m.name }));
        if (cancelled) return;
        setServerDefaultModel(def);
        if (models.length) setModelOptions(models);

        const saved = localStorage.getItem('chat_model') || '';
        const explicit = localStorage.getItem('chat_model_explicit') === '1';
        const noticed = localStorage.getItem('model_default_notice_v1') === '1';

        // 旧版本在未选择时也会把默认值(mimo)写入本地存储，无法与"学生主动选择"区分：
        // 因此只做**一次**提示 + 一次切换，之后完全尊重学生自己的选择，不再强制覆盖。
        if (saved && saved === 'mimo' && !explicit && !noticed) {
          localStorage.setItem('chat_model', def);
          localStorage.setItem('model_default_notice_v1', '1');
          setSelectedModel(def);
          message.info(
            `MiMo 会员已到期，系统默认文本模型已切换为 ${def}。`
            + '检测到你本地保存的是 MiMo（旧版本在未选择时会自动保存默认模型，无法区分是否你主动选择），'
            + '本次已改用默认模型；如需继续用 MiMo，可在下方"当前模型"中重新选择，之后不再自动改动。',
            10,
          );
          return;
        }
        if (!saved) {
          // 新用户 / 新会话 / 从未设置过 → 跟随后端默认模型
          setSelectedModel(def);
        }
      } catch {
        /* 取不到后端模型状态时保持静态列表，不影响问答 */
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // 学生在下拉中主动选择模型 → 记为显式选择，后端/前端都不再自动改动
  const handleModelChange = useCallback((value: string) => {
    setSelectedModel(value);
    localStorage.setItem('chat_model_explicit', '1');
  }, []);

  // 组件卸载时中止流式请求 + 停止录音/播放
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      agentAbortRef.current?.abort();
      recorderRef.current?.cancel();
      ttsPlayer.stop();
    };
  }, []);

  // 切换学科/会话时停止当前播放，避免上一个会话的回答继续朗读。
  //
  // 关键区别（否则自动朗读会"时好时坏"）：
  // - 发送第一条消息会**新建会话**：conversationId 由空变成新 id → 不算切换，不能停
  // - 用户从一个已有会话切到另一个（或新建对话把会话清空）→ 必须立即停止
  useEffect(() => {
    const prev = lastConvRef.current;
    lastConvRef.current = conversationId;
    if (prev && prev !== conversationId) {
      ttsPlayer.stop();
    }
  }, [conversationId, subjectId]);

  // ===== 录音逻辑 =====
  const handleToggleRecord = async () => {
    // 设置页关闭了"语音输入" → 提示并直接返回（文字输入不受影响）
    if (localStorage.getItem('voice_input_enabled') === '0') {
      message.info('语音输入已在「设置 - 语音」中关闭，可先开启或直接输入文字');
      return;
    }
    if (recording) {
      // 停止录音
      const recorder = recorderRef.current;
      recorderRef.current = null;
      setRecording(false);
      setRecordingSeconds(0);
      if (!recorder) return;
      let blob: Blob;
      try {
        blob = await recorder.stop();
      } catch (e) {
        message.info('录音已取消');
        return;
      }
      if (blob.size === 0) {
        message.warning('未捕获到声音');
        return;
      }
      // ASR 转文本，填入输入框（不自动发送）
      setTranscribing(true);
      try {
        const res: any = await transcribeAudio(blob);
        const text = res?.data?.text || '';
        if (text) {
          setInputValue((prev) => (prev ? prev + text : text));
          const svc = res?.data?.service === 'local' ? '本地' : '云端';
          message.success(`语音识别完成（${svc}）：请确认文字后发送`, 4);
        } else {
          message.warning('未识别到内容，请重试');
        }
      } catch (e: any) {
        const errType = e?.response?.data?.error_type;
        const serverMsg = e?.response?.data?.message;
        let msg = '语音识别失败，请重试';
        if (errType === 'microphone_permission_denied') msg = '未获得麦克风权限';
        else if (errType === 'unsupported_audio' || errType === 'audio_invalid_format') msg = '不支持的音频格式';
        else if (errType === 'audio_too_large') msg = '音频过大，请缩短录音';
        else if (errType === 'timeout') msg = '语音识别超时，请重试';
        else if (errType === 'asr_empty_result') msg = '没有听清，请重新录制或直接输入文字';
        else if (['speech_unavailable', 'engine_not_ready', 'model_missing',
          'dependency_missing', 'cuda_oom', 'cuda_error'].includes(errType)) {
          // 本地语音不可用：如实说明原因（文字输入不受影响）
          msg = serverMsg || '本地语音识别当前不可用，可以先用文字提问';
        }
        message.error(msg, 6);
      } finally {
        setTranscribing(false);
      }
      return;
    }

    // 开始录音
    try {
      const recorder = createRecorder((sec) => setRecordingSeconds(Math.round(sec)));
      await recorder.start();
      recorderRef.current = recorder;
      setRecording(true);
      setRecordingSeconds(0);
    } catch (e: any) {
      const name = e?.name;
      let msg = '无法访问麦克风';
      if (name === 'NotAllowedError') msg = '未获得麦克风权限，请在浏览器设置中允许';
      else if (name === 'NotFoundError') msg = '未检测到麦克风设备';
      message.error(msg);
    }
  };

  // 取消录音
  const handleCancelRecord = () => {
    recorderRef.current?.cancel();
    recorderRef.current = null;
    setRecording(false);
    setRecordingSeconds(0);
  };

  // ===== TTS 播放（V2.5.3 统一控制器）=====
  // useCallback 稳定引用：避免传入 ChatMessageItem 时破坏 React.memo
  const playTTS = useCallback(async (msg: ChatMessage) => {
    const text = msg.content;
    if (!text || !text.trim()) return;
    // 手动🔊朗读（后端按句切分，顺序播放），同一消息再次点击 → 暂停/恢复
    const voice = localStorage.getItem('tts_voice') || undefined;
    await ttsPlayer.toggle(msg.id, text, voice);
  }, []);

  // ===== 流式 delta 批处理（V2.5.4）=====
  // 将缓冲中的字符一次性写入 assistant 消息；返回是否真实写入了内容
  const flushStreamBuffer = useCallback((): boolean => {
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    const buf = pendingBufferRef.current;
    pendingBufferRef.current = '';
    const aiId = aiIdRef.current;
    if (!buf || !aiId) return false;
    setMessages(prev => {
      const exists = prev.some(m => m.id === aiId);
      if (!exists) {
        // 被历史加载覆盖（不应发生，防御性兜底补回）
        return [...prev, { id: aiId, role: 'assistant', content: buf, timestamp: Date.now() }];
      }
      return prev.map(m => (m.id === aiId ? { ...m, content: m.content + buf } : m));
    });
    return true;
  }, []);

  // 追加 delta 到缓冲，按 STREAM_BATCH_MS 合并一次渲染
  const enqueueDelta = useCallback((content: string) => {
    pendingBufferRef.current += content;
    if (!flushTimerRef.current) {
      flushTimerRef.current = setTimeout(() => { flushStreamBuffer(); }, STREAM_BATCH_MS);
    }
  }, [flushStreamBuffer]);

  // ===== 自动滚动（V2.5.4）=====
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    // 距底部 < 120px 视为"靠近底部"
    isNearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }, []);

  // 滚动到底部：流式期间 auto（不触发平滑动画）；用户主动发送/切换时 smooth
  const scrollToBottom = useCallback((smooth: boolean) => {
    messagesEndRef.current?.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'end' });
  }, []);

  // 规范化参考来源列表：去重（同文件+同页）、最多5条、过滤无效
  const normalizeSources = useCallback((sources: SourceInfo[] | undefined): SourceInfo[] => {
    if (!Array.isArray(sources) || sources.length === 0) return [];
    const seen = new Set<string>();
    const result: SourceInfo[] = [];
    for (const s of sources) {
      if (!s || typeof s !== 'object') continue;
      const file = s.file_name || '';
      const page = s.page != null ? s.page : '';
      const key = `${file}|${page}`;
      if (seen.has(key)) continue;
      seen.add(key);
      result.push({ id: result.length + 1, file_name: file, page: s.page, type: s.type });
      if (result.length >= 5) break;
    }
    return result;
  }, []);

  // 将 /health/ready 状态映射为顶部标签
  const applyRetrievalTag = (ready: any) => {
    const rm = ready?.retrieval_mode;
    const faissOk = ready?.faiss?.available;
    const bm25Ok = ready?.bm25?.available;
    if (rm === 'hybrid_ready' || (rm === 'not_loaded' && faissOk && bm25Ok)) {
      setRetrievalTag({ color: 'success', text: '混合检索已就绪' });
    } else if (rm === 'loading') {
      setRetrievalTag({ color: 'processing', text: '向量检索组件正在加载' });
    } else if (rm === 'bm25_degraded') {
      setRetrievalTag({ color: 'warning', text: '向量检索暂不可用，当前仅使用关键词检索，回答质量可能下降' });
    } else {
      setRetrievalTag({ color: 'error', text: '知识库检索服务不可用' });
    }
  };

  // 加载检索服务状态（失败不阻塞聊天）
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const ready = await getReadyStatus();
        if (mounted) applyRetrievalTag(ready);
      } catch (error) {
        if (mounted) {
          setRetrievalTag({ color: 'error', text: '知识库检索服务不可用' });
        }
      }
    })();
    return () => { mounted = false; };
  }, []);

  // 后端 Message → 前端 ChatMessage（含结构化 payload 恢复）
  const backendMessageToChat = useCallback((m: ConversationMessage, idx: number): ChatMessage => ({
    id: m.id,
    role: m.role,
    content: m.content,
    timestamp: new Date(m.created_at).getTime() + idx,
    sources: m.sources && m.sources.length ? normalizeSources(m.sources) : undefined,
    model: m.model || undefined,
    message_type: m.message_type,
    payload: m.payload,
    interaction_id: m.interaction_id ?? null,
  }), [normalizeSources]);

  // 点击历史会话恢复：从后端 SQLite 加载消息（不依赖 localStorage）
  useEffect(() => {
    // 流式回复进行中不加载历史，避免覆盖本地正在显示的消息
    if (streamingRef.current) return;
    let cancelled = false;
    if (!conversationId) {
      setMessages([]);
      return;
    }
    (async () => {
      try {
        const res: any = await getConversationMessages(conversationId);
        const msgs = res?.data || [];
        if (!cancelled && !streamingRef.current) setMessages(msgs.map(backendMessageToChat));
      } catch (e) {
        if (!cancelled) message.error('加载历史消息失败');
      }
    })();
    return () => { cancelled = true; };
  }, [conversationId, backendMessageToChat]);

  // 自动滚动（V2.5.4）：
  // - 流式期间 messages 变化 → auto 跟随（无平滑动画开销）
  // - 仅当用户仍靠近底部才跟随；用户上滚历史时停止强制拉回
  useEffect(() => {
    if (!isNearBottomRef.current) return;
    scrollToBottom(false);
  }, [messages, scrollToBottom]);

  /**
   * 学习助手模式（阶段二）
   *
   * 与普通问答的区别：不直接把问题丢给模型，而是走受约束的 Agent 运行
   * （读学习记录 → 检查预算 → 诊断/复习/资料/计划 → 调整建议），
   * 前端展示每一步的执行状态，并可随时停止。
   */
  const handleSendAgent = async () => {
    const goal = inputValue.trim();
    if (!goal) {
      message.warning('请输入学习目标');
      return;
    }
    const userMessage: ChatMessage = {
      id: Date.now().toString(), role: 'user', content: goal, timestamp: Date.now(),
    };
    const aiId = (Date.now() + 1).toString();
    const aiMessage: ChatMessage = {
      id: aiId, role: 'assistant', content: '', timestamp: Date.now(),
      agent_steps: [],
    };
    setMessages((prev) => [...prev, userMessage, aiMessage]);
    setInputValue('');
    setAgentRunning(true);
    playSendSound();
    isNearBottomRef.current = true;

    const controller = new AbortController();
    agentAbortRef.current = controller;

    // 步骤状态直接写进消息，避免额外 state 与消息不同步
    const updateStep = (step: AgentStepView) => {
      setMessages((cur) => cur.map((m) => {
        if (m.id !== aiId) return m;
        const next = [...(m.agent_steps || [])];
        const idx = next.findIndex((s) => s.tool === step.tool && s.status === 'running');
        if (step.status === 'running') next.push(step);
        else if (idx >= 0) next[idx] = { ...next[idx], ...step };
        else next.push(step);
        return { ...m, agent_steps: next };
      }));
    };

    try {
      await runAgentStream(
        {
          subject_id: subjectId, goal, intent: 'study',
          // 沿用学生在界面上选择的模型（工具调用与问答使用同一模型）
          model: selectedModel || undefined,
        },
        {
          onStep: (s) => updateStep({
            tool: s.tool, label: s.label, status: s.status,
            summary: s.summary, error: s.error,
          }),
          onDone: (run) => {
            const failed = run.status === 'FAILED' || run.status === 'TIMEOUT';
            setMessages((cur) => cur.map((m) => m.id === aiId
              ? {
                  ...m,
                  content: run.goal ? '' : '',
                  agent_run: {
                    id: run.id, status: run.status, error: run.error,
                    steps: run.steps, results: run.results,
                  },
                }
              : m));
            if (run.status === 'CANCELLED') message.info('已停止本次安排');
            else if (failed) message.warning('部分步骤未完成，可查看下方说明或使用手动操作');
            else message.success('学习助手已完成本轮安排');
          },
          onError: (msg) => {
            setMessages((cur) => cur.map((m) => m.id === aiId
              ? { ...m, agent_run: { id: '', status: 'FAILED', error: msg } } : m));
            message.error(msg);
          },
        },
        controller.signal,
      );
    } catch (e: any) {
      if (e?.name !== 'AbortError') message.error('学习助手执行失败');
    } finally {
      setAgentRunning(false);
      agentAbortRef.current = null;
    }
  };

  /** 停止学习助手（断开连接即停止，后端会在下一步检查点退出） */
  const handleStopAgent = () => {
    agentAbortRef.current?.abort();
    agentAbortRef.current = null;
    setAgentRunning(false);
  };

  // 发送消息（真流式 + 服务端会话持久化）
  const handleSend = async () => {
    if (!inputValue.trim()) {
      message.warning('请输入问题');
      return;
    }

    if (!subjectId) {
      message.warning('请选择学科');
      return;
    }

    // 学习助手模式走单独的受约束运行入口
    if (mode === 'agent') {
      await handleSendAgent();
      return;
    }

    // 无会话时自动创建（绑定当前学科）
    let targetConvId = conversationId;
    if (!targetConvId) {
      try {
        const res: any = await createConversation(subjectId);
        targetConvId = res?.data?.id;
        if (targetConvId) onConversationCreated?.(targetConvId);
      } catch (e) {
        message.error('创建会话失败，请重试');
        return;
      }
    }

    // 添加用户消息
    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: inputValue.trim(),
      timestamp: Date.now(),
    };

    // 创建空的AI消息（先显示"正在思考..."）
    const aiId = (Date.now() + 1).toString();
    const aiMessage: ChatMessage = {
      id: aiId,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
    };

    setMessages(prev => [...prev, userMessage, aiMessage]);
    setInputValue('');
    setLoading(true);
    playSendSound();
    // 标记流式回复开始（屏蔽历史加载覆盖）
    streamingRef.current = true;
    // 记录当前流式消息 id 供 batch flush 使用
    aiIdRef.current = aiId;
    pendingBufferRef.current = '';
    // 用户发送消息：视为已回到底部，允许平滑跟随
    isNearBottomRef.current = true;

    const startedAt = Date.now();

    // 新提问：立刻停掉上一轮的朗读，避免旧音频与新回答叠音
    ttsPlayer.stop();
    // 自动朗读：为新回答开启一个流式朗读会话（形成完整句即开始合成）
    if (autoVoiceRef.current && mode === 'chat') {
      ttsPlayer.beginStream(aiMessage.id, {
        voice: localStorage.getItem('tts_voice') || undefined,
      });
    }

    const controller = sendMessageStream(
      {
        question: userMessage.content,
        subject_id: subjectId,
        // 未选择时不传，由后端默认文本模型决定（DeepSeek）
        model: selectedModel || undefined,
        conversation_id: targetConvId,
        mode,
      },
      {
        onStart: () => {
          // 检索完成，开始生成
        },
        onDelta: (content) => {
          // V2.5.4：delta 先进缓冲，按 ~40ms 合并一次渲染（不丢字符）
          enqueueDelta(content);
          // 自动朗读：形成完整句子就立刻合成播放，不必等全文结束
          if (autoVoiceRef.current && mode === 'chat') {
            ttsPlayer.pushStreamText(aiId, content);
          }
        },
        onDone: (ev) => {
          const elapsed = ev.elapsed != null ? ev.elapsed : (Date.now() - startedAt) / 1000;
          const finalContent = ev.full_answer || '';
          // done 必须立即 flush 剩余 buffer，不能丢最后几个字符
          flushStreamBuffer();
          // 收到完整回复：柔和提示音
          if (finalContent) {
            playReceiveSound();
          }
          setMessages(prev => {
            const exists = prev.some(m => m.id === aiId);
            const updated: ChatMessage = {
              ...aiMessage,
              content: finalContent,
              sources: mode === 'chat' ? normalizeSources(ev.sources) : undefined,
              model: ev.model,
              provider: ev.provider,
              latency: elapsed,
              ttft: ev.ttft,
              message_type: ev.message_type,
              payload: ev.payload,
              // 本轮教学策略与依据（画像 → 教学安排，可解释）
              teaching: ev.teaching || null,
            };
            if (!exists) {
              // 历史加载覆盖了本地消息，兜底追加（避免回复"消失"）
              return [...prev, updated];
            }
            return prev.map(m => (m.id === aiId ? updated : m));
          });
          setLoading(false);
          // 流式结束，恢复历史加载
          streamingRef.current = false;
          aiIdRef.current = '';
          refreshStatus();
          // 自动语音回复（V2.5.3：按 message_id 去重，仅 streaming→done 触发一次）
          if (autoVoiceRef.current && mode === 'chat' && finalContent) {
            // 流式已经把大部分句子读掉；这里补读最后一句/极短回答
            ttsPlayer.endStream(aiId, finalContent);
          }
        },
        onError: (errMsg) => {
          // error 也立即 flush，保留已收到的字符
          flushStreamBuffer();
          // 已生成的内容保留；空内容时显示错误提示（找不到aiId时兜底追加）
          setMessages(prev => {
            const exists = prev.some(m => m.id === aiId);
            if (!exists) {
              return [...prev, { ...aiMessage, content: `⚠️ ${errMsg}` }];
            }
            return prev.map(m =>
              m.id === aiId
                ? { ...m, content: m.content || `⚠️ ${errMsg}` }
                : m
            );
          });
          message.error(errMsg || '回答生成失败，请重试');
          setLoading(false);
          // 流式结束（失败也恢复历史加载）
          streamingRef.current = false;
          aiIdRef.current = '';
          refreshStatus();
        },
      }
    );
    abortRef.current = controller;
  };

  // 停止生成（保留已生成内容）
  const handleStop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    // V2.5.4：停止后立即 flush 剩余 buffer，保留已收到的字符
    flushStreamBuffer();
    setLoading(false);
    // 停止后恢复历史加载
    streamingRef.current = false;
    aiIdRef.current = '';
  };

  // 统一键盘交互（V2.5.1）：Enter 发送，Shift+Enter 换行，IME 候选不发送
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // IME 中文输入法候选状态下按 Enter：只确认候选，不发送
    if (e.nativeEvent.isComposing) return;
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
    // Shift+Enter → 保留默认换行行为
  };

  // 问答完成后刷新检索状态标签
  const refreshStatus = async () => {
    try {
      const ready = await getReadyStatus();
      applyRetrievalTag(ready);
    } catch (e) {
      /* 状态刷新失败不影响聊天 */
    }
  };

  // 消息渲染（V2.5.4：委托给 ChatMessageItem，React.memo 隔离流式更新）
  const renderItem = (msg: ChatMessage) => (
    <ChatMessageItem
      msg={msg}
      mode={mode}
      isPlaying={playingMsgId === msg.id && (playbackState === 'playing' || playbackState === 'requesting' || playbackState === 'paused')}
      playbackState={playingMsgId === msg.id ? playbackState : 'idle'}
      playbackQueued={playingMsgId === msg.id ? playbackQueued : 0}
      onPlay={playTTS}
      onStopPlay={() => ttsPlayer.stop()}
      subjectId={subjectId}
      conversationId={conversationId}
      sourceTaskId={sourceTaskId}
      agentRunning={agentRunning}
      onStopAgent={handleStopAgent}
    />
  );

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      background: '#fff',
      borderRadius: '12px',
      boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
      overflow: 'hidden',
    }}>
      {/* 头部 */}
      <div style={{
        padding: '16px 24px',
        borderBottom: '1px solid #f0f0f0',
        background: '#fafafa',
      }}>
        <Space>
          <Title level={5} style={{ margin: 0 }}>
            AI学习助手
          </Title>
          <Text type="secondary">
            当前学科: {subjects.find(s => s.subject_id === subjectId)?.name || '未选择'}
          </Text>
          {retrievalTag && (
            <Tag color={retrievalTag.color} style={{ marginLeft: 8 }}>
              {retrievalTag.text}
            </Tag>
          )}
        </Space>
      </div>

      {/* 消息列表（V2.5.4：onScroll 检测是否靠近底部；流式期间 auto 跟随） */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        style={{
          flex: 1,
          overflow: 'auto',
          padding: '24px',
          background: '#fff',
          maxHeight: 'calc(100vh - 260px)',
        }}
      >
        {messages.length === 0 ? (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100%',
            color: '#999',
          }}>
            <RobotOutlined style={{ fontSize: '48px', marginBottom: '16px' }} />
            <Text type="secondary">开始你的智能问答之旅</Text>
            <Text type="secondary" style={{ fontSize: '12px', marginTop: '8px' }}>
              选择学科后，输入你的问题
            </Text>
          </div>
        ) : (
          <List
            dataSource={messages}
            renderItem={renderItem}
            style={{ background: 'transparent' }}
          />
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* 输入区域 */}
      <div style={{
        padding: '16px 24px',
        borderTop: '1px solid #f0f0f0',
        background: '#fafafa',
      }}>
        {/* 学习模式选择（Segmented，轻量不占纵向空间） */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          marginBottom: '8px',
          flexWrap: 'wrap',
        }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {MODE_OPTIONS.map(opt => {
              const active = mode === opt.value;
              const color = MODE_COLORS[opt.value];
              return (
                <Tag
                  key={opt.value}
                  onClick={() => setMode(opt.value)}
                  style={{
                    cursor: 'pointer',
                    marginInlineEnd: 0,
                    padding: '2px 10px',
                    borderRadius: 6,
                    fontSize: 12,
                    fontWeight: active ? 600 : 450,
                    border: active ? `1px solid ${color}` : '1px solid #d1d5db',
                    background: active ? color : '#ffffff',
                    color: active ? '#ffffff' : '#475569',
                    transition: 'all 0.15s cubic-bezier(0.16, 1, 0.3, 1)',
                  }}
                >
                  {opt.label}
                </Tag>
              );
            })}
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
            <Text type="secondary" style={{ fontSize: '12px' }}>当前模型:</Text>
            <Select
              value={selectedModel || serverDefaultModel}
              onChange={handleModelChange}
              options={modelOptions}
              style={{ width: 120 }}
              size="small"
            />
          </div>
        </div>
        <Space.Compact style={{ width: '100%' }}>
          {/* 麦克风按钮（录音/停止，V2.5.1 Checkpoint D） */}
          <Tooltip title={recording ? '点击停止录音' : '语音输入'}>
            <Button
              icon={transcribing ? <Spin size="small" /> : <AudioOutlined />}
              onClick={handleToggleRecord}
              danger={recording}
              type={recording ? 'primary' : 'default'}
              style={{ height: 'auto' }}
            />
          </Tooltip>
          <TextArea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder={
              recording
                ? `录音中 ${String(Math.floor(recordingSeconds / 60)).padStart(2, '0')}:${String(recordingSeconds % 60).padStart(2, '0')} ...`
                : MODE_PLACEHOLDERS[mode] + ENTER_HINT
            }
            autoSize={{ minRows: 1, maxRows: 4 }}
            onKeyDown={handleKeyDown}
            disabled={loading}
            style={{ flex: 1 }}
          />
          {recording ? (
            <Button
              danger
              icon={<StopOutlined />}
              onClick={handleCancelRecord}
              style={{ height: 'auto' }}
            >
              取消
            </Button>
          ) : loading ? (
            <Button
              danger
              icon={<StopOutlined />}
              onClick={handleStop}
              style={{ height: 'auto' }}
            >
              停止生成
            </Button>
          ) : (
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSend}
              disabled={!inputValue.trim() || !subjectId}
              style={{ height: 'auto' }}
            >
              发送
            </Button>
          )}
        </Space.Compact>
        {/* 自动语音回复开关（默认关闭，localStorage UI 偏好） */}
        <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Switch size="small" checked={autoVoice} onChange={setAutoVoice} />
          <Text type="secondary" style={{ fontSize: 12 }}>自动语音回复</Text>
        </div>
      </div>
    </div>
  );
};

export default ChatBox;
