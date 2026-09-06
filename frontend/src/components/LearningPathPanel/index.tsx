import React, { useState, useEffect } from 'react';
import { Card, Steps, Tag, Space, Typography, Spin, Empty, Button, Progress } from 'antd';
import {
  RocketOutlined,
  ReloadOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
  LoadingOutlined,
  ExclamationCircleOutlined,
  BookOutlined,
  EditOutlined,
  TrophyOutlined,
} from '@ant-design/icons';
import { getLearningPath, LearningPathItem } from '../../api/agent';

const { Text } = Typography;

interface LearningPathPanelProps {
  userId?: string;
  subjectId?: string;
  onTopicClick?: (topic: string) => void;
}

const LearningPathPanel: React.FC<LearningPathPanelProps> = ({
  userId = 'default_user',
  subjectId,
  onTopicClick,
}) => {
  const [loading, setLoading] = useState(false);
  const [learningPath, setLearningPath] = useState<LearningPathItem[]>([]);

  const loadData = async () => {
    if (!subjectId) return;
    setLoading(true);
    try {
      const res = await getLearningPath(userId, subjectId);
      setLearningPath(res?.data || []);
    } catch (error) {
      console.error('加载学习路径失败:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [userId, subjectId]);

  const getStepIcon = (item: LearningPathItem) => {
    switch (item.status) {
      case 'completed':
        return <CheckCircleOutlined style={{ color: '#52c41a' }} />;
      case 'in_progress':
        return <LoadingOutlined style={{ color: '#1890ff' }} />;
      default:
        if (item.priority === 'high') {
          return <ExclamationCircleOutlined style={{ color: '#ff4d4f' }} />;
        }
        return <ClockCircleOutlined style={{ color: '#999' }} />;
    }
  };

  const getStepStatus = (item: LearningPathItem): 'wait' | 'process' | 'finish' | 'error' => {
    switch (item.status) {
      case 'completed':
        return 'finish';
      case 'in_progress':
        return 'process';
      default:
        if (item.priority === 'high') {
          return 'error';
        }
        return 'wait';
    }
  };

  const getActivityIcon = (activity: string) => {
    if (activity.includes('笔记') || activity.includes('阅读')) {
      return <BookOutlined />;
    }
    if (activity.includes('练习') || activity.includes('自测')) {
      return <EditOutlined />;
    }
    if (activity.includes('巩固') || activity.includes('高级')) {
      return <TrophyOutlined />;
    }
    return <RocketOutlined />;
  };

  const getPriorityTag = (priority: string) => {
    switch (priority) {
      case 'high':
        return <Tag color="error" style={{ fontSize: 10, margin: 0 }}>重要</Tag>;
      case 'medium':
        return <Tag color="warning" style={{ fontSize: 10, margin: 0 }}>建议</Tag>;
      case 'low':
        return <Tag color="default" style={{ fontSize: 10, margin: 0 }}>可选</Tag>;
      default:
        return null;
    }
  };

  if (!subjectId) {
    return (
      <Card size="small" title="学习路径" style={{ marginBottom: 12 }}>
        <Empty description="请先选择学科" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </Card>
    );
  }

  return (
    <Card
      size="small"
      title={
        <Space>
          <RocketOutlined />
          <span>学习路径</span>
        </Space>
      }
      extra={
        <Button type="text" size="small" icon={<ReloadOutlined />} onClick={loadData} loading={loading} />
      }
      style={{ marginBottom: 12 }}
    >
      <Spin spinning={loading}>
        {learningPath.length > 0 ? (
          <div style={{ padding: '0 8px' }}>
            {/* 学习路径步骤 */}
            <Steps
              direction="vertical"
              size="small"
              items={learningPath.map((item) => ({
                title: (
                  <div
                    style={{ cursor: onTopicClick ? 'pointer' : 'default' }}
                    onClick={() => onTopicClick?.(item.knowledge_point)}
                  >
                    <Space>
                      <Text strong style={{ fontSize: 13 }}>{item.knowledge_point}</Text>
                      {getPriorityTag(item.priority)}
                    </Space>
                  </div>
                ),
                description: (
                  <div style={{ marginTop: 4 }}>
                    {/* 掌握度进度 */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        掌握度: {Math.round(item.current_mastery * 100)}% → {Math.round(item.target_mastery * 100)}%
                      </Text>
                      <Progress
                        percent={Math.round(item.current_mastery * 100)}
                        size="small"
                        showInfo={false}
                        style={{ width: 60, margin: 0 }}
                        strokeColor={item.current_mastery >= 0.7 ? '#52c41a' : item.current_mastery >= 0.4 ? '#faad14' : '#ff4d4f'}
                      />
                    </div>
                    
                    {/* 建议活动 */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      {getActivityIcon(item.suggested_activity)}
                      <Text type="secondary" style={{ fontSize: 11 }}>{item.suggested_activity}</Text>
                    </div>
                    
                    {/* 预计时间 */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                      <ClockCircleOutlined style={{ fontSize: 11, color: '#999' }} />
                      <Text type="secondary" style={{ fontSize: 11 }}>预计 {item.estimated_minutes} 分钟</Text>
                    </div>
                  </div>
                ),
                icon: getStepIcon(item),
                status: getStepStatus(item),
              }))}
            />

            {/* 总计时间 */}
            <div style={{ marginTop: 16, padding: '8px 12px', background: '#f5f5f5', borderRadius: '6px' }}>
              <Space>
                <ClockCircleOutlined />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  总计预计学习时间: {learningPath.reduce((sum, item) => sum + item.estimated_minutes, 0)} 分钟
                </Text>
              </Space>
            </div>
          </div>
        ) : (
          <Empty
            description="暂无学习路径，开始学习后将自动生成"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            style={{ padding: '16px 0' }}
          />
        )}
      </Spin>
    </Card>
  );
};

export default LearningPathPanel;
