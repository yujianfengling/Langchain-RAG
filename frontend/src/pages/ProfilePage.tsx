import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Avatar, Button, Card, Col, List, Progress, Row, Select, Space, Statistic, Tag,
  Typography,
} from 'antd';
import {
  ExperimentOutlined, FileTextOutlined, ReloadOutlined, RightOutlined, SettingOutlined,
  UserOutlined, WarningOutlined,
} from '@ant-design/icons';
import { AuthUser, isLoggedIn, me } from '../api/auth';
import {
  KnowledgeTracingPoint, MasteryPoint, WrongQuestion,
  getKnowledgeTracing, getMastery, getQuizStats, getWrongQuestions,
} from '../api/student';
import { listArtifacts } from '../api/tutor';
import { MemoryItem, getEffectiveMemory } from '../api/planAgent';
import { useCourse } from '../context/CourseContext';
import { EmptyBlock, ErrorAlert, LoadingBlock, formatTime } from '../components/Teacher/common';
import CollapsiblePanel from '../components/CollapsiblePanel';
import PersonalizationSettings from '../components/PersonalizationSettings';
import SystemStatusPanel from '../components/SystemStatusPanel';
import TeacherActivation from '../components/TeacherActivation';

const { Text } = Typography;

interface MasterySummary {
  total_points: number;
  distribution: { strong: number; medium: number; weak: number; pending: number };
  accuracy: number | null;
  independent_correct: number;
  independent_wrong: number;
}

interface QuizStats {
  total_attempts: number;
  correct: number;
  wrong: number;
  pending_self_eval: number;
  accuracy: number | null;
}

interface ArtifactItem {
  id: string;
  kind: string;
  kind_label?: string;
  title: string;
  updated_at?: string;
  edited?: boolean;
  stale?: boolean;
}

const ROLE_LABEL: Record<string, string> = { student: '学生', teacher: '教师', admin: '管理员' };

const MEMORY_SOURCE: Record<string, string> = {
  explicit_user: '我明确设置',
  inferred: '系统推断',
  teacher: '教师设定',
  curriculum: '课程资料确认',
};

/**
 * 我的主页（学生与教师共用，2026-10-07）
 *
 * 只展示来自后端的真实记录（掌握度 / 作答统计 / 错题 / 成果 / 学习记忆），
 * 不展示内部 ID、耗时与调试字段；高级模型与诊断配置收进「高级设置」，
 * 且教师开通与运行状态只对教师 / 管理员展示。
 */
