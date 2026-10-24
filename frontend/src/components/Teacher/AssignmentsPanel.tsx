import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, Card, DatePicker, Descriptions, Empty, Form, Input, InputNumber,
  List, Modal, Popconfirm, Radio, Select, Space, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  CloseCircleOutlined, EyeOutlined, FileTextOutlined, PlusOutlined, ReloadOutlined,
  RocketOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import dayjs, { Dayjs } from 'dayjs';
import {
  Assignment, AssignmentQuestion, AssignmentType, PublishAssignmentResult, TeacherClass,
  closeAssignment, createAssignment, describeTeacherError, generateAssignmentQuestions,
  isForbiddenError, listAssignments, listClasses, publishAssignment, updateAssignment,
} from '../../api/teacher';
import {
  EmptyBlock, ErrorAlert, LoadingBlock, PermissionAlert, assignmentStatusMeta, formatTime,
  questionStem, questionTypeColor, questionTypeLabel,
} from './common';
import PaperBuilderModal from './PaperBuilderModal';

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

/**
 * 题目**依据来源**标签（2026-10-04）
 *
 * 与 `QuizList` 的来源口径一致。注意与**渠道**区分：
 *   - 依据来源：knowledge_base / note / quiz（课程资料）、web_search（网络资料）、
 *     general_knowledge（AI 通用知识）；
 *   - 渠道：channel === 'teacher_assignment' 表示"教师布置的作业"。
 * 此前教师作业出题把渠道写进了 source（写死 teacher_assignment），界面因此既显示不出
 * 真实依据，也分不清渠道；现在两者分别展示。
 */
const QUESTION_SOURCE_META: Record<string, { color: string; label: string; tip: string }> = {
  knowledge_base: { color: 'green', label: '课程知识库', tip: '题目依据课程资料命制' },
  note: { color: 'green', label: '课程知识库', tip: '题目依据课程笔记命制' },
  quiz: { color: 'green', label: '课程知识库', tip: '题目依据课程资料命制' },
  web_search: { color: 'blue', label: '网络资料', tip: '本地资料不足，题目由网络公开资料整理，答案已由 AI 重新核对' },
  general_knowledge: { color: 'orange', label: 'AI 通用知识', tip: '本地资料不足，题目由 AI 通用知识生成，表述可能与你的教材不同' },
};

export interface AssignmentsPanelProps {
  courseId: string;
  courseTitle?: string;
  subjectId?: string;
  onBack?: () => void;
  /** 定向出题无合格题目时的"去补充资料"入口（切到课程资料） */
  onOpenMaterials?: () => void;
}

interface AssignmentFormValues {
  class_id: string;
  title: string;
  description?: string;
  assign_type: AssignmentType;
  knowledge_points?: string[];
  due_date?: Dayjs | null;
  estimated_minutes?: number;
}

