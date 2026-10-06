import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Button, Card, Empty, List, Segmented, Space, Spin, Tag, Typography, message,
} from 'antd';
import {
  CheckOutlined, FormOutlined, PlayCircleOutlined, ReloadOutlined, SyncOutlined,
} from '@ant-design/icons';
import { useCourse } from '../context/CourseContext';
import { getHome, getTasks, completeTask } from '../api/student';
import { listMyAssignments } from '../api/studentCourses';

const { Title, Text } = Typography;

type Filter = 'current' | 'upcoming' | 'done';

interface TodoItem {
  key: string;
  kind: 'task' | 'assignment' | 'review';
  courseTitle: string;
  title: string;
  detail?: string;
  due?: string | null;
  overdue: boolean;
  raw: any;
}

const todayStr = () => new Date().toISOString().slice(0, 10);

/**
 * 作业与复习（2026-10-06 改版）：把今天任务、逾期任务、待交作业、
 * 到期复习、计划任务合并成**一个待办列表**。逾期只是筛选，不单独成页。
 * 草案计划（后端 DRAFT）不会出现在这里；完成/提交后自动刷新。
 */
const TodoPage: React.FC = () => {
  const navigate = useNavigate();
  const ctx = useCourse();
  const [filter, setFilter] = useState<Filter>('current');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tasks, setTasks] = useState<any[]>([]);
  const [assignments, setAssignments] = useState<any[]>([]);
  const [reviews, setReviews] = useState<any[]>([]);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    const sid = ctx.currentSubjectId || undefined;
    try {
      const [taskRes, asgRes, homeRes] = await Promise.all([
        getTasks(sid ? { subject_id: sid } : {}),
        listMyAssignments(),
        getHome(sid),
      ]);
      const t: any = (taskRes as any)?.data || {};
      setTasks([...(t.todo || [])]);
      const a: any = (asgRes as any)?.data || {};
      setAssignments(a.assignments || []);
      const h: any = (homeRes as any)?.data || {};
      setReviews(h.pending_reviews || []);
    } catch (e: any) {
      setError((e && e.response && e.response.data && e.response.data.message) || '待办加载失败，请重试');
    } finally { setLoading(false); }
  }, [ctx.currentSubjectId]);

  useEffect(() => { load(); }, [load]);
  // 从作答/复习返回后自动刷新（不发轮询）
  useEffect(() => {
    const onFocus = () => load();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [load]);

  const courseTitle = useCallback((subjectId?: string | null) => {
    if (!subjectId) return ctx.current ? ctx.current.title : '个人资料';
    const c = ctx.courses.find((x) => x.subject_id === subjectId);
    return c ? c.title : (ctx.current ? ctx.current.title : subjectId);
  }, [ctx.courses, ctx.current]);

  /** 作业记录带的是 course_id（不是 subject_id），必须按课程 ID 映射，不能用当前课程兜底 */
  const courseTitleById = useCallback((courseId?: string | null) => {
    const c = courseId ? ctx.courses.find((x) => x.course_id === courseId) : null;
    return c ? c.title : (courseId || '');
  }, [ctx.courses]);

  const items = useMemo<TodoItem[]>(() => {
    const today = todayStr();
    const out: TodoItem[] = [];
    // 任务（含计划任务与自动复习任务）
    // 去重依据真实业务关联：同一课程 + 同一事项 + 同一日期 + 同一类型视为同一件事
    // （多次生成计划可能留下完全相同的任务记录，不只是标题相同）
    const taskSeen = new Set<string>();
    const coveredReviewKp = new Set<string>();
    for (const t of tasks) {
      if (t.status === 'COMPLETED') continue;
      const due = t.planned_date || null;
      const bizKey = [t.subject_id || '', t.title || t.knowledge_point || '', due || '', t.task_type || ''].join('|');
      if (taskSeen.has(bizKey)) continue;
      taskSeen.add(bizKey);
      if (t.task_type === 'REVIEW' && t.knowledge_point_id) coveredReviewKp.add(t.knowledge_point_id);
      out.push({
        key: 'task:' + t.id, kind: 'task', courseTitle: courseTitle(t.subject_id),
        title: t.title || t.knowledge_point || '学习任务',
        detail: (t.task_type === 'REVIEW' ? '复习' : t.task_type === 'QUIZ' ? '自测' : '学习')
          + (t.estimated_minutes ? ' · 约 ' + t.estimated_minutes + ' 分钟' : ''),
        due, overdue: !!t.overdue || (!!due && due < today), raw: t,
      });
    }
    // 待交作业：只看当前课程；已全部完成的不再列为待办；同课同题同截止只留一条
    const asgSeen = new Set<string>();
    for (const a of assignments) {
      if (a.status !== 'published') continue;
      if (ctx.current && a.course_id && a.course_id !== ctx.current.course_id) continue;
      const done = Object.keys(a.results || {}).length;
      const total = (a.questions || []).length || a.question_count || 0;
      if (total > 0 && done >= total) continue;
      const due = a.due_date || null;
      const bizKey = [a.course_id || '', a.title || '', due ? String(due).slice(0, 10) : ''].join('|');
      if (asgSeen.has(bizKey)) continue;
      asgSeen.add(bizKey);
      out.push({
        key: 'asg:' + a.id, kind: 'assignment',
        courseTitle: courseTitleById(a.course_id) || a.course_title || '',
        title: a.title, detail: '作业 · 已完成 ' + done + '/' + total,
        due, overdue: !!due && String(due).slice(0, 10) < today, raw: a,
      });
    }
    // 待办复习（同一知识点已有复习任务时不再重复列一条）
    for (const r of reviews) {
      if (r.knowledge_point_id && coveredReviewKp.has(r.knowledge_point_id)) continue;
      const due = r.due_date || null;
      out.push({
        key: 'review:' + (r.id || r.knowledge_point_id), kind: 'review',
        courseTitle: courseTitle(r.subject_id),
        title: '复习：' + (r.knowledge_point_name || r.knowledge_point || '知识点'),
        detail: '复习' + (r.merged_count > 1 ? '（已合并 ' + r.merged_count + ' 次）' : ''),
        due, overdue: !!due && String(due).slice(0, 10) < today, raw: r,
      });
    }
    // 去重：任务与复习可能指向同一知识点，保留任务（有 task_id 可完成）
    const seen = new Map<string, TodoItem>();
    for (const it of out) {
      const dedupeKey = it.kind === 'review' ? 'kp:' + (it.raw.knowledge_point_id || it.title) : it.key;
      if (!seen.has(dedupeKey)) seen.set(dedupeKey, it);
      else if (it.kind !== 'review') seen.set(dedupeKey, it);
    }
    return [...seen.values()];
  }, [tasks, assignments, reviews, courseTitle, courseTitleById, ctx.current]);

  const today = todayStr();
  const current = items.filter((i) => !i.due || String(i.due).slice(0, 10) <= today);
  const upcoming = items.filter((i) => i.due && String(i.due).slice(0, 10) > today);
  const shown = filter === 'current' ? current : filter === 'upcoming' ? upcoming : [];

  const start = (it: TodoItem) => {
    if (it.kind === 'assignment') { navigate('/student/assignments?assignment=' + it.raw.id); return; }
    const sid = it.raw.subject_id || ctx.currentSubjectId;
    if (it.kind === 'review') {
      navigate('/student', { state: { preset: {
        mode: 'quiz', subjectId: sid, autoStart: true,
        input: '复习：' + (it.raw.knowledge_point_name || ''),
        review: { id: it.raw.id, knowledgePoint: it.raw.knowledge_point_name || '' },
      } } });
      return;
    }
    const type = it.raw.task_type;
    navigate('/student', { state: { preset: {
      mode: type === 'REVIEW' || type === 'QUIZ' ? 'quiz' : 'chat',
      subjectId: sid, autoStart: type === 'REVIEW' || type === 'QUIZ',
      taskId: it.raw.id,
      input: type === 'REVIEW' ? ('复习：' + (it.raw.knowledge_point || '')) : '',
    } } });
  };

  const finish = async (it: TodoItem) => {
    if (it.kind !== 'task') return;
    try {
      await completeTask(it.raw.id, it.raw.estimated_minutes || undefined);
      message.success('已完成');
      await load();
    } catch (e: any) {
      message.error((e && e.response && e.response.data && e.response.data.message) || '完成失败');
    }
  };

  if (ctx.status === 'loading') return <div style={{ display: 'flex', justifyContent: 'center', padding: 64 }}><Spin tip="正在加载待办…" /></div>;

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
        <Title level={4} style={{ margin: 0 }}>作业与复习</Title>
        {ctx.current && <Tag color="blue">{ctx.current.title}</Tag>}
        <Segmented
          style={{ marginLeft: 'auto' }}
          value={filter}
          onChange={(v) => setFilter(v as Filter)}
          options={[
            { label: '今天与逾期（' + current.length + '）', value: 'current' },
            { label: '接下来（' + upcoming.length + '）', value: 'upcoming' },
            { label: '已完成', value: 'done' },
          ]}
        />
        <Button type="text" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </div>

      {error && <Alert type="error" showIcon style={{ marginBottom: 10 }} message={error}
                       action={<Button icon={<SyncOutlined />} onClick={load}>重试</Button>} />}
      {ctx.forbidden && <Alert type="warning" showIcon style={{ marginBottom: 10 }} message={ctx.forbidden} />}

      {loading ? <Spin /> : filter === 'done' ? (
        <Empty description="已完成的事项在课程与学习情况里可查" />
      ) : shown.length === 0 ? (
        <Card><Empty description={filter === 'current' ? '今天没有待办，去「开始学习」继续提问吧' : '接下来没有安排'}>
          <Button type="primary" onClick={() => navigate('/student')}>去开始学习</Button></Empty></Card>
      ) : (
        <List
          itemLayout="horizontal"
          dataSource={shown}
          renderItem={(it) => (
            <List.Item
              actions={[
                <Button key="start" type="primary" ghost icon={<PlayCircleOutlined />} onClick={() => start(it)}>开始</Button>,
                it.kind === 'task' && it.raw.task_type !== 'QUIZ' && it.raw.task_type !== 'REVIEW'
                  ? <Button key="done" icon={<CheckOutlined />} onClick={() => finish(it)}>完成</Button>
                  : null,
              ].filter(Boolean) as React.ReactNode[]}
            >
              <List.Item.Meta
                avatar={it.kind === 'assignment' ? <FormOutlined style={{ color: '#0f766e' }} /> : <PlayCircleOutlined style={{ color: '#0f766e' }} />}
                title={<Space wrap><Text strong>{it.title}</Text>
                  {it.overdue && <Tag color="red">已逾期</Tag>}
                  <Tag>{it.kind === 'assignment' ? '作业' : it.kind === 'review' ? '复习' : '任务'}</Tag></Space>}
                description={<Text type="secondary" style={{ fontSize: 12 }}>
                  {it.courseTitle}{it.detail ? ' · ' + it.detail : ''}{it.due ? ' · 到期 ' + String(it.due).slice(0, 10) : ''}
                </Text>}
              />
            </List.Item>
          )}
        />
      )}
    </div>
  );
};

export default TodoPage;
