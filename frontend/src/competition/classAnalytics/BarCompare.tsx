import React from 'react';
import { Tooltip, Typography } from 'antd';

const { Text } = Typography;

/**
 * 两班考点分组横向条形图（讲评工作台主视觉）。
 * 视觉语法参考 carbon-charts「Bar (grouped)」：每个考点一组、每班一条、
 * 图例标班级、坐标从 0 起算、统一比例尺；交互参考 metabase drill-through：
 * 点柱条 → 当前上下文内的明细。本组件为项目内手搓实现（纯 div，无新依赖，未复制任何库代码）。
 *
 * 数据与明细表同源（listKnowledgePoints + getKpStat）；口径（全部有效作答/仅共同题）
 * 由父组件算好传入，图表、结论条、明细共用同一口径。
 */

/** 考点中文短名（编辑自官方《课程要求》指标全文；全文在 title/明细中保留） */
export const KP_SHORT_NAME: Record<string, string> = {
  T01I1: '列代数式',
  T01I2: '整式化简',
  T01I3: '整式实际应用',
  T02I1: '解一元一次方程',
  T02I2: '解二元一次方程组',
  T02I3: '方程实际应用',
  T03I1: '解一元一次不等式',
  T03I2: '解不等式组',
  T03I3: '不等式实际应用',
};

export interface BarClassCell {
  rate: number | null;
  status: string;
  validAttempts: number;
  score: number;
  maxScore: number;
  /** 仅共同题口径的汇总；无共同题为 null（rows 显示「无法同题比较」） */
  common: { rate: number; score: number; full: number; events: number; items: number } | null;
}

export interface BarRow {
  id: string;
  indicatorId: string;
  name: string;
  cells: Record<string, BarClassCell | undefined>;
}

const pct = (r: number | null | undefined) => `${Math.round((r ?? 0) * 100)}%`;

const BarCompare: React.FC<{
  rows: BarRow[];
  lectureClassId: string;
  compareClassId?: string;
  lectureClassName: string;
  compareClassName?: string;
  scope: 'all' | 'common';
  selected: { kpId: string; classId: string } | null;
  onSelect: (indicatorId: string, classId: string) => void;
  onSelectKp: (indicatorId: string) => void;
}> = ({ rows, lectureClassId, compareClassId, lectureClassName, compareClassName, scope, selected, onSelect, onSelectKp }) => {
  const cellOf = (row: BarRow, classId: string): BarClassCell | undefined => row.cells[classId];
  const valueOf = (row: BarRow, classId: string): { label: string; rate: number | null; muted: boolean; title: string } => {
    const c = cellOf(row, classId);
    if (!c) return { label: '无数据', rate: null, muted: true, title: '该班此考点无统计数据' };
    if (scope === 'common') {
      if (!c.common) return { label: '无法同题比较', rate: null, muted: true, title: '两班没有共同的有效作答题目' };
      if (c.status === '记录不足' || c.status === '本次未测') return { label: c.status, rate: null, muted: true, title: `${c.status}；共同题 ${c.common.events} 条有效作答，不按零分处理` };
      return { label: pct(c.common.rate), rate: c.common.rate, muted: false, title: `共同题得分率 ${pct(c.common.rate)} = ${c.common.score}÷${c.common.full}（${c.common.events} 条有效 · ${c.common.items} 道共同题）` };
    }
    if (c.status === '本次未测') return { label: '本次未测', rate: null, muted: true, title: '本次测验未考此考点' };
    if (c.status === '记录不足') return { label: `记录不足（${c.validAttempts} 条）`, rate: null, muted: true, title: `仅 ${c.validAttempts} 条有效作答，不足以判定` };
    if (c.status === '真实零分') return { label: '0%', rate: 0, muted: false, title: `${c.validAttempts} 条有效作答，得分率确为 0` };
    if (c.rate == null) return { label: '无有效得分率', rate: null, muted: true, title: '没有可用于得分率计算的有效记录' };
    return { label: pct(c.rate), rate: c.rate, muted: false, title: `得分率 ${pct(c.rate)} = ${c.score}÷${c.maxScore}（${c.validAttempts} 条有效作答）` };
  };

  return (
    <div className="bc-wrap" data-testid="bar-compare">
      <div className="bc-legend">
        <span className="bc-legend-item"><i className="bc-swatch is-lecture" />{lectureClassName}（讲评班）</span>
        {compareClassId && <span className="bc-legend-item"><i className="bc-swatch is-compare" />{compareClassName}（对照班）</span>}
        {scope === 'common' && <span className="bc-legend-note">口径：仅比较两班共同题目</span>}
      </div>
      {rows.map((row) => {
        const short = KP_SHORT_NAME[row.indicatorId] || row.name.replace(/^T\d+I\d+ · /, '');
        const isSelectedKp = selected?.kpId === row.id;
        return (
          <div key={row.id} className={'bc-row' + (isSelectedKp ? ' is-selected' : '')}>
            <Tooltip title={row.name}>
              <button type="button" className="bc-kpname"
                      aria-label={`查看 ${short} 的失分依据`}
                      onClick={() => onSelectKp(row.indicatorId)}>
                {short}
              </button>
            </Tooltip>
            <div className="bc-bars">
              {[{ id: lectureClassId, name: lectureClassName, kind: 'lecture' },
                ...(compareClassId ? [{ id: compareClassId, name: compareClassName || compareClassId, kind: 'compare' }] : [])].map(({ id, name, kind }) => {
                const v = valueOf(row, id);
                const isSel = selected?.kpId === row.id && selected?.classId === id;
                return (
                  <button key={id} type="button"
                          data-kp={row.indicatorId} data-class={id}
                          className={'bc-bar' + (kind === 'lecture' ? ' is-lecture' : ' is-compare')
                            + (v.muted ? ' is-muted' : '') + (isSel ? ' is-active' : '')}
                          aria-label={`${name} ${short}：${v.label}，查看失分依据`}
                          title={v.title}
                          onClick={() => onSelect(row.indicatorId, id)}>
                    <span className="bc-track">
                      {!v.muted && v.rate != null && (
                        <span className={'bc-fill' + (kind === 'lecture' ? ' is-lecture' : ' is-compare')}
                              style={{ width: `${Math.max(0, Math.min(100, v.rate * 100))}%` }} />
                      )}
                    </span>
                    <span className="bc-val">{v.label}</span>
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
      {!rows.length && <Text type="secondary">该测验暂无考点数据</Text>}
    </div>
  );
};

export default BarCompare;
