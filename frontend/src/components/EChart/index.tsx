import React, { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

export interface EChartProps {
  option: echarts.EChartsOption;
  height?: number | string;
  className?: string;
  ariaLabel?: string;
  renderer?: 'svg' | 'canvas';
  onClick?: (params: echarts.ECElementEvent) => void;
}

/**
 * 轻量 ECharts 封装：自适应容器尺寸（ResizeObserver）、高分屏清晰渲染、卸载自动销毁。
 * 供教师端学情分析、学生端学情反馈复用。
 */
const EChart: React.FC<EChartProps> = ({
  option,
  height = 260,
  className,
  ariaLabel,
  renderer = 'svg',
  onClick,
}) => {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const onClickRef = useRef(onClick);
  onClickRef.current = onClick;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = echarts.init(el, undefined, {
      renderer,
      devicePixelRatio: Math.max(2, window.devicePixelRatio || 1),
    });
    chartRef.current = chart;
    const handler = (params: echarts.ECElementEvent) => onClickRef.current?.(params);
    chart.on('click', handler);
    const observer = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => chart.resize())
      : null;
    observer?.observe(el);
    return () => {
      observer?.disconnect();
      chart.off('click', handler);
      chart.dispose();
      chartRef.current = null;
    };
  }, [renderer]);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return (
    <div
      ref={ref}
      className={className}
      style={{ width: '100%', height }}
      role="img"
      aria-label={ariaLabel}
    />
  );
};

export default EChart;
