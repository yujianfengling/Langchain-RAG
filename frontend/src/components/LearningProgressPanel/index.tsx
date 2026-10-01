import React, { useState, useEffect } from 'react';
import { Card, Progress, List, Tag, Space, Typography, Spin, Empty, Button, Statistic, Row, Col } from 'antd';
import {
  TrophyOutlined,
  ExclamationCircleOutlined,
  StarOutlined,
  ReloadOutlined,
  RocketOutlined,
  BookOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import { getWeakPoints, getStrongPoints, getRecommendations, getProgressSummary } from '../../api/agent';

const { Text } = Typography;

interface LearningProgressPanelProps {
  userId?: string;
  subjectId?: string;
  currentTopic?: string;
  compact?: boolean;
}

interface KnowledgePoint {
  knowledge_point: string;
  mastery_level: number;
  test_count: number;
  correct_count?: number;
}

interface Recommendation {
  type: string;
  title: string;
  description: string;
  priority: string;
  knowledge_points?: string[];
}

interface ProgressSummary {
  total_interactions: number;
  knowledge_points_count: number;
  mastery_distribution: {
    strong: number;
    medium: number;
    weak: number;
  };
  recent_activity: any[];
  suggestions: string[];
  positive_rate: number;
}

const LearningProgressPanel: React.FC<LearningProgressPanelProps> = ({
  subjectId,
  currentTopic,
  compact = false,
}) => {
  const [loading, setLoading] = useState(false);
  const [weakPoints, setWeakPoints] = useState<KnowledgePoint[]>([]);
  const [strongPoints, setStrongPoints] = useState<KnowledgePoint[]>([]);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [summary, setSummary] = useState<ProgressSummary | null>(null);

  const loadData = async () => {
    if (!subjectId) return;
    setLoading(true);
    try {
      const [weakRes, strongRes, recRes, summaryRes] = await Promise.all([
        getWeakPoints(subjectId).catch(() => ({ data: [] })),
        getStrongPoints(subjectId).catch(() => ({ data: [] })),
        getRecommendations(subjectId, currentTopic).catch(() => ({ data: { recommendations: [] } })),
        getProgressSummary(subjectId).catch(() => ({ data: null })),
      ]);
      setWeakPoints(weakRes?.data || []);
      setStrongPoints(strongRes?.data || []);
      setRecommendations(recRes?.data?.recommendations || []);
      setSummary(summaryRes?.data || null);
    } catch (error) {
      console.error('加载学习进度失败:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [subjectId, currentTopic]);

  const getMasteryColor = (level: number) => {
    if (level >= 0.7) return '#52c41a';
    if (level >= 0.4) return '#faad14';
    return '#ff4d4f';
  };

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'high': return 'red';
      case 'medium': return 'orange';
      case 'low': return 'blue';
      default: return 'default';
    }
  };

  if (!subjectId) {
    return (
      <Card size="small" title="学习进度" style={{ marginBottom: 12 }}>
        <Empty description="请先选择学科" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </Card>
    );
  }

  // 紧凑模式：只显示摘要统计
  if (compact) {
    return (
      <Card size="small" style={{ marginBottom: 12 }}>
        <Spin spinning={loading}>
          {summary && (
            <Row gutter={16}>
              <Col span={8}>
                <Statistic
                  title="交互次数"
                  value={summary.total_interactions}
                  prefix={<BookOutlined />}
                  valueStyle={{ fontSize: 16 }}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="知识点"
                  value={summary.knowledge_points_count}
                  prefix={<StarOutlined />}
                  valueStyle={{ fontSize: 16 }}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="好评率"
                  value={Math.round(summary.positive_rate * 100)}
                  suffix="%"
                  prefix={<CheckCircleOutlined />}
                  valueStyle={{ fontSize: 16, color: summary.positive_rate > 0.7 ? '#52c41a' : '#faad14' }}
                />
              </Col>
            </Row>
          )}
          {!summary && !loading && <Text type="secondary">暂无数据</Text>}
        </Spin>
      </Card>
    );
  }

  return (
    <Card
      size="small"
      title={
        <Space>
          <RocketOutlined />
          <span>学习进度</span>
        </Space>
      }
      extra={
        <Button type="text" size="small" icon={<ReloadOutlined />} onClick={loadData} loading={loading} />
      }
      style={{ marginBottom: 12 }}
    >
      <Spin spinning={loading}>
        {/* 摘要统计 */}
        {summary && (
          <div style={{ marginBottom: 16, padding: '12px', background: '#f5f5f5', borderRadius: '8px' }}>
            <Row gutter={16}>
              <Col span={8}>
                <Statistic
                  title="交互次数"
                  value={summary.total_interactions}
                  prefix={<BookOutlined />}
                  valueStyle={{ fontSize: 16 }}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="知识点"
                  value={summary.knowledge_points_count}
                  prefix={<StarOutlined />}
                  valueStyle={{ fontSize: 16 }}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="好评率"
                  value={Math.round(summary.positive_rate * 100)}
                  suffix="%"
                  prefix={<CheckCircleOutlined />}
                  valueStyle={{ fontSize: 16, color: summary.positive_rate > 0.7 ? '#52c41a' : '#faad14' }}
                />
              </Col>
            </Row>
            
            {/* 掌握度分布 */}
            {summary.mastery_distribution && (
              <div style={{ marginTop: 12 }}>
                <Text type="secondary" style={{ fontSize: 11 }}>掌握度分布</Text>
                <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                  <Tag color="success">强项 {summary.mastery_distribution.strong}</Tag>
                  <Tag color="warning">中等 {summary.mastery_distribution.medium}</Tag>
                  <Tag color="error">薄弱 {summary.mastery_distribution.weak}</Tag>
                </div>
              </div>
            )}
          </div>
        )}

        {/* 强项知识点 */}
        {strongPoints.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <Text strong style={{ fontSize: 13, color: '#52c41a' }}>
              <StarOutlined /> 掌握较好的知识点
            </Text>
            <div style={{ marginTop: 8 }}>
              {strongPoints.slice(0, 3).map((point, idx) => (
                <Tag key={idx} color="success" style={{ marginBottom: 4 }}>
                  {point.knowledge_point} ({Math.round(point.mastery_level * 100)}%)
                </Tag>
              ))}
            </div>
          </div>
        )}

        {/* 薄弱知识点 */}
        {weakPoints.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <Text strong style={{ fontSize: 13, color: '#ff4d4f' }}>
              <ExclamationCircleOutlined /> 需要加强的知识点
            </Text>
            <div style={{ marginTop: 8 }}>
              {weakPoints.slice(0, 5).map((point, idx) => (
                <div key={idx} style={{ marginBottom: 8 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <Text style={{ fontSize: 12 }}>{point.knowledge_point}</Text>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {Math.round(point.mastery_level * 100)}%
                    </Text>
                  </div>
                  <Progress
                    percent={Math.round(point.mastery_level * 100)}
                    size="small"
                    strokeColor={getMasteryColor(point.mastery_level)}
                    showInfo={false}
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 学习建议 */}
        {recommendations.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <Text strong style={{ fontSize: 13 }}>
              <TrophyOutlined /> 学习建议
            </Text>
            <List
              size="small"
              dataSource={recommendations.slice(0, 3)}
              renderItem={(item) => (
                <List.Item style={{ padding: '8px 0', border: 'none' }}>
                  <div style={{ width: '100%' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Text style={{ fontSize: 12, fontWeight: 500 }}>{item.title}</Text>
                      <Tag color={getPriorityColor(item.priority)} style={{ fontSize: 10, margin: 0 }}>
                        {item.priority === 'high' ? '重要' : item.priority === 'medium' ? '建议' : '可选'}
                      </Tag>
                    </div>
                    <Text type="secondary" style={{ fontSize: 11 }}>{item.description}</Text>
                  </div>
                </List.Item>
              )}
            />
          </div>
        )}

        {/* 系统建议 */}
        {summary?.suggestions && summary.suggestions.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            <Text type="secondary" style={{ fontSize: 11 }}>
              <ClockCircleOutlined /> {summary.suggestions[0]}
            </Text>
          </div>
        )}

        {/* 空状态 */}
        {!loading && weakPoints.length === 0 && strongPoints.length === 0 && !summary && (
          <Empty
            description="暂无学习数据，开始问答后将自动记录"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            style={{ padding: '16px 0' }}
          />
        )}
      </Spin>
    </Card>
  );
};

export default LearningProgressPanel;
