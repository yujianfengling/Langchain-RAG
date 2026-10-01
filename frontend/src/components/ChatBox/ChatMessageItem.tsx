import React, { useMemo } from 'react';
import { List, Avatar, Typography, Space, Spin, Button, Tooltip, Tag, Alert } from 'antd';
import {
  UserOutlined, RobotOutlined, SoundOutlined, PauseOutlined,
  LoadingOutlined, CheckCircleOutlined, CloseCircleOutlined,
  MinusCircleOutlined, StopOutlined,
} from '@ant-design/icons';
import { ChatMessage, ChatMode } from '../../types';
import { MessageBubble } from '../AnimateMotion';
import StructuredMessage from '../StructuredMessage';
import FeedbackButtons from '../FeedbackButtons';
import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js';

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
  /** 该消息是否正在播放语音（仅当前播放中的消息此值为 true） */
  isPlaying: boolean;
  /** 播放状态机（idle/requesting/playing/paused/stopped/error），用于按钮与提示 */
  playbackState?: string;
  /** 已排队待播放的句子数（逐句合成时可见进度） */
  playbackQueued?: number;
  /** 语音播放/暂停回调（父组件 useCallback 稳定引用，避免破坏 React.memo） */
  onPlay: (msg: ChatMessage) => void;
  /** 立即停止朗读（新提问/切换会话也会自动停止） */
  onStopPlay?: () => void;
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

/** 学习助手执行过程（只展示工具调用与结果摘要，不展示模型思维过程） */
const AgentProgress: React.FC<{
  steps: ChatMessage['agent_steps'];
  run?: ChatMessage['agent_run'];
}> = ({ steps, run }) => (
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
  ({ msg, mode, isPlaying, playbackState, playbackQueued, onPlay, onStopPlay,
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
              <AgentProgress steps={msg.agent_steps} run={msg.agent_run} />
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

            {/* 语音播放按钮 + 模型信息 */}
            {!isUser && (
              <div style={{ marginTop: '6px', display: 'flex', alignItems: 'center', gap: 8 }}>
                {msg.content && (
                  <Tooltip title={isPlaying ? '暂停朗读' : '朗读这条回答'}>
                    <Button
                      size="small"
                      type="text"
                      icon={isPlaying ? <PauseOutlined /> : <SoundOutlined />}
                      onClick={() => onPlay(msg)}
                      style={{ fontSize: 12 }}
                    />
                  </Tooltip>
                )}
                {/* 朗读失败：如实显示原因（不静默失败） */}
                {playbackState === 'error' && (
                  <Text type="danger" style={{ fontSize: '11px' }}>
                    朗读失败：语音服务不可用或尚未就绪（文字回答不受影响）
                  </Text>
                )}
                {/* 正在朗读：显示进度并允许立即停止（新提问/切换会话也会自动停） */}
                {isPlaying && (
                  <>
                    <Text type="secondary" style={{ fontSize: '11px' }}>
                      朗读中{playbackState === 'paused' ? '（已暂停）'
                        : playbackState === 'requesting' ? '（正在合成）' : ''}
                      {playbackQueued ? ` | 待播 ${playbackQueued} 句` : ''}
                    </Text>
                    {onStopPlay && (
                      <Button size="small" type="text" icon={<StopOutlined />}
                        onClick={onStopPlay} style={{ fontSize: 12 }}>
                        停止
                      </Button>
                    )}
                  </>
                )}
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
