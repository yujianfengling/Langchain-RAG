import React, { useMemo } from 'react';
import { List, Avatar, Typography, Space, Spin, Button, Tooltip } from 'antd';
import {
  UserOutlined, RobotOutlined, SoundOutlined, PauseOutlined,
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
  /** 语音播放/暂停回调（父组件 useCallback 稳定引用，避免破坏 React.memo） */
  onPlay: (msg: ChatMessage) => void;
}

/**
 * 单条聊天消息（V2.5.4 拆分）
 *
 * 性能设计：
 * - React.memo：历史消息的 msg 引用不变时，不随流式 delta 重渲染
 * - Markdown useMemo：content 未变化时不重新执行 md.render / highlight.js
 * - 正在流式的那条消息 content 变化 → 仅它自己重渲染
 */
const ChatMessageItem: React.FC<ChatMessageItemProps> = React.memo(
  ({ msg, mode, isPlaying, onPlay }) => {
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
              <StructuredMessage messageType={msg.message_type!} payload={msg.payload} />
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

            {/* 语音播放按钮 + 模型信息 */}
            {!isUser && (
              <div style={{ marginTop: '6px', display: 'flex', alignItems: 'center', gap: 8 }}>
                {msg.content && (
                  <Tooltip title={isPlaying ? '暂停' : '语音播放'}>
                    <Button
                      size="small"
                      type="text"
                      icon={isPlaying ? <PauseOutlined /> : <SoundOutlined />}
                      onClick={() => onPlay(msg)}
                      style={{ fontSize: 12 }}
                    />
                  </Tooltip>
                )}
                {(msg.model || msg.latency != null) && (
                  <Text type="secondary" style={{ fontSize: '11px' }}>
                    {msg.model ? `模型: ${msg.model}` : ''}
                    {msg.ttft != null ? ` | 首字响应: ${msg.ttft.toFixed(2)}s` : ''}
                    {msg.latency != null ? ` | 总耗时: ${msg.latency.toFixed(2)}s` : ''}
                  </Text>
                )}
                {/* V2.6：回答反馈按钮（保留自 upstream） */}
                <FeedbackButtons messageId={msg.id} userId="default_user" />
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
