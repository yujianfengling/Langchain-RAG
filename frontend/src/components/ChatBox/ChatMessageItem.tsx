import React, { useEffect, useMemo, useState } from 'react';
import { List, Avatar, Typography, Space, Spin, Button, Tag, Alert, Popconfirm, message } from 'antd';
import {
  UserOutlined, RobotOutlined,
  LoadingOutlined, CheckCircleOutlined, CloseCircleOutlined,
  MinusCircleOutlined, StopOutlined,
} from '@ant-design/icons';
import { ChatMessage, ChatMode } from '../../types';
import type { AttemptResult, PublicQuestion } from '../../api/student';
import { MessageBubble } from '../AnimateMotion';
import StructuredMessage from '../StructuredMessage';
import FeedbackButtons from '../FeedbackButtons';
import MarkdownIt from 'markdown-it';
import { createMarkdown } from '../../utils/markdown';
import hljs from 'highlight.js';
import { applyAdjustment, listAdjustments, PlanAdjustment } from '../../api/planAgent';

const { Text } = Typography;

// 初始化Markdown渲染器（模块级单例，跨消息复用；避免每次 render 重建）
const md: MarkdownIt = createMarkdown({
  html: true,
  linkify: true,
  typographer: true,
  highlight: function (str: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre class="hljs"><code>' +
               hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
               '</code></pre>';
      } catch (__) { /* 高亮失败时回退为转义纯文本 */ }
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>';
  }
});

interface ChatMessageItemProps {
  msg: ChatMessage;
  mode: ChatMode;
  /** 当前学科（用于把结构化卡片内的作答关联到学科） */
  subjectId?: string;
  /** 当前会话（用于把作答关联到来源会话） */
  conversationId?: string;
  /** 来源学习任务（从学习计划进入时携带） */
  sourceTaskId?: string;
  /** 学习助手是否正在执行（用于展示"停止"按钮） */
  agentRunning?: boolean;
  /** 停止学习助手 */
  onStopAgent?: () => void;
  /** 结构化题卡作答完成回调（用于复习结束反馈） */
  onAnswered?: (result: AttemptResult, question: PublicQuestion,
                progress: { answered: number; total: number }) => void;
}

const AGENT_STATUS_META: Record<string, { color: string; label: string }> = {
  running: { color: 'processing', label: '执行中' },
  done: { color: 'success', label: '已完成' },
  failed: { color: 'error', label: '失败' },
  skipped: { color: 'warning', label: '已跳过' },
  cancelled: { color: 'default', label: '已取消' },
  timeout: { color: 'warning', label: '超时' },
};

const agentStatusIcon = (status: string) => {
  if (status === 'running') return <LoadingOutlined />;
  if (status === 'done') return <CheckCircleOutlined />;
  if (status === 'failed') return <CloseCircleOutlined />;
  return <MinusCircleOutlined />;
};

/** Agent 建议的计划调整：**在会话内直接确认应用**
 *
 * 背景（2026-10-03 补齐，见交付报告"Agent 缺口 ①"）：Agent 跑完
 * `propose_adjustment` 只会在步骤摘要里显示"已生成调整建议"，学生必须先切到
 * 「计划预算」页签才能确认；也就是"提议 → 确认"这段 UI 链路在对话里是断的。
 * 这里复用既有的 `GET /plan/adjustments`（取最新 PROPOSED）与
 * `POST /plan/adjustments/{id}/apply`（重大调整由服务端强制 confirm），
 * 不新增后端接口、不改流式契约。
 */
