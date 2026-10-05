import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Card, DatePicker, Descriptions, Divider, Empty, List,
  Progress, Segmented, Select, Space, Spin, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  ArrowLeftOutlined, BookOutlined, BulbOutlined, CalendarOutlined,
  CheckCircleOutlined, ClockCircleOutlined, DatabaseOutlined, ExclamationCircleOutlined,
  EyeInvisibleOutlined, EyeOutlined, LineChartOutlined, MoreOutlined, ReadOutlined,
  ReloadOutlined, RocketOutlined, ScheduleOutlined, WarningOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { getSubjects } from '../api/subjects';
import LearningMemoryPanel from '../components/LearningMemoryPanel';
import StudentCourses from '../components/StudentCourses';
import { listMyAssignments, listMyCourses, listCourseMaterials } from '../api/studentCourses';
import type { MyCourse, StudentAssignment } from '../api/studentCourses';
import PlanBudgetPanel from '../components/PlanBudgetPanel';
import { isLoggedIn } from '../api/auth';
import { AUTH_CHANGED_EVENT } from '../components/UserAuth';
import {
  StudentTask, WrongQuestion, MasteryPoint,
  LearningHome, getHome, completeTask, rescheduleTask,
  getWrongQuestions, getWrongQuestionDetail, getMastery,
} from '../api/student';

const { Title, Text, Paragraph } = Typography;

const TASK_TYPE_META: Record<string, { color: string; label: string }> = {
  READ: { color: 'cyan', label: '阅读' },
  REVIEW: { color: 'purple', label: '复习' },
  QUIZ: { color: 'green', label: '自测' },
  PRACTICE: { color: 'orange', label: '练习' },
};

/**
 * 学习首页的视图。
 *
 * 默认 home：当前课程 + 今日任务（逾期是筛选）+ 待交作业 + 待复习。
 * 其余是**次级入口**（课程/错题/掌握度/计划/记忆），不再是首页平铺的页签。
 * 旧网址里的 today / overdue / review 一律回到首页（内容已合并到首页）。
 */
type ViewKey = 'home' | 'courses' | 'wrong' | 'mastery' | 'plan' | 'memory';

const VIEW_KEYS: ViewKey[] = ['home', 'courses', 'wrong', 'mastery', 'plan', 'memory'];

const SECONDARY_NAV: Array<{ key: ViewKey; label: string; icon: React.ReactNode }> = [
  { key: 'courses', label: '我的课程', icon: <BookOutlined /> },
  { key: 'wrong', label: '错题本', icon: <WarningOutlined /> },
  { key: 'mastery', label: '掌握度', icon: <LineChartOutlined /> },
  { key: 'plan', label: '计划与时间', icon: <ScheduleOutlined /> },
  { key: 'memory', label: '学习记忆', icon: <BulbOutlined /> },
];

const VIEW_TITLE: Record<Exclude<ViewKey, 'home'>, string> = {
  courses: '我的课程',
  wrong: '错题本',
  mastery: '掌握度',
  plan: '计划与时间',
  memory: '学习记忆',
};

/** 当前掌握（最新有效作答）的颜色：与百分比、等级、排序同源，避免新旧口径混色 */
const masteryColorOf = (level: number | null | undefined) => {
  if (level == null) return '#94a3b8';
  if (level >= 0.7) return '#059669';
  if (level >= 0.4) return '#d97706';
  return '#dc2626';
};

/**
 * 去掉内部作答 ID 等开发信息：后端在复习/任务原因里会附「（来源作答 att_xxx）」，
 * 那是给排查用的，学生不需要看到。
 */
const cleanReason = (text?: string | null) => (text || '')
  .replace(/[（(]\s*来源作答\s*att_[0-9a-zA-Z]+\s*[）)]/g, '')
  .replace(/来源作答\s*att_[0-9a-zA-Z]+/g, '')
  .replace(/\s{2,}/g, ' ')
  .trim();

/** 作业完成度：已作答题数 / 总题数 */
const assignmentProgress = (a: StudentAssignment) => {
  const done = Object.keys(a.results || {}).length;
  const total = a.questions?.length || a.question_count || 0;
  return { done, total, complete: total > 0 && done >= total };
};

