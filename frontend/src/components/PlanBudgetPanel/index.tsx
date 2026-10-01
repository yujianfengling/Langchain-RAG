import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, Card, DatePicker, Empty, Form, InputNumber, List,
  Popconfirm, Space, Statistic, Switch, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  CheckOutlined, CloseOutlined, ReloadOutlined, UndoOutlined, WarningOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import {
  PlanAdjustment, PlanBudget, PlanConstraints,
  getBudget, getConstraints, saveConstraints, proposeAdjustment,
  listAdjustments, applyAdjustment, undoAdjustment, rejectAdjustment,
} from '../../api/planAgent';

const { Text, Paragraph } = Typography;

const ADJ_STATUS_META: Record<string, { color: string; label: string }> = {
  PROPOSED: { color: 'processing', label: '待确认' },
  APPLIED: { color: 'success', label: '已应用' },
  UNDONE: { color: 'default', label: '已撤销' },
  REJECTED: { color: 'warning', label: '已拒绝' },
};

/**
 * 计划约束与调整（阶段二）
 *
 * - 截止日期 / 每天可用时长 / 教师固定截止 / 自动调整授权
 * - 预算与冲突由后端程序判定，前端只展示，不自己算
 * - 调整建议展示变更内容与原因，支持确认应用与撤销
 */
