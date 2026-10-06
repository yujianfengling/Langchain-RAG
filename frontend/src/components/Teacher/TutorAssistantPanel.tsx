import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert, Button, Card, Collapse, Input, Select, Space, Spin, Tag, Typography,
} from 'antd';
import {
  BookOutlined, BulbOutlined, CheckCircleFilled, CloseCircleFilled, DeleteOutlined,
  FileTextOutlined, NodeIndexOutlined, QuestionCircleOutlined, RobotOutlined,
  SendOutlined, StopOutlined,
} from '@ant-design/icons';
import {
  ABILITY_LABELS, TutorDoneEvent, TutorSource, streamTutorTurn,
} from '../../api/tutor';
import { Assignment, listAssignments } from '../../api/teacher';
import { EmptyBlock, MarkdownBlock } from './common';

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

/** 教学助手可渲染的成果与练习（字段与后端 tutor_agent 事件一致） */
interface ArtifactLite {
  id?: string;
  kind?: string;
  kind_label?: string;
  title?: string;
}

interface PracticeQuestion {
  id?: string;
  question?: string;
  type?: string;
  options?: { id?: string; text?: string }[];
  knowledge_point?: string | null;
}

interface ToolStep {
  tool: string;
  label: string;
  args?: Record<string, unknown>;
  ok?: boolean;
  summary?: string;
  error?: string;
}

interface StageStep {
  stage: string;
  note?: string;
}

interface TutorTurnView {
  id: string;
  userMessage: string;
  ability: string;
  stages: StageStep[];
  tools: ToolStep[];
  notices: string[];
  answer: string;
  streaming: string;
  sources: TutorSource[];
  cited: number[];
  artifacts: ArtifactLite[];
  payload: { quiz?: PracticeQuestion[]; topic?: string } | null;
  messageType: string;
  askUser: { question: string; options: string[] } | null;
  running: boolean;
  error?: string;
}

/** SSE 事件（字段与 services/tutor_agent.py 的 emit 对齐，仅取前端要展示的部分） */
interface TutorEvent {
  type: string;
  stage?: string;
  note?: string;
  tool?: string;
  label?: string;
  args?: Record<string, unknown>;
  ok?: boolean;
  summary?: string;
  error?: string;
  sources?: TutorSource[];
  artifact?: ArtifactLite;
  message?: string;
  content?: string;
}

interface QuickAction {
  key: string;
  label: string;
  ability: string;
  prompt: string;
  icon: React.ReactNode;
}

/**
 * 教师版「教学助手」（默认能力：学习整理 / 引导讲解）。
 *
 * 与统一伴学接口对齐：事件流里的阶段、工具调用、引用、成果分别渲染，
 * 不把工具调用伪装成回答，也不展示内部 ID 与耗时。
 */
const QUICK_ACTIONS: QuickAction[] = [
  { key: 'prep', label: '基于资料备课', ability: 'organize', icon: <BookOutlined />,
    prompt: '请基于本课程资料，整理这堂课的教学要点、重难点与备课提纲。' },
  { key: 'plan', label: '生成 / 修改教案', ability: 'organize', icon: <FileTextOutlined />,
    prompt: '请基于课程资料生成一份教案；如果已有教案，请指出需要修改或补充的地方。' },
  { key: 'weak', label: '薄弱知识点与练习', ability: 'explain', icon: <QuestionCircleOutlined />,
    prompt: '请查看当前学习记录，梳理需要重点关注的薄弱知识点，并为它们安排练习。' },
  { key: 'map', label: '课程思维导图', ability: 'map', icon: <NodeIndexOutlined />,
    prompt: '请根据当前课程资料生成课程思维导图。' },
  { key: 'ask', label: '自由问答', ability: 'chat', icon: <BulbOutlined />, prompt: '' },
];

const ABILITY_OPTIONS = ['organize', 'explain', 'map', 'practice', 'chat'].map((key) => ({
  value: key,
  label: ABILITY_LABELS[key] || key,
}));

export interface TutorAssistantPanelProps {
  subjectId?: string;
  courseId?: string;
  courseTitle?: string;
  /** 没有课程时的下一步（由 TeacherPage 切到「课程与备课」） */
  onGoPrep?: () => void;
}

