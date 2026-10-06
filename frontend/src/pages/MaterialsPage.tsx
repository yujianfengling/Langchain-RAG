import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Button, Card, Checkbox, Collapse, Empty, Form, Input, List, Modal, Popconfirm,
  Space, Spin, Tag, Typography, Upload, message,
} from 'antd';
import {
  BuildOutlined, CloudUploadOutlined, DatabaseOutlined, DeleteOutlined,
  FileTextOutlined, PlusOutlined, ReadOutlined, ReloadOutlined, SyncOutlined,
  TeamOutlined, UserOutlined,
} from '@ant-design/icons';
import { useCourse } from '../context/CourseContext';
import { createSubject } from '../api/subjects';
import {
  deleteDocument, getDocuments, getKnowledgeStats, rebuildKnowledge, uploadFile,
} from '../api/knowledge';
import { getTutorSession } from '../api/tutor';
import { getHome } from '../api/student';
import { SkeletonCard } from '../components/AnimateMotion';

// 知识图谱依赖 echarts（较大），只在学生真正展开"概念关系分析"时才加载，
// 避免资料库首屏为了一个次要功能多下载近 1MB（AGENTS 第 7 节：拆包要实测首屏）。
const KnowledgeGraph = React.lazy(() => import('../components/KnowledgeGraph'));

const { Title, Text, Paragraph } = Typography;

/**
 * 资料库（2026-10-07 统一伴学重构）
 *
 * 产品主线：创建学科 → 上传资料 → 查看处理进度 → 知识库就绪 → 开始伴学。
 * 个人学科与教师课程统一成同构卡片，但来源与可操作范围清楚区分：
 * 学生只能读课程资料，不能上传/删除/重建。
 */

interface KnowledgeDoc {
  name: string;
  size: string;
  type: string;
  upload_time: string;
}

interface StatsPayload {
  has_knowledge_base?: boolean;
  document_count?: number;
  chunk_count?: number;
  total_size_mb?: number;
}

interface StatsResponse {
  status?: string;
  stats?: StatsPayload;
}

interface HomePayload {
  last_position?: { knowledge_point?: string | null } | null;
  today_tasks?: unknown[];
  overdue_tasks?: unknown[];
  pending_reviews?: unknown[];
}

interface HomeResponse { data?: HomePayload }

type KbState = 'ready' | 'processing' | 'empty' | 'unknown';

interface SubjectMeta {
  docCount: number | null;
  state: KbState;
  progressText: string;
}

interface SubjectCardData {
  subject_id: string;
  name: string;
  source: 'course' | 'personal';
  className?: string;
  teacherName?: string;
  description?: string;
  docCount: number | null;
  state: KbState;
  progressText: string;
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

/** 生成稳定、后端可接受的学科 ID（中文名退化为随机短串，用户无需关心） */
const makeSubjectId = (name: string): string => {
  const ascii = name.trim().toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 20);
  const suffix = Math.random().toString(36).slice(2, 7);
  return ascii ? 'sub-' + ascii + '-' + suffix : 'sub-' + suffix + Date.now().toString(36).slice(-3);
};

const stateFromStats = (res: StatsResponse | null): KbState => {
  const s = res?.stats;
  if (!s) return 'unknown';
  if (s.has_knowledge_base && res?.status === 'ready') return 'ready';
  if (s.has_knowledge_base) return 'processing';
  if ((s.document_count ?? 0) > 0) return 'processing';
  return 'empty';
};

const KbStatusTag: React.FC<{ state: KbState }> = ({ state }) => {
  if (state === 'ready') return <Tag color="green">知识库就绪</Tag>;
  if (state === 'processing') return <Tag color="orange">待处理</Tag>;
  if (state === 'empty') return <Tag>暂无资料</Tag>;
  return <Tag>状态未知</Tag>;
};