/** 作业 / 练习：创建草稿 → AI 出题 → 预览 → 设截止 → 发布 → 查看完成情况 */
const AssignmentsPanel: React.FC<AssignmentsPanelProps> = ({
  courseId, courseTitle, subjectId, onBack, onOpenMaterials,
}) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState<string | null>(null);
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [classes, setClasses] = useState<TeacherClass[]>([]);
  const [classFilter, setClassFilter] = useState<string | undefined>();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [paperOpen, setPaperOpen] = useState(false);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Assignment | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<AssignmentFormValues>();

  const [questionsFor, setQuestionsFor] = useState<Assignment | null>(null);
  const [genOpen, setGenOpen] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [genResult, setGenResult] = useState<{
    requested: number; generated: number; knowledge_point: string | null; model: string;
    questions: AssignmentQuestion[];
    /** 出题时挂到的草稿作业；为空表示只预览、没写入任何作业 */
    assignmentId?: string;
    /** 渠道与依据来源（2026-10-04 起后端分开下发）：渠道=教师作业；依据=课程知识库等 */
    channel?: string | null;
    channel_label?: string | null;
    source?: string | null;
    source_label?: string | null;
    /** 命制素材（真实引用的课程资料） */
    evidence?: { file_name?: string; page?: number | null; type?: string }[];
  } | null>(null);
  const [genForm] = Form.useForm<{
    assignment_id?: string; knowledge_points?: string[]; count: number;
  }>();

  const [publishResult, setPublishResult] = useState<PublishAssignmentResult | null>(null);

  const load = useCallback(async (cid?: string) => {
    if (!courseId) return;
    setLoading(true);
    setError(null);
    setForbidden(null);
    try {
      const res = await listAssignments(courseId, cid);
      setAssignments(res?.data?.assignments || []);
    } catch (e) {
      const f = describeTeacherError(e, '加载作业失败');
      if (isForbiddenError(e)) setForbidden(f.message);
      else setError(f.message);
    } finally {
      setLoading(false);
    }
  }, [courseId]);

  const loadClasses = useCallback(async () => {
    try {
      const res = await listClasses(courseId);
      setClasses(res?.data?.classes || []);
    } catch {
      /* 参考数据失败不阻断 */
    }
  }, [courseId]);

  useEffect(() => { load(classFilter); }, [load, classFilter]);
  useEffect(() => { loadClasses(); }, [loadClasses]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ assign_type: 'quiz', estimated_minutes: 30 });
    setFormOpen(true);
  };

  const openEdit = (a: Assignment) => {
    if (a.status !== 'draft') {
      message.warning('已发布作业不能修改，如需更改请新建一次作业');
      return;
    }
    setEditing(a);
    form.setFieldsValue({
      class_id: a.class_id,
      title: a.title,
      description: a.description || undefined,
      assign_type: (a.assign_type as AssignmentType) || 'quiz',
      knowledge_points: a.knowledge_points || [],
      due_date: a.due_date ? dayjs(a.due_date) : null,
      estimated_minutes: a.estimated_minutes ?? 30,
    });
    setFormOpen(true);
  };

  const handleSubmit = async (values: AssignmentFormValues) => {
    setSubmitting(true);
    try {
      const payload = {
        title: values.title.trim(),
        description: values.description,
        knowledge_points: values.knowledge_points?.length ? values.knowledge_points : undefined,
        due_date: values.due_date ? values.due_date.format('YYYY-MM-DD') : undefined,
        estimated_minutes: values.estimated_minutes,
      };
      if (editing) {
        await updateAssignment(editing.id, payload);
        message.success('作业已更新（仍是草稿，发布后才会下发给学生）');
      } else {
        await createAssignment(courseId, {
          ...payload,
          class_id: values.class_id,
          assign_type: values.assign_type || 'quiz',
        });
        message.success('作业草稿已创建，可继续 AI 出题后再发布');
      }
      setFormOpen(false);
      setEditing(null);
      form.resetFields();
      await load(classFilter);
    } catch (e) {
      // 已发布作业被后端拒绝时，如实显示后端原因
      message.error(describeTeacherError(e, '保存作业失败').message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleGenerate = async (values: {
    assignment_id?: string; knowledge_points?: string[]; count: number;
  }) => {
    setGenerating(true);
    try {
      const res = await generateAssignmentQuestions(courseId, {
        assignment_id: values.assignment_id,
        knowledge_points: values.knowledge_points?.length ? values.knowledge_points : undefined,
        count: values.count ?? 3,
      });
      const d = res?.data;
      setGenResult(d ? {
        requested: d.requested, generated: d.generated,
        knowledge_point: d.knowledge_point, model: d.model,
        questions: d.questions || [],
                // 渠道与依据来源必须一起映射：否则渠道有 '教师作业' 兜底仍显示，
        // 而「依据」永远不出现（实测 2026-10-04：界面只见渠道、不见依据）。
        channel: d.channel, channel_label: d.channel_label,
        source: d.source, source_label: d.source_label,
        evidence: (d.evidence || []) as unknown as { file_name?: string }[],
assignmentId: values.assignment_id,
      } : null);
      // 定向出题的有效性必须如实反馈：有效题数、待确认题数、不足原因与下一步入口
      const valid = d?.valid_count ?? d?.generated ?? 0;
      const pending = d?.pending_count ?? 0;
      if (d?.warning && valid === 0 && onOpenMaterials) {
        Modal.warning({
          title: '没有生成符合目标知识点的题目',
          content: d.warning,
          okText: '去补充课程资料',
          cancelText: '关闭',
          onOk: onOpenMaterials,
        });
      } else if (d?.warning) {
        message.warning(d.warning, 8);
      } else if (d && d.generated < d.requested) {
        message.warning(`请求 ${d.requested} 道，实际生成 ${d.generated} 道（资料可能不足）`);
      } else if (pending > 0) {
        message.warning(`已生成 ${valid} 道题，另有 ${pending} 道归属待确认（不计入掌握度）`);
      } else {
        message.success(`已生成 ${d?.generated ?? 0} 道题`);
      }
      await load(classFilter);
    } catch (e) {
      message.error(describeTeacherError(e, 'AI 出题失败').message);
    } finally {
      setGenerating(false);
    }
  };

  const handlePublish = async (a: Assignment) => {
    setBusyId(a.id);
    setPublishResult(null);
    try {
      const res = await publishAssignment(a.id);
      const d = res?.data;
      setPublishResult(d || null);
      if (d?.already_published) {
        message.info('该作业此前已发布过');
      } else if (d?.failed?.length) {
        message.warning(`发布完成，但有 ${d.failed.length} 名学生写入失败，请查看原因`);
      } else {
        message.success(`已发布给 ${d?.created?.length ?? 0} 名学生`);
      }
      await load(classFilter);
    } catch (e) {
      message.error(describeTeacherError(e, '发布作业失败').message);
    } finally {
      setBusyId(null);
    }
  };

  const handleClose = async (a: Assignment) => {
    setBusyId(a.id);
    try {
      await closeAssignment(a.id);
      message.success('作业已关闭');
      await load(classFilter);
    } catch (e) {
      message.error(describeTeacherError(e, '关闭作业失败').message);
    } finally {
      setBusyId(null);
    }
  };

  const classOptions = classes.map((c) => ({ value: c.id, label: c.name }));
  const className = (id: string) => classes.find((c) => c.id === id)?.name || id;
  const draftOptions = assignments
    .filter((a) => a.status === 'draft')
    .map((a) => ({ value: a.id, label: a.title }));

  if (forbidden) return <PermissionAlert message={forbidden} onBack={onBack} />;

  return (
    <div>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        <Title level={5} style={{ margin: 0 }}>
          作业与练习{courseTitle ? ` · ${courseTitle}` : ''}
        </Title>
        <Space wrap>
          <Select
            size="small"
            allowClear
            style={{ width: 180 }}
            placeholder="按班级筛选"
            value={classFilter}
            onChange={(v) => setClassFilter(v)}
            options={classOptions}
          />
          <Button size="small" icon={<ReloadOutlined />} onClick={() => load(classFilter)} loading={loading}>
            刷新
          </Button>
          <Button size="small" icon={<ThunderboltOutlined />} onClick={() => { setGenResult(null); setGenOpen(true); }}>
            AI 出题
          </Button>
          <Button size="small" icon={<FileTextOutlined />} disabled={!subjectId}
                  onClick={() => setPaperOpen(true)}>
            智能组卷
          </Button>
          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建作业
          </Button>
        </Space>
      </Space>

      {error && <ErrorAlert message={error} onRetry={() => load(classFilter)} />}
      {classes.length === 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="该课程还没有班级"
          description="作业必须发布到某个班级。请先在「班级管理」中新建班级并把班级邀请码发给学生。"
        />
      )}

      {/* 发布结果（成功 / 跳过 / 失败人数必须如实展示） */}
      {publishResult && (
        <Alert
          type={publishResult.failed?.length ? 'warning' : publishResult.already_published ? 'info' : 'success'}
          showIcon
          style={{ marginBottom: 12 }}
          message={
            publishResult.already_published
              ? '该作业此前已发布，未重复创建任务'
              : `发布完成：成功 ${publishResult.created?.length ?? 0} 人 / 跳过 ${publishResult.skipped?.length ?? 0} 人 / 失败 ${publishResult.failed?.length ?? 0} 人`
          }
          description={
            <Space direction="vertical" size={2} style={{ width: '100%' }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                班级在班学生共 {publishResult.student_count ?? (publishResult.created.length + publishResult.skipped.length + publishResult.failed.length)} 人；
                「跳过」表示该学生已有此作业任务，不会重复创建。
              </Text>
              {(publishResult.failed || []).map((f) => (
                <Text key={f.user_id} type="danger" style={{ fontSize: 12 }}>
                  失败：{f.user_id} —— {f.reason}
                </Text>
              ))}
            </Space>
          }
          closable
          onClose={() => setPublishResult(null)}
        />
      )}

      <Card size="small" title={`作业列表（${assignments.length}）`}>
        {loading ? <LoadingBlock tip="加载作业…" /> : assignments.length === 0 ? (
          <EmptyBlock description="该课程还没有作业。点击「新建作业」创建草稿后发布。" />
        ) : (
          <Table<Assignment>
            rowKey="id"
            size="small"
            dataSource={assignments}
            pagination={false}
            scroll={{ x: 1000 }}
            columns={[
              {
                title: '作业', dataIndex: 'title',
                render: (v: string, a: Assignment) => (
                  <Space direction="vertical" size={0}>
                    <Text strong>{v}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {a.assign_type === 'quiz' ? '练习（题目作答）' : '任务（实践作业）'}
                      {a.description ? ` · ${a.description}` : ''}
                    </Text>
                  </Space>
                ),
              },
              {
                title: '班级', dataIndex: 'class_id', width: 140,
                render: (v: string) => className(v),
              },
              {
                title: '状态', dataIndex: 'status', width: 90,
                render: (s: string) => {
                  const m = assignmentStatusMeta(s);
                  return <Tag color={m.color}>{m.label}</Tag>;
                },
              },
              {
                title: '截止日期', dataIndex: 'due_date', width: 120,
                render: (v: string | null) => v || '未设置',
              },
              {
                title: '题目数', dataIndex: 'question_count', width: 80,
                render: (v: number) => v ?? 0,
              },
              {
                title: '完成情况', width: 220,
                render: (_: unknown, a: Assignment) => {
                  const st = a.publish_stats;
                  if (!st?.published) {
                    return <Text type="secondary" style={{ fontSize: 12 }}>未发布，暂无完成数据</Text>;
                  }
                  return (
                    <Space direction="vertical" size={0}>
                      <Text style={{ fontSize: 12 }}>
                        在班 {st.student_count ?? 0} 人 · 完成任务 {st.task_completed_count ?? 0} 人
                      </Text>
                      <Text style={{ fontSize: 12 }}>
                        有作答记录 {st.answered_student_count ?? 0} 人
                      </Text>
                    </Space>
                  );
                },
              },
              {
                title: '操作', width: 260, fixed: 'right',
                render: (_: unknown, a: Assignment) => (
                  <Space size={2} wrap>
                    <Button
                      size="small" type="link" icon={<EyeOutlined />}
                      onClick={() => setQuestionsFor(a)}
                    >
                      预览题目
                    </Button>
                    {a.status === 'draft' ? (
                      <Button size="small" type="link" onClick={() => openEdit(a)}>编辑</Button>
                    ) : (
                      <Tooltip title="已发布作业不能修改，如需更改请新建一次作业">
                        <Button size="small" type="link" disabled>编辑</Button>
                      </Tooltip>
                    )}
                    {a.status === 'draft' && (
                      <Popconfirm
                        title="发布这份作业？"
                        description="会为当前在班学生生成任务；发布后不可再修改题目。"
                        okText="发布"
                        cancelText="取消"
                        onConfirm={() => handlePublish(a)}
                      >
                        <Button size="small" type="link" icon={<RocketOutlined />} loading={busyId === a.id}>
                          发布
                        </Button>
                      </Popconfirm>
                    )}
                    {a.status === 'published' && (
                      <Popconfirm
                        title="关闭这份作业？"
                        description="关闭后学生不能再提交。"
                        okText="关闭"
                        cancelText="取消"
                        onConfirm={() => handleClose(a)}
                      >
                        <Button size="small" type="link" danger icon={<CloseCircleOutlined />}
                                loading={busyId === a.id}>
                          关闭
                        </Button>
                      </Popconfirm>
                    )}
                  </Space>
                ),
              },
            ]}
          />
        )}
        <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
          已发布的作业不能再修改（后端会拒绝），如需调整题目或截止日期，请新建一次作业。
          完成情况按「教师布置任务的学习状态」与「作答记录」统计，均为真实数据。
        </Paragraph>
      </Card>

      {/* 新建 / 编辑作业 */}
      <Modal
        title={editing ? `编辑作业：${editing.title}` : '新建作业草稿'}
        open={formOpen}
        onCancel={() => { setFormOpen(false); setEditing(null); form.resetFields(); }}
        onOk={() => form.submit()}
        okText={editing ? '保存' : '创建草稿'}
        cancelText="取消"
        confirmLoading={submitting}
        width={640}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          initialValues={{ assign_type: 'quiz', estimated_minutes: 30 }}
        >
          <Form.Item label="所属班级" name="class_id" rules={[{ required: true, message: '请选择班级' }]}>
            <Select
              placeholder={classes.length ? '选择班级' : '该课程还没有班级'}
              options={classOptions}
              disabled={!!editing}
            />
          </Form.Item>
          <Form.Item label="作业标题" name="title" rules={[{ required: true, message: '请填写作业标题' }]}>
            <Input placeholder="例如：第 3 章 树与二叉树 练习" maxLength={120} />
          </Form.Item>
          <Form.Item label="说明（可选）" name="description">
            <TextArea rows={2} placeholder="作业说明，会展示给学生" maxLength={500} />
          </Form.Item>
          <Space size={12} wrap>
            <Form.Item label="类型" name="assign_type">
              <Radio.Group disabled={!!editing}>
                <Radio.Button value="quiz">练习（题目作答）</Radio.Button>
                <Radio.Button value="task">任务（实践作业）</Radio.Button>
              </Radio.Group>
            </Form.Item>
            <Form.Item label="预计用时（分钟）" name="estimated_minutes">
              <InputNumber min={1} max={600} style={{ width: 160 }} />
            </Form.Item>
            <Form.Item label="截止日期（可选）" name="due_date">
              <DatePicker style={{ width: 180 }} />
            </Form.Item>
          </Space>
          <Form.Item
            label="知识点（可选，回车添加）"
            name="knowledge_points"
            extra="用于学生错题归因与学情统计；练习类作业建议填写。"
          >
            <Select mode="tags" placeholder="例如：二叉树的遍历" open={false} />
          </Form.Item>
          <Alert
            type="info"
            showIcon
            message="先创建草稿，再用「AI 出题」补题目；练习类作业没有题目时后端会拒绝发布。"
          />
        </Form>
      </Modal>

      {/* AI 出题 */}
      <Modal
        title="AI 依据课程资料出题"
        open={genOpen}
        onCancel={() => { setGenOpen(false); setGenResult(null); genForm.resetFields(); }}
        footer={null}
        width={760}
        destroyOnClose
      >
        <Form
          form={genForm}
          layout="vertical"
          onFinish={handleGenerate}
          initialValues={{ count: 3 }}
        >
          <Space size={12} wrap style={{ width: '100%' }}>
            <Form.Item
              label="挂到草稿作业"
              name="assignment_id"
              style={{ width: 260 }}
              extra="不选择时只预览题目，不会写入作业——这样发布的作业会是空的"
            >
              <Select
                allowClear
                placeholder={draftOptions.length ? '选择草稿作业' : '没有草稿作业'}
                options={draftOptions}
                disabled={draftOptions.length === 0}
              />
            </Form.Item>
            <Form.Item label="知识点（可选，回车添加）" name="knowledge_points" style={{ width: 280 }}>
              <Select mode="tags" placeholder="不填则按课程资料重点出题" open={false} />
            </Form.Item>
            <Form.Item label="题目数量" name="count" rules={[{ required: true, message: '请填写题目数量' }]}>
              <InputNumber min={1} max={10} />
            </Form.Item>
          </Space>
          <Form.Item style={{ marginBottom: 8 }}>
            <Button type="primary" htmlType="submit" loading={generating} icon={<ThunderboltOutlined />}>
              开始出题
            </Button>
          </Form.Item>
        </Form>

        {genResult && (
          <Card size="small" title={`生成结果：请求 ${genResult.requested} 道 / 实际 ${genResult.generated} 道`}>
            {!genResult.assignmentId && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 8 }}
                message="本次只是预览，题目没有写入任何作业"
                description="没有选择「挂到草稿作业」，所以这些题目不会被保存，发布的作业会是空的（发布时会被拒绝：作业还没有题目）。要让学生作答，请在下方选择一份草稿作业后重新出题。"
              />
            )}
            {genResult.generated < genResult.requested && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 8 }}
                message="实际生成数量少于请求数量"
                description="常见原因是课程资料不足或资料未覆盖该知识点。建议补充资料并重建知识库后重试。"
              />
            )}
            <Text type="secondary" style={{ fontSize: 12 }}>
              知识点：{genResult.knowledge_point} · 模型：{genResult.model}
            </Text>
            {/* 渠道与依据分开显示（2026-10-04）：新生成题目的 per-question source 可能尚未回填，
                这里直接展示本次生成的渠道/依据与命制素材 */}
            <div style={{ marginTop: 6 }}>
              <Space size={6} wrap>
                <Tag color="geekblue">渠道：{genResult.channel_label || '教师作业'}</Tag>
                {genResult.source && (
                  <Tag color={(QUESTION_SOURCE_META[genResult.source] || {}).color || 'green'}>
                    依据：{(QUESTION_SOURCE_META[genResult.source] || {}).label || genResult.source}
                  </Tag>
                )}
                {(genResult.evidence || []).length > 0 && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    命制素材：
                    {(genResult.evidence || [])
                      .map((e: { file_name?: string }) => e.file_name)
                      .filter(Boolean)
                      .join('、')}
                  </Text>
                )}
              </Space>
            </div>
            <List
              size="small"
              style={{ marginTop: 8 }}
              dataSource={genResult.questions}
              renderItem={(q, i) => (
                <List.Item>
                  <div style={{ width: '100%' }}>
                    <Space size={6} wrap>
                      <Tag color={questionTypeColor(q.type || q.question_type)}>
                        {questionTypeLabel(q.type || q.question_type)}
                      </Tag>
                      <Text strong>{i + 1}. {questionStem(q)}</Text>
                      {/* 依据来源：题目是按什么命制的（课程知识库 / 网络资料 / AI 通用知识） */}
                      {q.source && QUESTION_SOURCE_META[q.source] && (
                        <Tooltip title={QUESTION_SOURCE_META[q.source].tip}>
                          <Tag color={QUESTION_SOURCE_META[q.source].color}>
                            依据：{QUESTION_SOURCE_META[q.source].label}
                          </Tag>
                        </Tooltip>
                      )}
                      {/* 渠道：题目从哪里来的（教师作业 / 学生自测），与"依据"分开表达 */}
                      {q.channel === 'teacher_assignment' && (
                        <Tag color="geekblue">渠道：教师作业</Tag>
                      )}
                    </Space>
                    {Array.isArray(q.options) && q.options.length > 0 && (
                      <div style={{ marginTop: 4, paddingLeft: 8 }}>
                        {q.options.map((o) => (
                          <div key={o.id}>
                            <Text type="secondary" style={{ fontSize: 12 }}>{o.id}. {o.text}</Text>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </List.Item>
              )}
            />
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 8 }}
              message="出于防泄题考虑，题目结构里不包含标准答案；学生作答后由后端权威判分。"
            />
          </Card>
        )}
      </Modal>

      {/* 题目预览 */}
      <Modal
        title={`题目预览：${questionsFor?.title || ''}`}
        open={!!questionsFor}
        onCancel={() => setQuestionsFor(null)}
        footer={null}
        width={720}
      >
        {!questionsFor?.questions?.length ? (
          <Empty description="该作业还没有题目（可先用 AI 出题）" />
        ) : (
          <>
            <Descriptions size="small" column={2} style={{ marginBottom: 8 }}>
              <Descriptions.Item label="班级">{className(questionsFor.class_id)}</Descriptions.Item>
              <Descriptions.Item label="题目数">{questionsFor.questions.length}</Descriptions.Item>
              <Descriptions.Item label="截止日期">{questionsFor.due_date || '未设置'}</Descriptions.Item>
              <Descriptions.Item label="创建时间">{formatTime(questionsFor.created_at)}</Descriptions.Item>
            </Descriptions>
            <List
              size="small"
              dataSource={questionsFor.questions}
              renderItem={(q, i) => (
                <List.Item>
                  <div style={{ width: '100%' }}>
                    <Space size={6} wrap>
                      <Tag color={questionTypeColor(q.type || q.question_type)}>
                        {questionTypeLabel(q.type || q.question_type)}
                      </Tag>
                      <Text strong>{i + 1}. {questionStem(q)}</Text>
                      {/* 依据来源：题目是按什么命制的（课程知识库 / 网络资料 / AI 通用知识） */}
                      {q.source && QUESTION_SOURCE_META[q.source] && (
                        <Tooltip title={QUESTION_SOURCE_META[q.source].tip}>
                          <Tag color={QUESTION_SOURCE_META[q.source].color}>
                            依据：{QUESTION_SOURCE_META[q.source].label}
                          </Tag>
                        </Tooltip>
                      )}
                      {/* 渠道：题目从哪里来的（教师作业 / 学生自测），与"依据"分开表达 */}
                      {q.channel === 'teacher_assignment' && (
                        <Tag color="geekblue">渠道：教师作业</Tag>
                      )}
                    </Space>
                    {Array.isArray(q.options) && q.options.length > 0 && (
                      <div style={{ marginTop: 4, paddingLeft: 8 }}>
                        {q.options.map((o) => (
                          <div key={o.id}>
                            <Text type="secondary" style={{ fontSize: 12 }}>{o.id}. {o.text}</Text>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </List.Item>
              )}
            />
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 8 }}
              message="题目不含标准答案（后端刻意不下发），学生提交后由后端判分。"
            />
          </>
        )}
      </Modal>

      {subjectId && (
        <PaperBuilderModal open={paperOpen} onClose={() => setPaperOpen(false)} subjectId={subjectId} />
      )}
    </div>
  );
};

export default AssignmentsPanel;
