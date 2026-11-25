import React, { useMemo } from 'react';
import { Button, Segmented, Tooltip, Typography } from 'antd';
import {
  BarChartOutlined, BulbOutlined, CheckCircleOutlined, FallOutlined, LineChartOutlined, QuestionCircleOutlined,
  RadarChartOutlined, RiseOutlined, RobotOutlined, TeamOutlined, TrophyOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { EChartsOption } from 'echarts';
import EChart from '../shared/EChart';
import { percent } from '../shared/examReport';
import type { examReport } from '../shared/examReport';
import type { KnowledgePoint } from '../shared/types';
import { getKpStat } from './data';
import { getBundle } from '../dataSource';
import { KP_SHORT_NAME } from './BarCompare';
import { FileWordOutlined } from '@ant-design/icons';

const { Text } = Typography;
const BANDS = ['0–24%', '25–49%', '50–74%', '75–100%'];
const CLASS_COLORS = ['#2878b8', '#8a6be8', '#2ca02c', '#d62728'];
const indicatorOf = (kp: KnowledgePoint) => (kp as KnowledgePoint & { indicatorId?: string }).indicatorId || kp.id;
const shortName = (kp: KnowledgePoint) => KP_SHORT_NAME[indicatorOf(kp)] || kp.name.replace(/^T\d+I\d+ · /, '');
const bandOf = (rate: number) => Math.min(3, Math.floor(rate / 25));

export type OverviewScope = 'mine' | 'all';

export interface ExamOverviewProps {
  overview: ReturnType<typeof examReport>;
  kps: KnowledgePoint[];
  classA: string;
  scope: OverviewScope;
  onScopeChange: (scope: OverviewScope) => void;
  className: (id: string) => string;
  onGoStudent: (studentId: string, questionId: string, classId: string) => void;
}

/** 考试分析首屏：一句话结论先行 + 班级 KPI + 柱状图/考点雷达，先判断后明细。默认只看顶栏所选班级，可切全部班级对比。 */
const ExamOverview: React.FC<ExamOverviewProps> = ({ overview, kps, classA, scope, onScopeChange, className, onGoStudent }) => {
  const classes = overview.classes;
  const scopeClasses = useMemo(() => (scope === 'mine' ? classes.filter((c) => c.id === classA) : classes), [classes, scope, classA]);
  const firstRow = overview.rows[0];
  const navigate = useNavigate();
  const quizId = firstRow?.quizId || '';
  const jumpAssistant = (prompt: string) => navigate(`/assistant?prompt=${encodeURIComponent(prompt)}&autosend=1`);

  const distribution = useMemo(() => scopeClasses.map((c) => BANDS.map((_, band) => c.scored.filter((s) => bandOf(s.rate || 0) === band).length)), [scopeClasses]);

  const barOption = useMemo<EChartsOption>(() => ({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { bottom: 0, textStyle: { fontSize: 12 } },
    grid: { left: 40, right: 16, top: 24, bottom: 40 },
    xAxis: { type: 'category', data: BANDS, axisLabel: { fontSize: 12 } },
    yAxis: { type: 'value', name: '人数', minInterval: 1 },
    color: CLASS_COLORS,
    series: scopeClasses.map((c) => ({ name: className(c.id), type: 'bar', barMaxWidth: 28, data: distribution[scopeClasses.indexOf(c)],
      itemStyle: { borderRadius: [3, 3, 0, 0] }, label: { show: true, position: 'top', fontSize: 12 } })),
  }), [scopeClasses, distribution, className]);

  const radarKps = useMemo(() => kps.filter((kp) => scopeClasses.every((c) => getKpStat(kp, c.id)?.scoreRate != null)).slice(0, 8), [kps, scopeClasses]);

  const radarOption = useMemo<EChartsOption>(() => {
    const indicator = radarKps.map((kp) => ({ name: shortName(kp), max: 100 }));
    const seriesData = scopeClasses.map((c) => ({
      name: className(c.id), value: radarKps.map((kp) => { const r = getKpStat(kp, c.id)?.scoreRate; return r == null ? 0 : Math.round(r * 100); }),
      lineStyle: { color: CLASS_COLORS[classes.findIndex((x) => x.id === c.id) % CLASS_COLORS.length] },
      itemStyle: { color: CLASS_COLORS[classes.findIndex((x) => x.id === c.id) % CLASS_COLORS.length] }, areaStyle: { opacity: 0.12 },
    }));
    return {
      tooltip: {}, legend: { bottom: 0, textStyle: { fontSize: 12 } },
      radar: { indicator, radius: '62%', center: ['50%', '46%'], axisName: { fontSize: 12, color: '#555' },
        splitArea: { areaStyle: { color: ['#fafafa', '#f0f2f5'] } } },
      series: [{ type: 'radar', data: seriesData }],
    };
  }, [radarKps, scopeClasses, classes, className]);

  const trendOption = useMemo<EChartsOption>(() => {
    const bundle = getBundle();
    const quizzes = [...(bundle?.quizzes || [])].sort((a, b) => (a.date || '').localeCompare(b.date || ''));
    // 本班模式：本班实线加粗，其余班虚线作参照，差距仍然一眼可见。
    const series = classes.map((c) => {
      const cid = c.id;
      const i = classes.findIndex((x) => x.id === cid);
      const reference = scope === 'mine' && cid !== classA;
      return {
        name: className(cid), type: 'line' as const, smooth: true, symbol: 'circle', symbolSize: reference ? 4 : 7,
        lineStyle: { width: reference ? 1.5 : 3, type: reference ? ('dashed' as const) : ('solid' as const), color: CLASS_COLORS[i % CLASS_COLORS.length], opacity: reference ? 0.65 : 1 },
        itemStyle: { color: CLASS_COLORS[i % CLASS_COLORS.length], opacity: reference ? 0.65 : 1 },
        data: quizzes.map((q) => {
          const members = (bundle?.students || []).filter((s) => s.classId === cid).map((s) => s.id);
          const rates = members.map((sid) => {
            const r = (bundle?.answers || []).filter((a) => a.quizId === q.id && a.studentId === sid);
            const full = r.reduce((n, a) => n + a.fullScore, 0);
            return full > 0 ? r.reduce((n, a) => n + a.score, 0) / full * 100 : null;
          }).filter((x): x is number => x != null);
          return rates.length ? Number((rates.reduce((s, r) => s + r, 0) / rates.length).toFixed(1)) : null;
        }),
      };
    });
    return {
      tooltip: { trigger: 'axis', valueFormatter: (v) => v == null ? '无记录' : `${v}%` },
      legend: { bottom: 0, textStyle: { fontSize: 12 } },
      grid: { left: 44, right: 16, top: 24, bottom: 40 },
      xAxis: { type: 'category', data: quizzes.map((q) => q.name || q.id), axisLabel: { fontSize: 12 } },
      yAxis: { type: 'value', min: 0, max: 100, name: '平均得分率%', axisLabel: { formatter: '{value}%' } },
      series,
    };
  }, [classes, scope, classA, className]);

  // 结论先行：用真实统计现算「最弱考点 + 平均差」，不写死文案；本班模式与其他班均值比，全部模式班际比。
  const verdict = useMemo(() => {
    const parts: string[] = [];
    if (scope === 'mine') {
      const mine = kps.map((kp) => ({ kp, rate: getKpStat(kp, classA)?.scoreRate }))
        .filter((x): x is { kp: KnowledgePoint; rate: number } => x.rate != null);
      const weakest = mine.length ? mine.reduce((min, x) => (x.rate < min.rate ? x : min)) : null;
      if (weakest) parts.push(`本班弱项：${shortName(weakest.kp)}（得分率 ${Math.round(weakest.rate * 100)}%），建议优先讲评。`);
      const ca = classes.find((c) => c.id === classA);
      const others = classes.filter((c) => c.id !== classA && c.mean != null);
      if (ca?.mean != null && others.length) {
        const otherAvg = others.reduce((s, c) => s + (c.mean || 0), 0) / others.length;
        const gap = ca.mean - otherAvg;
        parts.push(Math.abs(gap) < 0.05
          ? `本班平均 ${percent(ca.mean)}，与其他班均值基本持平。`
          : `本班平均 ${percent(ca.mean)}，${gap > 0 ? '高于' : '低于'}其他班均值 ${percent(otherAvg)} 约 ${Math.abs(gap).toFixed(1)} 个百分点。`);
      }
      return parts.length ? parts.join('') : '暂无本班可比的考点与成绩数据，先看下方明细。';
    }
    const comparable = kps.map((kp) => {
      const rates = classes.map((c) => getKpStat(kp, c.id)?.scoreRate).filter((r): r is number => r != null);
      return rates.length === classes.length && classes.length > 1 ? { kp, avg: rates.reduce((s, r) => s + r, 0) / rates.length, rates } : null;
    }).filter((x): x is { kp: KnowledgePoint; avg: number; rates: number[] } => x != null);
    const weakest = comparable.length ? comparable.reduce((min, x) => (x.avg < min.avg ? x : min)) : null;
    if (weakest) parts.push(`各班共同弱项：${shortName(weakest.kp)}（${classes.map((c, i) => `${className(c.id)} ${Math.round(weakest.rates[i] * 100)}%`).join(' / ')}），建议优先讲评。`);
    const ranked = classes.filter((c) => c.mean != null).sort((a, b) => (b.mean || 0) - (a.mean || 0));
    if (ranked.length > 1) {
      const hi = ranked[0]; const lo = ranked[ranked.length - 1];
      const gap = (hi.mean || 0) - (lo.mean || 0);
      parts.push(Math.abs(gap) < 0.05
        ? `各班平均得分率基本持平（${percent(hi.mean)} vs ${percent(lo.mean)}）。`
        : `${className(hi.id)} 平均 ${percent(hi.mean)} 领先，${className(lo.id)} ${percent(lo.mean)}，相差 ${gap.toFixed(1)} 个百分点。`);
    }
    return parts.length ? parts.join('') : '暂无各班可比的考点与成绩数据，先看下方各班明细。';
  }, [kps, classA, classes, scope, className]);

  return <section className="teacher-section teacher-exam-overview" aria-label="考试总览" data-testid="exam-overview">
    <div className="teacher-section-heading"><h3 style={{ margin: 0 }}><BarChartOutlined aria-hidden />班级考试总览</h3>
      <span className="teacher-ranking-tools">
        <Text type="secondary">{scope === 'mine' ? overview.people.filter((p) => p.classId === classA).reduce((n, p) => n + p.count, 0) : overview.rows.length} 条有效已评分作答 · {scope === 'mine' ? className(classA) : classes.map((c) => className(c.id)).join(' vs ')}</Text>
        <Segmented
          value={scope}
          onChange={(v) => onScopeChange(v as OverviewScope)}
          options={[{ label: '本班', value: 'mine' }, { label: '全部班级对比', value: 'all' }]}
        />
      </span></div>

    <div className="teacher-exam-verdict">
      <p className="teacher-exam-verdict-text"><BulbOutlined aria-hidden />{verdict}</p>
      <div className="teacher-exam-verdict-action">
        <Button type="primary" icon={<RobotOutlined aria-hidden />} onClick={() => jumpAssistant(`请分析班级 ${classA} 在测验 ${quizId} 的整体情况与共性薄弱，给出讲评优先级。`)} data-testid="jump-assistant-class">AI 班级共性诊断</Button>
        <Tooltip title="跳转 AI 助手并自动发送带本页班级/测验上下文的提示词，结果需教师核对。"><QuestionCircleOutlined aria-hidden className="teacher-note-hint" /></Tooltip>
        <Button icon={<FileWordOutlined aria-hidden />} onClick={() => jumpAssistant(`请为班级 ${classA} 生成一份10题查漏补缺强化卷（风格变式提升），含答案解析，并导出Word。`)} data-testid="jump-assistant-paper">AI 出卷（本班强化卷）</Button>
      </div>
    </div>

    <div className={'teacher-exam-kpis' + (scopeClasses.length === 1 ? ' is-single' : '')}>
      {scopeClasses.map((c) => {
        const index = classes.findIndex((x) => x.id === c.id);
        const scored = c.scored;
        const excellent = scored.filter((s) => (s.rate || 0) >= 85).length;
        const pass = scored.filter((s) => (s.rate || 0) >= 60).length;
        const active = c.members.filter((p) => p.count).length;
        return <article className="teacher-exam-kpi" key={c.id} style={{ borderTopColor: CLASS_COLORS[index % CLASS_COLORS.length] }}>
          <div className="teacher-exam-kpi-head"><strong><TeamOutlined aria-hidden />{c.name === c.id ? `班级 ${c.id}` : c.name}</strong><span>{active}/{c.members.length} 人有有效答卷</span></div>
          <div className="teacher-exam-kpi-rate"><b>{percent(c.mean)}</b><span>平均得分率</span></div>
          <div className="teacher-exam-kpi-grid">
            <div><span><TrophyOutlined aria-hidden />优秀率 ≥85%</span><strong>{scored.length ? (excellent / scored.length * 100).toFixed(0) : '—'}%</strong><small>{excellent} 人</small></div>
            <div><span><CheckCircleOutlined aria-hidden />及格率 ≥60%</span><strong>{scored.length ? (pass / scored.length * 100).toFixed(0) : '—'}%</strong><small>{pass} 人</small></div>
          </div>
          {([{ label: '最高', students: c.high }, { label: '最低', students: c.low }]).map(({ label, students }) =>
            <div className="teacher-exam-highlow" key={label}><span>{label === '最高' ? <RiseOutlined aria-hidden /> : <FallOutlined aria-hidden />}{label}</span>
              {students.length ? students.map((p) => <Button key={p.id} type="link" size="small" onClick={() => onGoStudent(p.id, overview.rows.find((a) => a.studentId === p.id)?.questionId || firstRow?.questionId || '', p.classId)}>
                {p.name} {percent(p.rate)}<span className="teacher-muted">（{p.score}/{p.full} 分）</span></Button>) : <span>—</span>}
            </div>)}
        </article>;
      })}
    </div>

    <div className="teacher-exam-charts">
      <figure className="teacher-exam-chart"><figcaption><LineChartOutlined aria-hidden />{scope === 'mine' ? '历次平均得分率（实线本班 · 虚线其他班）' : '各班历次平均得分率'}</figcaption>
        <EChart option={trendOption} height={230} ariaLabel="各班历次测验平均得分率折线图" /></figure>
      <figure className="teacher-exam-chart"><figcaption><BarChartOutlined aria-hidden />{scope === 'mine' ? '本班得分段人数' : '各班得分段人数对比'}</figcaption>
        {scopeClasses.some((c) => c.scored.length) ? <EChart option={barOption} height={230} ariaLabel="各得分段人数柱状图" /> : <Text type="secondary">暂无有效成绩</Text>}</figure>
      <figure className="teacher-exam-chart"><figcaption><RadarChartOutlined aria-hidden />{scope === 'mine' ? '考点得分率雷达（本班）' : '考点得分率雷达（各班）'}</figcaption>
        {radarKps.length ? <EChart option={radarOption} height={230} ariaLabel="各考点得分率雷达图" /> : <Text type="secondary">无可比考点</Text>}</figure>
    </div>

    <details className="teacher-note-fold teacher-exam-note"><summary><QuestionCircleOutlined aria-hidden />统计口径说明</summary>
      <p className="teacher-muted">个人得分率＝本次有效作答得分合计 ÷ 对应题目满分合计；班级平均得分率＝班内有效得分合计 ÷ 对应满分合计（合并口径，与成果包一致，可用得分和/满分和独立复算）。最高/最低只反映本次有记录的题目，并列同时显示。学生作答题目不同，不能据此排整卷名次。优秀率/及格率按个人得分率 ≥85% / ≥60% 统计。「本班」范围只统计顶栏所选班级，折线图中虚线为其他班参照；「全部班级对比」并列所有班级。考点柱条/雷达为考点级合并得分率（该考点全部题目得分和÷满分和），逐题明细表为单题得分率，两者口径不同、不可直接互比。</p>
    </details>
  </section>;
};

export default ExamOverview;