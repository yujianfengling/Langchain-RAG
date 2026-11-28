import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Alert, Button, Input, Popconfirm, Spin, Tooltip, message } from 'antd';
import {
  BugOutlined,
  CopyOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  FilePdfOutlined,
  FileWordOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
  SendOutlined,
  StopOutlined,
  SwapOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import MarkdownIt from 'markdown-it';
import { getToken } from '../../api/authState';
import { getBundle, getDatasetId } from '../dataSource';
import { QUIZZES, quizLabel } from '../shared/ids';
import { canUseExternalAi, LOCAL_DATA_NOTICE } from '../shared/externalAi';
import './assistant.css';

/* ------------------------------------------------------------------ */
/* 后端契约类型（/api/competition/agent/*）                              */
/* ------------------------------------------------------------------ */

interface AgentCard { kind: string; title: string; lines: string[] }
interface AgentFile { id: string; name: string; format: 'docx' | 'pdf' }
interface AgentMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  cards?: AgentCard[];
  files?: AgentFile[];
  created_at?: string;
}
interface ConversationMeta { id: string; title: string; updated_at?: string }
interface ChatResponse { conversation_id: string; message: AgentMessage }
interface ConversationDetail { id: string; title: string; messages: AgentMessage[] }

/** 错误态：不静默；带可重试动作（重发消息 / 重新加载会话 / 刷新列表）。 */
type PageError = {
  text: string;
  retry?: { type: 'send'; message: string } | { type: 'open'; id: string } | { type: 'list' };
};

