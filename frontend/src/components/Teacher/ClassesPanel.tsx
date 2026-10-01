import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, Card, Descriptions, Empty, Form, Input, List, Modal, Popconfirm,
  Space, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  KeyOutlined, PlusOutlined, ReloadOutlined, TeamOutlined, UserDeleteOutlined,
} from '@ant-design/icons';
import {
  ClassMember, ClassMembersResult, TeacherClass,
  createClass, describeTeacherError, isForbiddenError, listClassMembers, listClasses,
  removeClassMember, resetClassJoinCode,
} from '../../api/teacher';
import {
  EmptyBlock, ErrorAlert, LoadingBlock, PermissionAlert, formatTime, memberStatusMeta,
} from './common';

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

export interface ClassesPanelProps {
  courseId: string;
  courseTitle?: string;
  /** 从工作台跳转过来时预选的班级 */
  focusClassId?: string;
  onBack?: () => void;
}

/** 班级管理：邀请码、成员（含退班/移出记录）、重置邀请码、移除学生 */
const ClassesPanel: React.FC<ClassesPanelProps> = ({
  courseId, courseTitle, focusClassId, onBack,
}) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState<string | null>(null);
  const [classes, setClasses] = useState<TeacherClass[]>([]);
  const [selectedId, setSelectedId] = useState<string | undefined>(focusClassId);

  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detail, setDetail] = useState<ClassMembersResult | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<{ name: string }>();
  const [busy, setBusy] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<ClassMember | null>(null);
  const [removeReason, setRemoveReason] = useState('');

  const loadClasses = useCallback(async () => {
    if (!courseId) return;
    setLoading(true);
    setError(null);
    setForbidden(null);
    try {
      const res = await listClasses(courseId);
      const list = res?.data?.classes || [];
      setClasses(list);
      setSelectedId((prev) => {
        if (prev && list.some((c) => c.id === prev)) return prev;
        return list[0]?.id;
      });
    } catch (e) {
      const f = describeTeacherError(e, '加载班级失败');
      if (isForbiddenError(e)) setForbidden(f.message);
      else setError(f.message);
    } finally {
      setLoading(false);
    }
  }, [courseId]);

  const loadMembers = useCallback(async (classId?: string) => {
    if (!classId) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    try {
      const res = await listClassMembers(classId);
      setDetail(res?.data || null);
    } catch (e) {
      const f = describeTeacherError(e, '加载成员失败');
      setDetailError(f.message);
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => { loadClasses(); }, [loadClasses]);
  useEffect(() => { loadMembers(selectedId); }, [selectedId, loadMembers]);

  const handleCreate = async (values: { name: string }) => {
    setCreating(true);
    try {
      const res = await createClass(courseId, values.name.trim());
      const created = res?.data;
      message.success(`班级已创建${created?.join_code ? `，班级邀请码 ${created.join_code}` : ''}`);
      setCreateOpen(false);
      form.resetFields();
      if (created?.id) setSelectedId(created.id);
      await loadClasses();
    } catch (e) {
      message.error(describeTeacherError(e, '创建班级失败').message);
    } finally {
      setCreating(false);
    }
  };

  const handleResetCode = async () => {
    if (!selectedId) return;
    setBusy(true);
    try {
      const res = await resetClassJoinCode(selectedId);
      const code = res?.data?.join_code;
      message.success(`班级邀请码已重置${code ? `：${code}（旧码已立即失效）` : ''}`);
      await loadClasses();
      await loadMembers(selectedId);
    } catch (e) {
      message.error(describeTeacherError(e, '重置邀请码失败').message);
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    if (!selectedId || !removeTarget) return;
    setBusy(true);
    try {
      await removeClassMember(selectedId, removeTarget.member_id, removeReason.trim() || undefined);
      message.success(`${removeTarget.display_name} 已移出班级（历史作答保留）`);
      setRemoveTarget(null);
      setRemoveReason('');
      await loadClasses();
      await loadMembers(selectedId);
    } catch (e) {
      message.error(describeTeacherError(e, '移出学生失败').message);
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <LoadingBlock tip="正在加载班级…" />;
  if (forbidden) return <PermissionAlert message={forbidden} onBack={onBack} />;

  const selected = classes.find((c) => c.id === selectedId) || null;

  return (
    <div>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        <Title level={5} style={{ margin: 0 }}>
          班级管理{courseTitle ? ` · ${courseTitle}` : ''}
        </Title>
        <Space wrap>
          <Button size="small" icon={<ReloadOutlined />} onClick={loadClasses} loading={loading}>刷新</Button>
          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建班级
          </Button>
        </Space>
      </Space>

      {error && <ErrorAlert message={error} onRetry={loadClasses} />}

      {/* 班级列表 */}
      <Card size="small" title="班级列表" style={{ marginBottom: 16 }}>
        {classes.length === 0 ? (
          <EmptyBlock description="该课程还没有班级。新建班级后把班级邀请码发给学生即可。" />
        ) : (
          <List
            size="small"
            dataSource={classes}
            renderItem={(c) => {
              const active = c.id === selectedId;
              return (
                <List.Item
                  onClick={() => setSelectedId(c.id)}
                  style={{
                    cursor: 'pointer',
                    background: active ? 'rgba(15,118,110,0.06)' : undefined,
                    borderRadius: 6,
                    paddingLeft: 8,
                  }}
                  actions={[
                    <Tag key="cnt" icon={<TeamOutlined />}>{c.member_count ?? 0} 人</Tag>,
                    <Text key="code" copyable={{ text: c.join_code, tooltips: ['复制邀请码', '已复制'] }}>
                      <Tag color="blue" icon={<KeyOutlined />}>班级邀请码 {c.join_code}</Tag>
                    </Text>,
                  ]}
                >
                  <List.Item.Meta
                    title={<Text strong>{c.name}</Text>}
                    description={
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        创建于 {formatTime(c.created_at)}
                        {c.status !== 'active' ? ` · 状态：${c.status}` : ''}
                      </Text>
                    }
                  />
                </List.Item>
              );
            }}
          />
        )}
      </Card>

      {selected && (
        <Card
          size="small"
          title={`${selected.name} · 学生与班级邀请码`}
          extra={
            <Space>
              <Popconfirm
                title="重置邀请码？"
                description="重置后旧邀请码立即失效，已在班学生不受影响。"
                onConfirm={handleResetCode}
                okText="确认重置"
                cancelText="取消"
              >
                <Button size="small" icon={<KeyOutlined />} loading={busy}>重置邀请码</Button>
              </Popconfirm>
            </Space>
          }
        >
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={
              <Space size={6} wrap>
                <Text>班级邀请码：</Text>
                <Text strong copyable style={{ fontSize: 18, letterSpacing: 2 }}>
                  {selected.join_code}
                </Text>
              </Space>
            }
            description={
              <Paragraph style={{ marginBottom: 0 }}>
                把班级邀请码发给学生，学生在「我的学习」页输入即可加入本班。
                重置邀请码后旧码立即失效，请把新码重新发给学生。
              </Paragraph>
            }
          />

          <Descriptions size="small" column={3} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="在班学生">{detail?.active_count ?? 0} 人</Descriptions.Item>
            <Descriptions.Item label="成员记录总数">{detail?.members.length ?? 0} 条</Descriptions.Item>
            <Descriptions.Item label="班级状态">{selected.status}</Descriptions.Item>
          </Descriptions>

          {detailError && <ErrorAlert message={detailError} onRetry={() => loadMembers(selectedId)} />}
          {detailLoading ? <LoadingBlock tip="正在加载成员…" /> : !detail ? null : detail.members.length === 0 ? (
            <EmptyBlock description="该班级还没有成员。学生在「我的学习」页输入邀请码后会出现这里。" />
          ) : (
            <Table<ClassMember>
              rowKey="member_id"
              size="small"
              dataSource={detail.members}
              pagination={false}
              scroll={{ x: 760 }}
              columns={[
                { title: '学生', dataIndex: 'display_name', width: 160 },
                {
                  title: '账号', dataIndex: 'username', width: 140,
                  render: (v: string | null) => v || '—',
                },
                {
                  title: '状态', dataIndex: 'status', width: 100,
                  render: (s: string) => {
                    const m = memberStatusMeta(s);
                    return <Tag color={m.color}>{m.label}</Tag>;
                  },
                },
                {
                  title: '加入时间', dataIndex: 'joined_at', width: 170,
                  render: (v: string) => formatTime(v),
                },
                {
                  title: '离开 / 被移出', width: 170,
                  render: (_: unknown, r: ClassMember) =>
                    r.removed_at ? `移出：${formatTime(r.removed_at)}`
                      : r.left_at ? `退班：${formatTime(r.left_at)}` : '—',
                },
                {
                  title: '操作', width: 110, fixed: 'right',
                  render: (_: unknown, r: ClassMember) => (
                    <Tooltip title={r.status === 'active' ? '移出班级（保留历史记录）' : '该学生已不在班'}>
                      <Button
                        size="small"
                        danger
                        type="link"
                        icon={<UserDeleteOutlined />}
                        disabled={r.status !== 'active'}
                        onClick={() => setRemoveTarget(r)}
                      >
                        移出
                      </Button>
                    </Tooltip>
                  ),
                },
              ]}
            />
          )}

          {/* 成员变动记录 */}
          <Card
            type="inner"
            size="small"
            style={{ marginTop: 12 }}
            title="成员变动记录（加入 / 退班 / 被移出）"
          >
            {(detail?.events || []).length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无变动记录" />
            ) : (
              <List
                size="small"
                dataSource={detail?.events || []}
                renderItem={(ev) => (
                  <List.Item>
                    <Space size={8} wrap>
                      <Tag
                        color={ev.event_type?.includes('join') ? 'green'
                          : ev.event_type?.includes('remove') ? 'red' : 'orange'}
                      >
                        {ev.event_type}
                      </Tag>
                      <Text code style={{ fontSize: 12 }}>{ev.user_id}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>{formatTime(ev.created_at)}</Text>
                      {ev.detail && Object.keys(ev.detail).length > 0 && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {JSON.stringify(ev.detail)}
                        </Text>
                      )}
                    </Space>
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Card>
      )}

      {/* 新建班级 */}
      <Modal
        title="新建班级"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); form.resetFields(); }}
        footer={null}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            label="班级名称"
            name="name"
            rules={[{ required: true, message: '请输入班级名称' }]}
          >
            <Input placeholder="例如：2026 春 · 计算机导论 1 班" maxLength={60} />
          </Form.Item>
          <Text type="secondary" style={{ fontSize: 12 }}>
            创建后系统会生成 6 位班级邀请码（学生入班用，与「设置 → 教师身份」里的教师邀请码无关），学生凭码在「我的学习」页加入。
          </Text>
          <Form.Item style={{ marginBottom: 0, textAlign: 'right', marginTop: 16 }}>
            <Space>
              <Button onClick={() => { setCreateOpen(false); form.resetFields(); }}>取消</Button>
              <Button type="primary" htmlType="submit" loading={creating}>创建</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 移出学生 */}
      <Modal
        title="把学生移出班级"
        open={!!removeTarget}
        onCancel={() => { setRemoveTarget(null); setRemoveReason(''); }}
        onOk={handleRemove}
        okText="确认移出"
        okButtonProps={{ danger: true, loading: busy }}
        cancelText="取消"
        destroyOnClose
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`确认把「${removeTarget?.display_name || ''}」移出本班？`}
          description="移出只影响班级关系：历史作答与学习记录会保留，学生可凭新的邀请码重新加入。"
        />
        <TextArea
          rows={3}
          value={removeReason}
          onChange={(e) => setRemoveReason(e.target.value)}
          placeholder="移出原因（可选，会记入变动记录）"
          maxLength={200}
        />
      </Modal>
    </div>
  );
};

export default ClassesPanel;
