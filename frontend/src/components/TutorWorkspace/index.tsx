import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Button, Checkbox, Collapse, Drawer, Dropdown, Input, Popover, Select, Space, Spin, Tag,
  Tooltip, Typography, message as antdMessage,
} from 'antd';
import {
  BookOutlined, BulbOutlined, CheckCircleOutlined, CheckSquareOutlined, CloseOutlined, DatabaseOutlined,
  DeleteOutlined, DownOutlined, FileTextOutlined, FormOutlined, HistoryOutlined, MenuOutlined,
  MoreOutlined,
  PartitionOutlined, ReloadOutlined, RightOutlined, RobotOutlined, SendOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { useCourse } from '../../context/CourseContext';
import { useIsNarrow } from '../../hooks/useIsNarrow';
import {
  ABILITY_LABELS, deleteArtifact, deleteTurn, generateKnowledgeMap, getKnowledgeMap, getMapNode,
  listQuestionsForPoint, setNodeStatus,
  getTutorSession, listArtifacts, listTurns, streamTutorTurn, updateArtifact, updateTutorSession,
  type TutorSource,
} from '../../api/tutor';
import { MarkdownText } from './markdown';
import MapTree, { type MapNode } from './MapTree';
import PracticeCard, { type PracticeQuestion } from './PracticeCard';
import SourcePanel from './SourcePanel';
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
      out.push({ id: 'hist-a-' + t.id, role: 'assistant', content: t.answer });
    }
  });
  return out;
};

const QUICK_PROMPTS: Record<string, { label: string; text: string }[]> = {
  chat: [
    { label: '解释这个概念', text: '请用简单的语言解释当前知识点，并指出资料中的依据。' },
    { label: '举个例子', text: '请举一个具体例子说明，并标出资料出处。' },
  ],
  explain: [
    { label: '更基础一点', text: '我没理解，请更基础一点重新讲，用小步骤。' },
    { label: '换个例子', text: '换个例子再讲一遍这个知识点。' },
    { label: '可能考点', text: '根据资料推测这个知识点可能怎么考，说明这是 AI 建议而非老师确定的考点。' },
  ],
  practice: [
    { label: '出 3 道题', text: '围绕当前知识点出 3 道难度递进的练习。' },
    { label: '只考概念', text: '围绕当前知识点出一道概念辨析题。' },
  ],
  map: [
    { label: '生成学习路线', text: '根据当前资料生成这门课的学习路线。' },
    { label: '这门课重点', text: '说明这门课资料覆盖了什么，哪些资料还没有整理成知识点。' },
  ],
  organize: [
    { label: '整理成笔记', text: '把当前知识点整理成一份可保存的复习笔记。' },
    { label: '安排今天', text: '结合我的真实学习记录和时间预算，安排今天的学习。' },
  ],
};

