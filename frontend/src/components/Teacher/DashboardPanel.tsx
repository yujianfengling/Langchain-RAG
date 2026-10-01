import React, { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Card, Col, List, Row, Space, Statistic, Tag, Typography } from 'antd';
import {
  BookOutlined, FileTextOutlined, ReloadOutlined, SolutionOutlined,
  TeamOutlined, WarningOutlined,
} from '@ant-design/icons';
import {
  TeacherDashboard, getTeacherDashboard, describeTeacherError, isForbiddenError,
} from '../../api/teacher';
import {
  EmptyBlock, ErrorAlert, LoadingBlock, PermissionAlert, formatTime, ratioText,
} from './common';

const { Text, Title } = Typography;

export interface DashboardNavTarget {
  key: 'classes' | 'materials' | 'plans' | 'assignments' | 'analytics';
  courseId?: string;
  classId?: string;
  planId?: string;
  knowledgePointId?: string;
}

export interface DashboardPanelProps {
  /** 跳转到具体管理页（由 TeacherPage 处理） */
  onOpen: (target: DashboardNavTarget) => void;
}

/** 工作台：我的课程 / 待完成的教案 / 已发布任务 / 需要关注的知识点 */
const DashboardPanel: React.FC<DashboardPanelProps> = ({ onOpen }) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState<string | null>(null);
  const [data, setData] = useState<TeacherDashboard | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setForbidden(null);
    try {
      const res = await getTeacherDashboard();
      setData(res?.data || null);
    } catch (e) {
      const f = describeTeacherError(e, '加载工作台失败');
      if (isForbiddenError(e)) setForbidden(f.message);
      else setError(f.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <LoadingBlock tip="正在加载工作台…" />;
  if (forbidden) return <PermissionAlert message={forbidden} />;
  if (error) return <ErrorAlert message={error} onRetry={load} />;
  if (!data) return <EmptyBlock description="暂无工作台数据" />;

  const courses = data.courses || [];
  const pendingPlans = data.pending_lesson_plans || [];
  const publishedTasks = data.published_assignments || [];
  const focusPoints = data.focus_knowledge_points || [];

  return (
    <div>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        <Title level={5} style={{ margin: 0 }}>教师工作台</Title>
        <Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
      </Space>

      <Row gutter={[16, 16]}>
        {/* 我的课程 */}
        <Col xs={24} lg={12}>
          <Card
            size="small"
            title={<Space><BookOutlined />我的课程</Space>}
            extra={<Badge count={courses.length} showZero color="#0f766e" />}
          >
            {courses.length === 0 ? (
              <EmptyBlock description="还没有课程。可在上方「课程」处新建一门课程后开始。" />
            ) : (
              <List
                size="small"
                dataSource={courses}
                renderItem={(c) => (
                  <List.Item
                    actions={[
                      <Button key="cls" type="link" size="small"
                              onClick={() => onOpen({ key: 'classes', courseId: c.id })}>班级</Button>,
                      <Button key="mat" type="link" size="small"
                              onClick={() => onOpen({ key: 'materials', courseId: c.id })}>资料</Button>,
                      <Button key="plan" type="link" size="small"
                              onClick={() => onOpen({ key: 'plans', courseId: c.id })}>教案</Button>,
                      <Button key="asg" type="link" size="small"
                              onClick={() => onOpen({ key: 'assignments', courseId: c.id })}>作业</Button>,
                    ]}
                  >
                    <List.Item.Meta
                      title={<Text strong>{c.title}</Text>}
                      description={
                        <Space size={4} wrap>
                          <Tag icon={<TeamOutlined />}>{c.class_count ?? 0} 个班</Tag>
                          <Tag>{c.student_count ?? 0} 名学生</Tag>
                          <Tag>{c.material_count ?? 0} 份资料</Tag>
                          <Tag color={c.draft_plan_count ? 'orange' : 'default'}>
                            教案草稿 {c.draft_plan_count ?? 0}
                          </Tag>
                          <Tag color={c.published_assignment_count ? 'green' : 'default'}>
                            已发布作业 {c.published_assignment_count ?? 0}
                          </Tag>
                        </Space>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>

        {/* 待完成的教案 */}
        <Col xs={24} lg={12}>
          <Card
            size="small"
            title={<Space><FileTextOutlined />待完成的教案（草稿）</Space>}
            extra={<Badge count={pendingPlans.length} showZero color="#d97706" />}
          >
            {pendingPlans.length === 0 ? (
              <EmptyBlock description="没有待完成的教案草稿" />
            ) : (
              <List
                size="small"
                dataSource={pendingPlans}
                renderItem={(p) => (
                  <List.Item
                    actions={[
                      <Button key="edit" type="link" size="small"
                              onClick={() => onOpen({ key: 'plans', courseId: p.course_id, planId: p.plan_id })}>
                        继续编辑
                      </Button>,
                    ]}
                  >
                    <List.Item.Meta
                      title={p.title}
                      description={
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {p.course_title} · 更新于 {formatTime(p.updated_at)}
                        </Text>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>

        {/* 已发布任务 */}
        <Col xs={24} lg={12}>
          <Card
            size="small"
            title={<Space><SolutionOutlined />已发布任务</Space>}
            extra={<Badge count={publishedTasks.length} showZero color="#2563eb" />}
          >
            {publishedTasks.length === 0 ? (
              <EmptyBlock description="还没有发布过作业 / 练习" />
            ) : (
              <List
                size="small"
                dataSource={publishedTasks}
                renderItem={(a) => (
                  <List.Item
                    actions={[
                      <Button key="go" type="link" size="small"
                              onClick={() => onOpen({ key: 'assignments', courseId: a.course_id })}>
                        查看完成情况
                      </Button>,
                    ]}
                  >
                    <List.Item.Meta
                      title={a.title}
                      description={
                        <Space size={4} wrap>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {a.course_title}{a.class_name ? ` · ${a.class_name}` : ''}
                          </Text>
                          <Tag color={a.due_date ? 'blue' : 'default'}>
                            截止 {a.due_date || '未设置'}
                          </Tag>
                        </Space>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>

        {/* 需要关注的知识点 */}
        <Col xs={24} lg={12}>
          <Card
            size="small"
            title={<Space><WarningOutlined />需要关注的知识点</Space>}
            extra={<Badge count={focusPoints.length} showZero color="#dc2626" />}
          >
            {focusPoints.length === 0 ? (
              <EmptyBlock description="暂无需要关注的知识点（可能是还没有作答数据）" />
            ) : (
              <List
                size="small"
                dataSource={focusPoints}
                renderItem={(k) => (
                  <List.Item
                    actions={[
                      <Button key="go" type="link" size="small"
                              disabled={!k.class_id}
                              onClick={() => onOpen({
                                key: 'analytics', courseId: k.course_id,
                                classId: k.class_id, knowledgePointId: k.knowledge_point_id || undefined,
                              })}>
                        看学情
                      </Button>,
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <Space size={6} wrap>
                          <Text strong>{k.knowledge_point || '未标注知识点'}</Text>
                          {k.enough_data === false && <Tag color="default">数据不足，仅供参考</Tag>}
                          {k.enough_data === true && k.wrong_rate != null && (
                            <Tag color="error">错误率 {ratioText(k.wrong_rate)}</Tag>
                          )}
                        </Space>
                      }
                      description={
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {k.class_name} · {k.course_title} · 作答 {k.attempt_count ?? 0} 次 / 错 {k.wrong_count ?? 0} 次
                          {k.range ? ` · ${k.range.start} ~ ${k.range.end}` : ''}
                        </Text>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>
      </Row>

      {/* 概览数字 */}
      <Card size="small" style={{ marginTop: 16 }}>
        <Space size={32} wrap>
          <Statistic title="我的课程" value={courses.length} suffix="门" />
          <Statistic title="教案草稿待完成" value={pendingPlans.length} suffix="份" />
          <Statistic title="已发布任务" value={publishedTasks.length} suffix="个" />
          <Statistic title="待关注知识点" value={focusPoints.length} suffix="个" />
        </Space>
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
          以上数字均来自后端真实记录；没有数据显示 0 或空列表，不做推测。
        </Text>
      </Card>
    </div>
  );
};

export default DashboardPanel;
