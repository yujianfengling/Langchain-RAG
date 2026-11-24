import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Alert, Button, Collapse, Drawer, Empty, Radio, Select, Space, Table, Tag, Typography } from 'antd';
import { CLASSES, TOPICS, QUIZZES, getStudentName } from '../shared/ids';
import { listKnowledgePoints, getKpStat, getShowcaseCases } from './data';
import type { ShowcaseCase } from './data';
import { getQuestion, listAnswersByQuestion } from '../studentAnswers/data';
import { getSourceByQuestion } from '../curriculum/data';
import type { KnowledgePoint, KpClassStat, StudentAnswer, TextbookSource } from '../shared/types';
import TextbookDrawer from '../shared/TextbookDrawer';
import { rememberAnalysis } from '../shared/teacherContext';
import ReviewPlanPanel from '../reviewPlan/ReviewPlanPanel';
import BarCompare, { KP_SHORT_NAME, type BarRow } from './BarCompare';
import { getBundle, isRealData } from '../dataSource';
import type { AnomalyRecord, RealAnswer } from '../dataSource';
import './teacherAnalytics.css';

const { Title, Text, Paragraph } = Typography;
type CompareScope = 'all' | 'common';
const indicatorOf = (kp: KnowledgePoint) => (kp as KnowledgePoint & { indicatorId?: string }).indicatorId || kp.id;
const className = (id: string) => CLASSES.find((c) => c.id === id)?.name || id;
const shortName = (kp: KnowledgePoint) => KP_SHORT_NAME[indicatorOf(kp)] || kp.name.replace(/^T\d+I\d+ · /, '');
function getCommonItemIds(a?: KpClassStat | null, b?: KpClassStat | null): string[] {
  const ids = new Set((b?.itemBreakdown || []).map((x) => x.item_id));
  return (a?.itemBreakdown || []).filter((x) => ids.has(x.item_id)).map((x) => x.item_id);
}
function sumBreakdown(stat: KpClassStat | undefined | null, ids: string[]) {
  const rows = (stat?.itemBreakdown || []).filter((x) => ids.includes(x.item_id));
  return { score: rows.reduce((s, x) => s + x.score, 0), full: rows.reduce((s, x) => s + x.full_score, 0), events: rows.reduce((s, x) => s + x.event_count, 0), items: rows.length };
}
const StatusTag: React.FC<{ stat: KpClassStat }> = ({ stat }) => <Tag color={stat.status === '真实零分' ? 'red' : stat.status === '正常' ? 'green' : 'default'}>{stat.status === '正常' ? '有效' : stat.status}</Tag>;
const RateCell: React.FC<{ stat?: KpClassStat; scope?: CompareScope; common?: ReturnType<typeof sumBreakdown> }> = ({ stat, scope = 'all', common }) => {
  if (!stat) return <Text type="secondary">无数据</Text>;
  if (scope === 'common') {
    if (stat.status === '本次未测') return <Text type="secondary">本次未测（正式题目范围未覆盖，不是两班题目不同）</Text>;
    if (stat.status === '记录不足') return <Text type="secondary">记录不足（{stat.validAttempts} 条有效作答）</Text>;
    return common?.items && common.full > 0
      ? <Space direction="vertical" size={0}><Text strong>{`${Math.round(common.score / common.full * 100)}%`}</Text><Text type="secondary">{common.events} 条有效作答 · {common.items} 道共同题</Text></Space>
      : <Text type="secondary">无法同题比较</Text>;
  }
  return <Space direction="vertical" size={0}><Text strong>{stat.scoreRate == null ? stat.status : `${Math.round(stat.scoreRate * 100)}%`}</Text><Text type="secondary">{stat.validAttempts} 条有效作答</Text></Space>;
};
interface SavedView { drawerOpen?: boolean; compareOpen?: boolean; compareEnabled?: boolean; tableOpen?: boolean; kpId?: string; selectedClass?: string; questionKeys?: string[]; scroll?: number; drawerScroll?: number }
const ClassAnalyticsPage: React.FC = () => {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const bundle = getBundle();
  const quizId = QUIZZES.some((q) => q.id === params.get('quizId')) ? params.get('quizId')! : QUIZZES[0]?.id || '';
  const classA = CLASSES.some((c) => c.id === params.get('classId')) ? params.get('classId')! : CLASSES[0]?.id || '';
  const topicId = params.get('topic') || 'all';
  const scope: CompareScope = params.get('scope') === 'common' ? 'common' : 'all';
  const planTheme = topicsValid(params.get('planTheme')) || bundle?.reviewPlans?.theme || TOPICS[0]?.id || 'T01';
  function topicsValid(id: string | null) { return id && TOPICS.some((t) => t.id === id) ? id : null; }
  const setContext = (values: Record<string, string>) => {
    const next = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(values)) next.set(key, value);
    setParams(next, { replace: true });
  };
  const [classB, setClassB] = useState(() => params.get('compareClassId') || CLASSES.find((c) => c.id !== classA)?.id || classA);
  useEffect(() => {
    if (classB === classA) setClassB(CLASSES.find((c) => c.id !== classA)?.id || classA);
  }, [classA, classB]);
  const initialPath = useRef(`/class-analytics${window.location.search}`);
  const [restored] = useState<SavedView>(() => {
    if (sessionStorage.getItem('competition.plan.autoRecompute') === '1') return { scroll: 0 };
    try { return JSON.parse(sessionStorage.getItem(`competition.analysis.view:${initialPath.current}`) || '{}'); } catch { return {}; }
  });
  const [selected, setSelected] = useState<{ kp: KnowledgePoint; classId: string } | null>(() => {
    const id = restored.kpId || params.get('kpId');
    const kp = listKnowledgePoints(undefined, quizId).find((k) => indicatorOf(k) === id || k.id === id);
    return kp ? { kp, classId: restored.selectedClass || classA } : null;
  });
  const [drawerOpen, setDrawerOpen] = useState(!!restored.drawerOpen);
  const compareEnabled = params.has('compareEnabled') ? params.get('compareEnabled') === 'true' : !!(restored.compareEnabled || restored.compareOpen);
  const [tableOpen, setTableOpen] = useState(!!restored.tableOpen);
  const drawerScroll = useRef(restored.drawerScroll || 0);
  const [questionKeys, setQuestionKeys] = useState<string[]>(restored.questionKeys || []);
  const [textbook, setTextbook] = useState<TextbookSource | null>(null);
  useEffect(() => {
    const t = setTimeout(() => {
      if (restored.scroll != null) document.querySelector('.comp-main')?.scrollTo({ top: restored.scroll });
    }, 300);
    return () => clearTimeout(t);
  }, [restored]);
  useEffect(() => {
    const path = `/class-analytics?${params.toString()}`;
    rememberAnalysis(path);
    const scroller = document.querySelector('.comp-main');
    const saveView = () => {
      sessionStorage.setItem(`competition.analysis.view:${path}`, JSON.stringify({
        drawerOpen, compareEnabled, tableOpen, kpId: selected ? indicatorOf(selected.kp) : '',
        selectedClass: selected?.classId, questionKeys, scroll: scroller?.scrollTop || 0, drawerScroll: drawerScroll.current,
      }));
    };
    saveView();
    scroller?.addEventListener('scroll', saveView, { passive: true });
    return () => scroller?.removeEventListener('scroll', saveView);
  }, [params, drawerOpen, compareEnabled, tableOpen, selected, questionKeys]);
  const allKps = useMemo(() => listKnowledgePoints(topicId === 'all' ? undefined : topicId, quizId), [topicId, quizId]);
  const hasBreakdown = listKnowledgePoints(undefined, quizId).some((kp) => kp.stats.some((s) => s.itemBreakdown != null));
  const effectiveScope = compareEnabled && hasBreakdown ? scope : 'all';
  const sortedKps = useMemo(() => {
    const rate = (kp: KnowledgePoint) => {
      const stat = getKpStat(kp, classA);
      if (stat?.status === '记录不足' || stat?.status === '本次未测') return Infinity;
      if (effectiveScope === 'all') return stat?.scoreRate ?? Infinity;
      const common = sumBreakdown(stat, getCommonItemIds(stat, getKpStat(kp, classB)));
      return common.full > 0 ? common.score / common.full : Infinity;
    };
    return [...allKps].sort((a, b) => rate(a) - rate(b));
  }, [allKps, classA, classB, effectiveScope]);
  const selectKp = (kp: KnowledgePoint, cls: string) => { drawerScroll.current = 0; setSelected({ kp, classId: cls }); setQuestionKeys([]); setDrawerOpen(true); };
  const onBarSelect = (indicator: string, cls: string) => {
    const kp = allKps.find((k) => indicatorOf(k) === indicator);
    if (kp) selectKp(kp, cls);
  };
  const barRows: BarRow[] = sortedKps.map((kp) => {
    const cells: BarRow['cells'] = {};
    for (const id of [classA, classB]) {
      const stat = getKpStat(kp, id);
      if (!stat) continue;
      const ids = getCommonItemIds(stat, getKpStat(kp, id === classA ? classB : classA));
      const common = sumBreakdown(stat, ids);
      cells[id] = { rate: stat.scoreRate, status: stat.status, validAttempts: stat.validAttempts, score: stat.score || 0, maxScore: stat.maxScore || 0, common: ids.length && common.full > 0 ? { ...common, rate: common.score / common.full } : null };
    }
    return { id: kp.id, indicatorId: indicatorOf(kp), name: kp.name, cells };
  });
  const selectedStat = selected ? getKpStat(selected.kp, selected.classId) : undefined;
  const selectedIndicator = selected ? indicatorOf(selected.kp) : '';
  const otherClass = selected?.classId === classA ? classB : classA;
  const otherStat = selected ? getKpStat(selected.kp, otherClass) : undefined;
  const panelCommonIds = getCommonItemIds(selectedStat, otherStat);
  const panelCommon = sumBreakdown(selectedStat, panelCommonIds);
  const scopedQuestionIds = (selectedStat?.itemBreakdown?.map((x) => x.item_id) || selectedStat?.topWrongQuestionIds || [])
    .filter((id) => effectiveScope !== 'common' || panelCommonIds.includes(id));
  const wrongQuestionIds = scopedQuestionIds.filter((id) => selected && listAnswersByQuestion(id, { quizId, classId: selected.classId }).some((a) => a.score < a.fullScore));
  const onlyMine = (selectedStat?.itemBreakdown || []).map((x) => x.item_id).filter((id) => !panelCommonIds.includes(id));
  const onlyOther = (otherStat?.itemBreakdown || []).map((x) => x.item_id).filter((id) => !panelCommonIds.includes(id));
  const matchedAnomalies = useMemo<AnomalyRecord[]>(() => {
    const eventQuiz = new Map((bundle?.events || []).map((e) => [e.recordId, e.quizId]));
    return (bundle?.anomalies || []).filter((a) => a.class_id === selected?.classId && a.indicator_id === selectedIndicator && (!eventQuiz.has(a.record_id) || eventQuiz.get(a.record_id) === quizId));
  }, [bundle, selected, selectedIndicator, quizId]);
  const rememberReturn = () => {
    drawerScroll.current = document.querySelector('.teacher-kp-drawer .ant-drawer-body')?.scrollTop || 0;
    const next = new URLSearchParams(window.location.search);
    next.set('quizId', quizId); next.set('classId', classA); next.set('topic', topicId); next.set('scope', scope); next.set('planTheme', planTheme); next.set('compareClassId', classB); next.set('compareEnabled', String(compareEnabled));
    if (!next.has('reviewBudget')) {
      let budget = 3; try { if (JSON.parse(sessionStorage.getItem('competition.plan.ui.v1') || '{}').budget === 2) budget = 2; } catch { /* storage unavailable */ }
      next.set('reviewBudget', String(budget));
    }
    if (selected) next.set('kpId', indicatorOf(selected.kp));
    const path = `/class-analytics?${next.toString()}`;
    try { sessionStorage.setItem(`competition.analysis.view:${path}`, JSON.stringify({ drawerOpen, compareEnabled, tableOpen, kpId: selected ? indicatorOf(selected.kp) : '', selectedClass: selected?.classId, questionKeys, scroll: document.querySelector('.comp-main')?.scrollTop || 0, drawerScroll: drawerScroll.current })); } catch { /* storage unavailable */ }
    rememberAnalysis(path);
    return path;
  };
  const goStudent = (studentId: string, questionId: string, indicator: string, cls = classA, answer?: StudentAnswer, sourceRecordId?: string) => {
    const sp = new URLSearchParams({ studentId, questionId, quizId, classId: cls, kpId: indicator, returnTo: rememberReturn() });
    const recordId = sourceRecordId && bundle?.answers.some((a) => a.sourceRecordId === sourceRecordId && a.studentId === studentId && a.questionId === questionId && a.quizId === quizId)
      ? sourceRecordId : (answer as RealAnswer | undefined)?.sourceRecordId;
    if (recordId) sp.set('recordId', recordId);
    const source = params.get('source'); if (source) sp.set('source', source);
    navigate({ pathname: '/student-answers', search: sp.toString() });
  };
  const comparisonColumns = [
    { title: '考点', key: 'name', render: (_: unknown, kp: KnowledgePoint) => <Button type="link" onClick={() => selectKp(kp, classA)}>{shortName(kp)}</Button> },
    ...(compareEnabled ? [classA, classB] : [classA]).map((id) => ({ title: className(id), key: id, render: (_: unknown, kp: KnowledgePoint) => {
      const stat = getKpStat(kp, id);
      const common = sumBreakdown(stat, getCommonItemIds(stat, getKpStat(kp, id === classA ? classB : classA)));
      return <div><Button type="text" style={{ height: 'auto', textAlign: 'left' }} onClick={() => selectKp(kp, id)}><RateCell stat={stat} scope={effectiveScope} common={common} /></Button><div>{stat && (effectiveScope === 'all' ? <StatusTag stat={stat} /> : <Text type="secondary">全口径状态：<StatusTag stat={stat} /></Text>)}</div><Text type="secondary">{effectiveScope === 'common' ? '全口径' : ''}有记录 {stat?.observedStudents ?? '—'}/{stat?.classSize ?? '—'} 人</Text></div>;
    } })),
  ];
  const reconciliation = bundle?.reconciliation;
  const showcaseCases = getShowcaseCases();
  const goShowcase = (c: ShowcaseCase) => {
    const sp = new URLSearchParams({
      studentId: c.studentId, questionId: c.questionId, quizId: c.nodeId,
      classId: c.classId, kpId: c.indicatorId, tab: 'profile', returnTo: rememberReturn(),
    });
    const source = params.get('source');
    if (source) sp.set('source', source);
    navigate({ pathname: '/student-answers', search: sp.toString() });
  };
  return <div className="competition-page" style={{ paddingTop: 10 }}>
    <header className="teacher-page-header" data-testid="class-context"><Title level={3} style={{ margin: 0 }}>考试分析</Title><Space wrap><Select aria-label="当前测验" value={quizId} style={{ width: 240 }} options={QUIZZES.map((q) => ({ value: q.id, label: `${q.name}（${q.date}）` }))} onChange={(id) => { setContext({ quizId: id }); setSelected(null); setDrawerOpen(false); }} /><Text>本班</Text><Select aria-label="讲评班级" value={classA} style={{ width: 130 }} options={CLASSES.map((c) => ({ value: c.id, label: c.name }))} onChange={(id) => { setContext({ classId: id }); setSelected(null); setDrawerOpen(false); }} /></Space></header>
    {/* 赛题硬要求：三名代表性学生案例（部分得分/表现较好/记录不足），首屏可见 */}
    {showcaseCases.length > 0 && (
      <section className="teacher-section teacher-showcase" aria-label="三类代表性学生案例" data-testid="showcase-cases">
        <div className="teacher-section-heading"><Title level={5} style={{ margin: 0 }}>三类代表性学生案例</Title><Text className="teacher-muted">点击看答题证据、判断依据与下一步安排</Text></div>
        <div className="teacher-showcase-list">
          {showcaseCases.map((c) => (
            <Button key={`${c.studentId}|${c.indicatorId}`} type="text" className="teacher-showcase-case"
                    data-case-name={c.caseName} onClick={() => goShowcase(c)}>
              <span className="teacher-showcase-kind">{c.caseName}</span>
              <strong>{c.studentId} · {c.nodeId}/{c.indicatorId}</strong>
              <span className="teacher-muted">考点覆盖：{c.coverageVerdict} · {c.score}/{c.fullScore}</span>
            </Button>
          ))}
        </div>
      </section>
    )}
    <div className="teacher-analysis-grid">
      <ReviewPlanPanel quizId={quizId} classId={classA} planTheme={planTheme} onPlanThemeChange={(id) => setContext({ planTheme: id })} onGoStudent={(sid, qid, ind, recordId) => goStudent(sid, qid, ind, classA, undefined, recordId)} />
      <section className="teacher-section teacher-kp-panel" aria-label="考点得分率">
        <div className="teacher-section-heading"><Title level={4} style={{ margin: 0 }}>{compareEnabled ? '两班考点对比' : '本班易错考点'}</Title><Button disabled={!compareEnabled && CLASSES.length < 2} onClick={() => { setContext({ compareEnabled: String(!compareEnabled), compareClassId: classB }); setDrawerOpen(false); }}>{compareEnabled ? '只看本班' : '与另一班比较'}</Button></div>
        <div className="teacher-kp-controls">
          <label>考点范围 <Select aria-label="考点范围" value={topicId} style={{ width: 160 }} onChange={(id) => { setContext({ topic: id }); setDrawerOpen(false); }} options={[{ value: 'all', label: '全部主题' }, ...TOPICS.map((t) => ({ value: t.id, label: t.name }))]} /></label>
          {compareEnabled && <label>对照班 <Select aria-label="对照班级" value={classB} style={{ width: 130 }} onChange={(id) => { setClassB(id); setContext({ compareClassId: id }); setDrawerOpen(false); }} options={CLASSES.filter((c) => c.id !== classA).map((c) => ({ value: c.id, label: c.name }))} /></label>}
          {compareEnabled && hasBreakdown && <Radio.Group aria-label="统计口径" value={effectiveScope} onChange={(e) => { setContext({ scope: e.target.value }); setQuestionKeys([]); }} options={[{ label: '各班全部有效作答', value: 'all' }, { label: '仅比较共同题目', value: 'common' }]} optionType="button" size="small" />}
        </div>
        <Paragraph className="teacher-muted">{effectiveScope === 'common' ? '仅共同题目' : '全部有效作答'} · 按本班得分率升序 · 点条形查看易错题、学生与答卷；浏览不改变左侧讲评安排。</Paragraph>
        <BarCompare rows={barRows} lectureClassId={classA} compareClassId={compareEnabled ? classB : undefined} lectureClassName={className(classA)} compareClassName={className(classB)} scope={effectiveScope} selected={selected ? { kpId: selected.kp.id, classId: selected.classId } : null} onSelect={onBarSelect} onSelectKp={(ind) => onBarSelect(ind, classA)} />
        {effectiveScope === 'common' && !barRows.some((row) => row.cells[classA]?.common && row.cells[classB]?.common) && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前范围无共同有效题目，无法同题比较" />}
        <Paragraph className="teacher-muted teacher-kp-footnote">同尺度 0–100%；样本数为作答条数，非学生人数。未测、记录不足不按零分处理；共同题不保证学生覆盖相同。</Paragraph>
        <Collapse activeKey={tableOpen ? ['statistics'] : []} onChange={(keys) => setTableOpen(keys.includes('statistics'))} items={[{ key: 'statistics', label: '统计明细 · 与图表同口径', children: <Table size="small" tableLayout="fixed" pagination={false} rowKey="id" dataSource={sortedKps} columns={comparisonColumns} /> }]} />
      </section>
    </div>
    <Drawer title={selected ? `易错题与答卷 · ${className(selected.classId)} · ${shortName(selected.kp)}` : '易错题与答卷'} open={drawerOpen && !!selected} onClose={() => setDrawerOpen(false)} width={720} rootClassName="teacher-kp-drawer" afterOpenChange={(open) => { if (open) document.querySelector('.teacher-kp-drawer .ant-drawer-body')?.scrollTo({ top: drawerScroll.current }); }}>
      {selected && <><Paragraph>{selected.kp.name}</Paragraph>
        <Paragraph>当前口径：{effectiveScope === 'common' ? '仅比较共同题目' : '全部有效作答'} · {className(selected.classId)}</Paragraph>
        <RateCell stat={selectedStat} scope={effectiveScope} common={panelCommon} />
        {wrongQuestionIds.length === 0 ? <Empty description={effectiveScope === 'common' && !panelCommonIds.length ? '无共同有效题目，无法同题比较' : '当前口径没有记录到的失分题'} /> : <Collapse activeKey={questionKeys} onChange={(keys) => setQuestionKeys(typeof keys === 'string' ? [keys] : keys)} items={wrongQuestionIds.map((qid) => {
          const q = getQuestion(qid); const source = getSourceByQuestion(qid);
          const answers = listAnswersByQuestion(qid, { quizId, classId: selected.classId });
          const lost = new Set(answers.filter((a) => a.score < a.fullScore).map((a) => a.studentId));
          return { key: qid, label: `${qid} · ${q?.stem || '暂无题干'} · ${lost.size} 人失分`, children: <><Paragraph type="secondary">相关学生答卷（含满分记录供对照）</Paragraph>{answers.length ? answers.map((a) => <div key={a.id}><Button type="link" onClick={() => goStudent(a.studentId, qid, selectedIndicator, selected.classId, a)}>{getStudentName(a.studentId)} · 得分 {a.score}/{a.fullScore}</Button></div>) : <Text type="secondary">当前数据没有学生答卷明细</Text>}{source && <Button type="link" onClick={() => setTextbook(source)}>教材原页 · {source.docName} 第 {source.page} 页</Button>}</> };
        })} />}
        {isRealData() && selectedStat && <>
          <Title level={5}>统计依据与异常复核</Title>
          <Paragraph>各班全部有效作答：得分和 {selectedStat.score ?? '—'} / 满分和 {selectedStat.maxScore ?? '—'}；{selectedStat.scoreRate == null ? selectedStat.status : `得分率 ${(selectedStat.scoreRate * 100).toFixed(2)}%`}；有效作答 {selectedStat.validAttempts} 条；有记录 {selectedStat.observedStudents ?? '—'} / 班级 {selectedStat.classSize ?? '—'} 人；参与题目 {selectedStat.itemCount ?? '—'} 道。</Paragraph>
          <Paragraph type="secondary">同节点同指标按作答事件保留最大修订版本，只纳入已评分有效记录。待评分 {selectedStat.pendingEvents ?? 0} · 未作答 {selectedStat.unansweredEvents ?? 0} · 分数越界隔离 {selectedStat.quarantinedEvents ?? 0} · 超范围 {selectedStat.outOfScopeEvents ?? 0}，不计入得分或分母。</Paragraph>
          {hasBreakdown && <><Paragraph>共同题目：{panelCommonIds.join('、') || '无，无法同题比较'}；{panelCommon.items > 0 && panelCommon.full > 0 ? `得分和 ${panelCommon.score} / 满分和 ${panelCommon.full} = ${(panelCommon.score / panelCommon.full * 100).toFixed(2)}%；${panelCommon.events} 条有效作答 · ${panelCommon.items} 道共同题` : ''}</Paragraph><Paragraph type="secondary">仅本班有：{onlyMine.join('、') || '无'}；仅{className(otherClass)}有：{onlyOther.join('、') || '无'}。共同口径只有题目级计数，不能据此按学生去重。</Paragraph></>}
          {selectedStat.observedStudents != null && selectedStat.classSize != null && selectedStat.observedStudents < selectedStat.classSize && <Alert type="warning" showIcon message={`覆盖差异：仅 ${selectedStat.observedStudents}/${selectedStat.classSize} 人有记录`} description="结论仅限现有记录；未作答或缺记录不按零分处理。" />}
        </>}
        {isRealData() && <Collapse items={[{ key: 'anomalies', forceRender: true, label: `待核异常（不计入统计）· ${matchedAnomalies.length} 条`, children: matchedAnomalies.length ? matchedAnomalies.map((a) => <div className="teacher-list-row" key={a.record_id}><Text code>{a.record_id}</Text><Tag color={a.kind === 'quarantined' ? 'red' : 'orange'}>{a.kind === 'quarantined' ? '分数越界·隔离待核' : '超出测验范围·待核'}</Tag><Text>{a.score ?? '—'}/{a.full_score ?? '—'} · {a.reason}</Text></div>) : <Text type="secondary">该班此考点没有待核异常记录</Text> }]} />}
        {isRealData() && reconciliation && <Paragraph type="secondary">全量对账（两班全部测验）：原始行 {reconciliation.raw_rows} · 归并事件 {reconciliation.merged_events} · 原始状态 {Object.entries(reconciliation.raw_status).map(([k, v]) => `${k} ${v}`).join(' / ')} · 实际纳入统计 {reconciliation.included_scored_events} · 隔离 {reconciliation.quarantined_events} · 超范围 {reconciliation.out_of_scope_events}</Paragraph>}
      </>}
    </Drawer>
    <TextbookDrawer open={!!textbook} source={textbook} onClose={() => setTextbook(null)} />
  </div>;
};
export default ClassAnalyticsPage;
