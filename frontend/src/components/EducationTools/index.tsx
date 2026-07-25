import React, { useState } from 'react';
import { 
  Card, 
  Tabs, 
  Input, 
  Button, 
  Typography, 
  List, 
  Tag, 
  Space,
  message 
} from 'antd';
import { 
  BookOutlined, 
  EditOutlined, 
  BulbOutlined,
  SendOutlined 
} from '@ant-design/icons';
import { 
  generateSummary, 
  generateExplanation, 
  generateAdvice 
} from '../../api/education';
import './index.css';

const { TextArea } = Input;
const { Title, Paragraph, Text } = Typography;

interface KeyPoint {
  id: number;
  title: string;
  description: string;
}

const EducationTools: React.FC = () => {
  const [activeTab, setActiveTab] = useState('summary');
  const [summaryText, setSummaryText] = useState('');
  const [explainQuestion, setExplainQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [summaryResult, setSummaryResult] = useState<{
    key_points: KeyPoint[];
    summary: string;
    model: string;
  } | null>(null);
  const [explainResult, setExplainResult] = useState<{
    question: string;
    explanation: string;
    model: string;
  } | null>(null);
  const [adviceResult, setAdviceResult] = useState<{
    advice: string;
    model: string;
  } | null>(null);

  // 生成知识点总结
  const handleSummary = async () => {
    if (!summaryText.trim()) {
      message.warning('请输入文本内容');
      return;
    }
    
    setLoading(true);
    try {
      const result = await generateSummary({ text: summaryText });
      if (result.success) {
        setSummaryResult(result.data);
        message.success('知识点总结生成成功');
      } else {
        message.error(result.message || '生成失败');
      }
    } catch (error) {
      console.error('生成知识点总结失败:', error);
      message.error('生成知识点总结失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  // 生成题目解析
  const handleExplain = async () => {
    if (!explainQuestion.trim()) {
      message.warning('请输入题目内容');
      return;
    }
    
    setLoading(true);
    try {
      const result = await generateExplanation({ question: explainQuestion });
      if (result.success) {
        setExplainResult(result.data);
        message.success('题目解析生成成功');
      } else {
        message.error(result.message || '生成失败');
      }
    } catch (error) {
      console.error('生成题目解析失败:', error);
      message.error('生成题目解析失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  // 生成学习建议
  const handleAdvice = async () => {
    setLoading(true);
    try {
      const result = await generateAdvice();
      if (result.success) {
        setAdviceResult(result.data);
        message.success('学习建议生成成功');
      } else {
        message.error(result.message || '生成失败');
      }
    } catch (error) {
      console.error('生成学习建议失败:', error);
      message.error('生成学习建议失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  // 渲染知识点总结
  const renderSummary = () => (
    <div className="education-section">
      <div className="input-section">
        <TextArea
          value={summaryText}
          onChange={(e) => setSummaryText(e.target.value)}
          placeholder="请输入需要总结的文本内容..."
          rows={6}
          className="education-textarea"
        />
        <Button 
          type="primary" 
          icon={<SendOutlined />}
          onClick={handleSummary}
          loading={loading}
          className="education-button"
        >
          生成知识点总结
        </Button>
      </div>
      
      {summaryResult && (
        <div className="result-section">
          <Title level={4}>知识点总结</Title>
          <div className="key-points-list">
            <List
              dataSource={summaryResult.key_points}
              renderItem={(point) => (
                <List.Item>
                  <List.Item.Meta
                    title={
                      <Space>
                        <Tag color="blue">{point.id}</Tag>
                        <Text strong>{point.title}</Text>
                      </Space>
                    }
                    description={point.description}
                  />
                </List.Item>
              )}
            />
          </div>
          <div className="summary-text">
            <Title level={5}>整体总结</Title>
            <Paragraph>{summaryResult.summary}</Paragraph>
          </div>
          <div className="model-info">
            <Text type="secondary">模型: {summaryResult.model}</Text>
          </div>
        </div>
      )}
    </div>
  );

  // 渲染题目解析
  const renderExplain = () => (
    <div className="education-section">
      <div className="input-section">
        <TextArea
          value={explainQuestion}
          onChange={(e) => setExplainQuestion(e.target.value)}
          placeholder="请输入题目内容..."
          rows={4}
          className="education-textarea"
        />
        <Button 
          type="primary" 
          icon={<SendOutlined />}
          onClick={handleExplain}
          loading={loading}
          className="education-button"
        >
          生成题目解析
        </Button>
      </div>
      
      {explainResult && (
        <div className="result-section">
          <Title level={4}>题目解析</Title>
          <div className="question-content">
            <Title level={5}>题目</Title>
            <Paragraph>{explainResult.question}</Paragraph>
          </div>
          <div className="explanation-content">
            <Title level={5}>解析</Title>
            <Paragraph className="explanation-text">
              {explainResult.explanation}
            </Paragraph>
          </div>
          <div className="model-info">
            <Text type="secondary">模型: {explainResult.model}</Text>
          </div>
        </div>
      )}
    </div>
  );

  // 渲染学习建议
  const renderAdvice = () => (
    <div className="education-section">
      <div className="input-section">
        <Button 
          type="primary" 
          icon={<BulbOutlined />}
          onClick={handleAdvice}
          loading={loading}
          className="education-button"
        >
          生成学习建议
        </Button>
        <Text type="secondary" className="advice-hint">
          根据您的学习历史生成个性化建议
        </Text>
      </div>
      
      {adviceResult && (
        <div className="result-section">
          <Title level={4}>学习建议</Title>
          <div className="advice-content">
            <Paragraph className="advice-text">
              {adviceResult.advice}
            </Paragraph>
          </div>
          <div className="model-info">
            <Text type="secondary">模型: {adviceResult.model}</Text>
          </div>
        </div>
      )}
    </div>
  );

  // 标签页配置
  const tabItems = [
    {
      key: 'summary',
      label: (
        <span>
          <BookOutlined />
          知识点总结
        </span>
      ),
      children: renderSummary(),
    },
    {
      key: 'explain',
      label: (
        <span>
          <EditOutlined />
          题目解析
        </span>
      ),
      children: renderExplain(),
    },
    {
      key: 'advice',
      label: (
        <span>
          <BulbOutlined />
          学习建议
        </span>
      ),
      children: renderAdvice(),
    },
  ];

  return (
    <Card 
      title="AI教学辅助工具" 
      className="education-tools-card"
      extra={<Text type="secondary">智能教育助手</Text>}
    >
      <Tabs 
        activeKey={activeTab} 
        onChange={setActiveTab}
        items={tabItems}
        className="education-tabs"
      />
    </Card>
  );
};

export default EducationTools;