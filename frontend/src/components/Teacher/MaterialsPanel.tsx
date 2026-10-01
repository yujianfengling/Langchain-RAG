import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, Card, Popconfirm, Radio, Space, Table, Tag, Typography, Upload, message,
} from 'antd';
import type { UploadProps } from 'antd';
import {
  BuildOutlined, DeleteOutlined, FileTextOutlined, InboxOutlined, ReloadOutlined,
} from '@ant-design/icons';
import {
  CourseMaterial, KnowledgeBaseStatus, MaterialVisibility,
  deleteCourseMaterial, describeTeacherError, isForbiddenError, listCourseMaterials,
  rebuildCourseKnowledge, setMaterialVisibility, uploadCourseMaterial,
} from '../../api/teacher';
import {
  EmptyBlock, ErrorAlert, LoadingBlock, PermissionAlert, formatBytes, formatTime,
  materialStatusMeta, visibilityLabel,
} from './common';

const { Text, Title, Paragraph } = Typography;
const { Dragger } = Upload;

export interface MaterialsPanelProps {
  courseId: string;
  courseTitle?: string;
  onBack?: () => void;
}

/** 课程资料：上传 / 列表（状态与失败原因）/ 可见范围 / 删除 / 重建知识库 */
const MaterialsPanel: React.FC<MaterialsPanelProps> = ({ courseId, courseTitle, onBack }) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState<string | null>(null);
  const [materials, setMaterials] = useState<CourseMaterial[]>([]);
  const [kb, setKb] = useState<KnowledgeBaseStatus | null>(null);

  const [uploadVisibility, setUploadVisibility] = useState<MaterialVisibility>('students');
  const [uploading, setUploading] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildOk, setRebuildOk] = useState<boolean | null>(null);
  const [rebuildMsg, setRebuildMsg] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!courseId) return;
    setLoading(true);
    setError(null);
    setForbidden(null);
    try {
      const res = await listCourseMaterials(courseId);
      setMaterials(res?.data?.materials || []);
      setKb(res?.data?.knowledge_base || null);
    } catch (e) {
      const f = describeTeacherError(e, '加载课程资料失败');
      if (isForbiddenError(e)) setForbidden(f.message);
      else setError(f.message);
    } finally {
      setLoading(false);
    }
  }, [courseId]);

  useEffect(() => { load(); }, [load]);

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const res = await uploadCourseMaterial(courseId, file, uploadVisibility);
      const data = res?.data;
      if (data?.status && data.status !== 'ready') {
        message.warning(`《${file.name}》已上传，但处理状态为「${materialStatusMeta(data.status).label}」`);
      } else {
        message.success(`《${file.name}》上传成功`);
      }
      if (data?.kb) setKb(data.kb);
      const rebuild = data?.kb_rebuild;
      // 上传后后端会自动构建知识库；若这一步失败必须让老师知道（否则 AI 检索/出题会"莫名"没有依据）
      if (rebuild?.attempted && rebuild.ok === false) {
        setRebuildMsg(`上传成功，但知识库自动构建失败：${rebuild.error || '后端未返回原因'}`);
      } else if (rebuild?.attempted && rebuild.ok) {
        setRebuildMsg('资料已上传，课程知识库已自动构建完成，现在可以生成教案与 AI 出题。');
      }
      await load();
    } catch (e) {
      message.error(describeTeacherError(e, '资料上传失败').message);
    } finally {
      setUploading(false);
    }
  };

  const handleVisibility = async (m: CourseMaterial, next: MaterialVisibility) => {
    setBusyId(m.id);
    try {
      await setMaterialVisibility(m.id, courseId, next);
      message.success(`可见范围已改为「${visibilityLabel(next)}」`);
      await load();
    } catch (e) {
      message.error(describeTeacherError(e, '修改可见范围失败').message);
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (m: CourseMaterial) => {
    setBusyId(m.id);
    try {
      const res = await deleteCourseMaterial(m.id, courseId);
      const d = res?.data;
      if (d?.file_error) {
        message.warning(`已删除记录，但文件删除失败：${d.file_error}`);
      } else if (d?.rebuild_error) {
        message.warning(`资料已删除，但知识库重建失败：${d.rebuild_error}`);
      } else {
        message.success('资料已删除，并已重建索引');
      }
      await load();
    } catch (e) {
      message.error(describeTeacherError(e, '删除资料失败').message);
    } finally {
      setBusyId(null);
    }
  };

  const handleRebuild = async () => {
    setRebuilding(true);
    setRebuildOk(null);
    setRebuildMsg(null);
    try {
      const res = await rebuildCourseKnowledge(courseId);
      const d = res?.data;
      if (d?.ok) {
        setRebuildOk(true);
        setRebuildMsg('知识库重建完成');
        message.success('知识库重建完成');
      } else {
        // 后端明确返回失败：如实展示失败原因，不假装成功
        setRebuildOk(false);
        setRebuildMsg(d?.error || '知识库重建失败（后端未返回原因）');
      }
      if (d?.kb) setKb(d.kb);
      await load();
    } catch (e) {
      const f = describeTeacherError(e, '知识库重建失败');
      setRebuildOk(false);
      setRebuildMsg(f.message);
    } finally {
      setRebuilding(false);
    }
  };

  const uploadProps: UploadProps = {
    name: 'file',
    multiple: false,
    showUploadList: false,
    accept: '.pdf,.docx,.txt,.md,.jpg,.jpeg,.png,.webp',
    beforeUpload: (file) => {
      handleUpload(file as File);
      return false;
    },
  };

  if (forbidden) return <PermissionAlert message={forbidden} onBack={onBack} />;

  return (
    <div>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        <Title level={5} style={{ margin: 0 }}>
          课程资料{courseTitle ? ` · ${courseTitle}` : ''}
        </Title>
        <Space wrap>
          <Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
          <Button
            size="small"
            type="primary"
            icon={<BuildOutlined />}
            loading={rebuilding}
            onClick={handleRebuild}
          >
            重建知识库
          </Button>
        </Space>
      </Space>

      {error && <ErrorAlert message={error} onRetry={load} />}

      {/* 知识库状态：不可用时必须显示原因 */}
      {kb && (
        kb.available ? (
          <Alert
            type="success"
            showIcon
            style={{ marginBottom: 12 }}
            message={`课程知识库可用（已收录 ${kb.document_count} 份文档）`}
            description={
              <Text type="secondary" style={{ fontSize: 12 }}>
                教案生成、AI 出题、学情下钻都会从此知识库检索依据。上传/删除资料后可点击「重建知识库」刷新索引。
              </Text>
            }
          />
        ) : (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 12 }}
            message="课程知识库当前不可用"
            description={
              <Space direction="vertical" size={2}>
                <Text>原因：{kb.error || '后端未返回原因'}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  知识库不可用时，教案生成与 AI 出题会因缺少依据而失败或提示资料不足。
                </Text>
              </Space>
            }
          />
        )
      )}

      {/* 重建结果（成功/失败都要说清楚） */}
      {rebuildOk === false && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="知识库重建失败"
          description={rebuildMsg}
          action={<Button size="small" onClick={handleRebuild}>重试</Button>}
        />
      )}
      {rebuildOk === true && (
        <Alert type="success" showIcon style={{ marginBottom: 12 }} message={rebuildMsg || '知识库重建完成'} />
      )}

      <Card size="small" title="上传资料" style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space wrap>
            <Text>可见范围：</Text>
            <Radio.Group
              value={uploadVisibility}
              onChange={(e) => setUploadVisibility(e.target.value as MaterialVisibility)}
              optionType="button"
              buttonStyle="solid"
              size="small"
            >
              <Radio.Button value="students">学生可见</Radio.Button>
              <Radio.Button value="teacher_only">仅教师可见</Radio.Button>
            </Radio.Group>
            <Text type="secondary" style={{ fontSize: 12 }}>
              仅教师可见的资料学生看不到，但会作为教案与出题的依据。
            </Text>
          </Space>
          <Dragger {...uploadProps} disabled={uploading}>
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">{uploading ? '上传处理中…' : '点击或拖拽文件到此区域上传'}</p>
            <p className="ant-upload-hint">
              支持 PDF、DOCX、TXT、Markdown、图片（JPG/PNG/WEBP）。上传后会进入本课程专属知识库。
            </p>
          </Dragger>
        </Space>
      </Card>

      <Card size="small" title={`资料列表（${materials.length}）`}>
        {loading ? (
          <LoadingBlock tip="正在加载资料…" />
        ) : materials.length === 0 ? (
          <EmptyBlock description="该课程还没有资料。上传资料后教案生成与 AI 出题才有依据。" />
        ) : (
          <Table<CourseMaterial>
            rowKey="id"
            size="small"
            dataSource={materials}
            pagination={false}
            scroll={{ x: 900 }}
            columns={[
              {
                title: '文件名', dataIndex: 'filename',
                render: (v: string) => (
                  <Space size={6}>
                    <FileTextOutlined style={{ color: '#0f766e' }} />
                    <Text>{v}</Text>
                  </Space>
                ),
              },
              {
                title: '大小', dataIndex: 'size_bytes', width: 100,
                render: (v: number | null) => formatBytes(v),
              },
              {
                title: '处理状态', dataIndex: 'status', width: 110,
                render: (s: string) => {
                  const m = materialStatusMeta(s);
                  return <Tag color={m.color}>{m.label}</Tag>;
                },
              },
              {
                title: '失败原因', dataIndex: 'error_message',
                render: (v: string | null) =>
                  v ? <Text type="danger" style={{ fontSize: 12 }}>{v}</Text> : '—',
              },
              {
                title: '可见范围', dataIndex: 'visibility', width: 130,
                render: (v: string) => (
                  <Tag color={v === 'teacher_only' ? 'purple' : 'blue'}>{visibilityLabel(v)}</Tag>
                ),
              },
              {
                title: '上传时间', dataIndex: 'created_at', width: 170,
                render: (v: string) => formatTime(v),
              },
              {
                title: '操作', width: 220, fixed: 'right',
                render: (_: unknown, m: CourseMaterial) => (
                  <Space size={4}>
                    <Button
                      size="small"
                      type="link"
                      loading={busyId === m.id}
                      onClick={() => handleVisibility(
                        m, m.visibility === 'teacher_only' ? 'students' : 'teacher_only')}
                    >
                      {m.visibility === 'teacher_only' ? '改为学生可见' : '设为仅教师可见'}
                    </Button>
                    <Popconfirm
                      title="删除该资料？"
                      description="删除后会同时从知识库移除并重建索引，操作不可撤销。"
                      okText="删除"
                      okButtonProps={{ danger: true }}
                      cancelText="取消"
                      onConfirm={() => handleDelete(m)}
                    >
                      <Button size="small" type="link" danger icon={<DeleteOutlined />}
                              loading={busyId === m.id}>
                        删除
                      </Button>
                    </Popconfirm>
                  </Space>
                ),
              },
            ]}
          />
        )}
        <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
          状态为「处理失败」的资料不会进入检索：请查看失败原因（常见为格式不支持、文件损坏或文本无法解析）后重新上传。
        </Paragraph>
      </Card>
    </div>
  );
};

export default MaterialsPanel;
