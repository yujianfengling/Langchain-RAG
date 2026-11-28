import { canUseExternalAi, LOCAL_DATA_NOTICE } from '../shared/externalAi';
import { questionLabel } from '../shared/teacherContext';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Collapse, Drawer, Empty, Input, List, Modal, Radio, Select, Space, Spin, Table, Tooltip, Typography } from 'antd';
import { AimOutlined, BulbOutlined, CheckCircleOutlined, InfoCircleOutlined, RobotOutlined } from '@ant-design/icons';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { assistantPath } from '../shared/teacherContext';
import { listAllQuestions, listAnswersByQuestion } from '../studentAnswers/data';
import { getBundle, getDatasetId } from '../dataSource';
import type { PlanComparison, ReviewPlan, ReviewPlans } from '../dataSource';
import { readLiveEvidence } from '../livePretest/LivePretest';
import { getToken } from '../../api/authState';
import { getStudentName } from '../shared/ids';
import { printedPageLabel } from '../shared/textbookPage';
import TeachingSourceLink from '../curriculum/TeachingSourceLink';
import './reviewPlanEnhance.css';

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
/** 讲评规划结论层：一个薄弱考点 = 一个讲评环节（讲什么 / 怎么讲 / 怎么确认），全部由当前方案与 bundle 现算 */
interface ReviewSegment {
  indicator: string; name: string; rate: number | null; rateText: string; lostCount: number;
  typicalId: string; itemIds: string[]; reasons: string[]; mismatchFact: string;
  textbook: string; suggestion: string;
  pretest: { itemId: string; count: number; observe: string } | null;
  wrongFacts: string[]; sampleStems: string[];
  fallback: { id: string; stem: string }[];
}
interface AiQuestion { stem: string; answer: string; analysis: string; difficulty: string }
interface AiState { busy: boolean; error: string; questions: AiQuestion[] | null }
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
  onFollowUp: (studentId: string, questionId: string, indicator: string, recordId?: string) => void;
}> = ({ quizId, classId, planTheme, onPlanThemeChange, onGoStudent, onFollowUp }) => {
  const bundle = getBundle();
  const rp = bundle?.reviewPlans;
  const topics = bundle?.topics || [];
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const externalAi = canUseExternalAi();
  const readUi = () => {
    try { return JSON.parse(sessionStorage.getItem(PLAN_UI_KEY) || '{}'); } catch { return {}; }
  };
  const [strategy, setStrategy] = useState<'common' | 'pretest'>(() => {
    const url = params.get('reviewStrategy');
    return url === 'common' || url === 'pretest' ? url : readUi().strategy === 'pretest' ? 'pretest' : 'common';
  });
  const [budget, setBudget] = useState<number>(() => {
    const url = params.get('reviewBudget');
    return url === '2' ? 2 : url === '3' ? 3 : readUi().budget === 3 ? 3 : 2;
  });
  const paramsRef = useRef(params); paramsRef.current = params;
  useEffect(() => {
    try { sessionStorage.setItem(PLAN_UI_KEY, JSON.stringify({ budget, strategy })); } catch { /* storage unavailable */ }
    const next = new URLSearchParams(window.location.search);
    next.set('reviewBudget', String(budget)); next.set('planTheme', planTheme); next.set('reviewStrategy', strategy);
    next.set('quizId', quizId); next.set('classId', classId);
    if (next.toString() !== paramsRef.current.toString()) setParams(next, { replace: true });
  }, [budget, strategy, planTheme, quizId, classId, setParams]);

  const scopeKey = `${getDatasetId()}|${quizId}|${classId}|${planTheme}`;
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
  const [ai, setAi] = useState<Record<string, AiState>>({});
  const aiSeq = useRef<Record<string, number>>({});
  const viewKey = `competition.plan.view:${scopeKey}`;
  const savedView = () => {
    if (sessionStorage.getItem('competition.plan.autoRecompute') === '1') return {};
    try { return JSON.parse(sessionStorage.getItem(viewKey) || '{}'); } catch { return {}; }
  };
  const [studentsOpen, setStudentsOpen] = useState(() => !!savedView().studentsOpen);
  const [questionOpen, setQuestionOpen] = useState<string | null>(() => savedView().questionOpen || null);
  const [choosingFollowUp, setChoosingFollowUp] = useState(false);
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
      body: JSON.stringify({ node_id: quizId, class_id: classId, theme: planTheme, budget, strategy, dataset: getDatasetId(), extra_evidence: withEvidence ? collectLive() : [] }),
    });
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.detail || `HTTP ${res.status}`); }
    return res.json() as Promise<{ plans: ReviewPlans; new_plans: ReviewPlans; base: ReviewPlan; new: ReviewPlan; changes: string[]; audit: PlanComparison }>;
  };
  useEffect(() => {
    const id = ++sequence.current;
    setBusy(false); setError(''); setNotice(''); setRecomputed(readAudit(requestKey)); setLoading(false); setAi({});
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
  const impactLine = removedIds.length ? `少${strategy === 'common' ? '讲' : '测'} ${removedIds.map(questionLabel).join('、')}，${emptied.length ? `${emptied.join('、')} 需要另作安排` : `${removedStudents.size} 人仍有安排`}` : '';
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
  // 讲评环节：按本次薄弱考点（得分率低→高）排序，最多 3 个；数字与名单全部现算，不写死。
  const segments = useMemo<ReviewSegment[]>(() => {
    if (!plan || !bundle) return [];
    const classStudents = new Set(bundle.students.filter((s) => s.classId === classId).map((s) => s.id));
    const indIds = [...new Set([...plan.lecture_items.map((x) => x.indicator), ...plan.pretest_tasks.map((x) => x.indicator)])].filter(Boolean);
    const usedIds = new Set([...plan.lecture_items.map((x) => x.item_id), ...plan.pretest_tasks.map((x) => x.item_id)]);
    const segs = indIds.map((ind): ReviewSegment => {
      const lecture = plan.lecture_items.filter((x) => x.indicator === ind);
      const pretests = plan.pretest_tasks.filter((x) => x.indicator === ind);
      const indQuestions = bundle.questions.filter((q) => q.kpId === ind);
      const qIds = new Set(indQuestions.map((q) => q.id));
      const answers = bundle.answers.filter((a) => a.quizId === quizId && qIds.has(a.questionId) && classStudents.has(a.studentId));
      const lost = new Set(lecture.flatMap((x) => x.lost_students));
      answers.forEach((a) => { if (a.score < a.fullScore) lost.add(a.studentId); });
      const kp = bundle.kps.find((k) => {
        const withQuiz = k as typeof k & { quizId?: string };
        return (k.id === ind || k.id === `${quizId}-${ind}`) && (!withQuiz.quizId || withQuiz.quizId === quizId);
      });
      const stat = kp?.stats.find((s) => s.classId === classId);
      let rate = stat?.scoreRate ?? null;
      if (rate == null && answers.length) {
        const full = answers.reduce((t, a) => t + a.fullScore, 0);
        rate = full ? answers.reduce((t, a) => t + a.score, 0) / full : null;
      }
      const rateText = rate != null ? `${(rate * 100).toFixed(1)}%` : (stat?.status || '记录不足');
      const indicator = bundle.curriculumMap?.indicators.find((i) => i.indicator_id === ind);
      const name = kp?.name || indicator?.requirement || ind;
      const reasons = [...new Set(lecture.flatMap((x) => x.lost_points.map((p) => p.label)))].slice(0, 2);
      const mismatch = (bundle.diagnoses?.diagnoses || [])
        .filter((d) => d.node_id === quizId && d.indicator_id === ind && classStudents.has(d.student_id))
        .flatMap((d) => d.question_findings.filter((f) => f.kind === '过程与填答不一致'))[0];
      const itemIds = (lecture.length ? lecture.map((x) => x.item_id) : pretests.map((x) => x.item_id));
      const typicalId = itemIds[0] || indQuestions[0]?.id || '';
      const pageRef = indicator?.textbook_pages?.[0];
      const textbook = pageRef ? printedPageLabel(pageRef.printed) : (bundle.questions.find((q) => q.id === typicalId)?.sourceLocation || '教材页待定位');
      const suggestion = mismatch
        ? `先对照${questionLabel(mismatch.item_id)}的过程末行与填答栏，再回到教材例页规范书写。`
        : reasons[0]
          ? `围绕「${reasons[0]}」板演${questionLabel(typicalId)}，让学生说清每一步依据。`
          : '结合教材例页重讲基本步骤，再让学生独立完成同型题。';
      const pretest = pretests[0] ? { itemId: pretests[0].item_id, count: new Set(pretests[0].students).size, observe: pretests[0].observe } : null;
      const wrongFacts = [...reasons, ...(mismatch ? [mismatch.fact] : []), ...lecture.map((x) => displayReason(x.rank_reason))].filter(Boolean).slice(0, 5);
      const sampleStems = [...new Set([typicalId, ...indQuestions.map((q) => q.id)])]
        .map((id) => bundle.questions.find((q) => q.id === id)?.stem || '').filter(Boolean).slice(0, 3);
      const unused = indQuestions.filter((q) => !usedIds.has(q.id));
      const fallback = (unused.length ? unused : indQuestions).slice(0, 3).map((q) => ({ id: q.id, stem: q.stem }));
      return { indicator: ind, name, rate, rateText, lostCount: lost.size, typicalId, itemIds, reasons,
        mismatchFact: mismatch?.fact || '', textbook, suggestion, pretest, wrongFacts, sampleStems, fallback };
    });
    return segs.sort((a, b) => (a.rate ?? 1.01) - (b.rate ?? 1.01) || b.lostCount - a.lostCount).slice(0, 3);
  }, [plan, bundle, quizId, classId]);
  const generateVariants = async (seg: ReviewSegment) => {
    if (!externalAi) return;
    const key = seg.indicator; const scope = requestKey;
    const id = (aiSeq.current[key] || 0) + 1; aiSeq.current[key] = id;
    setAi((m) => ({ ...m, [key]: { busy: true, error: '', questions: null } }));
    const fresh = () => requestKeyRef.current === scope && aiSeq.current[key] === id;
    try {
      const res = await fetch('/api/competition/ai-questions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-User-Token': getToken() || '' },
        body: JSON.stringify({ dataset: getDatasetId(), indicator_id: seg.indicator, indicator_name: seg.name, quiz_id: quizId, class_id: classId,
          wrong_facts: seg.wrongFacts, sample_stems: seg.sampleStems, count: 2 }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json() as { questions?: AiQuestion[] };
      const questions = (data.questions || []).filter((q) => q && q.stem);
      if (!questions.length) throw new Error('模型返回为空');
      if (fresh()) setAi((m) => ({ ...m, [key]: { busy: false, error: '', questions } }));
    } catch (e) {
      if (fresh()) setAi((m) => ({ ...m, [key]: { busy: false, error: e instanceof Error ? e.message : String(e), questions: null } }));
    }
  };
  const selectedQuestion = plan?.lecture_items.find((x) => x.item_id === questionOpen);
  if (!rp) {
    const examples = listAllQuestions().flatMap(question => {
      const answers = listAnswersByQuestion(question.id, { quizId, classId }).filter(a => a.score < a.fullScore);
      return answers.length ? [{ question, answers }] : [];
    }).sort((a, b) => new Set(b.answers.map(x => x.studentId)).size - new Set(a.answers.map(x => x.studentId)).size);
    return <section className="teacher-section" data-testid="review-plan">
      <Title level={4}>本次讲评</Title>
      <Text type="secondary">按当前班级已有失分答卷选题</Text>
      <div><Text type="secondary">环节按考点组织（顺序号非题号）；本节课讲哪几道以下方题单为准（预算 {budget} 道）。</Text></div>
      {examples.slice(0, budget).map(({ question, answers }) => <details key={question.id}>
        <summary>{question.stem} · {new Set(answers.map(a => a.studentId)).size} 人失分</summary>
        {answers.map(a => <Button key={a.id} type="link" onClick={() => onGoStudent(a.studentId, question.id, question.kpId)}>
          {getStudentName(a.studentId)} · 得分 {a.score}/{a.fullScore}
        </Button>)}
      </details>)}
      {!examples.length && <Empty description="当前测验和班级没有可供讲评的失分答卷" />}
    </section>;
  }
  return <section className="teacher-section" data-testid="review-plan">
    <div className="teacher-section-heading"><Title level={4} style={{ margin: 0 }}>本次讲评</Title><Space><Text>{strategy === 'common' ? '讲评题量' : '补测题量'}</Text><Radio.Group aria-label="方案题目预算" value={budget} onChange={(e) => setBudget(e.target.value)} options={[{ label: '2 道', value: 2 }, { label: '3 道', value: 3 }]} optionType="button" size="small" /></Space></div>
    <div className="teacher-plan-scope"><Text type="secondary">讲评主题 </Text><Select aria-label="讲评主题" value={planTheme} onChange={onPlanThemeChange} options={topics.map((t) => ({ value: t.id, label: t.name }))} style={{ width: 170 }} />{usingApplied && <Text type="secondary">含本次练习记录</Text>}</div>
    {notice && <p className="teacher-result-note" role="status">{notice}</p>}
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => { if (plan) void recompute(sessionStorage.getItem('competition.plan.autoRecompute') === '1'); else setRetry((n) => n + 1); }}>重试</Button>} />}
    {(loading || busy) && <Space role="status"><Spin size="small" /><Text>{busy ? '正在重算安排…' : '正在生成该范围方案…'}</Text></Space>}
    {!loading && !plan && !error && <Empty description="该范围暂无可用方案" />}
    {plan && <>
      {segments.length > 0 && <div className="rp-segments" data-testid="review-plan-segments">
        <div className="rp-segments-head">
          <Title level={5} style={{ margin: 0 }}>讲评规划 · 这节课讲什么</Title>
          <Tooltip title="得分率 = 本班该考点有效作答得分 ÷ 满分；失分人数按方案名单与答卷去重；环节按得分率从低到高排序，最多 3 个。数据来自本次测验记录与当前方案，下方题组为预算明细。">
            <InfoCircleOutlined className="rp-segments-info" tabIndex={0} aria-label="讲评规划口径说明" />
          </Tooltip>
          <Button size="small" type="primary" ghost icon={<RobotOutlined />} onClick={() => navigate(assistantPath(params, {
            classId, quizId, fromExam: true, prompt: '请根据本班本次测验的薄弱考点生成40分钟讲评教案，含分层任务与教材出处。',
          }))} disabled={!externalAi} data-testid="jump-assistant-plan">AI 生成教案</Button>
          {!externalAi && <Text type="secondary">{LOCAL_DATA_NOTICE}</Text>}
        </div>
        {segments.map((s, i) => {
          const st = ai[s.indicator];
          return <article className="rp-segment" key={s.indicator} data-testid={`review-segment-${s.indicator}`}>
            <header className="rp-segment-head">
              <span className="rp-segment-index" title="讲评环节顺序，不是题号；题号见「典型错题」">环节 {i + 1}</span>
              <AimOutlined className="rp-segment-icon" />
              <div className="rp-segment-title">
                <Text strong>{s.name}</Text>
                <span className="rp-segment-meta">本班得分率 <b>{s.rateText}</b> · {s.lostCount} 人失分</span>
              </div>
            </header>
            <div className="rp-segment-body">
              <div className="rp-segment-point"><BulbOutlined /><div>
                <span className="rp-point-label">怎么讲</span>
                <span>典型错题 </span>
                {s.itemIds.map((id) => <Button key={id} type="link" size="small" onClick={() => { setChoosingFollowUp(false); setQuestionOpen(id); }}>{questionLabel(id)}</Button>)}
                {s.reasons.length > 0 && <div>错因：{s.reasons.join('；')}</div>}
                {s.mismatchFact && <div>过程与填答差异：{s.mismatchFact}</div>}
                <div>教材出处：{s.textbook}{s.typicalId && <> · <TeachingSourceLink questionId={s.typicalId} quizId={quizId} classId={classId} /></>}</div>
                <div className="rp-segment-suggestion">讲法建议：{s.suggestion}</div>
              </div></div>
              <div className="rp-segment-point"><CheckCircleOutlined /><div>
                <span className="rp-point-label">怎么确认</span>
                <div>当堂追问：请学生复述{questionLabel(s.typicalId)}的关键步骤并说明依据{s.typicalId && <Button type="link" size="small" onClick={() => { setChoosingFollowUp(false); setQuestionOpen(s.typicalId); }}>看典型答卷</Button>}</div>
                <div>分层补测：{s.pretest
                  ? <>{questionLabel(s.pretest.itemId)} · {s.pretest.count} 人（{s.pretest.observe}）<Button type="link" size="small" onClick={() => setStudentsOpen(true)}>分层补测名单</Button></>
                  : '本环节暂无补测分组，可在分析详情中用现场证据重算后补充'}</div>
              </div></div>
            </div>
            <footer className="rp-segment-foot">
              <Button size="small" icon={<RobotOutlined />} disabled={!externalAi} loading={st?.busy} onClick={() => void generateVariants(s)}>AI 出变式题</Button>
              {st?.busy && <span className="rp-ai-busy" role="status"><Spin size="small" />AI 出题中…</span>}
              {st?.questions && <div className="rp-ai-result" data-testid={`ai-variants-${s.indicator}`}>
                <span className="rp-ai-note">AI 生成 · 需教师核对</span>
                <Collapse size="small" items={st.questions.map((q, qi) => ({
                  key: String(qi), label: `变式 ${qi + 1}${q.difficulty ? ` · ${q.difficulty}` : ''}`,
                  children: <div className="rp-ai-q"><Paragraph className="rp-ai-q-stem">{q.stem}</Paragraph><Paragraph>答案：{q.answer}</Paragraph><Paragraph type="secondary">解析：{q.analysis}</Paragraph></div>,
                }))} />
              </div>}
              {st?.error && <div className="rp-ai-fallback" data-testid={`ai-fallback-${s.indicator}`}>
                <Alert type="warning" showIcon message="模型暂不可用，可改用题库同考点题" />
                {s.fallback.length > 0
                  ? <ul className="rp-ai-fallback-list">{s.fallback.map((q) => <li key={q.id}><Text strong>{questionLabel(q.id)}</Text> {q.stem}</li>)}</ul>
                  : <Text type="secondary">题库暂无同考点候选题</Text>}
              </div>}
            </footer>
          </article>;
        })}
      </div>}
      {choices.length === 0 && <Empty description="该主题没有可安排的题目" />}
      {choices.map((x) => <div className="teacher-list-row rp-choice-row" key={x.id} data-testid={`${strategy === 'common' ? 'lecture' : 'pretest'}-choice-${x.id}`}>
        <Text strong>{questionLabel(x.id)}</Text><div className="rp-choice-main" style={{ flex: 1, minWidth: 0 }}><div>{bundle?.questions.find((q) => q.id === x.id)?.stem || '暂无题干'}</div><Text type="secondary">{strategy === 'common' ? '重点讲：' : '补观察：'}{x.reason}</Text></div>
        <div className="teacher-plan-question-actions">
          <Button className="teacher-answer-trigger" onClick={() => { setChoosingFollowUp(false); setQuestionOpen(x.id); }}>{new Set(x.students).size} 人{strategy === 'common' ? '失分' : '待观察'} · 看答卷</Button>
          {strategy === 'common' && <Button type="link" onClick={() => {
            setChoosingFollowUp(true); setQuestionOpen(x.id);
          }}>课后五分钟补测</Button>}
        </div>
      </div>)}
      {impactLine && <div data-testid="budget-tradeoff" className="teacher-muted">{impactLine}{impactAnswers.map((a) => <Button key={`${a.student}|${a.itemId}|${a.recordId}`} type="link" aria-label={`查看答卷 ${a.student} ${quizId} ${a.itemId}`} onClick={() => onGoStudent(a.student, a.itemId, a.indicator, a.recordId)}>看学生 {a.student} 的{questionLabel(a.itemId)}答卷</Button>)}</div>}
      <div className="teacher-actions"><Button onClick={() => setStudentsOpen(true)}>看学生安排（{assignedStudents.size} 人）</Button><Button type="text" onClick={() => setDetailsOpen(true)}>分析详情</Button></div>
    </>}
    {!plan && <Button onClick={() => setDetailsOpen(true)}>分析详情</Button>}
    <Drawer title={choosingFollowUp ? '课后五分钟补测 · 选择学生' : '看失分情况'} open={!!questionOpen} onClose={() => setQuestionOpen(null)} width={620}
      extra={<Button type="text" onClick={() => setQuestionOpen(null)}>返回题单</Button>}>
      <Title level={5}>{questionLabel(questionOpen || '')} · {bundle?.questions.find((q) => q.id === questionOpen)?.stem}</Title>
      {questionOpen && <Space wrap>
        <TeachingSourceLink questionId={questionOpen} quizId={quizId} classId={classId} />
        <TeachingSourceLink questionId={questionOpen} quizId={quizId} classId={classId} view="lecture" />
      </Space>}
      {selectedQuestion ? <List dataSource={[...new Set(selectedQuestion.lost_points.map((p) => p.student))]} locale={{ emptyText: '暂无可定位的失分观察项' }} renderItem={(student) => {
        const points = selectedQuestion.lost_points.filter((p) => p.student === student);
        const response = bundle?.answers.find(a => a.studentId === student && a.questionId === questionOpen && a.quizId === quizId && a.sourceRecordId === points[0]?.record_id);
        return <List.Item><Button type="link" onClick={() => onGoStudent(student, selectedQuestion.item_id, selectedQuestion.indicator, points[0]?.record_id)}>{getStudentName(student)}</Button><div>{response && <div>填答 {response.finalAnswer || '未单独记录'} · 得分 {response.score}/{response.fullScore}</div>}<span>{[...new Set(points.map((p) => p.label))].join('；')}</span></div><Button size="small" onClick={() => onFollowUp(student, selectedQuestion.item_id, selectedQuestion.indicator, points[0]?.record_id)} aria-label={`安排学生 ${student} 的课后补测`}>安排补测</Button></List.Item>;
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
