import { useState } from 'react';
import { Alert, Button, Select } from 'antd';
import { getToken } from '../../api/authState';
import type { Bundle } from '../dataSource';
import TeachingSourceLink from '../curriculum/TeachingSourceLink';
import { questionLabel } from '../shared/teacherContext';
import { getPrepContext, rehearse } from './prepRehearsal';
import type { PrepEvidence, PrepObservation, PrepPlan, PrepSettings, PrepStep } from './prepRehearsal';
import './prepRehearsal.css';

type Props = {
  bundle: Bundle; quizId: string; classId: string;
  backTo: () => string;
  openStudent: (studentId: string, mode: 'answer' | 'profile' | 'followup', evidence?: PrepEvidence) => void;
};
type View = { current: PrepSettings; previous?: PrepSettings; open: boolean };
const tasks = [{ id: 'lecture', name: '先讲共性失分' }, { id: 'probe', name: '先补测，再决定怎么讲' }] as const;
const initial: View = { current: { minutes: 8, lock: '', observationKey: '' }, open: false };
const limited = (value: unknown): value is PrepSettings => {
  const s = value as PrepSettings;
  return !!s && [5, 8].includes(s.minutes) && typeof s.lock === 'string' && typeof s.observationKey === 'string';
};

function readView(key: string): View {
  try {
    const view = JSON.parse(sessionStorage.getItem(key) || 'null') as View;
    if (view && limited(view.current)) return { ...view, previous: limited(view.previous) ? view.previous : undefined };
  } catch { /* storage can be unavailable */ }
  return initial;
}

function Changes({ before, after }: { before: PrepPlan; after: PrepPlan }) {
  const oldKeys = new Set(before.lessons.map((s) => s.key));
  const newKeys = new Set(after.lessons.map((s) => s.key));
  const keep = after.lessons.filter((s) => oldKeys.has(s.key));
  const add = after.lessons.filter((s) => !oldKeys.has(s.key));
  const remove = before.lessons.filter((s) => !newKeys.has(s.key));
  const changed = after.individual.filter((item) => before.individual.find((p) => p.studentId === item.studentId)?.reason !== item.reason);
  const summarize = (steps: PrepStep[]) => steps.map((s) => `${questionLabel(s.questionId)}「${s.label}」`).join('、');
  const sameProbe = before.probe?.focus.answer.id === after.probe?.focus.answer.id && before.probe?.questionId === after.probe?.questionId;
  return <div className="prep-changes" aria-label="与上一步相比">
    <strong>与上一步相比</strong>
    {(keep.length > 0 || add.length > 0 || remove.length > 0) && <div className="prep-forward">
      {keep.length > 0 && <p>保留：{summarize(keep)}</p>}
      {add.length > 0 && <p>新安排：{summarize(add)}</p>}
      {remove.length > 0 && <p>移出课堂：{summarize(remove)}</p>}
    </div>}
    {!sameProbe && <p>先补测：{before.probe ? `学生 ${before.probe.focus.answer.studentId}` : '无'} → {after.probe ? `学生 ${after.probe.focus.answer.studentId}` : '本方案不再安排'}。</p>}
    <p>讲到的学生 {before.covered.length} → {after.covered.length} 人。{changed.length > 0
      ? `${changed.length} 人新增或调整了课后安排，见下方名单。` : '课后安排没有新增变化。'}</p>
  </div>;
}

