import React, { useEffect, useRef, useState, useCallback } from 'react';
import * as echarts from 'echarts';
import { Button, Card, Empty, Spin, Tag, Space, Typography } from 'antd';
import { ApiOutlined, ReloadOutlined } from '@ant-design/icons';
import { getKnowledgeGraph } from '../../api/knowledge';

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
  const [loading, setLoading] = useState(true);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

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

  useEffect(() => {
    loadGraph();
  }, [loadGraph]);

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
        <Button size="small" icon={<ReloadOutlined />} onClick={loadGraph} loading={loading}>
          刷新
        </Button>
      }
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin />
        </div>
      ) : !graph?.nodes?.length ? (
        <Empty
          description="暂无知识图谱，构建知识库时将自动生成"
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