const errText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', 'X-User-Token': getToken() || '' },
  });
  if (!res.ok) {
    const failure = await res.json().catch(() => null);
    throw new Error(typeof failure?.detail === 'string' ? failure.detail : `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

/** 简单 markdown 渲染：html:false 保证转义安全，breaks 保留换行。 */
const md = new MarkdownIt({ html: false, linkify: true, breaks: true });

function formatTime(value?: string): string {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  if (d.toDateString() === now.toDateString()) return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

/* ------------------------------------------------------------------ */
/* 快捷指令：前五个一点即发；「知识点讲解」只预填输入框                    */
/* ------------------------------------------------------------------ */

const QUICK_COMMANDS: { label: string; prompt: string }[] = [
  { label: '写讲评教案', prompt: '请调用 read_class 读取本班最近一次测验学情，再调用 generate_plan 生成一份40分钟讲评教案（含分层任务与教材出处）。' },
  { label: '出强化卷并导出Word', prompt: '请调用 read_class 读取本班薄弱考点，再调用 generate_paper 生成一份10题查漏补缺强化卷（风格变式提升，含答案与解析）。' },
  { label: '班级整体诊断', prompt: '请调用 read_class 与 read_exam 读取本班本次测验数据，诊断总体得分、薄弱知识点与共性错因，并给出讲评优先级。' },
  { label: '需关注学生点评', prompt: '请调用 read_class 找出本班得分率最低的学生，再调用 read_student 逐人读取历次学情，给出点评与课后辅导建议。' },
  { label: '下一步怎么讲', prompt: '请调用 read_class 与 read_curriculum 读取本班薄弱考点及课标/教材出处，给出下一节课先讲什么、怎么讲、怎么确认。' },
];
const PREFILL_PROMPT = '请讲解知识点：';

/** 空态建议卡片（ChatGPT 式双列网格）：第 4 条直接发送出卷工具指令。 */
const EXAMPLE_QUESTIONS: { icon: React.ReactNode; text: string; prompt: string }[] = [
  { icon: <SwapOutlined />, text: '这次考试两个班的差距主要在哪里？', prompt: '这次考试两个班的差距主要在哪里？' },
  { icon: <BugOutlined />, text: '帮我分析二元一次方程组的共性错因', prompt: '帮我分析二元一次方程组的共性错因' },
  { icon: <TeamOutlined />, text: '给及格线边缘的学生列一份基础巩固清单', prompt: '给及格线边缘的学生列一份基础巩固清单' },
  { icon: <FileWordOutlined />, text: '生成一份本班薄弱考点强化卷并导出Word', prompt: QUICK_COMMANDS[1].prompt },
];

/* ------------------------------------------------------------------ */
/* 单条消息（统一左对齐 + 角色图标 + 悬停操作条，参考 ChatGPT）           */
/* ------------------------------------------------------------------ */

const MessageView: React.FC<{
  msg: AgentMessage;
  downloadingId: string | null;
  onDownload: (file: AgentFile) => void;
  onCopy: (text: string) => void;
  onEdit?: (text: string) => void;
  onRegenerate?: () => void;
}> = ({ msg, downloadingId, onDownload, onCopy, onEdit, onRegenerate }) => {
  const html = useMemo(
    () => (msg.role === 'assistant' ? md.render(msg.text || '') : ''),
    [msg.role, msg.text],
  );
  const isUser = msg.role === 'user';
  return (
    <div className={'assistant-msg' + (isUser ? ' is-user' : ' is-assistant')} data-role={msg.role}>
      <div className="assistant-msg-avatar" aria-hidden="true">
        {isUser ? <UserOutlined /> : <RobotOutlined />}
      </div>
      <div className="assistant-msg-body">
        <div className="assistant-msg-role">{isUser ? '我' : 'AI 助手'}</div>
        {isUser
          ? <div className="assistant-msg-text">{msg.text}</div>
          : <div className="assistant-markdown" dangerouslySetInnerHTML={{ __html: html }} />}
        {msg.cards && msg.cards.length > 0 && (
          <div className="assistant-cards">
            {msg.cards.map((card, i) => (
              <div className="assistant-card" key={`${card.kind}-${i}`}>
                <div className="assistant-card-title">{card.title}</div>
                <ul className="assistant-card-lines">
                  {(card.lines || []).map((line, j) => <li key={j}>{line}</li>)}
                </ul>
              </div>
            ))}
          </div>
        )}
        {msg.files && msg.files.length > 0 && (
          <div className="assistant-files">
            {msg.files.map((file) => (
              <Button
                key={file.id}
                className="assistant-file-btn"
                icon={file.format === 'pdf' ? <FilePdfOutlined /> : <FileWordOutlined />}
                loading={downloadingId === file.id}
                onClick={() => onDownload(file)}
              >
                <span className="assistant-file-name">{file.name}</span>
                <DownloadOutlined />
              </Button>
            ))}
          </div>
        )}
        <div className="assistant-msg-actions">
          <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => onCopy(msg.text)} aria-label="复制消息">
            复制
          </Button>
          {isUser && onEdit && (
            <Button type="text" size="small" icon={<EditOutlined />} onClick={() => onEdit(msg.text)} aria-label="编辑消息">
              编辑
            </Button>
          )}
          {onRegenerate && (
            <Button type="text" size="small" icon={<ReloadOutlined />} onClick={onRegenerate} aria-label="重新生成回复">
              重新生成
            </Button>
          )}
        </div>
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* 页面                                                                 */
/* ------------------------------------------------------------------ */

const AssistantPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const bundle = getBundle();
  const externalAi = canUseExternalAi();
  const dataset = bundle?.manifest.source === 'official' ? getDatasetId() : '';
  const selectedClass = bundle?.classes.find((c) => c.id === searchParams.get('classId'));
  const selectedQuiz = bundle?.quizzes.find((q) => q.id === searchParams.get('quizId'));
  const classId = selectedClass?.id;
  const quizId = selectedQuiz?.id;

  const [conversations, setConversations] = useState<ConversationMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [loadingConv, setLoadingConv] = useState(false);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [error, setError] = useState<PageError | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(() => window.innerWidth > 900);
  const [availH, setAvailH] = useState<number | null>(null);
  const [convSearch, setConvSearch] = useState('');

  // ref 镜像：让 send / open 等回调保持稳定，且避免闭包读到旧值
  const activeIdRef = useRef<string | null>(null);
  const sendingRef = useRef(false);
  const autoSentRef = useRef(false); // 深链只自动发送一次
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<TextAreaRef | null>(null);
  const abortRef = useRef<AbortController | null>(null); // 停止生成

  /* 高度自适应：comp-main 是唯一滚动容器，聊天页要在其内部自滚动。 */
  useEffect(() => {
    const main = document.querySelector('.comp-main');
    if (!main) return;
    const update = () => setAvailH(main.clientHeight);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(main);
    return () => ro.disconnect();
  }, []);

  const refreshConversations = useCallback(async () => {
    try {
      const data = await apiFetch<{ conversations: ConversationMeta[] }>('/api/competition/agent/conversations');
      setConversations(data.conversations || []);
    } catch (e) {
      setError({ text: `对话列表加载失败：${errText(e)}`, retry: { type: 'list' } });
    }
  }, []);

  useEffect(() => { void refreshConversations(); }, [refreshConversations]);

  /**
   * 发送消息。opts.optimistic=false 用于「重新生成」：
   * user 气泡已在消息流里，不再追加乐观气泡、不清空输入框草稿。
   */
  const send = useCallback(async (raw: string, opts?: { optimistic?: boolean }) => {
    const text = raw.trim();
    if (!externalAi || !text || sendingRef.current) return;
    const optimisticEnabled = opts?.optimistic !== false;
    sendingRef.current = true;
    setSending(true);
    setError(null);
    if (optimisticEnabled) setInput('');
    const optimistic: AgentMessage | null = optimisticEnabled
      ? { id: `local-${Date.now()}`, role: 'user', text }
      : null;
    if (optimistic) setMessages((prev) => [...prev, optimistic]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const data = await apiFetch<ChatResponse>('/api/competition/agent/chat', {
        method: 'POST',
        body: JSON.stringify({
          conversation_id: activeIdRef.current, message: text,
          scope: dataset && classId && quizId ? { dataset, class_id: classId, quiz_id: quizId } : null,
        }),
        signal: controller.signal,
      });
      activeIdRef.current = data.conversation_id;
      setActiveId(data.conversation_id);
      setMessages((prev) => [...prev, data.message]);
      void refreshConversations();
    } catch (e) {
      if (controller.signal.aborted) {
        // 用户主动停止：不算错误，保留已发送的问题，追加一条本地说明
        setMessages((prev) => [...prev, {
          id: `local-stop-${Date.now()}`,
          role: 'assistant',
          text: '已停止本次回复，可继续提问或重新生成。',
        }]);
      } else {
        // 撤掉乐观气泡，保留原文供一键重试，避免重复消息
        if (optimistic) setMessages((prev) => prev.filter((m) => m.id !== optimistic.id));
        setError({ text: `发送失败：${errText(e)}`, retry: { type: 'send', message: text } });
      }
    } finally {
      abortRef.current = null;
      sendingRef.current = false;
      setSending(false);
    }
  }, [refreshConversations, dataset, classId, quizId, externalAi]);

  const stopGenerating = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const newConversation = useCallback(() => {
    if (sendingRef.current) return;
    activeIdRef.current = null;
    setActiveId(null);
    setMessages([]);
    setError(null);
    setInput('');
    inputRef.current?.focus();
  }, []);

  const openConversation = useCallback(async (id: string) => {
    if (sendingRef.current || activeIdRef.current === id) return;
    activeIdRef.current = id;
    setActiveId(id);
    setMessages([]);
    setError(null);
    setLoadingConv(true);
    try {
      const data = await apiFetch<ConversationDetail>(`/api/competition/agent/conversations/${encodeURIComponent(id)}`);
      if (activeIdRef.current !== id) return; // 已切走：丢弃陈旧响应
      setMessages(data.messages || []);
    } catch (e) {
      setError({ text: `对话加载失败：${errText(e)}`, retry: { type: 'open', id } });
    } finally {
      setLoadingConv(false);
    }
  }, []);

  const deleteConversation = useCallback(async (id: string) => {
    try {
      await apiFetch<{ ok: boolean }>(`/api/competition/agent/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' });
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (activeIdRef.current === id) newConversation();
    } catch (e) {
      setError({ text: `删除对话失败：${errText(e)}`, retry: { type: 'list' } });
    }
  }, [newConversation]);

  const downloadFile = useCallback(async (file: AgentFile) => {
    setDownloadingId(file.id);
    try {
      const res = await fetch(`/api/competition/agent/file/${encodeURIComponent(file.id)}`, {
        headers: { 'X-User-Token': getToken() || '' },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.name || `file.${file.format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError({ text: `文件下载失败：${errText(e)}` });
    } finally {
      setDownloadingId(null);
    }
  }, []);

  /* 深链：/assistant?prompt=...&autosend=1 → 无当前对话则新建并自动发送（仅一次）。 */
  useEffect(() => {
    if (autoSentRef.current) return;
    const prompt = searchParams.get('prompt');
    if (!prompt) return;
    autoSentRef.current = true;
    if (searchParams.get('autosend') === '1') {
      if (!activeIdRef.current) {
        activeIdRef.current = null; // 挂载即新对话，conversation_id 传 null 由后端创建
        setActiveId(null);
        setMessages([]);
      }
      void send(prompt);
    } else {
      setInput(prompt);
      inputRef.current?.focus();
    }
    const next = new URLSearchParams(searchParams);
    next.delete('prompt');
    next.delete('autosend');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams, send]);

  /* 新消息 / loading 气泡出现时滚到底部。 */
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending, loadingConv]);

  const retry = () => {
    if (!error?.retry) return;
    const r = error.retry;
    setError(null);
    if (r.type === 'send') void send(r.message);
    else if (r.type === 'open') void openConversation(r.id);
    else void refreshConversations();
  };

  const prefillKnowledge = () => {
    setInput(PREFILL_PROMPT);
    inputRef.current?.focus();
  };

  const onInputKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 中文输入法组词中的 Enter 不发送
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(input);
    }
  };

  /* 复制：优先 Clipboard API，失败回退 textarea + execCommand。 */
  const copyText = useCallback(async (text: string) => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(text);
      message.success('已复制');
    } catch {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand('copy');
        ta.remove();
        if (ok) message.success('已复制');
        else message.error('复制失败，请手动选择文本复制');
      } catch {
        message.error('复制失败，请手动选择文本复制');
      }
    }
  }, []);

  /* 编辑：把原文填回输入框并聚焦。 */
  const editMessage = useCallback((text: string) => {
    setInput(text);
    inputRef.current?.focus();
  }, []);

  /* 重新生成：截掉最后一条 user 之后的 assistant 回复，重发原文（不追加乐观气泡）。 */
  const regenerate = () => {
    if (sendingRef.current) return;
    const lastUser = [...messages].reverse().find((m) => m.role === 'user');
    if (!lastUser) return;
    const idx = messages.findIndex((m) => m.id === lastUser.id);
    setMessages(messages.slice(0, idx + 1));
    void send(lastUser.text, { optimistic: false });
  };

  const activeTitle = conversations.find((c) => c.id === activeId)?.title;
  const showEmpty = !loadingConv && messages.length === 0 && !sending;
  const lastAssistantId = useMemo(
    () => [...messages].reverse().find((m) => m.role === 'assistant')?.id ?? null,
    [messages],
  );

  /* 侧栏搜索：按标题过滤。 */
  const filteredConversations = useMemo(() => {
    const q = convSearch.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter((c) => (c.title || '未命名对话').toLowerCase().includes(q));
  }, [conversations, convSearch]);

  const hasData = !!(dataset && selectedClass && selectedQuiz);
  const quiz = QUIZZES.find((q) => q.id === quizId);
  const dataBadgeText = hasData
    ? `${bundle?.manifest.independentEvaluation ? '独立评测数据' : '官方赛题数据'} · 班级 ${classId} · ${quiz ? quizLabel(quiz) : quizId}`
    : '请先在考试分析中选择班级和测验';

  /* 导出当前对话为 Markdown（纯客户端生成，不走后端）。 */
  const exportConversation = () => {
    if (messages.length === 0) return;
    const title = activeTitle || '新对话';
    const parts = messages.map((m) => {
      const head = `**${m.role === 'user' ? '我' : 'AI 助手'}**`;
      const files = m.files && m.files.length > 0
        ? `\n\n附件：${m.files.map((f) => f.name).join('、')}`
        : '';
      return `${head}\n\n${m.text || ''}${files}`;
    });
    const content = `# ${title}\n\n${parts.join('\n\n---\n\n')}\n`;
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${title.replace(/[\\/:*?"<>|]/g, '_')}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    message.success('已导出当前对话');
  };

  return (
    <div
      className={'assistant-shell' + (sidebarOpen ? '' : ' is-collapsed')}
      style={{ height: availH ?? 'calc(100dvh - 64px)' }}
      data-testid="assistant-page"
    >
      <aside className="assistant-sidebar" aria-label="对话历史">
        <div className="assistant-sidebar-head">
          <Button block type="primary" icon={<PlusOutlined />} onClick={newConversation} disabled={sending}>
            新对话
          </Button>
          <Input
            className="assistant-sidebar-search"
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索对话"
            aria-label="搜索对话"
            value={convSearch}
            onChange={(e) => setConvSearch(e.target.value)}
          />
        </div>
        <div className="assistant-conv-list">
          {filteredConversations.map((c) => (
            <div key={c.id} className={'assistant-conv-item' + (c.id === activeId ? ' is-active' : '')}>
              <button
                type="button"
                className="assistant-conv-open"
                onClick={() => {
                  void openConversation(c.id);
                  if (window.innerWidth <= 900) setSidebarOpen(false);
                }}
              >
                <span className="assistant-conv-title">{c.title || '未命名对话'}</span>
                <span className="assistant-conv-time">{formatTime(c.updated_at)}</span>
              </button>
              <Popconfirm
                title="删除该对话？"
                description="删除后不可恢复。"
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => { void deleteConversation(c.id); }}
              >
                <button type="button" className="assistant-conv-del" aria-label={`删除对话：${c.title || c.id}`}>
                  <DeleteOutlined />
                </button>
              </Popconfirm>
            </div>
          ))}
          {conversations.length === 0 && <div className="assistant-conv-empty">暂无历史对话</div>}
          {conversations.length > 0 && filteredConversations.length === 0 && (
            <div className="assistant-conv-empty">没有匹配的对话</div>
          )}
        </div>
      </aside>
      {sidebarOpen && (
        <div className="assistant-scrim" aria-hidden="true" onClick={() => setSidebarOpen(false)} />
      )}

      <section className="assistant-main">
        <header className="assistant-topbar">
          <Button
            type="text"
            size="small"
            icon={sidebarOpen ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />}
            onClick={() => setSidebarOpen((v) => !v)}
            aria-label={sidebarOpen ? '收起侧栏' : '展开侧栏'}
          />
          <span className="assistant-topbar-title">{activeTitle || '新对话'}</span>
          <div className="assistant-topbar-right">
            <Tooltip title="学情问题按当前数据、班级和测验读取">
              <span className={'assistant-data-badge' + (hasData ? '' : ' is-empty')}>
                <DatabaseOutlined /> {dataBadgeText}
              </span>
            </Tooltip>
            <Button
              size="small"
              icon={<DownloadOutlined />}
              disabled={messages.length === 0}
              onClick={exportConversation}
              aria-label="导出当前对话"
            >
              导出
            </Button>
          </div>
        </header>

        {!externalAi && <Alert type="info" showIcon style={{ margin: '12px 16px 0' }}
          message={bundle && dataset ? '赛题数据受本地保护' : '外部 AI 需要导入评测数据'}
          description={LOCAL_DATA_NOTICE} />}
        <div className="assistant-scroll" ref={scrollRef}>
          {loadingConv ? (
            <div className="assistant-center"><Spin tip="正在加载对话…" /></div>
          ) : showEmpty ? (
            <div className="assistant-empty">
              <div className="assistant-empty-icon"><RobotOutlined /></div>
              <h1>我是教学 AI 助手</h1>
              <p>基于两班学情、学生答卷与课标教材，帮你完成讲评备课、班级诊断与强化出题。</p>
              <div className="assistant-empty-chips">
                {EXAMPLE_QUESTIONS.map((q) => (
                  <button
                    key={q.text}
                    type="button"
                    className="assistant-example-chip"
                    disabled={!externalAi}
                    onClick={() => { void send(q.prompt); }}
                  >
                    <span className="assistant-example-icon" aria-hidden="true">{q.icon}</span>
                    <span className="assistant-example-text">{q.text}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="assistant-thread">
              {messages.map((m) => (
                <MessageView
                  key={m.id}
                  msg={m}
                  downloadingId={downloadingId}
                  onDownload={(f) => { void downloadFile(f); }}
                  onCopy={(text) => { void copyText(text); }}
                  onEdit={m.role === 'user' ? editMessage : undefined}
                  onRegenerate={!sending && m.id === lastAssistantId ? regenerate : undefined}
                />
              ))}
              {sending && (
                <div className="assistant-msg is-assistant" role="status" aria-label="AI 正在回复">
                  <div className="assistant-msg-avatar" aria-hidden="true"><RobotOutlined /></div>
                  <div className="assistant-msg-body">
                    <div className="assistant-msg-role">AI 助手</div>
                    <div className="assistant-typing"><Spin size="small" /> 正在思考…</div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {error && (
          <div className="assistant-error">
            <Alert
              type="error"
              showIcon
              closable
              message={error.text}
              onClose={() => setError(null)}
              action={error.retry ? <Button size="small" danger onClick={retry}>重试</Button> : undefined}
            />
          </div>
        )}

        <footer className="assistant-composer">
          <div className="assistant-quick" aria-label="快捷指令">
            {QUICK_COMMANDS.map((cmd) => (
              <button
                key={cmd.label}
                type="button"
                className="assistant-chip"
                disabled={sending || !externalAi}
                onClick={() => { void send(cmd.prompt); }}
              >
                {cmd.label}
              </button>
            ))}
            <button type="button" className="assistant-chip is-prefill" disabled={sending || !externalAi} onClick={prefillKnowledge}>
              <EditOutlined /> 知识点讲解
            </button>
          </div>
          <div className="assistant-input-row">
            <Input.TextArea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onInputKeyDown}
              placeholder={externalAi ? '问点什么，例如：这次考试哪些知识点最需要重讲？（Enter 发送，Shift+Enter 换行）' : '导入合成或自定义评测数据后可提问'}
              autoSize={{ minRows: 1, maxRows: 6 }}
              disabled={sending || !externalAi}
              aria-label="消息输入框"
            />
            {sending ? (
              <Button
                danger
                shape="circle"
                className="assistant-send-btn"
                icon={<StopOutlined />}
                onClick={stopGenerating}
                aria-label="停止生成"
              />
            ) : (
              <Button
                type="primary"
                shape="circle"
                className="assistant-send-btn"
                icon={<SendOutlined />}
                disabled={!externalAi || !input.trim()}
                onClick={() => { void send(input); }}
                aria-label="发送"
              />
            )}
          </div>
          <div className="assistant-hint">AI 生成内容仅供参考，请结合学情证据核对后使用。</div>
        </footer>
      </section>
    </div>
  );
};

export default AssistantPage;
