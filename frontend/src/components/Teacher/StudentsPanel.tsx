import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, Card, Col, Descriptions, Empty, Row, Select, Space, Spin, Statistic,
  Table, Tag, Typography,
} from 'antd';
import {
  BookOutlined, ReloadOutlined, StarOutlined, TeamOutlined, UserOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import {
  ClassOverview, StudentOverview, StudentSummary,
  describeTeacherError, getClassOverview, getStudents, getStudentOverview,
} from '../../api/teacher';
import { ErrorAlert, formatTime } from './common';

const { Text, Title } = Typography;

/** 学习风格中文名（沿用组员实现的映射） */
const STYLE_LABELS: Record<string, string> = {
  visual: '视觉型', auditory: '听觉型', reading: '阅读型',
  kinesthetic: '动手型', balanced: '均衡型',
};

const masteryColor = (m: number) => {
  if (m >= 0.7) return '#059669';
  if (m >= 0.4) return '#d97706';
  return '#dc2626';
};

export interface StudentsPanelProps {
  onBack?: () => void;
}

/**
 * 学生画像（并入组员实现，2026-09-27 合并）
 *
 * 与「学情分析」的分工：
 * - 学情分析：**班级/知识点**层面的聚合（错误率、常见错题、趋势），用于调整下一次教学；
 * - 学生画像（本面板）：**逐个学生**的画像（学习风格、掌握度分布、强项/弱项、反馈），用于个别辅导。
 *
 * 数据范围由后端限定为"自己名下班级的在班学生"（原组员实现返回全体用户且无鉴权，合并时已收紧）。
 */
const StudentsPanel: React.FC<StudentsPanelProps> = ({ onBack }) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [students, setStudents] = useState<StudentSummary[]>([]);
  const [scope, setScope] = useState<string>('');
  const [overview, setOverview] = useState<ClassOverview | null>(null);
  const [selected, setSelected] = useState<string>('');
  // 详情卡标题用的展示名（selected 是内部 user_id，直接摆出来教师认不出是谁）
  const [selectedLabel, setSelectedLabel] = useState<string>('');
  const [detail, setDetail] = useState<StudentOverview | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, agg] = await Promise.all([getStudents(), getClassOverview()]);
      setStudents(list?.data?.students || []);
      setScope(list?.data?.scope || agg?.data?.scope || '');
      setOverview(agg?.data || null);
    } catch (e) {
      setError(describeTeacherError(e, '加载学生画像失败').message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openStudent = async (target: string, subjectId?: string, label?: string) => {
    setSelected(target);
    setSelectedLabel(label || target);
    setDetailLoading(true);
    setDetailError(null);
    try {
      const res = await getStudentOverview(target, subjectId);
      setDetail(res?.data || null);
    } catch (e) {
      setDetail(null);
      setDetailError(describeTeacherError(e, '加载该学生学情失败').message);
    } finally {
      setDetailLoading(false);
    }
  };

  const columns = [
    {
      title: '学生',
      dataIndex: 'display_name',
      // 显示名回退：display_name → username → user_id。
      // 后端返回的 user_id 是内部 id（user_xxxxxxxxxxxx），直接摆出来教师认不出是谁；
      // 它只用于 rowKey 和"查看画像"的请求参数。
      render: (_: unknown, r: StudentSummary) => (
        <Space>
          <UserOutlined style={{ color: '#0f766e' }} />
          {r.display_name || r.username || r.user_id}
        </Space>
      ),
    },
    {
      title: '学习风格',
      dataIndex: 'learning_style',
      render: (s: string) => <Tag color="cyan">{STYLE_LABELS[s] || s || '—'}</Tag>,
    },
    {
      title: '交互次数', dataIndex: 'interaction_count',
      sorter: (a: StudentSummary, b: StudentSummary) => a.interaction_count - b.interaction_count,
    },
    { title: '知识点数', dataIndex: 'knowledge_point_count' },
    {
      title: '平均掌握度', dataIndex: 'average_mastery',
      sorter: (a: StudentSummary, b: StudentSummary) => a.average_mastery - b.average_mastery,
      render: (m: number) => (
        <Text style={{ color: masteryColor(m) }}>{Math.round((m || 0) * 100)}%</Text>
      ),
    },
    {
      title: '强项', dataIndex: 'strong_count',
      render: (n: number) => <Tag color="success">{n || 0}</Tag>,
    },
    {
      title: '薄弱', dataIndex: 'weak_count',
      render: (n: number) => <Tag color="error">{n || 0}</Tag>,
    },
    { title: '最近活跃', dataIndex: 'last_active', render: (t: string) => formatTime(t) },
    {
      title: '操作',
      render: (_: unknown, r: StudentSummary) => (
        <Button
          type="link"
          size="small"
          onClick={() => openStudent(r.user_id, undefined,
            r.display_name || r.username || r.user_id)}
        >
          查看画像
        </Button>
      ),
    },
  ];

  const styleLabel = (s: string) => STYLE_LABELS[s] || s || '—';

  return (
    <div>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        <Space>
          <Title level={5} style={{ margin: 0 }}>学生画像</Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            逐个学生看：学习风格 / 掌握度分布 / 强项弱项 / 反馈（班级层面看「学情分析」）
          </Text>
        </Space>
        <Space>
          {onBack && <Button size="small" onClick={onBack}>返回工作台</Button>}
          <Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
        </Space>
      </Space>

      {error && <ErrorAlert message={error} onRetry={load} />}

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="数据范围：只包含你自己名下班级的在班学生"
        description={scope || '（后端按班级归属限定，未在班学生不会出现在这里）'}
      />

      {!loading && !error && students.length === 0 && (
        <Card size="small">
          <Empty description="你名下的班级里还没有在班学生。学生在「我的学习 → 我的课程」用班级邀请码加入后会出现。" />
        </Card>
      )}

      {overview && (
        <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
          <Col xs={12} md={8}><Card size="small"><Statistic title="学生数" value={overview.total_students} prefix={<TeamOutlined />} /></Card></Col>
          <Col xs={12} md={8}><Card size="small"><Statistic title="总交互" value={overview.total_interactions} /></Card></Col>
          <Col xs={12} md={8}><Card size="small"><Statistic title="知识点" value={overview.total_knowledge_points} prefix={<BookOutlined />} /></Card></Col>
          <Col xs={12} md={8}><Card size="small"><Statistic title="薄弱点" value={overview.total_weak_points} prefix={<WarningOutlined />} valueStyle={{ color: '#dc2626' }} /></Card></Col>
          <Col xs={12} md={8}><Card size="small"><Statistic title="强项" value={overview.total_strong_points} prefix={<StarOutlined />} valueStyle={{ color: '#059669' }} /></Card></Col>
          <Col xs={12} md={8}><Card size="small"><Statistic title="平均掌握度" value={Math.round((overview.average_mastery || 0) * 100)} suffix="%" valueStyle={{ color: masteryColor(overview.average_mastery || 0) }} /></Card></Col>
        </Row>
      )}

      {(loading || students.length > 0) && (
        <Card size="small" title="学生列表">
          {loading ? <Spin /> : (
            <Table
              rowKey="user_id"
              dataSource={students}
              columns={columns}
              size="small"
              pagination={false}
              scroll={{ x: 900 }}
              className="app-table-scroll"
            />
          )}
        </Card>
      )}

      {selected && (
        <Card
          size="small"
          style={{ marginTop: 12 }}
          title={`学生画像：${selectedLabel || selected}`}
          extra={<Button size="small" onClick={() => { setSelected(''); setDetail(null); }}>收起</Button>}
        >
          {detailLoading && <Spin />}
          {detailError && <Alert type="warning" showIcon message={detailError} />}
          {detail && !detailLoading && (
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="学科">
                  {detail.subjects?.length ? (
                    <Select
                      size="small"
                      style={{ width: 200 }}
                      value={detail.current_subject || undefined}
                      options={detail.subjects.map((s) => ({ value: s, label: s }))}
                      onChange={(v) => openStudent(selected, v, selectedLabel)}
                    />
                  ) : '暂无学科记录'}
                </Descriptions.Item>
                <Descriptions.Item label="学习风格">
                  {styleLabel(String(detail.learning_stats?.learning_style || ''))}
                </Descriptions.Item>
              </Descriptions>

              <Row gutter={[12, 12]}>
                <Col xs={12} md={6}><Statistic title="知识点总数" value={Number(detail.learning_stats?.total_knowledge_points || 0)} /></Col>
                <Col xs={12} md={6}>
                  <Statistic
                    title="平均掌握度"
                    value={Math.round(Number(detail.learning_stats?.average_mastery || 0) * 100)}
                    suffix="%"
                    valueStyle={{ color: masteryColor(Number(detail.learning_stats?.average_mastery || 0)) }}
                  />
                </Col>
                <Col xs={12} md={6}><Statistic title="强项知识点" value={detail.strong_points?.length || 0} valueStyle={{ color: '#059669' }} /></Col>
                <Col xs={12} md={6}><Statistic title="薄弱知识点" value={detail.weak_points?.length || 0} valueStyle={{ color: '#dc2626' }} /></Col>
              </Row>

              <Card size="small" type="inner" title={<Space><WarningOutlined style={{ color: '#dc2626' }} />薄弱知识点</Space>}>
                {detail.weak_points?.length ? (
                  <Space wrap>
                    {detail.weak_points.map((w, i) => (
                      <Tag color="error" key={i}>
                        {String((w as any).knowledge_point || (w as any).name || JSON.stringify(w))}
                      </Tag>
                    ))}
                  </Space>
                ) : <Text type="secondary">暂无薄弱知识点记录</Text>}
              </Card>

              <Card size="small" type="inner" title={<Space><StarOutlined style={{ color: '#059669' }} />已掌握知识点</Space>}>
                {detail.strong_points?.length ? (
                  <Space wrap>
                    {detail.strong_points.map((s, i) => (
                      <Tag color="success" key={i}>
                        {String((s as any).knowledge_point || (s as any).name || JSON.stringify(s))}
                      </Tag>
                    ))}
                  </Space>
                ) : <Text type="secondary">暂无已掌握知识点记录</Text>}
              </Card>

              {detail.knowledge_gaps?.length > 0 && (
                <Card size="small" type="inner" title="知识缺口（系统推断）">
                  <Space wrap>
                    {detail.knowledge_gaps.map((g, i) => (
                      <Tag key={i}>{String((g as any).knowledge_point || (g as any).name || JSON.stringify(g))}</Tag>
                    ))}
                  </Space>
                </Card>
              )}

              <Card size="small" type="inner" title="学生反馈统计">
                <Descriptions size="small" column={2}>
                  <Descriptions.Item label="反馈条数">
                    {String((detail.feedback as any)?.total ?? (detail.feedback as any)?.count ?? '—')}
                  </Descriptions.Item>
                  <Descriptions.Item label="正向 / 负向">
                    {String((detail.feedback as any)?.positive ?? '—')} / {String((detail.feedback as any)?.negative ?? '—')}
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            </Space>
          )}
        </Card>
      )}
    </div>
  );
};

export default StudentsPanel;