const StudentPage: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [subjectId, setSubjectId] = useState('');
  const [loggedIn, setLoggedIn] = useState(isLoggedIn());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [home, setHome] = useState<LearningHome | null>(null);
  const [courses, setCourses] = useState<MyCourse[]>([]);
  const [assignments, setAssignments] = useState<StudentAssignment[]>([]);
  /** 当前课程的资料/知识库范围（学生可见的真实状态） */
  const [currentKb, setCurrentKb] = useState<{ materials: number; available: boolean } | null>(null);
  const [wrong, setWrong] = useState<WrongQuestion[]>([]);
  /** 错题答案默认不下发（answers_included=false），点「查看答案」后才拉取 */
  const [wrongDetails, setWrongDetails] = useState<Record<string, any>>({});
  const [revealingId, setRevealingId] = useState<string | null>(null);
  /** 已展开答案的题目。**默认全部收起**——即使后端已下发（此前查看过），
   *  也要学生自己点开，避免答案在错题本里一直摊着。 */
  const [expandedAnswers, setExpandedAnswers] = useState<Record<string, boolean>>({});
  const [mastery, setMastery] = useState<MasteryPoint[]>([]);
  /** 学科注册表（用于非课程学科的显示）；课程学科一律用真实课程名覆盖 */
  const [registrySubjects, setRegistrySubjects] = useState<Array<{ subject_id: string; name: string }>>([]);
  const [busyTask, setBusyTask] = useState<string | null>(null);
  /** 首页「今天的任务」里的筛选：逾期不是独立页签，而是这个筛选值 */
  const [taskFilter, setTaskFilter] = useState<'today' | 'overdue'>('today');
  /** 辅助操作（改期）默认收起，点「改期」才展开 */
  const [opsOpen, setOpsOpen] = useState<Record<string, boolean>>({});

  // 视图由网址驱动：/student?tab=courses 直接进入我的课程并可分享/刷新
  const tabParam = searchParams.get('tab') || '';
  const view: ViewKey = (VIEW_KEYS as string[]).includes(tabParam)
    ? (tabParam as ViewKey) : 'home';
  const focusAssignmentId = searchParams.get('assignment') || null;

  const goToView = useCallback((next: ViewKey, extra?: { assignment?: string }) => {
    const params = new URLSearchParams();
    if (next !== 'home') params.set('tab', next);
    if (extra?.assignment) params.set('assignment', extra.assignment);
    setSearchParams(params);
    if (typeof window !== 'undefined') window.scrollTo({ top: 0, behavior: 'smooth' });
  }, [setSearchParams]);

  /** 消费掉作业深链参数（作业弹窗打开后从网址移除，避免关闭后被再次弹开） */
  const clearAssignmentParam = useCallback(() => {
    const params = new URLSearchParams(window.location.search);
    if (!params.get('assignment')) return;
    params.delete('assignment');
    setSearchParams(params, { replace: true });
  }, [setSearchParams]);

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
      subject ? getMastery(subject) : Promise.resolve({ data: { summary: null, points: [] } } as any),
      listMyCourses(),
      listMyAssignments(),
      getSubjects().catch(() => [] as Array<{ subject_id: string; name: string }>),
    ]);

    const [homeRes, wrongRes, masteryRes, courseRes, assignRes, subjectRes] = results;
    if (homeRes.status === 'fulfilled') {
      setHome((homeRes.value as any)?.data || null);
    } else {
      setError('加载学习首页失败，请重试');
    }
    if (wrongRes.status === 'fulfilled') setWrong((wrongRes.value as any)?.data || []);
    if (masteryRes.status === 'fulfilled') setMastery((masteryRes.value as any)?.data?.points || []);
    if (courseRes.status === 'fulfilled') setCourses((courseRes.value as any)?.data?.courses || []);
    if (assignRes.status === 'fulfilled') setAssignments((assignRes.value as any)?.data?.assignments || []);
    if (subjectRes.status === 'fulfilled') setRegistrySubjects((subjectRes.value as any) || []);
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
   * 现在课程列表随首页一起加载，这里直接用它自动选中，不再多打一次接口。
   */
  useEffect(() => {
    if (!loggedIn || subjectId || courses.length === 0) return;
    const sid = courses.find((c) => c.subject_id)?.subject_id;
    if (sid) setSubjectId(sid);
  }, [loggedIn, subjectId, courses]);

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

  /** 基于当前课程资料提问：自动带入课程，学生不用先选课/选资料 */
  const jumpToAskCourse = () => {
    if (!currentCourse) return;
    navigate('/chat', {
      state: { preset: { mode: 'chat', subjectId: currentCourse.subject_id } },
    });
  };

  /** 带上下文跳转到 AI 学习助手（去自测 / 生成笔记 / 复习）
   *  学科优先用任务自己的，避免"先去选一遍课程"；
   *  测验类自动开始（学生不用再组织提示词），笔记类只预填。 */
  const jumpToChat = (task: StudentTask, mode: 'quiz' | 'smart_note') => {
    const topic = task.knowledge_point || task.title;
    navigate('/chat', {
      state: {
        preset: {
          mode,
          input: topic,
          taskId: task.id,
          subjectId: task.subject_id || subjectId,
          autoStart: mode === 'quiz',
        },
      },
    });
  };

  /** 去复习：学科用复习任务自己的，不要求首页先选课程；直接开始练习。
   *  带上复习 id，助手据此在作答后给出"本次复习结果 + 下次复习时间"。 */
  const goReview = (subject: string | undefined, knowledgePoint: string, reviewId?: string) => {
    navigate('/chat', {
      state: {
        preset: {
          mode: 'quiz', input: knowledgePoint,
          subjectId: subject || subjectId, autoStart: true,
          review: { id: reviewId, knowledgePoint },
        },
      },
    });
  };

  // ---- 派生数据 ----
  // 「同一复习事项只出现一次」：错题自动排的复习任务与待复习是同一件事，
  // 统一只在「待复习」里出现（任务列表过滤掉），不再额外放"已在上方列出"的说明卡。

  const pendingReviews = home?.pending_reviews || [];
  /** 区分"今天该复习"和"未来已安排的复测"：未来项不计入今天的待复习数量 */
  const todayStr = dayjs().format('YYYY-MM-DD');
  const dueReviews = pendingReviews.filter((r) => !r.due_date || r.due_date <= todayStr);
  const futureReviews = pendingReviews.filter((r) => !!r.due_date && r.due_date > todayStr);
  const nextFutureDue = futureReviews.map((r) => r.due_date).sort()[0];
  const reviewKeys = useMemo(
    () => new Set(pendingReviews.map((r) => r.knowledge_point_name)),
    [pendingReviews],
  );
  /**
   * 错题自动排出的复习任务：统一由「待复习」呈现，不在任务列表里重复出现。
   * 复习完成后（不再有 pending review）它也就不会再冒出来，
   * 不会留下一条"看起来还没做"的旧复习任务。
   */
  const isReviewDuplicate = useCallback(
    (t: StudentTask) => t.source === 'auto_review'
      || (t.task_type === 'REVIEW' && !!t.knowledge_point && reviewKeys.has(t.knowledge_point)),
    [reviewKeys],
  );
  const todayTasks = (home?.today_tasks || []).filter((t) => !isReviewDuplicate(t));
  const overdueTasks = (home?.overdue_tasks || []).filter((t) => !isReviewDuplicate(t));

  /** 待交作业：还没答完的（含已关闭但未完成的，单独标注） */
  const pendingAssignments = useMemo(
    () => assignments
      .filter((a) => !assignmentProgress(a).complete)
      .sort((a, b) => (a.due_date || '9999').localeCompare(b.due_date || '9999')),
    [assignments],
  );

  const currentCourse = courses.find((c) => c.subject_id === subjectId) || courses[0] || null;

  /** 课程优先的学科选项：课程学科显示真实课程名（不再显示注册表自动生成的时间戳名） */
  const subjectOptions = useMemo(() => {
    const map = new Map<string, string>();
    for (const c of courses) {
      if (c.subject_id) map.set(c.subject_id, c.title || c.subject_id);
    }
    for (const s of registrySubjects) {
      if (s?.subject_id && !map.has(s.subject_id)) map.set(s.subject_id, s.name || s.subject_id);
    }
    return [...map.entries()].map(([value, label]) => ({ value, label }));
  }, [courses, registrySubjects]);

  // 拉取当前课程的资料数与知识库可用性（失败保持未知，不阻断首页）
  useEffect(() => {
    let alive = true;
    const cid = currentCourse?.course_id;
    if (!cid) { setCurrentKb(null); return () => { alive = false; }; }
    setCurrentKb(null);
    (async () => {
      try {
        const res: any = await listCourseMaterials(cid);
        const kb = res?.data?.knowledge_base || {};
        if (alive) setCurrentKb({
          materials: (res?.data?.materials || []).length,
          available: !!kb.available,
        });
      } catch {
        if (alive) setCurrentKb(null);
      }
    })();
    return () => { alive = false; };
  }, [currentCourse?.course_id]);

  const renderTask = (task: StudentTask) => {
    const typeMeta = TASK_TYPE_META[task.task_type] || TASK_TYPE_META.READ;
    const overdue = !!task.overdue
      || (!!task.planned_date && task.planned_date < dayjs().format('YYYY-MM-DD')
        && task.status !== 'COMPLETED');
    const primaryLabel = task.task_type === 'QUIZ' ? '去自测'
      : task.task_type === 'REVIEW' ? '去复习'
        : task.task_type === 'PRACTICE' ? '去练习' : '看笔记';
    const primaryMode: 'quiz' | 'smart_note' = task.task_type === 'READ' ? 'smart_note' : 'quiz';
    // 复习任务不提供"完成"按钮：完成复习只能靠真实作答（答对后后端自动关闭），
    // 否则未作答也能标记完成，复习状态与掌握度会失真。
    const isReview = task.task_type === 'REVIEW' || task.source === 'auto_review';
    const showOps = !!opsOpen[task.id];
    const desc = cleanReason(task.description);
    return (
      <List.Item
        key={task.id}
        actions={[
          <Button
            key="do"
            size="small"
            type="primary"
            icon={task.task_type === 'QUIZ' || isReview
              ? <ExclamationCircleOutlined /> : <BookOutlined />}
            onClick={() => jumpToChat(task, primaryMode)}
          >
            {primaryLabel}
          </Button>,
          !isReview && (
            <Button
              key="done"
              size="small"
              icon={<CheckCircleOutlined />}
              loading={busyTask === task.id}
              disabled={task.status === 'COMPLETED'}
              onClick={() => handleComplete(task)}
            >
              完成
            </Button>
          ),
          // 改期是辅助操作：默认收起，点一下才展开日期选择
          <Button
            key="ops"
            size="small"
            type="text"
            icon={<MoreOutlined />}
            onClick={() => setOpsOpen((prev) => ({ ...prev, [task.id]: !prev[task.id] }))}
          >
            改期
          </Button>,
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
              {desc && <Text type="secondary">{desc}</Text>}
              <Space size={12} wrap>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <CalendarOutlined /> 计划日期：{task.planned_date || '未排期'}
                </Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <ClockCircleOutlined /> 预计 {task.estimated_minutes} 分钟
                </Text>
                {task.knowledge_point && (
                  <Text type="secondary" style={{ fontSize: 12 }}>知识点：{task.knowledge_point}</Text>
                )}
              </Space>
              {showOps && (
                <Space size={6} wrap>
                  <Text type="secondary" style={{ fontSize: 12 }}>调整计划日期：</Text>
                  <DatePicker
                    size="small"
                    placeholder="选择新日期"
                    value={null}
                    disabledDate={(d) => d && d < dayjs().startOf('day')}
                    onChange={(d: any) => d && handleReschedule(task, d.format('YYYY-MM-DD'))}
                    style={{ width: 140 }}
                  />
                </Space>
              )}
            </Space>
          }
        />
      </List.Item>
    );
  };

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

  /** 首页：当前课程卡片 */
  const renderCourseHero = () => (
    <Card size="small" style={{ marginBottom: 16 }}>
      {currentCourse ? (
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          <Space wrap size={10} align="center">
            <BookOutlined style={{ color: '#0f766e' }} />
            <Text strong style={{ fontSize: 16 }}>{currentCourse.title}</Text>
            <Tag color="blue">{currentCourse.class_name}</Tag>
            {courses.length > 1 && (
              <Select
                size="small"
                value={currentCourse.course_id}
                style={{ width: 180 }}
                options={courses.map((c) => ({ value: c.course_id, label: c.title }))}
                onChange={(cid) => {
                  const picked = courses.find((c) => c.course_id === cid);
                  if (picked?.subject_id) setSubjectId(picked.subject_id);
                }}
              />
            )}
          </Space>
          <Space wrap size={16}>
            <Text type="secondary">授课教师：{currentCourse.teacher_name}</Text>
            <Text type="secondary">已布置作业：{currentCourse.assignment_count ?? 0} 次</Text>
            {currentCourse.description && <Text type="secondary">{currentCourse.description}</Text>}
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            <DatabaseOutlined /> 课程资料与知识库：
            {currentKb
              ? (currentKb.materials === 0
                ? '暂无资料（可在「我的课程」查看）'
                : currentKb.materials + ' 份资料 · ' + (currentKb.available ? '知识库可用' : '知识库暂不可用'))
              : '读取中…'}
          </Text>
          <Space wrap>
            <Button type="primary" icon={<ReadOutlined />} onClick={jumpToAskCourse}>
              基于课程资料提问
            </Button>
            <Button onClick={() => goToView('courses')}>进入我的课程</Button>
            {pendingAssignments[0] && (
              <Button onClick={() => goToView('courses', { assignment: pendingAssignments[0].id })}>
                去做最近的作业
              </Button>
            )}
          </Space>
        </Space>
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="还没有加入课程。用老师给的班级邀请码加入后，这里会显示你的课程与作业。"
        >
          <Button type="primary" onClick={() => goToView('courses')}>输入邀请码加入课程</Button>
        </Empty>
      )}
    </Card>
  );

  /** 首页：今天的任务（逾期是筛选） */
  const renderTasks = () => {
    const shown = taskFilter === 'today' ? todayTasks : overdueTasks;
    return (
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
            <Text strong><CalendarOutlined /> 今天的任务</Text>
            <Segmented
              size="small"
              value={taskFilter}
              onChange={(v) => setTaskFilter(v as 'today' | 'overdue')}
              options={[
                { label: '今天（' + todayTasks.length + '）', value: 'today' },
                { label: '已逾期（' + overdueTasks.length + '）', value: 'overdue' },
              ]}
            />
          </Space>
          {shown.length === 0 ? (
            // 空任务区保持一行：有待交作业优先引导作业；都没有才提供"安排学习"入口
            <Space size={8} wrap>
              <Text type="secondary">
                {taskFilter === 'overdue' ? '没有逾期任务。' : '今天没有安排任务。'}
              </Text>
              {taskFilter === 'today' && pendingAssignments.length > 0 && (
                <Button size="small" type="primary"
                        onClick={() => goToView('courses', { assignment: pendingAssignments[0].id })}>
                  先完成待交作业
                </Button>
              )}
              {taskFilter === 'today' && pendingAssignments.length === 0
                && dueReviews.length > 0 && (
                  <Text type="secondary">先看看下方的待复习。</Text>
                )}
              {taskFilter === 'today' && pendingAssignments.length === 0
                && dueReviews.length === 0 && (
                  currentCourse ? (
                    <Button size="small" type="primary" icon={<RocketOutlined />}
                            onClick={() => jumpToAgent('')}>
                      让学习助手安排今天的学习
                    </Button>
                  ) : (
                    <Button size="small" type="primary" onClick={() => goToView('courses')}>
                      先加入一门课程
                    </Button>
                  )
                )}
            </Space>
          ) : (
            <List dataSource={shown} renderItem={renderTask} />
          )}
        </Space>
      </Card>
    );
  };

  /** 首页：待交作业 */
  const renderAssignments = () => (
    <Card size="small" style={{ marginBottom: 16 }}>
      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        <Text strong><ExclamationCircleOutlined /> 待交作业（{pendingAssignments.length}）</Text>
        {pendingAssignments.length === 0 ? (
          <Text type="secondary">没有待完成的作业。</Text>
        ) : (
          <List
            dataSource={pendingAssignments}
            renderItem={(a) => {
              const { done, total } = assignmentProgress(a);
              const overdue = !!a.due_date && a.due_date < dayjs().format('YYYY-MM-DD');
              return (
                <List.Item
                  actions={[
                    <Button key="open" size="small" type="primary" ghost
                            onClick={() => goToView('courses', { assignment: a.id })}>
                      去作答
                    </Button>,
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <Space wrap>
                        <Text strong>{a.title}</Text>
                        {a.status === 'closed'
                          ? <Tag>已关闭</Tag>
                          : <Tag color="green">进行中</Tag>}
                        {a.due_date && (
                          <Tag color={overdue ? 'error' : 'orange'}>截止 {a.due_date}</Tag>
                        )}
                        {a.course_title && <Tag>{a.course_title}</Tag>}
                      </Space>
                    }
                    description={
                      <Space wrap size={12}>
                        <Text type="secondary">题量：{total} 题</Text>
                        <Text type="secondary">已作答：{done} / {total}</Text>
                        {(a.knowledge_points || []).length > 0 && (
                          <Text type="secondary">知识点：{a.knowledge_points!.join('、')}</Text>
                        )}
                      </Space>
                    }
                  />
                </List.Item>
              );
            }}
          />
        )}
      </Space>
    </Card>
  );

  /** 首页：今天要复习（未来已安排的复测只做说明，不计入今天的数量） */
  const renderReviews = () => (
    <Card size="small" style={{ marginBottom: 16 }}>
      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        <Text strong><ClockCircleOutlined /> 今天要复习（{dueReviews.length}）</Text>
        {dueReviews.length === 0 ? (
          <Text type="secondary">今天没有需要复习的内容。</Text>
        ) : (
          <List
            dataSource={dueReviews}
            renderItem={(r) => (
              <List.Item
                actions={[
                  <Button key="go" size="small" type="primary"
                          onClick={() => goReview(r.subject_id, r.knowledge_point_name, r.id)}>
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
                  description={
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {cleanReason(r.reason) || '答错后自动安排'}
                    </Text>
                  }
                />
              </List.Item>
            )}
          />
        )}
        {futureReviews.length > 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            已安排复测 {futureReviews.length} 项
            {nextFutureDue ? '，最近到 ' + nextFutureDue : ''}（到期后可在这里完成）
          </Text>
        )}
      </Space>
    </Card>
  );

  /** 首页：次级入口（课程/错题/掌握度/计划/记忆） */
  const renderSecondaryEntries = () => (
    <Card size="small" title="更多学习入口">
      <Space wrap size={8}>
        {SECONDARY_NAV.map((item) => (
          <Button key={item.key} icon={item.icon} onClick={() => goToView(item.key)}>
            {item.label}
            {item.key === 'wrong' && wrong.length > 0 ? '（' + wrong.length + '）' : ''}
            {item.key === 'courses' && courses.length > 0 ? '（' + courses.length + '）' : ''}
          </Button>
        ))}
      </Space>
    </Card>
  );

  const renderWrong = () => (
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
                ? shown.correct_option_id + '（' + shown.correct_option_text + '）'
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
  );

  const renderMastery = () => (
    <Card size="small"
          title="知识点掌握度（当前掌握=每题最新有效作答；历史错误用于复习与趋势）">
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
          scroll={{ x: 920 }}
          className="app-table-scroll"
          columns={[
            { title: '知识点', dataIndex: 'knowledge_point' },
            {
              /* 当前掌握：只看每题的**最新一次有效作答**。
                 无有效作答（或后端旧版本未下发 latest_view）时显示"待评估"，
                 绝不显示 0% —— 0% 会被误读成"完全不会"。 */
              title: '当前掌握',
              width: 210,
              render: (_: any, r: MasteryPoint) => {
                const lv = r.latest_view?.latest_mastery_level;
                const hist = r.latest_view;
                if (lv == null) {
                  return <Text type="secondary">待评估</Text>;
                }
                const pct = Math.round(lv * 100);
                const color = masteryColorOf(lv);
                return (
                  <Space direction="vertical" size={0}>
                    <Space size={8}>
                      <Progress percent={pct} size="small" strokeColor={color}
                                style={{ width: 72 }} showInfo={false} />
                      <Text strong style={{ color }}>{pct}%</Text>
                    </Space>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      基于作答记录的估计
                      {hist && hist.history_attempt_count != null
                        ? '（历史 ' + hist.history_attempt_count + ' 次 / 答错 ' + (hist.history_wrong_count ?? 0) + ' 次）'
                        : ''}
                    </Text>
                  </Space>
                );
              },
            },
            {
              /* 建议行动必须落到真实流程：复习在首页、错题本在次级入口。
                 如实表达行为，不写"定位到该知识点"这种尚未实现的话。 */
              title: '建议行动',
              width: 210,
              render: (_: any, r: MasteryPoint) => {
                const lv = r.latest_view?.latest_mastery_level;
                const weak = lv == null || lv < 0.6;
                return (
                  <Space size={6} wrap>
                    <Button size="small" type={weak ? 'primary' : 'default'}
                            onClick={() => goToView('home')}>
                      查看复习安排
                    </Button>
                    <Button size="small" type="link"
                            onClick={() => goToView('wrong')}>
                      查看错题本
                    </Button>
                  </Space>
                );
              },
            },
          ]}
        />
      )}
      <Divider style={{ margin: '12px 0 8px' }} />
      <Text type="secondary" style={{ fontSize: 12 }}>
        掌握度按你最近一次的有效作答更新（先错后对会变好）；历史错误只用于复习建议。
      </Text>
    </Card>
  );

  return (
    <div style={{ padding: 16, maxWidth: 1000, margin: '0 auto' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        {view === 'home' ? (
          <Title level={4} style={{ margin: 0 }}>学习首页</Title>
        ) : (
          <Space wrap>
            <Button icon={<ArrowLeftOutlined />} onClick={() => goToView('home')}>返回学习首页</Button>
            <Title level={4} style={{ margin: 0 }}>{VIEW_TITLE[view]}</Title>
          </Space>
        )}
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={() => loadAll(subjectId)} loading={loading}>
            刷新
          </Button>
          <Tooltip title="用一句话说出目标与限制，学习助手会读取你的真实学习记录来安排">
            <Button type="primary" icon={<RocketOutlined />} onClick={() => jumpToAgent('')}>
              让学习助手安排
            </Button>
          </Tooltip>
        </Space>
      </Space>

      {error && (
        <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error}
               action={<Button size="small" onClick={() => loadAll(subjectId)}>重试</Button>} />
      )}

      {view === 'home' && (
        loading && !home ? <Spin /> : (
          <>
            {renderCourseHero()}
            {renderTasks()}
            {renderAssignments()}
            {renderReviews()}
            {renderSecondaryEntries()}
          </>
        )
      )}

      {view !== 'home' && (
        <Space wrap size={8} style={{ marginBottom: 12 }}>
          {SECONDARY_NAV.map((item) => (
            <Button
              key={item.key}
              type={view === item.key ? 'primary' : 'default'}
              icon={item.icon}
              onClick={() => goToView(item.key)}
            >
              {item.label}
            </Button>
          ))}
          {(view === 'wrong' || view === 'mastery' || view === 'plan' || view === 'memory') && (
            <Space size={6} wrap>
              <Text type="secondary">学科</Text>
              <Select
                value={subjectId || undefined}
                onChange={setSubjectId}
                options={subjectOptions}
                showSearch
                optionFilterProp="label"
                placeholder="选择课程"
                style={{ width: 220 }}
              />
            </Space>
          )}
        </Space>
      )}

      {view === 'courses' && (
        <StudentCourses
          onJoined={(sid) => { if (sid) setSubjectId(sid); }}
          focusAssignmentId={focusAssignmentId}
          onFocusConsumed={clearAssignmentParam}
          onChanged={() => loadAll(subjectId)}
        />
      )}
      {view === 'wrong' && renderWrong()}
      {view === 'mastery' && renderMastery()}
      {view === 'plan' && <PlanBudgetPanel subjectId={subjectId} />}
      {view === 'memory' && <LearningMemoryPanel subjectId={subjectId} />}
    </div>
  );
};

export default StudentPage;
