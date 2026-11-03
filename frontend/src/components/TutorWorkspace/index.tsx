import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Button, Checkbox, Collapse, Drawer, Dropdown, Input, Popover, Segmented, Select, Space, Spin,
  Tag, Tooltip, Typography, message as antdMessage,
} from 'antd';
import {
  AudioOutlined, BookOutlined, BulbOutlined, CheckCircleOutlined, CloseOutlined, DatabaseOutlined,
  DownOutlined, FileTextOutlined, FormOutlined, HighlightOutlined, HistoryOutlined,
  MenuOutlined, MoreOutlined,
  PartitionOutlined, PlusOutlined, ReloadOutlined, RightOutlined, SendOutlined,
  StopOutlined,
  ReadOutlined, AimOutlined,
} from '@ant-design/icons';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { useCourse } from '../../context/CourseContext';
import { useIsNarrow } from '../../hooks/useIsNarrow';
import {
  deleteArtifact, generateKnowledgeMap, getKnowledgeMap, getMapNode,
  setNodeStatus, cancelTurn,
  getTurn, getTutorSession, listArtifacts, listTutorConversations, listTurns, streamTutorTurn, updateArtifact, updateTutorSession,
  type GuidanceMode, type TutorSource,
} from '../../api/tutor';
import { useAuth } from '../../context/AuthContext';
import { MarkdownText } from './markdown';
import MapTree, { type MapNode } from './MapTree';
import PracticeCard, { type PracticeQuestion } from './PracticeCard';
import SourcePanel from './SourcePanel';
import WhiteboardModal from './WhiteboardModal';
import LectureNotesModal from './LectureNotesModal';
import './tutor.css';

const { Text, Title } = Typography;
const { TextArea } = Input;

/**
 * 路线状态的中文兜底。
 * 后端 capabilities/知识地图会给 status_labels，但缺失时界面绝不能回落到英文键
 * （实测截图里出现过 new/learning/practicing/passed/needs_review 的英文图例）。
 * 状态含义保持真实业务口径：一次"检测通过"不等于永久掌握，复习后仍会回到待复习。
 */
const STATUS_FALLBACK: Record<string, string> = {
  new: '未开始',
  learning: '学习中',
  practicing: '待检测',
  passed: '检测通过',
  needs_review: '待复习',
};

export interface TutorWorkspaceProps {
  initialSubjectId?: string;
  initialDocIds?: string[];
  initialNodeId?: string;
  role?: 'student' | 'teacher';
  greeting?: string;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  streaming?: boolean;
  error?: string;
  sources?: TutorSource[];
  citedIds?: number[];
  trace?: { tool: string; label: string; summary: string; ok: boolean }[];
  messageType?: string;
  payload?: any;
  askUser?: { question: string; options: string[] } | null;
  notices?: { level: string; message: string }[];
}

/** 把历史轮次转成消息气泡。
 *
 *  接口按时间**倒序**返回，这里翻回正序；一问一答各占一条，
 *  这样点知识点时看到的就是"当时的对话"，而不是一堆摘要。
 */
const historyToMessages = (rows: any[]): ChatMessage[] => {
  const out: ChatMessage[] = [];
  [...(rows || [])].reverse().forEach((t) => {
    if (t.user_message) {
      out.push({ id: 'hist-u-' + t.id, role: 'user', content: t.user_message });
    }
    if (t.answer) {
      out.push({ id: 'hist-a-' + t.id, role: 'assistant', content: t.answer, error: t.status === 'CANCELLED' ? '本轮已中断' : t.status === 'FAILED' ? '本轮未完成' : undefined });
    }
    if (!t.answer && ['CANCELLED', 'FAILED'].includes(t.status)) {
      out.push({ id: 'hist-a-' + t.id, role: 'assistant', content: '',
        error: t.status === 'CANCELLED' ? '本轮已中断' : '本轮未完成' });
    }
    // 这一轮出的题挂在**同一条历史**下面：题目属于那次出题，
    // 删掉这条历史时它们一起消失（后端软删），不再另起一块。
    if (Array.isArray(t.questions) && t.questions.length > 0) {
      out.push({
        id: 'hist-q-' + t.id,
        role: 'assistant',
        content: '',
        messageType: 'quiz',
        payload: { quiz: t.questions },
      });
    }
  });
  return out;
};

