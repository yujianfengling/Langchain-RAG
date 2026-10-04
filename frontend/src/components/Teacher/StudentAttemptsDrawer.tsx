import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, Card, Col, Empty, Form, Input, message, Modal, Radio, Row, Space,
  Spin, Statistic, Table, Tag, Tooltip, Typography,
} from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  StudentAttempt, StudentAttemptsResult, describeTeacherError, listStudentAttempts,
  regradeAttempt,
} from '../../api/teacher';
import { ErrorAlert, formatTime } from './common';

const { Text } = Typography;

export interface StudentAttemptsDrawerProps {
  open: boolean;
  studentId: string;
  studentName?: string;
  onClose: () => void;
  /** 改判成功后通知外部刷新（学情/画像会随之变化） */
  onRegraded?: () => void;
}

/** 判分状态 → 中文说明（让教师看懂"这条结论是谁给的"） */
const GRADE_LABELS: Record<string, string> = {
  graded: '客观题自动判分',
  ai_graded: 'AI 判分（简答题）',
  self_evaluated: '学生自评',
  teacher_regraded: '教师改判',
  pending_self_eval: '待自评（无结论）',
};

const verdictTag = (v: boolean | null, label?: string) => {
  if (v === null || v === undefined) return <Tag>{label || '无结论'}</Tag>;
  return v
    ? <Tag color="success" icon={<CheckCircleOutlined />}>{label || '正确'}</Tag>
    : <Tag color="error" icon={<CloseCircleOutlined />}>{label || '错误'}</Tag>;
};

/**
 * 单学生作答明细 + 教师改判（2026-10-04）
 *
 * 负责人要求：教师能看某个学生的逐题明细，并能改判；**原判定、教师判定、理由和时间都要保留**。
 * 界面因此把三者分列展示，改判必须填理由（后端同样强制校验）。
 * 改判后生效判定变化，学情/错题本/掌握度/复习随之更新（后端联动）。
 */
