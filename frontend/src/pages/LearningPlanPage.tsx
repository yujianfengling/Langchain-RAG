import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  Alert, Button, Card, DatePicker, Empty, Input, List, Popconfirm, Space,
  Tag, Tooltip, Typography, message,
} from 'antd';
import {
  CalendarOutlined, PlusOutlined, DeleteOutlined, ReloadOutlined,
  CheckCircleOutlined, ExclamationCircleOutlined, ReadOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import SubjectSelector from '../components/SubjectSelector';
import { isLoggedIn } from '../api/auth';
import { AUTH_CHANGED_EVENT } from '../components/UserAuth';
import {
  generatePlan, getPlans, deletePlan, LearningPlan, LearningTask, TaskType,
} from '../api/learning';
import { completeTask, rescheduleTask } from '../api/student';

const { TextArea } = Input;
const { Title, Text } = Typography;

const TASK_TYPE_META: Record<TaskType, { color: string; label: string }> = {
  READ: { color: 'cyan', label: '阅读' },
  REVIEW: { color: 'purple', label: '复习' },
  QUIZ: { color: 'green', label: '自测' },
  PRACTICE: { color: 'orange', label: '练习' },
};

const TODAY = () => dayjs().format('YYYY-MM-DD');

const LearningPlanPage: React.FC = () => {
  const navigate = useNavigate();
  const [subjectId, setSubjectId] = useState<string>('');
  const [goal, setGoal] = useState('');
  const [plans, setPlans] = useState<LearningPlan[]>([]);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [busyTask, setBusyTask] = useState<string | null>(null);
  const [loggedIn, setLoggedIn] = useState(isLoggedIn());

  useEffect(() => {
    const sync = () => setLoggedIn(isLoggedIn());
    window.addEventListener('storage', sync);
    window.addEventListener(AUTH_CHANGED_EVENT, sync);
    return () => {
      window.removeEventListener('storage', sync);
      window.removeEventListener(AUTH_CHANGED_EVENT, sync);
    };
  }, []);

  const loadPlans = useCallback(async (sid?: string) => {
    setLoading(true);
    try {
      const res = await getPlans(sid);
      setPlans(res.data || []);
    } catch (e) {
      message.error('加载学习计划失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPlans(subjectId || undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleGenerate = async () => {
    if (!subjectId) { message.warning('请选择学科'); return; }
    if (!goal.trim()) { message.warning('请输入学习目标'); return; }

    setGenerating(true);
    try {
      const res = await generatePlan({ subject_id: subjectId, goal: goal.trim() });
      if (res.success) {
        message.success('学习计划生成成功');
        setGoal('');
        await loadPlans(subjectId);
      } else {
        message.error(res.message || '生成失败');
      }
    } catch (e: any) {
      const errType = e?.response?.data?.error_type;
      let msg = e?.response?.data?.message || '学习计划生成失败';
      if (errType === 'knowledge_base_not_ready') {
        msg = '请先构建该课程的知识库';
      } else if (errType === 'subject_not_found') {
        msg = '课程不存在，请先创建课程';
      } else if (errType === 'model_api_error') {
        msg = 'AI生成服务暂时不可用，请稍后重试';
      }
      message.error(msg);
    } finally {
      setGenerating(false);
    }
  };

  const handleDelete = async (planId: string) => {
    try {
      await deletePlan(planId);
      message.success('计划已删除');
      loadPlans(subjectId || undefined);
    } catch (e) {
      message.error('删除失败');
    }
  };

  /** 完成任务：走学生端幂等接口（只记行为；测验任务需先作答） */
  const handleComplete = async (task: LearningTask) => {
    setBusyTask(task.id);
    try {
      await completeTask(task.id, task.actual_minutes || undefined);
      message.success('任务已完成');
      await loadPlans(subjectId || undefined);
    } catch (e: any) {
      const errType = e?.response?.data?.error_type;
      message[errType === 'quiz_attempt_required' ? 'warning' : 'error'](
        e?.response?.data?.message || '完成任务失败');
    } finally {
      setBusyTask(null);
    }
  };

  const handleReschedule = async (task: LearningTask, dateStr: string) => {
    setBusyTask(task.id);
    try {
      await rescheduleTask(task.id, dateStr, '学生手动调整');
      message.success('计划日期已调整（已保留原日期与调整记录）');
      await loadPlans(subjectId || undefined);
    } catch (e: any) {
      message.error(e?.response?.data?.message || '调整日期失败');
    } finally {
      setBusyTask(null);
    }
  };

  // 任务所属计划的学科（任务未带 subject_id 时从计划取）
  const planSubjectId = (task: LearningTask) => {
    const plan = plans.find((p) => p.id === task.plan_id);
    return plan?.subject_id || subjectId;
  };

  /** 带上下文跳到 AI 学习助手（去自测 / 生成笔记 / 阅读） */
  const jumpToChat = (task: LearningTask, mode: 'quiz' | 'smart_note') => {
    navigate('/chat', {
      state: {
        preset: {
          mode,
          input: task.knowledge_point || task.title,
          taskId: task.id,
          subjectId: task.subject_id || planSubjectId(task),
        },
      },
    });
  };

  const renderTask = (t: LearningTask) => {
    const typeMeta = TASK_TYPE_META[t.task_type] || TASK_TYPE_META.READ;
    const planned = t.planned_date;
    const overdue = !!planned && planned < TODAY() && t.status !== 'COMPLETED';
    const isToday = planned === TODAY();
    return (
      <List.Item
        key={t.id}
        actions={[
          <Button
            key="do"
            size="small"
            type="primary"
            ghost
            icon={t.task_type === 'QUIZ' ? <ExclamationCircleOutlined /> : <ReadOutlined />}
            onClick={() => jumpToChat(t, t.task_type === 'QUIZ' ? 'quiz' : 'smart_note')}
          >
            {t.task_type === 'QUIZ' ? '去自测' : '看笔记'}
          </Button>,
          <Button
            key="done"
            size="small"
            icon={<CheckCircleOutlined />}
            loading={busyTask === t.id}
            disabled={t.status === 'COMPLETED'}
            onClick={() => handleComplete(t)}
          >
            完成
          </Button>,
          <DatePicker
            key="date"
            size="small"
            placeholder="改期"
            value={null}
            disabledDate={(d) => !!d && d < dayjs().startOf('day')}
            onChange={(d: any) => d && handleReschedule(t, d.format('YYYY-MM-DD'))}
            style={{ width: 116 }}
          />,
        ]}
      >
        <List.Item.Meta
          title={
            <Space size={8} wrap>
              <Tag color={typeMeta.color}>{typeMeta.label}</Tag>
              <Text delete={t.status === 'COMPLETED'}>{t.title}</Text>
              {isToday && t.status !== 'COMPLETED' && <Tag color="blue">今日</Tag>}
              {overdue && <Tag color="error">已逾期</Tag>}
              {t.source === 'auto_review' && <Tag color="purple">错题自动安排</Tag>}
              {t.status === 'COMPLETED' && <Tag color="success">已完成</Tag>}
              {!!t.defer_count && (
                <Tooltip title={`原计划日期：${t.original_planned_date || '—'}`}>
                  <Tag>已改期 {t.defer_count} 次</Tag>
                </Tooltip>
              )}
            </Space>
          }
          description={
            <Space direction="vertical" size={2}>
              {t.description && <Text type="secondary">{t.description}</Text>}
              <Space size={12} wrap>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <CalendarOutlined /> 计划日期：{planned || '未排期'}
                </Text>
                <Text type="secondary" style={{ fontSize: 12 }}>预计 {t.estimated_minutes} 分钟</Text>
                {t.knowledge_point && (
                  <Text type="secondary" style={{ fontSize: 12 }}>知识点：{t.knowledge_point}</Text>
                )}
              </Space>
            </Space>
          }
        />
      </List.Item>
    );
  };

  /** 按真实计划日期分组（不再用"每 2 个任务算一天"的近似） */
  const groupByDate = (tasks: LearningTask[]) => {
    const groups = new Map<string, LearningTask[]>();
    for (const t of tasks) {
      const key = t.planned_date || '未排期';
      const list = groups.get(key) || [];
      list.push(t);
      groups.set(key, list);
    }
    return Array.from(groups.entries()).sort(([a], [b]) => {
      if (a === '未排期') return 1;
      if (b === '未排期') return -1;
      return a < b ? -1 : 1;
    });
  };

  const renderPlan = (plan: LearningPlan) => {
    const tasks = Array.isArray(plan.tasks) ? plan.tasks : [];
    const groups = groupByDate(tasks);
    const overdueCount = tasks.filter(
      (t) => t.planned_date
        && t.planned_date! < TODAY() && t.status !== 'COMPLETED').length;
    return (
      <Card
        size="small"
        key={plan.id}
        style={{ marginBottom: 16 }}
        title={
          <Space>
            <CalendarOutlined />
            {plan.title}
            <Tag color={plan.status === 'ACTIVE' ? 'processing' : 'default'}>
              {plan.status === 'ACTIVE' ? '进行中' : plan.status}
            </Tag>
            {overdueCount > 0 && <Tag color="error">{overdueCount} 项逾期</Tag>}
          </Space>
        }
        extra={
          <Popconfirm title="确定删除该计划？" onConfirm={() => handleDelete(plan.id)}>
            <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        }
      >
        <Space direction="vertical" size={4} style={{ marginBottom: 12 }}>
          <Text type="secondary">目标：{plan.goal}</Text>
          {plan.start_date && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              计划周期：{plan.start_date} ~ {plan.end_date}
            </Text>
          )}
        </Space>

        {groups.length === 0 ? <Empty description="该计划暂无任务" /> : groups.map(([date, dayTasks]) => (
          <div key={date} style={{ marginBottom: 12 }}>
            <Space size={8} style={{ marginBottom: 4 }}>
              <Text strong>{date}</Text>
              {date === TODAY() && <Tag color="blue">今天</Tag>}
              {date !== '未排期' && date < TODAY() && <Tag color="error">已过期</Tag>}
              <Text type="secondary" style={{ fontSize: 12 }}>
                {dayTasks.filter((t) => t.status === 'COMPLETED').length}/{dayTasks.length} 已完成
              </Text>
            </Space>
            <List size="small" dataSource={dayTasks} renderItem={renderTask} />
          </div>
        ))}
      </Card>
    );
  };

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: '0 auto' }}>
      <Title level={4}>学习计划</Title>

      {!loggedIn && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="当前未登录：可以查看已有计划，但「完成 / 改期」需要登录后归属到你的账号。"
        />
      )}

      {/* 生成计划 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <SubjectSelector value={subjectId} onChange={setSubjectId} style={{ maxWidth: 320 }} />
          <TextArea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="输入学习目标，如：三天复习完数据结构中的树和图"
            rows={2}
          />
          <Space>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleGenerate} loading={generating}>
              {generating ? '生成中...' : '生成学习计划'}
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => loadPlans(subjectId || undefined)}>
              刷新
            </Button>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            计划基于知识库知识点由 AI 生成，任务带真实计划日期；逾期任务与今日任务可在「我的学习」集中处理。
          </Text>
        </Space>
      </Card>

      {/* 计划列表 */}
      {loading ? (
        <Empty description="加载中..." />
      ) : plans.length === 0 ? (
        <Empty description="暂无学习计划，输入目标生成一个吧" />
      ) : (
        plans.map((plan, index) => (
          <motion.div
            key={plan.id}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(index * 0.04, 0.4), duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
          >
            {renderPlan(plan)}
          </motion.div>
        ))
      )}
    </div>
  );
};

export default LearningPlanPage;
