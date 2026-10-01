import React, { useState, useEffect } from 'react';
import { Card, List, Tag, Space, Typography, Spin, Empty, Button, Statistic, Row, Col, message, Tooltip } from 'antd';
import {
  ExperimentOutlined,
  ReloadOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  PlusCircleOutlined,
  DatabaseOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { getPendingKnowledge, getExtractionStats, validateKnowledge, addKnowledgeToKB, PendingKnowledge, ExtractionStats } from '../../api/agent';

const { Text } = Typography;

interface KnowledgeExtractionPanelProps {
  userId?: string;
  subjectId?: string;
  compact?: boolean;
}

const KnowledgeExtractionPanel: React.FC<KnowledgeExtractionPanelProps> = ({
  subjectId,
  compact = false,
}) => {
  const [loading, setLoading] = useState(false);
  const [pendingKnowledge, setPendingKnowledge] = useState<PendingKnowledge[]>([]);
  const [stats, setStats] = useState<ExtractionStats | null>(null);
  const [validating, setValidating] = useState<string | null>(null);
  const [adding, setAdding] = useState<string | null>(null);

  const loadData = async () => {
    setLoading(true);
    try {
      const [pendingRes, statsRes] = await Promise.all([
        getPendingKnowledge(subjectId, 5).catch(() => ({ data: [] })),
        getExtractionStats(subjectId).catch(() => ({ data: null })),
      ]);
      setPendingKnowledge(pendingRes?.data || []);
      setStats(statsRes?.data || null);
    } catch (error) {
      console.error('加载知识提取数据失败:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [subjectId]);

  const handleValidate = async (knowledgeId: string) => {
    setValidating(knowledgeId);
    try {
      const res = await validateKnowledge(knowledgeId);
      if (res?.data?.is_valid) {
        message.success('知识验证通过！');
      } else {
        message.info('知识验证未通过：' + (res?.data?.reason || '质量不达标'));
      }
      loadData();
    } catch (error) {
      message.error('验证失败');
    } finally {
      setValidating(null);
    }
  };

  const handleAddToKB = async (knowledgeId: string) => {
    if (!subjectId) {
      message.warning('请先选择学科');
      return;
    }
    setAdding(knowledgeId);
    try {
      await addKnowledgeToKB(knowledgeId, subjectId);
      message.success('知识已添加到知识库！');
      loadData();
    } catch (error) {
      message.error('添加失败');
    } finally {
      setAdding(null);
    }
  };

  const getStatusTag = (status: string) => {
    switch (status) {
      case 'pending':
        return <Tag icon={<ClockCircleOutlined />} color="warning">待验证</Tag>;
      case 'validated':
        return <Tag icon={<SafetyCertificateOutlined />} color="processing">已验证</Tag>;
      case 'added':
        return <Tag icon={<CheckCircleOutlined />} color="success">已入库</Tag>;
      case 'rejected':
        return <Tag icon={<CloseCircleOutlined />} color="error">已拒绝</Tag>;
      default:
        return <Tag>{status}</Tag>;
    }
  };

  if (!subjectId) {
    return (
      <Card size="small" title="知识扩展" style={{ marginBottom: 12 }}>
        <Empty description="请先选择学科" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </Card>
    );
  }

  // 紧凑模式：只显示统计
  if (compact) {
    return (
      <Card size="small" style={{ marginBottom: 12 }}>
        <Spin spinning={loading}>
          {stats && (
            <Row gutter={16}>
              <Col span={8}>
                <Statistic
                  title="待验证"
                  value={stats.pending}
                  prefix={<ClockCircleOutlined />}
                  valueStyle={{ fontSize: 16, color: '#faad14' }}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="已入库"
                  value={stats.added}
                  prefix={<DatabaseOutlined />}
                  valueStyle={{ fontSize: 16, color: '#52c41a' }}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="总提取"
                  value={stats.total}
                  prefix={<ExperimentOutlined />}
                  valueStyle={{ fontSize: 16 }}
                />
              </Col>
            </Row>
          )}
          {!stats && !loading && <Text type="secondary">暂无数据</Text>}
        </Spin>
      </Card>
    );
  }

  return (
    <Card
      size="small"
      title={
        <Space>
          <ExperimentOutlined />
          <span>知识扩展</span>
        </Space>
      }
      extra={
        <Button type="text" size="small" icon={<ReloadOutlined />} onClick={loadData} loading={loading} />
      }
      style={{ marginBottom: 12 }}
    >
      <Spin spinning={loading}>
        {/* 统计信息 */}
        {stats && (
          <div style={{ marginBottom: 16, padding: '12px', background: '#f5f5f5', borderRadius: '8px' }}>
            <Row gutter={16}>
              <Col span={6}>
                <Statistic
                  title="待验证"
                  value={stats.pending}
                  prefix={<ClockCircleOutlined />}
                  valueStyle={{ fontSize: 14, color: '#faad14' }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="已验证"
                  value={stats.validated}
                  prefix={<SafetyCertificateOutlined />}
                  valueStyle={{ fontSize: 14, color: '#1890ff' }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="已入库"
                  value={stats.added}
                  prefix={<DatabaseOutlined />}
                  valueStyle={{ fontSize: 14, color: '#52c41a' }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="总提取"
                  value={stats.total}
                  prefix={<ExperimentOutlined />}
                  valueStyle={{ fontSize: 14 }}
                />
              </Col>
            </Row>
          </div>
        )}

        {/* 待验证知识列表 */}
        {pendingKnowledge.length > 0 ? (
          <div>
            <Text strong style={{ fontSize: 13, marginBottom: 8, display: 'block' }}>
              待验证知识点
            </Text>
            <List
              size="small"
              dataSource={pendingKnowledge}
              renderItem={(item) => (
                <List.Item
                  style={{ padding: '8px 0', border: 'none' }}
                  actions={[
                    <Tooltip title="验证知识质量">
                      <Button
                        type="link"
                        size="small"
                        icon={<SafetyCertificateOutlined />}
                        loading={validating === item.id}
                        onClick={() => handleValidate(item.id)}
                      >
                        验证
                      </Button>
                    </Tooltip>,
                    <Tooltip title="添加到知识库">
                      <Button
                        type="link"
                        size="small"
                        icon={<PlusCircleOutlined />}
                        loading={adding === item.id}
                        onClick={() => handleAddToKB(item.id)}
                        disabled={item.status !== 'validated'}
                      >
                        入库
                      </Button>
                    </Tooltip>,
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <Space>
                        <Text strong style={{ fontSize: 12 }}>{item.knowledge_point}</Text>
                        {getStatusTag(item.status)}
                      </Space>
                    }
                    description={
                      <div>
                        <Text type="secondary" style={{ fontSize: 11 }} ellipsis>
                          {item.content}
                        </Text>
                        <div style={{ marginTop: 4 }}>
                          <Text type="secondary" style={{ fontSize: 10 }}>
                            置信度: {Math.round(item.confidence * 100)}% · {new Date(item.created_at).toLocaleDateString()}
                          </Text>
                        </div>
                      </div>
                    }
                  />
                </List.Item>
              )}
            />
          </div>
        ) : (
          <Empty
            description="暂无待验证知识，高质量问答将自动提取"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            style={{ padding: '16px 0' }}
          />
        )}

        {/* 说明 */}
        <div style={{ marginTop: 12, padding: '8px 12px', background: '#f0f5ff', borderRadius: '6px' }}>
          <Text type="secondary" style={{ fontSize: 11 }}>
            💡 系统会自动从高质量问答中提取知识点。正向反馈的回答更容易被提取。
          </Text>
        </div>
      </Spin>
    </Card>
  );
};

export default KnowledgeExtractionPanel;
