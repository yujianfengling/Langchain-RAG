import React, { useState, useEffect, useCallback } from 'react';
import { motion } from 'framer-motion';
import {
  Card, Input, Button, Typography, Space, List, Tag, Timeline,
  Empty, message, Popconfirm,
} from 'antd';
import {
  CalendarOutlined, PlusOutlined, DeleteOutlined, ReloadOutlined,
  PlayCircleOutlined, CheckCircleOutlined,
} from '@ant-design/icons';
import SubjectSelector from '../components/SubjectSelector';
import {
  generatePlan, getPlans, deletePlan, updateTaskStatus,
  LearningPlan, LearningTask, TaskStatus, TaskType,
} from '../api/learning';

const { TextArea } = Input;
const { Title, Paragraph, Text } = Typography;

// 任务类型标签映射
const TASK_TYPE_META: Record<TaskType, { color: string; label: string }> = {
  READ: { color: 'cyan', label: '阅读' },
  REVIEW: { color: 'purple', label: '复习' },
  QUIZ: { color: 'green', label: '自测' },
  PRACTICE: { color: 'orange', label: '练习' },
};

const TASK_STATUS_META: Record<TaskStatus, { color: string; label: string }> = {
  TODO: { color: 'default', label: '未开始' },
  IN_PROGRESS: { color: 'processing', label: '进行中' },
  COMPLETED: { color: 'success', label: '已完成' },
};

// 状态按钮（TODO→IN_PROGRESS→COMPLETED 流转）
const StatusAction: React.FC<{ task: LearningTask; onChanged: (t: LearningTask) => void }> = ({ task, onChanged }) => {
  const [loading, setLoading] = useState(false);

  const nextStatus: TaskStatus =
    task.status === 'TODO' ? 'IN_PROGRESS' : task.status === 'IN_PROGRESS' ? 'COMPLETED' : 'COMPLETED';

  const handleClick = async () => {
    setLoading(true);
    try {
      const res = await updateTaskStatus(task.id, nextStatus);
      onChanged(res.data);
      if (nextStatus === 'COMPLETED') message.success('任务已完成');
    } catch (e) {
      message.error('更新任务状态失败');
    } finally {
      setLoading(false);
    }
  };

  const icon = nextStatus === 'COMPLETED' ? <CheckCircleOutlined /> : <PlayCircleOutlined />;
  const label = task.status === 'TODO' ? '开始' : task.status === 'IN_PROGRESS' ? '完成' : '已完成';

  return (
    <Button size="small" icon={icon} onClick={handleClick} loading={loading} disabled={task.status === 'COMPLETED'}>
      {label}
    </Button>
  );
};

const LearningPlanPage: React.FC = () => {
  const [subjectId, setSubjectId] = useState<string>('');
  const [goal, setGoal] = useState('');
  const [plans, setPlans] = useState<LearningPlan[]>([]);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);

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

  // 按 day 分组（通过 priority 推断天序，此处简单按任务顺序分组）
  const groupTasksByDay = (tasks: LearningTask[]): LearningTask[][] => {
    const groups: LearningTask[][] = [];
    let current: LearningTask[] = [];
    for (const t of tasks) {
      current.push(t);
      // 简化：按 2-3 个任务一组作为一天的近似（真实 day 字段在生成时用 priority 表示）
      if (current.length >= 2) {
        groups.push(current);
        current = [];
      }
    }
    if (current.length) groups.push(current);
    return groups.length ? groups : [[]];
  };

  const renderPlan = (plan: LearningPlan) => {
    // 合理防御：列表接口可能未携带 tasks（或旧数据缺字段），避免迭代崩溃
    const tasks = Array.isArray(plan.tasks) ? plan.tasks : [];
    const days = groupTasksByDay(tasks);
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
          </Space>
        }
        extra={
          <Popconfirm title="确定删除该计划？" onConfirm={() => handleDelete(plan.id)}>
            <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        }
      >
        <Paragraph type="secondary" style={{ marginBottom: 12 }}>
          目标：{plan.goal}
          {plan.start_date && <Text style={{ marginLeft: 8 }}>（{plan.start_date} ~ {plan.end_date}）</Text>}
        </Paragraph>
        <Timeline
          items={days.map((dayTasks, idx) => ({
            children: (
              <div>
                <Text strong>Day {idx + 1}</Text>
                <List
                  size="small"
                  dataSource={dayTasks}
                  style={{ marginTop: 8 }}
                  renderItem={(t) => {
                    const typeMeta = TASK_TYPE_META[t.task_type] || TASK_TYPE_META.READ;
                    const statusMeta = TASK_STATUS_META[t.status] || TASK_STATUS_META.TODO;
                    return (
                      <List.Item
                        actions={[<StatusAction key={t.id} task={t} onChanged={(nt) => {
                          setPlans((prev) => prev.map((p) =>
                            p.id === plan.id
                              ? { ...p, tasks: (p.tasks || []).map((tt) => (tt.id === nt.id ? nt : tt)) }
                              : p
                          ));
                        }} />]}
                      >
                        <List.Item.Meta
                          title={
                            <Space size={8}>
                              <Tag color={typeMeta.color}>{typeMeta.label}</Tag>
                              <Text>{t.title}</Text>
                              {t.knowledge_point && <Text type="secondary" style={{ fontSize: 12 }}>{t.knowledge_point}</Text>}
                              <Tag color={statusMeta.color}>{statusMeta.label}</Tag>
                              <Text type="secondary" style={{ fontSize: 12 }}>~{t.estimated_minutes}分钟</Text>
                            </Space>
                          }
                          description={t.description || undefined}
                        />
                      </List.Item>
                    );
                  }}
                />
              </div>
            ),
          }))}
        />
      </Card>
    );
  };

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: '0 auto' }}>
      <Title level={4}>学习计划</Title>

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
            计划基于知识库知识点由 AI 生成，保存后刷新页面仍会保留。
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
