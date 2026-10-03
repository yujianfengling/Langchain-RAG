import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Badge, Button, Card, DatePicker, Descriptions, Divider, Empty, List,
  Progress, Space, Spin, Statistic, Table, Tabs, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  CalendarOutlined, CheckCircleOutlined, ClockCircleOutlined, ReloadOutlined,
  ReadOutlined, ExclamationCircleOutlined, RocketOutlined,
  EyeOutlined, EyeInvisibleOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import SubjectSelector from '../components/SubjectSelector';
import LearningMemoryPanel from '../components/LearningMemoryPanel';
import StudentCourses from '../components/StudentCourses';
import { listMyCourses } from '../api/studentCourses';
import PlanBudgetPanel from '../components/PlanBudgetPanel';
import { isLoggedIn } from '../api/auth';
import { AUTH_CHANGED_EVENT } from '../components/UserAuth';
import {
  StudentTask, WrongQuestion, ReviewTask, MasteryPoint,
  LearningHome, getHome, completeTask, rescheduleTask,
  getWrongQuestions, getWrongQuestionDetail, getReviews, getMastery,
} from '../api/student';

const { Title, Text, Paragraph } = Typography;

const TASK_TYPE_META: Record<string, { color: string; label: string }> = {
  READ: { color: 'cyan', label: '阅读' },
  REVIEW: { color: 'purple', label: '复习' },
  QUIZ: { color: 'green', label: '自测' },
  PRACTICE: { color: 'orange', label: '练习' },
};

const MASTERY_META: Record<string, { color: string; label: string }> = {
  pending: { color: 'default', label: '待评估' },
  estimated: { color: 'processing', label: '暂估' },
};

/** 掌握度展示：无证据时显示"待评估"，不显示 0% */
const masteryText = (p: MasteryPoint) =>
  p.mastery_level == null ? '待评估' : `${Math.round(p.mastery_level * 100)}%`;

const masteryPercent = (p: MasteryPoint) =>
  p.mastery_level == null ? 0 : Math.round(p.mastery_level * 100);

const masteryColor = (p: MasteryPoint) => {
  if (p.mastery_level == null) return '#94a3b8';
  if (p.mastery_level >= 0.7) return '#059669';
  if (p.mastery_level >= 0.4) return '#d97706';
  return '#dc2626';
};

