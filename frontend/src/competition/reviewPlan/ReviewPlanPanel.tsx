import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Collapse, Drawer, Empty, Input, List, Modal, Radio, Select, Space, Spin, Table, Typography } from 'antd';
import { useSearchParams } from 'react-router-dom';
import { getBundle } from '../dataSource';
import type { PlanComparison, ReviewPlan, ReviewPlans } from '../dataSource';
import { readLiveEvidence } from '../livePretest/LivePretest';
import { getToken } from '../../api/authState';
import { getStudentName } from '../shared/ids';

const { Text, Paragraph, Title } = Typography;
const APPLIED_KEY = 'competition.reviewplan.applied.v1';
const PLAN_UI_KEY = 'competition.plan.ui.v1';
interface Applied { at: string; strategy: string; budget: number; scope?: string; changes: string[]; plan: ReviewPlan }
interface Recomputed { base: ReviewPlan; new: ReviewPlan; changes: string[]; audit: PlanComparison; serverAudit: PlanComparison }
function readAudit(key: string): Recomputed | null {
  try { return JSON.parse(sessionStorage.getItem(`competition.plan.audit:${key}`) || 'null'); } catch { return null; }
}
interface ExportReview {
  files: { name: string; bytes: number }[];
  review: { node_id: string; class_id: string; theme: string; budget: number; lecture_items: string[]; lecture_assignments: number; pretest_items: string[]; pretest_assignments: number };
  consistency: { matches_current: boolean; note: string };
}
function readApplied(): Applied | undefined {
  try { return JSON.parse(localStorage.getItem(APPLIED_KEY) || 'null') || undefined; } catch { return undefined; }
}
const displayReason = (text: string) => text.replace(/可定位失分点/g, '可定位失分观察项').replace(/已定位失分点/g, '已覆盖观察项').replace(/未覆盖失分点/g, '未覆盖观察项').replace(/预算外的失分点/g, '预算外的观察项');
const assignments = (p: ReviewPlan) => [
  ...p.lecture_items.flatMap((x) => x.lost_students.map((student) => ({ student, item_id: x.item_id, type: 'lecture' }))),
  ...p.pretest_tasks.flatMap((x) => x.students.map((student) => ({ student, item_id: x.item_id, type: 'pretest' }))),
];
const taskKey = (a: { student: string; item_id: string; type: string }) => `${a.student}|${a.item_id}|${a.type}`;
/** The server's base is official data, not necessarily the session plan already on screen. */
function compareApplied(before: ReviewPlan, after: ReviewPlan): PlanComparison {
  const a = new Map(assignments(before).map((x) => [taskKey(x), x]));
  const b = new Map(assignments(after).map((x) => [taskKey(x), x]));
  const added = [...b].filter(([k]) => !a.has(k)).map(([, x]) => x);
  const removed = [...a].filter(([k]) => !b.has(k)).map(([, x]) => x);
  // Mirror the pipeline identity and multiplicity: independent records must not cancel.
  type Point = ReviewPlan['uncovered']['lost_points'][number] & { record_id?: string; live?: boolean };
  const pointKey = (p: Point) => JSON.stringify([p.student, p.item, p.point_id, p.record_id || '', !!p.live]);
  const delta = (points: Point[], previous: Point[]) => {
    const counts = new Map<string, number>();
    previous.forEach((p) => counts.set(pointKey(p), (counts.get(pointKey(p)) || 0) + 1));
    return points.filter((p) => {
      const key = pointKey(p), count = counts.get(key) || 0;
      if (count) { counts.set(key, count - 1); return false; }
      return true;
    }).map((p) => ({ ...p, record_id: p.record_id || '', live: !!p.live }));
  };
  return { before: before.coverage_audit, after: after.coverage_audit,
    added_points: delta(after.uncovered.lost_points, before.uncovered.lost_points),
    removed_points: delta(before.uncovered.lost_points, after.uncovered.lost_points),
    assignments: { changed: !!(added.length || removed.length), added, removed, unchanged_count: [...a.keys()].filter((k) => b.has(k)).length },
    explanation: `与当前已显示方案比较：新增 ${added.length} 份、移除 ${removed.length} 份学生任务；官方答卷与统计不变。` };
}

