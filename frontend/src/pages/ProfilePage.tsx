import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Badge, Button, Card, Col, Input, List, Progress, Row, Segmented, Select, Space,
  Statistic, Table, Tabs, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  ApiOutlined, BookOutlined, BulbOutlined, ClockCircleOutlined, EditOutlined,
  ExperimentOutlined, FileTextOutlined, FormOutlined, MessageOutlined, ReloadOutlined,
  RightOutlined, RiseOutlined, RobotOutlined, SettingOutlined, UserOutlined, WarningOutlined,
} from '@ant-design/icons';
import type { EChartsOption } from 'echarts';
import { AuthUser, isLoggedIn, me, updateProfile, uploadAvatar } from '../api/auth';
import {
  StudentFeedbackPoint, StudentFeedbackSummary, WrongQuestion,
  getQuizStats, getStudentFeedback, getWrongQuestions,
} from '../api/student';
import { listArtifacts } from '../api/tutor';
import { MemoryItem, getEffectiveMemory } from '../api/planAgent';
import { useCourse } from '../context/CourseContext';
import { useAuth } from '../context/AuthContext';
import UserAvatar from '../components/UserAvatar';
import { EmptyBlock, ErrorAlert, LoadingBlock, formatTime } from '../components/Teacher/common';
import PersonalizationSettings from '../components/PersonalizationSettings';
import SystemStatusPanel from '../components/SystemStatusPanel';
import TeacherActivation from '../components/TeacherActivation';
import EChart from '../components/EChart';

const KnowledgeGraph = React.lazy(() => import('../components/KnowledgeGraph'));

const { Text, Title } = Typography;

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

const STAGE_COLOR: Record<string, string> = {
  weak: 'error',
  forgetting: 'warning',
  developing: 'processing',
  consolidated: 'success',
  pending: 'default',
};

/**
 * 个人学情首页（登录默认首屏）
 *
 * 宽屏模块化布局：
 * - 顶部轻量工具条：身份 + 学科切换 + 时间范围 + 直达 AI 伴学/设置
 * - 首屏总览带：左（一句话诊断与首要行动）+ 右（4 项核心学情指标）
 * - 4 个清晰模块标签：学情诊断 / 知识图谱 / 错题与成果 / 账号与偏好，避免无止境纵向堆叠
 */