const MaterialsPage: React.FC = () => {
  const navigate = useNavigate();
  const ctx = useCourse();

  const [meta, setMeta] = useState<Record<string, SubjectMeta>>({});
  const [selectedId, setSelectedId] = useState('');
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [detailStats, setDetailStats] = useState<StatsResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [selectedDocs, setSelectedDocs] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [creating, setCreating] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm<{ name: string; description?: string }>();

  const allIds = useMemo(() => {
    const set = new Set<string>();
    ctx.courses.forEach((c) => set.add(c.subject_id));
    ctx.personalSubjects.forEach((s) => set.add(s.subject_id));
    return Array.from(set);
  }, [ctx.courses, ctx.personalSubjects]);

  const loadMeta = useCallback(async (ids: string[]) => {
    if (ids.length === 0) { setMeta({}); return; }
    const entries = await Promise.all(ids.map(async (sid): Promise<[string, SubjectMeta]> => {
      let docCount: number | null = null;
      let state: KbState = 'unknown';
      try {
        const st = (await getKnowledgeStats(sid)) as unknown as StatsResponse;
        const s = st?.stats || {};
        docCount = typeof s.document_count === 'number' ? s.document_count : null;
        state = stateFromStats(st);
      } catch {
        // 尚未建库时 stats 会 404：退化为直接数资料文件，卡片不能一直显示“读取中”
        try {
          const docRes = await getDocuments(sid);
          const n = (docRes?.documents || []).length;
          docCount = n;
          state = n > 0 ? 'processing' : 'empty';
        } catch {
          state = 'unknown';
        }
      }
      let progressText = '尚未开始';
      try {
        const homeRes = (await getHome(sid)) as unknown as HomeResponse;
        const d = homeRes?.data;
        const kp = d?.last_position?.knowledge_point;
        const pending = (d?.today_tasks?.length || 0)
          + (d?.overdue_tasks?.length || 0)
          + (d?.pending_reviews?.length || 0);
        progressText = kp ? '继续：' + kp : pending > 0 ? pending + ' 项待完成' : '尚未开始';
      } catch {
        // 学习进度读取失败不影响资料卡片本身
      }
      return [sid, { docCount, state, progressText }];
    }));
    const next: Record<string, SubjectMeta> = {};
    for (const [sid, m] of entries) next[sid] = m;
    setMeta(next);
  }, []);

  const loadDetail = useCallback(async (sid: string) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      const [docRes, statRes] = await Promise.all([
        getDocuments(sid),
        getKnowledgeStats(sid)
          .then((s) => s as unknown as StatsResponse)
          .catch(() => null),
      ]);
      const list: KnowledgeDoc[] = (docRes?.documents || []) as KnowledgeDoc[];
      setDocs(list);
      setDetailStats(statRes);
      setSelectedDocs((prev) => prev.filter((n) => list.some((d) => d.name === n)));
    } catch (e) {
      setDetailError(describeError(e, '资料加载失败'));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (ctx.status === 'ready') { void loadMeta(allIds); }
  }, [ctx.status, allIds, loadMeta]);

  useEffect(() => {
    if (ctx.status === 'ready' && !selectedId && allIds.length === 1) {
      setSelectedId(allIds[0]);
    }
  }, [ctx.status, allIds, selectedId]);

  useEffect(() => {
    if (selectedId) { void loadDetail(selectedId); }
  }, [selectedId, loadDetail]);

  const cards = useMemo<SubjectCardData[]>(() => {
    const out: SubjectCardData[] = [];
    for (const c of ctx.courses) {
      const m = meta[c.subject_id];
      out.push({
        subject_id: c.subject_id,
        name: c.title,
        source: 'course',
        className: c.class_name || undefined,
        teacherName: c.teacher_name || undefined,
        description: c.description || undefined,
        docCount: m ? m.docCount : (c.material_count ?? null),
        state: m ? m.state : 'unknown',
        progressText: m ? m.progressText : '尚未开始',
      });
    }
    for (const s of ctx.personalSubjects) {
      const m = meta[s.subject_id];
      out.push({
        subject_id: s.subject_id,
        name: s.name,
        source: 'personal',
        description: s.description || undefined,
        docCount: m ? m.docCount : null,
        state: m ? m.state : 'unknown',
        progressText: m ? m.progressText : '尚未开始',
      });
    }
    return out;
  }, [ctx.courses, ctx.personalSubjects, meta]);

  const selectedCard = cards.find((c) => c.subject_id === selectedId) || null;
  const writable = selectedId ? !ctx.isCourseSubject(selectedId) : false;
  const selectedState = detailStats ? stateFromStats(detailStats) : (selectedCard?.state || 'unknown');

  const submitCreate = async () => {
    let values: { name: string; description?: string };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setCreating(true);
    try {
      const sid = makeSubjectId(values.name);
      await createSubject(sid, values.name.trim(), values.description?.trim() || undefined);
      message.success('学科已创建');
      setModalOpen(false);
      form.resetFields();
      await ctx.refresh();
      setSelectedId(sid);
    } catch (e) {
      message.error(describeError(e, '创建失败，请重试'));
    } finally {
      setCreating(false);
    }
  };

  const handleUpload = async (file: File) => {
    if (!selectedId) return;
    setUploading(true);
    try {
      await uploadFile(selectedId, file);
      message.success('已上传 ' + file.name);
      await loadDetail(selectedId);
      await loadMeta(allIds);
    } catch (e) {
      message.error(describeError(e, '上传失败，请重试'));
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (name: string) => {
    if (!selectedId) return;
    try {
      await deleteDocument(selectedId, name);
      message.success('已删除');
      setSelectedDocs((prev) => prev.filter((n) => n !== name));
      await loadDetail(selectedId);
      await loadMeta(allIds);
    } catch (e) {
      message.error(describeError(e, '删除失败，请重试'));
    }
  };

  const handleRebuild = async () => {
    if (!selectedId) return;
    setRebuilding(true);
    try {
      await rebuildKnowledge(selectedId);
      message.success('知识库重建完成');
      await loadDetail(selectedId);
      await loadMeta(allIds);
    } catch (e) {
      message.error(describeError(e, '重建失败，请重试'));
    } finally {
      setRebuilding(false);
    }
  };

  /** 用所选资料进入伴学：doc_id 必须来自后端统一资料范围（课程为 m:xx，个人为 f:文件名） */
  const startScoped = async () => {
    if (!selectedId) return;
    const chosen = selectedDocs.slice();
    let ids: string[] = [];
    if (chosen.length > 0) {
      try {
        const session = await getTutorSession(selectedId);
        const byName = new Map<string, string>();
        for (const m of session.materials || []) byName.set(m.file_name, m.doc_id);
        ids = chosen.map((n) => byName.get(n) || 'f:' + n);
      } catch {
        ids = chosen.map((n) => 'f:' + n);
      }
    }
    const params = new URLSearchParams({ subject: selectedId });
    if (ids.length > 0) params.set('docs', ids.join(','));
    navigate('/student?' + params.toString());
  };

  const toggleDoc = (name: string, checked: boolean) => {
    setSelectedDocs((prev) => (checked ? [...prev, name] : prev.filter((n) => n !== name)));
  };

  if (ctx.status === 'loading') {
    return (
      <div style={{ maxWidth: 1080, margin: '0 auto', padding: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
          <SkeletonCard height={180} />
          <SkeletonCard height={180} />
          <SkeletonCard height={180} />
        </div>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto', padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <DatabaseOutlined style={{ color: 'var(--color-primary)', fontSize: 18 }} />
        <Title level={4} style={{ margin: 0 }}>资料库</Title>
        <Text type="secondary" style={{ fontSize: 12 }}>管理伴学依据，勾选资料即可定向学习</Text>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          style={{ marginLeft: 'auto' }}
          onClick={() => setModalOpen(true)}
        >
          创建学科
        </Button>
      </div>

      {ctx.status === 'error' && (
        <Alert
          type="error" showIcon style={{ marginBottom: 12 }}
          message="资料列表加载失败"
          description={ctx.error || undefined}
          action={<Button icon={<SyncOutlined />} onClick={() => ctx.refresh()}>重试</Button>}
        />
      )}
      {ctx.forbidden && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={ctx.forbidden} />}

      {cards.length === 0 ? (
        <Card>
          <Empty description="还没有学科。创建一个学科并上传资料，就能开始伴学。">
            <Space>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>创建学科</Button>
              {ctx.courses.length > 0 && (
                <Button onClick={() => navigate('/student/courses')}>查看我的课程</Button>
              )}
            </Space>
          </Empty>
        </Card>
      ) : (
        <List
          grid={{ gutter: 16, xs: 1, sm: 2, lg: 3 }}
          dataSource={cards}
          renderItem={(item) => {
            const active = item.subject_id === selectedId;
            return (
              <List.Item style={{ display: 'flex' }}>
                <Card
                  hoverable
                  style={{
                    flex: 1,
                    borderColor: active ? 'var(--color-primary)' : undefined,
                    boxShadow: active ? 'var(--shadow-md)' : undefined,
                  }}
                  bodyStyle={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}
                  onClick={() => setSelectedId(item.subject_id)}
                >
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                    <div style={{
                      width: 34, height: 34, borderRadius: 9, flexShrink: 0,
                      background: 'var(--color-primary-50)', color: 'var(--color-primary)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                      {item.source === 'course' ? <TeamOutlined /> : <UserOutlined />}
                    </div>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div
                        title={item.name}
                        style={{ fontWeight: 600, fontSize: 15, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
                      >
                        {item.name}
                      </div>
                      <Space size={4} wrap style={{ marginTop: 2 }}>
                        <Tag color={item.source === 'course' ? 'blue' : 'default'} style={{ marginInlineEnd: 0 }}>
                          {item.source === 'course' ? '教师课程' : '个人学科'}
                        </Tag>
                        {item.className && <Text type="secondary" style={{ fontSize: 12 }}>{item.className}</Text>}
                        {item.teacherName && <Text type="secondary" style={{ fontSize: 12 }}>· {item.teacherName}</Text>}
                      </Space>
                    </div>
                  </div>

                  <Paragraph
                    type="secondary"
                    ellipsis={{ rows: 2 }}
                    style={{ margin: 0, minHeight: 40, fontSize: 12 }}
                  >
                    {item.description
                      || (item.source === 'course' ? '课程资料由教师维护，可阅读并用于伴学' : '上传自己的资料，建立专属知识库')}
                  </Paragraph>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {item.docCount === null ? '资料数量读取中' : '资料 ' + item.docCount + ' 份'}
                    </Text>
                    <KbStatusTag state={item.state} />
                  </div>

                  <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={item.progressText}>
                    {item.progressText}
                  </Text>

                  <div style={{ marginTop: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Button
                      type="primary"
                      icon={<ReadOutlined />}
                      onClick={(e) => {
                        e.stopPropagation();
                        navigate('/student?subject=' + encodeURIComponent(item.subject_id));
                      }}
                    >
                      {item.progressText === '尚未开始' ? '开始学习' : '继续学习'}
                    </Button>
                    <Button
                      type="text"
                      onClick={(e) => { e.stopPropagation(); setSelectedId(item.subject_id); }}
                    >
                      管理资料
                    </Button>
                  </div>
                </Card>
              </List.Item>
            );
          }}
        />
      )}

      {selectedId && selectedCard && (
        <Card
          style={{ marginTop: 16 }}
          title={(
            <Space wrap>
              <DatabaseOutlined style={{ color: 'var(--color-primary)' }} />
              <Text strong style={{ fontSize: 15 }}>{selectedCard.name}</Text>
              <Tag color={selectedCard.source === 'course' ? 'blue' : 'default'}>
                {selectedCard.source === 'course' ? '教师课程' : '个人学科'}
              </Tag>
            </Space>
          )}
          extra={<Button type="text" icon={<ReloadOutlined />} onClick={() => loadDetail(selectedId)}>刷新</Button>}
        >
          {detailError && (
            <Alert
              type="error" showIcon style={{ marginBottom: 10 }}
              message={detailError}
              action={<Button icon={<SyncOutlined />} onClick={() => loadDetail(selectedId)}>重试</Button>}
            />
          )}

          <Space size={16} wrap style={{ marginBottom: 10 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>资料 {docs.length} 份</Text>
            {typeof detailStats?.stats?.chunk_count === 'number' && (
              <Text type="secondary" style={{ fontSize: 12 }}>索引 {detailStats.stats.chunk_count} 段</Text>
            )}
            {typeof detailStats?.stats?.total_size_mb === 'number' && (
              <Text type="secondary" style={{ fontSize: 12 }}>{detailStats.stats.total_size_mb} MB</Text>
            )}
            <KbStatusTag state={selectedState} />
          </Space>

          {!writable && (
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
              课程资料由授课教师维护，你可以阅读并用于伴学，但不能修改。
            </Text>
          )}

          {writable && (
            <Space style={{ marginBottom: 8 }} wrap>
              <Upload
                showUploadList={false}
                accept=".pdf,.docx,.txt,.md,.jpg,.jpeg,.png,.webp"
                beforeUpload={(file) => { void handleUpload(file); return false; }}
              >
                <Button icon={<CloudUploadOutlined />} loading={uploading}>上传资料</Button>
              </Upload>
              <Button icon={<BuildOutlined />} loading={rebuilding} disabled={docs.length === 0} onClick={handleRebuild}>
                重建知识库
              </Button>
            </Space>
          )}

          {docs.length > 0 && selectedState !== 'ready' && (
            <Alert
              type="warning" showIcon style={{ marginBottom: 10 }}
              message="资料已上传，但知识库尚未就绪"
              description={writable ? '重建知识库后，这些资料才能用于伴学检索。' : '知识库由授课教师维护，可先阅读资料。'}
              action={writable ? (
                <Button size="small" icon={<BuildOutlined />} loading={rebuilding} onClick={handleRebuild}>重建知识库</Button>
              ) : undefined}
            />
          )}

          {detailLoading ? (
            <Spin />
          ) : docs.length === 0 ? (
            <Empty description={writable ? '还没有资料，上传第一份课件即可开始伴学' : '该课程暂无资料'}>
              {writable && (
                <Upload
                  showUploadList={false}
                  accept=".pdf,.docx,.txt,.md,.jpg,.jpeg,.png,.webp"
                  beforeUpload={(file) => { void handleUpload(file); return false; }}
                >
                  <Button type="primary" icon={<CloudUploadOutlined />} loading={uploading}>上传第一份资料</Button>
                </Upload>
              )}
            </Empty>
          ) : (
            <>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, margin: '6px 0' }}>
                <Checkbox
                  indeterminate={selectedDocs.length > 0 && selectedDocs.length < docs.length}
                  checked={docs.length > 0 && selectedDocs.length === docs.length}
                  onChange={(e) => setSelectedDocs(e.target.checked ? docs.map((d) => d.name) : [])}
                >
                  全选
                </Checkbox>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {selectedDocs.length > 0 ? '已选 ' + selectedDocs.length + ' 份' : '不勾选则使用全部资料'}
                </Text>
                <Button
                  type="primary"
                  icon={<ReadOutlined />}
                  style={{ marginLeft: 'auto' }}
                  onClick={startScoped}
                >
                  {selectedDocs.length > 0 ? '用所选 ' + selectedDocs.length + ' 份资料开始伴学' : '用全部资料开始学习'}
                </Button>
              </div>

              <List
                size="small"
                dataSource={docs}
                renderItem={(doc) => {
                  const href = '/api/knowledge/' + encodeURIComponent(selectedId)
                    + '/documents/' + encodeURIComponent(doc.name) + '/file';
                  return (
                    <List.Item
                      actions={[
                        <a key="view" href={href} target="_blank" rel="noreferrer">查看</a>,
                        writable ? (
                          <Popconfirm
                            key="del"
                            title="删除这份资料？"
                            description="删除后需重建知识库才会从检索中移除。"
                            okText="删除" cancelText="取消"
                            onConfirm={() => handleDelete(doc.name)}
                          >
                            <Button type="text" danger size="small" icon={<DeleteOutlined />}>删除</Button>
                          </Popconfirm>
                        ) : null,
                      ].filter(Boolean) as React.ReactNode[]}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, width: '100%' }}>
                        <Checkbox
                          checked={selectedDocs.includes(doc.name)}
                          onChange={(e) => toggleDoc(doc.name, e.target.checked)}
                        />
                        <FileTextOutlined style={{ color: 'var(--color-primary)', flexShrink: 0 }} />
                        <Text
                          ellipsis
                          title={doc.name}
                          style={{ maxWidth: 320, minWidth: 0 }}
                        >
                          {doc.name}
                        </Text>
                        <Tag style={{ flexShrink: 0 }}>{(doc.type || '').replace('.', '').toUpperCase()}</Tag>
                        <Text type="secondary" style={{ fontSize: 12, marginLeft: 'auto', flexShrink: 0 }}>
                          {doc.size} · {doc.upload_time}
                        </Text>
                      </div>
                    </List.Item>
                  );
                }}
              />

              {/* 检索用知识图谱（概念关系分析）：与"学习路线"是两个东西，这里明确标注，
                  旧的知识库页面删除后由此入口承接，功能不会变成"找不到"。 */}
              <Collapse
                ghost
                style={{ marginTop: 12, borderTop: '1px solid var(--color-border-light)' }}
                items={[{
                  key: 'graph',
                  label: (
                    <span>
                      知识图谱（概念关系分析）
                      <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                        用于查看资料里的概念关系，不是学习路线
                      </Text>
                    </span>
                  ),
                  children: (
                    <React.Suspense fallback={<SkeletonCard height={280} />}>
                      <KnowledgeGraph subjectId={selectedId} />
                    </React.Suspense>
                  ),
                }]}
              />
            </>
          )}
        </Card>
      )}

      <Modal
        title="创建学科"
        open={modalOpen}
        onCancel={() => { setModalOpen(false); form.resetFields(); }}
        onOk={submitCreate}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item
            name="name"
            label="学科名称"
            rules={[
              { required: true, message: '请输入学科名称' },
              { max: 40, message: '名称不超过 40 个字' },
            ]}
          >
            <Input placeholder="例如：数据结构、大学物理" maxLength={40} autoFocus />
          </Form.Item>
          <Form.Item
            name="description"
            label="描述（可选）"
            rules={[{ max: 120, message: '描述不超过 120 个字' }]}
          >
            <Input.TextArea placeholder="一句话说明这个学科要学什么" rows={2} maxLength={120} showCount />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default MaterialsPage;
