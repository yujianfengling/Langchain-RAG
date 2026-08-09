import React, { useState } from 'react';
import {
  Input, Button, Typography, Space, Radio, List, Tag, Divider,
  message, Empty, Alert,
} from 'antd';
import {
  SendOutlined, BookOutlined, EditOutlined, WarningOutlined,
  BulbOutlined, QuestionCircleOutlined, EyeOutlined,
} from '@ant-design/icons';
import SubjectSelector from '../SubjectSelector';
import { generateSmartNote, SmartNote, QuizItem } from '../../api/notes';

const { TextArea } = Input;
const { Title, Paragraph, Text } = Typography;

// ===== 自测题组件（Task C 可复用）=====
export const QuizList: React.FC<{ quizzes: QuizItem[] }> = ({ quizzes }) => {
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Record<string, string>>({});

  if (!quizzes.length) return <Empty description="暂无自测题" />;

  return (
    <List
      dataSource={quizzes}
      renderItem={(q, idx) => {
        const isRevealed = !!revealed[q.id];
        return (
          <List.Item>
            <div style={{ width: '100%' }}>
              <div style={{ marginBottom: 8 }}>
                <Tag color={q.type === 'single_choice' ? 'cyan' : q.type === 'judgement' ? 'green' : 'orange'}>
                  {q.type === 'single_choice' ? '单选题' : q.type === 'judgement' ? '判断题' : '简答题'}
                </Tag>
                <Text strong>
                  {idx + 1}. {q.question}
                </Text>
              </div>

              {/* 选项选择区（单选/判断） */}
              {(q.type === 'single_choice' || q.type === 'judgement') && (
                <Radio.Group
                  onChange={(e) => setSelected((prev) => ({ ...prev, [q.id]: e.target.value }))}
                  value={selected[q.id]}
                  style={{ marginBottom: 8 }}
                >
                  <Space direction="vertical">
                    {q.options.map((opt) => (
                      <Radio key={opt} value={opt}>{opt}</Radio>
                    ))}
                  </Space>
                </Radio.Group>
              )}

              {/* 查看答案 */}
              {!isRevealed ? (
                <Button
                  size="small"
                  icon={<EyeOutlined />}
                  onClick={() => setRevealed((prev) => ({ ...prev, [q.id]: true }))}
                >
                  查看答案
                </Button>
              ) : (
                <Alert
                  type="info"
                  showIcon
                  message={
                    <Space direction="vertical" size={4}>
                      <Text strong>答案：{q.answer}</Text>
                      {q.explanation && <Text type="secondary">解析：{q.explanation}</Text>}
                      {q.knowledge_point && (
                        <Tag color="purple">知识点：{q.knowledge_point}</Tag>
                      )}
                    </Space>
                  }
                />
              )}
            </div>
          </List.Item>
        );
      }}
    />
  );
};

// ===== 五段式智能笔记工具 =====
const SmartNoteTool: React.FC = () => {
  const [subjectId, setSubjectId] = useState<string>('');
  const [topic, setTopic] = useState('');
  const [userInstruction, setUserInstruction] = useState('');
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState<SmartNote | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleGenerate = async () => {
    if (!subjectId) { message.warning('请选择学科'); return; }
    if (!topic.trim()) { message.warning('请输入知识点主题'); return; }

    setLoading(true);
    setError(null);
    try {
      const res = await generateSmartNote({
        subject_id: subjectId,
        topic: topic.trim(),
        user_instruction: userInstruction.trim() || undefined,
      });
      if (res.success) {
        setNote(res.data);
        if (!res.data.context_used) {
          message.warning('当前知识库中没有足够的证据，笔记内容可能不完整');
        } else {
          message.success('智能笔记生成成功');
        }
      } else {
        setError(res.message || '生成失败');
        message.error(res.message || '生成失败');
      }
    } catch (e: any) {
      const errType = e?.response?.data?.error_type;
      const rawMsg = e?.response?.data?.message;
      let msg = rawMsg || '智能笔记生成失败，请稍后重试';
      // 按 error_type 映射用户可理解提示
      if (errType === 'knowledge_base_not_ready') {
        msg = '请先构建该课程的知识库';
      } else if (errType === 'subject_not_found') {
        msg = '课程不存在，请先创建课程';
      } else if (errType === 'model_api_error') {
        msg = 'AI生成服务暂时不可用，请稍后重试';
      }
      setError(msg);
      message.error(msg);
    } finally {
      setLoading(false);
    }
  };

  const renderSection = (
    icon: React.ReactNode,
    title: string,
    content: string,
    sources?: SmartNote['concept']['sources']
  ) => (
    <div style={{ marginBottom: 16 }}>
      <Title level={5}>
        <Space>
          {icon}
          {title}
        </Space>
      </Title>
      <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{content}</Paragraph>
      {sources && sources.length > 0 && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          来源: {sources.map((s, i) => `[${i + 1}] ${s.file_name}${s.page != null ? ` 第${s.page}页` : ''}`).join('；')}
        </Text>
      )}
    </div>
  );

  return (
    <div className="education-section">
      <div className="input-section">
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <SubjectSelector value={subjectId} onChange={setSubjectId} />
          <TextArea
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="输入知识点主题，如：二叉树、栈与队列、牛顿第二定律..."
            rows={2}
            className="education-textarea"
          />
          <TextArea
            value={userInstruction}
            onChange={(e) => setUserInstruction(e.target.value)}
            placeholder="附加要求（可选），如：重点讲清楚易错点、用生活中的例子..."
            rows={2}
            className="education-textarea"
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={handleGenerate}
            loading={loading}
            className="education-button"
          >
            生成智能笔记
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            基于知识库证据生成「概念-例题-易错点-课堂提示-自测题」五段式笔记；证据不足时不会编造课堂内容。
          </Text>
        </Space>
      </div>

      {error && (
        <Alert type="error" showIcon message={error} style={{ marginTop: 16 }} />
      )}

      {note && (
        <div className="result-section" style={{ marginTop: 16 }}>
          <Divider />
          <Title level={4}>{note.title}</Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            证据来源数: {note.evidence_count} | 模型: {note.model || '默认'}
          </Text>

          <div style={{ marginTop: 16 }}>
            {renderSection(<BookOutlined style={{ color: '#0f766e' }} />, '核心概念', note.concept.content, note.concept.sources)}
            {renderSection(<EditOutlined style={{ color: '#059669' }} />, '典型例题', note.example.content, note.example.sources)}
            {renderSection(<WarningOutlined style={{ color: '#faad14' }} />, '易错点', note.mistakes.content, note.mistakes.sources)}
            {renderSection(<BulbOutlined style={{ color: '#722ed1' }} />, '课堂提示', note.class_tips.content, note.class_tips.sources)}
          </div>

          <Divider />
          <Title level={5}>
            <Space>
              <QuestionCircleOutlined />
              自测题
            </Space>
          </Title>
          <QuizList quizzes={note.quiz} />
        </div>
      )}
    </div>
  );
};

export default SmartNoteTool;