const ReviewPlanPanel: React.FC<{
  quizId: string; classId: string; planTheme: string;
  onPlanThemeChange: (themeId: string) => void;
  onGoStudent: (studentId: string, questionId: string, indicator: string, recordId?: string) => void;
}> = ({ quizId, classId, planTheme, onPlanThemeChange, onGoStudent }) => {
  const bundle = getBundle();
  const rp = bundle?.reviewPlans;
  const topics = bundle?.topics || [];
  const [params, setParams] = useSearchParams();
  const readUi = () => {
    try { return JSON.parse(sessionStorage.getItem(PLAN_UI_KEY) || '{}'); } catch { return {}; }
  };
  const [strategy, setStrategy] = useState<'common' | 'pretest'>(() => {
    const url = params.get('reviewStrategy');
    return url === 'common' || url === 'pretest' ? url : readUi().strategy === 'pretest' ? 'pretest' : 'common';
  });
  const [budget, setBudget] = useState<number>(() => {
    const url = params.get('reviewBudget');
    return url === '2' ? 2 : url === '3' ? 3 : readUi().budget === 2 ? 2 : 3;
  });
  const paramsRef = useRef(params); paramsRef.current = params;
  useEffect(() => {
    try { sessionStorage.setItem(PLAN_UI_KEY, JSON.stringify({ budget, strategy })); } catch { /* storage unavailable */ }
    const next = new URLSearchParams(window.location.search);
    next.set('reviewBudget', String(budget)); next.set('planTheme', planTheme); next.set('reviewStrategy', strategy);
    next.set('quizId', quizId); next.set('classId', classId);
    if (next.toString() !== paramsRef.current.toString()) setParams(next, { replace: true });
  }, [budget, strategy, planTheme, quizId, classId, setParams]);

  const scopeKey = `${quizId}|${classId}|${planTheme}`;
  const requestKey = `${scopeKey}|${budget}|${strategy}`;
  const requestKeyRef = useRef(requestKey); requestKeyRef.current = requestKey;
  const sequence = useRef(0);
  useEffect(() => () => { ++sequence.current; }, []);
  const [retry, setRetry] = useState(0);
  const [live, setLive] = useState<{ key: string; data: ReviewPlans } | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [applied, setApplied] = useState<Applied | undefined>(readApplied);
  const [recomputed, setRecomputed] = useState<Recomputed | null>(() => readAudit(requestKey));
  const [notice, setNotice] = useState('');
  const viewKey = `competition.plan.view:${scopeKey}`;
  const savedView = () => {
    if (sessionStorage.getItem('competition.plan.autoRecompute') === '1') return {};
    try { return JSON.parse(sessionStorage.getItem(viewKey) || '{}'); } catch { return {}; }
  };
  const [studentsOpen, setStudentsOpen] = useState(() => !!savedView().studentsOpen);
  const [questionOpen, setQuestionOpen] = useState<string | null>(() => savedView().questionOpen || null);
  const [detailsOpen, setDetailsOpen] = useState(() => !!savedView().detailsOpen);
  const [search, setSearch] = useState('');
  useEffect(() => {
    try { sessionStorage.setItem(viewKey, JSON.stringify({ studentsOpen, questionOpen, detailsOpen })); } catch { /* storage unavailable */ }
  }, [viewKey, studentsOpen, questionOpen, detailsOpen]);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState('');
  const [exportReview, setExportReview] = useState<ExportReview | null>(null);
  const bundled = !!rp && quizId === rp.node_id && classId === rp.class_id && planTheme === rp.theme;
  const sourceData = bundled ? rp : live?.key === scopeKey ? live.data : undefined;
  const usingApplied = applied?.scope === scopeKey && applied.strategy === strategy && applied.budget === budget && !!applied.plan.workload;
  const plan = usingApplied ? applied?.plan : sourceData?.strategies[strategy]?.budgets[String(budget)];
  const planRef = useRef(plan); planRef.current = plan;

  const collectLive = () => {
    const out: { student_id: string; item_id: string; kind: string; record_id: string }[] = [];
    const inds = (bundle?.curriculumMap?.indicators ?? []).filter((x) => x.theme_id === planTheme).map((x) => x.indicator_id);
    for (const s of bundle?.students || []) {
      if (s.classId !== classId) continue;
      for (const ind of inds) for (const e of readLiveEvidence(s.id, quizId, ind)) {
        out.push({ student_id: s.id, item_id: e.questionId, kind: e.kind, record_id: e.id || `现场-${e.at}` });
      }
    }
    return out;
  };
  const fetchPlan = async (withEvidence: boolean) => {
    const token = getToken();
    const res = await fetch('/api/competition/review-plan/recompute', {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { 'X-User-Token': token } : {}) },
      body: JSON.stringify({ node_id: quizId, class_id: classId, theme: planTheme, budget, strategy, extra_evidence: withEvidence ? collectLive() : [] }),
    });
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.detail || `HTTP ${res.status}`); }
    return res.json() as Promise<{ plans: ReviewPlans; new_plans: ReviewPlans; base: ReviewPlan; new: ReviewPlan; changes: string[]; audit: PlanComparison }>;
  };
  useEffect(() => {
    const id = ++sequence.current;
    setBusy(false); setError(''); setNotice(''); setRecomputed(readAudit(requestKey)); setLoading(false);
    if (bundled || !rp || live?.key === scopeKey) return;
    let cancelled = false;
    const key = requestKey;
    setLoading(true);
    void fetchPlan(false).then((p) => {
      if (!cancelled && sequence.current === id && requestKeyRef.current === key) setLive({ key: scopeKey, data: p.plans });
    }).catch((e: unknown) => {
      if (!cancelled && sequence.current === id) setError(`生成方案失败：${e instanceof Error ? e.message : String(e)}`);
    }).finally(() => { if (!cancelled && sequence.current === id) setLoading(false); });
    return () => { cancelled = true; };
    // Every request captures scope AND policy; stale responses cannot overwrite a newer selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey, bundled, retry]);

  const applyPlan = (next: ReviewPlan, changes: string[]) => {
    const a: Applied = { at: new Date().toISOString(), scope: scopeKey, budget, strategy, changes, plan: next };
    localStorage.setItem(APPLIED_KEY, JSON.stringify(a)); setApplied(a);
  };
  async function recompute(auto = false) {
    const key = requestKey; const id = ++sequence.current;
    const before = planRef.current;
    setBusy(true); setLoading(false); setError('');
    try {
      const payload = await fetchPlan(true);
      if (requestKeyRef.current !== key || sequence.current !== id) return;
      const audit = before ? compareApplied(before, payload.new) : payload.audit;
      const message = audit.assignments.changed
        ? `已记录本次练习，新增 ${audit.assignments.added.length} 份、移除 ${audit.assignments.removed.length} 份任务`
        : '已记录本次练习，讲评安排不变';
      setLive({ key: scopeKey, data: payload.plans });
      const result: Recomputed = { base: before || payload.base, new: payload.new, changes: payload.changes, audit, serverAudit: payload.audit };
      setRecomputed(result);
      try { sessionStorage.setItem(`competition.plan.audit:${key}`, JSON.stringify(result)); } catch { /* storage unavailable */ }
      if (auto) { applyPlan(payload.new, payload.changes); setNotice(message); sessionStorage.removeItem('competition.plan.autoRecompute'); }
      else setDetailsOpen(true);
    } catch (e) {
      if (requestKeyRef.current === key && sequence.current === id) setError(`重算失败：${e instanceof Error ? e.message : String(e)}；可重试，原方案未变。`);
    } finally { if (sequence.current === id) setBusy(false); }
  }
  const autoAttempt = useRef('');
  useEffect(() => {
    if (!plan || loading || sessionStorage.getItem('competition.plan.autoRecompute') !== '1' || autoAttempt.current === requestKey) return;
    autoAttempt.current = requestKey;
    void recompute(true);
    return () => { if (autoAttempt.current === requestKey) autoAttempt.current = ''; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey, plan, loading]);

  const reviewExport = async () => {
    setExportOpen(true); setExportBusy(true); setExportError(''); setExportReview(null);
    try {
      const token = getToken();
      const res = await fetch('/api/competition/export-review', { method: 'GET', cache: 'no-store', headers: token ? { 'X-User-Token': token } : {} });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setExportReview(await res.json());
    } catch (e) { setExportError(`成果包复核失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setExportBusy(false); }
  };
  const plan3 = sourceData?.strategies[strategy]?.budgets['3'];
  const removedIds = budget === 2 ? (strategy === 'common' ? plan3?.lecture_items.map((x) => x.item_id) : plan3?.pretest_tasks.map((x) => x.item_id))?.filter((id) => !(strategy === 'common' ? plan?.lecture_items : plan?.pretest_tasks)?.some((x) => x.item_id === id)) || [] : [];
  const removedStudents = new Set(plan3 ? assignments(plan3).filter((a) => a.type === (strategy === 'common' ? 'lecture' : 'pretest') && removedIds.includes(a.item_id)).map((a) => a.student) : []);
  const currentTasks = plan ? [...new Map(assignments(plan).map((a) => [taskKey(a), a])).values()] : [];
  const assignedStudents = new Set(currentTasks.map((a) => a.student));
  const emptied = [...removedStudents].filter((s) => !assignedStudents.has(s));
  const impactLine = removedIds.length ? `少${strategy === 'common' ? '讲' : '测'} ${removedIds.join('、')}，${emptied.length ? `${emptied.join('、')} 需要另作安排` : `${removedStudents.size} 人仍有安排`}` : '';
  const impactAnswers = (plan3?.lecture_items || []).filter((item) => removedIds.includes(item.item_id)).flatMap((item) =>
    [...new Set(item.lost_students)].filter((student) => (emptied.length ? emptied.includes(student) : removedStudents.has(student))).flatMap((student) => {
      const point = item.lost_points.find((p) => p.student === student && p.record_id);
      return point ? [{ student, itemId: item.item_id, indicator: item.indicator, recordId: point.record_id }] : [];
    }));
  const roster = useMemo(() => {
    if (!plan) return [];
    const ids = new Set([...currentTasks.map((a) => a.student), ...removedStudents, ...plan.uncovered.insufficient_students.map((s) => s.student), ...plan.uncovered.lost_points.map((p) => p.student)]);
    return [...ids].sort().map((student) => ({ student, tasks: currentTasks.filter((a) => a.student === student) }));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan, plan3, budget]);
  const indicatorOf = (id: string) => [...(plan?.lecture_items || []), ...(plan?.pretest_tasks || []), ...(plan3?.lecture_items || [])].find((x) => x.item_id === id)?.indicator || bundle?.questions.find((q) => q.id === id)?.kpId || '';
  const choices = strategy === 'common' ? (plan?.lecture_items || []).map((x) => ({ id: x.item_id, students: x.lost_students, reason: [...new Set(x.lost_points.map((p) => p.label))].slice(0, 2).join('；') || displayReason(x.rank_reason) }))
    : (plan?.pretest_tasks || []).map((x) => ({ id: x.item_id, students: x.students, reason: x.observe || displayReason(x.rank_reason || '') }));
  const cmp = sourceData?.comparison[String(budget)];
  const selectedQuestion = plan?.lecture_items.find((x) => x.item_id === questionOpen);
  if (!rp) return <section className="teacher-section" data-testid="review-plan"><Title level={4}>建议先讲</Title><Text type="secondary">当前演示数据没有讲评方案；可在下方查看易错题与答卷，不调用官方重算。</Text></section>;
  return <section className="teacher-section" data-testid="review-plan">
    <div className="teacher-section-heading"><Title level={4} style={{ margin: 0 }}>建议先讲</Title><Space><Text>{strategy === 'common' ? '讲评题量' : '补测题量'}</Text><Radio.Group aria-label="方案题目预算" value={budget} onChange={(e) => setBudget(e.target.value)} options={[{ label: '2 道', value: 2 }, { label: '3 道', value: 3 }]} optionType="button" size="small" /></Space></div>
    <div className="teacher-plan-scope"><Text type="secondary">仅讲评所选主题：</Text><Select aria-label="讲评主题" value={planTheme} onChange={onPlanThemeChange} options={topics.map((t) => ({ value: t.id, label: t.name }))} style={{ width: 170 }} />{usingApplied && <Text type="secondary">含本次练习记录</Text>}</div>
    {notice && <p className="teacher-result-note" role="status">{notice}</p>}
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => { if (plan) void recompute(sessionStorage.getItem('competition.plan.autoRecompute') === '1'); else setRetry((n) => n + 1); }}>重试</Button>} />}
    {(loading || busy) && <Space role="status"><Spin size="small" /><Text>{busy ? '正在重算安排…' : '正在生成该范围方案…'}</Text></Space>}
    {!loading && !plan && !error && <Empty description="该范围暂无可用方案" />}
    {plan && <>
      {choices.length === 0 && <Empty description="该主题没有可安排的题目" />}
      {choices.map((x) => <div className="teacher-list-row" key={x.id} data-testid={`${strategy === 'common' ? 'lecture' : 'pretest'}-choice-${x.id}`}>
        <Text strong>{x.id}</Text><div style={{ flex: 1, minWidth: 0 }}><div>{bundle?.questions.find((q) => q.id === x.id)?.stem || '暂无题干'}</div><Text type="secondary">{strategy === 'common' ? '重点讲：' : '补观察：'}{x.reason}</Text></div>
        <Button type="link" onClick={() => setQuestionOpen(x.id)}>{new Set(x.students).size} 人{strategy === 'common' ? '失分' : '待观察'} · 看失分情况</Button>
      </div>)}
      {impactLine && <div data-testid="budget-tradeoff" className="teacher-muted">{impactLine}{impactAnswers.map((a) => <Button key={`${a.student}|${a.itemId}|${a.recordId}`} type="link" aria-label={`查看答卷 ${a.student} ${quizId} ${a.itemId}`} onClick={() => onGoStudent(a.student, a.itemId, a.indicator, a.recordId)}>查看答卷{impactAnswers.length > 1 ? ` · ${getStudentName(a.student)} ${a.itemId}` : ''}</Button>)}</div>}
      <div className="teacher-actions"><Button onClick={() => setStudentsOpen(true)}>看学生安排（{assignedStudents.size} 人）</Button><Button type="text" onClick={() => setDetailsOpen(true)}>分析详情</Button></div>
    </>}
    {!plan && <Button onClick={() => setDetailsOpen(true)}>分析详情</Button>}
    <Drawer title="看失分情况" open={!!questionOpen} onClose={() => setQuestionOpen(null)} width={620}
      extra={<Button type="text" onClick={() => setQuestionOpen(null)}>返回题单</Button>}>
      <Title level={5}>{questionOpen} · {bundle?.questions.find((q) => q.id === questionOpen)?.stem}</Title>
      {selectedQuestion ? <List dataSource={[...new Set(selectedQuestion.lost_points.map((p) => p.student))]} locale={{ emptyText: '暂无可定位的失分观察项' }} renderItem={(student) => {
        const points = selectedQuestion.lost_points.filter((p) => p.student === student);
        return <List.Item><Button type="link" onClick={() => onGoStudent(student, selectedQuestion.item_id, selectedQuestion.indicator, points[0]?.record_id)}>{getStudentName(student)}</Button><span>{[...new Set(points.map((p) => p.label))].join('；')}</span></List.Item>;
      }} />
        : <List dataSource={plan?.pretest_tasks.find((x) => x.item_id === questionOpen)?.students || []} renderItem={(s) => <List.Item><Button type="link" onClick={() => onGoStudent(s, questionOpen || '', indicatorOf(questionOpen || ''))}>{getStudentName(s)} · 查看现有答卷</Button></List.Item>} />}
    </Drawer>
    <Drawer title={`学生安排 · 当前任务去重 ${assignedStudents.size} 人`} open={studentsOpen} onClose={() => setStudentsOpen(false)} width={740}>
      <Input.Search aria-label="搜索学生安排" placeholder="按学生姓名或编号搜索" value={search} onChange={(e) => setSearch(e.target.value)} allowClear />
      <Paragraph type="secondary">讲评与补测名单合并显示；同一学生只占一行。补测分组按题号标注，不占讲评题量；受影响但已无任务的学生仍保留。</Paragraph>
      <Paragraph>全班补测分组：{plan?.pretest_tasks.map((t) => `${t.item_id}（${new Set(t.students).size} 人，每生 1 题）：${t.observe}`).join('；') || '无'}</Paragraph>
      <List dataSource={roster.filter((r) => `${r.student} ${getStudentName(r.student)}`.toLowerCase().includes(search.trim().toLowerCase()))} renderItem={(r) => <List.Item><div><Text strong>{getStudentName(r.student)}</Text><div>{r.tasks.length ? r.tasks.map((a) => <Button key={taskKey(a)} type="link" onClick={() => onGoStudent(r.student, a.item_id, indicatorOf(a.item_id))}>{a.type === 'lecture' ? '讲评' : '补测组'} {a.item_id}</Button>) : <Text type="warning">暂无任务，需要教师另作安排</Text>}</div>{removedStudents.has(r.student) && <Text type="secondary">少讲题目的原名单；{r.tasks.length ? `仍有 ${r.tasks.length} 份任务` : '任务已空'}。</Text>}</div></List.Item>} />
      <Text type="secondary">任务量按学生 × 题 × 类型计；当前 {currentTasks.length} 份任务。</Text>
    </Drawer>
    <Drawer title="分析详情" open={detailsOpen} onClose={() => setDetailsOpen(false)} width={820}>
      <Space wrap className="teacher-actions"><Text>讲评主题</Text><Select aria-label="方案主题" value={planTheme} onChange={onPlanThemeChange} style={{ width: 200 }} options={topics.map((t) => ({ value: t.id, label: t.name }))} /><Radio.Group value={strategy} onChange={(e) => setStrategy(e.target.value)} options={[{ label: '优先讲共性失分', value: 'common' }, { label: '优先补测', value: 'pretest' }]} optionType="button" /><Button loading={busy} onClick={() => void recompute()}>用现场证据重算</Button><Button onClick={reviewExport}>成果包复核</Button>{usingApplied && <Button danger onClick={() => { localStorage.removeItem(APPLIED_KEY); setApplied(undefined); setRecomputed(null); setNotice('已撤销会话方案，恢复原始建议'); }}>撤销会话方案</Button>}</Space>
      <Paragraph type="secondary">会话练习与官方记录分开；应用不修改官方答卷、评分或班级统计。</Paragraph>
      {plan && <>
        <Table size="small" pagination={false} rowKey="name" dataSource={[
          { name: '观察项总数（分母）', value: plan.coverage_audit?.total_points }, { name: '已覆盖观察项', value: plan.covered_lost_points }, { name: '未覆盖观察项', value: plan.uncovered.lost_points.length }, { name: '未安排补测学生', value: plan.uncovered.insufficient_students.length }, { name: '覆盖学生（名单去重）', value: assignedStudents.size }, { name: '总任务（人题类型）', value: currentTasks.length },
        ]} columns={[{ title: '观察项 = 学生 × 题 × 评分点，不是得分', dataIndex: 'name' }, { title: '数量', dataIndex: 'value' }]} />
        <Collapse items={[{ key: 'uncovered', label: '未覆盖观察项与待安排原因', children: <><List dataSource={plan.uncovered.lost_points} renderItem={(p) => <List.Item>{p.student} · {p.item} · {p.point_id} · {p.label}</List.Item>} />{plan.uncovered.reasons.map((r) => <Paragraph key={r}>{displayReason(r)}</Paragraph>)}<Text>证据不足：{plan.uncovered.insufficient_students.map((s) => `${s.student}（${s.indicator} ${s.effective}/${s.min}）`).join('、') || '无'}</Text></> }]} />
      </>}
      {recomputed && <div data-testid="plan-recompute">
        <Title level={5}>本次重算审计（相对当前已显示方案）</Title>
        <Alert type={recomputed.audit.assignments.changed ? 'warning' : 'success'} message={recomputed.audit.assignments.changed ? `新增 ${recomputed.audit.assignments.added.length} 份、移除 ${recomputed.audit.assignments.removed.length} 份任务` : '已记录本次练习，讲评安排不变'} description={recomputed.audit.explanation} />
        <Table size="small" pagination={false} rowKey="metric" dataSource={(['total_points', 'covered_points', 'uncovered_points', 'official_points', 'live_points'] as const).map((key, i) => ({ metric: ['观察项总数（分母）', '已覆盖', '未覆盖', '官方记录观察', '现场新增观察'][i], before: recomputed.audit.before[key], after: recomputed.audit.after[key] }))} columns={[{ title: '观察项', dataIndex: 'metric' }, { title: '重算前', dataIndex: 'before' }, { title: '重算后', dataIndex: 'after' }]} />
        <List header="任务增删" dataSource={[...recomputed.audit.assignments.added.map((a) => ({ ...a, op: '新增' })), ...recomputed.audit.assignments.removed.map((a) => ({ ...a, op: '移除' }))]} renderItem={(a) => <List.Item>{a.op}：{a.student} / {a.item_id} / {a.type === 'lecture' ? '讲评' : '补测'}</List.Item>} />
        <List header="未覆盖观察项增删" dataSource={[...recomputed.audit.added_points.map((p) => ({ ...p, op: '新增' })), ...recomputed.audit.removed_points.map((p) => ({ ...p, op: '移除' }))]} renderItem={(p) => <List.Item>{p.op} · {p.student} / {p.item} / {p.point_id} · {p.label} · {p.record_id}</List.Item>} />
        <Collapse items={[{ key: 'server', label: '接口原始审计（相对官方基线，非本次会话变化）', children: <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(recomputed.serverAudit, null, 2)}</pre> }]} />
        <Space><Button onClick={() => { applyPlan(recomputed.new, recomputed.changes); setNotice(recomputed.audit.assignments.changed ? `已应用：新增 ${recomputed.audit.assignments.added.length} 份、移除 ${recomputed.audit.assignments.removed.length} 份任务` : '已记录本次练习，讲评安排不变'); }}>应用到当前会话</Button><Button onClick={() => setRecomputed(null)}>关闭本次审计</Button></Space>
      </div>}
      {cmp && <><Title level={5}>按失分人数选题 vs 共性失分方案</Title><Paragraph type="secondary">以下为原始方案对照，不代表已应用会话或补测优先策略。讲评预算同为 {budget} 道，额外补测单列，非同总预算。</Paragraph><Table size="small" pagination={false} rowKey="k" dataSource={[
        { k: '讲评题', a: cmp.naive.lecture_items.join('、'), b: cmp.plan.lecture_items.join('、') }, { k: '覆盖学生', a: cmp.naive.covered_students.length, b: cmp.plan.covered_students.length }, { k: '已覆盖观察项', a: cmp.naive.covered_lost_points, b: cmp.plan.covered_lost_points }, { k: '可定位观察项', a: cmp.naive.located_points, b: cmp.plan.located_points },
        ...(['lecture_assignments', 'pretest_questions', 'pretest_assignments', 'total_assignments'] as const).map((k, i) => ({ k: ['讲评任务', '额外补测题数', '额外补测任务', '总任务'][i], a: cmp.naive_plan?.workload?.[k] ?? '—', b: sourceData?.strategies.common?.budgets[String(budget)]?.workload?.[k] ?? '—' })),
      ]} columns={[{ title: '指标', dataIndex: 'k' }, { title: '按失分人数', dataIndex: 'a' }, { title: '共性失分方案', dataIndex: 'b' }]} /><Paragraph>{displayReason(cmp.tradeoff)}</Paragraph></>}
    </Drawer>
    <Modal title="成果包复核" open={exportOpen} onCancel={() => setExportOpen(false)} width={720} footer={<Space><Button loading={exportBusy} onClick={reviewExport}>重新复核</Button><Button onClick={() => setExportOpen(false)}>关闭</Button></Space>}>
      <div data-testid="export-review" aria-busy={exportBusy}>{exportBusy && <Spin />}{exportError && <Alert type="error" message={exportError} />}{exportReview && <><Alert type={exportReview.consistency.matches_current ? 'success' : 'warning'} message={exportReview.consistency.matches_current ? '磁盘成果包与当前系统建议一致' : '磁盘成果包与当前系统建议不一致'} description={exportReview.consistency.note} /><Table size="small" rowKey="name" pagination={false} dataSource={exportReview.files} columns={[{ title: '磁盘文件', dataIndex: 'name' }, { title: 'bytes', dataIndex: 'bytes' }]} /><Paragraph>节点 {exportReview.review.node_id} / 班级 {exportReview.review.class_id} / 主题 {exportReview.review.theme}；预算 {exportReview.review.budget} 道</Paragraph><Paragraph>讲评：{exportReview.review.lecture_items.join('、')}（{exportReview.review.lecture_assignments} 人题）；补测：{exportReview.review.pretest_items.join('、')}（{exportReview.review.pretest_assignments} 人题）</Paragraph><Text type="secondary">来自接口读取的磁盘成果包，不以页面预算或会话应用替代。</Text></>}</div>
    </Modal>
  </section>;
};
export default ReviewPlanPanel;
