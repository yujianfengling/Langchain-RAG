import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Button, Card, Collapse, Dropdown, Empty, List, Modal, Select, Space, Spin,
  Tag, Typography, message,
} from 'antd';
import {
  CalendarOutlined, CheckOutlined, CheckSquareOutlined, ClockCircleOutlined,
  ExclamationCircleOutlined, FormOutlined, PlayCircleOutlined, ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { useCourse } from '../context/CourseContext';
import {
  completeTask, getHome, getReviews, getTasks, getWrongQuestions, rescheduleTask,
} from '../api/student';
import type {
  LearningHome, ReviewTask, StudentTask, TaskBuckets, WrongQuestion,
} from '../api/student';
import { listMyAssignments } from '../api/studentCourses';
import type { StudentAssignment } from '../api/studentCourses';
import type { ApiResponse } from '../types';
import apiClient from '../api/axios';

const { Title, Text } = Typography;

/**
 * 任务与复习（2026-10-07 统一伴学重构）
 *
 * 把今天的任务、逾期任务、接下来的安排、待交作业、到期复习与错题合并成
 * 一个去重后的清单，并明确分成「逾期 / 今天 / 未来」三段。
 * 每条按钮写明具体动作（开始复习 / 再做一次 / 提交作业…），不写含糊的「开始」。
 */

interface AdjustmentChange {
  change_type?: string;
  task_id?: string;
  title?: string | null;
  knowledge_point?: string | null;
  task_type?: string | null;
  from_date?: string | null;
  to_date?: string | null;
  task_ids?: string[];
  titles?: string[];
  minutes?: number;
  note?: string;
}

interface PlanAdjustment {
  id: string;
  subject_id?: string | null;
  reason?: string | null;
  summary?: string | null;
  status: string;
  major?: boolean;
  requires_confirmation?: boolean;
  created_at?: string;
  changes?: AdjustmentChange[];
}

type ItemKind = 'task' | 'assignment' | 'review' | 'wrong';

interface UnifiedItem {
  key: string;
  kind: ItemKind;
  subjectId: string | null;
  courseTitle: string;
  title: string;
  detail: string;
  due: string | null;
  overdue: boolean;
  task?: StudentTask;
  assignment?: StudentAssignment;
  review?: ReviewTask;
  wrong?: WrongQuestion;
}

const describeError = (e: unknown, fallback = '操作失败，请重试'): string => {
  const err = e as {
    response?: { data?: { message?: string; detail?: string } };
    message?: string;
  };
  const detail = err?.response?.data?.detail;
  return err?.response?.data?.message
    || (typeof detail === 'string' ? detail : '')
    || err?.message
    || fallback;
};

/** 单个数据源失败不影响整页，但要把失败告诉用户并提供重试 */
const settle = async <T,>(
  p: Promise<T>, fallback: T, label: string, failed: string[],
): Promise<T> => {
  try {
    return await p;
  } catch {
    failed.push(label);
    return fallback;
  }
};

const taskDetailText = (t: StudentTask): string => {
  const type = t.task_type === 'REVIEW' ? '复习'
    : t.task_type === 'QUIZ' ? '自测'
      : t.task_type === 'PRACTICE' ? '练习' : '阅读';
  const minutes = t.estimated_minutes ? ' · 约 ' + t.estimated_minutes + ' 分钟' : '';
  const source = t.source === 'auto_review' ? ' · 错题自动安排' : '';
  return type + minutes + source;
};

const changesToText = (adj: PlanAdjustment): string[] => {
  const changes = adj.changes || [];
  return changes.map((c) => {
    if (c.task_ids && c.task_ids.length > 0) {
      return '建议缩减 ' + c.task_ids.length + ' 个任务的范围'
        + (c.minutes ? '（约节省 ' + c.minutes + ' 分钟）' : '');
    }
    if (c.titles && c.titles.length > 0) {
      return '建议缩减 ' + c.titles.length + ' 个任务的范围';
    }
    if (c.change_type === 'defer') return (c.title || '任务') + '：顺延安排';
    const from = c.from_date || '未安排';
    const to = c.to_date || '待安排';
    return (c.title || c.knowledge_point || '任务') + '：' + from + ' → ' + to;
  });
};

const TasksPage: React.FC = () => {
  const navigate = useNavigate();
  const ctx = useCourse();

  const [home, setHome] = useState<LearningHome | null>(null);
  const [buckets, setBuckets] = useState<TaskBuckets | null>(null);
  const [wrongs, setWrongs] = useState<WrongQuestion[]>([]);
  const [reviews, setReviews] = useState<ReviewTask[]>([]);
  const [assignments, setAssignments] = useState<StudentAssignment[]>([]);
  const [adjustments, setAdjustments] = useState<PlanAdjustment[]>([]);
  const [failed, setFailed] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  /** 任务页自己的学科筛选：默认全部，不跟随 AI 伴学里的当前学科 */
  const [subjectFilter, setSubjectFilter] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    const sid = subjectFilter || undefined;
    const params = sid ? { subject_id: sid } : {};
    const failures: string[] = [];
    const adjPromise = apiClient.get('/plan/adjustments', { params }) as unknown as Promise<{ data?: PlanAdjustment[] }>;
    try {
      const [homeRes, taskRes, wrongRes, reviewRes, asgRes, adjRes] = await Promise.all([
        settle(getHome(sid), null as ApiResponse<LearningHome> | null, '学习首页', failures),
        settle(getTasks(params), null as ApiResponse<TaskBuckets | StudentTask[]> | null, '任务', failures),
        settle(getWrongQuestions(sid), null as ApiResponse<WrongQuestion[]> | null, '错题', failures),
        settle(getReviews(params), null as ApiResponse<ReviewTask[]> | null, '复习', failures),
        settle(listMyAssignments(), null as ApiResponse<{ assignments: StudentAssignment[] }> | null, '作业', failures),
        settle(adjPromise, null as { data?: PlanAdjustment[] } | null, '计划调整', failures),
      ]);
      const rawTasks = taskRes?.data;
      let nextBuckets: TaskBuckets | null = null;
      if (rawTasks && !Array.isArray(rawTasks)) {
        nextBuckets = rawTasks;
      } else if (Array.isArray(rawTasks)) {
        nextBuckets = {
          today: rawTasks, todo: [], completed: [], overdue: [],
          counts: { today: rawTasks.length, todo: 0, completed: 0, overdue: 0 },
        };
      }
      setHome(homeRes?.data ?? null);
      setBuckets(nextBuckets);
      setWrongs(wrongRes?.data ?? []);
      setReviews(reviewRes?.data ?? []);
      setAssignments(asgRes?.data?.assignments ?? []);
      setAdjustments(adjRes?.data ?? []);
      setFailed(failures);
    } finally {
      setLoading(false);
    }
  }, [subjectFilter]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const onFocus = () => { void load(); };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [load]);

  const items = useMemo<UnifiedItem[]>(() => {
    const today = dayjs().format('YYYY-MM-DD');
    const subjectName = (sid?: string | null): string => {
      if (!sid) return '个人资料';
      const c = ctx.courses.find((x) => x.subject_id === sid);
      if (c) return c.title;
      const p = ctx.personalSubjects.find((x) => x.subject_id === sid);
      return p ? p.name : '个人学科';
    };
    const courseName = (cid?: string | null): string => {
      const c = cid ? ctx.courses.find((x) => x.course_id === cid) : null;
      return c ? c.title : (cid || '课程');
    };
    const courseSubject = (cid?: string | null): string | null => {
      const c = cid ? ctx.courses.find((x) => x.course_id === cid) : null;
      return c ? c.subject_id : null;
    };

    const out: UnifiedItem[] = [];
    const taskSeen = new Set<string>();
    const coveredKp = new Set<string>();
    const addTask = (t: StudentTask) => {
      if (!t || !t.id || t.status === 'COMPLETED') return;
      if (taskSeen.has(t.id)) return;
      taskSeen.add(t.id);
      const due = t.planned_date ? String(t.planned_date).slice(0, 10) : null;
      if (t.task_type === 'REVIEW' && t.knowledge_point_id) coveredKp.add(t.knowledge_point_id);
      out.push({
        key: 'task:' + t.id,
        kind: 'task',
        subjectId: t.subject_id || null,
        courseTitle: subjectName(t.subject_id),
        title: t.title || t.knowledge_point || '学习任务',
        detail: taskDetailText(t),
        due,
        overdue: !!t.overdue || (!!due && due < today),
        task: t,
      });
    };
    if (buckets) {
      buckets.overdue.forEach(addTask);
      buckets.today.forEach(addTask);
      buckets.todo.forEach(addTask);
    }
    if (home) {
      (home.overdue_tasks || []).forEach(addTask);
      (home.today_tasks || []).forEach(addTask);
    }

    const asgSeen = new Set<string>();
    for (const a of assignments) {
      if (!a || a.status !== 'published') continue;
      const total = (a.questions || []).length || a.question_count || 0;
      const done = Object.keys(a.results || {}).length;
      if (total > 0 && done >= total) continue;
      if (asgSeen.has(a.id)) continue;
      asgSeen.add(a.id);
      const due = a.due_date ? String(a.due_date).slice(0, 10) : null;
      out.push({
        key: 'asg:' + a.id,
        kind: 'assignment',
        subjectId: courseSubject(a.course_id),
        courseTitle: a.course_title || courseName(a.course_id),
        title: a.title,
        detail: '已完成 ' + done + '/' + total + ' 题',
        due,
        overdue: !!due && due < today,
        assignment: a,
      });
    }

    const reviewSeen = new Set<string>();
    for (const r of reviews) {
      if (!r || !r.id) continue;
      if (r.knowledge_point_id && coveredKp.has(r.knowledge_point_id)) continue;
      if (reviewSeen.has(r.id)) continue;
      reviewSeen.add(r.id);
      const due = r.due_date ? String(r.due_date).slice(0, 10) : null;
      out.push({
        key: 'review:' + r.id,
        kind: 'review',
        subjectId: r.subject_id,
        courseTitle: subjectName(r.subject_id),
        title: '复习：' + (r.knowledge_point_name || '知识点'),
        detail: [
          r.reason || '到期复习',
          r.merged_count && r.merged_count > 1 ? '已合并 ' + r.merged_count + ' 次安排' : '',
        ].filter(Boolean).join(' · '),
        due,
        overdue: !!due && due < today,
        review: r,
      });
    }

    const wrongSeen = new Set<string>();
    for (const w of wrongs) {
      if (!w || !w.question_id) continue;
      if (w.knowledge_point_id && coveredKp.has(w.knowledge_point_id)) continue;
      if (wrongSeen.has(w.question_id)) continue;
      wrongSeen.add(w.question_id);
      out.push({
        key: 'wrong:' + w.question_id,
        kind: 'wrong',
        subjectId: null,
        courseTitle: '错题',
        title: w.stem || '错题',
        detail: [
          w.knowledge_point ? '知识点：' + w.knowledge_point : '重新做一遍',
          w.error_label ? '错因：' + w.error_label : '',
        ].filter(Boolean).join(' · '),
        due: null,
        overdue: false,
        wrong: w,
      });
    }
    return out;
  }, [buckets, home, assignments, reviews, wrongs, ctx.courses, ctx.personalSubjects]);

  const todayStr = dayjs().format('YYYY-MM-DD');
  const overdueItems = useMemo(() => items.filter((i) => i.overdue), [items]);
  const todayItems = useMemo(
    () => items.filter((i) => !i.overdue && (!i.due || i.due <= todayStr)), [items, todayStr]);
  const futureItems = useMemo(
    () => items.filter((i) => !i.overdue && !!i.due && i.due > todayStr), [items, todayStr]);
  const pendingAdjustments = useMemo(
    () => adjustments.filter((a) => a.status === 'PROPOSED'), [adjustments]);

  const actionLabel = (it: UnifiedItem): string => {
    if (it.kind === 'assignment') {
      const done = Object.keys(it.assignment?.results || {}).length;
      return done > 0 ? '继续作答' : '提交作业';
    }
    if (it.kind === 'review') return '开始复习';
    if (it.kind === 'wrong') return '重做错题';
    if (it.task?.task_type === 'REVIEW') return '开始复习';
    if (it.task?.task_type === 'QUIZ') return '再做一次';
    return '开始学习';
  };

  const canComplete = (it: UnifiedItem): boolean => (
    it.kind === 'task' && (it.task?.task_type === 'READ' || it.task?.task_type === 'PRACTICE')
  );

  const go = (it: UnifiedItem) => {
    if (it.kind === 'assignment' && it.assignment) {
      navigate('/student/assignments?assignment=' + encodeURIComponent(it.assignment.id));
      return;
    }
    const id = it.kind === 'task' ? it.task?.id
      : it.kind === 'review' ? it.review?.id
        : it.wrong?.question_id;
    if (!id) return;
    const sid = it.subjectId || subjectFilter || ctx.currentSubjectId || '';
    const params = new URLSearchParams();
    if (sid) params.set('subject', sid);
    params.set('task', id);
    navigate('/student?' + params.toString());
  };

  const openTutor = (it: UnifiedItem) => {
    if (!it.assignment) return;
    const params = new URLSearchParams();
    if (it.subjectId) params.set('subject', it.subjectId);
    params.set('task', it.assignment.id);
    navigate('/student?' + params.toString());
  };

  const onComplete = async (it: UnifiedItem) => {
    if (!it.task) return;
    try {
      await completeTask(it.task.id, it.task.estimated_minutes || undefined);
      message.success('已标记完成');
      await load();
    } catch (e) {
      message.error(describeError(e, '完成失败，请重试'));
    }
  };

  const onReschedule = async (it: UnifiedItem, key: string) => {
    if (!it.task) return;
    const addDays = key === 'tomorrow' ? 1 : key === 'in3' ? 3 : 7;
    const newDate = dayjs().add(addDays, 'day').format('YYYY-MM-DD');
    try {
      await rescheduleTask(it.task.id, newDate, '学生手动改期');
      message.success('已改到 ' + newDate);
      await load();
    } catch (e) {
      message.error(describeError(e, '改期失败，请重试'));
    }
  };

  const doApply = async (adj: PlanAdjustment) => {
    try {
      await apiClient.post('/plan/adjustments/' + adj.id + '/apply', {
        confirm: !!adj.requires_confirmation,
      });
      message.success('调整已应用');
      await load();
    } catch (e) {
      message.error(describeError(e, '应用失败，请重试'));
    }
  };

  const onApply = (adj: PlanAdjustment) => {
    if (adj.requires_confirmation) {
      Modal.confirm({
        title: '确认应用这组调整？',
        content: '这是一次较大范围的调整，会改动多个任务的计划日期。应用后可在学习计划里查看。',
        okText: '确认应用',
        cancelText: '再想想',
        onOk: () => doApply(adj),
      });
    } else {
      void doApply(adj);
    }
  };

  const onReject = async (adj: PlanAdjustment) => {
    try {
      await apiClient.post('/plan/adjustments/' + adj.id + '/reject');
      message.success('已忽略该建议');
      await load();
    } catch (e) {
      message.error(describeError(e, '操作失败，请重试'));
    }
  };

  const kindTag = (it: UnifiedItem): React.ReactNode => {
    if (it.kind === 'assignment') return <Tag color="blue">作业</Tag>;
    if (it.kind === 'review') return <Tag color="orange">复习</Tag>;
    if (it.kind === 'wrong') return <Tag>错题</Tag>;
    if (it.task?.task_type === 'QUIZ') return <Tag>自测</Tag>;
    if (it.task?.task_type === 'REVIEW') return <Tag color="orange">复习</Tag>;
    return <Tag>任务</Tag>;
  };

  const avatarOf = (it: UnifiedItem): React.ReactNode => {
    if (it.kind === 'assignment') return <FormOutlined style={{ color: 'var(--color-primary)' }} />;
    if (it.kind === 'wrong') return <ExclamationCircleOutlined style={{ color: '#d97706' }} />;
    return <ClockCircleOutlined style={{ color: 'var(--color-primary)' }} />;
  };

  const deferItems = [
    { key: 'tomorrow', label: '挪到明天' },
    { key: 'in3', label: '挪到三天后' },
    { key: 'nextweek', label: '挪到下周' },
  ];

  const actionsOf = (it: UnifiedItem): React.ReactNode[] => {
    const nodes: React.ReactNode[] = [
      <Button key="go" type="primary" ghost icon={<PlayCircleOutlined />} onClick={() => go(it)}>
        {actionLabel(it)}
      </Button>,
    ];
    if (canComplete(it)) {
      nodes.push(<Button key="done" icon={<CheckOutlined />} onClick={() => onComplete(it)}>标记完成</Button>);
    }
    if (it.kind === 'task') {
      nodes.push(
        <Dropdown key="defer" menu={{ items: deferItems, onClick: ({ key }) => onReschedule(it, key) }}>
          <Button icon={<CalendarOutlined />}>改期</Button>
        </Dropdown>,
      );
    }
    if (it.kind === 'assignment') {
      nodes.push(<Button key="tutor" type="link" onClick={() => openTutor(it)}>去伴学</Button>);
    }
    return nodes;
  };

  const renderGroup = (list: UnifiedItem[], emptyText: string): React.ReactNode => {
    if (list.length === 0) {
      return <Text type="secondary" style={{ fontSize: 13 }}>{emptyText}</Text>;
    }
    return (
      <List
        size="small"
        dataSource={list}
        rowKey={(it) => it.key}
        renderItem={(it) => (
          <List.Item actions={actionsOf(it)}>
            <List.Item.Meta
              avatar={avatarOf(it)}
              title={(
                <Space wrap>
                  <Text strong>{it.title}</Text>
                  {it.overdue && <Tag color="red">已逾期</Tag>}
                  {kindTag(it)}
                </Space>
              )}
              description={(
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {it.courseTitle}
                  {it.detail ? ' · ' + it.detail : ''}
                  {it.due ? ' · ' + (it.overdue ? '应于 ' : '截止 ') + it.due : ''}
                </Text>
              )}
            />
          </List.Item>
        )}
      />
    );
  };

  const panelLabel = (title: string, count: number, tone: 'danger' | 'warning' | 'default'): React.ReactNode => (
    <Space>
      <Text strong>{title}</Text>
      <Tag color={tone === 'danger' ? 'red' : tone === 'warning' ? 'orange' : 'default'}>{count}</Tag>
    </Space>
  );

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <CheckSquareOutlined style={{ color: 'var(--color-primary)', fontSize: 18 }} />
        <Title level={4} style={{ margin: 0 }}>任务与复习</Title>
        <Select
          allowClear
          value={subjectFilter || undefined}
          placeholder="全部学科"
          style={{ minWidth: 190 }}
          options={ctx.subjects.map((s) => ({ value: s.subject_id, label: s.name }))}
          onChange={(value) => setSubjectFilter(String(value || ''))}
        />
        <Button type="text" icon={<ReloadOutlined />} style={{ marginLeft: 'auto' }} onClick={() => load()}>
          刷新
        </Button>
      </div>

      {failed.length > 0 && (
        <Alert
          type="warning" showIcon style={{ marginBottom: 12 }}
          message={'部分内容加载失败：' + failed.join('、')}
          description="其余内容已正常显示，可点重试再拉取一次。"
          action={<Button icon={<SyncOutlined />} onClick={() => load()}>重试</Button>}
        />
      )}
      {ctx.forbidden && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={ctx.forbidden} />}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 64 }}><Spin tip="正在加载任务…" /></div>
      ) : (
        <>
          {pendingAdjustments.length > 0 && (
            <Card
              size="small"
              style={{ marginBottom: 12, borderColor: 'var(--color-primary-200)' }}
              title={(
                <Space wrap>
                  <Tag color="orange">草案未生效</Tag>
                  <Text strong>学习计划调整待确认（{pendingAdjustments.length}）</Text>
                </Space>
              )}
            >
              <List
                size="small"
                dataSource={pendingAdjustments}
                renderItem={(adj) => (
                  <List.Item actions={[
                    <Button key="apply" type="primary" size="small" onClick={() => onApply(adj)}>确认应用</Button>,
                    <Button key="reject" size="small" onClick={() => onReject(adj)}>忽略</Button>,
                  ]}>
                    <List.Item.Meta
                      title={<Text strong>{adj.summary || adj.reason || '学习计划调整建议'}</Text>}
                      description={(
                        <div>
                          {changesToText(adj).map((line, idx) => (
                            <div key={idx} style={{ fontSize: 12 }}>· {line}</div>
                          ))}
                        </div>
                      )}
                    />
                  </List.Item>
                )}
              />
            </Card>
          )}

          {items.length === 0 ? (
            <Card>
              <Empty description="现在没有待办。可以去开始学习，或先到资料库准备资料。">
                <Space>
                  <Button type="primary" onClick={() => navigate('/student')}>去开始学习</Button>
                  <Button onClick={() => navigate('/student/materials')}>去资料库</Button>
                </Space>
              </Empty>
            </Card>
          ) : (
            <Collapse
              defaultActiveKey={['overdue', 'today']}
              items={[
                {
                  key: 'overdue',
                  label: panelLabel('逾期', overdueItems.length, 'danger'),
                  children: renderGroup(overdueItems, '没有逾期事项，保持节奏。'),
                },
                {
                  key: 'today',
                  label: panelLabel('今天', todayItems.length, 'warning'),
                  children: renderGroup(todayItems, '今天没有安排，可以去「开始学习」自由提问。'),
                },
                {
                  key: 'future',
                  label: panelLabel('未来', futureItems.length, 'default'),
                  children: renderGroup(futureItems, '未来还没有排期。'),
                },
              ]}
            />
          )}
        </>
      )}
    </div>
  );
};

export default TasksPage;
