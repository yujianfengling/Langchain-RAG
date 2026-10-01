import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, Card, Descriptions, Empty, Form, Input, InputNumber, List, Modal,
  Popconfirm, Radio, Select, Space, Tag, Typography, message,
} from 'antd';
import {
  DownloadOutlined, EyeOutlined, FileAddOutlined, FileTextOutlined,
  HistoryOutlined, PlusOutlined, ReloadOutlined, RocketOutlined,
} from '@ant-design/icons';
import {
  CourseMaterial, LessonPlan, PlanVersion, TeacherClass,
  createLessonPlan, describeBlobError, describeTeacherError, exportLessonPlan, generateLessonPlan,
  getLessonPlan, isForbiddenError, listCourseMaterials, listClasses, listLessonPlanVersions,
  listLessonPlans, previewLessonPlan, publishLessonPlan,
} from '../../api/teacher';
import {
  EmptyBlock, ErrorAlert, LoadingBlock, MarkdownBlock, PermissionAlert, formatTime,
} from './common';
import LessonPlanEditor from './LessonPlanEditor';

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

export interface LessonPlansPanelProps {
  courseId: string;
  courseTitle?: string;
  /** 从工作台「继续编辑」跳转过来的教案 ID */
  focusPlanId?: string;
  onBack?: () => void;
}

type StatusFilter = 'draft' | 'published' | 'all';

