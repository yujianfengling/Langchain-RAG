import React, { useState } from 'react';
import { Button, Empty, Popconfirm, Tag, Tooltip, Typography, message } from 'antd';
import { DeleteOutlined, EditOutlined, FileTextOutlined, SaveOutlined } from '@ant-design/icons';

const { Text } = Typography;

/**
 * 右侧面板：引用原文 / 学习成果 / 执行详情。
 *
 * 明确区分三件事（不能混为一谈）：
 *   检索到片段  → sources 列表
 *   回答标注了引用 → citedIds 高亮
 *   引用能支持结论 → 由学生点开原文自行核对，界面不替学生下结论
 */
const SourcePanel: React.FC<{
  sources: any[];
  citedIds: number[];
  artifacts: any[];
  trace: any[];
  stages: string[];
  onOpenArtifact?: (artifact: any) => void;
  onDeleteArtifact?: (artifact: any) => void;
  onUpdateArtifact?: (artifact: any, content: any) => void;
  focusSourceId?: number | null;
}> = ({ sources, citedIds, artifacts, trace, stages, onOpenArtifact, onDeleteArtifact,
        onUpdateArtifact, focusSourceId }) => {
  const [tab, setTab] = useState<'sources' | 'artifacts' | 'trace'>('sources');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  const tabs: { key: typeof tab; label: string; count?: number }[] = [
    { key: 'sources', label: '引用原文', count: sources.length },
    { key: 'artifacts', label: '学习成果', count: artifacts.length },
    { key: 'trace', label: '执行详情', count: trace.length },
  ];

  return (
    <div className="tutor-side">
      <div className="tutor-side-tabs">
        {tabs.map((t) => (
          <button key={t.key} type="button"
                  className={'tutor-side-tab' + (tab === t.key ? ' is-active' : '')}
                  onClick={() => setTab(t.key)}>
            {t.label}
            {t.count ? <span className="tutor-side-count">{t.count}</span> : null}
          </button>
        ))}
      </div>

      <div className="tutor-side-body">
        {tab === 'sources' && (
          sources.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                   description={<Text type="secondary" style={{ fontSize: 12 }}>这一轮还没有检索到资料片段</Text>} />
          ) : (
            <div className="tutor-source-list">
              <Text type="secondary" style={{ fontSize: 12 }}>
                共 {sources.length} 条片段，其中 {citedIds.length} 条被回答引用
              </Text>
              {sources.map((s) => {
                const cited = citedIds.includes(s.id);
                return (
                  <div key={s.id + '-' + (s.file_name || '')}
                       className={'tutor-source-item' + (cited ? ' is-cited' : '')
                         + (focusSourceId === s.id ? ' is-focus' : '')}>
                    <div className="tutor-source-head">
                      <Tag color={cited ? 'green' : 'default'}>[{s.id}]</Tag>
                      <Tooltip title={s.file_name}>
                        <span className="tutor-source-name">
                          <FileTextOutlined /> {s.file_name}
                        </span>
                      </Tooltip>
                      {s.page != null
                        ? <Tag>第 {s.page} 页</Tag>
                        : <Tag color="default">片段</Tag>}
                    </div>
                    {s.section && <div className="tutor-source-section">{s.section}</div>}
                    <div className="tutor-source-snippet">{s.snippet || '（无原文片段）'}</div>
                    {!cited && (
                      <div className="tutor-source-hint">仅检索到，未被本次回答引用</div>
                    )}
                  </div>
                );
              })}
            </div>
          )
        )}

        {tab === 'artifacts' && (
          artifacts.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                   description={<Text type="secondary" style={{ fontSize: 12 }}>还没有保存的成果，可以让助手生成笔记或提纲</Text>} />
          ) : (
            <div className="tutor-artifact-list">
              {artifacts.map((a) => (
                <div className="tutor-artifact-item" key={a.id}>
                  <div className="tutor-artifact-head">
                    <Tag color="teal">{a.kind_label || a.kind}</Tag>
                    <span className="tutor-artifact-title" title={a.title}>{a.title}</span>
                  </div>
                  <div className="tutor-artifact-meta">
                    {a.updated_at?.replace('T', ' ')}
                    {a.edited ? ' · 已编辑' : ''}
                    {a.stale ? ' · 资料已变化，建议更新' : ''}
                  </div>
                  <div className="tutor-artifact-actions">
                    <Button size="small" type="link" icon={<EditOutlined />}
                            onClick={() => {
                              onOpenArtifact?.(a);
                              setEditingId(a.id);
                              setDraft(JSON.stringify(a.content || {}, null, 2));
                            }}>打开</Button>
                    <Popconfirm title="删除这个成果？" onConfirm={() => onDeleteArtifact?.(a)}>
                      <Button size="small" type="link" danger icon={<DeleteOutlined />}>删除</Button>
                    </Popconfirm>
                  </div>
                  {editingId === a.id && (
                    <div className="tutor-artifact-editor">
                      <textarea value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false} />
                      <div style={{ marginTop: 6 }}>
                        <Button size="small" type="primary" icon={<SaveOutlined />}
                                onClick={() => {
                                  try {
                                    onUpdateArtifact?.(a, JSON.parse(draft));
                                    setEditingId(null);
                                  } catch {
                                    message.error('内容不是合法 JSON');
                                  }
                                }}>保存修改</Button>
                        <Button size="small" type="text" onClick={() => setEditingId(null)}>取消</Button>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )
        )}

        {tab === 'trace' && (
          <div className="tutor-trace">
            {stages.length === 0 && trace.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                     description={<Text type="secondary" style={{ fontSize: 12 }}>本轮还没有工具调用</Text>} />
            ) : (
              <>
                {trace.map((t, i) => (
                  <div className="tutor-trace-item" key={i}>
                    <span className={'tutor-trace-dot' + (t.ok ? '' : ' is-error')} />
                    <div>
                      <div className="tutor-trace-label">{t.label || t.tool}</div>
                      <div className="tutor-trace-summary">{t.summary}</div>
                    </div>
                  </div>
                ))}
                <div className="tutor-trace-note">
                  以上是本轮**真实执行**的步骤；没有调用到的工具不会出现在这里。
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default SourcePanel;