const TutorWorkspace: React.FC<TutorWorkspaceProps> = ({
  initialSubjectId, initialDocIds, initialNodeId, role = 'student', greeting,
}) => {
  const ctx = useCourse();
  const navigate = useNavigate();
  const isNarrow = useIsNarrow();
  const reduceMotion = useReducedMotion();
  /**
   * 右栏（引用原文/学习成果/执行详情）在 1366px 及以下改为**覆盖式抽屉**：
   * 并排展开会把正文挤到难读（实测 1366px 时三栏只剩 ~700px），
   * 抽屉只覆盖、不改变主区宽度，关闭后空间立即归还。
   */
  const isNarrowForPanel = useIsNarrow('(max-width: 1559px)');
  const [subjectId, setSubjectId] = useState<string>(initialSubjectId || '');
  const [sessionLoading, setSessionLoading] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [materials, setMaterials] = useState<any[]>([]);
  const [selectedDocs, setSelectedDocs] = useState<string[]>(initialDocIds || []);
  const [scopeLabel, setScopeLabel] = useState('');
  const [scopeInvalid, setScopeInvalid] = useState<any[]>([]);

  const [ability, setAbility] = useState('chat');
  // 讲解方式：direct=直接回答（默认，保持旧行为）；socratic=先问再提示，不直接给答案。
  const [guidanceMode, setGuidanceMode] = useState<GuidanceMode>('direct');
  const [modeSaving, setModeSaving] = useState(false);
  const [boardOpen, setBoardOpen] = useState(false);
  /** 白板当前要展示的课件依据：直接来自本轮回答，或从历史轮次的事件里恢复 */
  const [boardSources, setBoardSources] = useState<TutorSource[]>([]);
  const [boardCitedIds, setBoardCitedIds] = useState<number[]>([]);
  const [lectureOpen, setLectureOpen] = useState(false);
  const [map, setMap] = useState<any>(null);
  const [mapLoading, setMapLoading] = useState(false);
  const [currentNode, setCurrentNode] = useState<string>(initialNodeId || '');
  const [nodeDetail, setNodeDetail] = useState<any>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // 历史记录（2026-10-03）：后端一直有 /tutor/turns，但学生端此前没有任何入口，
  // 关掉页面就再也找不回之前的讲解。这里补上列表与回看。
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [history, setHistory] = useState<any[]>([]);
  /** 旧格式散轮（无 conversation_id）：只读可查看，不假装可续聊 */
  const [looseTurns, setLooseTurns] = useState<any[]>([]);
  // 2026-10-31（ui-simplify）：会话化历史。conversationId 为空 = 新对话（首次发送时生成）。
  const { user: authUser } = useAuth();
  const [conversationId, setConversationId] = useState<string | null>(null);
  /** 本次挂载是否已完成自动恢复/用户已显式操作——之后不再自动抢焦点 */
  const resumeSettledRef = React.useRef(false);
  /** 服务端会话上下文（含持久化的 conversation_id），供自动恢复读取 */
  const sessionCtxRef = React.useRef<any>(null);
  /** 当前展开的历史轮次：打开白板时优先用这一轮的 sources */
  const [historyFocusId, setHistoryFocusId] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<string[]>([]);
  const [artifacts, setArtifacts] = useState<any[]>([]);
  const [focusSource, setFocusSource] = useState<number | null>(null);
  const [panelSources, setPanelSources] = useState<TutorSource[]>([]);
  const [panelCitedIds, setPanelCitedIds] = useState<number[]>([]);
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const panelPrefKey = useMemo(() => `tutor.rightPanel:${subjectId || 'default'}`, [subjectId]);
  const panelTriggerRef = useRef<HTMLElement | null>(null);
  const leftPanelRef = useRef<HTMLDivElement>(null);
  const rightPanelRef = useRef<HTMLDivElement>(null);
  const composingRef = useRef(false);
  const stickToBottomRef = useRef(true);
  /** 用户是否停在底部：不在底部时显示"回到底部"浮钮 */
  const [atBottom, setAtBottom] = useState(true);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    setRightOpen(window.sessionStorage.getItem(panelPrefKey) === '1');
  }, [panelPrefKey]);
  const toggleRightPanel = useCallback((next: boolean, trigger?: HTMLElement) => {
    if (next && trigger) panelTriggerRef.current = trigger;
    if (next) setLeftOpen(false);
    setRightOpen(next);
    try { window.sessionStorage.setItem(panelPrefKey, next ? '1' : '0'); } catch { /* 隐私模式忽略 */ }
  }, [panelPrefKey]);
  const openLeftPanel = useCallback((trigger: HTMLElement) => {
    panelTriggerRef.current = trigger;
    toggleRightPanel(false);
    setLeftOpen(true);
  }, [toggleRightPanel]);
  const restorePanelFocus = useCallback(() => {
    const trigger = panelTriggerRef.current;
    if (trigger?.isConnected) trigger.focus();
  }, []);

  // 覆盖面板统一处理 Esc、焦点约束与关闭后的焦点归还。
  useEffect(() => {
    if (!leftOpen && !rightOpen) return undefined;
    const overlay = leftOpen || isNarrow || isNarrowForPanel;
    const panel = leftOpen ? leftPanelRef.current : rightPanelRef.current;
    const frame = overlay ? window.requestAnimationFrame(() => {
      panel?.querySelector<HTMLElement>('[data-panel-close], button, [tabindex="0"]')?.focus();
    }) : 0;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (leftOpen) setLeftOpen(false); else toggleRightPanel(false);
        return;
      }
      if (!overlay || event.key !== 'Tab' || !panel) return;
      const focusable = [...panel.querySelectorAll<HTMLElement>(
        'button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]',
      )].filter((element) => element.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener('keydown', onKey);
    };
  }, [leftOpen, rightOpen, isNarrow, isNarrowForPanel, toggleRightPanel]);

  const abortRef = useRef<AbortController | null>(null);
  const activeTurnRef = useRef<string | null>(null);
  // 路由切换或页面卸载时中止未完成的流，避免旧请求继续更新已离开的页面。
  useEffect(() => () => { abortRef.current?.abort(); }, []);
  const listRef = useRef<HTMLDivElement>(null);
  const lastNoticeRef = useRef<any[]>([]);
  const turnSourcesRef = useRef<TutorSource[]>([]);
  const turnCitedRef = useRef<number[]>([]);
  const turnTraceRef = useRef<any[]>([]);
  const lastSourcesRef = useRef<TutorSource[]>([]);
  const lastCitedRef = useRef<number[]>([]);
  const lastTraceRef = useRef<any[]>([]);

  /** 学科：URL 指定优先，否则用全局课程上下文 */
  useEffect(() => {
    if (initialSubjectId) { setSubjectId(initialSubjectId); return; }
    if (!subjectId && ctx.currentSubjectId) setSubjectId(ctx.currentSubjectId);
  }, [initialSubjectId, ctx.currentSubjectId, subjectId]);

  const selectionEpoch = useRef(0);
  const activeSubject = useRef(subjectId);
  activeSubject.current = subjectId;
  const nodeRequest = useRef(0);
  const loadSession = useCallback(async (sid: string) => {
    if (!sid) return;
    const epoch = selectionEpoch.current;
    setSessionLoading(true);
    setSessionError(null);
    try {
      const data = await getTutorSession(sid);
      if (sid !== activeSubject.current || epoch !== selectionEpoch.current) return;
      sessionCtxRef.current = data.context || null;
      setMaterials(data.materials || []);
      setScopeLabel(data.scope?.label || '');
      setScopeInvalid(data.scope?.invalid || []);
      const stored = (data.context?.material_scope?.selected_doc_ids || []) as string[];
      if (initialDocIds && initialDocIds.length) {
        setSelectedDocs(initialDocIds);
        await updateTutorSession({ subject_id: sid, doc_ids: initialDocIds });
      } else {
        setSelectedDocs(stored);
      }
      if (data.context?.ability) setAbility(data.context.ability);
      setGuidanceMode(data.context?.guidance_mode === 'socratic' ? 'socratic' : 'direct');
      if (data.context?.current_node_id && !initialNodeId) setCurrentNode(data.context.current_node_id);
      const items = await listArtifacts(sid).catch(() => []);
      if (sid === activeSubject.current && epoch === selectionEpoch.current) setArtifacts(items);
    } catch (e: any) {
      const detail = e?.response?.data?.detail || '会话加载失败';
      if (sid === activeSubject.current) setSessionError(detail);
    } finally {
      if (sid === activeSubject.current) setSessionLoading(false);
    }
  }, [initialDocIds, initialNodeId]);

  const loadMap = useCallback(async (sid: string) => {
    if (!sid) return;
    setMapLoading(true);
    try {
      const data = await getKnowledgeMap(sid);
      if (sid !== activeSubject.current) return;
      setMap(data.map || null);
    } catch {
      if (sid === activeSubject.current) setMap(null);
    } finally {
      if (sid === activeSubject.current) setMapLoading(false);
    }
  }, []);

  /** 载入一个历史会话到主聊天区（历史抽屉点击 / 自动恢复共用）。
      当前节点与"建议下一步"保持不变——会话原主题 ≠ 当前学习节点。 */
  const openConversation = useCallback(async (convId: string) => {
    if (!subjectId || !convId) return;
    const epoch = selectionEpoch.current;
    setHistoryLoading(true);
    try {
      const rows = await listTurns(subjectId, 60, undefined, convId);
      if (subjectId !== activeSubject.current || epoch !== selectionEpoch.current) return;
      setMessages(historyToMessages(Array.isArray(rows) ? rows : []));
      setConversationId(convId);
      setHistoryFocusId(null);
      setHistoryOpen(false);
      stickToBottomRef.current = true;
      // 草稿残留清理：若草稿内容与该会话最后一条已发送消息相同，
      // 说明它是发送前的旧拷贝（键随会话生成而迁移），不是未发送草稿。
      const uid = authUser?.id || 'anon';
      const dk = 'ui.tutor.draft:' + uid + ':' + subjectId + ':' + convId;
      const nk = 'ui.tutor.draft:' + uid + ':' + subjectId + ':new';
      const lastUser = [...(rows || [])].reverse().find((t) => t.user_message);
      const draft = localStorage.getItem(dk) || localStorage.getItem(nk) || '';
      if (draft && lastUser && draft.trim() === String(lastUser.user_message).trim()) {
        localStorage.removeItem(dk);
        localStorage.removeItem(nk);
      }
    } catch {
      antdMessage.error('会话载入失败，请重试');
    } finally {
      setHistoryLoading(false);
    }
  }, [subjectId, authUser]);

  /** 新对话：清空主区与当前会话指针；旧记录不删除（历史里仍可选回）。 */
  const startNewConversation = useCallback(() => {
    resumeSettledRef.current = true;
    setMessages([]);
    setConversationId(null);
    setHistoryFocusId(null);
    setHistoryOpen(false);
    setStages([]);
    setPanelSources([]);
    setPanelCitedIds([]);
  }, []);

  useEffect(() => {
    if (!subjectId) return;
    loadSession(subjectId);
    loadMap(subjectId);
  }, [subjectId, loadSession, loadMap]);

  // 自动恢复该学科上一次会话（仅一次；用户显式操作优先，不被覆盖）。
  // 服务端 tutor_sessions 已持久化 conversation_id，这里只是把它接回界面。
  useEffect(() => {
    if (!subjectId || sessionLoading || resumeSettledRef.current) return;
    // 任务深链（带 node/doc 参数）时用户意图明确，不自动恢复会话
    if (initialNodeId || (initialDocIds && initialDocIds.length)) {
      resumeSettledRef.current = true;
      return;
    }
    const ctx = sessionCtxRef.current;
    const savedConv: string = ctx?.conversation_id || '';
    if (savedConv && !savedConv.startsWith('socratic:')) {
      resumeSettledRef.current = true;
      void openConversation(savedConv);
    } else {
      resumeSettledRef.current = true;
    }
  }, [subjectId, sessionLoading, openConversation, initialNodeId, initialDocIds]);

  // 草稿隔离（2026-10-31 二版）：onChange 实时写入"用户/学科/会话"专属键
  // （键随 conversationId 即时正确，无 effect 时序竞态）；这里只在
  // 学科/会话变化时**读取**恢复，不代写。
  useEffect(() => {
    const key = 'ui.tutor.draft:' + (authUser?.id || 'anon') + ':' + subjectId + ':' + (conversationId || 'new');
    let saved = '';
    try { saved = localStorage.getItem(key) || ''; } catch { /* 忽略 */ }
    setInput(saved);
  }, [subjectId, conversationId, authUser?.id]);

  const handleInputChange = useCallback((value: string) => {
    setInput(value);
    try {
      const key = 'ui.tutor.draft:' + (authUser?.id || 'anon') + ':' + subjectId + ':' + (conversationId || 'new');
      if (value) localStorage.setItem(key, value);
      else localStorage.removeItem(key);
    } catch { /* 忽略 */ }
  }, [authUser?.id, subjectId, conversationId]);

  const loadNode = useCallback(async (sid: string, nodeId: string) => {
    const request = ++nodeRequest.current;
    if (!sid || !nodeId) { setNodeDetail(null); return; }
    try {
      const data = await getMapNode(sid, nodeId);
      if (request !== nodeRequest.current || sid !== activeSubject.current) return;
      setNodeDetail(data.node);
    } catch {
      setNodeDetail(null);
    }
    // 顺带把这个知识点最近两轮摆出来：学生点知识点，就是想看它以前讲过什么。
    // 取不到历史不影响节点详情展示，所以失败只吞掉不报错。
    try {
      const rows = await listTurns(sid, 2, nodeId);
      if (request === nodeRequest.current && sid === activeSubject.current)
        setMessages(historyToMessages(Array.isArray(rows) ? rows : []));
    } catch {
      /* ignore */
    }
  }, []);

  /**
   * 打开白板前先把「这一轮回答」的引用找回来。
   * 实时回答的 sources 在 lastSourcesRef；历史回答只存了文本，
   * 需要按 turn_id 取回 sources 事件，否则历史里明明有 [2][6][7]，白板却是空的。
   */
  const openWhiteboard = async (explicitTurnId?: string) => {
    const latest = [...messages].reverse().find((m) => m.role === 'assistant');
    let nextSources: TutorSource[] = latest?.sources || lastSourcesRef.current || [];
    let nextCited: number[] = latest?.citedIds || lastCitedRef.current || [];
    const historyTurnId = explicitTurnId || historyFocusId
      || (latest?.id?.startsWith('hist-a-') ? latest.id.slice('hist-a-'.length) : '');
    if (historyTurnId) {
      const turnId = historyTurnId;
      try {
        const data = await getTurn(turnId);
        const events = Array.isArray(data?.events) ? data.events : [];
        const sourceEvent = [...events].reverse().find((e: any) => e.type === 'sources');
        const doneEvent = [...events].reverse().find((e: any) => e.type === 'done');
        nextSources = sourceEvent?.payload?.sources
          || doneEvent?.payload?.sources || [];
        nextCited = doneEvent?.payload?.cited_source_ids || [];
      } catch {
        // 历史事件取不到就保留空状态，白板仍可正常生成
      }
    }
    setBoardSources(nextSources);
    setBoardCitedIds(nextCited);
    setBoardOpen(true);
  };

  /** 手动把当前知识点标记为「本次检测通过」。
   *
   *  只有**自己上传资料的个人学科**允许这么做（自学场景）；
   *  老师提供的课程资料会被后端拒绝，这里如实把原因显示出来。
   */
  const markPassed = useCallback(async () => {
    if (!subjectId || !currentNode) return;
    try {
      await setNodeStatus(subjectId, currentNode, 'passed');
      antdMessage.success('已标记为「本次检测通过」');
      await loadNode(subjectId, currentNode);
      if (typeof loadMap === 'function') await loadMap(subjectId);
    } catch (e: any) {
      antdMessage.warning(e?.response?.data?.detail || e?.message || '标记失败');
    }
  }, [subjectId, currentNode, loadNode, loadMap]);

  useEffect(() => {
    if (subjectId && currentNode) loadNode(subjectId, currentNode);
    else setNodeDetail(null);
  }, [subjectId, currentNode, loadNode]);

  useEffect(() => {
    const element = listRef.current;
    // 只有原本就在底部（或刚刚明确发送/选节点）时跟随流式内容；上翻后不抢滚动。
    if (element && stickToBottomRef.current) element.scrollTop = element.scrollHeight;
  }, [messages, stages]);

  const scopeMaterialIds = useMemo(
    () => new Set(materials.map((m) => m.doc_id)), [materials]);

  /** 当前所选范围是否缺少该知识点的来源资料（缺了就如实提示，不假装依据充分） */
  const nodeScopeMissing = useMemo(() => {
    if (!nodeDetail?.source_docs?.length || selectedDocs.length === 0) return [] as string[];
    const names = new Set(
      materials.filter((m) => selectedDocs.includes(m.doc_id)).map((m) => m.file_name));
    return (nodeDetail.source_docs as string[]).filter((d) => !names.has(d));
  }, [nodeDetail, selectedDocs, materials]);


  const applyScope = useCallback(async (docs: string[]) => {
    if (!subjectId) return;
    setSelectedDocs(docs);
    try {
      const res = await updateTutorSession({ subject_id: subjectId, doc_ids: docs });
      setScopeLabel(res.scope?.label || '');
      setScopeInvalid(res.scope?.invalid || []);
    } catch {
      antdMessage.error('资料范围更新失败');
    }
  }, [subjectId]);