const TutorAssistantPanel: React.FC<TutorAssistantPanelProps> = ({
  subjectId, courseId, courseTitle, onGoPrep,
}) => {
  const [ability, setAbility] = useState('organize');
  const [input, setInput] = useState('');
  const [turns, setTurns] = useState<TutorTurnView[]>([]);
  const [running, setRunning] = useState(false);
  const [deadlines, setDeadlines] = useState<Assignment[]>([]);
  const controllerRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const updateTurn = useCallback((
    id: string,
    patch: Partial<TutorTurnView> | ((t: TutorTurnView) => Partial<TutorTurnView>),
  ) => {
    setTurns((prev) => prev.map((t) => (
      t.id === id ? { ...t, ...(typeof patch === 'function' ? patch(t) : patch) } : t
    )));
  }, []);

  const handleEvent = useCallback((id: string, ev: TutorEvent) => {
    if (ev.type === 'done') return;
    switch (ev.type) {
      case 'stage':
        updateTurn(id, (t) => ({ stages: [...t.stages, { stage: ev.stage || '', note: ev.note }] }));
        break;
      case 'tool_call':
        updateTurn(id, (t) => ({
          tools: [...t.tools, { tool: ev.tool || '', label: ev.label || ev.tool || '工具', args: ev.args }],
        }));
        break;
      case 'tool_result':
        updateTurn(id, (t) => {
          const tools = [...t.tools];
          let idx = -1;
          for (let i = tools.length - 1; i >= 0; i -= 1) {
            if (tools[i].tool === ev.tool && tools[i].ok === undefined) { idx = i; break; }
          }
          const next: ToolStep = {
            tool: ev.tool || '', label: ev.label || ev.tool || '工具',
            ok: ev.ok, summary: ev.summary, error: ev.error,
          };
          if (idx >= 0) tools[idx] = { ...tools[idx], ...next };
          else tools.push(next);
          return { tools };
        });
        break;
      case 'sources':
        if (ev.sources && ev.sources.length) updateTurn(id, { sources: ev.sources });
        break;
      case 'artifact':
        if (ev.artifact) updateTurn(id, (t) => ({ artifacts: [...t.artifacts, ev.artifact as ArtifactLite] }));
        break;
      case 'notice':
        if (ev.message) updateTurn(id, (t) => ({ notices: [...t.notices, ev.message as string] }));
        break;
      case 'delta':
        updateTurn(id, (t) => ({ streaming: t.streaming + (ev.content || '') }));
        break;
      default:
        break;
    }
  }, [updateTurn]);

  const handleDone = useCallback((id: string, ev: TutorDoneEvent) => {
    updateTurn(id, {
      answer: ev.answer || '',
      sources: ev.sources || [],
      cited: ev.cited_source_ids || [],
      artifacts: (ev.artifacts || []) as ArtifactLite[],
      payload: (ev.payload as TutorTurnView['payload']) || null,
      messageType: ev.message_type || 'chat',
      askUser: ev.ask_user || null,
      running: false,
      streaming: '',
    });
    setRunning(false);
    controllerRef.current = null;
  }, [updateTurn]);

  const send = useCallback((raw: string, abilityOverride?: string) => {
    const message = raw.trim();
    const useAbility = abilityOverride || ability;
    if (!message || running || !subjectId) return;
    const id = 'turn-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
    setTurns((prev) => [...prev, {
      id, userMessage: message, ability: useAbility, stages: [], tools: [], notices: [],
      answer: '', streaming: '', sources: [], cited: [], artifacts: [], payload: null,
      messageType: 'chat', askUser: null, running: true,
    }]);
    setInput('');
    setRunning(true);
    controllerRef.current = streamTutorTurn(
      { message, subject_id: subjectId, ability: useAbility },
      {
        onEvent: (ev) => handleEvent(id, ev as TutorEvent),
        onDone: (ev) => handleDone(id, ev),
        onError: (msg) => {
          updateTurn(id, { running: false, error: msg });
          setRunning(false);
          controllerRef.current = null;
        },
      },
    );
  }, [ability, handleDone, handleEvent, running, subjectId, updateTurn]);

  const stop = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setRunning(false);
    setTurns((prev) => prev.map((t) => (
      t.running ? { ...t, running: false, error: '已停止本轮生成' } : t
    )));
  }, []);

  useEffect(() => () => { controllerRef.current?.abort(); }, []);

  // 作业截止日期只读展示：帮助教师确认 Agent 不会移动已定的截止日期
  useEffect(() => {
    if (!courseId) { setDeadlines([]); return; }
    let alive = true;
    listAssignments(courseId).then((res) => {
      if (alive) setDeadlines((res?.data?.assignments || []).filter((a) => !!a.due_date));
    }).catch(() => { if (alive) setDeadlines([]); });
    return () => { alive = false; };
  }, [courseId]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [turns]);

  if (!subjectId) {
    return (
      <Card size="small" title={<Space><RobotOutlined />教学助手</Space>}>
        <EmptyBlock description="教学助手需要先有一门课程。请先在「课程与备课」里选择或新建课程。">
          <Button type="primary" icon={<BookOutlined />} onClick={onGoPrep}>去课程与备课</Button>
        </EmptyBlock>
      </Card>
    );
  }

  const renderTurn = (t: TutorTurnView) => {
    const citedSources = t.sources.filter((s) => t.cited.length === 0 || t.cited.includes(s.id));
    const quiz = t.payload?.quiz || [];
    return (
      <Card key={t.id} size="small" style={{ marginBottom: 12 }}>
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          <div style={{
            alignSelf: 'flex-end', maxWidth: '92%', background: 'var(--color-primary-100)',
            padding: '8px 12px', borderRadius: 'var(--radius-md)',
          }}>
            <Text style={{ whiteSpace: 'pre-wrap' }}>{t.userMessage}</Text>
          </div>

          <Space size={6} wrap>
            <Tag>{ABILITY_LABELS[t.ability] || t.ability}</Tag>
            {t.running && <Spin size="small" />}
          </Space>

          {t.notices.map((n, i) => (
            <Alert key={'n' + i} type="warning" showIcon message={n} />
          ))}

          {t.stages.length > 0 && (
            <Collapse
              size="small"
              ghost
              items={[{
                key: 'stages',
                label: <Text type="secondary" style={{ fontSize: 12 }}>执行阶段 {t.stages.length} 步</Text>,
                children: (
                  <Space direction="vertical" size={2}>
                    {t.stages.map((s, i) => (
                      <Text key={i} type="secondary" style={{ fontSize: 12 }}>
                        {i + 1}. {s.stage}{s.note ? '：' + s.note : ''}
                      </Text>
                    ))}
                  </Space>
                ),
              }]}
            />
          )}

          {t.tools.length > 0 && (
            <Collapse
              size="small"
              items={[{
                key: 'tools',
                label: <Text style={{ fontSize: 12 }}>工具调用 {t.tools.length} 次（可展开）</Text>,
                children: (
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    {t.tools.map((tool, i) => (
                      <Space key={i} size={6} wrap align="start">
                        {tool.ok === false
                          ? <CloseCircleFilled style={{ color: 'var(--color-text-muted)' }} />
                          : tool.ok
                            ? <CheckCircleFilled style={{ color: 'var(--color-primary)' }} />
                            : <Spin size="small" />}
                        <Text style={{ fontSize: 13 }}>{tool.label}</Text>
                        {tool.summary && <Text type="secondary" style={{ fontSize: 12 }}>{tool.summary}</Text>}
                        {tool.error && <Text type="danger" style={{ fontSize: 12 }}>{tool.error}</Text>}
                      </Space>
                    ))}
                  </Space>
                ),
              }]}
            />
          )}

          {t.streaming && !t.answer && <Text type="secondary">{t.streaming}</Text>}
          {t.answer && <MarkdownBlock text={t.answer} />}

          {quiz.length > 0 && (
            <Card size="small" title={'已生成 ' + quiz.length + ' 道练习'}>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {quiz.map((q, i) => (
                  <div key={q.id || i}>
                    <Text style={{ fontSize: 13 }}>{i + 1}. {q.question}</Text>
                    {q.options && q.options.length > 0 && (
                      <Space direction="vertical" size={0} style={{ paddingLeft: 16, display: 'flex' }}>
                        {q.options.map((o) => (
                          <Text key={o.id || o.text} type="secondary" style={{ fontSize: 12 }}>
                            {o.id}. {o.text}
                          </Text>
                        ))}
                      </Space>
                    )}
                  </div>
                ))}
                <Text type="secondary" style={{ fontSize: 12 }}>答案由系统保存，学生作答后自动判分。</Text>
              </Space>
            </Card>
          )}

          {citedSources.length > 0 && (
            <Collapse
              size="small"
              items={[{
                key: 'sources',
                label: '资料依据 ' + citedSources.length + ' 条',
                children: (
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    {citedSources.map((s) => (
                      <div key={s.id}>
                        <Space size={6} wrap>
                          <Tag color="cyan">[{s.id}]</Tag>
                          <Text style={{ fontSize: 13 }} ellipsis={{ tooltip: s.file_name }}>{s.file_name}</Text>
                          {s.page != null && <Text type="secondary" style={{ fontSize: 12 }}>第 {s.page} 页</Text>}
                        </Space>
                        {s.snippet && (
                          <Paragraph type="secondary" style={{ fontSize: 12, margin: '4px 0 0 0' }}>
                            {s.snippet}
                          </Paragraph>
                        )}
                      </div>
                    ))}
                  </Space>
                ),
              }]}
            />
          )}

          {t.artifacts.length > 0 && (
            <Space size={6} wrap>
              <Text type="secondary" style={{ fontSize: 12 }}>已保存成果：</Text>
              {t.artifacts.map((a, i) => (
                <Tag key={a.id || i} color="green">
                  {a.kind_label || a.kind || '成果'}{a.title ? ' · ' + a.title : ''}
                </Tag>
              ))}
            </Space>
          )}

          {t.askUser && (
            <Alert
              type="info"
              showIcon
              message={t.askUser.question}
              action={t.askUser.options.length > 0 ? (
                <Space direction="vertical" size={4}>
                  {t.askUser.options.map((opt) => (
                    <Button key={opt} size="small" disabled={running} onClick={() => send(opt)}>{opt}</Button>
                  ))}
                </Space>
              ) : undefined}
            />
          )}

          {t.error && <Alert type="warning" showIcon message={t.error} />}
        </Space>
      </Card>
    );
  };

  return (
    <div>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
            <Space wrap>
              <Title level={5} style={{ margin: 0 }}><RobotOutlined /> 教学助手</Title>
              {courseTitle && <Tag color="blue">{courseTitle}</Tag>}
            </Space>
            <Space wrap>
              <Text type="secondary" style={{ fontSize: 12 }}>能力</Text>
              <Select size="small" style={{ width: 132 }} value={ability} onChange={setAbility} options={ABILITY_OPTIONS} />
            </Space>
          </Space>
          <Space wrap>
            {QUICK_ACTIONS.map((a) => (
              <Button
                key={a.key}
                size="small"
                icon={a.icon}
                onClick={() => { setAbility(a.ability); if (a.prompt) send(a.prompt, a.ability); }}
              >
                {a.label}
              </Button>
            ))}
          </Space>
        </Space>
      </Card>

      {turns.length === 0 && (
        <Card size="small" style={{ marginBottom: 12, background: 'var(--color-primary-50)', borderColor: 'var(--color-primary-100)' }}>
          <Space direction="vertical" size={4}>
            <Text strong><RobotOutlined /> 你好，我是本课程的教学助手</Text>
            <Text type="secondary" style={{ fontSize: 13 }}>
              我可以基于课程资料帮你备课、生成或修改教案、梳理需要重点关注的薄弱知识点并安排练习、
              生成课程思维导图，也可以直接回答课程相关问题。所有结论都会标注课程资料依据。
            </Text>
          </Space>
        </Card>
      )}

      <Card size="small" style={{ marginBottom: 12 }} title={<Space><FileTextOutlined />作业截止日期（只读）</Space>}>
        {deadlines.length === 0 ? (
          <Text type="secondary" style={{ fontSize: 13 }}>本课程暂无已设置截止日期的作业。</Text>
        ) : (
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            {deadlines.map((a) => (
              <Space key={a.id} size={8} wrap>
                <Tag color="blue">{a.due_date}</Tag>
                <Text style={{ maxWidth: 320 }} ellipsis={{ tooltip: a.title }}>{a.title}</Text>
                <Tag>{a.status === 'published' ? '已发布' : a.status === 'closed' ? '已关闭' : '草稿'}</Tag>
              </Space>
            ))}
          </Space>
        )}
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
          截止日期由教师设定，教学助手只读取、不会自动调整；如需修改请到「作业与批改」。
        </Text>
      </Card>

      {turns.map(renderTurn)}
      <div ref={bottomRef} />

      <Card size="small" style={{ marginTop: 12 }}>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="例如：用两节课的进度安排这一章的备课重点，并指出资料里没有覆盖的部分"
            autoSize={{ minRows: 2, maxRows: 6 }}
            disabled={running}
            onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); send(input); } }}
          />
          <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
            <Text type="secondary" style={{ fontSize: 12 }}>Enter 发送，Shift + Enter 换行。</Text>
            <Space>
              {turns.length > 0 && (
                <Button size="small" icon={<DeleteOutlined />} disabled={running} onClick={() => setTurns([])}>
                  清空
                </Button>
              )}
              {running
                ? <Button danger icon={<StopOutlined />} onClick={stop}>停止</Button>
                : <Button type="primary" icon={<SendOutlined />} disabled={!input.trim()} onClick={() => send(input)}>发送</Button>}
            </Space>
          </Space>
        </Space>
      </Card>
    </div>
  );
};

export default TutorAssistantPanel;