const TutorWorkspace: React.FC<TutorWorkspaceProps> = ({
  initialSubjectId, initialDocIds, initialNodeId, role = 'student', greeting,
}) => {
  const ctx = useCourse();
  const navigate = useNavigate();
  const isNarrow = useIsNarrow();
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
  // 当前知识点出过的题：历史对话里写着"已出 3 道题"，得让那些题真的能再做一遍
  const [nodeQuestions, setNodeQuestions] = useState<any[]>([]);
  // 题目可能积累十几道，默认只露一道、其余折叠——否则把输入框顶出视野
  const [questionsExpanded, setQuestionsExpanded] = useState(false);
  const [input, setInput] = useState('');
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<string[]>([]);
  const [artifacts, setArtifacts] = useState<any[]>([]);
  const [focusSource, setFocusSource] = useState<number | null>(null);
  const [leftOpen, setLeftOpen] = useState(false);
  /**
   * 右栏开关。默认收起；开合状态按"学科"记忆在 sessionStorage 里
   * （参考 Archify reader-layout.js 的 rail 偏好与 DeepTutor useLearningMode 的按工作区快照，
   * 见 docs/THIRD_PARTY_REUSE.md）。开关面板本身绝不动会话/草稿/节点/资料范围。
   */
  const [rightOpen, setRightOpen] = useState(false);
  const panelPrefKey = useMemo(() => `tutor.rightPanel:${subjectId || 'default'}`, [subjectId]);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    setRightOpen(window.sessionStorage.getItem(panelPrefKey) === '1');
  }, [panelPrefKey]);
  const toggleRightPanel = useCallback((next: boolean) => {
    setRightOpen(next);
    try { window.sessionStorage.setItem(panelPrefKey, next ? '1' : '0'); } catch { /* 隐私模式忽略 */ }
  }, [panelPrefKey]);

  // Esc 关闭覆盖式抽屉（参考 Archify reader-layout.js 的 overlay 键盘行为）
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') toggleRightPanel(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggleRightPanel]);

  const abortRef = useRef<AbortController | null>(null);
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

  const loadSession = useCallback(async (sid: string) => {
    if (!sid) return;
    setSessionLoading(true);
    setSessionError(null);
    try {
      const data = await getTutorSession(sid);
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
      if (data.context?.current_node_id && !initialNodeId) setCurrentNode(data.context.current_node_id);
      setArtifacts(await listArtifacts(sid).catch(() => []));
    } catch (e: any) {
      const detail = e?.response?.data?.detail || '会话加载失败';
      setSessionError(detail);
    } finally {
      setSessionLoading(false);
    }
  }, [initialDocIds, initialNodeId]);

  const loadMap = useCallback(async (sid: string) => {
    if (!sid) return;
    setMapLoading(true);
    try {
      const data = await getKnowledgeMap(sid);
      setMap(data.map || null);
    } catch {
      setMap(null);
    } finally {
      setMapLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!subjectId) return;
    loadSession(subjectId);
    loadMap(subjectId);
  }, [subjectId, loadSession, loadMap]);

  const loadNode = useCallback(async (sid: string, nodeId: string) => {
    if (!sid || !nodeId) { setNodeDetail(null); return; }
    try {
      const data = await getMapNode(sid, nodeId);
      setNodeDetail(data.node);
      // 把这个知识点出过的题也摆出来，否则历史里"已出 3 道题"是一句空话
      const label = data.node?.label;
      if (label) {
        try {
          setNodeQuestions(await listQuestionsForPoint(sid, label));
        } catch {
          setNodeQuestions([]);
        }
      } else {
        setNodeQuestions([]);
      }
    } catch {
      setNodeDetail(null);
    }
    // 顺带把这个知识点最近两轮摆出来：学生点知识点，就是想看它以前讲过什么。
    // 取不到历史不影响节点详情展示，所以失败只吞掉不报错。
    try {
      const rows = await listTurns(sid, 2, nodeId);
      setMessages(historyToMessages(Array.isArray(rows) ? rows : []));
    } catch {
      /* ignore */
    }
  }, []);

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
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
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

  const send = useCallback((text?: string, abilityOverride?: string) => {
    const content = (text ?? input).trim();
    if (!content || running || !subjectId) return;
    const turnAbility = abilityOverride || ability;
    setInput('');
    setStages([]);
    turnSourcesRef.current = [];
    turnCitedRef.current = [];
    turnTraceRef.current = [];
    lastNoticeRef.current = [];
    pushMessage({ id: 'u' + Date.now(), role: 'user', content });
    const assistantId = 'a' + Date.now();
    pushMessage({ id: assistantId, role: 'assistant', content: '', streaming: true });
    setRunning(true);

    abortRef.current = streamTutorTurn({
      message: content,
      subject_id: subjectId,
      ability: turnAbility,
      doc_ids: selectedDocs,
      node_id: currentNode || null,
    }, {
      onEvent: (ev) => {
        switch (ev.type) {
          case 'start':
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
            updateLast({
              content: ev.answer || '',
              streaming: false,
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
  }, [input, running, subjectId, ability, selectedDocs, currentNode, pushMessage, updateLast, loadMap, loadNode]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
    setStages([]);
    updateLast({ streaming: false });
  }, [updateLast]);

  const startNodeActivity = useCallback((kind: 'learn' | 'practice' | 'quiz' | 'note') => {
    if (!nodeDetail) return;
    const label = nodeDetail.label;
    const presets: Record<string, { ability: string; text: string }> = {
      learn: { ability: 'explain', text: `请讲解知识点「${label}」，给出概括、重点、例子与易错点，并标出资料依据。` },
      practice: { ability: 'practice', text: `围绕知识点「${label}」出 3 道练习题。` },
      quiz: { ability: 'practice', text: `围绕知识点「${label}」给我一次检测（3 道题，不要给答案）。` },
      note: { ability: 'organize', text: `把知识点「${label}」整理成一份可保存的复习笔记。` },
    };
    const preset = presets[kind];
    // 同步传 abilityOverride：setAbility 是异步的，直接 send 会用上一轮的能力
    setAbility(preset.ability);
    updateTutorSession({ subject_id: subjectId, ability: preset.ability, node_id: currentNode });
    send(preset.text, preset.ability);
  }, [nodeDetail, send, subjectId, currentNode]);

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
        <span className="tutor-right-title">引用与成果</span>
        <Button type="text" size="small" icon={<CloseOutlined />} aria-label="收起引用与成果"
                onClick={() => toggleRightPanel(false)} />
      </div>
      <SourcePanel
        sources={lastSourcesRef.current}
        citedIds={lastCitedRef.current}
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
            <Button type="text" icon={<MenuOutlined />} onClick={() => setLeftOpen(true)}>资料与路线</Button>
            <span className="tutor-mobile-title">{nodeDetail ? nodeDetail.label : 'AI 伴学'}</span>
            <Button type="text" icon={<FileTextOutlined />} onClick={() => toggleRightPanel(true)}>引用</Button>
          </div>
          {leftOpen && (
            <div className="tutor-drawer tutor-drawer-left">
              <Button type="text" icon={<CloseOutlined />} className="tutor-drawer-close"
                      onClick={() => setLeftOpen(false)} />
              {leftPanel}
            </div>
          )}
          {rightOpen && (
            <div className="tutor-drawer tutor-drawer-right">{rightPanelBody}</div>
          )}
        </>
      ) : (
        <aside className="tutor-aside tutor-aside-left">{leftPanel}</aside>
      )}

      <main className="tutor-main">
        <div className="tutor-main-head">
          {/* 2026-10-03：原「资料问答 / 引导讲解 / 练习检测 / 知识地图 / 学习整理」模式栏已移除。
              它和底部「快捷命令：开始学习 / 做练习 / 检测」功能重叠，两处都能切能力只会让人犹豫；
              现在能力统一由快捷命令（以及会话恢复）决定，这里只留右栏开关。 */}
          {/* 范围摘要原先在这里又出现一份（与左栏重复）。现在范围只有左栏一个入口，
              这里放右栏开关：默认收起，点开才占位（窄屏由移动端栏的「引用」承接）。 */}
          <div className="tutor-main-actions">
            <Tooltip title="回看这个学科以前的问答与讲解">
              <Button
                size="small"
                icon={<HistoryOutlined />}
                onClick={async () => {
                  setHistoryOpen(true);
                  setHistoryLoading(true);
                  try {
                    const rows = await listTurns(subjectId, 50);
                    setHistory(Array.isArray(rows) ? rows : []);
                  } catch {
                    antdMessage.error('历史记录加载失败');
                  } finally {
                    setHistoryLoading(false);
                  }
                }}
              >
                历史记录
              </Button>
            </Tooltip>
            {!isNarrow && (
              <Tooltip title={rightOpen ? '收起引用与成果' : '展开引用原文、学习成果与执行详情'}>
                <Button
                  size="small"
                  type={rightOpen ? 'primary' : 'default'}
                  icon={<FileTextOutlined />}
                  onClick={() => toggleRightPanel(!rightOpen)}
                  className="tutor-panel-toggle"
                >
                  引用与成果
                </Button>
              </Tooltip>
            )}
          </div>
        </div>

        <div className="tutor-messages" ref={listRef}>
          {sessionError && (
            <Alert type="error" showIcon message="会话加载失败" description={sessionError}
                   style={{ marginBottom: 12 }} />
          )}
          {messages.length === 0 && (
            <div className="tutor-welcome">
              <div className="tutor-welcome-icon"><RobotOutlined /></div>
              <div className="tutor-welcome-title">
                {greeting || (role === 'teacher' ? '今天想先备哪一部分？' : '今天想学什么？')}
              </div>
              <div className="tutor-welcome-sub">
                可以直接提问，或点下面的按钮。
                想切换「开始学习 / 做练习 / 检测」时，用底部的快捷命令。
                回答会给出来自课程资料的引用，可点开核对原文。
              </div>
              <Space wrap size={8} style={{ marginTop: 14 }}>
                {(QUICK_PROMPTS[ability] || []).map((q) => (
                  <Button key={q.label} onClick={() => send(q.text)}>{q.label}</Button>
                ))}
              </Space>
            </div>
          )}
          {messages.map((msg) => (
            <div key={msg.id} className={'tutor-msg tutor-msg-' + msg.role}>
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
                    ? <MarkdownText text={msg.content} onCitation={(id) => { setFocusSource(id); toggleRightPanel(true); }} />
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
                                  }} />
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
            </div>
          ))}
        </div>

        {/* 这个知识点出过的题。
            历史对话里写着"已出 3 道题"，如果那些题找不回来，历史就是一句空话——
            学生既看不到题、也无法作答。这里把它们从题库重新摆出来。 */}
        {nodeQuestions.length > 0 && (
          <div className="tutor-node-questions">
            <div className="tutor-node-questions-head">
              <Text type="secondary" style={{ fontSize: 12 }}>
                这个知识点出过的题（共 {nodeQuestions.length} 道，可以重做）
              </Text>
              {nodeQuestions.length > 1 && (
                <Button type="link" size="small"
                        onClick={() => setQuestionsExpanded((v) => !v)}>
                  {questionsExpanded ? '收起' : '展开全部 ' + nodeQuestions.length + ' 道'}
                </Button>
              )}
            </div>
            <PracticeCard
              questions={(questionsExpanded
                ? nodeQuestions
                : nodeQuestions.slice(0, 1)) as PracticeQuestion[]}
              subjectId={subjectId}
              nodeId={currentNode || null}
              onAnswered={() => {
                loadNode(subjectId, currentNode);
                loadMap(subjectId);
              }}
            />
          </div>
        )}

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
              <Text type="secondary" className="tutor-quick-label">快捷命令：</Text>
              <Button size="small" type="primary" ghost icon={<BulbOutlined />}
                      onClick={() => startNodeActivity('learn')}>开始学习</Button>
              <Button size="small" icon={<FormOutlined />}
                      onClick={() => startNodeActivity('practice')}>做练习</Button>
              <Button size="small" icon={<CheckSquareOutlined />}
                      onClick={() => startNodeActivity('quiz')}>检测</Button>
              <Dropdown
                trigger={['click']}
                menu={{
                  items: [
                    { key: 'note', icon: <FileTextOutlined />, label: '记笔记' },
                    { key: 'trace', icon: <RightOutlined />, label: '引用与执行详情' },
                    { type: 'divider' as const },
                    // 自学场景：自己上传的资料可以自己确认"已通过"。
                    // 老师提供的课程资料会被后端拒绝（成绩必须以检测为准）。
                    { key: 'passed', icon: <CheckCircleOutlined />, label: '我已通过' },
                  ],
                  onClick: ({ key }) => {
                    if (key === 'note') startNodeActivity('note');
                    if (key === 'trace') toggleRightPanel(true);
                    if (key === 'passed') markPassed();
                  },
                }}
              >
                <Button size="small" icon={<MoreOutlined />}>更多</Button>
              </Dropdown>
            </Space>
          </div>
        )}

        <div className="tutor-composer">
          <TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPressEnter={(e) => {
              if (!e.shiftKey) { e.preventDefault(); send(); }
            }}
            autoSize={{ minRows: 1, maxRows: 5 }}
            placeholder={role === 'teacher'
              ? '例如：基于资料生成这节课的教案初稿'
              : '输入问题，或粘贴一道不会的题（Shift + Enter 换行）'}
            disabled={running}
          />
          {running ? (
            <Button danger icon={<StopOutlined />} onClick={stop}>停止</Button>
          ) : (
            <Button type="primary" icon={<SendOutlined />} onClick={() => send()}
                    disabled={!input.trim()}>发送</Button>
          )}
        </div>
      </main>

      {/* 历史记录：复用后端 /tutor/turns（此前只有接口、学生端没有入口） */}
      <Drawer
        title="历史记录"
        width={440}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
      >
        {historyLoading ? (
          <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
        ) : history.length === 0 ? (
          <Text type="secondary">还没有历史记录。提问、出题或做练习之后，这里会留下记录。</Text>
        ) : (
          <Collapse
            accordion
            items={history.map((t) => ({
              key: String(t.id),
              label: (
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{ fontWeight: 500, whiteSpace: 'normal', flex: 1, minWidth: 0 }}>
                      {t.user_message || '（这一轮没有提问内容）'}
                    </div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {String(t.created_at || '').replace('T', ' ').slice(0, 16)}
                    {t.ability ? ' · ' + (ABILITY_LABELS[t.ability] || t.ability) : ''}
                    </Text>
                  </div>
                  <Button
                    type="text"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    style={{ marginLeft: 'auto' }}
                    onClick={async (e) => {
                      e.stopPropagation();
                      try {
                        await deleteTurn(t.id);
                        setHistory((prev) => prev.filter((x) => x.id !== t.id));
                        antdMessage.success('已删除这条记录');
                      } catch {
                        antdMessage.error('删除失败');
                      }
                    }}
                  >删除</Button>
                </div>
              ),
              children: t.answer
                ? <MarkdownText text={t.answer} />
                : <Text type="secondary">这一轮没有留下回答（可能已取消或失败）。</Text>,
            }))}
          />
        )}
      </Drawer>

      {/* 右栏默认收起：关闭后不渲染任何占位列，主区自动占满。
          宽屏才内嵌并排；1366px 及以下改为覆盖式抽屉，避免把正文挤窄。 */}
      {!isNarrow && rightOpen && !isNarrowForPanel && (
        <aside className="tutor-aside tutor-aside-right">{rightPanelBody}</aside>
      )}
      {!isNarrow && rightOpen && isNarrowForPanel && (
        <div className="tutor-panel-overlay" role="presentation"
             onClick={() => toggleRightPanel(false)}>
          <div className="tutor-panel-drawer" role="dialog" aria-label="引用与成果"
               onClick={(e) => e.stopPropagation()}>
            {rightPanelBody}
          </div>
        </div>
      )}
    </div>
  );
};

export default TutorWorkspace;