const includeNodeSources = useCallback(() => {
    if (!nodeDetail?.source_docs?.length) return;
    const wanted = new Set(nodeDetail.source_docs as string[]);
    const add = materials.filter((m) => wanted.has(m.file_name)).map((m) => m.doc_id);
    const next = Array.from(new Set([...selectedDocs, ...add]));
    applyScope(next);
    antdMessage.success('已把该知识点的来源资料加入范围');
  }, [nodeDetail, materials, selectedDocs, applyScope]);

  const pushMessage = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const updateLast = useCallback((patch: Partial<ChatMessage>) => {
    setMessages((prev) => {
      const next = [...prev];
      for (let i = next.length - 1; i >= 0; i -= 1) {
        if (next[i].role === 'assistant') { next[i] = { ...next[i], ...patch }; break; }
      }
      return next;
    });
  }, []);

  const changeGuidanceMode = useCallback(async (next: GuidanceMode) => {
    if (!subjectId || next === guidanceMode || modeSaving) return;
    const prev = guidanceMode;
    setGuidanceMode(next);
    setModeSaving(true);
    try {
      await updateTutorSession({ subject_id: subjectId, guidance_mode: next });
    } catch {
      setGuidanceMode(prev);
      antdMessage.error('讲解方式保存失败，请重试');
    } finally {
      setModeSaving(false);
    }
  }, [subjectId, guidanceMode, modeSaving]);

  const send = useCallback((text?: string, abilityOverride?: string) => {
    const content = (text ?? input).trim();
    if (!content || running || !subjectId) return;
    setHistoryFocusId(null);
    resumeSettledRef.current = true;
    const turnAbility = abilityOverride || ability;
    // 2026-10-31：会话化——新对话在首次发送时生成会话 id（此后每轮写回同一会话）；
    // 继续旧会话则沿用 conversationId。后端按它读历史与摘要（现有机制）。
    let convId = conversationId;
    if (!convId) {
      convId = 'conv_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
      setConversationId(convId);
    }
    // 清掉该会话的草稿（已发送）：'new' 键（发送前草稿暂存）与会话键都清
    try {
      const uid = authUser?.id || 'anon';
      localStorage.removeItem('ui.tutor.draft:' + uid + ':' + subjectId + ':new');
      localStorage.removeItem('ui.tutor.draft:' + uid + ':' + subjectId + ':' + convId);
    } catch { /* 忽略 */ }
    setInput('');
    stickToBottomRef.current = true;
    setInput('');
    setStages([]);
    setPanelSources([]);
    setPanelCitedIds([]);
    turnSourcesRef.current = [];
    turnCitedRef.current = [];
    turnTraceRef.current = [];
    lastNoticeRef.current = [];
    pushMessage({ id: 'u' + Date.now(), role: 'user', content });
    const assistantId = 'a' + Date.now();
    pushMessage({ id: assistantId, role: 'assistant', content: '', streaming: true });
    setRunning(true);

    activeTurnRef.current = null;
    abortRef.current = streamTutorTurn({
      message: content,
      subject_id: subjectId,
      ability: turnAbility,
      guidance_mode: guidanceMode,
      doc_ids: selectedDocs,
      node_id: currentNode || null,
      conversation_id: convId,
    }, {
      onEvent: (ev) => {
        if (subjectId !== activeSubject.current) return;
        switch (ev.type) {
          case 'start':
            activeTurnRef.current = ev.turn_id || null;
            if (ev.scope?.label) setScopeLabel(ev.scope.label);
            break;
          case 'context':
            if (ev.scope?.label) setScopeLabel(ev.scope.label);
            break;
          case 'stage':
            setStages((prev) => [...prev.slice(-4), ev.stage || '处理中']);
            break;
          case 'notice':
            updateLast({
              notices: [
                ...((lastNoticeRef.current || []) as any[]),
                { level: ev.level || 'info', message: ev.message },
              ],
            });
            lastNoticeRef.current = [
              ...((lastNoticeRef.current || []) as any[]),
              { level: ev.level || 'info', message: ev.message },
            ];
            break;
          case 'tool_call':
            turnTraceRef.current = [...turnTraceRef.current,
              { tool: ev.tool, label: ev.label, summary: '执行中…', ok: true }];
            updateLast({ trace: turnTraceRef.current });
            break;
          case 'tool_result':
            turnTraceRef.current = turnTraceRef.current.map((t: any) =>
              t.tool === ev.tool && t.summary === '执行中…'
                ? { ...t, summary: ev.summary || '', ok: ev.ok !== false } : t);
            updateLast({ trace: turnTraceRef.current });
            break;
          case 'sources': {
            const merged = [...turnSourcesRef.current];
            for (const s of ev.sources || []) {
              if (!merged.some((x) => x.id === s.id)) merged.push(s);
            }
            turnSourcesRef.current = merged;
            lastSourcesRef.current = merged;
            setPanelSources(merged);
            updateLast({ sources: merged });
            break;
          }
          case 'delta':
            setMessages((prev) => {
              const next = [...prev];
              for (let i = next.length - 1; i >= 0; i -= 1) {
                if (next[i].role === 'assistant') {
                  next[i] = { ...next[i], content: (next[i].content || '') + (ev.content || '') };
                  break;
                }
              }
              return next;
            });
            break;
          case 'artifact':
            if (ev.artifact?.id) {
              setArtifacts((prev) => {
                const exists = prev.some((a) => a.id === ev.artifact.id);
                return exists ? prev : [ev.artifact, ...prev];
              });
            }
            break;
          case 'done': {
            lastSourcesRef.current = ev.sources || turnSourcesRef.current;
            lastCitedRef.current = ev.cited_source_ids || [];
            lastTraceRef.current = ev.tool_trace || turnTraceRef.current;
            setPanelSources(ev.sources || turnSourcesRef.current);
            setPanelCitedIds(ev.cited_source_ids || []);
            updateLast({
              content: ev.answer || '',
              streaming: false,
              error: ev.answer?.trim() ? undefined : '模型返回了空回复，请重试',
              sources: ev.sources || turnSourcesRef.current,
              citedIds: ev.cited_source_ids || [],
              trace: ev.tool_trace || turnTraceRef.current,
              messageType: ev.message_type,
              payload: ev.payload,
              askUser: ev.ask_user || null,
            });
            setRunning(false);
            setStages([]);
            abortRef.current = null;
            listArtifacts(subjectId).then(setArtifacts).catch(() => undefined);
            if (ev.message_type === 'mindmap') loadMap(subjectId);
            if (currentNode) loadNode(subjectId, currentNode);
            if (ev.ask_user) setRightOpen(false);
            break;
          }
          case 'error':
            updateLast({ streaming: false, error: ev.message || '运行失败' });
            setRunning(false);
            setStages([]);
            abortRef.current = null;
            break;
          default:
            break;
        }
      },
      onError: (msg) => {
        updateLast({ streaming: false, error: msg });
        setRunning(false);
        setStages([]);
        abortRef.current = null;
      },
    });
  }, [input, running, subjectId, ability, guidanceMode, selectedDocs, currentNode, conversationId, authUser, pushMessage, updateLast, loadMap, loadNode]);

  const stop = useCallback(() => {
    if (activeTurnRef.current) void cancelTurn(activeTurnRef.current).catch(() => undefined);
    abortRef.current?.abort();
    abortRef.current = null;
    activeTurnRef.current = null;
    setRunning(false);
    setStages([]);
    updateLast({ streaming: false, error: '本轮已停止；已生成内容保留' });
  }, [updateLast]);

  const startNodeActivity = useCallback((kind: 'learn' | 'practice' | 'note') => {
    if (!nodeDetail) return;
    // 2026-10-31：章级节点不做"整章详解"（学生要的是按知识点学）。
    // 章被点击时自动落到它的第一个子知识点；没有子节点才按章名讲解概览。
    if (nodeDetail.level === 'chapter') {
      const children = (map?.nodes || []).filter(
        (n: any) => n.parent_id === nodeDetail.node_id && n.level !== 'chapter');
      const firstChild = children.find((n: any) => n.status !== 'passed') || children[0];
      if (firstChild) {
        setCurrentNode(firstChild.node_id);
        updateTutorSession({ subject_id: subjectId, node_id: firstChild.node_id, ability: kind === 'note' ? 'organize' : kind === 'practice' ? 'practice' : 'explain' });
        // 以子知识点重新执行本活动（label 换成子节点）
        const childLabel = firstChild.label;
        const childPresets: Record<string, { ability: string; text: string }> = {
          learn: { ability: 'explain', text: `请讲解知识点「${childLabel}」，给出概括、重点、例子与易错点，并标出资料依据。` },
          practice: { ability: 'practice', text: `围绕知识点「${childLabel}」出 3 道练习题，我作答后由系统判分。` },
          note: { ability: 'organize', text: `把知识点「${childLabel}」整理成一份可保存的复习笔记。` },
        };
        const cp = childPresets[kind];
        setAbility(cp.ability);
        send(cp.text, cp.ability);
        return;
      }
      // 章下没有可学的子节点：生成简短章概览（明确说明是概览，不是详解）
      const overviewText = `请用不超过 200 字概括「${nodeDetail.label}」讲了什么、包含哪几个部分，并标出资料依据。不要逐节详解。`;
      setAbility('explain');
      send(overviewText, 'explain');
      return;
    }
    const label = nodeDetail.label;
    const presets: Record<string, { ability: string; text: string }> = {
      learn: { ability: 'explain', text: `请讲解知识点「${label}」，给出概括、重点、例子与易错点，并标出资料依据。` },
      practice: { ability: 'practice', text: `围绕知识点「${label}」出 3 道练习题，我作答后由系统判分。` },
      note: { ability: 'organize', text: `把知识点「${label}」整理成一份可保存的复习笔记。` },
    };
    const preset = presets[kind];
    // 同步传 abilityOverride：setAbility 是异步的，直接 send 会用上一轮的能力
    setAbility(preset.ability);
    updateTutorSession({ subject_id: subjectId, ability: preset.ability, node_id: currentNode });
    send(preset.text, preset.ability);
  }, [nodeDetail, send, subjectId, currentNode, map]);

  const handleGenerateMap = useCallback(async () => {
    if (!subjectId) return;
    setMapLoading(true);
    try {
      await generateKnowledgeMap({ subject_id: subjectId, doc_ids: selectedDocs, force: Boolean(map) });
      await loadMap(subjectId);
      antdMessage.success('学习路线已更新');
    } catch (e: any) {
      antdMessage.error(e?.response?.data?.detail || '生成失败，请稍后重试');
    } finally {
      setMapLoading(false);
    }
  }, [subjectId, selectedDocs, map, loadMap]);

  const openHistory = useCallback(async () => {
    setHistoryOpen(true);
    setHistoryLoading(true);
    try {
      // 2026-10-31：拉**会话列表**（按 conversation_id 聚合）+ 旧散轮（只读）
      const { conversations: convs, loose_turns: loose } =
        await listTutorConversations(subjectId, 50);
      setHistory(convs);
      setLooseTurns(loose);
    } catch {
      antdMessage.error('历史记录加载失败');
    } finally {
      setHistoryLoading(false);
    }
  }, [subjectId]);

  const openCitation = useCallback(async (msg: ChatMessage, sourceId: number, trigger: HTMLElement) => {
    let nextSources = msg.sources || [];
    let nextCited = msg.citedIds || [];
    if (!nextSources.length && msg.id.startsWith('hist-a-')) {
      try {
        const data = await getTurn(msg.id.slice('hist-a-'.length));
        const events = Array.isArray(data?.events) ? data.events : [];
        const sourceEvent = [...events].reverse().find((event: any) => event.type === 'sources');
        const doneEvent = [...events].reverse().find((event: any) => event.type === 'done');
        nextSources = sourceEvent?.payload?.sources || doneEvent?.payload?.sources || [];
        nextCited = doneEvent?.payload?.cited_source_ids || [];
      } catch {
        antdMessage.error('引用原文加载失败，请重试');
      }
    }
    setPanelSources(nextSources);
    setPanelCitedIds(nextCited);
    lastSourcesRef.current = nextSources;
    lastCitedRef.current = nextCited;
    setFocusSource(null);
    window.requestAnimationFrame(() => setFocusSource(sourceId));
    toggleRightPanel(true, trigger);
  }, [toggleRightPanel]);

  const subjects = useMemo(() => {
    const list = ctx.courses.map((c) => ({ value: c.subject_id, label: c.title + '（课程）' }));
    for (const s of ctx.personalSubjects) {
      list.push({ value: s.subject_id, label: s.name + '（个人资料）' });
    }
    return list;
  }, [ctx.courses, ctx.personalSubjects]);

  // ---------- 未选择学科 ----------
  if (!subjectId) {
    if (ctx.status === 'loading') {
      return <div className="tutor-loading"><Spin tip="正在加载课程…" /></div>;
    }
    if (ctx.status === 'error') {
      return (
        <div className="tutor-center-card">
          <Alert type="error" showIcon message="课程列表加载失败"
                 description={ctx.error || '请检查网络后重试'}
                 action={<Button icon={<ReloadOutlined />} onClick={() => ctx.refresh()}>重试</Button>} />
        </div>
      );
    }
    return (
      <div className="tutor-center-card">
        <Title level={4} style={{ marginTop: 0 }}>选择要学习的内容</Title>
        <Text type="secondary">选定后即可在同一工作区里问答、讲解、练习与整理，不需要反复切换页面。</Text>
        <Space direction="vertical" style={{ width: '100%', marginTop: 12 }} size={8}>
          {ctx.courses.map((c) => (
            <Button key={c.course_id} block size="large" className="tutor-choose-btn"
                    onClick={() => ctx.select(c.course_id)}>
              <BookOutlined /> {c.title}
              <span className="tutor-choose-sub">{c.class_name || '我的班级'} · {c.teacher_name || '—'}</span>
            </Button>
          ))}
          {ctx.personalSubjects.map((s) => (
            <Button key={s.subject_id} block size="large" className="tutor-choose-btn"
                    onClick={() => ctx.selectSubject(s.subject_id)}>
              <DatabaseOutlined /> {s.name}
              <span className="tutor-choose-sub">个人资料</span>
            </Button>
          ))}
          {ctx.courses.length === 0 && ctx.personalSubjects.length === 0 && (
            /* 首次使用空状态：直接给可执行的下一步，不让用户自己去找资料库 */
            <div className="tutor-firstuse">
              <div className="tutor-firstuse-icon"><DatabaseOutlined /></div>
              <div className="tutor-firstuse-title">还没有资料，先建一门学科</div>
              <div className="tutor-firstuse-sub">
                创建学科后把课件、讲义或老师发的资料包拖进去，系统会自动解析并建好知识库，
                然后就能在这里提问、讲解、练习与整理。
              </div>
              <Button type="primary" size="large" icon={<DatabaseOutlined />}
                      onClick={() => navigate('/student/materials?create=1')}>
                创建学科并上传资料
              </Button>
              <Button type="link" onClick={() => navigate('/student/materials?join=1')}>
                已有班级邀请码？加入课程
              </Button>
            </div>
          )}
        </Space>
      </div>
    );
  }

  const readyMaterials = materials.filter((m) => (m.status || 'ready') === 'ready');
  const allSelected = selectedDocs.length === 0;

  const leftPanel = (
    <div className="tutor-left">
      <div className="tutor-left-section">
        <div className="tutor-left-title">当前学科</div>
        <Select
          style={{ width: '100%' }}
          value={subjectId}
          options={subjects.length ? subjects : [{ value: subjectId, label: subjectId }]}
          onChange={(v) => {
            const course = ctx.courses.find((c) => c.subject_id === v);
            if (course) ctx.select(course.course_id); else ctx.selectSubject(String(v));
            selectionEpoch.current++;
            nodeRequest.current++;
            resumeSettledRef.current = false;
            sessionCtxRef.current = null;
            setSubjectId(String(v));
            setMessages([]);
            setCurrentNode('');
          }}
        />
      </div>

      {/* 资料范围：收起时只占一行（"资料：全部 8 份"），点开才出现勾选列表。
          2026-10-07 之前这里是常驻复选列表，会把左栏的"学习路线"挤掉大半高度。 */}
      <div className="tutor-left-section tutor-left-scope">
        <Popover
          trigger="click"
          placement="bottomLeft"
          arrow={false}
          title="回答依据的资料"
          overlayClassName="tutor-scope-pop"
          content={sessionLoading ? <Spin size="small" /> : (
            <div className="tutor-scope">
              <Checkbox checked={allSelected} onChange={() => applyScope([])}>使用全部资料</Checkbox>
              <div className="tutor-scope-list">
                {readyMaterials.map((m) => (
                  <Checkbox
                    key={m.doc_id}
                    checked={selectedDocs.includes(m.doc_id)}
                    disabled={!scopeMaterialIds.has(m.doc_id)}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...selectedDocs, m.doc_id]
                        : selectedDocs.filter((x) => x !== m.doc_id);
                      applyScope(next);
                    }}
                  >
                    <Tooltip title={m.file_name}>
                      <span className="tutor-scope-name">{m.file_name}</span>
                    </Tooltip>
                  </Checkbox>
                ))}
                {readyMaterials.length === 0 && (
                  <Text type="secondary" style={{ fontSize: 12 }}>暂无可用资料，先到资料库上传</Text>
                )}
              </div>
              <div className="tutor-scope-hint">
                {scopeLabel || '基于本学科全部资料'}
                {scopeInvalid.length > 0 && `（${scopeInvalid.length} 项已失效）`}
              </div>
            </div>
          )}
        >
          <button type="button" className="tutor-scope-btn" title={scopeLabel || '基于本学科全部资料'}>
            <DatabaseOutlined />
            <span className="tutor-scope-btn-text">
              {allSelected ? `资料：全部 ${readyMaterials.length} 份` : `资料：已选 ${selectedDocs.length} 份`}
            </span>
            {scopeInvalid.length > 0 && <span className="tutor-scope-warn">·{scopeInvalid.length} 失效</span>}
            <DownOutlined className="tutor-scope-btn-caret" />
          </button>
        </Popover>
      </div>

      <div className="tutor-left-section tutor-left-grow">
        <div className="tutor-left-title">
          <span><PartitionOutlined /> 学习路线</span>
        </div>
        <MapTree
          nodes={(map?.nodes || []) as MapNode[]}
          currentNodeId={currentNode}
          nextNode={map?.next_node}
          generating={mapLoading}
          statusLabels={map?.status_labels || {}}
          onSelect={(nodeId) => {
            stickToBottomRef.current = true;
            setPanelSources([]);
            setPanelCitedIds([]);
            setFocusSource(null);
            selectionEpoch.current++;
            resumeSettledRef.current = true;
            setCurrentNode(nodeId);
            updateTutorSession({ subject_id: subjectId, node_id: nodeId, ability: 'explain' });
            setLeftOpen(false);
          }}
          onGenerate={handleGenerateMap}
        />
      </div>
    </div>
  );

  /** 右栏内容（引用原文 / 学习成果 / 执行详情）：桌面内嵌与窄屏抽屉共用同一份，
   *  避免两处各写一遍导致行为不一致。 */
  const rightPanelBody = (
    <div className="tutor-right">
      <div className="tutor-right-head">
        <span className="tutor-right-title">引用、成果与详情</span>
        <Button type="text" size="small" icon={<CloseOutlined />} aria-label="收起引用成果与详情"
                data-panel-close onClick={() => toggleRightPanel(false)} />
      </div>
      <SourcePanel
        showSources
        subjectId={subjectId}
        sources={panelSources}
        citedIds={panelCitedIds}
        artifacts={artifacts}
        trace={lastTraceRef.current}
        stages={stages}
        focusSourceId={focusSource}
        onDeleteArtifact={async (a) => {
          await deleteArtifact(a.id);
          setArtifacts((prev) => prev.filter((x) => x.id !== a.id));
        }}
        onUpdateArtifact={async (a, content) => {
          const updated = await updateArtifact(a.id, { subject_id: subjectId, content });
          setArtifacts((prev) => prev.map((x) => (x.id === a.id ? { ...x, ...updated } : x)));
          antdMessage.success('已保存修改');
        }}
      />
    </div>
  );

  return (
    <div className="tutor-workspace">
      {isNarrow ? (
        <>
          <div className="tutor-mobile-bar">
            <Button type="text" icon={<MenuOutlined />} aria-expanded={leftOpen}
                    onClick={(event) => openLeftPanel(event.currentTarget)}>资料与路线</Button>
            <span className="tutor-mobile-title">{nodeDetail ? nodeDetail.label : 'AI 伴学'}</span>
            <Button type="text" icon={<FileTextOutlined />} aria-expanded={rightOpen}
                    onClick={(event) => toggleRightPanel(true, event.currentTarget)}>成果</Button>
          </div>
          <AnimatePresence onExitComplete={restorePanelFocus}>
            {(leftOpen || rightOpen) && (
              <motion.div className="tutor-panel-overlay tutor-mobile-overlay" role="presentation"
                          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                          transition={{ duration: reduceMotion ? 0 : 0.18 }}
                          onMouseDown={(event) => {
                            if (event.target !== event.currentTarget) return;
                            if (leftOpen) setLeftOpen(false); else toggleRightPanel(false);
                          }}>
                <motion.div
                  ref={leftOpen ? leftPanelRef : rightPanelRef}
                  className={'tutor-drawer ' + (leftOpen ? 'tutor-drawer-left' : 'tutor-drawer-right')}
                  role="dialog" aria-modal="true" aria-label={leftOpen ? '资料与学习路线' : '引用、成果与详情'}
                  initial={reduceMotion ? { opacity: 1 } : { opacity: 0, x: leftOpen ? -12 : 12 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={reduceMotion ? { opacity: 0 } : { opacity: 0, x: leftOpen ? -12 : 12 }}
                  transition={{ duration: reduceMotion ? 0 : 0.22, ease: [0.2, 0, 0, 1] }}
                >
                  {leftOpen ? (
                    <>
                      <Button type="text" icon={<CloseOutlined />} className="tutor-drawer-close"
                              aria-label="关闭资料与学习路线" data-panel-close onClick={() => setLeftOpen(false)} />
                      {leftPanel}
                    </>
                  ) : rightPanelBody}
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>
        </>
      ) : (
        <aside className="tutor-aside tutor-aside-left">{leftPanel}</aside>
      )}

      <main className={'tutor-main' + (messages.length === 0 ? ' is-empty' : '')}>
        <div className="tutor-main-head">
          <div className="tutor-main-title">
            <div className="tutor-main-title-main">AI 伴学</div>
            {/* 2026-10-31 减法：固定副标题"资料问答 · 引导学习"与页面标题重复；
                只有选中知识点时才显示节点名（顶部唯一不重复的信息）。 */}
            {nodeDetail?.label && (
              <div className="tutor-main-title-sub">{nodeDetail.label}</div>
            )}
          </div>
          {/* 2026-10-03：原「资料问答 / 引导讲解 / 练习检测 / 知识地图 / 学习整理」模式栏已移除。
              它和底部「快捷命令：开始学习 / 做练习 / 检测」功能重叠，两处都能切能力只会让人犹豫；
              现在能力统一由快捷命令（以及会话恢复）决定，这里只留右栏开关。 */}
          {/* 范围摘要原先在这里又出现一份（与左栏重复）。现在范围只有左栏一个入口，
              这里放右栏开关：默认收起，点开才占位（窄屏由移动端栏的「引用」承接）。 */}
          <Tooltip title="引导我：先问你思路，再逐层提示，不直接给答案">
            <Segmented
              size="small"
              value={guidanceMode}
              disabled={!subjectId || modeSaving || running}
              onChange={(v) => changeGuidanceMode(v as GuidanceMode)}
              options={[
                { label: '直接', value: 'direct' },
                { label: '引导', value: 'socratic' },
              ]}
            />
          </Tooltip>
          <div className="tutor-main-actions">
            {/* 2026-10-30：原「工具」下拉已拆开——白板/录音笔记和「历史」并排成独立按钮。
                收在下拉里时用户必须先点一次才知道有什么，功能等于藏起来了。
                说明走 Tooltip 且 trigger 含 focus：键盘 Tab 过去也能读到一句话简介。 */}
            <Tooltip title="回看这个学科以前的问答与讲解" trigger={['hover', 'focus']}>
              <Button size="small" type="text" icon={<HistoryOutlined />} aria-label="历史"
                      onClick={() => void openHistory()}>
                <span className="tutor-action-label">历史</span>
              </Button>
            </Tooltip>
            <Tooltip title="用这一轮回答的资料生成板书，可逐层展开讲" trigger={['hover', 'focus']}>
              <Button size="small" type="text" icon={<HighlightOutlined />} aria-label="白板"
                      onClick={() => void openWhiteboard()}>
                <span className="tutor-action-label">白板</span>
              </Button>
            </Tooltip>
            <Tooltip title="录一段课堂讲解，自动转写并整理成可复习的笔记" trigger={['hover', 'focus']}>
              <Button size="small" type="text" icon={<AudioOutlined />} aria-label="录音笔记"
                      onClick={() => setLectureOpen(true)}>
                <span className="tutor-action-label">录音笔记</span>
              </Button>
            </Tooltip>
            <Tooltip title={rightOpen ? '收起引用、成果与详情' : '展开引用、成果与详情'}
                     trigger={['hover', 'focus']}>
              <Button
                size="small" type="text" icon={<FileTextOutlined />} aria-expanded={rightOpen}
                onClick={(event) => toggleRightPanel(!rightOpen, event.currentTarget)}
                className={'tutor-panel-toggle' + (rightOpen ? ' is-active' : '')}
              >
                <span className="tutor-action-label">{isNarrow ? '面板' : '成果与详情'}</span>
              </Button>
            </Tooltip>
          </div>
        </div>

        <div className="tutor-messages" ref={listRef} onScroll={(event) => {
          const element = event.currentTarget;
          const near = element.scrollHeight - element.scrollTop - element.clientHeight < 72;
          stickToBottomRef.current = near;
          setAtBottom(near);
        }}>
          {sessionError && (
            <Alert type="error" showIcon message="会话加载失败" description={sessionError}
                   style={{ marginBottom: 12 }} />
          )}
          <AnimatePresence initial={false}>
            {messages.length === 0 && (
              <motion.div className="tutor-welcome"
                          initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 6 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -6 }}
                          transition={{ duration: reduceMotion ? 0 : 0.18 }}>
                {/* 2026-10-31 学习桌面：按真实状态呈现起点（面包屑/短提示/一个主动作），
                    不再是"今天想学什么"+按钮组的居中卡片。 */}
                {(() => {
                  const subjectLabel = subjects.find((item) => item.value === subjectId)?.label
                    || (role === 'teacher' ? '当前课程' : '当前学科');
                  const crumbNode = nodeDetail?.label || (currentNode
                    ? (map?.nodes || []).find((n: any) => n.node_id === currentNode)?.label : '');
                  const firstMaterial = readyMaterials[0];
                  const hasMap = (map?.nodes || []).length > 0;
                  const startLearning = () => {
                    stickToBottomRef.current = true;
                    if (map?.next_node) {
                      setCurrentNode(map.next_node.node_id);
                      updateTutorSession({ subject_id: subjectId, node_id: map.next_node.node_id, ability: 'explain' });
                    }
                    startNodeActivity('learn');
                  };
                  return (
                    <>
                      <div className="tutor-welcome-crumb">
                        {subjectLabel}{crumbNode ? ' / ' + crumbNode : ' / 课件学习'}
                      </div>
                      {readyMaterials.length === 0 ? (
                        <>
                          <div className="tutor-welcome-eyebrow">还没有资料</div>
                          <div className="tutor-welcome-heading">{greeting || "先上传一份课件"}</div>
                          <div className="tutor-welcome-sub">上传老师的 PPT 或资料包后，这里会给出学习路线。</div>
                        </>
                      ) : !hasMap ? (
                        <>
                          <div className="tutor-welcome-eyebrow">资料已就绪</div>
                          <div className="tutor-welcome-heading">从这份课件开始。</div>
                          <div className="tutor-welcome-sub">按知识点学习，也可以直接在下方提问。</div>
                        </>
                      ) : nodeDetail ? (
                        <>
                          <div className="tutor-welcome-eyebrow">正在学习</div>
                          <div className="tutor-welcome-heading">{nodeDetail.label}</div>
                          {nodeDetail.objective && (
                            <div className="tutor-welcome-sub">{nodeDetail.objective}</div>
                          )}
                        </>
                      ) : (
                        <>
                          <div className="tutor-welcome-eyebrow">学习路线已就绪</div>
                          <div className="tutor-welcome-heading">选一个知识点开始。</div>
                          <div className="tutor-welcome-sub">也可以直接在下方提问，不强制按顺序学。</div>
                        </>
                      )}
                      <div className="tutor-welcome-rule" />
                      {readyMaterials.length === 0 ? (
                        <div className="tutor-welcome-row">
                          <div className="tutor-welcome-row-icon"><DatabaseOutlined /></div>
                          <div className="tutor-welcome-row-main">
                            <div className="tutor-welcome-row-title">去上传课件</div>
                            <div className="tutor-welcome-row-sub">个人资料仅自己可见</div>
                          </div>
                          <Button type="primary" icon={<DatabaseOutlined />}
                                  onClick={() => navigate(role === 'teacher' ? '/teacher?section=prep' : '/student/materials?create=1')}>
                            去上传
                          </Button>
                        </div>
                      ) : !hasMap ? (
                        <div className="tutor-welcome-row">
                          <div className="tutor-welcome-row-icon"><FileTextOutlined /></div>
                          <div className="tutor-welcome-row-main">
                            <div className="tutor-welcome-row-title">
                              {firstMaterial?.file_name || '课件'}
                            </div>
                            <div className="tutor-welcome-row-sub">
                              已可检索 · 学习路线待生成
                            </div>
                          </div>
                          <Button type="primary" loading={mapLoading}
                                  onClick={() => void handleGenerateMap()}>
                            开始学习
                          </Button>
                        </div>
                      ) : nodeDetail ? (
                        <div className="tutor-welcome-row">
                          <div className="tutor-welcome-row-icon"><ReadOutlined /></div>
                          <div className="tutor-welcome-row-main">
                            <div className="tutor-welcome-row-title">{nodeDetail.label}</div>
                            <div className="tutor-welcome-row-sub">结合课件讲解，可随时追问</div>
                          </div>
                          <Space size={6}>
                            <Button type="primary" onClick={() => startNodeActivity('learn')}>开始学习</Button>
                            <Button onClick={() => startNodeActivity('practice')}>做一道练习</Button>
                          </Space>
                        </div>
                      ) : (
                        <div className="tutor-welcome-row">
                          <div className="tutor-welcome-row-icon"><AimOutlined /></div>
                          <div className="tutor-welcome-row-main">
                            <div className="tutor-welcome-row-title">
                              {(map?.next_node || (map?.nodes || [])[0])?.label || '第一个知识点'}
                            </div>
                            <div className="tutor-welcome-row-sub">建议从这里开始</div>
                          </div>
                          <Button type="primary" onClick={startLearning}>开始学习</Button>
                        </div>
                      )}
                      {guidanceMode === 'socratic' && (
                        <div className="tutor-welcome-sub" style={{ marginTop: 10 }}>
                          引导模式：先说说你的思路，我会一层层提示，不直接给答案。
                        </div>
                      )}
                    </>
                  );
                })()}
              </motion.div>
            )}
          </AnimatePresence>
          {messages.map((msg) => (
            <motion.div key={msg.id} className={'tutor-msg tutor-msg-' + msg.role}
                        initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: reduceMotion ? 0 : 0.16 }}>
              {msg.role === 'user' ? (
                <div className="tutor-bubble tutor-bubble-user">{msg.content}</div>
              ) : (
                <div className="tutor-bubble tutor-bubble-ai">
                  {(msg.notices || []).map((n, i) => (
                    <Alert key={i} type={n.level === 'warning' ? 'warning' : 'info'} showIcon
                           message={n.message} style={{ marginBottom: 8 }} />
                  ))}
                  {msg.trace && msg.trace.length > 0 && (
                    <div className="tutor-inline-trace">
                      {msg.trace.map((t, i) => (
                        <span key={i} className={'tutor-chip' + (t.ok ? '' : ' is-error')}>{t.label}</span>
                      ))}
                    </div>
                  )}
                  {msg.content
                    ? <MarkdownText text={msg.content} onCitation={(id) => {
                      void openCitation(msg, id, document.activeElement as HTMLElement);
                    }} />
                    : (msg.streaming ? (
                      <div className="tutor-stage">
                        <Spin size="small" />
                        <span>{stages.length ? stages[stages.length - 1] : '正在处理…'}</span>
                      </div>
                    ) : null)}
                  {msg.streaming && stages.length > 0 && msg.content && (
                    <div className="tutor-stage tutor-stage-inline">
                      <Spin size="small" /><span>{stages[stages.length - 1]}</span>
                    </div>
                  )}
                  {msg.messageType === 'quiz' && msg.payload?.quiz?.length > 0 && (
                    <PracticeCard questions={msg.payload.quiz as PracticeQuestion[]}
                                  subjectId={subjectId} nodeId={currentNode || null}
                                  onAnswered={() => {
                                    loadNode(subjectId, currentNode);
                                    loadMap(subjectId);
                                  }}
                                  onAskExplain={() => startNodeActivity('learn')}
                                  onPracticeMore={() => startNodeActivity('practice')}
                                  onNextNode={map?.next_node && map.next_node.node_id !== currentNode ? () => {
                                    setCurrentNode(map.next_node!.node_id);
                                    updateTutorSession({ subject_id: subjectId, node_id: map.next_node!.node_id, ability: 'explain' });
                                    loadNode(subjectId, map.next_node!.node_id);
                                    startNodeActivity('learn');
                                  } : undefined}
                                  nextNodeLabel={map?.next_node && map.next_node.node_id !== currentNode
                                    ? map.next_node.label : null} />
                  )}
                  {msg.askUser && (
                    <div className="tutor-ask">
                      <div className="tutor-ask-q">{msg.askUser.question}</div>
                      <Space wrap size={6}>
                        {msg.askUser.options.map((opt) => (
                          <Button key={opt} size="small" onClick={() => send(opt)}>{opt}</Button>
                        ))}
                      </Space>
                    </div>
                  )}
                  {msg.error && (
                    <Alert type="error" showIcon message={msg.error} style={{ marginTop: 8 }} />
                  )}
                </div>
              )}
            </motion.div>
          ))}
        </div>

        {nodeDetail && (
          <div className="tutor-node-bar">
            <div className="tutor-node-info">
              <span className="tutor-node-eyebrow">正在学</span>
              <Tag color="green">
                {nodeDetail.status_label || STATUS_FALLBACK[nodeDetail.status] || '未开始'}
              </Tag>
              <span className="tutor-node-name" title={nodeDetail.label}>{nodeDetail.label}</span>
              {nodeDetail.source_docs?.length > 0 && (
                <Tooltip title={nodeDetail.source_docs.join('、')}>
                  <span className="tutor-node-src">· {nodeDetail.source_docs.length} 份来源</span>
                </Tooltip>
              )}
            </div>
            {nodeScopeMissing.length > 0 && (
              <Tooltip title={'缺少来源资料：' + nodeScopeMissing.join('、')}>
                <Button size="small" style={{ color: '#d97706' }}
                        onClick={includeNodeSources}>
                  范围缺少该知识点资料，点此加入
                </Button>
              </Tooltip>
            )}
            {/* 同一动作只保留一个主要入口：学习/练习/检测是主操作，
                「记笔记」等次级能力收进"更多"，避免与顶部模式栏功能重叠、按钮成排。 */}
            <Space size={6} wrap className="tutor-node-actions">
              {/* 2026-10-31：练习与检测合并——二者实际同一能力（出3题+系统判分），
                  仅文案不同；保留一个「做练习」。 */}
              <Button size="small" type="primary" icon={<BulbOutlined />}
                      onClick={() => startNodeActivity('learn')}>开始学习</Button>
              <Button size="small" icon={<FormOutlined />}
                      onClick={() => startNodeActivity('practice')}>做练习</Button>
              <Dropdown
                trigger={['click']}
                menu={{
                  items: [
                    { key: 'note', icon: <FileTextOutlined />, label: '整理笔记' },
                    { key: 'openPanel', icon: <RightOutlined />, label: '引用与成果' },
                    { type: 'divider' as const },
                    // 自学场景：自己上传的资料可以自己确认"已通过"。
                    // 老师提供的课程资料会被后端拒绝（成绩必须以检测为准）。
                    { key: 'passed', icon: <CheckCircleOutlined />, label: '我已通过' },
                  ],
                  onClick: ({ key }) => {
                    if (key === 'note') startNodeActivity('note');
                    if (key === 'openPanel') toggleRightPanel(true);
                    if (key === 'passed') markPassed();
                  },
                }}
              >
                <Button size="small" icon={<MoreOutlined />}>更多</Button>
              </Dropdown>
            </Space>
          </div>
        )}

        <motion.div className="tutor-composer-wrap" layout={reduceMotion ? false : 'position'}
                    transition={{ duration: reduceMotion ? 0 : 0.22, ease: [0.2, 0, 0, 1] }}>
          <div className="tutor-composer">
            <TextArea
              value={input}
              onChange={(e) => handleInputChange(e.target.value)}
              onCompositionStart={() => { composingRef.current = true; }}
              onCompositionEnd={() => { composingRef.current = false; }}
              onPressEnter={(e) => {
                if (!e.shiftKey && !composingRef.current && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send();
                }
              }}
              autoSize={{ minRows: 1, maxRows: 5 }}
              placeholder={role === 'teacher'
                ? '例如：基于资料生成这节课的教案初稿'
                : '问课件里的问题，或说说哪里没懂…（Shift + Enter 换行）'}
              disabled={running}
            />
            {guidanceMode === 'socratic' && !running && (
              <Tooltip title="只给下一层提示，不直接给答案">
                <Button icon={<BulbOutlined />} onClick={() => send('先别告诉我答案，给我一点提示。')}>
                  提示
                </Button>
              </Tooltip>
            )}
            {running ? (
              <Button danger icon={<StopOutlined />} onClick={stop}>停止</Button>
            ) : (
              <Button type="primary" icon={<SendOutlined />} onClick={() => send()}
                      disabled={!input.trim()}>发送</Button>
            )}
          </div>
          <div className="tutor-composer-foot">
            <span>
              {nodeDetail?.label
                ? '围绕「' + nodeDetail.label + '」提问'
                : (scopeLabel ? '基于当前课件' : '基于当前资料')}
            </span>
            <span>Shift + Enter 换行</span>
          </div>
        </motion.div>
        {!atBottom && messages.length > 0 && (
          <button type="button" className="tutor-jump-bottom" aria-label="回到最新消息"
                  onClick={() => {
                    stickToBottomRef.current = true;
                    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: reduceMotion ? 'auto' : 'smooth' });
                  }}>
            <DownOutlined />
          </button>
        )}
      </main>

      <WhiteboardModal
        open={boardOpen}
        onClose={() => setBoardOpen(false)}
        subjectId={subjectId}
        nodeId={currentNode}
        docIds={selectedDocs}
        defaultTopic={nodeDetail?.label || ''}
        sources={boardSources}
        citedIds={boardCitedIds}
      />
      <LectureNotesModal
        open={lectureOpen}
        onClose={() => setLectureOpen(false)}
        subjectId={subjectId}
      />

      {/* 历史：可继续的**会话列表**（2026-10-31）。点击载入主聊天区并写回同一会话；
          旧格式散轮（无会话归属）单独列出、明确标只读——可查看不可续聊，不假装支持。 */}
      <Drawer
        title="历史对话"
        width={440}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        styles={{ body: { padding: '12px 16px' } }}
      >
        <Button type="primary" block icon={<PlusOutlined />} style={{ marginBottom: 12 }}
                onClick={startNewConversation}>
          新对话
        </Button>
        {historyLoading ? (
          <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
        ) : history.length === 0 && looseTurns.length === 0 ? (
          <Text type="secondary">这个学科还没有会话。提问、出题或做练习之后，这里会留下记录。</Text>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {history.map((c) => (
              <button key={c.conversation_id} type="button"
                      onClick={() => void openConversation(c.conversation_id)}
                      style={{
                        textAlign: 'left', cursor: 'pointer', width: '100%',
                        border: '1px solid var(--color-border-light, #eef1f5)', borderRadius: 10,
                        background: '#fff', padding: '10px 12px',
                      }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <div style={{ fontWeight: 550, color: 'var(--color-text)', flex: 1, minWidth: 0,
                                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.title}
                  </div>
                  <Text type="secondary" style={{ fontSize: 11, flexShrink: 0 }}>
                    {String(c.last_at || '').replace('T', ' ').slice(5, 16)}
                  </Text>
                </div>
                <div style={{ marginTop: 3, fontSize: 12, color: 'var(--color-text-secondary)',
                              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {c.preview || '（无预览）'}
                </div>
                <div style={{ marginTop: 3, fontSize: 11, color: 'var(--color-text-muted)' }}>
                  {c.turns} 轮对话
                </div>
              </button>
            ))}
            {looseTurns.length > 0 && (
              <>
                <div style={{ marginTop: 8, marginBottom: 2 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>早期记录（旧格式 · 只读）</Text>
                </div>
                <Collapse
                  ghost size="small"
                  onChange={(key) => setHistoryFocusId(key ? String(key) : null)}
                  items={looseTurns.map((t) => ({
                    key: String(t.id),
                    label: (
                      <div style={{ minWidth: 0, display: 'flex', gap: 8, alignItems: 'baseline' }}>
                        <div style={{ fontWeight: 400, whiteSpace: 'normal', flex: 1, minWidth: 0, fontSize: 13 }}>
                          {t.user_message || '（这一轮没有提问内容）'}
                        </div>
                        <Text type="secondary" style={{ fontSize: 11, flexShrink: 0 }}>
                          {String(t.created_at || '').replace('T', ' ').slice(0, 16)}
                        </Text>
                      </div>
                    ),
                    children: (
                      <div>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          早期记录：只读查看，不支持继续对话（新消息请开新对话或选择上方会话）。
                        </Text>
                        <div style={{ marginTop: 8 }}>
                          {t.answer
                            ? <MarkdownText text={t.answer} />
                            : <Text type="secondary">这一轮没有留下回答。</Text>}
                        </div>
                        {t.answer && (
                          <Button
                            size="small" type="primary" ghost icon={<HighlightOutlined />}
                            style={{ marginTop: 12 }}
                            onClick={() => {
                              setHistoryFocusId(String(t.id));
                              void openWhiteboard(String(t.id));
                            }}
                          >
                            用这轮资料打开白板
                          </Button>
                        )}
                      </div>
                    ),
                  }))}
                />
              </>
            )}
          </div>
        )}
      </Drawer>

      {/* 右栏默认收起

      {/* 右栏默认收起：关闭后不渲染任何占位列，主区自动占满。
          宽屏才内嵌并排；1366px 及以下改为覆盖式抽屉，避免把正文挤窄。 */}
      <AnimatePresence onExitComplete={restorePanelFocus}>
        {!isNarrow && rightOpen && !isNarrowForPanel && (
          <motion.aside className="tutor-aside tutor-aside-right"
                        initial={reduceMotion ? { opacity: 1 } : { opacity: 0, x: 10 }}
                        animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
                        transition={{ duration: reduceMotion ? 0 : 0.18 }}>{rightPanelBody}</motion.aside>
        )}
        {!isNarrow && rightOpen && isNarrowForPanel && (
          <motion.div className="tutor-panel-overlay" role="presentation"
                      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                      transition={{ duration: reduceMotion ? 0 : 0.18 }}
                      onMouseDown={(event) => {
                        if (event.target === event.currentTarget) toggleRightPanel(false);
                      }}>
            <motion.div ref={rightPanelRef} className="tutor-panel-drawer" role="dialog" aria-modal="true"
                        aria-label="引用、成果与详情"
                        initial={reduceMotion ? { opacity: 1 } : { opacity: 0, x: 12 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={reduceMotion ? { opacity: 0 } : { opacity: 0, x: 12 }}
                        transition={{ duration: reduceMotion ? 0 : 0.22, ease: [0.2, 0, 0, 1] }}>
              {rightPanelBody}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default TutorWorkspace;