/** 教案：生成 / 手写 / 编辑（新版本）/ 版本回看 / 预览 / 发布 / 导出 */
const LessonPlansPanel: React.FC<LessonPlansPanelProps> = ({
  courseId, courseTitle, focusPlanId, onBack,
}) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState<string | null>(null);
  const [plans, setPlans] = useState<LessonPlan[]>([]);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  const [classes, setClasses] = useState<TeacherClass[]>([]);
  const [materials, setMaterials] = useState<CourseMaterial[]>([]);

  const [current, setCurrent] = useState<LessonPlan | null>(null);
  const [editingOverride, setEditingOverride] = useState<LessonPlan | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [acting, setActing] = useState(false);

  const [genOpen, setGenOpen] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [genForm] = Form.useForm<{
    topic: string; hours: number; objectives?: string; class_id?: string;
    material_ids?: string[]; title?: string;
  }>();

  const [manualOpen, setManualOpen] = useState(false);
  const [manualForm] = Form.useForm<{ title: string; topic?: string; hours?: number; class_id?: string }>();

  const [versionsOpen, setVersionsOpen] = useState(false);
  const [versions, setVersions] = useState<PlanVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);

  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewMd, setPreviewMd] = useState('');
  const [previewLabel, setPreviewLabel] = useState('');

  const loadPlans = useCallback(async (filter: StatusFilter = statusFilter) => {
    if (!courseId) return;
    setLoading(true);
    setError(null);
    setForbidden(null);
    try {
      const res = await listLessonPlans(courseId, filter === 'all' ? undefined : filter);
      setPlans(res?.data?.lesson_plans || []);
    } catch (e) {
      const f = describeTeacherError(e, '加载教案失败');
      if (isForbiddenError(e)) setForbidden(f.message);
      else setError(f.message);
    } finally {
      setLoading(false);
    }
  }, [courseId, statusFilter]);

  const loadRefData = useCallback(async () => {
    try {
      const [clsRes, matRes] = await Promise.allSettled([
        listClasses(courseId),
        listCourseMaterials(courseId),
      ]);
      if (clsRes.status === 'fulfilled') setClasses(clsRes.value?.data?.classes || []);
      if (matRes.status === 'fulfilled') {
        setMaterials((matRes.value?.data?.materials || []).filter((m) => m.status === 'ready'));
      }
    } catch {
      /* 参考数据失败不阻断主流程 */
    }
  }, [courseId]);

  const openPlan = useCallback(async (planId: string, version?: number) => {
    setDetailLoading(true);
    try {
      const res = await getLessonPlan(planId, version);
      setCurrent(res?.data || null);
      setEditingOverride(null);
    } catch (e) {
      message.error(describeTeacherError(e, '加载教案详情失败').message);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => { loadPlans(statusFilter); }, [loadPlans, statusFilter]);
  useEffect(() => { loadRefData(); }, [loadRefData]);
  useEffect(() => {
    if (focusPlanId) openPlan(focusPlanId);
  }, [focusPlanId, openPlan]);

  const handleGenerate = async (values: {
    topic: string; hours: number; objectives?: string; class_id?: string;
    material_ids?: string[]; title?: string;
  }) => {
    setGenerating(true);
    try {
      const res = await generateLessonPlan(courseId, {
        topic: values.topic.trim(),
        hours: values.hours ?? 1,
        objectives: values.objectives || '',
        class_id: values.class_id,
        material_ids: values.material_ids?.length ? values.material_ids : undefined,
        title: values.title?.trim() || undefined,
      });
      const plan = res?.data;
      setGenOpen(false);
      genForm.resetFields();
      await loadPlans(statusFilter);
      if (plan?.id) {
        setCurrent(plan);
        setEditingOverride(null);
      }
      if (plan?.insufficient_evidence) {
        message.warning('教案已生成，但课程资料不足：内容缺少资料依据，请核对后再使用');
      } else {
        message.success('教案草稿已生成');
      }
    } catch (e) {
      message.error(describeTeacherError(e, '生成教案失败').message);
    } finally {
      setGenerating(false);
    }
  };

  const handleManualCreate = async (values: {
    title: string; topic?: string; hours?: number; class_id?: string;
  }) => {
    setActing(true);
    try {
      const res = await createLessonPlan(courseId, {
        title: values.title.trim(),
        topic: values.topic,
        hours: values.hours,
        class_id: values.class_id,
        content: { title: values.title.trim(), objectives: [], key_points: [], difficulties: [], steps: [], examples: [], homework: [], evidence_note: '' },
      });
      message.success('已创建手写教案草稿');
      setManualOpen(false);
      manualForm.resetFields();
      await loadPlans(statusFilter);
      if (res?.data?.id) {
        setCurrent(res.data);
        setEditingOverride(null);
      }
    } catch (e) {
      message.error(describeTeacherError(e, '创建教案失败').message);
    } finally {
      setActing(false);
    }
  };

  const handlePublish = async () => {
    if (!current) return;
    setActing(true);
    try {
      const res = await publishLessonPlan(current.id);
      message.success('教案已发布，学生可在「我的学习」中查看已发布版本');
      setCurrent(res?.data || current);
      await loadPlans(statusFilter);
    } catch (e) {
      // 内容不完整时后端返回 400 与中文原因，如实展示
      message.error(describeTeacherError(e, '发布教案失败').message);
    } finally {
      setActing(false);
    }
  };

  const handlePreview = async (planId: string, version?: number, label?: string) => {
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreviewMd('');
    setPreviewLabel(label || '');
    try {
      const res = await previewLessonPlan(planId, version);
      setPreviewMd(res?.data?.markdown || '');
    } catch (e) {
      setPreviewMd('');
      message.error(describeTeacherError(e, '预览失败').message);
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleExport = async (plan: LessonPlan, version?: number) => {
    setActing(true);
    try {
      const blob = await exportLessonPlan(plan.id, version);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `教案-${plan.title || plan.id}-v${version ?? plan.version ?? 1}.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
      message.success('已开始下载 Markdown 文件');
    } catch (e) {
      message.error(await describeBlobError(e, '导出失败'));
    } finally {
      setActing(false);
    }
  };

  const handleOpenVersions = async () => {
    if (!current) return;
    setVersionsOpen(true);
    setVersionsLoading(true);
    try {
      const res = await listLessonPlanVersions(current.id);
      setVersions(res?.data?.versions || []);
    } catch (e) {
      message.error(describeTeacherError(e, '加载版本失败').message);
      setVersions([]);
    } finally {
      setVersionsLoading(false);
    }
  };

  const classOptions = classes.map((c) => ({ value: c.id, label: c.name }));
  const className = (id?: string | null) => classes.find((c) => c.id === id)?.name;

  if (forbidden) return <PermissionAlert message={forbidden} onBack={onBack} />;

  const editorPlan = editingOverride || current;

  return (
    <div>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        <Title level={5} style={{ margin: 0 }}>
          教案{courseTitle ? ` · ${courseTitle}` : ''}
        </Title>
        <Space wrap>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => loadPlans(statusFilter)} loading={loading}>
            刷新
          </Button>
          <Button size="small" icon={<FileAddOutlined />} onClick={() => setManualOpen(true)}>
            手写草稿
          </Button>
          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setGenOpen(true)}>
            生成教案
          </Button>
        </Space>
      </Space>

      {error && <ErrorAlert message={error} onRetry={() => loadPlans(statusFilter)} />}

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {/* 教案列表 */}
        <Card
          size="small"
          title="教案列表"
          style={{ width: 320, minWidth: 280 }}
          extra={
            <Radio.Group
              size="small"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            >
              <Radio.Button value="all">全部</Radio.Button>
              <Radio.Button value="draft">草稿</Radio.Button>
              <Radio.Button value="published">已发布</Radio.Button>
            </Radio.Group>
          }
        >
          {loading ? <LoadingBlock tip="加载教案…" /> : plans.length === 0 ? (
            <EmptyBlock description="还没有教案。点击右上角「生成教案」或「手写草稿」开始。" />
          ) : (
            <List
              size="small"
              dataSource={plans}
              renderItem={(p) => (
                <List.Item
                  style={{
                    cursor: 'pointer',
                    background: current?.id === p.id ? 'rgba(15,118,110,0.08)' : undefined,
                    borderRadius: 6,
                    paddingLeft: 8,
                  }}
                  onClick={() => openPlan(p.id)}
                >
                  <List.Item.Meta
                    title={
                      <Space size={6} wrap>
                        <FileTextOutlined />
                        <Text strong={current?.id === p.id}>{p.title}</Text>
                        <Tag color={p.status === 'published' ? 'success' : 'default'}>
                          {p.status === 'published' ? '已发布' : '草稿'}
                        </Tag>
                      </Space>
                    }
                    description={
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {p.topic ? `主题：${p.topic} · ` : ''}
                        v{p.current_version ?? 1} / 共 {p.version_count ?? 1} 版
                        {p.hours ? ` · ${p.hours} 课时` : ''}
                        <br />
                        更新于 {formatTime(p.updated_at)}
                      </Text>
                    }
                  />
                </List.Item>
              )}
            />
          )}
        </Card>

        {/* 编辑区 */}
        <div style={{ flex: 1, minWidth: 420 }}>
          {detailLoading ? <LoadingBlock tip="加载教案详情…" /> : !editorPlan ? (
            <Card size="small">
              <Empty description="从左侧选择一份教案，或先生成教案草稿" />
            </Card>
          ) : (
            <>
              {editingOverride && (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message={`正在基于历史版本 v${editingOverride.version} 编辑（保存后会生成新版本，历史版本仍保留）`}
                  action={
                    <Button size="small" onClick={() => setEditingOverride(null)}>
                      返回当前版本
                    </Button>
                  }
                />
              )}

              <Space style={{ marginBottom: 12 }} wrap>
                <Button
                  size="small"
                  icon={<HistoryOutlined />}
                  onClick={handleOpenVersions}
                >
                  版本（{editorPlan.version_count ?? 1}）
                </Button>
                <Button
                  size="small"
                  icon={<EyeOutlined />}
                  onClick={() => handlePreview(editorPlan.id, editorPlan.version,
                    `v${editorPlan.version ?? 1}`)}
                >
                  预览 Markdown
                </Button>
                <Button
                  size="small"
                  icon={<DownloadOutlined />}
                  loading={acting}
                  onClick={() => handleExport(editorPlan, editorPlan.version)}
                >
                  导出 .md
                </Button>
                <Popconfirm
                  title="发布这份教案？"
                  description="发布后学生可在「我的学习」看到当前版本。发布前请确认已补全教学目标与教学步骤。"
                  okText="发布"
                  cancelText="取消"
                  onConfirm={handlePublish}
                >
                  <Button
                    size="small"
                    type="primary"
                    icon={<RocketOutlined />}
                    loading={acting}
                    disabled={editorPlan.status === 'published'}
                  >
                    {editorPlan.status === 'published' ? '已发布' : '发布'}
                  </Button>
                </Popconfirm>
              </Space>

              {/* 教案基础信息 */}
              <Card size="small" style={{ marginBottom: 12 }}>
                <Descriptions size="small" column={2}>
                  <Descriptions.Item label="所属课程">{courseTitle || '—'}</Descriptions.Item>
                  <Descriptions.Item label="指定班级">
                    {className(current?.class_id) || (current?.class_id ? current.class_id : '未指定')}
                  </Descriptions.Item>
                  <Descriptions.Item label="状态">
                    {current?.status === 'published'
                      ? <Tag color="success">已发布（v{current.published_version ?? current.version}）</Tag>
                      : <Tag>草稿</Tag>}
                  </Descriptions.Item>
                  <Descriptions.Item label="课时">{current?.hours ?? '—'}</Descriptions.Item>
                  <Descriptions.Item label="主题">{current?.topic || '—'}</Descriptions.Item>
                  <Descriptions.Item label="最后更新">{formatTime(current?.updated_at)}</Descriptions.Item>
                </Descriptions>
              </Card>

              {current && (
                <LessonPlanEditor
                  plan={editingOverride || current}
                  onSaved={async (saved, tip) => {
                    message.success(tip);
                    setEditingOverride(null);
                    setCurrent(saved);
                    await loadPlans(statusFilter);
                  }}
                />
              )}
            </>
          )}
        </div>
      </div>

      {/* 生成教案 */}
      <Modal
        title="AI 生成教案草稿"
        open={genOpen}
        onCancel={() => { setGenOpen(false); genForm.resetFields(); }}
        onOk={() => genForm.submit()}
        okText="生成"
        cancelText="取消"
        confirmLoading={generating}
        width={620}
        destroyOnClose
      >
        <Form
          form={genForm}
          layout="vertical"
          onFinish={handleGenerate}
          initialValues={{ hours: 1 }}
        >
          <Form.Item label="课程主题" name="topic" rules={[{ required: true, message: '请填写课程主题' }]}>
            <Input placeholder="例如：二叉树的遍历" maxLength={120} />
          </Form.Item>
          <Space size={12} style={{ width: '100%' }} wrap>
            <Form.Item label="课时" name="hours" style={{ width: 160 }}>
              <InputNumber min={0.5} max={40} step={0.5} addonAfter="课时" style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item label="指定班级（可选）" name="class_id" style={{ width: 240 }}>
              <Select allowClear placeholder="不选则不参考班级学情" options={classOptions} />
            </Form.Item>
            <Form.Item label="教案名称（可选）" name="title" style={{ width: 240 }}>
              <Input placeholder="默认取主题" maxLength={120} />
            </Form.Item>
          </Space>
          <Form.Item label="教学目标（可选）" name="objectives">
            <TextArea rows={3} placeholder="写下希望学生达成的目标，生成时会一并参考" />
          </Form.Item>
          <Form.Item
            label="依据资料（可选）"
            name="material_ids"
            extra={materials.length === 0
              ? '本课程还没有已就绪的资料，生成结果会提示资料不足。'
              : '不选则使用全部已就绪资料；只允许引用本课程资料。'}
          >
            <Select
              mode="multiple"
              allowClear
              disabled={materials.length === 0}
              placeholder={materials.length === 0 ? '暂无可选资料' : '选择用于生成教案的资料'}
              options={materials.map((m) => ({
                value: m.id,
                label: `${m.filename}${m.visibility === 'teacher_only' ? '（仅教师可见）' : ''}`,
              }))}
            />
          </Form.Item>
          <Alert
            type="info"
            showIcon
            message="每次生成都会新建一份草稿，不会覆盖你已编辑过的版本。"
          />
        </Form>
      </Modal>

      {/* 手写草稿 */}
      <Modal
        title="新建手写教案草稿"
        open={manualOpen}
        onCancel={() => { setManualOpen(false); manualForm.resetFields(); }}
        onOk={() => manualForm.submit()}
        okText="创建"
        cancelText="取消"
        confirmLoading={acting}
        destroyOnClose
      >
        <Form form={manualForm} layout="vertical" onFinish={handleManualCreate} initialValues={{ hours: 1 }}>
          <Form.Item label="教案名称" name="title" rules={[{ required: true, message: '请填写教案名称' }]}>
            <Input placeholder="例如：第 3 讲 二叉树" maxLength={120} />
          </Form.Item>
          <Space size={12} wrap>
            <Form.Item label="主题（可选）" name="topic">
              <Input placeholder="主题" maxLength={120} />
            </Form.Item>
            <Form.Item label="课时（可选）" name="hours">
              <InputNumber min={0.5} max={40} step={0.5} />
            </Form.Item>
            <Form.Item label="班级（可选）" name="class_id">
              <Select allowClear style={{ width: 200 }} placeholder="不指定" options={classOptions} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      {/* 版本列表 */}
      <Modal
        title="历史版本（每次生成/编辑都会新增版本，历史不会被覆盖）"
        open={versionsOpen}
        onCancel={() => setVersionsOpen(false)}
        footer={null}
        width={680}
      >
        {versionsLoading ? <LoadingBlock tip="加载版本…" /> : versions.length === 0 ? (
          <Empty description="暂无版本记录" />
        ) : (
          <List
            size="small"
            dataSource={versions}
            renderItem={(v) => (
              <List.Item
                actions={[
                  <Button key="pv" type="link" size="small"
                          onClick={() => handlePreview(current!.id, v.version, `v${v.version}`)}>
                    回看
                  </Button>,
                  <Button
                    key="edit"
                    type="link"
                    size="small"
                    onClick={() => {
                      if (!current) return;
                      setEditingOverride({
                        ...current,
                        version: v.version,
                        content: v.content,
                        evidence: v.evidence,
                        version_note: v.note ?? null,
                        version_created_at: v.created_at,
                      });
                      setVersionsOpen(false);
                    }}
                  >
                    基于此版本编辑
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space size={6} wrap>
                      <Tag color={v.version === (current?.version ?? 1) ? 'blue' : 'default'}>
                        v{v.version}
                      </Tag>
                      {v.ai_generated ? <Tag color="purple">AI 生成</Tag> : <Tag>教师编辑</Tag>}
                      {v.insufficient_evidence && <Tag color="warning">资料不足</Tag>}
                      {v.version === (current?.published_version ?? -1) && <Tag color="success">已发布</Tag>}
                    </Space>
                  }
                  description={
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {v.note || '—'} · {formatTime(v.created_at)}
                      {v.ai_model ? ` · ${v.ai_model}` : ''}
                    </Text>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Modal>

      {/* Markdown 预览 */}
      <Modal
        title={`教案预览${previewLabel ? ` · ${previewLabel}` : ''}（Markdown，与导出内容一致）`}
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width={820}
      >
        {previewLoading ? <LoadingBlock tip="生成预览…" /> : previewMd ? (
          <MarkdownBlock text={previewMd} />
        ) : (
          <Empty description="暂无预览内容" />
        )}
      </Modal>

      <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
        说明：草稿学生看不到；只有「发布」后学生才在「我的学习」看到当前版本。每次编辑保存都会生成新版本，历史版本永久保留。
      </Paragraph>
    </div>
  );
};

export default LessonPlansPanel;