const StudentAttemptsDrawer: React.FC<StudentAttemptsDrawerProps> = ({
  open, studentId, studentName, onClose, onRegraded,
}) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<StudentAttemptsResult | null>(null);
  const [days, setDays] = useState(30);
  const [target, setTarget] = useState<StudentAttempt | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    if (!studentId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await listStudentAttempts(studentId, days);
      setData(res?.data || null);
    } catch (e) {
      setData(null);
      setError(describeTeacherError(e, '加载作答明细失败').message);
    } finally {
      setLoading(false);
    }
  }, [studentId, days]);

  useEffect(() => { if (open) load(); }, [open, load]);

  const openRegrade = (row: StudentAttempt) => {
    setTarget(row);
    form.setFieldsValue({
      is_correct: row.effective_is_correct === null ? true : !row.effective_is_correct,
      reason: '',
    });
  };

  const submitRegrade = async () => {
    if (!target) return;
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      const res = await regradeAttempt(target.attempt_id, values.is_correct, values.reason);
      const linkage = (res?.data as any)?.linkage || {};
      message.success(
        `改判成功（${values.is_correct ? '正确' : '错误'}）`
        + (linkage?.mastery?.rebuilt ? '；掌握度已重算' : '')
        + (linkage?.review?.action === 'completed' ? '；已关闭待复习'
          : linkage?.review?.action === 'ensured' ? '；已安排复习' : ''),
      );
      setTarget(null);
      await load();
      onRegraded?.();
    } catch (e) {
      if ((e as any)?.errorFields) return;      // 表单校验失败
      message.error(describeTeacherError(e, '改判失败').message);
    } finally {
      setSubmitting(false);
    }
  };

  const columns = [
    { title: '时间', dataIndex: 'created_at', width: 150, render: (t: string) => formatTime(t) },
    {
      title: '题目 / 知识点',
      render: (_: unknown, r: StudentAttempt) => (
        <div>
          <div style={{ maxWidth: 320 }}>{r.stem || r.question_id}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {r.knowledge_point_name || '（无知识点）'}
            {r.attempt_kind === 'review' ? ' · 复习重做' : ''}
            {r.used_hint ? ' · 用过提示' : ''}
            {r.revealed_answer ? ' · 看过答案' : ''}
          </Text>
        </div>
      ),
    },
    {
      title: '学生答案', dataIndex: 'user_answer', width: 110,
      render: (v: string, r: StudentAttempt) => (
        <Tooltip title={r.user_answer_text || undefined}>{v || '—'}</Tooltip>
      ),
    },
    {
      title: '原判定', width: 130,
      render: (_: unknown, r: StudentAttempt) => (
        r.original
          ? <Space direction="vertical" size={0}>
              {verdictTag(r.original.is_correct)}
              <Text type="secondary" style={{ fontSize: 11 }}>
                {GRADE_LABELS[r.original.grade_status || ''] || r.original.grade_status}
              </Text>
            </Space>
          : <Text type="secondary">未改判</Text>
      ),
    },
    {
      title: '教师判定', width: 180,
      render: (_: unknown, r: StudentAttempt) => (
        r.teacher
          ? <Space direction="vertical" size={0}>
              {verdictTag(r.teacher.is_correct, r.teacher.is_correct ? '教师判正确' : '教师判错误')}
              <Text type="secondary" style={{ fontSize: 11 }} ellipsis={{ tooltip: r.teacher.reason || '' }}>
                理由：{r.teacher.reason || '—'}
              </Text>
              <Text type="secondary" style={{ fontSize: 11 }}>{formatTime(r.teacher.graded_at)}</Text>
            </Space>
          : <Text type="secondary">—</Text>
      ),
    },
    {
      title: '生效判定（用于学情）', width: 140,
      render: (_: unknown, r: StudentAttempt) => (
        <Space direction="vertical" size={0}>
          {verdictTag(r.effective_is_correct)}
          <Text type="secondary" style={{ fontSize: 11 }}>
            {GRADE_LABELS[r.effective_grade_status] || r.effective_grade_status}
          </Text>
        </Space>
      ),
    },
    {
      title: '操作', width: 92,
      render: (_: unknown, r: StudentAttempt) => (
        <Button size="small" type="link" onClick={() => openRegrade(r)}>改判</Button>
      ),
    },
  ];

  return (
    <>
      <Modal
        open={open}
        onCancel={onClose}
        footer={null}
        width={1180}
        title={`作答明细：${studentName || studentId}`}
        destroyOnClose
      >
        <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 8 }} wrap>
          <Space>
            <Radio.Group
              size="small"
              value={days}
              onChange={(e) => setDays(e.target.value)}
              options={[{ label: '近 7 天', value: 7 }, { label: '近 30 天', value: 30 },
                        { label: '近 90 天', value: 90 }]}
              optionType="button"
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              改判后：学情、错题本、掌握度、复习任务会同步更新；原判定仍保留
            </Text>
          </Space>
          <Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
        </Space>

        {error && <ErrorAlert message={error} onRetry={load} />}

        {data && (
          <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
            <Col xs={12} md={4}><Card size="small"><Statistic title="作答" value={data.summary.attempt_count} /></Card></Col>
            <Col xs={12} md={4}><Card size="small"><Statistic title="正确" value={data.summary.correct_count} valueStyle={{ color: '#059669' }} /></Card></Col>
            <Col xs={12} md={4}><Card size="small"><Statistic title="错误" value={data.summary.wrong_count} valueStyle={{ color: '#dc2626' }} /></Card></Col>
            <Col xs={12} md={4}><Card size="small"><Statistic title="未判分" value={data.summary.ungraded_count} /></Card></Col>
            <Col xs={12} md={4}><Card size="small"><Statistic title="已改判" value={data.summary.regraded_count} /></Card></Col>
            <Col xs={12} md={4}>
              <Card size="small">
                <Statistic title="数据范围" value={data.summary.range_days} suffix="天" />
              </Card>
            </Col>
          </Row>
        )}

        {loading && !data && <Spin />}
        {data && data.attempts.length === 0 && (
          <Empty description="该学生在所选时间范围内没有作答记录" />
        )}
        {data && data.attempts.length > 0 && (
          <Table
            rowKey="attempt_id"
            dataSource={data.attempts}
            columns={columns as any}
            size="small"
            pagination={{ pageSize: 10, showSizeChanger: false }}
            scroll={{ x: 1080 }}
            className="app-table-scroll"
          />
        )}
        {data?.sources?.length ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            数据来源：{data.sources.join('；')}
          </Text>
        ) : null}
      </Modal>

      <Modal
        open={!!target}
        title="教师改判"
        onCancel={() => setTarget(null)}
        onOk={submitRegrade}
        okText="确认改判"
        cancelText="取消"
        confirmLoading={submitting}
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="改判会立即影响学情统计、错题本与掌握度；原判定与理由会一并保留，便于追溯。"
        />
        <div style={{ marginBottom: 12 }}>
          <Text strong>题目：</Text>
          <Text>{target?.stem || target?.question_id}</Text>
        </div>
        <div style={{ marginBottom: 12 }}>
          <Space size={16}>
            <span><Text strong>学生答案：</Text>{target?.user_answer || '—'}</span>
            <span><Text strong>当前判定：</Text>{verdictTag(target?.effective_is_correct ?? null)}</span>
          </Space>
        </div>
        {target?.ai_feedback ? (
          <div style={{ marginBottom: 12 }}>
            <Text strong>AI 说明：</Text><Text type="secondary">{target.ai_feedback}</Text>
          </div>
        ) : null}
        <Form form={form} layout="vertical">
          <Form.Item name="is_correct" label="教师判定" rules={[{ required: true, message: '请选择判定' }]}>
            <Radio.Group>
              <Radio.Button value={true}>判为正确</Radio.Button>
              <Radio.Button value={false}>判为错误</Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Form.Item
            name="reason"
            label="改判理由（必填，会随记录保留）"
            rules={[{ required: true, message: '请填写改判理由' }, { max: 500, message: '最多 500 字' }]}
          >
            <Input.TextArea rows={3} placeholder="例如：学生口头复述完全正确，AI 误判；或答案缺少关键条件，应为错误" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
};

export default StudentAttemptsDrawer;
