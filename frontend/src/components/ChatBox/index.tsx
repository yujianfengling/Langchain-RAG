import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Input, Button, List, Avatar, Typography, Space, Spin, Select, Tag, message } from 'antd';
import { SendOutlined, UserOutlined, RobotOutlined, StopOutlined } from '@ant-design/icons';
import { ChatMessage, Subject, SourceInfo } from '../../types';
import { sendMessageStream } from '../../api/chat';
import { getReadyStatus } from '../../api/health';
import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js';
import './ChatBox.css';

const { Text, Title } = Typography;
const { TextArea } = Input;

// 初始化Markdown渲染器
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

interface ChatBoxProps {
  subjectId: string;
  subjects: Subject[];
  onSubjectChange?: (subjectId: string) => void;
}

const ChatBox: React.FC<ChatBoxProps> = ({ subjectId, subjects }) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string>('');
  const [selectedModel, setSelectedModel] = useState<string>('mimo');
  const [retrievalTag, setRetrievalTag] = useState<{ color: string; text: string } | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 组件卸载时中止未完成的流式请求
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
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

  // 将 /health/ready 状态映射为顶部标签（not_loaded + 索引就绪 = 可用，首次检索自动加载）
  const applyRetrievalTag = (ready: any) => {
    const mode = ready?.retrieval_mode;
    const faissOk = ready?.faiss?.available;
    const bm25Ok = ready?.bm25?.available;
    if (mode === 'hybrid_ready' || (mode === 'not_loaded' && faissOk && bm25Ok)) {
      setRetrievalTag({ color: 'success', text: '混合检索已就绪' });
    } else if (mode === 'loading') {
      setRetrievalTag({ color: 'processing', text: '向量检索组件正在加载' });
    } else if (mode === 'bm25_degraded') {
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

  // 模型选项
  const modelOptions = [
    { value: 'mimo', label: '小米MiMo' },
    { value: 'deepseek', label: 'DeepSeek' },
    { value: 'qwen', label: '通义千问' },
  ];

  // 生成会话ID
  useEffect(() => {
    const savedConversationId = localStorage.getItem('chat_conversation_id');
    if (savedConversationId) {
      setConversationId(savedConversationId);
    } else {
      const newId = Date.now().toString();
      setConversationId(newId);
      localStorage.setItem('chat_conversation_id', newId);
    }
  }, []);

  // 恢复历史消息
  useEffect(() => {
    if (conversationId) {
      const savedMessages = localStorage.getItem(`chat_messages_${conversationId}`);
      if (savedMessages) {
        try {
          const parsedMessages = JSON.parse(savedMessages);
          setMessages(parsedMessages);
        } catch (error) {
          console.error('解析历史消息失败:', error);
        }
      }
    }
  }, [conversationId]);

  // 保存消息到localStorage
  useEffect(() => {
    if (conversationId && messages.length > 0) {
      localStorage.setItem(`chat_messages_${conversationId}`, JSON.stringify(messages));
    }
  }, [messages, conversationId]);

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // 发送消息（真流式）
  const handleSend = async () => {
    if (!inputValue.trim()) {
      message.warning('请输入问题');
      return;
    }

    if (!subjectId) {
      message.warning('请选择学科');
      return;
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

    // 构建历史消息
    const history = messages
      .filter(msg => msg.role === 'user' || msg.role === 'assistant')
      .map(msg => ({
        role: msg.role,
        content: msg.content,
      }));

    const startedAt = Date.now();

    const controller = sendMessageStream(
      {
        question: userMessage.content,
        subject_id: subjectId,
        model: selectedModel,
        history,
      },
      {
        onStart: () => {
          // 检索完成，开始生成；可更新为"正在生成"
        },
        onDelta: (content) => {
          // 逐块追加到同一条AI消息，不重复创建
          setMessages(prev =>
            prev.map(m => (m.id === aiId ? { ...m, content: m.content + content } : m))
          );
        },
        onDone: (ev) => {
          const elapsed = ev.elapsed != null ? ev.elapsed : (Date.now() - startedAt) / 1000;
          setMessages(prev =>
            prev.map(m =>
              m.id === aiId
                ? {
                    ...m,
                    sources: normalizeSources(ev.sources),
                    model: ev.model,
                    latency: elapsed,
                    ttft: ev.ttft,
                  }
                : m
            )
          );
          setLoading(false);
          refreshStatus();
        },
        onError: (errMsg) => {
          // 已生成的内容保留；空内容时显示错误提示
          setMessages(prev =>
            prev.map(m =>
              m.id === aiId
                ? { ...m, content: m.content || `⚠️ ${errMsg}` }
                : m
            )
          );
          message.error(errMsg || '回答生成失败，请重试');
          setLoading(false);
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
    setLoading(false);
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

  // 渲染Markdown内容
  const renderMarkdown = (content: string) => {
    return { __html: md.render(content) };
  };

  // 渲染消息列表
  const renderMessage = (msg: ChatMessage) => {
    const isUser = msg.role === 'user';
    
    return (
      <List.Item style={{ 
        justifyContent: isUser ? 'flex-end' : 'flex-start',
        padding: '12px 0',
        border: 'none',
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: '12px',
          maxWidth: '80%',
          flexDirection: isUser ? 'row-reverse' : 'row',
        }}>
          <Avatar 
            icon={isUser ? <UserOutlined /> : <RobotOutlined />} 
            style={{ 
              backgroundColor: isUser ? '#1890ff' : '#52c41a',
              flexShrink: 0,
            }}
          />
          
          <div style={{
            background: isUser ? '#1890ff' : '#f6f6f6',
            color: isUser ? '#fff' : '#333',
            padding: '12px 16px',
            borderRadius: '12px',
            maxWidth: '100%',
            wordBreak: 'break-word',
          }}>
            {isUser ? (
              <Text style={{ color: '#fff', margin: 0 }}>{msg.content}</Text>
            ) : (
              msg.content ? (
                <div
                  className="markdown-body"
                  dangerouslySetInnerHTML={renderMarkdown(msg.content)}
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
              )
            )}
            
            {/* 显示引用来源（列表形式） */}
            {msg.sources && msg.sources.length > 0 && (
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

            {/* 显示模型和延迟信息 */}
            {(msg.model || msg.latency != null) && (
              <div style={{ marginTop: '4px' }}>
                <Text type="secondary" style={{ fontSize: '11px' }}>
                  {msg.model ? `模型: ${msg.model}` : ''}
                  {msg.ttft != null ? ` | 首字响应: ${msg.ttft.toFixed(2)}s` : ''}
                  {msg.latency != null ? ` | 总耗时: ${msg.latency.toFixed(2)}s` : ''}
                </Text>
              </div>
            )}
          </div>
        </div>
      </List.Item>
    );
  };

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
            AI智能问答
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

      {/* 消息列表 */}
      <div style={{
        flex: 1,
        overflow: 'auto',
        padding: '24px',
        background: '#fff',
      }}>
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
            renderItem={renderMessage}
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
        <div style={{ 
          display: 'flex', 
          alignItems: 'center', 
          gap: '12px',
          marginBottom: '8px',
        }}>
          <Text type="secondary" style={{ fontSize: '12px' }}>当前模型:</Text>
          <Select
            value={selectedModel}
            onChange={setSelectedModel}
            options={modelOptions}
            style={{ width: 120 }}
            size="small"
          />
        </div>
        <Space.Compact style={{ width: '100%' }}>
          <TextArea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder="输入你的问题... (Ctrl+Enter 发送)"
            autoSize={{ minRows: 1, maxRows: 4 }}
            onPressEnter={(e) => {
              if (e.ctrlKey) {
                handleSend();
              }
            }}
            disabled={loading}
            style={{ flex: 1 }}
          />
          {loading ? (
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
      </div>
    </div>
  );
};

export default ChatBox;