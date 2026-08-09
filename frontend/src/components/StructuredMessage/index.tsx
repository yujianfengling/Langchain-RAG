import React from 'react';
import {
  Typography, Space, Tag, Divider, List, Empty,
} from 'antd';
import {
  BookOutlined, EditOutlined, WarningOutlined, BulbOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons';
import { QuizList } from '../SmartNoteTool';

const { Title, Paragraph, Text } = Typography;

// 智能笔记分节渲染（五段式）
const NoteSection: React.FC<{
  icon: React.ReactNode;
  title: string;
  content: string;
}> = ({ icon, title, content }) => (
  <div style={{ marginBottom: 12 }}>
    <Title level={5} style={{ marginBottom: 4 }}>
      <Space size={6}>
        {icon}
        {title}
      </Space>
    </Title>
    <Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{content || '—'}</Paragraph>
  </div>
);

// 智能笔记 Card
const SmartNoteCard: React.FC<{ payload: any }> = ({ payload }) => {
  if (!payload) return <Empty description="暂无内容" />;
  return (
    <div style={{ width: '100%' }}>
      <Title level={5} style={{ marginTop: 0, marginBottom: 12 }}>{payload.title}</Title>
      <NoteSection icon={<BookOutlined style={{ color: '#0f766e' }} />} title="核心概念" content={payload.concept?.content} />
      <NoteSection icon={<EditOutlined style={{ color: '#059669' }} />} title="典型例题" content={payload.example?.content} />
      <NoteSection icon={<WarningOutlined style={{ color: '#faad14' }} />} title="易错点" content={payload.mistakes?.content} />
      <NoteSection icon={<BulbOutlined style={{ color: '#722ed1' }} />} title="课堂提示" content={payload.class_tips?.content} />
      <Divider style={{ margin: '12px 0' }} />
      <Title level={5}>
        <Space size={6}>
          <QuestionCircleOutlined />
          自测题
        </Space>
      </Title>
      <QuizList quizzes={payload.quiz || []} />
    </div>
  );
};

// 自测题 Card
const QuizCard: React.FC<{ payload: any }> = ({ payload }) => {
  const quizzes = payload?.quiz || [];
  return (
    <div style={{ width: '100%' }}>
      <Title level={5} style={{ marginTop: 0 }}>自测题</Title>
      <QuizList quizzes={quizzes} />
    </div>
  );
};

// 知识点总结 Card
const SummaryCard: React.FC<{ payload: any }> = ({ payload }) => {
  const keyPoints = payload?.key_points || [];
  return (
    <div style={{ width: '100%' }}>
      <Title level={5} style={{ marginTop: 0 }}>知识点总结</Title>
      {keyPoints.length > 0 ? (
        <List
          size="small"
          dataSource={keyPoints}
          renderItem={(p: any, idx) => (
            <List.Item>
              <Space align="start" size={8}>
                <Tag color="cyan">{idx + 1}</Tag>
                <div>
                  <Text strong>{p.title}</Text>
                  <div><Text type="secondary" style={{ fontSize: 13 }}>{p.description}</Text></div>
                </div>
              </Space>
            </List.Item>
          )}
        />
      ) : null}
      {payload?.summary && (
        <Paragraph style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>
          <Text strong>整体总结：</Text>
          <br />
          {payload.summary}
        </Paragraph>
      )}
    </div>
  );
};

// 题目解析 Card
const ExplanationCard: React.FC<{ payload: any }> = ({ payload }) => (
  <div style={{ width: '100%' }}>
    <Title level={5} style={{ marginTop: 0 }}>题目解析</Title>
    <Paragraph style={{ whiteSpace: 'pre-wrap' }}>
      <Text strong>题目：</Text>
      <br />
      {payload?.question}
    </Paragraph>
    <Divider style={{ margin: '8px 0' }} />
    <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{payload?.explanation}</Paragraph>
  </div>
);

// 学习建议 Card
const AdviceCard: React.FC<{ payload: any }> = ({ payload }) => (
  <div style={{ width: '100%' }}>
    <Title level={5} style={{ marginTop: 0 }}>学习建议</Title>
    <Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{payload?.advice}</Paragraph>
  </div>
);

/**
 * 结构化消息渲染（V2.5.1）
 *
 * 根据 message_type 渲染对应 Card；历史对话重新打开后，
 * 从 payload_json 恢复结构化 Card，而不是降级为纯文本。
 */
const StructuredMessage: React.FC<{ messageType: string; payload: any }> = ({
  messageType,
  payload,
}) => {
  if (!payload) return null;

  switch (messageType) {
    case 'smart_note':
      return <SmartNoteCard payload={payload} />;
    case 'quiz':
      return <QuizCard payload={payload} />;
    case 'summary':
      return <SummaryCard payload={payload} />;
    case 'explanation':
      return <ExplanationCard payload={payload} />;
    case 'advice':
      return <AdviceCard payload={payload} />;
    default:
      return null;
  }
};

export default StructuredMessage;