function ClassroomPrompt({ classId, evidence, quizId, probeId }: {
  classId: string; evidence: PrepEvidence; quizId: string; probeId?: string;
}) {
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const record = evidence.answer.sourceRecordId;
  if (!record) return null;
  const generate = async () => {
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/competition/prep-prompt', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-User-Token': getToken() || '' },
        body: JSON.stringify({ quiz_id: quizId, class_id: classId, question_id: evidence.answer.questionId, record_id: record, probe_id: probeId }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '暂时无法拟定追问');
      setPrompt(data.prompt);
    } catch (err) { setError(err instanceof Error ? err.message : '暂时无法拟定追问'); }
    finally { setBusy(false); }
  };
  return <div className="prep-prompt">
    <Button type="link" loading={busy} onClick={() => void generate()}>{prompt ? '再拟一句追问' : '请 AI 拟一句课堂追问'}</Button>
    {prompt && <p><b>课堂追问草稿</b> {prompt}</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

function PlanCard({ plan, before, name, observation, ...props }: Props & {
  name: string; plan: PrepPlan; before?: PrepPlan; observation?: PrepObservation;
}) {
  const { bundle, quizId, classId, openStudent, backTo } = props;
  const nameOf = (id: string) => bundle.students.find((s) => s.id === id)?.name || id;
  const lessonSource = (questionId: string, evidence?: PrepEvidence) => <TeachingSourceLink
    questionId={questionId} quizId={quizId} classId={classId} studentId={evidence?.answer.studentId}
    answerId={evidence?.answer.id} backTo={backTo} view="lecture" />;
  const links = (e: PrepEvidence) => <div className="prep-inline">
    <Button type="link" onClick={() => openStudent(e.answer.studentId, 'answer', e)}>原答卷</Button>
    <Button type="link" onClick={() => openStudent(e.answer.studentId, 'profile', e)}>个人档案</Button>
    <Button type="link" onClick={() => openStudent(e.answer.studentId, 'followup', e)}>补测卡</Button>
  </div>;
  const roster = (items: PrepPlan['individual'], updated = false) => <ul className="prep-people">{items.map((item) =>
    <li key={item.studentId}>
      <div><strong>学生 {nameOf(item.studentId)}</strong> <span>{updated && observation?.entry.studentId === item.studentId && ' · 新作答已参考'}</span></div>
      <p>{item.reason}</p>
      {item.evidence ? links(item.evidence) : <Button type="link" onClick={() => openStudent(item.studentId, 'profile')}>打开个人档案</Button>}
    </li>)}</ul>;
  return <article className="prep-card" aria-label={name}>
    <div className="prep-card-heading"><h3>{name}</h3><span>{plan.used} 分钟</span></div>
    <p className="prep-counts"><strong>{plan.covered.length} 人</strong>至少有一个失分步骤会讲到</p>
    {before && <Changes before={before} after={plan} />}
    {plan.probe && <div className="prep-step prep-probe">
      <h4>先看 {nameOf(plan.probe.focus.answer.studentId)} 的作答 <small>约 {plan.probe.minutes} 分钟</small></h4>
      <p>{questionLabel(plan.probe.questionId)} · {plan.probe.stem}</p>
      <p>{plan.probe.reason}</p>
      {links(plan.probe.focus)}
      <ClassroomPrompt key={plan.probe.focus.answer.id} classId={classId} quizId={quizId}
        evidence={plan.probe.focus} probeId={plan.probe.questionId} />
    </div>}
    {plan.lessons.length ? plan.lessons.map((step) => <div key={step.key} className="prep-step" data-question-id={step.questionId}>
      <h4>{questionLabel(step.questionId)} <small>约 {step.minutes} 分钟</small></h4>
      <p>{step.stem}</p>
      <p><strong>讲这一点</strong> {step.label} · {new Set(step.evidence.map((e) => e.answer.studentId)).size} 人在这一点失分</p>
      {lessonSource(step.questionId, step.evidence[0])}
      {step.evidence.length > 0 && <details>
        <summary>查看这一评分点的原答卷</summary>
        <ul className="prep-people">{step.evidence.map((e) => <li key={e.answer.id}>
          <strong>学生 {nameOf(e.answer.studentId)}</strong> · 本题 {e.answer.score}/{e.answer.fullScore} 分
          {links(e)}
        </li>)}</ul>
        <ClassroomPrompt classId={classId} quizId={quizId} evidence={step.evidence[0]} />
      </details>}
    </div>) : <p className="teacher-muted">当前时长没有可安排的讲评题。</p>}
    {observation && plan.individual.some((item) => item.studentId === observation.entry.studentId) && <div className="prep-included-work">
      <strong>学生 {nameOf(observation.entry.studentId)} 的新安排</strong>
      <p>{plan.individual.find((item) => item.studentId === observation.entry.studentId)?.reason}</p>
      {links(observation.original)}
    </div>}
    <details className="prep-roster"><summary>课后另作安排（{plan.individual.length} 人）</summary>
      <p className="teacher-muted">包括课堂已经讲到一部分、仍有其他失分步骤的学生。</p>
      {plan.individual.length ? roster(plan.individual, true) : <p>没有额外安排。</p>}
    </details>
    <details className="prep-roster"><summary>记录不足，待观察（{plan.waiting.length} 人）</summary>
      {plan.waiting.length ? roster(plan.waiting) : <p>当前选题没有待观察名单。</p>}
    </details>
  </article>;
}

export default function PrepRehearsal(props: Props) {
  const { bundle, quizId, classId } = props;
  const key = `competition.prep.v1|${bundle.manifest.generated_at}|${quizId}|${classId}`;
  const [view, setView] = useState(() => readView(key));
  const [, setRefresh] = useState(0);
  const context = getPrepContext(bundle, quizId, classId);
  const { current, previous, open } = view;
  const set = (next: View) => {
    setView(next);
    try { sessionStorage.setItem(key, JSON.stringify(next)); } catch { /* current page state still works */ }
  };
  const update = (patch: Partial<PrepSettings>) => {
    const next = { ...current, ...patch };
    if (JSON.stringify(next) !== JSON.stringify(current)) set({ open: true, current: next, previous: current });
  };
  const observation = context.practices.find((p) => p.key === current.observationKey);
  const options = [...new Map(context.steps.map((step) => [step.questionId, {
    value: step.questionId, label: questionLabel(step.questionId),
  }])).values()];
  return <section className="teacher-section prep-rehearsal" aria-label="AI 备课推演">
    <header className="prep-header">
      <div><h2>AI 备课推演</h2><p>课上先讲什么，哪些学生留到课后跟进？</p></div>
      <Button type={open ? 'default' : 'primary'} onClick={() => set({ ...view, open: !open })}>{open ? '收起推演' : '开始推演'}</Button>
    </header>
    {open && (context.answers.length === 0 ? <Alert type="info" message="本班本次测验没有有效作答，暂不能安排讲评。" /> : <>
      <div className="prep-controls">
        <label>课堂可用时间 <Select aria-label="课堂可用时间" value={current.minutes} options={[8, 5].map((n) => ({ value: n, label: `${n} 分钟` }))}
          onChange={(minutes) => update({ minutes })} /></label>
        <label>指定要讲的题 <Select aria-label="指定要讲的题" value={current.lock} options={[{ value: '', label: '由系统选题' }, ...options]}
          onChange={(lock) => update({ lock })} /></label>
        <label>补进一份新作答 <Select aria-label="补进一份新作答" value={observation?.key || ''}
          options={[{ value: '', label: '只看原测验' }, ...context.practices.map((p) => ({
            value: p.key, label: `学生 ${p.entry.studentId} · ${questionLabel(p.entry.originalQuestionId || '')}的补测 · ${p.entry.at.slice(11, 19)}`,
          }))]} onChange={(observationKey) => update({ observationKey })} /></label>
        <Button type="link" onClick={() => setRefresh((value) => value + 1)}>刷新补测</Button>
      </div>
      <p className="teacher-muted prep-method">每个讲评步骤预留 3 分钟，补测预留 2 分钟。人数按学生去重，待观察不算讲评覆盖。每一次改动都与上一步比。</p>
      {context.practices.length === 0 && <p className="teacher-muted">还没有本班本次的补测作答。可从下面的原答卷进入补测卡，完成后再回来选。</p>}
      {observation && <Alert type="info" message={`已参考学生 ${observation.entry.studentId} 的补测作答`}
        description={<span>原题{questionLabel(observation.original.answer.questionId)}，练习{questionLabel(observation.entry.questionId)}。{observation.entry.supports} 原测验得分保持不变。</span>} />}
      <div className="prep-plans">{tasks.map((task) => <PlanCard key={task.id} {...props} name={task.name}
        plan={rehearse(context, current, task.id === 'probe')}
        before={previous ? rehearse(context, previous, task.id === 'probe') : undefined} observation={observation} />)}</div>
    </>)}
  </section>;
}
