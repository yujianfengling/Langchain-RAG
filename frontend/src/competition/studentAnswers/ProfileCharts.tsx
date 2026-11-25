import React, { useMemo } from 'react';
import type { EChartsOption } from 'echarts';
import EChart from '../shared/EChart';
import type { ProfileIndicator } from './studentProfileData';

export interface TrendPoint { id: string; name: string; studentRate: number | null; classRate: number | null; sampleRate: number | null }

/** 历次测验得分率折线：本人 / 本班平均 / 两班样本平均。 */
export const ProfileTrendChart: React.FC<{ data: TrendPoint[]; showSample?: boolean; height?: number }> = ({ data, showSample = true, height = 240 }) => {
  const option = useMemo<EChartsOption>(() => {
    const series = [
      { name: '本人', key: 'studentRate' as const, color: '#2878b8', width: 3 },
      { name: '本班平均', key: 'classRate' as const, color: '#8a6be8', width: 2 },
      ...(showSample ? [{ name: '两班样本平均', key: 'sampleRate' as const, color: '#94a3b8', width: 2 }] : []),
    ].map((s) => ({
      name: s.name, type: 'line' as const, smooth: true, symbol: 'circle', symbolSize: 7,
      lineStyle: { width: s.width, color: s.color }, itemStyle: { color: s.color },
      data: data.map((d) => d[s.key] == null ? null : Number(d[s.key]!.toFixed(1))),
    }));
    return {
      tooltip: { trigger: 'axis', valueFormatter: (v) => v == null ? '无记录' : `${v}%` },
      legend: { bottom: 0, textStyle: { fontSize: 12 } },
      grid: { left: 44, right: 18, top: 20, bottom: 40 },
      xAxis: { type: 'category', data: data.map((d) => d.name), axisLabel: { fontSize: 12 }, boundaryGap: false },
      yAxis: { type: 'value', min: 0, max: 100, name: '得分率%', axisLabel: { formatter: '{value}%' } },
      series,
    };
  }, [data, showSample]);
  return <EChart option={option} height={height} ariaLabel="本人、本班平均与两班样本平均的历次测验得分率折线图" />;
};

const shortLabel = (name: string) => name.length > 12 ? name.slice(0, 11) + '…' : name;

/** 考点掌握雷达：仅纳入本次有有效已评分记录的考点，缺记录不画成零分。 */
export const ProfileRadarChart: React.FC<{ indicators: ProfileIndicator[]; height?: number }> = ({ indicators, height = 240 }) => {
  const observed = useMemo(() => indicators.filter((r) => r.totals.scoredCount > 0 && r.totals.fullScore > 0).slice(0, 8), [indicators]);
  const option = useMemo<EChartsOption>(() => ({
    tooltip: {},
    radar: {
      indicator: observed.map((r) => ({ name: shortLabel(r.name), max: 100 })),
      radius: '64%', center: ['50%', '50%'], axisName: { fontSize: 12, color: '#555' },
      splitArea: { areaStyle: { color: ['#fafafa', '#f0f2f5'] } },
    },
    series: [{ type: 'radar', data: [{ name: '本人得分率', value: observed.map((r) => Math.round(r.totals.score / r.totals.fullScore * 100)),
      lineStyle: { color: '#2878b8' }, itemStyle: { color: '#2878b8' }, areaStyle: { opacity: 0.18 } }] }],
  }), [observed]);
  if (!observed.length) return null;
  return <EChart option={option} height={height} ariaLabel="本人各考点得分率雷达图" />;
};