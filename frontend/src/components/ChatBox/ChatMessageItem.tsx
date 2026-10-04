import React, { useEffect, useMemo, useState } from 'react';
import { List, Avatar, Typography, Space, Spin, Button, Tag, Alert, Popconfirm, message } from 'antd';
import {
  UserOutlined, RobotOutlined,
  LoadingOutlined, CheckCircleOutlined, CloseCircleOutlined,
  MinusCircleOutlined, StopOutlined,
} from '@ant-design/icons';
import { ChatMessage, ChatMode } from '../../types';
import { MessageBubble } from '../AnimateMotion';
import StructuredMessage from '../StructuredMessage';
import FeedbackButtons from '../FeedbackButtons';
import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js';
import { applyAdjustment, listAdjustments, PlanAdjustment } from '../../api/planAgent';

const { Text } = Typography;

// 初始化Markdown渲染器（模块级单例，跨消息复用；避免每次 render 重建）
const md: MarkdownIt = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
  highlight: function (str: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre class="hljs"><code>' +
               hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
               '</code></pre>';
      } catch (__) {}
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

/** 学习助手执行过程（只展示工具调用与结果摘要，不展示模型思维过程） */
const AgentProgress: React.FC<{
  steps: ChatMessage['agent_steps'];
  run?: ChatMessage['agent_run'];
  subjectId?: string;
}> = ({ steps, run, subjectId }) => (
  <div style={{ minWidth: 320 }}>
    <Space size={8} style={{ marginBottom: 6 }}>
      <Text strong style={{ fontSize: 13 }}>学习助手执行过程</Text>
      {run && (
        <Tag color={run.status === 'SUCCEEDED' ? 'success'
          : run.status === 'CANCELLED' ? 'default'
          : run.status === 'RUNNING' ? 'processing' : 'error'}>
          {run.status}
        </Tag>
      )}
    </Space>
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
    {run?.error && (
      <Alert type="warning" showIcon style={{ marginTop: 8 }}
             message={run.error.includes(';') ? '部分步骤未完成（仍可手动操作）' : run.error} />
    )}
    {/* 助手提出了计划调整 → 会话内即可确认应用（不必切页签） */}
    {(steps || []).some((s) => s.tool === 'propose_adjustment' && s.status === 'done') && (
      <AgentAdjustmentAction subjectId={subjectId} />
    )}
    <div style={{ marginTop: 6 }}>
      <Text type="secondary" style={{ fontSize: 11 }}>
        说明：这里记录的是工具调用与结果摘要，不含模型内部推理过程；
        计划、错题、判分等基础功能不依赖本次运行。
      </Text>
    </div>
  </div>
);

/**
 * 单条聊天消息（V2.5.4 拆分）
 *
 * 性能设计：
 * - React.memo：历史消息的 msg 引用不变时，不随流式 delta 重渲染
 * - Markdown useMemo：content 未变化时不重新执行 md.render / highlight.js
 * - 正在流式的那条消息 content 变化 → 仅它自己重渲染
 */
const ChatMessageItem: React.FC<ChatMessageItemProps> = React.memo(
  ({ msg, mode,
     subjectId, conversationId, sourceTaskId,
     agentRunning, onStopAgent }) => {
    const isUser = msg.role === 'user';
    const isStructured = !!msg.message_type && msg.message_type !== 'chat' && !!msg.payload;

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
        <MessageBubble delay={0} style={{ maxWidth: '80%' }}>
        <div style={{
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

          <div style={{
            background: isUser ? '#0f766e' : '#f6f6f6',
            color: isUser ? '#fff' : '#333',
            padding: isStructured ? '12px' : '12px 16px',
            borderRadius: '12px',
            maxWidth: '100%',
            minWidth: 0,
            wordBreak: 'break-word',
          }}>
            {isUser ? (
              <Text style={{ color: '#fff', margin: 0 }}>{msg.content}</Text>
            ) : isStructured ? (
              <StructuredMessage
                messageType={msg.message_type!}
                payload={msg.payload}
                subjectId={subjectId}
                conversationId={conversationId}
                sourceTaskId={sourceTaskId}
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

            {/* 显示引用来源（列表形式） */}
            {!isUser && mode === 'chat' && msg.sources && msg.sources.length > 0 && (
              <div style={{
                marginTop: '8px',
                padding: '8px 10px',
                background: 'rgba(0,0,0,0.04)',
                borderRadius: '6px',
              }}>
                <Text type="secondary" style={{ fontSize: '12px', fontWeight: 600 }}>
                  参考来源（{msg.sources.length}）
                </Text>
                <div style={{ marginTop: '4px' }}>
                  {msg.sources.map(s => (
                    <div key={s.id} style={{ fontSize: '12px', lineHeight: '20px', color: '#666' }}>
                      [{s.id}] {s.file_name || '未知来源'}
                      {s.page != null ? ` · 第 ${s.page} 页` : ''}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 本轮教学策略与依据（画像 → 教学安排，可解释） */}
            {!isUser && msg.teaching?.reasons?.length ? (
              <div style={{
                marginTop: '8px', padding: '8px 10px',
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
                        <li key={`p${i}`}>
                          <Text type="secondary" style={{ fontSize: 12 }}>{r}</Text>
                        </li>
                      ))}
                      {(msg.teaching.applied?.memory || []).slice(0, 3).map((r, i) => (
                        <li key={`m${i}`}>
                          <Text type="secondary" style={{ fontSize: 12 }}>{r}</Text>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            ) : null}

            {/* 模型信息（语音朗读已移除） */}
            {!isUser && (
              <div style={{ marginTop: '6px', display: 'flex', alignItems: 'center', gap: 8 }}>
                {(msg.model || msg.latency != null) && (
                  <Text type="secondary" style={{ fontSize: '11px' }}>
                    {/* 显示后端实际使用的提供方与模型（历史消息保留当时的标记） */}
                    {msg.model ? `模型: ${msg.provider ? `${msg.provider} / ` : ''}${msg.model}` : ''}
                    {msg.ttft != null ? ` | 首字响应: ${msg.ttft.toFixed(2)}s` : ''}
                    {msg.latency != null ? ` | 总耗时: ${msg.latency.toFixed(2)}s` : ''}
                  </Text>
                )}
                {/* 回答反馈：关联后端回写的真实 interaction_id（阶段一修正） */}
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
