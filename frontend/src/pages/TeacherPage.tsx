import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Row, Col, Table, Tag, Typography, Statistic, Space,
  Select, Empty, Spin, Progress, Descriptions, Divider, message, Button,
} from 'antd';
import {
  TeamOutlined, BookOutlined, WarningOutlined, StarOutlined,
  InteractionOutlined, ArrowLeftOutlined, UserOutlined,
} from '@ant-design/icons';
import { motion } from 'framer-motion';
import {
  getStudents, getStudentOverview, getClassOverview,
  StudentSummary, StudentOverview, ClassOverview,
} from '../api/teacher';

const { Title, Text } = Typography;

// 学习风格中文映射
const STYLE_LABELS: Record<string, string> = {
  balanced: '均衡型',
  visual: '视觉型',
  auditory: '听觉型',
  reading: '读写型',
  kinesthetic: '实践型',
};

// 掌握度颜色
const masteryColor = (m: number) => {
  if (m >= 0.7) return '#059669';
  if (m >= 0.4) return '#d97706';
  return '#dc2626';
};

const TeacherPage: React.FC = () => {
  const [students, setStudents] = useState<StudentSummary[]>([]);
  const [classOverview, setClassOverview] = useState<ClassOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedStudent, setSelectedStudent] = useState<string | null>(null);
  const [studentDetail, setStudentDetail] = useState<StudentOverview | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [selectedSubject, setSelectedSubject] = useState<string | undefined>(undefined);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [studentsRes, overviewRes] = await Promise.all([
        getStudents(),
        getClassOverview(),
      ]);
      setStudents(studentsRes?.data?.students || []);
      setClassOverview(overviewRes?.data || null);
    } catch (e) {
      message.error('加载教师端数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const loadStudentDetail = useCallback(async (userId: string, subjectId?: string) => {
    setDetailLoading(true);
    try {
      const res = await getStudentOverview(userId, subjectId);
      setStudentDetail(res?.data || null);
    } catch (e) {
      message.error('加载学生学情失败');
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const handleSelectStudent = (userId: string) => {
    setSelectedStudent(userId);
    setSelectedSubject(undefined);
    loadStudentDetail(userId);
  };

  const handleSubjectChange = (subjectId: string | undefined) => {
    setSelectedSubject(subjectId);
    if (selectedStudent) {
      loadStudentDetail(selectedStudent, subjectId);
    }
  };

  // ============ 学生详情视图 ============
  // 已选中学生但详情尚未返回时，显示加载态（否则会瞬间落回班级列表，造成"点了没反应"）
  if (selectedStudent && !studentDetail) {
    return (
      <div style={{ padding: 24 }}>
        <Button type="link" icon={<ArrowLeftOutlined />} onClick={() => setSelectedStudent(null)} style={{ padding: 0 }}>
          返回班级列表
        </Button>
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" spinning={detailLoading} />
        </div>
      </div>
    );
  }

  if (selectedStudent && studentDetail) {
    const stats = studentDetail.learning_stats || {};
    const weakPoints = studentDetail.weak_points || [];
    const strongPoints = studentDetail.strong_points || [];

    return (
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
        <div style={{ marginBottom: 16 }}>
          <Button type="link" icon={<ArrowLeftOutlined />} onClick={() => { setSelectedStudent(null); setStudentDetail(null); }} style={{ padding: 0 }}>
            返回学生列表
          </Button>
        </div>

        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {/* 学生基本信息 + 学科选择 */}
          <Card>
            <Space size={12} wrap>
              <UserOutlined style={{ fontSize: 24, color: '#0f766e' }} />
              <Title level={4} style={{ margin: 0 }}>{selectedStudent}</Title>
              <Tag color="cyan">{STYLE_LABELS[studentDetail.feedback?.learning_style] || '未知风格'}</Tag>
              {studentDetail.subjects?.length > 0 && (
                <Select
                  placeholder="选择学科"
                  value={selectedSubject}
                  onChange={handleSubjectChange}
                  options={studentDetail.subjects.map(s => ({ value: s, label: s }))}
                  style={{ width: 200 }}
                  allowClear
                />
              )}
            </Space>
          </Card>

          {/* 学习统计 */}
          <Row gutter={16}>
            <Col xs={12} md={6}><Card><Statistic title="知识点总数" value={stats.total_knowledge_points || 0} prefix={<BookOutlined />} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="平均掌握度" value={Math.round((stats.average_mastery || 0) * 100) / 100} suffix="%" valueStyle={{ color: masteryColor(stats.average_mastery || 0) }} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="强项知识点" value={stats.mastery_distribution?.strong || 0} prefix={<StarOutlined />} valueStyle={{ color: '#059669' }} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="薄弱知识点" value={stats.mastery_distribution?.weak || 0} prefix={<WarningOutlined />} valueStyle={{ color: '#dc2626' }} /></Card></Col>
          </Row>

          {/* 弱项和强项 */}
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Card title={<Space><WarningOutlined style={{ color: '#dc2626' }} />薄弱知识点</Space>}>
                {weakPoints.length === 0 ? (
                  <Empty description="暂无薄弱知识点" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                ) : (
                  weakPoints.map((w, i) => (
                    <div key={i} style={{ marginBottom: 12 }}>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <Text>{w.knowledge_point}</Text>
                        <Text type="secondary">掌握度 {Math.round(w.mastery_level * 100)}%</Text>
                      </Space>
                      <Progress percent={Math.round(w.mastery_level * 100)} showInfo={false} strokeColor="#dc2626" size="small" />
                    </div>
                  ))
                )}
              </Card>
            </Col>
            <Col xs={24} md={12}>
              <Card title={<Space><StarOutlined style={{ color: '#059669' }} />已掌握知识点</Space>}>
                {strongPoints.length === 0 ? (
                  <Empty description="暂无强项知识点" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                ) : (
                  strongPoints.map((s, i) => (
                    <div key={i} style={{ marginBottom: 12 }}>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <Text>{s.knowledge_point}</Text>
                        <Text type="secondary">掌握度 {Math.round(s.mastery_level * 100)}%</Text>
                      </Space>
                      <Progress percent={Math.round(s.mastery_level * 100)} showInfo={false} strokeColor="#059669" size="small" />
                    </div>
                  ))
                )}
              </Card>
            </Col>
          </Row>

          {/* 反馈统计 */}
          {studentDetail.feedback && (
            <Card title="学生反馈">
              <Descriptions column={3} size="small">
                <Descriptions.Item label="总反馈数">{studentDetail.feedback.total_feedback || 0}</Descriptions.Item>
                <Descriptions.Item label="正向反馈">{studentDetail.feedback.positive_count || 0}</Descriptions.Item>
                <Descriptions.Item label="负向反馈">{studentDetail.feedback.negative_count || 0}</Descriptions.Item>
              </Descriptions>
            </Card>
          )}
        </Space>
      </motion.div>
    );
  }

  // ============ 班级概览 + 学生列表 ============
  const columns = [
    {
      title: '学生',
      dataIndex: 'user_id',
      key: 'user_id',
      render: (text: string) => <Space><UserOutlined style={{ color: '#0f766e' }} />{text}</Space>,
    },
    {
      title: '学习风格',
      dataIndex: 'learning_style',
      key: 'learning_style',
      render: (s: string) => <Tag color="cyan">{STYLE_LABELS[s] || s}</Tag>,
    },
    { title: '交互次数', dataIndex: 'interaction_count', key: 'interaction_count', sorter: (a: any, b: any) => a.interaction_count - b.interaction_count },
    { title: '知识点数', dataIndex: 'knowledge_point_count', key: 'knowledge_point_count' },
    {
      title: '平均掌握度',
      dataIndex: 'average_mastery',
      key: 'average_mastery',
      sorter: (a: any, b: any) => a.average_mastery - b.average_mastery,
      render: (m: number) => <Text style={{ color: masteryColor(m) }}>{Math.round(m * 100)}%</Text>,
    },
    { title: '强项', dataIndex: 'strong_count', key: 'strong_count', render: (n: number) => <Tag color="success">{n}</Tag> },
    { title: '薄弱', dataIndex: 'weak_count', key: 'weak_count', render: (n: number) => <Tag color="error">{n}</Tag> },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: StudentSummary) => (
        <Button type="link" size="small" onClick={() => handleSelectStudent(record.user_id)}>查看学情</Button>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Title level={4}>教师端 · 学情管理</Title>
      <Text type="secondary">查看学生学习进度、知识掌握情况和薄弱环节</Text>

      <Divider />

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
      ) : (
        <>
          {/* 班级概览 */}
          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col xs={12} md={4}><Card><Statistic title="学生数" value={classOverview?.total_students || 0} prefix={<TeamOutlined />} /></Card></Col>
            <Col xs={12} md={4}><Card><Statistic title="总交互" value={classOverview?.total_interactions || 0} prefix={<InteractionOutlined />} /></Card></Col>
            <Col xs={12} md={4}><Card><Statistic title="知识点" value={classOverview?.total_knowledge_points || 0} prefix={<BookOutlined />} /></Card></Col>
            <Col xs={12} md={4}><Card><Statistic title="薄弱点" value={classOverview?.total_weak_points || 0} valueStyle={{ color: '#dc2626' }} prefix={<WarningOutlined />} /></Card></Col>
            <Col xs={12} md={4}><Card><Statistic title="强项" value={classOverview?.total_strong_points || 0} valueStyle={{ color: '#059669' }} prefix={<StarOutlined />} /></Card></Col>
            <Col xs={12} md={4}><Card><Statistic title="平均掌握度" value={Math.round((classOverview?.average_mastery || 0) * 100)} suffix="%" /></Card></Col>
          </Row>

          {/* 学生列表 */}
          <Card title="学生列表">
            {students.length === 0 ? (
              <Empty description="暂无学生数据" />
            ) : (
              <Table
                columns={columns}
                dataSource={students}
                rowKey="user_id"
                pagination={{ pageSize: 10 }}
                size="middle"
              />
            )}
          </Card>
        </>
      )}
    </div>
  );
};

export default TeacherPage;