const ProfilePage: React.FC = () => {
  const navigate = useNavigate();
  const { refreshUser } = useAuth();
  const { currentSubjectId, subjects, courses, select, selectSubject } = useCourse();
  const avatarInputRef = useRef<HTMLInputElement>(null);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState('');
  const [savingProfile, setSavingProfile] = useState(false);
  const [days, setDays] = useState(30);
  const [activeTab, setActiveTab] = useState('overview');
  const [kpFilter, setKpFilter] = useState<'all' | 'focus' | 'strong'>('all');
  const [signalTab, setSignalTab] = useState<'forgetting' | 'causes' | 'teacher'>('forgetting');

  const [user, setUser] = useState<AuthUser | null>(null);
  const [userLoading, setUserLoading] = useState(true);

  const [feedback, setFeedback] = useState<StudentFeedbackSummary | null>(null);
  const [quizStats, setQuizStats] = useState<QuizStats | null>(null);
  const [wrongs, setWrongs] = useState<WrongQuestion[]>([]);
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
      setFeedback(null); setQuizStats(null);
      setWrongs([]); setArtifacts([]); setMemories([]);
      setDataError(null); setDataLoading(false);
      return;
    }
    setDataLoading(true);
    setDataError(null);
    const [fb, q, w, a, mem] = await Promise.allSettled([
      getStudentFeedback(currentSubjectId, days),
      getQuizStats(currentSubjectId),
      getWrongQuestions(currentSubjectId),
      listArtifacts(currentSubjectId),
      getEffectiveMemory(currentSubjectId),
    ]);
    if (fb.status === 'fulfilled') setFeedback(fb.value?.data ?? null);
    if (q.status === 'fulfilled') setQuizStats((q.value?.data as QuizStats) ?? null);
    if (w.status === 'fulfilled') setWrongs(w.value?.data ?? []);
    if (a.status === 'fulfilled') setArtifacts(Array.isArray(a.value) ? a.value as ArtifactItem[] : []);
    if (mem.status === 'fulfilled') setMemories(mem.value?.data?.items ?? []);
    if ([fb, q, w, a, mem].every((r) => r.status === 'rejected')) {
      setDataError('学习数据加载失败，请稍后重试');
    }
    setDataLoading(false);
  }, [currentSubjectId, days]);

  useEffect(() => { loadUser(); }, [loadUser]);
  useEffect(() => { loadData(); }, [loadData]);

  // 自动选中当前最有信号的右侧子面板（教师评语 > 遗忘预警 > 错因画像）
  useEffect(() => {
    if (!feedback) return;
    if ((feedback.teacher_feedback?.length || 0) > 0) setSignalTab('teacher');
    else if ((feedback.forgetting_alerts?.length || 0) > 0) setSignalTab('forgetting');
    else if ((feedback.error_causes?.length || 0) > 0) setSignalTab('causes');
  }, [feedback]);

  const jumpStudy = useCallback((kp: string, action: 'explain' | 'practice' | 'prereq' | 'review') => {
    if (!currentSubjectId) return;
    const qs = new URLSearchParams();
    qs.set('subject', currentSubjectId);
    qs.set('kp', kp);
    qs.set('action', action);
    navigate(`/student?${qs.toString()}`);
  }, [currentSubjectId, navigate]);

  const handleSubjectChange = (sid: string) => {
    const course = courses.find((c) => c.subject_id === sid);
    if (course) select(course.course_id);
    else selectSubject(sid);
  };

  const trendOption = useMemo<EChartsOption>(() => {
    const rows = feedback?.progress?.daily_trend || [];
    return {
      tooltip: { trigger: 'axis' },
      legend: { bottom: 0, textStyle: { fontSize: 11 } },
      grid: { left: 36, right: 40, top: 20, bottom: 32 },
      xAxis: { type: 'category', data: rows.map((r) => r.date.slice(5)), axisLabel: { fontSize: 11 } },
      yAxis: [
        { type: 'value', name: '题次', minInterval: 1, nameTextStyle: { fontSize: 11 } },
        { type: 'value', name: '正确率', min: 0, max: 100, nameTextStyle: { fontSize: 11 }, axisLabel: { formatter: '{value}%', fontSize: 11 } },
      ],
      series: [
        {
          name: '作答次数',
          type: 'bar',
          barMaxWidth: 20,
          itemStyle: { color: '#93c5fd', borderRadius: [3, 3, 0, 0] },
          data: rows.map((r) => r.attempts),
        },
        {
          name: '正确率%',
          type: 'line',
          yAxisIndex: 1,
          smooth: true,
          symbolSize: 6,
          itemStyle: { color: '#059669' },
          lineStyle: { width: 2.2, color: '#059669' },
          data: rows.map((r) => (r.accuracy == null ? null : Math.round(r.accuracy * 100))),
        },
      ],
    };
  }, [feedback?.progress?.daily_trend]);

  const errorCauseOption = useMemo<EChartsOption>(() => {
    const causes = feedback?.error_causes || [];
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 78, right: 36, top: 8, bottom: 16 },
      xAxis: { type: 'value', minInterval: 1 },
      yAxis: { type: 'category', data: causes.map((c) => c.label).reverse(), axisLabel: { fontSize: 12 } },
      series: [{
        type: 'bar',
        barMaxWidth: 18,
        itemStyle: { color: '#f97316', borderRadius: [0, 4, 4, 0] },
        label: {
          show: true,
          position: 'right',
          formatter: (p: any) => {
            const c = causes[causes.length - 1 - p.dataIndex];
            return c ? `${c.count}次 (${Math.round(c.ratio * 100)}%)` : '';
          },
          fontSize: 11,
        },
        data: causes.map((c) => c.count).reverse(),
      }],
    };
  }, [feedback?.error_causes]);

  const filteredKps = useMemo(() => {
    const list = feedback?.knowledge_points || [];
    if (kpFilter === 'focus') {
      return list.filter((p) => p.stage === 'weak' || p.stage === 'forgetting' || p.stage === 'developing');
    }
    if (kpFilter === 'strong') {
      return list.filter((p) => p.stage === 'consolidated');
    }
    return list;
  }, [feedback?.knowledge_points, kpFilter]);

  if (!loggedIn) {
    return (
      <div className="app-page app-page-wide">
        <Card title={<Space><UserOutlined />个人学情</Space>}>
          <EmptyBlock description="登录后即可查看个人学情反馈、知识图谱热力、遗忘预警与学习成果。">
            <Text type="secondary">请通过顶栏头像菜单登录。</Text>
          </EmptyBlock>
        </Card>
      </div>
    );
  }

  if (userLoading) {
    return (
      <div className="app-page app-page-wide">
        <LoadingBlock tip="正在读取账号信息…" />
      </div>
    );
  }

  const displayName = user?.display_name || user?.username || '未登录';

  const saveName = async () => {
    const name = nameDraft.trim();
    if (!name) {
      message.warning('姓名不能为空');
      return;
    }
    setSavingProfile(true);
    try {
      await updateProfile(name);
      await loadUser();
      await refreshUser();
      setEditingName(false);
      message.success('姓名已更新');
    } catch (e) {
      const err = e as { response?: { data?: { message?: string } }; message?: string };
      message.error(err?.response?.data?.message || err?.message || '姓名更新失败');
    } finally {
      setSavingProfile(false);
    }
  };

  const changeAvatar = async (file: File) => {
    setSavingProfile(true);
    try {
      await uploadAvatar(file);
      await loadUser();
      await refreshUser();
      message.success('头像已更新');
    } catch (e) {
      const err = e as { message?: string };
      message.error(err?.message || '头像上传失败');
    } finally {
      setSavingProfile(false);
    }
  };

  const isTeacher = user?.role === 'teacher' || user?.role === 'admin';
  const summary = feedback?.summary;
  const distribution = summary?.distribution;
  const evaluated = (summary?.total_points ?? 0) - (distribution?.pending ?? 0);
  const totalAttempts = quizStats?.total_attempts ?? 0;
  const accuracyText = quizStats?.accuracy == null ? '—' : Math.round(quizStats.accuracy * 100) + '%';
  const kpList = feedback?.knowledge_points || [];
  const forgettingAlerts = feedback?.forgetting_alerts || [];
  const teacherFeedback = feedback?.teacher_feedback || [];
  const errorCauses = feedback?.error_causes || [];
  const progress = feedback?.progress;
  const accDelta = progress?.accuracy_delta;
  const hasLearningData = totalAttempts > 0 || kpList.length > 0 || wrongs.length > 0 || teacherFeedback.length > 0;
  const currentSubjectName = subjects.find((s) => s.subject_id === currentSubjectId)?.name || '未选学科';

  const signalContent = () => {
    if (signalTab === 'teacher') {
      if (teacherFeedback.length === 0) {
        return <EmptyBlock description="暂无教师批改或改判评语。作业批改后的评语会自动回流到这里。" />;
      }
      return (
        <List
          size="small"
          dataSource={teacherFeedback.slice(0, 4)}
          renderItem={(item) => (
            <List.Item
              style={{ paddingInline: 0 }}
              actions={[
                !item.teacher_is_correct ? (
                  <Button key="fix" size="small" type="link" style={{ padding: 0 }}
                          onClick={() => jumpStudy(item.knowledge_point, 'explain')}>
                    按评语补课
                  </Button>
                ) : null,
              ].filter(Boolean)}
            >
              <List.Item.Meta
                title={
                  <Space size={4} wrap>
                    <Tag color={item.teacher_is_correct ? 'success' : 'error'}>
                      教师判{item.teacher_is_correct ? '对' : '错'}
                    </Tag>
                    <Tag color="purple">{item.knowledge_point}</Tag>
                  </Space>
                }
                description={
                  <div style={{ fontSize: 12 }}>
                    <Text ellipsis={{ tooltip: item.teacher_reason || item.stem }} style={{ maxWidth: '100%' }}>
                      {item.teacher_reason || item.stem || '（无附加说明）'}
                    </Text>
                    {item.teacher_graded_at && (
                      <div style={{ color: '#8c8c8c', fontSize: 11 }}>{formatTime(item.teacher_graded_at)}</div>
                    )}
                  </div>
                }
              />
            </List.Item>
          )}
        />
      );
    }

    if (signalTab === 'causes') {
      if (errorCauses.length === 0) {
        return <EmptyBlock description="答错后勾选「概念混淆 / 公式记错 / 计算失误」等错因，这里会自动生成错因画像与建议。" />;
      }
      return (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <EChart option={errorCauseOption} height={160} ariaLabel="个人错因分布条形图" />
          {errorCauses[0]?.advice && (
            <Alert
              type="info"
              showIcon
              message={`高频错因：${errorCauses[0].label}（${Math.round(errorCauses[0].ratio * 100)}%）`}
              description={errorCauses[0].advice}
            />
          )}
        </Space>
      );
    }

    if (forgettingAlerts.length === 0) {
      return <EmptyBlock description="当前没有进入遗忘预警的知识点，按计划复习即可。" />;
    }
    return (
      <List
        size="small"
        dataSource={forgettingAlerts.slice(0, 4)}
        renderItem={(a) => (
          <List.Item
            style={{ paddingInline: 0 }}
            actions={[
              <Button key="rev" size="small" type="primary" ghost
                      onClick={() => jumpStudy(a.knowledge_point, 'review')}>
                复测
              </Button>,
            ]}
          >
            <List.Item.Meta
              title={
                <Space size={4} wrap>
                  <Text strong style={{ fontSize: 13 }}>{a.knowledge_point}</Text>
                  {a.overdue && <Tag color="error">已到期</Tag>}
                  {a.retrievability != null && (
                    <Tag color={a.retrievability < 0.65 ? 'error' : 'warning'}>
                      保持率 {Math.round(a.retrievability * 100)}%
                    </Tag>
                  )}
                </Space>
              }
              description={
                <Text type="secondary" style={{ fontSize: 11 }}>
                  建议复测：{a.due_date || '尽快'} · 已复习 {a.reps} 次
                </Text>
              }
            />
          </List.Item>
        )}
      />
    );
  };

  const actionBtnText = (kind?: string): string => {
    if (kind === 'prereq') return '先补前置';
    if (kind === 'review') return '立即复测';
    if (kind === 'explain') return 'AI 精讲';
    return '立即巩固练习';
  };

  // 与知识图谱热力及知识点明细完全一致的四色体系
  const distItems = distribution ? [
    { key: 'strong', label: '掌握较好', count: distribution.strong, dot: '#10b981', bg: '#ecfdf5', border: '#a7f3d0', text: '#047857', filter: 'strong' as const },
    { key: 'medium', label: '巩固中', count: distribution.medium, dot: '#3b82f6', bg: '#eff6ff', border: '#bfdbfe', text: '#1d4ed8', filter: 'focus' as const },
    { key: 'weak', label: '薄弱', count: distribution.weak, dot: '#ef4444', bg: '#fef2f2', border: '#fecaca', text: '#b91c1c', filter: 'focus' as const },
    { key: 'pending', label: '待评估', count: distribution.pending, dot: '#94a3b8', bg: '#f8fafc', border: '#e2e8f0', text: '#475569', filter: 'all' as const },
  ] : [];

  const diagTone = (() => {
    if ((feedback?.root_causes?.length || 0) > 0 || (distribution?.weak || 0) > 0) {
      return { bg: '#fef2f2', border: '#fecaca', accent: '#ef4444', text: '#991b1b', sub: '#b91c1c', badge: '需优先巩固' };
    }
    if (forgettingAlerts.length > 0) {
      return { bg: '#fffbeb', border: '#fde68a', accent: '#f59e0b', text: '#92400e', sub: '#b45309', badge: '遗忘预警' };
    }
    if ((distribution?.strong || 0) > 0) {
      return { bg: '#ecfdf5', border: '#a7f3d0', accent: '#10b981', text: '#065f46', sub: '#047857', badge: '状态良好' };
    }
    return { bg: '#eff6ff', border: '#bfdbfe', accent: '#3b82f6', text: '#1e40af', sub: '#1d4ed8', badge: '巩固推进中' };
  })();

  return (
    <div className="app-page app-page-wide">
      {/* 顶部紧凑工具条：身份 + 学科选择 + 周期 + 一键跳转 */}
      <Card size="small" styles={{ body: { padding: '12px 16px' } }} style={{ marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
          <Space size={12} align="center" wrap>
            <Tooltip title="点击更换头像">
              <span>
                <UserAvatar user={user} size={42} onClick={() => avatarInputRef.current?.click()} />
              </span>
            </Tooltip>
            <input
              ref={avatarInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) void changeAvatar(file);
              }}
            />
            <div>
              <Space size={6} align="center" wrap>
                <Title level={4} style={{ margin: 0, fontSize: 17, lineHeight: 1.3 }}>
                  {displayName} 的个人学情
                </Title>
                <Tag color={user?.role === 'admin' ? 'gold' : isTeacher ? 'blue' : 'default'} style={{ margin: 0 }}>
                  {ROLE_LABEL[user?.role || 'student'] || '学生'}
                </Tag>
              </Space>
              <Text type="secondary" style={{ fontSize: 12 }}>
                当前学科：{currentSubjectName} · 真实作答、图谱溯因与遗忘曲线统一汇总
              </Text>
            </div>
          </Space>

          <Space size={8} wrap>
            <Select
              style={{ width: 200 }}
              placeholder={subjects.length ? '选择学科' : '还没有学科'}
              value={currentSubjectId || undefined}
              onChange={handleSubjectChange}
              options={subjects.map((s) => ({ value: s.subject_id, label: s.name }))}
              notFoundContent="先加入或创建课程"
            />
            <Segmented
              size="middle"
              value={days}
              onChange={(v) => setDays(v as number)}
              options={[
                { label: '7天', value: 7 },
                { label: '30天', value: 30 },
                { label: '90天', value: 90 },
              ]}
            />
            <Button type="primary" icon={<RobotOutlined />} onClick={() => navigate('/student')}>
              进入 AI 伴学
            </Button>
            {isTeacher && (
              <Button onClick={() => navigate('/teacher')}>教师工作台</Button>
            )}
            <Tooltip title="刷新数据">
              <Button icon={<ReloadOutlined />} onClick={loadData} loading={dataLoading} />
            </Tooltip>
            <Tooltip title="系统设置">
              <Button icon={<SettingOutlined />} onClick={() => navigate('/settings')} />
            </Tooltip>
          </Space>
        </div>
      </Card>

      {dataError && <div style={{ marginBottom: 12 }}><ErrorAlert message={dataError} onRetry={loadData} /></div>}

      {dataLoading ? (
        <Card><LoadingBlock tip="正在汇总学情反馈…" /></Card>
      ) : !currentSubjectId ? (
        <Card>
          <EmptyBlock description="还没有选择学科。先加入课程或上传个人资料，学情反馈与知识图谱会自动生效。">
            <Space>
              <Button type="primary" onClick={() => navigate('/student/materials')}>去资料库 / 课程</Button>
              <Button onClick={() => navigate('/student')}>去 AI 伴学</Button>
            </Space>
          </EmptyBlock>
        </Card>
      ) : (
        <>
          {/* 首屏核心带：左（诊断结论 + 下一步 CTA） + 右（4 项关键指标，左右充分利用） */}
          <Row gutter={[14, 14]} style={{ marginBottom: 14 }}>
            <Col xs={24} xl={11}>
              <Card
                size="small"
                style={{ height: '100%' }}
                styles={{ body: { padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10, height: '100%' } }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
                  <Space size={6} style={{ flexShrink: 0 }}>
                    <BulbOutlined style={{ color: 'var(--color-primary)' }} />
                    <Text strong style={{ whiteSpace: 'nowrap' }}>学情诊断与下一步建议</Text>
                    {hasLearningData && (
                      <span style={{
                        fontSize: 11, fontWeight: 600, padding: '1px 7px', borderRadius: 99,
                        background: diagTone.bg, color: diagTone.sub, border: `1px solid ${diagTone.border}`,
                        whiteSpace: 'nowrap',
                      }}>
                        {diagTone.badge}
                      </span>
                    )}
                  </Space>
                  {feedback?.next_action && (
                    <Tooltip title={feedback.next_action.label}>
                      <Button
                        type="primary"
                        size="small"
                        style={{ flexShrink: 0 }}
                        onClick={() => jumpStudy(
                          feedback.next_action!.concept,
                          (feedback.next_action!.kind as 'explain' | 'practice' | 'prereq' | 'review') || 'practice',
                        )}
                      >
                        {actionBtnText(feedback.next_action.kind)}
                      </Button>
                    </Tooltip>
                  )}
                </div>

                {!hasLearningData ? (
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '6px 0' }}>
                    <Text type="secondary" style={{ fontSize: 13 }}>
                      还没有作答记录。去「AI 伴学」提一个问题或做 2 道练习题，这里会立即生成学情画像。
                    </Text>
                    <Button type="primary" size="small" style={{ flexShrink: 0 }} onClick={() => navigate('/student')}>开始学习</Button>
                  </div>
                ) : (
                  <>
                    <div
                      style={{
                        background: diagTone.bg,
                        border: `1px solid ${diagTone.border}`,
                        borderLeft: `3.5px solid ${diagTone.accent}`,
                        borderRadius: 10,
                        padding: '10px 12px',
                      }}
                    >
                      <div style={{ fontSize: 13, fontWeight: 600, color: diagTone.text, lineHeight: 1.55, wordBreak: 'break-word' }}>
                        {feedback?.headline || '按当前节奏稳步巩固中'}
                      </div>
                      {feedback?.next_action?.reason && (
                        <div style={{ fontSize: 12, color: diagTone.sub, marginTop: 3, lineHeight: 1.45 }}>
                          {feedback.next_action.reason}
                        </div>
                      )}
                    </div>
                    {distItems.length > 0 && (
                      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginTop: 'auto' }}>
                        {distItems.map((item) => (
                          <button
                            key={item.key}
                            type="button"
                            onClick={() => { setActiveTab('overview'); setKpFilter(item.filter); }}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: 6,
                              padding: '3px 10px',
                              borderRadius: 99,
                              background: item.bg,
                              border: `1px solid ${item.border}`,
                              color: item.text,
                              fontSize: 12,
                              fontWeight: item.count > 0 ? 600 : 400,
                              opacity: item.count > 0 ? 1 : 0.65,
                              cursor: 'pointer',
                              lineHeight: 1.4,
                            }}
                          >
                            <span style={{ width: 7, height: 7, borderRadius: '50%', background: item.dot, flexShrink: 0 }} />
                            <span>{item.label}</span>
                            <span>{item.count}</span>
                          </button>
                        ))}
                        {(progress?.recovered_kps?.length || 0) > 0 && (
                          <Tag color="cyan" style={{ margin: 0 }}>
                            已转稳：{progress!.recovered_kps.slice(0, 2).join('、')}
                          </Tag>
                        )}
                      </div>
                    )}
                  </>
                )}
              </Card>
            </Col>

            <Col xs={24} xl={13}>
              <Row gutter={[12, 12]} style={{ height: '100%' }}>
                <Col xs={12} sm={6}>
                  <Card size="small" style={{ height: '100%' }} styles={{ body: { padding: '14px 16px' } }}>
                    <Statistic title="已评估知识点" value={evaluated} suffix={`/ ${summary?.total_points ?? 0}`} />
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      薄弱 {distribution?.weak ?? 0} · 遗忘预警 {forgettingAlerts.length}
                    </Text>
                  </Card>
                </Col>
                <Col xs={12} sm={6}>
                  <Card size="small" style={{ height: '100%' }} styles={{ body: { padding: '14px 16px' } }}>
                    <Statistic title="累计作答" value={totalAttempts} suffix="次" />
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      待清错题 {wrongs.length} 道
                    </Text>
                  </Card>
                </Col>
                <Col xs={12} sm={6}>
                  <Card size="small" style={{ height: '100%' }} styles={{ body: { padding: '14px 16px' } }}>
                    <Statistic title="总体正确率" value={accuracyText} />
                    {accDelta != null ? (
                      <Tag color={accDelta >= 0 ? 'success' : 'error'} style={{ marginTop: 2 }}>
                        近7天 {accDelta >= 0 ? '+' : ''}{Math.round(accDelta * 100)}%
                      </Tag>
                    ) : (
                      <Text type="secondary" style={{ fontSize: 11 }}>近 {days} 天统计</Text>
                    )}
                  </Card>
                </Col>
                <Col xs={12} sm={6}>
                  <Card size="small" style={{ height: '100%' }} styles={{ body: { padding: '14px 16px' } }}>
                    <Statistic
                      title="复测转对"
                      value={progress?.recovered_questions ?? 0}
                      prefix={<RiseOutlined style={{ color: '#059669' }} />}
                      suffix="题"
                    />
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      学习成果 {artifacts.length} 项
                    </Text>
                  </Card>
                </Col>
              </Row>
            </Col>
          </Row>

          {/* 4 模块标签区：把原本 3500px 的纵向长页拆为 4 个一屏可读的清晰模块 */}
          <Card size="small" styles={{ body: { padding: '8px 16px 16px' } }}>
            <Tabs
              activeKey={activeTab}
              onChange={setActiveTab}
              items={[
                {
                  key: 'overview',
                  label: (
                    <Space size={6}>
                      <ExperimentOutlined />
                      <span>学情诊断</span>
                    </Space>
                  ),
                  children: (
                    <Row gutter={[16, 16]}>
                      {/* 左栏：知识点掌握与行动表 */}
                      <Col xs={24} lg={14}>
                        <Card
                          size="small"
                          type="inner"
                          title="知识点掌握与巩固明细"
                          extra={
                            <Segmented
                              size="small"
                              value={kpFilter}
                              onChange={(v) => setKpFilter(v as 'all' | 'focus' | 'strong')}
                              options={[
                                { label: `全部 (${kpList.length})`, value: 'all' },
                                { label: '优先巩固', value: 'focus' },
                                { label: '已巩固', value: 'strong' },
                              ]}
                            />
                          }
                          style={{ height: '100%' }}
                        >
                          {filteredKps.length === 0 ? (
                            <EmptyBlock description="当前筛选下暂无知识点数据。去 AI 伴学完成 2~3 道题后会自动评估。" />
                          ) : (
                            <Table<StudentFeedbackPoint>
                              rowKey={(r) => r.knowledge_point_id || r.knowledge_point}
                              size="small"
                              dataSource={filteredKps}
                              pagination={{ pageSize: 7, hideOnSinglePage: true }}
                              scroll={{ x: 640 }}
                              columns={[
                                {
                                  title: '知识点',
                                  dataIndex: 'knowledge_point',
                                  render: (name: string, r: StudentFeedbackPoint) => (
                                    <div>
                                      <Text strong style={{ fontSize: 13 }}>{name}</Text>
                                      {r.prerequisite_diagnosis?.root_cause_type === 'prerequisite_gap' && (
                                        <div style={{ fontSize: 11, color: '#b45309' }}>
                                          前置薄弱：{(r.prerequisite_diagnosis.weak_prerequisites || []).map((p) => p.concept).slice(0, 2).join('、')}
                                        </div>
                                      )}
                                    </div>
                                  ),
                                },
                                {
                                  title: '状态',
                                  dataIndex: 'stage',
                                  width: 92,
                                  render: (s: string, r: StudentFeedbackPoint) => (
                                    <Tag color={STAGE_COLOR[s] || 'default'}>{r.stage_label}</Tag>
                                  ),
                                },
                                {
                                  title: '掌握 / 记忆',
                                  dataIndex: 'mastery_level',
                                  width: 150,
                                  render: (v: number | null, r: StudentFeedbackPoint) => (
                                    v == null ? (
                                      <Text type="secondary" style={{ fontSize: 12 }}>待评估</Text>
                                    ) : (
                                      <Space size={6}>
                                        <Progress
                                          percent={Math.round(v * 100)}
                                          size="small"
                                          style={{ width: 58, marginBottom: 0 }}
                                          showInfo={false}
                                          strokeColor={v >= 0.7 ? '#10b981' : v >= 0.5 ? '#f59e0b' : '#ef4444'}
                                        />
                                        <Text style={{ fontSize: 12 }}>{Math.round(v * 100)}%</Text>
                                        {r.retrievability != null && (
                                          <Text type={r.retrievability < 0.78 ? 'warning' : 'secondary'} style={{ fontSize: 11 }} title="FSRS 记忆保持率">
                                            忆{Math.round(r.retrievability * 100)}%
                                          </Text>
                                        )}
                                      </Space>
                                    )
                                  ),
                                },
                                {
                                  title: '对/错',
                                  width: 72,
                                  render: (_: unknown, r: StudentFeedbackPoint) => (
                                    <Text style={{ fontSize: 12 }}>{r.correct_count}/{r.wrong_count}</Text>
                                  ),
                                },
                                {
                                  title: '直达行动',
                                  width: 175,
                                  render: (_: unknown, r: StudentFeedbackPoint) => {
                                    const pre = r.prerequisite_diagnosis?.weak_prerequisites?.[0]?.concept;
                                    return (
                                      <Space size={2}>
                                        {pre && (
                                          <Button size="small" type="link" danger style={{ padding: '0 4px' }}
                                                  onClick={() => jumpStudy(pre, 'prereq')}>
                                            补前置
                                          </Button>
                                        )}
                                        <Button size="small" type="link" icon={<BookOutlined />} style={{ padding: '0 4px' }}
                                                onClick={() => jumpStudy(r.knowledge_point, 'explain')}>
                                          精讲
                                        </Button>
                                        <Button size="small" type="link" icon={<FormOutlined />} style={{ padding: '0 4px' }}
                                                onClick={() => jumpStudy(r.knowledge_point, 'practice')}>
                                          练题
                                        </Button>
                                      </Space>
                                    );
                                  },
                                },
                              ]}
                            />
                          )}
                        </Card>
                      </Col>

                      {/* 右栏：趋势图 + 预警/错因/教师评语三合一信号卡 */}
                      <Col xs={24} lg={10}>
                        <Space direction="vertical" size={14} style={{ width: '100%' }}>
                          <Card size="small" type="inner" title={`近 ${days} 天作答与正确率趋势`}>
                            {(progress?.daily_trend?.length || 0) > 0 ? (
                              <EChart option={trendOption} height={190} ariaLabel="个人每日作答量与正确率趋势图" />
                            ) : (
                              <EmptyBlock description={`近 ${days} 天暂无作答趋势数据。`} />
                            )}
                          </Card>

                          <Card
                            size="small"
                            type="inner"
                            title={
                              <Segmented
                                size="small"
                                value={signalTab}
                                onChange={(v) => setSignalTab(v as 'forgetting' | 'causes' | 'teacher')}
                                options={[
                                  {
                                    value: 'forgetting',
                                    label: (
                                      <Space size={4}>
                                        <ClockCircleOutlined />
                                        <span>遗忘预警</span>
                                        {forgettingAlerts.length > 0 && <Badge count={forgettingAlerts.length} size="small" />}
                                      </Space>
                                    ),
                                  },
                                  {
                                    value: 'causes',
                                    label: (
                                      <Space size={4}>
                                        <WarningOutlined />
                                        <span>错因画像</span>
                                        {errorCauses.length > 0 && <Badge count={errorCauses.length} color="#f97316" size="small" />}
                                      </Space>
                                    ),
                                  },
                                  {
                                    value: 'teacher',
                                    label: (
                                      <Space size={4}>
                                        <MessageOutlined />
                                        <span>教师评语</span>
                                        {teacherFeedback.length > 0 && <Badge count={teacherFeedback.length} color="#2563eb" size="small" />}
                                      </Space>
                                    ),
                                  },
                                ]}
                              />
                            }
                          >
                            {signalContent()}
                          </Card>
                        </Space>
                      </Col>
                    </Row>
                  ),
                },
                {
                  key: 'graph',
                  label: (
                    <Space size={6}>
                      <ApiOutlined />
                      <span>知识图谱热力</span>
                      {(feedback?.root_causes?.length || 0) > 0 && (
                        <Badge count={feedback!.root_causes!.length} size="small" />
                      )}
                    </Space>
                  ),
                  children: (
                    <React.Suspense fallback={<LoadingBlock tip="加载知识图谱…" />}>
                      <KnowledgeGraph subjectId={currentSubjectId} mode="student" />
                    </React.Suspense>
                  ),
                },
                {
                  key: 'assets',
                  label: (
                    <Space size={6}>
                      <FileTextOutlined />
                      <span>错题与成果</span>
                      {wrongs.length > 0 && <Badge count={wrongs.length} color="#f59e0b" size="small" />}
                    </Space>
                  ),
                  children: (
                    <Row gutter={[16, 16]}>
                      <Col xs={24} lg={12}>
                        <Card
                          size="small"
                          type="inner"
                          title={<Space><WarningOutlined />待清错题</Space>}
                          extra={
                            <Button type="link" size="small" style={{ padding: 0 }} onClick={() => navigate('/student/tasks')}>
                              全部错题与复习 <RightOutlined />
                            </Button>
                          }
                          style={{ height: '100%' }}
                        >
                          {wrongs.length === 0 ? (
                            <EmptyBlock description="暂无待清错题。">
                              <Button size="small" onClick={() => navigate('/student')}>去做几道练习</Button>
                            </EmptyBlock>
                          ) : (
                            <List
                              size="small"
                              dataSource={wrongs.slice(0, 6)}
                              renderItem={(w) => (
                                <List.Item
                                  actions={w.knowledge_point ? [
                                    <Button key="ex" size="small" type="link" style={{ padding: 0 }}
                                            onClick={() => jumpStudy(w.knowledge_point!, 'explain')}>
                                      精讲
                                    </Button>,
                                  ] : undefined}
                                >
                                  <List.Item.Meta
                                    title={<Text ellipsis={{ tooltip: w.stem }} style={{ fontSize: 13 }}>{w.stem}</Text>}
                                    description={
                                      <Space size={4} wrap>
                                        {w.knowledge_point && <Tag>{w.knowledge_point}</Tag>}
                                        {w.error_label && <Tag color="orange">{w.error_label}</Tag>}
                                        {w.teacher_regraded && <Tag color="blue">含教师评语</Tag>}
                                      </Space>
                                    }
                                  />
                                </List.Item>
                              )}
                            />
                          )}
                        </Card>
                      </Col>

                      <Col xs={24} lg={12}>
                        <Space direction="vertical" size={14} style={{ width: '100%' }}>
                          <Card
                            size="small"
                            type="inner"
                            title={<Space><FileTextOutlined />最近学习成果</Space>}
                            extra={<Text type="secondary" style={{ fontSize: 12 }}>{artifacts.length} 项</Text>}
                          >
                            {artifacts.length === 0 ? (
                              <EmptyBlock description="在 AI 伴学里整理笔记或生成学习路线后会保存在这里。" />
                            ) : (
                              <List
                                size="small"
                                dataSource={artifacts.slice(0, 5)}
                                renderItem={(a) => (
                                  <List.Item>
                                    <List.Item.Meta
                                      title={
                                        <Space size={6} wrap>
                                          <Tag color="green">{a.kind_label || a.kind}</Tag>
                                          <Text ellipsis={{ tooltip: a.title }} style={{ maxWidth: 280 }}>{a.title}</Text>
                                          {a.edited && <Tag>已编辑</Tag>}
                                          {a.stale && <Tag color="orange">资料已更新</Tag>}
                                        </Space>
                                      }
                                      description={<Text type="secondary" style={{ fontSize: 11 }}>更新于 {formatTime(a.updated_at)}</Text>}
                                    />
                                  </List.Item>
                                )}
                              />
                            )}
                          </Card>

                          <Card
                            size="small"
                            type="inner"
                            title={<Space><ExperimentOutlined />学习记忆</Space>}
                            extra={
                              <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setActiveTab('account')}>
                                调整偏好
                              </Button>
                            }
                          >
                            {memories.length === 0 ? (
                              <EmptyBlock description="助手还没有形成学习记忆，多提问或设置讲解偏好后会出现在这里。" />
                            ) : (
                              <List
                                size="small"
                                dataSource={memories.slice(0, 5)}
                                renderItem={(m) => (
                                  <List.Item>
                                    <List.Item.Meta
                                      title={
                                        <Space size={6} wrap>
                                          <Tag>{m.type_label || m.memory_type}</Tag>
                                          <Text strong>{m.key}</Text>
                                          <Text type="secondary" style={{ fontSize: 11 }}>
                                            {MEMORY_SOURCE[m.source] || m.source}
                                          </Text>
                                        </Space>
                                      }
                                      description={<Text style={{ fontSize: 12 }}>{m.value}</Text>}
                                    />
                                  </List.Item>
                                )}
                              />
                            )}
                          </Card>
                        </Space>
                      </Col>
                    </Row>
                  ),
                },
                {
                  key: 'account',
                  label: (
                    <Space size={6}>
                      <UserOutlined />
                      <span>账号与偏好</span>
                    </Space>
                  ),
                  children: (
                    <Row gutter={[16, 16]}>
                      <Col xs={24} lg={12}>
                        <Space direction="vertical" size={14} style={{ width: '100%' }}>
                          <Card size="small" type="inner" title="个人资料">
                            <Space align="center" size={16} wrap>
                              <UserAvatar user={user} size={56} onClick={() => avatarInputRef.current?.click()} />
                              <Space direction="vertical" size={4}>
                                <Space size={8} wrap>
                                  {editingName ? (
                                    <>
                                      <Input
                                        size="small"
                                        value={nameDraft}
                                        maxLength={40}
                                        autoFocus
                                        style={{ width: 180 }}
                                        onChange={(e) => setNameDraft(e.target.value)}
                                        onPressEnter={() => void saveName()}
                                      />
                                      <Button size="small" type="primary" loading={savingProfile} onClick={() => void saveName()}>保存</Button>
                                      <Button size="small" disabled={savingProfile} onClick={() => setEditingName(false)}>取消</Button>
                                    </>
                                  ) : (
                                    <>
                                      <Text strong style={{ fontSize: 16 }}>{displayName}</Text>
                                      <Button
                                        type="link"
                                        size="small"
                                        icon={<EditOutlined />}
                                        style={{ padding: 0 }}
                                        onClick={() => { setNameDraft(displayName); setEditingName(true); }}
                                      >
                                        改名
                                      </Button>
                                    </>
                                  )}
                                </Space>
                                <Text type="secondary" style={{ fontSize: 12 }}>
                                  账号：{user?.username || '—'} · 身份：{ROLE_LABEL[user?.role || 'student'] || '学生'}
                                </Text>
                                <Button
                                  size="small"
                                  disabled={savingProfile}
                                  onClick={() => avatarInputRef.current?.click()}
                                >
                                  更换头像
                                </Button>
                              </Space>
                            </Space>
                          </Card>
                          <PersonalizationSettings />
                        </Space>
                      </Col>

                      <Col xs={24} lg={12}>
                        <Space direction="vertical" size={14} style={{ width: '100%' }}>
                          {isTeacher ? (
                            <>
                              <TeacherActivation />
                              <SystemStatusPanel />
                            </>
                          ) : (
                            <Card size="small" type="inner" title="更多设置">
                              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                                <Text type="secondary" style={{ fontSize: 13 }}>
                                  导航风格（顶栏 / 左轮）、语音输入、教师邀请码开通与系统运行状态集中在「设置」页管理。
                                </Text>
                                <Button icon={<SettingOutlined />} onClick={() => navigate('/settings')}>
                                  打开系统设置
                                </Button>
                              </Space>
                            </Card>
                          )}
                        </Space>
                      </Col>
                    </Row>
                  ),
                },
              ]}
            />
          </Card>
        </>
      )}
    </div>
  );
};

export default ProfilePage;
