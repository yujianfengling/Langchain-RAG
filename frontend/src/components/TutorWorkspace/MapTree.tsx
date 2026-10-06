import React, { useMemo } from 'react';
import { Button, Empty, Tooltip, Typography } from 'antd';
import { AimOutlined, ReloadOutlined, PartitionOutlined } from '@ant-design/icons';

const { Text } = Typography;

/**
 * 学习路线（知识地图）树。
 *
 * 与"检索知识图谱"是两个东西：这里是章节 → 知识点的学习路线，节点有稳定 ID 与学习状态；
 * 检索知识图谱在资料页用于关系分析，不在这里混用。
 */
export interface MapNode {
  node_id: string;
  label: string;
  level: string;
  parent_id?: string | null;
  status: string;
  status_label?: string;
  source_docs?: string[];
  prerequisites?: string[];
}

const STATUS_DOT: Record<string, string> = {
  new: '#cbd5e1',
  learning: '#0ea5e9',
  practicing: '#6366f1',
  passed: '#059669',
  needs_review: '#d97706',
};

const MapTree: React.FC<{
  nodes: MapNode[];
  currentNodeId?: string | null;
  nextNode?: { node_id: string; label: string; reason: string } | null;
  generating?: boolean;
  onSelect: (nodeId: string) => void;
  onGenerate: () => void;
  statusLabels?: Record<string, string>;
}> = ({ nodes, currentNodeId, nextNode, generating, onSelect, onGenerate, statusLabels }) => {
  const childrenOf = useMemo(() => {
    const map = new Map<string | null, MapNode[]>();
    for (const n of nodes) {
      const key = n.parent_id || null;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(n);
    }
    return map;
  }, [nodes]);

  const renderLevel = (parentId: string | null, depth: number): React.ReactNode => {
    const items = childrenOf.get(parentId) || [];
    if (!items.length) return null;
    return (
      <ul className="tutor-map-level" style={{ paddingLeft: depth === 0 ? 0 : 14 }}>
        {items.map((node) => {
          const active = node.node_id === currentNodeId;
          const isNext = nextNode?.node_id === node.node_id;
          return (
            <li key={node.node_id}>
              <button
                type="button"
                className={'tutor-map-node' + (active ? ' is-active' : '') + (isNext ? ' is-next' : '')}
                onClick={() => onSelect(node.node_id)}
                title={(node.source_docs || []).join('、') || node.label}
              >
                <span className="tutor-map-dot" style={{ background: STATUS_DOT[node.status] || '#cbd5e1' }} />
                <span className="tutor-map-label">{node.label}</span>
                <span className="tutor-map-status">
                  {statusLabels?.[node.status] || node.status_label || ''}
                </span>
              </button>
              {renderLevel(node.node_id, depth + 1)}
            </li>
          );
        })}
      </ul>
    );
  };

  if (!nodes.length) {
    return (
      <div className="tutor-panel-empty">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={<span>还没有学习路线<br /><Text type="secondary" style={{ fontSize: 12 }}>基于当前资料生成，约需十几秒</Text></span>}
        />
        <Button type="primary" icon={<PartitionOutlined />} loading={generating} onClick={onGenerate}>
          生成学习路线
        </Button>
      </div>
    );
  }

  return (
    <div className="tutor-map">
      {nextNode && (
        <div className="tutor-map-next">
          <AimOutlined style={{ color: 'var(--color-primary)' }} />
          <div>
            <div style={{ fontSize: 12, fontWeight: 600 }}>建议下一步：{nextNode.label}</div>
            <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{nextNode.reason}</div>
          </div>
        </div>
      )}
      <div className="tutor-map-scroll">{renderLevel(null, 0)}</div>
      <div className="tutor-map-footer">
        <Tooltip title="资料变化后可重新生成；能匹配上的节点会保留学习记录">
          <Button size="small" type="text" icon={<ReloadOutlined />} loading={generating}
                  onClick={onGenerate}>重新生成</Button>
        </Tooltip>
        <span className="tutor-map-legend">
          {(Object.keys(STATUS_DOT) as string[]).map((k) => (
            <span key={k} className="tutor-legend-item">
              <i style={{ background: STATUS_DOT[k] }} />{statusLabels?.[k] || k}
            </span>
          ))}
        </span>
      </div>
    </div>
  );
};

export default MapTree;