const StudentPage: React.FC = () => {
  const navigate = useNavigate();
  const [subjectId, setSubjectId] = useState('');
  const [loggedIn, setLoggedIn] = useState(isLoggedIn());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [home, setHome] = useState<LearningHome | null>(null);
  const [buckets, setBuckets] = useState<{ today: StudentTask[]; overdue: StudentTask[] }>({ today: [], overdue: [] });
  const [wrong, setWrong] = useState<WrongQuestion[]>([]);
  /** 错题答案默认不下发（answers_included=false），点「查看答案」后才拉取 */
  const [wrongDetails, setWrongDetails] = useState<Record<string, any>>({});
  const [revealingId, setRevealingId] = useState<string | null>(null);
  /** 已展开答案的题目。**默认全部收起**——即使后端已下发（此前查看过），
   *  也要学生自己点开，避免答案在错题本里一直摊着。 */
  const [expandedAnswers, setExpandedAnswers] = useState<Record<string, boolean>>({});
  const [reviews, setReviews] = useState<ReviewTask[]>([]);
  const [mastery, setMastery] = useState<MasteryPoint[]>([]);
  const [busyTask, setBusyTask] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('today');

  useEffect(() => {
    const sync = () => setLoggedIn(isLoggedIn());
    window.addEventListener('storage', sync);
    window.addEventListener(AUTH_CHANGED_EVENT, sync);
    return () => {
      window.removeEventListener('storage', sync);
      window.removeEventListener(AUTH_CHANGED_EVENT, sync);
    };
  }, []);

  /** 加载全部学生端数据（分别容错，单个接口失败不影响其他区域） */
  const loadAll = useCallback(async (sid?: string) => {
    if (!isLoggedIn()) {
      setError(null);
      setHome(null);
      return;
    }
    setLoading(true);
    setError(null);
    const subject = sid || undefined;

    const results = await Promise.allSettled([
      getHome(subject),
      getWrongQuestions(subject),
      getReviews({ subject_id: subject, status: 'PENDING' }),
      subject ? getMastery(subject) : Promise.resolve({ data: { summary: null, points: [] } } as any),
    ]);

    const [homeRes, wrongRes, revRes, masteryRes] = results;
    if (homeRes.status === 'fulfilled') {
      const data = (homeRes.value as any)?.data;
      setHome(data || null);
      setBuckets({ today: data?.today_tasks || [], overdue: data?.overdue_tasks || [] });
    } else {
      setError('加载学习首页失败，请重试');
    }
    if (wrongRes.status === 'fulfilled') setWrong((wrongRes.value as any)?.data || []);
    if (revRes.status === 'fulfilled') setReviews((revRes.value as any)?.data || []);
    if (masteryRes.status === 'fulfilled') setMastery((masteryRes.value as any)?.data?.points || []);
    setLoading(false);
  }, []);

  /**
   * 查看某道错题的正确答案与解析。
   *
   * 后端会登记"已查看答案"：该题之后的作答记为 after_reveal、不再计入
   * 独立掌握证据——所以答案默认不随错题列表下发，藏在「查看答案」按钮之后。
   */
  const handleRevealWrong = useCallback(async (questionId: string) => {
    setRevealingId(questionId);
    try {
      const res: any = await getWrongQuestionDetail(questionId);
      const data = res?.data;
      if (data) {
        setWrongDetails((prev) => ({ ...prev, [questionId]: data }));
        setExpandedAnswers((prev) => ({ ...prev, [questionId]: true }));
      }
    } catch (e: any) {
      message.error(e?.response?.data?.message || '查看答案失败，请稍后重试');
    } finally {
      setRevealingId(null);
    }
  }, []);

  useEffect(() => {
    loadAll(subjectId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjectId, loggedIn]);

  /**
   * 首次进入时自动选中"我加入的课程"对应的学科。
   *
   * 实测问题（2026-09-27 首次使用走查）：学生做完老师布置的作业后打开「错题本」，
   * 看到的是"暂无错题"——因为学科没选，错题/复习都按"当前学科"查。
   * 学生并不知道要去上方下拉框里选课程知识库，会以为错题本坏了。
   */
  useEffect(() => {
    if (!loggedIn || subjectId) return;
    let alive = true;
    (async () => {
      try {
        const res: any = await listMyCourses();
        const list: any[] = res?.data?.courses || [];
        const sid = list.find((c) => c.subject_id)?.subject_id;
        if (alive && sid) setSubjectId(sid);
      } catch {
        /* 自动选择失败不阻断：学生仍可手动选学科 */
      }
    })();
    return () => { alive = false; };
  }, [loggedIn, subjectId]);

  /** 完成任务：只记录学习行为；测验任务需先有真实作答（后端强制） */
  const handleComplete = async (task: StudentTask) => {
    setBusyTask(task.id);
    try {
      await completeTask(task.id);
      message.success('任务已完成');
      await loadAll(subjectId);
    } catch (e: any) {
      const errType = e?.response?.data?.error_type;
      const msg = e?.response?.data?.message || '完成任务失败';
      if (errType === 'quiz_attempt_required') {
        message.warning(msg);
      } else {
        message.error(msg);
      }
    } finally {
      setBusyTask(null);
    }
  };

  /** 改期：保留原日期与调整记录（后端留痕） */
  const handleReschedule = async (task: StudentTask, dateStr: string) => {
    setBusyTask(task.id);
    try {
      await rescheduleTask(task.id, dateStr, '学生手动调整');
      message.success('计划日期已调整');
      await loadAll(subjectId);
    } catch (e: any) {
      message.error(e?.response?.data?.message || '调整日期失败');
    } finally {
      setBusyTask(null);
    }
  };

  /** 带上下文跳到学习助手（Agent 模式），可带上目标文本 */
  const jumpToAgent = (goal?: string) => {
    navigate('/chat', {
      state: { preset: { mode: 'agent', input: goal || '', subjectId } },
    });
  };

  /** 带上下文跳转到 AI 学习助手（去自测 / 生成笔记） */
  const jumpToChat = (task: StudentTask, mode: 'quiz' | 'smart_note') => {
    const topic = task.knowledge_point || task.title;
    navigate('/chat', {
      state: {
        preset: {
          mode,
          input: topic,
          taskId: task.id,
          subjectId: task.subject_id || subjectId,
        },
      },
    });
  };

  const renderTask = (task: StudentTask, showOverdue = false) => {
    const typeMeta = TASK_TYPE_META[task.task_type] || TASK_TYPE_META.READ;
    const overdue = showOverdue || (task.planned_date && task.planned_date < dayjs().format('YYYY-MM-DD')
      && task.status !== 'COMPLETED');
    return (
      <List.Item
        key={task.id}
        actions={[
          <Button
            key="do"
            size="small"
            type="primary"
            ghost
            icon={task.task_type === 'QUIZ' ? <ExclamationCircleOutlined /> : <ReadOutlined />}
            onClick={() => jumpToChat(task, task.task_type === 'QUIZ' ? 'quiz' : 'smart_note')}
          >
            {task.task_type === 'QUIZ' ? '去自测' : '看笔记'}
          </Button>,
          <Button
            key="done"
            size="small"
            icon={<CheckCircleOutlined />}
            loading={busyTask === task.id}
            disabled={task.status === 'COMPLETED'}
            onClick={() => handleComplete(task)}
          >
            完成
          </Button>,
          <DatePicker
            key="date"
            size="small"
            placeholder="改期"
            value={null}
            disabledDate={(d) => d && d < dayjs().startOf('day')}
            onChange={(d: any) => d && handleReschedule(task, d.format('YYYY-MM-DD'))}
            style={{ width: 118 }}
          />,
        ]}
      >
        <List.Item.Meta
          title={
            <Space size={8} wrap>
              <Tag color={typeMeta.color}>{typeMeta.label}</Tag>
              <Text>{task.title}</Text>
              {overdue && <Tag color="error">已逾期</Tag>}
              {task.source === 'auto_review' && <Tag color="purple">错题自动安排</Tag>}
              {task.status === 'COMPLETED' && <Tag color="success">已完成</Tag>}
              {task.defer_count ? <Tag>已改期 {task.defer_count} 次</Tag> : null}
            </Space>
          }
          description={
            <Space direction="vertical" size={2}>
              {task.description && <Text type="secondary">{task.description}</Text>}
              <Space size={12} wrap>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <CalendarOutlined /> 计划日期：{task.planned_date || '未排期'}
                </Text>
                {task.original_planned_date && task.original_planned_date !== task.planned_date && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    原计划：{task.original_planned_date}
                  </Text>
                )}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <ClockCircleOutlined /> 预计 {task.estimated_minutes} 分钟
                </Text>
                {task.knowledge_point && (
                  <Text type="secondary" style={{ fontSize: 12 }}>知识点：{task.knowledge_point}</Text>
                )}
              </Space>
            </Space>
          }
        />
      </List.Item>
    );
  };

  const returnToPlan = (
    <Button size="small" icon={<CalendarOutlined />} onClick={() => navigate('/learning')}>
      去学习计划
    </Button>
  );

  if (!loggedIn) {
    return (
      <div style={{ padding: 24, maxWidth: 900, margin: '0 auto' }}>
        <Title level={4}>我的学习</Title>
        <Card>
          <Alert
            type="info"
            showIcon
            message="请先登录"
            description="学习首页、错题本、复习安排与掌握度都需要归属到你的账号。请在左侧「登录 / 注册」后查看。"
          />
        </Card>
      </div>
    );
  }

  return (
    <div style={{ padding: 24, maxWidth: 1000, margin: '0 auto' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        <Title level={4} style={{ margin: 0 }}>我的学习</Title>
        <Space wrap>
          <SubjectSelector value={subjectId} onChange={setSubjectId} style={{ width: 220 }} />
          <Tooltip title="用一句话说出目标与限制，学习助手会读取你的真实学习记录来安排">
            <Button type="primary" icon={<RocketOutlined />}
                    onClick={() => jumpToAgent('')}>
              让学习助手安排
            </Button>
          </Tooltip>
          <Button icon={<ReloadOutlined />} onClick={() => loadAll(subjectId)} loading={loading}>
            刷新
          </Button>
        </Space>
      </Space>

      {error && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error}
                       action={<Button size="small" onClick={() => loadAll(subjectId)}>重试</Button>} />}

      {/* 概览 */}
      {home && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Space size={32} wrap>
            <Statistic title="今日任务" value={home.today_tasks.length} suffix="项" />
            <Statistic title="待办合计" value={home.todo_count} suffix="项" />
            <Statistic
              title="逾期"
              value={home.overdue_tasks.length}
              suffix="项"
              valueStyle={{ color: home.overdue_tasks.length ? '#dc2626' : undefined }}
            />
            <Statistic title="待复习" value={home.pending_reviews.length} suffix="个知识点" />
            <Statistic title="错题" value={home.wrong_count} suffix="道" />
            <Statistic
              title="作答道数"
              value={home.quiz_stats.total_attempts}
              suffix={home.quiz_stats.accuracy != null
                ? `（正确率 ${Math.round(home.quiz_stats.accuracy * 100)}%）`
                : '（暂无判分）'}
            />
          </Space>
          {home.last_position?.knowledge_point && (
            <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
              上次学到：<Text strong>{home.last_position.knowledge_point}</Text>
              {home.last_position.at ? `（${home.last_position.at}）` : ''}
            </Paragraph>
          )}
          {home.quiz_stats.pending_self_eval > 0 && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              message={`有 ${home.quiz_stats.pending_self_eval} 道简答题未能自动判分（AI 判分不可用），请对照参考要点自评；自评结果会单独标记，不计入客观判分。`}
            />
          )}
        </Card>
      )}

      <Tabs
        activeKey={activeTab}
        onChange={(key) => {
          setActiveTab(key);
          /**
           * 切到"跟作答结果有关"的标签页时重新拉一次数据。
           * 实测问题：学生刚在「我的课程」里答完作业，切到「错题本」却是空的——
           * 页面数据是进入时加载的，作答之后没有刷新，学生会以为错题本坏了。
           */
          if (['wrong', 'review', 'mastery', 'today'].includes(key)) loadAll(subjectId);
        }}
        items={[
          {
            key: 'courses',
            label: <Badge count={0} size="small" offset={[10, -2]} dot={false} style={{ display: 'none' }}>我的课程</Badge>,
            children: <StudentCourses onJoined={(sid) => { if (sid) setSubjectId(sid); }} />,
          },
          {
            key: 'today',
            label: <Badge count={buckets.today.length} size="small" offset={[10, -2]}>今日任务</Badge>,
            children: loading && !home ? <Spin /> : (
              <Card
                size="small"
                title="今天要完成的任务"
                extra={returnToPlan}
              >
                {buckets.today.length === 0
                  ? (
                    <Empty description="今天没有安排任务。可以在学习计划中调整任务日期，或生成新的计划。">
                      {/* 首次使用的学生最需要知道的是"怎么进课程"，这里给一个明确入口 */}
                      <Button type="primary" onClick={() => setActiveTab('courses')}>
                        去「我的课程」用班级邀请码加入
                      </Button>
                    </Empty>
                  )
                  : <List dataSource={buckets.today} renderItem={(t) => renderTask(t)} />}
              </Card>
            ),
          },
          {
            key: 'overdue',
            label: <Badge count={buckets.overdue.length} size="small" offset={[10, -2]}>逾期</Badge>,
            children: (
              <Card size="small" title="逾期任务（计划日期早于今天且未完成）" extra={returnToPlan}>
                {buckets.overdue.length === 0
                  ? <Empty description="没有逾期任务" />
                  : <List dataSource={buckets.overdue} renderItem={(t) => renderTask(t, true)} />}
              </Card>
            ),
          },
          {
            key: 'review',
            label: <Badge count={reviews.length} size="small" offset={[10, -2]}>复习安排</Badge>,
            children: (
              <Card size="small" title="错题自动安排的复习（同一知识点会合并，不重复堆积）">
                {reviews.length === 0 ? <Empty description="暂无待复习的知识点" /> : (
                  <List
                    dataSource={reviews}
                    renderItem={(r) => (
                      <List.Item
                        actions={[
                          <Button
                            key="go"
                            size="small"
                            type="primary"
                            ghost
                            onClick={() => {
                              if (!subjectId) message.warning('请先在上方选择学科');
                              navigate('/chat', {
                                state: { preset: { mode: 'quiz', input: r.knowledge_point_name, subjectId } },
                              });
                            }}
                          >
                            去复习
                          </Button>,
                        ]}
                      >
                        <List.Item.Meta
                          title={
                            <Space size={8} wrap>
                              <Tag color="purple">复习</Tag>
                              <Text>{r.knowledge_point_name}</Text>
                              <Tag color={r.due_date <= dayjs().format('YYYY-MM-DD') ? 'error' : 'default'}>
                                到期 {r.due_date}
                              </Tag>
                              {!!r.merged_count && r.merged_count > 1 && (
                                <Tooltip title="同一知识点的多次错题已合并到这一条复习任务">
                                  <Tag>已合并 {r.merged_count} 次</Tag>
                                </Tooltip>
                              )}
                            </Space>
                          }
                          description={<Text type="secondary" style={{ fontSize: 12 }}>{r.reason || '答错后自动安排'}</Text>}
                        />
                      </List.Item>
                    )}
                  />
                )}
              </Card>
            ),
          },
          {
            key: 'wrong',
            label: <Badge count={wrong.length} size="small" offset={[10, -2]}>错题本</Badge>,
            children: (
              <Card size="small"
                    title="错题本（按题目聚合最近一次结果；重做答对后移出，历史作答保留）">
                {wrong.length === 0 ? <Empty description="暂无错题" /> : (
                  <List
                    dataSource={wrong}
                    renderItem={(w) => {
                      // 答案默认不下发：只有"本次点了查看答案"或"此前查看过"（answers_included）才有数据。
                      // 即便已有数据也默认收起（平时隐蔽），展开与否由学生自己控制。
                      const fetched = wrongDetails[w.question_id];
                      const known = fetched || (w.answers_included ? w : null);
                      const expanded = !!expandedAnswers[w.question_id];
                      const shown = expanded ? known : null;
                      const correctText = shown
                        ? (shown.correct_option_text
                          ? `${shown.correct_option_id}（${shown.correct_option_text}）`
                          : (shown.reference_answer || '—'))
                        : null;
                      return (
                        <List.Item>
                          <div style={{ width: '100%' }}>
                            <Space size={8} wrap style={{ marginBottom: 6 }}>
                              {w.knowledge_point && <Tag color="purple">{w.knowledge_point}</Tag>}
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                作答 {w.attempt_count} 次 · 最近 {w.last_attempt_at}
                              </Text>
                            </Space>
                            <Paragraph style={{ marginBottom: 6 }}>{w.stem}</Paragraph>
                            <Descriptions size="small" column={1}
                                          items={[
                                            { key: 'u', label: '你的作答', children: w.user_answer_text || w.user_answer || '—' },
                                            {
                                              key: 'c',
                                              label: '正确答案',
                                              children: correctText
                                                ?? <Text type="secondary">（点「查看答案」后显示）</Text>,
                                            },
                                            ...(shown?.explanation
                                              ? [{ key: 'e', label: '解析', children: shown.explanation }]
                                              : []),
                                          ]} />
                            {w.ai_feedback && (
                              <Paragraph type="secondary" style={{ fontSize: 12, margin: '4px 0 0' }}>
                                AI 评判理由：{w.ai_feedback}
                              </Paragraph>
                            )}
                            <div style={{ marginTop: 6 }}>
                              <Space size={8} wrap>
                                {!known && (
                                  <Button
                                    size="small"
                                    icon={<EyeOutlined />}
                                    loading={revealingId === w.question_id}
                                    onClick={() => handleRevealWrong(w.question_id)}
                                  >
                                    查看答案
                                  </Button>
                                )}
                                {known && (
                                  <Button
                                    size="small"
                                    icon={expanded ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                                    onClick={() => setExpandedAnswers((prev) => ({
                                      ...prev, [w.question_id]: !prev[w.question_id],
                                    }))}
                                  >
                                    {expanded ? '收起答案' : '查看答案'}
                                  </Button>
                                )}
                                {shown && (
                                  <Text type="secondary" style={{ fontSize: 12 }}>
                                    已查看答案：该题后续作答不计入独立掌握证据
                                  </Text>
                                )}
                              </Space>
                            </div>
                          </div>
                        </List.Item>
                      );
                    }}
                  />
                )}
              </Card>
            ),
          },
          {
            key: 'mastery',
            label: '掌握度',
            children: (
              <Card size="small"
                    title="知识点掌握度（只由独立作答证据计算；无证据显示待评估）">
                {!subjectId ? (
                  <Alert type="info" showIcon message="请先在上方选择学科，再查看该学科的掌握度。" />
                ) : mastery.length === 0 ? (
                  <Empty description="该学科暂无学习记录。完成一次自测后即可看到掌握度。" />
                ) : (
                  <Table
                    rowKey={(r) => r.knowledge_point_id || r.knowledge_point}
                    dataSource={mastery}
                    size="small"
                    pagination={false}
                    /* 窄屏（手机）下让宽表在自己的容器里横向滚动：
                       否则最右侧列会被裁掉且滚不过去（实测 390px 下表格 379px / 容器 316px）。
                       这里用最小宽度而不是 max-content——后者会把表格撑到 1554px，
                       连电脑端也出现横向滚动条（实测），反而更糟。 */
                    scroll={{ x: 700 }}
                    className="app-table-scroll"
                    columns={[
                      { title: '知识点', dataIndex: 'knowledge_point' },
                      {
                        title: '状态',
                        dataIndex: 'mastery_status',
                        width: 90,
                        render: (s: string) => (
                          <Tag color={(MASTERY_META[s] || MASTERY_META.pending).color}>
                            {(MASTERY_META[s] || MASTERY_META.pending).label}
                          </Tag>
                        ),
                      },
                      {
                        title: '掌握估计',
                        width: 190,
                        render: (_: any, r: MasteryPoint) => (
                          <Space size={8}>
                            <Progress percent={masteryPercent(r)} size="small"
                                      strokeColor={masteryColor(r)} style={{ width: 90 }} showInfo={false} />
                            <Text style={{ color: masteryColor(r) }}>{masteryText(r)}</Text>
                          </Space>
                        ),
                      },
                      {
                        title: '证据（对/错）',
                        width: 120,
                        render: (_: any, r: MasteryPoint) =>
                          `${r.independent_correct_count} / ${r.independent_wrong_count}`,
                      },
                      {
                        title: '学习行为',
                        dataIndex: 'behavior_count',
                        width: 90,
                        render: (n: number) => <Text type="secondary">{n} 次</Text>,
                      },
                      {
                        title: '计算依据',
                        dataIndex: 'reason',
                        render: (t: string) => <Text type="secondary" style={{ fontSize: 12 }}>{t || '—'}</Text>,
                      },
                    ]}
                  />
                )}
                <Divider style={{ margin: '12px 0 8px' }} />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  口径说明：阅读/完成任务只记「学习行为」，不改变掌握度；只有独立作答与复习重做计入证据；
                  「查看答案后作答」会被标记且不计入独立证据。
                </Text>
              </Card>
            ),
          },
          {
            key: 'plan',
            label: '计划与时间',
            children: <PlanBudgetPanel subjectId={subjectId} />,
          },
          {
            key: 'memory',
            label: '学习记忆',
            children: <LearningMemoryPanel subjectId={subjectId} />,
          },
        ]}
      />
    </div>
  );
};

export default StudentPage;