const ProfilePage: React.FC = () => {
  const navigate = useNavigate();
  const { currentSubjectId, subjects, courses, select, selectSubject } = useCourse();

  const [user, setUser] = useState<AuthUser | null>(null);
  const [userLoading, setUserLoading] = useState(true);

  const [mastery, setMastery] = useState<MasterySummary | null>(null);
  const [points, setPoints] = useState<MasteryPoint[]>([]);
  const [quizStats, setQuizStats] = useState<QuizStats | null>(null);
  const [wrongs, setWrongs] = useState<WrongQuestion[]>([]);
  const [bktPoints, setBktPoints] = useState<KnowledgeTracingPoint[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [dataLoading, setDataLoading] = useState(false);
  const [dataError, setDataError] = useState<string | null>(null);

  const loggedIn = isLoggedIn();

  const loadUser = useCallback(async () => {
    if (!isLoggedIn()) { setUser(null); setUserLoading(false); return; }
    setUserLoading(true);
    try {
      const res = await me();
      setUser(res?.data ?? null);
    } catch {
      setUser(null);
    } finally {
      setUserLoading(false);
    }
  }, []);

  const loadData = useCallback(async () => {
    if (!isLoggedIn() || !currentSubjectId) {
      setMastery(null); setPoints([]); setQuizStats(null);
      setWrongs([]); setArtifacts([]); setMemories([]); setBktPoints([]);
      setDataError(null); setDataLoading(false);
      return;
    }
    setDataLoading(true);
    setDataError(null);
    const [m, q, w, a, mem, bkt] = await Promise.allSettled([
      getMastery(currentSubjectId),
      getQuizStats(currentSubjectId),
      getWrongQuestions(currentSubjectId),
      listArtifacts(currentSubjectId),
      getEffectiveMemory(currentSubjectId),
      getKnowledgeTracing(currentSubjectId),
    ]);
    if (m.status === 'fulfilled') {
      setMastery((m.value?.data?.summary as MasterySummary) ?? null);
      setPoints(m.value?.data?.points ?? []);
    }
    if (q.status === 'fulfilled') setQuizStats((q.value?.data as QuizStats) ?? null);
    if (w.status === 'fulfilled') setWrongs(w.value?.data ?? []);
    if (a.status === 'fulfilled') setArtifacts(Array.isArray(a.value) ? a.value as ArtifactItem[] : []);
    if (mem.status === 'fulfilled') setMemories(mem.value?.data?.items ?? []);
    if (bkt.status === 'fulfilled') setBktPoints(bkt.value?.data?.points ?? []);
    if ([m, q, w, a, mem, bkt].every((r) => r.status === 'rejected')) {
      setDataError('学习数据加载失败，请稍后重试');
    }
    setDataLoading(false);
  }, [currentSubjectId]);

  useEffect(() => { loadUser(); }, [loadUser]);
  useEffect(() => { loadData(); }, [loadData]);

  const handleSubjectChange = (sid: string) => {
    const course = courses.find((c) => c.subject_id === sid);
    if (course) select(course.course_id);
    else selectSubject(sid);
  };

  if (!loggedIn) {
    return (
      <div style={{ padding: 16, maxWidth: 1080, margin: '0 auto' }}>
        <Card title={<Space><UserOutlined />我的主页</Space>}>
          <EmptyBlock description="登录后可以查看学习记录、学习成果与学习记忆。">
            <Text type="secondary">请通过左下角 / 顶栏的头像菜单登录。</Text>
          </EmptyBlock>
        </Card>
      </div>
    );
  }

  if (userLoading) {
    return (
      <div style={{ padding: 16, maxWidth: 1080, margin: '0 auto' }}>
        <LoadingBlock tip="正在读取账号信息…" />
      </div>
    );
  }

  const displayName = user?.display_name || user?.username || '未登录';
  const isTeacher = user?.role === 'teacher' || user?.role === 'admin';
  const distribution = mastery?.distribution;
  const evaluated = (mastery?.total_points ?? 0) - (distribution?.pending ?? 0);
  const totalAttempts = quizStats?.total_attempts ?? 0;
  const accuracyText = quizStats?.accuracy == null ? '—' : Math.round(quizStats.accuracy * 100) + '%';
  const hasLearningData = totalAttempts > 0 || (mastery?.total_points ?? 0) > 0 || wrongs.length > 0;
  const weakPoints = points.filter((p) => p.mastery_level != null && p.mastery_level < 0.6).slice(0, 5);

  return (
    <div className="app-page">
      <header className="page-header">
        <div className="page-heading">
          <div className="page-eyebrow">个人记录</div>
          <Typography.Title level={1} className="page-title">我的主页</Typography.Title>
          <Text className="page-subtitle">查看真实学习记录、成果与偏好，让下一步学习更有依据。</Text>
        </div>
        <Space className="page-actions">
          <Button icon={<SettingOutlined />} onClick={() => navigate('/settings')}>设置</Button>
        </Space>
      </header>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {/* 头像与账号 */}
        <Card>
          <Row align="middle" gutter={[16, 12]}>
            <Col flex="none">
              <Avatar size={60} style={{ backgroundColor: 'var(--color-primary)', fontSize: 24 }}>
                {displayName.charAt(0)}
              </Avatar>
            </Col>
            <Col flex="auto" style={{ minWidth: 0 }}>
              <Space direction="vertical" size={2} style={{ width: '100%' }}>
                <Space size={8} wrap>
                  <Text strong style={{ fontSize: 18 }}>{displayName}</Text>
                  <Tag color={user?.role === 'admin' ? 'gold' : isTeacher ? 'blue' : 'default'}>
                    {ROLE_LABEL[user?.role || 'student'] || '学生'}
                  </Tag>
                </Space>
                <Text type="secondary" ellipsis>{user?.username ? '账号：' + user.username : ''}</Text>
              </Space>
            </Col>
            <Col xs={24} sm={24} md={8} lg={7}>
              <Select
                style={{ width: '100%' }}
                placeholder={subjects.length ? '选择学科' : '还没有学科'}
                value={currentSubjectId || undefined}
                onChange={handleSubjectChange}
                options={subjects.map((s) => ({ value: s.subject_id, label: s.name }))}
                notFoundContent="先加入或创建课程"
              />
            </Col>
          </Row>
        </Card>

        {dataError && <ErrorAlert message={dataError} onRetry={loadData} />}

        {dataLoading ? (
          <Card><LoadingBlock tip="正在加载学习记录…" /></Card>
        ) : (
          <>
            {/* 学习概览 */}
            <Card
              title={<Space><ExperimentOutlined />学习概览</Space>}
              extra={<Button size="small" icon={<ReloadOutlined />} onClick={loadData}>刷新</Button>}
            >
              {!currentSubjectId ? (
                <EmptyBlock description="还没有选择学科。先加入或创建一门课程，再回来看学习记录。">
                  <Button type="primary" onClick={() => navigate('/student/courses')}>去我的课程</Button>
                </EmptyBlock>
              ) : !hasLearningData ? (
                <EmptyBlock description="还没有学习记录。先去「开始学习」提一个问题或做一道练习，这里就会出现真实数据。">
                  <Button type="primary" onClick={() => navigate('/student')}>开始学习</Button>
                </EmptyBlock>
              ) : (
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <Row gutter={[16, 16]}>
                    <Col xs={12} sm={6}>
                      <Statistic title="已评估知识点" value={evaluated} suffix={'/ ' + (mastery?.total_points ?? 0)} />
                    </Col>
                    <Col xs={12} sm={6}><Statistic title="作答次数" value={totalAttempts} /></Col>
                    <Col xs={12} sm={6}><Statistic title="客观正确率" value={accuracyText} /></Col>
                    <Col xs={12} sm={6}><Statistic title="错题" value={wrongs.length} /></Col>
                  </Row>
                  {distribution && (
                    <Space size={8} wrap>
                      <Tag color="success">强项 {distribution.strong}</Tag>
                      <Tag color="warning">中等 {distribution.medium}</Tag>
                      <Tag color="error">薄弱 {distribution.weak}</Tag>
                      <Tag>待评估 {distribution.pending}</Tag>
                    </Space>
                  )}
                  {weakPoints.length > 0 && (
                    <div>
                      <Text type="secondary" style={{ fontSize: 12 }}>需要加强的知识点</Text>
                      {weakPoints.map((p) => (
                        <div key={p.knowledge_point_id || p.knowledge_point} style={{ marginTop: 6 }}>
                          <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
                            <Text style={{ fontSize: 13 }}>{p.knowledge_point}</Text>
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              {Math.round((p.mastery_level ?? 0) * 100)}%
                            </Text>
                          </Space>
                          <Progress
                            percent={Math.round((p.mastery_level ?? 0) * 100)}
                            showInfo={false}
                            strokeColor="var(--color-primary)"
                            size="small"
                          />
                        </div>
                      ))}
                    </div>
                  )}
                  {bktPoints.length > 0 && (
                    <div>
                      <Space size={6} wrap style={{ marginBottom: 4 }}>
                        <Text type="secondary" style={{ fontSize: 12 }}>BKT 预测（影子）</Text>
                        <Tag color="purple">不改变掌握度</Tag>
                      </Space>
                      {bktPoints.slice(0, 5).map((p) => (
                        <div key={p.knowledge_point} style={{ marginTop: 6 }}>
                          <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
                            <Space size={4}>
                              <Text style={{ fontSize: 13 }}>{p.knowledge_point}</Text>
                              {bktPoints[0]?.knowledge_point === p.knowledge_point && (
                                <Tag color="volcano">建议优先练</Tag>
                              )}
                            </Space>
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              {Math.round(p.p_known * 100)}%
                            </Text>
                          </Space>
                          <Progress percent={Math.round(p.p_known * 100)} showInfo={false}
                                    strokeColor="#7c3aed" size="small" />
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            样本 {p.attempts} · 置信度 {Math.round(p.confidence * 100)}%
                          </Text>
                        </div>
                      ))}
                    </div>
                  )}
                </Space>
              )}
            </Card>

            <Row gutter={[16, 16]}>
              <Col xs={24} lg={12}>
                <Card title={<Space><WarningOutlined />错题与复习</Space>} style={{ height: '100%' }}>
                  {wrongs.length === 0 ? (
                    <EmptyBlock description="暂无错题。做几道练习后，错题会汇总到这里。">
                      <Button onClick={() => navigate('/student/todo')}>去做练习</Button>
                    </EmptyBlock>
                  ) : (
                    <>
                      <List
                        size="small"
                        dataSource={wrongs.slice(0, 3)}
                        renderItem={(w) => (
                          <List.Item>
                            <List.Item.Meta
                              title={<Text ellipsis={{ tooltip: w.stem }}>{w.stem}</Text>}
                              description={w.knowledge_point ? <Tag>{w.knowledge_point}</Tag> : null}
                            />
                          </List.Item>
                        )}
                      />
                      <Button type="link" onClick={() => navigate('/student/todo')}>
                        查看全部错题与复习 <RightOutlined />
                      </Button>
                    </>
                  )}
                </Card>
              </Col>

              <Col xs={24} lg={12}>
                <Card
                  title={<Space><FileTextOutlined />最近成果</Space>}
                  style={{ height: '100%' }}
                  extra={<Text type="secondary" style={{ fontSize: 12 }}>{artifacts.length} 项</Text>}
                >
                  {artifacts.length === 0 ? (
                    <EmptyBlock description="还没有学习成果。在学习页让助手整理笔记、生成思维导图后会保存在这里。">
                      <Button onClick={() => navigate('/student')}>去学习</Button>
                    </EmptyBlock>
                  ) : (
                    <List
                      size="small"
                      dataSource={artifacts.slice(0, 4)}
                      renderItem={(a) => (
                        <List.Item>
                          <List.Item.Meta
                            title={
                              <Space size={6} wrap>
                                <Tag color="green">{a.kind_label || a.kind}</Tag>
                                <Text ellipsis={{ tooltip: a.title }} style={{ maxWidth: 260 }}>{a.title}</Text>
                                {a.edited && <Tag>已编辑</Tag>}
                                {a.stale && <Tag color="orange">资料已更新</Tag>}
                              </Space>
                            }
                            description={
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                更新于 {formatTime(a.updated_at)}
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

            {/* 学习记忆 */}
            <Card
              title={<Space><ExperimentOutlined />学习记忆</Space>}
              extra={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {memories.length > 0 ? memories.length + ' 条生效中' : ''}
                </Text>
              }
            >
              {memories.length === 0 ? (
                <EmptyBlock description="助手还没有形成学习记忆。多提问、明确你的偏好后会出现在这里。">
                  <Button onClick={() => navigate('/settings?tab=preference')}>去设置偏好</Button>
                </EmptyBlock>
              ) : (
                <List
                  size="small"
                  dataSource={memories}
                  renderItem={(m) => (
                    <List.Item>
                      <List.Item.Meta
                        title={
                          <Space size={6} wrap>
                            <Tag>{m.type_label || m.memory_type}</Tag>
                            <Text strong>{m.key}</Text>
                          </Space>
                        }
                        description={
                          <Space direction="vertical" size={2}>
                            <Text>{m.value}</Text>
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              来源：{MEMORY_SOURCE[m.source] || m.source}
                            </Text>
                          </Space>
                        }
                      />
                    </List.Item>
                  )}
                />
              )}
            </Card>
          </>
        )}

        {/* 偏好默认收起，减少学习记录页的配置噪音；完整设置仍可从页头进入。 */}
        <CollapsiblePanel title="学习偏好" icon={<SettingOutlined />} defaultOpen={false}>
          <PersonalizationSettings />
        </CollapsiblePanel>

        {/* 高级设置：按角色限制 */}
        <CollapsiblePanel title="高级设置（模型与诊断）" icon={<SettingOutlined />} defaultOpen={false}>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {isTeacher ? (
              <>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  教师身份开通、运行状态与外部能力用量属于管理与诊断信息，仅教师 / 管理员可见。
                </Text>
                <TeacherActivation />
                <SystemStatusPanel />
              </>
            ) : (
              <Alert
                type="info"
                showIcon
                message="运行状态与教师身份开通仅对教师 / 管理员展示"
                description="如需开通教师身份，请到「设置」页凭教师邀请码申请；模型与运行状态可在「设置」页查看。"
              />
            )}
          </Space>
        </CollapsiblePanel>
      </Space>
    </div>
  );
};

export default ProfilePage;
