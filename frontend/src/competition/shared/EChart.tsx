import React, { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

export interface EChartProps {
  option: echarts.EChartsOption;
  height?: number | string;
  className?: string;
  ariaLabel?: string;
  /** 点击图表元素（如雷达顶点、柱条）时回调，参数为 echarts 事件对象 */
  onClick?: (params: echarts.ECElementEvent) => void;
}

/**
 * 轻量 ECharts 封装：自适应容器尺寸、统一主题、卸载即销毁。
 * 考试分析与学生档案共用，避免各处手写 SVG 折线/柱状。
 */
const EChart: React.FC<EChartProps> = ({ option, height = 260, className, ariaLabel, onClick }) => {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const onClickRef = useRef(onClick);
  onClickRef.current = onClick;

  useEffect(() => {
    if (!ref.current) return;
    // SVG 矢量渲染：任意缩放/高分屏截图都不糊（canvas 位图在低 DPR 下会发虚）。
    const chart = echarts.init(ref.current, undefined, {
      renderer: 'svg',
      devicePixelRatio: Math.max(2, window.devicePixelRatio || 1),
    });
    chartRef.current = chart;
    const handler = (params: echarts.ECElementEvent) => onClickRef.current?.(params);
    chart.on('click', handler);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.off('click', handler);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return <div ref={ref} className={className} style={{ width: '100%', height }} role="img" aria-label={ariaLabel} />;
};

export default EChart;