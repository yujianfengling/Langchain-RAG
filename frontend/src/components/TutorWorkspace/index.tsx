import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Checkbox, Empty, Input, Segmented, Select, Space, Spin, Tag, Tooltip,
  Typography, message as antdMessage,
} from 'antd';
import {
  BookOutlined, BulbOutlined, CheckSquareOutlined, CloseOutlined, DatabaseOutlined,
  FileTextOutlined, FormOutlined, MenuOutlined, PartitionOutlined, ReloadOutlined,
  RobotOutlined, SendOutlined, StopOutlined,
} from '@ant-design/icons';
import { useCourse } from '../../context/CourseContext';
import { useIsNarrow } from '../../hooks/useIsNarrow';
import {
  ABILITY_LABELS, deleteArtifact, generateKnowledgeMap, getKnowledgeMap, getMapNode,
  getTutorSession, listArtifacts, streamTutorTurn, updateArtifact, updateTutorSession,
  type TutorSource,
} from '../../api/tutor';
import { MarkdownText } from './markdown';
import MapTree, { type MapNode } from './MapTree';
import PracticeCard, { type PracticeQuestion } from './PracticeCard';
import SourcePanel from './SourcePanel';
import './tutor.css';

const { Text, Title } = Typography;
const { TextArea } = Input;

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
  const isNarrow = useIsNarrow();
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
  const [input, setInput] = useState('');
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<string[]>([]);
  const [artifacts, setArtifacts] = useState<any[]>([]);
  const [focusSource, setFocusSource] = useState<number | null>(null);
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);

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
    } catch {
      setNodeDetail(null);
    }
  }, []);

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
            <Empty description="还没有课程或资料" />
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

      <div className="tutor-left-section">
        <div className="tutor-left-title">
          回答依据的资料
          <span className="tutor-left-count">{allSelected ? readyMaterials.length : selectedDocs.length} 份</span>
        </div>
        {sessionLoading ? <Spin size="small" /> : (
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

  return (
    <div className="tutor-workspace">
      {isNarrow ? (
        <>
          <div className="tutor-mobile-bar">
            <Button type="text" icon={<MenuOutlined />} onClick={() => setLeftOpen(true)}>资料与路线</Button>
            <span className="tutor-mobile-title">{nodeDetail ? nodeDetail.label : 'AI 伴学'}</span>
            <Button type="text" icon={<FileTextOutlined />} onClick={() => setRightOpen(true)}>引用</Button>
          </div>
          {leftOpen && (
            <div className="tutor-drawer tutor-drawer-left">
              <Button type="text" icon={<CloseOutlined />} className="tutor-drawer-close"
                      onClick={() => setLeftOpen(false)} />
              {leftPanel}
            </div>
          )}
          {rightOpen && (
            <div className="tutor-drawer tutor-drawer-right">
              <Button type="text" icon={<CloseOutlined />} className="tutor-drawer-close"
                      onClick={() => setRightOpen(false)} />
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
          )}
        </>
      ) : (
        <aside className="tutor-aside tutor-aside-left">{leftPanel}</aside>
      )}

      <main className="tutor-main">
        <div className="tutor-main-head">
          <Segmented
            value={ability}
            onChange={(v) => {
              setAbility(String(v));
              updateTutorSession({ subject_id: subjectId, ability: String(v) });
            }}
            options={Object.entries(ABILITY_LABELS).map(([key, label]) => ({ label, value: key }))}
          />
          <div className="tutor-main-scope">
            <Tooltip title={scopeLabel || '基于全部资料'}>
              <Tag icon={<DatabaseOutlined />} color={allSelected ? 'default' : 'teal'}>
                {allSelected ? `全部资料 ${readyMaterials.length} 份` : `所选 ${selectedDocs.length} 份`}
              </Tag>
            </Tooltip>
          </div>
        </div>

        {nodeDetail && (
          <div className="tutor-node-bar">
            <div className="tutor-node-info">
              <Tag color="green">{nodeDetail.status_label || '未开始'}</Tag>
              <span className="tutor-node-name">{nodeDetail.label}</span>
              {nodeDetail.source_docs?.length > 0 && (
                <span className="tutor-node-src">来源：{nodeDetail.source_docs.join('、')}</span>
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
            <Space size={6} wrap>
              <Button size="small" icon={<BulbOutlined />} onClick={() => startNodeActivity('learn')}>学习</Button>
              <Button size="small" icon={<FormOutlined />} onClick={() => startNodeActivity('practice')}>练习</Button>
              <Button size="small" icon={<CheckSquareOutlined />} onClick={() => startNodeActivity('quiz')}>检测</Button>
              <Button size="small" icon={<FileTextOutlined />} onClick={() => startNodeActivity('note')}>记笔记</Button>
            </Space>
          </div>
        )}

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
                可以直接提问，或从下面的操作开始。回答会给出来自课程资料的引用，可点开核对原文。
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
                    ? <MarkdownText text={msg.content} onCitation={(id) => { setFocusSource(id); setRightOpen(true); }} />
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

      {!isNarrow && (
        <aside className="tutor-aside tutor-aside-right">
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
        </aside>
      )}
    </div>
  );
};

export default TutorWorkspace;