const PlanBudgetPanel: React.FC<{ subjectId?: string }> = ({ subjectId }) => {
  const [constraints, setConstraints] = useState<PlanConstraints | null>(null);
  const [budget, setBudget] = useState<PlanBudget | null>(null);
  const [adjustments, setAdjustments] = useState<PlanAdjustment[]>([]);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    if (!subjectId) { setConstraints(null); setBudget(null); setAdjustments([]); return; }
    setLoading(true);
    try {
      const [c, b, a] = await Promise.all([
        getConstraints(subjectId), getBudget(subjectId), listAdjustments(subjectId),
      ]);
      const cd = (c as any)?.data || null;
      setConstraints(cd);
      setBudget((b as any)?.data || null);
      setAdjustments((a as any)?.data || []);
      if (cd) {
        form.setFieldsValue({
          deadline: cd.deadline ? dayjs(cd.deadline) : null,
          daily_minutes: cd.daily_minutes,
          auto_adjust: !!cd.auto_adjust,
        });
      }
    } catch {
      message.error('加载计划约束失败');
    } finally {
      setLoading(false);
    }
  }, [subjectId, form]);

  useEffect(() => { load(); }, [load]);

  const handleSave = async () => {
    if (!subjectId) { message.warning('请先选择学科'); return; }
    const v = form.getFieldsValue();
    try {
      await saveConstraints({
        subject_id: subjectId,
        deadline: v.deadline ? dayjs(v.deadline).format('YYYY-MM-DD') : null,
        daily_minutes: v.daily_minutes ?? null,
        auto_adjust: !!v.auto_adjust,
      });
      message.success('已保存（后续安排会遵守这些限制）');
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '保存失败');
    }
  };

  const handlePropose = async () => {
    if (!subjectId) { message.warning('请先选择学科'); return; }
    try {
      const res: any = await proposeAdjustment({ subject_id: subjectId, reason: '学生手动触发重排' });
      const adj = res?.data;
      message.success(adj?.duplicate ? '已存在相同建议（未重复创建）' : '已生成调整建议');
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '生成建议失败');
    }
  };

  const handleApply = async (adj: PlanAdjustment, confirm: boolean) => {
    try {
      const res: any = await applyAdjustment(adj.id, confirm);
      const data = res?.data;
      const skipped = (data?.skipped_changes || []).length;
      message.success(`已应用 ${(data?.applied_changes || []).length} 项调整`
        + (skipped ? `，跳过 ${skipped} 项（已被手动修改或已完成）` : ''));
      load();
    } catch (e: any) {
      const body = e?.response?.data;
      if (body?.error_type === 'confirmation_required') {
        // 兜底提示：正常情况下「确认应用」会带 confirm=true 并在确认框中征得同意，
        // 走到这里说明服务端在提议之后仍判定为较大调整（例如任务集发生变化）。
        message.warning('该调整被服务端判定为较大范围，需要确认后才能应用：'
          + '请重新点击「确认应用」，并在确认框中选择「确认应用」。');
      } else {
        message.error(body?.message || '应用失败');
      }
    }
  };

  const handleUndo = async (adj: PlanAdjustment) => {
    try {
      await undoAdjustment(adj.id);
      message.success('已撤销，任务日期恢复为调整前');
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '撤销失败');
    }
  };

  const handleReject = async (adj: PlanAdjustment) => {
    try {
      await rejectAdjustment(adj.id);
      message.success('已拒绝该建议');
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '操作失败');
    }
  };

  if (!subjectId) {
    return (
      <Card size="small" title="计划约束与调整">
        <Alert type="info" showIcon message="请先在上方选择学科，再查看时间预算与调整建议。" />
      </Card>
    );
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Card
        size="small"
        title="我的时间安排"
        extra={<Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>}
      >
        <Form form={form} layout="inline" onFinish={handleSave}>
          <Form.Item name="deadline" label="截止日期">
            <DatePicker placeholder="例如考试当天" />
          </Form.Item>
          <Form.Item name="daily_minutes" label="每天可用" rules={[{ type: 'number', min: 10, max: 600 }]}>
            <InputNumber min={10} max={600} step={5} addonAfter="分钟" style={{ width: 150 }} />
          </Form.Item>
          <Form.Item name="auto_adjust" label="自动调整" valuePropName="checked">
            <Switch size="small" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" onClick={handleSave}>保存限制</Button>
          </Form.Item>
        </Form>
        <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
          「自动调整」默认关闭：重大调整需要你确认后才应用。教师/课程的固定截止时间由后端判定，不能被推后。
          {constraints?.teacher_deadline && (
            <Text strong> 当前教师固定截止：{constraints.teacher_deadline}</Text>
          )}
        </Paragraph>
      </Card>

      {budget && (
        <Card size="small" title="时间预算检查（由后端程序判定）">
          <Space size={28} wrap>
            <Statistic title="剩余任务" value={budget.remaining_tasks} suffix="个" />
            <Statistic title="预计需要" value={budget.remaining_minutes} suffix="分钟" />
            <Statistic title="每天可用" value={budget.daily_minutes} suffix="分钟" />
            <Statistic
              title="截止前可用"
              value={budget.capacity_minutes ?? '—'}
              suffix={budget.capacity_minutes != null ? '分钟' : ''}
            />
            <Statistic
              title="剩余天数"
              value={budget.days_left ?? '—'}
              suffix={budget.days_left != null ? '天' : ''}
            />
          </Space>

          {budget.fits ? (
            <Alert type="success" showIcon style={{ marginTop: 12 }}
                   message="当前安排在你的时间预算内" />
          ) : (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 12 }}
              message="存在时间冲突：按当前预算无法在截止前完成"
              description={
                <div>
                  {budget.conflicts.map((c, i) => (
                    <div key={i}>· {c}</div>
                  ))}
                  {budget.suggestions.length > 0 && (
                    <div style={{ marginTop: 6 }}>
                      <b>建议方案：</b>
                      {budget.suggestions.map((s, i) => <div key={i}>· {s}</div>)}
                    </div>
                  )}
                  <div style={{ marginTop: 8 }}>
                    <Button size="small" type="primary" onClick={handlePropose}>
                      生成重排建议
                    </Button>
                    <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                      系统不会直接改你的计划，也不会排出注定超时的安排
                    </Text>
                  </div>
                </div>
              }
            />
          )}
        </Card>
      )}

      <Card
        size="small"
        title="调整记录（含原因与撤销入口）"
        extra={<Button size="small" onClick={handlePropose}>生成调整建议</Button>}
      >
        {adjustments.length === 0 ? (
          <Empty description="暂无调整记录" />
        ) : (
          <List
            dataSource={adjustments}
            renderItem={(adj) => {
              const meta = ADJ_STATUS_META[adj.status] || ADJ_STATUS_META.PROPOSED;
              const reschedules = adj.changes.filter((c: any) => c.change_type === 'reschedule');
              const reduces = adj.changes.filter((c: any) => c.change_type === 'reduce_scope');
              // 较大调整的确认框内容：先把"改什么、影响什么"摆出来，用户确认后才发送 confirm:true。
              // 取消 / 只是查看都不会应用（cancel 不触发 onConfirm）。
              const confirmDetail = (
                <div style={{ fontSize: 12, maxWidth: 300 }}>
                  <div>
                    将调整 {reschedules.length} 项任务的日期
                    {reduces.length > 0 ? '，并缩小学习范围' : ''}：
                  </div>
                  {reschedules.slice(0, 5).map((c: any, i: number) => (
                    <div key={i} style={{ color: '#475569' }}>
                      · {c.title}：{c.from_date || '未排期'} → <b>{c.to_date}</b>
                    </div>
                  ))}
                  {reschedules.length > 5 && <div>…还有 {reschedules.length - 5} 项</div>}
                  {reduces.length > 0 && (
                    <div style={{ color: '#b45309', marginTop: 4 }}>{reduces[0].note}</div>
                  )}
                  <div style={{ color: '#b45309', marginTop: 4 }}>
                    确认后任务日期会立即改变；已被手动改期或已完成的任务会被跳过，可在「调整记录」里撤销。
                  </div>
                </div>
              );
              return (
                <List.Item
                  actions={[
                    adj.status === 'PROPOSED' && (
                      <Space key="p" size={4}>
                        {adj.requires_confirmation ? (
                          <Popconfirm
                            title="确认应用这次较大范围的调整？"
                            description={confirmDetail}
                            okText="确认应用"
                            cancelText="取消"
                            onConfirm={() => handleApply(adj, true)}
                          >
                            <Button size="small" type="primary" icon={<CheckOutlined />}>
                              确认应用
                            </Button>
                          </Popconfirm>
                        ) : (
                          <Tooltip title="应用调整">
                            <Button size="small" type="primary" ghost icon={<CheckOutlined />}
                                    onClick={() => handleApply(adj, false)}>
                              应用
                            </Button>
                          </Tooltip>
                        )}
                        <Button size="small" icon={<CloseOutlined />}
                                onClick={() => handleReject(adj)}>拒绝</Button>
                      </Space>
                    ),
                    adj.status === 'APPLIED' && (
                      <Popconfirm key="u" title="撤销后任务日期会恢复为调整前"
                                  onConfirm={() => handleUndo(adj)}>
                        <Button size="small" icon={<UndoOutlined />}>撤销</Button>
                      </Popconfirm>
                    ),
                  ].filter(Boolean)}
                >
                  <List.Item.Meta
                    title={
                      <Space size={8} wrap>
                        <Tag color={meta.color}>{meta.label}</Tag>
                        {adj.major && <Tag color="volcano" icon={<WarningOutlined />}>较大调整</Tag>}
                        <Text>{adj.summary || adj.reason || '计划调整'}</Text>
                        <Text type="secondary" style={{ fontSize: 12 }}>{adj.created_at}</Text>
                      </Space>
                    }
                    description={
                      <div style={{ fontSize: 12 }}>
                        <div>原因：{adj.reason || '—'}</div>
                        {reschedules.length > 0 && (
                          <div style={{ marginTop: 4 }}>
                            日期变更（{reschedules.length} 项）：
                            {reschedules.slice(0, 4).map((c: any, i: number) => (
                              <div key={i} style={{ color: '#475569' }}>
                                · {c.title}：{c.from_date || '未排期'} → <b>{c.to_date}</b>
                              </div>
                            ))}
                          </div>
                        )}
                        {reduces.length > 0 && (
                          <div style={{ marginTop: 4, color: '#b45309' }}>
                            {reduces[0].note}
                          </div>
                        )}
                        {adj.skipped_changes && adj.skipped_changes.length > 0 && (
                          <div style={{ marginTop: 4, color: '#94a3b8' }}>
                            已跳过 {adj.skipped_changes.length} 项（手动改过或已完成，不会被覆盖）
                          </div>
                        )}
                      </div>
                    }
                  />
                </List.Item>
              );
            }}
          />
        )}
      </Card>
    </Space>
  );
};

export default PlanBudgetPanel;
