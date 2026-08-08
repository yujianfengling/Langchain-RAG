import React, { useEffect, useRef, useState, useCallback } from 'react';
import * as echarts from 'echarts';
import { Button, Card, Empty, Popconfirm, Spin, Tag, Space, Typography, message } from 'antd';
import { ApiOutlined, ReloadOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { SkeletonCard } from '../AnimateMotion';
import { getKnowledgeGraph, buildKnowledgeGraph, getKnowledgeGraphStatus } from '../../api/knowledge';

const { Text } = Typography;

interface GraphNode {
  id: string;
  label: string;
  type?: string;
  weight?: number;
  content?: string;
  source?: string;
}

interface GraphEdge {
  source: string;
  target: string;
  relation: string;
  weight?: number;
}

interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats?: {
    node_count: number;
    edge_count: number;
    created_at?: string;
  };
}

// 节点类型颜色映射（CiteSpace风格）
const TYPE_COLORS: Record<string, string> = {
  '概念': '#5470c6',
  '公式': '#91cc75',
  '定理': '#fac858',
  '例题': '#ee6666',
  '方法': '#73c0de',
  '人物': '#3ba272',
  '其他': '#999999',
};

const KnowledgeGraph: React.FC<{ subjectId: string }> = ({ subjectId }) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState(false);
  const [progress, setProgress] = useState<{ current: number; total: number } | null>(null);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const loadGraph = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getKnowledgeGraph(subjectId);
      // 兼容 axios 拦截器解包后的格式
      const data = res?.data ?? res;
      if (data?.nodes) {
        setGraph(data);
      } else {
        setGraph(null);
      }
    } catch {
      setGraph(null);
    } finally {
      setLoading(false);
    }
  }, [subjectId]);

  // 轮询图谱构建状态
  const pollBuildStatus = useCallback(async () => {
    try {
      const res: any = await getKnowledgeGraphStatus(subjectId);
      const data = res?.data ?? res;
      const status = data?.status;
      if (status === 'success') {
        stopPolling();
        setBuilding(false);
        setProgress(null);
        message.success('知识图谱生成完成');
        loadGraph();
      } else if (status === 'failed') {
        stopPolling();
        setBuilding(false);
        setProgress(null);
        message.error(data?.message || '知识图谱生成失败，请重试');
      } else {
        // queued / building：展示进度（不显示假百分比）
        const current = data?.processed_chunks;
        const total = data?.total_chunks;
        if (typeof current === 'number' && typeof total === 'number') {
          setProgress({ current, total });
        }
        pollTimer.current = setTimeout(pollBuildStatus, 2500);
      }
    } catch {
      stopPolling();
      setBuilding(false);
      setProgress(null);
      message.error('查询图谱生成状态失败，请刷新后重试');
    }
  }, [subjectId, loadGraph, stopPolling]);

  // 提交图谱构建任务
  const handleBuild = async () => {
    setBuilding(true);
    setProgress(null);
    try {
      const res: any = await buildKnowledgeGraph(subjectId);
      const data = res?.data ?? res;
      const status = data?.status;
      if (status === 'queued' || status === 'building') {
        message.info('知识图谱生成任务已提交，正在后台生成...');
        pollTimer.current = setTimeout(pollBuildStatus, 2500);
      } else if (status === 'success') {
        setBuilding(false);
        message.success('知识图谱生成完成');
        loadGraph();
      } else if (status === 'failed') {
        setBuilding(false);
        message.error(data?.message || '知识图谱生成失败');
      } else {
        // 409 已构建中 / busy 等
        setBuilding(false);
        message.warning(data?.message || '任务未能提交，请稍后再试');
      }
    } catch (error: any) {
      setBuilding(false);
      const detail = error?.response?.data?.message
        || error?.response?.data?.detail
        || '提交知识图谱生成任务失败';
      // 409 = 已有任务进行中，提示即可
      if (error?.response?.status === 409) {
        message.warning(detail);
      } else {
        message.error(detail);
      }
    }
  };

  useEffect(() => {
    loadGraph();
    return () => stopPolling();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadGraph, stopPolling]);

  // 渲染图谱
  useEffect(() => {
    if (!chartRef.current || !graph?.nodes?.length) return;

    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current);
    }

    const maxWeight = Math.max(...graph.nodes.map(n => n.weight || 1));
    const minWeight = Math.min(...graph.nodes.map(n => n.weight || 1));

    const option: echarts.EChartsOption = {
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          if (params.dataType === 'node') {
            const n = params.data;
            return `<b>${n.label}</b><br/>类型: ${n.type || '概念'}<br/>权重: ${(n.weight || 1).toFixed(1)}`;
          }
          if (params.dataType === 'edge') {
            const e = params.data;
            return `${e.source} —[${e.relation}]→ ${e.target}`;
          }
          return '';
        },
      },
      animationDuration: 1500,
      animationEasingUpdate: 'quinticInOut' as any,
      series: [
        {
          type: 'graph' as const,
          layout: 'force' as const,
          roam: true,
          draggable: true,
          data: graph.nodes.map(n => ({
            id: n.id,
            name: n.label,
            symbolSize: 20 + ((n.weight || 1) - minWeight) / (maxWeight - minWeight || 1) * 30,
            itemStyle: {
              color: TYPE_COLORS[n.type || '其他'] || TYPE_COLORS['其他'],
            },
            label: { show: true, fontSize: 11 },
            value: n,
          })) as any,
          links: graph.edges.map(e => ({
            source: e.source,
            target: e.target,
            label: { show: true, formatter: e.relation, fontSize: 9, color: '#888' },
            lineStyle: { width: (e.weight || 1) * 1.5, curveness: 0.1 },
          })) as any,
          label: {
            show: true,
            position: 'bottom',
            fontSize: 10,
          },
          force: {
            repulsion: 150,
            edgeLength: 120,
          },
          emphasis: {
            focus: 'adjacency' as const,
            lineStyle: { width: 4 },
          },
        },
      ],
    };

    chartInstance.current.setOption(option, true);

    // 点击节点显示详情
    chartInstance.current.on('click', (params: any) => {
      if (params.dataType === 'node') {
        setSelectedNode(params.data.value);
      }
    });

    return () => {
      chartInstance.current?.dispose();
      chartInstance.current = null;
    };
  }, [graph]);

  // 窗口resize适配
  useEffect(() => {
    const handleResize = () => chartInstance.current?.resize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return (
    <Card
      size="small"
      title={
        <Space>
          <ApiOutlined />
          知识图谱
          {graph?.stats && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {graph.stats.node_count}节点 / {graph.stats.edge_count}边
            </Text>
          )}
        </Space>
      }
      extra={
        <Space>
          {building ? (
            <Button size="small" disabled icon={<Spin size="small" />}>
              {progress && progress.total > 0
                ? `正在抽取知识关系：${progress.current} / ${progress.total}`
                : '知识图谱生成中...'}
            </Button>
          ) : graph?.nodes?.length ? (
            <Popconfirm
              title="生成知识图谱需要调用大模型并可能产生API费用，是否继续？"
              onConfirm={handleBuild}
              okText="生成"
              cancelText="取消"
            >
              <Button size="small" icon={<PlayCircleOutlined />}>
                重新生成
              </Button>
            </Popconfirm>
          ) : (
            <Popconfirm
              title="生成知识图谱需要调用大模型并可能产生API费用，是否继续？"
              onConfirm={handleBuild}
              okText="生成"
              cancelText="取消"
            >
              <Button size="small" type="primary" icon={<PlayCircleOutlined />}>
                生成知识图谱
              </Button>
            </Popconfirm>
          )}
          <Button size="small" icon={<ReloadOutlined />} onClick={loadGraph} loading={loading}>
            刷新
          </Button>
        </Space>
      }
    >
      {loading ? (
        <div style={{ padding: 24 }}>
          <SkeletonCard height={40} />
          <div style={{ height: 12 }} />
          <SkeletonCard height={280} />
          <div style={{ height: 12 }} />
          <SkeletonCard height={30} width="60%" />
        </div>
      ) : !graph?.nodes?.length ? (
        <Empty
          description={
            <div>
              <div>暂无知识图谱</div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                点击右上角"生成知识图谱"手动生成（需先构建知识库）
              </Text>
            </div>
          }
          style={{ padding: 40 }}
        />
      ) : (
        <>
          <div
            ref={chartRef}
            style={{ width: '100%', height: 480, borderRadius: 8 }}
          />
          <div style={{ marginTop: 12 }}>
            <Space size={4} wrap>
              {Object.entries(TYPE_COLORS).filter(([k]) => graph.nodes.some(n => (n.type || '其他') === k)).map(([type, color]) => (
                <Tag key={type} color={color}>{type}</Tag>
              ))}
            </Space>
          </div>
          {selectedNode && (
            <Card size="small" style={{ marginTop: 12, background: '#fafafa' }}>
              <Text strong>{selectedNode.label}</Text>
              <div style={{ marginTop: 8, fontSize: 12, color: '#666' }}>
                <div>类型：{selectedNode.type || '概念'}</div>
                {selectedNode.source && <div>来源：{selectedNode.source}</div>}
                {selectedNode.content && <div>内容：{selectedNode.content}</div>}
              </div>
            </Card>
          )}
        </>
      )}
    </Card>
  );
};

export default KnowledgeGraph;
