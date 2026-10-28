import React, { useState, useEffect, useCallback } from 'react';
import { Card, Row, Col, Statistic, Progress, Typography, Spin, Tag, Space, Empty } from 'antd';
import {
  BookOutlined,
  StarOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
  TrophyOutlined,

  RiseOutlined,
} from '@ant-design/icons';
import { getProgressSummary, getLearningStats, ProgressSummary, LearningStats } from '../../api/agent';

const { Text } = Typography;

interface UserProfileDashboardProps {
  userId?: string;
  subjectId?: string;
}

const UserProfileDashboard: React.FC<UserProfileDashboardProps> = ({
  subjectId,
}) => {
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState<ProgressSummary | null>(null);
  const [stats, setStats] = useState<LearningStats | null>(null);

  const loadData = useCallback(async () => {
    if (!subjectId) return;
    setLoading(true);
    try {
      const [summaryRes, statsRes] = await Promise.all([
        getProgressSummary(subjectId).catch(() => ({ data: null })),
        getLearningStats(subjectId).catch(() => ({ data: null })),
      ]);
      setSummary(summaryRes?.data || null);
      setStats(statsRes?.data || null);
    } catch (error) {
      console.error('加载用户数据失败:', error);
    } finally {
      setLoading(false);
    }
  }, [subjectId]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  if (!subjectId) {
    return (
      <Card>
        <Empty description="请先选择学科" />
      </Card>
    );
  }

  const getMasteryColor = (level: number) => {
    if (level >= 0.7) return '#52c41a';
    if (level >= 0.4) return '#faad14';
    return '#ff4d4f';
  };

  const getAchievements = () => {
    const achievements = [];
    if (summary && summary.total_interactions >= 10) {
      achievements.push({ icon: '🎯', title: '初学者', desc: '完成10次交互' });
    }
    if (summary && summary.total_interactions >= 50) {
      achievements.push({ icon: '📚', title: '勤奋学习者', desc: '完成50次交互' });
    }
    if (stats && stats.mastery_distribution.strong >= 5) {
      achievements.push({ icon: '🌟', title: '知识达人', desc: '掌握5个知识点' });
    }
    if (summary && summary.positive_rate >= 0.8) {
      achievements.push({ icon: '👍', title: '高质量学习', desc: '好评率超过80%' });
    }
    return achievements;
  };

  const achievements = getAchievements();

  return (
    <Card
      title={
        <Space>
          <TrophyOutlined />
          <span>学习仪表盘</span>
        </Space>
      }
      style={{ marginBottom: 16 }}
    >
      <Spin spinning={loading}>
        {/* 核心统计 */}
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          <Col xs={12} sm={6}>
            <Statistic
              title="总交互"
              value={summary?.total_interactions || 0}
              prefix={<BookOutlined />}
              valueStyle={{ color: '#1890ff' }}
            />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic
              title="知识点"
              value={stats?.total_knowledge_points || 0}
              prefix={<StarOutlined />}
              valueStyle={{ color: '#722ed1' }}
            />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic
              title="学习时间"
              value={stats?.total_study_time || 0}
              suffix="分钟"
              prefix={<ClockCircleOutlined />}
              valueStyle={{ color: '#13c2c2' }}
            />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic
              title="好评率"
              value={Math.round((summary?.positive_rate || 0) * 100)}
              suffix="%"
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: (summary?.positive_rate || 0) >= 0.7 ? '#52c41a' : '#faad14' }}
            />
          </Col>
        </Row>

        {/* 知识掌握分布 */}
        {stats && (
          <div style={{ marginBottom: 24 }}>
            <Text strong style={{ marginBottom: 12, display: 'block' }}>知识掌握分布</Text>
            <Row gutter={16}>
              <Col span={8}>
                <div style={{ textAlign: 'center' }}>
                  <Progress
                    type="circle"
                    percent={stats.mastery_distribution.strong}
                    size={80}
                    strokeColor="#52c41a"
                    format={() => stats.mastery_distribution.strong}
                  />
                  <div style={{ marginTop: 8 }}>
                    <Tag color="success">强项</Tag>
                  </div>
                </div>
              </Col>
              <Col span={8}>
                <div style={{ textAlign: 'center' }}>
                  <Progress
                    type="circle"
                    percent={stats.mastery_distribution.medium}
                    size={80}
                    strokeColor="#faad14"
                    format={() => stats.mastery_distribution.medium}
                  />
                  <div style={{ marginTop: 8 }}>
                    <Tag color="warning">中等</Tag>
                  </div>
                </div>
              </Col>
              <Col span={8}>
                <div style={{ textAlign: 'center' }}>
                  <Progress
                    type="circle"
                    percent={stats.mastery_distribution.weak}
                    size={80}
                    strokeColor="#ff4d4f"
                    format={() => stats.mastery_distribution.weak}
                  />
                  <div style={{ marginTop: 8 }}>
                    <Tag color="error">薄弱</Tag>
                  </div>
                </div>
              </Col>
            </Row>
          </div>
        )}

        {/* 平均掌握度 */}
        {stats && (
          <div style={{ marginBottom: 24 }}>
            <Text strong style={{ marginBottom: 8, display: 'block' }}>平均掌握度</Text>
            <Progress
              percent={Math.round(stats.average_mastery * 100)}
              strokeColor={getMasteryColor(stats.average_mastery)}
              status="active"
            />
          </div>
        )}

        {/* 学习效率 */}
        {stats && (
          <div style={{ marginBottom: 24 }}>
            <Text strong style={{ marginBottom: 8, display: 'block' }}>
              <RiseOutlined /> 学习效率
            </Text>
            <div style={{
              padding: '12px',
              background: '#f6ffed',
              borderRadius: '8px',
              border: '1px solid #b7eb8f',
            }}>
              <Text>
                每个知识点平均掌握度: <Text strong>{Math.round(stats.learning_efficiency)}%</Text>
              </Text>
            </div>
          </div>
        )}

        {/* 成就徽章 */}
        {achievements.length > 0 && (
          <div>
            <Text strong style={{ marginBottom: 12, display: 'block' }}>
              <TrophyOutlined /> 成就徽章
            </Text>
            <Space wrap>
              {achievements.map((achievement, index) => (
                <Tag
                  key={index}
                  icon={<span>{achievement.icon}</span>}
                  color="gold"
                  style={{ padding: '4px 8px', fontSize: 12 }}
                >
                  {achievement.title}
                </Tag>
              ))}
            </Space>
          </div>
        )}

        {/* 空状态 */}
        {!loading && !summary && !stats && (
          <Empty description="暂无学习数据，开始学习后将自动记录" />
        )}
      </Spin>
    </Card>
  );
};

export default UserProfileDashboard;