const AgentAdjustmentAction: React.FC<{ subjectId?: string }> = ({ subjectId }) => {
  const [adjustment, setAdjustment] = useState<PlanAdjustment | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);

  const load = async () => {
    if (!subjectId) return;
    setLoading(true);
    try {
      const res: any = await listAdjustments(subjectId, 'PROPOSED');
      const list: PlanAdjustment[] = res?.data || [];
      setAdjustment(list.length ? list[list.length - 1] : null);
    } catch {
      setAdjustment(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjectId]);

  const apply = async () => {
    if (!adjustment) return;
    setApplying(true);
    try {
      // confirm=true：重大调整的强制确认由服务端校验（未确认会返回 confirmation_required）
      await applyAdjustment(adjustment.id, true);
      message.success('已应用到学习计划');
      setAdjustment(null);
    } catch (e: any) {
      message.error(e?.message || '应用失败，请稍后重试');
    } finally {
      setApplying(false);
    }
  };

  if (loading) {
    return (
      <div style={{ marginTop: 8 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>正在读取待确认的调整建议…</Text>
      </div>
    );
  }
  if (!adjustment) {
    return (
      <div style={{ marginTop: 8 }}>
        <Text type="secondary" style={{ fontSize: 11 }}>当前没有待确认的计划调整</Text>
      </div>
    );
  }
  const changeCount = (adjustment.changes || []).length;
  const brief = adjustment.summary || adjustment.reason || '助手建议调整学习计划';
  return (
    <div style={{ marginTop: 8 }}>
      <Space size={8} wrap>
        <Text style={{ fontSize: 12 }}>
          待确认：{brief}（{changeCount} 处改动{adjustment.major ? '，重大调整' : ''}）
        </Text>
        <Popconfirm
          title="应用这次计划调整？"
          description={
            <div style={{ maxWidth: 320 }}>
              <div>{brief}</div>
              <div style={{ marginTop: 4 }}>
                共 {changeCount} 处改动；应用后会写入你的学习计划（可在计划页撤销）。
              </div>
            </div>
          }
          okText="确认应用"
          cancelText="取消"
          onConfirm={apply}
        >
          <Button size="small" type="primary" loading={applying}>确认应用</Button>
        </Popconfirm>
        <Button size="small" onClick={load} loading={loading}>刷新</Button>
      </Space>
    </div>
  );
};

/** 运行状态 → 中文 */
const AGENT_RUN_LABEL: Record<string, string> = {
  SUCCEEDED: '已完成',
  RUNNING: '进行中',
  FAILED: '未完成',
  CANCELLED: '已停止',
  TIMEOUT: '超时',
};

/**
 * 学习助手执行过程（只展示工具调用与结果摘要，不展示模型思维过程）。
 *
 * 2026-10-05 简化：**默认收起**——执行中才展开，结束后只留一行摘要 +
 * "查看执行过程"；工具细节不再默认占据整屏。
 */
const AgentProgress: React.FC<{
  steps: ChatMessage['agent_steps'];
  run?: ChatMessage['agent_run'];
  subjectId?: string;
}> = ({ steps, run, subjectId }) => {
  const running = !run || run.status === 'RUNNING';
  const [showSteps, setShowSteps] = useState(running);
  useEffect(() => {
    if (running) setShowSteps(true);
  }, [running]);

  const hasAdjustment = (steps || []).some(
    (s) => s.tool === 'propose_adjustment' && s.status === 'done');

  return (
    <div style={{ minWidth: 280 }}>
      <Space size={8} style={{ marginBottom: 6 }} wrap>
        <Text strong style={{ fontSize: 13 }}>学习助手</Text>
        {run && (
          <Tag color={run.status === 'SUCCEEDED' ? 'success'
            : run.status === 'CANCELLED' ? 'default'
            : run.status === 'RUNNING' ? 'processing' : 'error'}>
            {AGENT_RUN_LABEL[run.status] || run.status}
          </Tag>
        )}
        <Button type="link" size="small" style={{ padding: 0 }}
                onClick={() => setShowSteps((v) => !v)}>
          {showSteps ? '收起执行过程' : '查看执行过程'}
        </Button>
      </Space>
      {showSteps && (
        <>
          <List
            size="small"
            dataSource={steps || []}
            locale={{ emptyText: '正在启动…' }}
            renderItem={(s) => {
              const meta = AGENT_STATUS_META[s.status] || AGENT_STATUS_META.running;
              return (
                <List.Item style={{ padding: '4px 0', border: 'none' }}>
                  <Space size={8} align="start">
                    <Tag color={meta.color} icon={agentStatusIcon(s.status)}>{meta.label}</Tag>
                    <div>
                      <Text style={{ fontSize: 13 }}>{s.label || s.tool}</Text>
                      {s.summary && (
                        <div><Text type="secondary" style={{ fontSize: 12 }}>{s.summary}</Text></div>
                      )}
                      {s.error && (
                        <div><Text type="danger" style={{ fontSize: 12 }}>{s.error}</Text></div>
                      )}
                    </div>
                  </Space>
                </List.Item>
              );
            }}
          />
          {/* 助手提出了计划调整 → 会话内即可确认应用（不必切页签） */}
          {hasAdjustment && <AgentAdjustmentAction subjectId={subjectId} />}
        </>
      )}
      {run?.error && (
        <Alert type="warning" showIcon style={{ marginTop: 8 }}
               message={run.error.includes(';') ? '部分步骤未完成（仍可手动操作）' : run.error} />
      )}
    </div>
  );
};

/**
 * 单条聊天消息（V2.5.4 拆分）
 *
 * 性能设计：
 * - React.memo：历史消息的 msg 引用不变时，不随流式 delta 重渲染
 * - Markdown useMemo：content 未变化时不重新执行 md.render / highlight.js
 * - 正在流式的那条消息 content 变化 → 仅它自己重渲染
 */
const ChatMessageItem: React.FC<ChatMessageItemProps> = React.memo(
  ({ msg,
     subjectId, conversationId, sourceTaskId,
     agentRunning, onStopAgent, onAnswered }) => {
    const isUser = msg.role === 'user';
    const isStructured = !!msg.message_type && msg.message_type !== 'chat' && !!msg.payload;
    // "查看原文"展开状态（按来源 id）
    const [showOriginal, setShowOriginal] = useState<Record<number, boolean>>({});

    // Markdown 缓存：仅当 content 变化时重新解析（历史消息不会重复执行 md.render）
    const markdownHtml = useMemo(() => {
      if (isUser || isStructured || !msg.content) return '';
      return md.render(msg.content);
    }, [msg.content, isUser, isStructured]);

    return (
      <List.Item
        style={{
          justifyContent: isUser ? 'flex-end' : 'flex-start',
          padding: '12px 0',
          border: 'none',
        }}
      >
        <MessageBubble delay={0} className="chat-bubble" style={{ maxWidth: '80%' }}>
        <div className="chat-msg-row" style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: '12px',
          flexDirection: isUser ? 'row-reverse' : 'row',
        }}>
          <Avatar
            icon={isUser ? <UserOutlined /> : <RobotOutlined />}
            style={{
              backgroundColor: isUser ? '#0f766e' : '#059669',
              flexShrink: 0,
            }}
          />

          <div className="chat-bubble-content" style={{
            background: isUser ? '#0f766e' : '#f6f6f6',
            color: isUser ? '#fff' : '#333',
            padding: isStructured ? '12px' : '12px 16px',
            borderRadius: '12px',
            maxWidth: '100%',
            minWidth: 0,
            wordBreak: 'break-word',
          }}>
            {/* 学生可感知的一句变化说明（对应真实作答记录；无证据时不显示） */}
            {!isUser && !isStructured && msg.teaching?.adaptation_note ? (
              <div style={{
                marginBottom: 8, padding: '6px 10px',
                background: 'rgba(15,118,110,0.08)',
                border: '1px solid rgba(15,118,110,0.2)',
                borderRadius: 6, fontSize: 12, color: '#0f766e', fontWeight: 600,
              }}>
                {msg.teaching.adaptation_note}
              </div>
            ) : null}
            {isUser ? (
              <Text style={{ color: '#fff', margin: 0 }}>{msg.content}</Text>
            ) : isStructured ? (
              <StructuredMessage
                messageType={msg.message_type!}
                payload={msg.payload}
                subjectId={subjectId}
                conversationId={conversationId}
                sourceTaskId={sourceTaskId}
                onAnswered={onAnswered}
              />
            ) : msg.agent_steps || msg.agent_run ? (
              <AgentProgress steps={msg.agent_steps} run={msg.agent_run} subjectId={subjectId} />
            ) : msg.content ? (
              <div
                className="markdown-body"
                dangerouslySetInnerHTML={{ __html: markdownHtml }}
                style={{
                  background: 'transparent',
                  color: '#333',
                }}
              />
            ) : (
              <Space size={8}>
                <Spin size="small" />
                <Text type="secondary" style={{ fontSize: '13px' }}>正在思考...</Text>
              </Space>
            )}

            {/* RAG 回答依据：真实检索结果（文件/页码/章节 + 原文），无依据时如实说明 */}
            {!isUser && !isStructured
              && ((msg.sources && msg.sources.length > 0) || msg.context_used === false) && (() => {
              const sources = msg.sources || [];
              // context_used 的准确含义：检索到课程资料并放进了模型上下文；
              // "回答是否真的引用"以后端从回答解析出的 cited_source_ids 为准
              // （确定性信号，不靠回答文案猜测）。
              const citedIds = msg.cited_source_ids || [];
              const cited = sources.filter((s: any) => citedIds.includes(s.id));
              const others = sources.filter((s: any) => !citedIds.includes(s.id));
              const noBasis = msg.context_used === false;
              const item = (s: any) => (
                <div key={s.id} style={{ marginBottom: 4 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <Text style={{ fontSize: 12, color: '#334155' }}>
                      [{s.id}] {s.file_name || '未知来源'}
                      {s.page != null ? ' · 第 ' + s.page + ' 页' : ''}
                      {s.section ? ' · ' + s.section : ''}
                    </Text>
                    {s.snippet ? (
                      <Button
                        type="link" size="small"
                        style={{ padding: 0, height: 'auto', fontSize: 12 }}
                        onClick={() => setShowOriginal((prev) => ({ ...prev, [s.id]: !prev[s.id] }))}
                      >
                        {showOriginal[s.id] ? '收起原文' : '查看原文'}
                      </Button>
                    ) : null}
                  </div>
                  {showOriginal[s.id] && s.snippet ? (
                    <div style={{
                      marginTop: 4, padding: '6px 8px', background: '#fff',
                      border: '1px solid #e2e8f0', borderRadius: 6,
                      fontSize: 12, lineHeight: 1.6, color: '#334155', whiteSpace: 'pre-wrap',
                    }}>
                      {s.snippet}
                    </div>
                  ) : null}
                </div>
              );
              const diagnostics = (
                <details style={{ marginTop: 4 }}>
                  <summary style={{ cursor: 'pointer', fontSize: 11, color: '#64748b' }}>检索诊断</summary>
                  <div style={{ marginTop: 4, fontSize: 11, color: '#64748b' }}>
                    <div>命中片段：{sources.length} 处</div>
                    {sources.map((s: any) => (
                      <div key={'d' + s.id}>
                        [{s.id}] {s.retrieval_type === 'keyword' ? '关键词检索'
                          : s.retrieval_type === 'vector' ? '向量检索' : '检索'}
                        {s.score != null ? ' · 相似度 ' + Number(s.score).toFixed(3) : ''}
                      </div>
                    ))}
                  </div>
                </details>
              );
              const hasCited = cited.length > 0;
              const tone = noBasis
                ? { bg: '#fffbeb', border: '#fde68a', color: '#92400e' }
                : hasCited
                  ? { bg: 'rgba(15,118,110,0.06)', border: 'rgba(15,118,110,0.18)', color: '#0f766e' }
                  : { bg: '#f8fafc', border: '#e2e8f0', color: '#475569' };
              const title = noBasis
                ? '课程资料中未找到依据：以下回答来自模型通用知识，请自行核对。'
                : hasCited
                  ? '依据课程资料（' + cited.length + ' 处，回答已标注引用）'
                  : '已检索到 ' + sources.length + ' 个课程资料片段（回答未标注具体引用）';
              return (
                <div style={{
                  marginTop: '8px', padding: '8px 10px', borderRadius: '6px',
                  background: tone.bg, border: '1px solid ' + tone.border,
                }}>
                  <Text style={{ fontSize: 12, fontWeight: 600, color: tone.color }}>{title}</Text>
                  {noBasis ? (
                    sources.length > 0 ? (
                      <details style={{ marginTop: 4 }}>
                        <summary style={{ cursor: 'pointer', fontSize: 11, color: tone.color }}>
                          查看检索到的片段
                        </summary>
                        <div style={{ marginTop: 4 }}>{sources.map(item)}</div>
                        {diagnostics}
                      </details>
                    ) : null
                  ) : hasCited ? (
                    <>
                      <div style={{ marginTop: 4 }}>{cited.map(item)}</div>
                      {others.length > 0 && (
                        <details style={{ marginTop: 4 }}>
                          <summary style={{ cursor: 'pointer', fontSize: 11, color: '#64748b' }}>
                            其他检索到的片段（{others.length} 条，未标注引用）
                          </summary>
                          <div style={{ marginTop: 4 }}>{others.map(item)}</div>
                        </details>
                      )}
                      {diagnostics}
                    </>
                  ) : (
                    <details style={{ marginTop: 4 }}>
                      <summary style={{ cursor: 'pointer', fontSize: 11, color: '#64748b' }}>
                        查看检索到的片段与诊断
                      </summary>
                      <div style={{ marginTop: 4 }}>{sources.map(item)}</div>
                      {diagnostics}
                    </details>
                  )}
                </div>
              );
            })()}

            {/* 本轮教学策略与依据：默认折叠（学习记忆详情），学生需要时可展开核对 */}
            {!isUser && msg.teaching?.reasons?.length ? (
              <details style={{ marginTop: 8 }}>
                <summary style={{ cursor: 'pointer', fontSize: 12, color: '#0f766e' }}>
                  查看本次讲解依据
                </summary>
                <div style={{
                  marginTop: 6, padding: '8px 10px',
                  background: 'rgba(15,118,110,0.06)', borderRadius: '6px',
                }}>
                  <Text style={{ fontSize: 12, fontWeight: 600, color: '#0f766e' }}>
                    本次讲解依据（{msg.teaching.mode === 'foundation' ? '基础优先'
                      : msg.teaching.mode === 'advanced' ? '综合提升' : '常规'}）
                  </Text>
                  <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                    {msg.teaching.reasons.slice(0, 4).map((r, i) => (
                      <li key={i}><Text type="secondary" style={{ fontSize: 12 }}>{r}</Text></li>
                    ))}
                  </ul>
                  {/* 明确设置与生效记忆：学生可核对"系统用了我的哪些信息" */}
                  {(msg.teaching.applied?.preferences?.length
                    || msg.teaching.applied?.memory?.length) ? (
                    <div style={{ marginTop: '6px' }}>
                      <Text style={{ fontSize: 12, fontWeight: 600, color: '#0f766e' }}>
                        已采用你的设置与记忆
                      </Text>
                      <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                        {(msg.teaching.applied?.preferences || []).map((r, i) => (
                          <li key={'p' + i}>
                            <Text type="secondary" style={{ fontSize: 12 }}>{r}</Text>
                          </li>
                        ))}
                        {(msg.teaching.applied?.memory || []).slice(0, 3).map((r, i) => (
                          <li key={'m' + i}>
                            <Text type="secondary" style={{ fontSize: 12 }}>{r}</Text>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>
              </details>
            ) : null}

            {/* 消息底部操作：反馈 / 停止（不再显示模型与耗时等内部指标） */}
            {!isUser && (
              <div style={{ marginTop: '6px', display: 'flex', alignItems: 'center', gap: 8 }}>
                <FeedbackButtons interactionId={msg.interaction_id} />
                {/* 学习助手执行中 → 可停止 */}
                {agentRunning && msg.agent_steps && onStopAgent && (
                  <Button size="small" danger icon={<StopOutlined />} onClick={onStopAgent}>
                    停止
                  </Button>
                )}
              </div>
            )}
          </div>
        </div>
        </MessageBubble>
      </List.Item>
    );
  }
);

ChatMessageItem.displayName = 'ChatMessageItem';

export default ChatMessageItem;