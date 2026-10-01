import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, Card, Empty, Form, Input, List, Popconfirm, Space, Switch,
  Table, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, StopOutlined,
  PlayCircleOutlined, HistoryOutlined,
} from '@ant-design/icons';
import {
  MemoryItem, getMemory, getEffectiveMemory, createMemory, correctMemory,
  setMemoryStatus, deleteMemory, getMemoryHistory, refreshInference,
} from '../../api/planAgent';

const { Text } = Typography;

const TYPE_META: Record<string, { color: string; label: string; hint: string }> = {
  preference: { color: 'blue', label: '我的明确设置',
    hint: '你主动指定的偏好/约束，优先于系统推断' },
  inference: { color: 'orange', label: '系统推断（暂时）',
    hint: '根据学习记录推断，带依据与有效期，可随时更正或关闭' },
  curriculum: { color: 'green', label: '课程资料确认',
    hint: '经课程资料/教师确认的内容；未核验的回答与点赞不会写到这里' },
};

/**
 * 学习记忆管理（阶段二）
 *
 * 学生可以查看、更正、删除、关闭个性化记忆，并看到每条记忆的
 * 来源、时间、适用范围与依据。
 */
const LearningMemoryPanel: React.FC<{ subjectId?: string }> = ({ subjectId }) => {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [shadowed, setShadowed] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [history, setHistory] = useState<{ id: string; rows: any[] } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [all, eff] = await Promise.all([
        getMemory(subjectId, showInactive),
        getEffectiveMemory(subjectId),
      ]);
      setItems((all as any)?.data || []);
      setShadowed((eff as any)?.data?.shadowed || []);
    } catch {
      message.error('加载学习记忆失败');
    } finally {
      setLoading(false);
    }
  }, [subjectId, showInactive]);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async (values: any) => {
    try {
      await createMemory({
        key: values.key, value: values.value,
        scope: subjectId || undefined, note: '学生手动添加',
      });
      message.success('已添加');
      values.key = ''; values.value = '';
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '添加失败');
    }
  };

  const handleCorrect = async (id: string) => {
    try {
      await correctMemory(id, editValue, '学生在记忆管理中更正');
      message.success('已更正（该条转为你的明确设置，长期有效）');
      setEditing(null);
      setEditValue('');
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '更正失败');
    }
  };

  const handleStatus = async (id: string, active: boolean) => {
    try {
      await setMemoryStatus(id, active ? 'active' : 'disabled',
        active ? '学生重新启用' : '学生关闭个性化');
      message.success(active ? '已启用' : '已关闭（不再影响教学）');
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '操作失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteMemory(id);
      message.success('已删除（变更历史保留）');
      load();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '删除失败');
    }
  };

  const showHistory = async (id: string) => {
    try {
      const res: any = await getMemoryHistory(id);
      setHistory({ id, rows: res?.data || [] });
    } catch {
      message.error('加载历史失败');
    }
  };

  return (
    <Card
      size="small"
      title="学习记忆（个性化依据）"
      extra={
        <Space>
          <Tooltip title="同时显示已关闭/已删除/已失效的记录">
            <Space size={4}>
              <Text type="secondary" style={{ fontSize: 12 }}>显示已停用</Text>
              <Switch size="small" checked={showInactive}
                      onChange={setShowInactive} />
            </Space>
          </Tooltip>
          <Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
          <Button size="small" onClick={async () => {
            if (!subjectId) { message.warning('请先选择学科'); return; }
            try {
              await refreshInference(subjectId);
              message.success('已根据最新学习记录刷新系统判断');
              load();
            } catch {
              message.error('刷新失败');
            }
          }}>
            按记录刷新判断
          </Button>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="三类信息分开保存，互不混淆"
        description={
          <div style={{ fontSize: 12 }}>
            <div>· <b>你的明确设置</b>：优先于系统推断，长期有效</div>
            <div>· <b>系统推断</b>：根据作答/任务记录推断，带依据与有效期；答对后自动撤销</div>
            <div>· <b>课程资料确认</b>：只有经确认的资料才进入；未经核验的回答、点赞与纠正不会写入</div>
          </div>
        }
      />

      <Form layout="inline" onFinish={handleCreate} style={{ marginBottom: 12 }}>
        <Form.Item name="key" rules={[{ required: true, message: '请填写名称' }]}>
          <Input placeholder="偏好名称，例如 explanation_style" style={{ width: 220 }} />
        </Form.Item>
        <Form.Item name="value" rules={[{ required: true, message: '请填写内容' }]}>
          <Input placeholder="内容，例如 多举生活例子" style={{ width: 220 }} />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" icon={<PlusOutlined />}>添加我的设置</Button>
        </Form.Item>
      </Form>

      {items.length === 0 ? (
        <Empty description="暂无学习记忆。系统会在有真实学习记录后给出带依据的暂时判断。" />
      ) : (
        <Table
          rowKey="id"
          size="small"
          dataSource={items}
          pagination={false}
          columns={[
            {
              title: '类型', dataIndex: 'memory_type', width: 140,
              render: (t: string) => {
                const meta = TYPE_META[t] || { color: 'default', label: t, hint: '' };
                return <Tooltip title={meta.hint}><Tag color={meta.color}>{meta.label}</Tag></Tooltip>;
              },
            },
            { title: '名称', dataIndex: 'key', width: 150 },
            {
              title: '内容',
              render: (_: any, r: MemoryItem) => (
                editing === r.id ? (
                  <Space>
                    <Input size="small" value={editValue} style={{ width: 200 }}
                           onChange={(e) => setEditValue(e.target.value)} />
                    <Button size="small" type="primary" onClick={() => handleCorrect(r.id)}>保存</Button>
                    <Button size="small" onClick={() => setEditing(null)}>取消</Button>
                  </Space>
                ) : <Text>{r.value}</Text>
              ),
            },
            {
              title: '依据 / 适用范围',
              render: (_: any, r: MemoryItem) => (
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {r.note || '—'}
                  </Text>
                  <div style={{ fontSize: 11, color: '#94a3b8' }}>
                    范围：{r.scope === 'global' ? '全部学科' : r.scope}
                    {r.expires_at ? `；有效期至 ${r.expires_at.slice(0, 10)}` : '；长期有效'}
                    {r.status !== 'active' ? `；状态：${r.status}` : ''}
                  </div>
                </div>
              ),
            },
            {
              title: '操作', width: 220,
              render: (_: any, r: MemoryItem) => (
                <Space size={4} wrap>
                  <Button size="small" icon={<EditOutlined />}
                          onClick={() => { setEditing(r.id); setEditValue(r.value); }}>更正</Button>
                  {r.status === 'active' ? (
                    <Button size="small" icon={<StopOutlined />}
                            onClick={() => handleStatus(r.id, false)}>关闭</Button>
                  ) : (
                    <Button size="small" icon={<PlayCircleOutlined />}
                            onClick={() => handleStatus(r.id, true)}>启用</Button>
                  )}
                  <Button size="small" icon={<HistoryOutlined />}
                          onClick={() => showHistory(r.id)}>历史</Button>
                  <Popconfirm title="确定删除这条记忆？" onConfirm={() => handleDelete(r.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      )}

      {shadowed.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            以下记录当前未参与教学（被你的明确设置覆盖或已被取代）：
          </Text>
          <List
            size="small"
            dataSource={shadowed}
            renderItem={(s) => (
              <List.Item style={{ padding: '2px 0', border: 'none' }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {s.key} = {s.value}（{TYPE_META[s.memory_type]?.label || s.memory_type}）
                </Text>
              </List.Item>
            )}
          />
        </div>
      )}

      {history && (
        <Card size="small" style={{ marginTop: 12 }} title="变更历史"
              extra={<Button size="small" onClick={() => setHistory(null)}>收起</Button>}>
          {history.rows.length === 0 ? <Empty description="暂无变更" /> : (
            <List
              size="small"
              dataSource={history.rows}
              renderItem={(h: any) => (
                <List.Item style={{ padding: '2px 0', border: 'none' }}>
                  <Text style={{ fontSize: 12 }}>
                    {h.created_at} · {h.action}
                    {h.old_value ? `：${h.old_value}` : ''}
                    {h.new_value ? ` → ${h.new_value}` : ''}
                    {h.reason ? `（${h.reason}）` : ''}
                  </Text>
                </List.Item>
              )}
            />
          )}
        </Card>
      )}
    </Card>
  );
};

export default LearningMemoryPanel;
