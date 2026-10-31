import React, { useEffect, useMemo, useState } from 'react';
import { Button, Empty, Tooltip, Typography } from 'antd';
import { AimOutlined, DownOutlined, ReloadOutlined, PartitionOutlined } from '@ant-design/icons';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';

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

/**
 * 状态中文兜底（2026-10-07）。
 * 后端会下发 status_labels，但缺失时界面曾直接显示 new / learning / practicing /
 * passed / needs_review 这些英文键（截图里可见）。这里给中文兜底，
 * 并保持真实业务口径：一次"检测通过"不代表永久掌握。
 */
const STATUS_FALLBACK: Record<string, string> = {
  new: '未开始',
  learning: '学习中',
  practicing: '待检测',
  passed: '检测通过',
  needs_review: '待复习',
};

const statusText = (key: string, labels?: Record<string, string>, own?: string): string =>
  labels?.[key] || own || STATUS_FALLBACK[key] || key;

const MapTree: React.FC<{
  nodes: MapNode[];
  currentNodeId?: string | null;
  nextNode?: { node_id: string; label: string; reason: string } | null;
  generating?: boolean;
  onSelect: (nodeId: string) => void;
  onGenerate: () => void;
  statusLabels?: Record<string, string>;
}> = ({ nodes, currentNodeId, nextNode, generating, onSelect, onGenerate, statusLabels }) => {
  const reduceMotion = useReducedMotion();
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const childrenOf = useMemo(() => {
    const map = new Map<string | null, MapNode[]>();
    for (const n of nodes) {
      const key = n.parent_id || null;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(n);
    }
    return map;
  }, [nodes]);

  // 明确选中节点时只展开它的祖先，不主动滚动目录，也不改变用户已展开的其它章节。
  useEffect(() => {
    if (!currentNodeId) return;
    const byId = new Map(nodes.map((node) => [node.node_id, node]));
    const ancestors = new Set<string>();
    let parent = byId.get(currentNodeId)?.parent_id;
    while (parent) {
      ancestors.add(parent);
      parent = byId.get(parent)?.parent_id;
    }
    setCollapsed((prev) => {
      if (![...ancestors].some((id) => prev.has(id))) return prev;
      const next = new Set(prev);
      ancestors.forEach((id) => next.delete(id));
      return next;
    });
  }, [currentNodeId, nodes]);

  const renderLevel = (parentId: string | null, depth: number): React.ReactNode => {
    const items = childrenOf.get(parentId) || [];
    if (!items.length) return null;
    return items.map((node) => {
      const active = node.node_id === currentNodeId;
      const isNext = nextNode?.node_id === node.node_id;
      const hasChildren = Boolean(childrenOf.get(node.node_id)?.length);
      const isCollapsed = collapsed.has(node.node_id);
      return (
        <li key={node.node_id}>
          <div className="tutor-map-row">
            {hasChildren ? (
              <button
                type="button"
                className={'tutor-map-toggle' + (isCollapsed ? ' is-collapsed' : '')}
                aria-label={(isCollapsed ? '展开 ' : '收起 ') + node.label}
                aria-expanded={!isCollapsed}
                onClick={() => setCollapsed((prev) => {
                  const next = new Set(prev);
                  if (next.has(node.node_id)) next.delete(node.node_id); else next.add(node.node_id);
                  return next;
                })}
              ><DownOutlined /></button>
            ) : <span className="tutor-map-toggle-spacer" />}
            <button
              type="button"
              className={'tutor-map-node' + (active ? ' is-active' : '') + (isNext ? ' is-next' : '')}
              data-node-id={node.node_id}
              onClick={() => onSelect(node.node_id)}
              title={(node.source_docs || []).join('、') || node.label}
            >
              <span className="tutor-map-dot" style={{ background: STATUS_DOT[node.status] || '#cbd5e1' }} />
              <span className="tutor-map-label">{node.label}</span>
              {/* 2026-10-31 减法：圆点已表达状态色；"未开始"逐节点重复没有信息量，
                  只有进行中/待复习等**偏离默认**的状态才显示文字。悬停圆点仍有完整说明。 */}
              {node.status && node.status !== 'new' && (
                <span className={'tutor-map-status' + (node.status === 'needs_review' ? ' is-attention' : '')}>
                  {statusText(node.status, statusLabels, node.status_label)}
                </span>
              )}
            </button>
          </div>
          {hasChildren && (
            <AnimatePresence initial={false}>
              {!isCollapsed && (
                <motion.ul
                  className="tutor-map-level tutor-map-children"
                  style={{ paddingLeft: depth === 0 ? 12 : 14 }}
                  initial={reduceMotion ? { opacity: 1 } : { height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={reduceMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
                  transition={{ duration: reduceMotion ? 0 : 0.18, ease: [0.2, 0, 0, 1] }}
                >{renderLevel(node.node_id, depth + 1)}</motion.ul>
              )}
            </AnimatePresence>
          )}
        </li>
      );
    });
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
      {/* 下一步建议压成单行：此前是一张带说明的卡片，反复占掉路线高度。 */}
      {nextNode && (
        <Tooltip title={nextNode.reason}>
          <div className="tutor-map-next">
            <AimOutlined style={{ color: 'var(--color-primary)' }} />
            <span className="tutor-map-next-label">建议下一步：{nextNode.label}</span>
          </div>
        </Tooltip>
      )}
      <div className="tutor-map-scroll"><ul className="tutor-map-level">{renderLevel(null, 0)}</ul></div>
      <div className="tutor-map-footer">
        {/* 2026-10-31 减法：常驻状态图例移除——圆点颜色 + 悬停 tooltip 足够，
            底部一行图例在每次打开路线时都占据视觉焦点。 */}
        <Tooltip title="路线状态：● 未开始 / 学习中 / 待检测 / 检测通过 / ● 待复习（橙色）。资料变化后可重新生成；能匹配上的节点会保留学习记录">
          <Button size="small" type="text" icon={<ReloadOutlined />} loading={generating}
                  onClick={onGenerate}>重新生成</Button>
        </Tooltip>
      </div>
    </div>
  );
};

export default MapTree;
// 2026-10-07 布局轮：状态标签中文兜底（触发 Vite 重新编译）
