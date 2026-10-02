import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { getReadyStatus } from '../../api/health';

const { Text } = Typography;

const CAP_LABEL: Record<string, string> = {
  llm: '文本模型',
  web_search: '联网搜题',
  vision: '图片识别',
  speech: '语音服务',
};

const RETRIEVAL_META: Record<string, { color: string; text: string }> = {
  hybrid_ready: { color: 'success', text: '混合检索就绪（向量 + 关键词）' },
  vector_only: { color: 'warning', text: '仅向量检索（BM25 索引缺失，建议重建知识库）' },
  bm25_degraded: { color: 'warning', text: '仅关键词检索（向量检索不可用）' },
  not_loaded: { color: 'default', text: '尚未加载（首次提问时加载 embedding 模型）' },
  loading: { color: 'processing', text: '加载中' },
  unavailable: { color: 'error', text: '检索不可用' },
};

/**
 * 系统状态面板（2026-10-02）
 *
 * 把 /health/ready 的"运行态外部能力"与"当日用量"摆到界面上。
 * 此前这些信息只在降级或超预算时以横幅形式冒出来，平时看不见——
 * 而"今天用了多少、联网还通不通"恰恰是排查问题的第一手信息。
 *
 * 注意与"配置态"的区别：这里反映的是**最近一次真实调用**的结果，
 * ok=null 表示本次进程还没调用过（不等于不可用）。
 */
const SystemStatusPanel: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [ready, setReady] = useState<any>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setFailed(false);
    try {
      setReady(await getReadyStatus());
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const caps = ready?.external_capabilities || {};
  const usage = ready?.usage_today;
  const retrieval = RETRIEVAL_META[ready?.retrieval_mode] || null;

  const rows = Object.keys(CAP_LABEL).map((key) => {
    const cap = caps[key];
    const u = usage?.kinds?.[key];
    const ok: boolean | null = cap?.ok ?? null;
    return {
      key,
      label: CAP_LABEL[key],
      ok,
      detail: ok === false
        ? (cap?.last_error || cap?.reason || '原因未知')
        : (ok === true ? '最近一次调用正常' : '本次进程尚未调用'),
      used: u?.used ?? 0,
      limit: u?.limit ?? 0,
      over: !!u?.over,
      failures: cap?.fail_count ?? 0,
    };
  });

  const degraded = rows.filter((r) => r.ok === false);

  return (
    <Card
      size="small"
      title="运行状态"
      extra={
        <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>
          刷新
        </Button>
      }
    >
      {failed ? (
        <Alert type="error" showIcon message="读取系统状态失败（后端可能未启动）" />
      ) : (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space size={8} wrap>
            <Text type="secondary">知识库检索</Text>
            {retrieval
              ? <Tag color={retrieval.color}>{retrieval.text}</Tag>
              : <Tag>{ready?.retrieval_mode || '未知'}</Tag>}
          </Space>

          {degraded.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message={degraded.length + ' 项能力正在降级'}
              description={
                <Space direction="vertical" size={2}>
                  {degraded.map((d) => (
                    <Text key={d.key} style={{ fontSize: 12 }}>
                      {d.label}：{d.detail}
                    </Text>
                  ))}
                </Space>
              }
            />
          )}

          <Table
            size="small"
            rowKey="key"
            pagination={false}
            dataSource={rows}
            columns={[
              { title: '能力', dataIndex: 'label', width: 100 },
              {
                title: '状态',
                width: 90,
                render: (_: any, r: any) => (
                  r.ok === null
                    ? <Tooltip title="本次进程还没调用过，不代表不可用"><Tag>未使用</Tag></Tooltip>
                    : r.ok ? <Tag color="success">正常</Tag> : <Tag color="error">降级</Tag>
                ),
              },
              {
                title: '今日用量',
                width: 140,
                render: (_: any, r: any) => (
                  r.limit > 0
                    ? <Text type={r.over ? 'danger' : undefined}>{r.used} / {r.limit}{r.over ? '（已超限）' : ''}</Text>
                    : <Text type="secondary">{r.used} / 不限</Text>
                ),
              },
              {
                title: '说明',
                render: (_: any, r: any) => (
                  <Text type={r.ok === false ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
                    {r.detail}{r.failures > 0 ? '（累计失败 ' + r.failures + ' 次）' : ''}
                  </Text>
                ),
              },
            ]}
          />

          <Text type="secondary" style={{ fontSize: 12 }}>
            用量只统计成功的外部调用；超出预算时后台题库预生成会自动暂停，学生按需出题不受影响。
          </Text>
        </Space>
      )}
    </Card>
  );
};

export default SystemStatusPanel;
