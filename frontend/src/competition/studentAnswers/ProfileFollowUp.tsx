import { canUseExternalAi, LOCAL_DATA_NOTICE } from '../shared/externalAi';
import { useEffect, useState } from 'react';
import { Alert, Button, Input, Modal, Select } from 'antd';
import { getToken } from '../../api/authState';
import type { Bundle, Diagnosis } from '../dataSource';
import { getDatasetId } from '../dataSource';
import { pickQuestion } from '../livePretest/LivePretest';
import type { LiveEvidenceEntry } from '../livePretest/LivePretest';
import { questionLabel } from '../shared/teacherContext';
import type { ProfileAnswerRef, ProfileIndicator, ProfileRecord, StudentProfileData } from './studentProfileData';
import TeachingSourceLink from '../curriculum/TeachingSourceLink';
import PracticeHints from '../livePretest/PracticeHints';
import FollowUpComparison, { WrittenFollowUp } from './FollowUpComparison';
import './followUp.css';

type Prompt = { goal: string; hint: string; if_confirmed: string; if_unclear: string };
type Outcome = '已观察到' | '需要复练' | '证据不足';
type Observation = { question_id: string; status: Outcome; note: string };
type StudentWork = { question_id: string; response: string; submitted_at?: string };
type Plan = {
  student_id: string; quiz_id: string; indicator_id: string; question_ids: string[];
  original_question_id?: string; original_record_id?: string;
  prompt: Prompt; observations: Observation[]; revision: number; updated_at?: string;
  student_work?: StudentWork[]; review_on?: string | null;
};
const OUTCOMES: Outcome[] = ['已观察到', '需要复练', '证据不足'];
const recordedSteps = (attempt: LiveEvidenceEntry) => [
  '去括号后：' + (attempt.intermediate || '未填写'),
  '合并后：' + (attempt.combined || '未填写'),
  '最终填答：' + (attempt.final || '未填写'),
].join('\n');
const FIELDS = [
  ['goal', '这次观察什么'], ['hint', '分步提示'],
  ['if_confirmed', '这一步会了，接着做什么'], ['if_unclear', '这一步还看不清，怎么补问'],
] as const;

async function request<T>(suffix: string, method: string, body?: object, signal?: AbortSignal): Promise<T> {
  const response = await fetch('/api/competition/follow-up' + suffix, {
    method, signal, headers: { 'Content-Type': 'application/json', 'X-User-Token': getToken() || '' },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '跟进记录暂不可用');
  return data as T;
}

function candidates(bundle: Bundle, diagnosis: Diagnosis) {
  return diagnosis.candidates.flatMap((candidate) => {
    const question = bundle.questions.find((q) => q.id === candidate.material_id);
    return candidate.node_available && candidate.material_type === '题目' && question
      && !question.questionType?.includes('选择') ? [{ ...question, done: candidate.done }] : [];
  });
}

interface WorkbenchProps {
  bundle: Bundle;
  profile: StudentProfileData;
  focusQuestionId?: string;
  focusRecordId?: string | null;
  preferredIndicator?: string | null;
  practice: LiveEvidenceEntry[];
  onOpenAnswer: (target: ProfileAnswerRef) => void;
  onStartPractice: (original: ProfileAnswerRef, practiceQuestion: string, hint?: string) => void;
}

export default function ProfileFollowUp(props: WorkbenchProps) {
  const { bundle, profile, focusQuestionId, focusRecordId, preferredIndicator } = props;
  const selectionContext = `${preferredIndicator || ''}|${focusQuestionId || ''}|${focusRecordId || ''}`;
  const [selected, setSelected] = useState({ context: '', id: '' });
  const available = profile.indicators.flatMap((row) => {
    const diagnosis = bundle.diagnoses?.diagnoses.find((d) => d.student_id === profile.student?.id
      && d.node_id === profile.quiz?.id && d.indicator_id === row.id);
    return diagnosis && candidates(bundle, diagnosis).length ? [{ row, diagnosis }] : [];
  });
  const target = available.find(({ row }) => selected.context === selectionContext && row.id === selected.id)
    || available.find(({ row }) => row.id === preferredIndicator)
    || available.find(({ row }) => row.records.some((r) => r.questionId === focusQuestionId && r.included))
    || available.find(({ row }) => row.findings.length > 0) || available[0];
  if (!target) return <section className="teacher-section" id="student-follow-up" aria-label="课后五分钟补测">
    <h3>课后五分钟补测</h3><p className="teacher-muted">本测验暂无可用于跟进的同考点题目。</p>
  </section>;
  return <section className="teacher-section profile-followup" id="student-follow-up" aria-label="课后五分钟补测">
    <div className="profile-followup-heading"><div><h3>课后五分钟补测</h3><p className="teacher-muted">先定观察目标，再用新作答决定下一步。</p></div>
      <Select aria-label="补测考点" value={target.row.id} onChange={(id) => setSelected({ context: selectionContext, id })}
        options={available.map(({ row }) => ({ value: row.id, label: row.name }))} />
    </div>
    <FollowUpEditor key={`${target.row.id}|${focusQuestionId || ''}|${focusRecordId || ''}`} {...props} {...target} />
  </section>;
}

function FollowUpEditor({ bundle, row, diagnosis, focusQuestionId, focusRecordId, practice, onOpenAnswer, onStartPractice }: WorkbenchProps & {
  row: ProfileIndicator; diagnosis: Diagnosis;
}) {
  const options = candidates(bundle, diagnosis);
  const onCurrentQuestion = row.records.some((r) => r.questionId === focusQuestionId);
  const defaultOrigin = onCurrentQuestion ? row.records.find((r) => r.included && r.questionId === focusQuestionId
    && (!focusRecordId || r.recordId === focusRecordId))
    : row.findings[0]?.record || row.records.find((r) => r.included);
  const automatic = pickQuestion(row.id, diagnosis);
  const defaults = [...new Set([automatic?.id, ...diagnosis.pretest_questions,
    ...options.filter((q) => !q.done).map((q) => q.id), ...options.map((q) => q.id)])]
    .filter((id): id is string => options.some((q) => q.id === id)).slice(0, 2);
  const [plan, setPlan] = useState<Plan>({
    student_id: diagnosis.student_id, quiz_id: diagnosis.node_id, indicator_id: row.id, revision: 0,
    original_question_id: defaultOrigin?.answer ? defaultOrigin.questionId : undefined,
    original_record_id: defaultOrigin?.answer ? defaultOrigin.recordId : undefined,
    question_ids: defaults, observations: [],
    prompt: {
      goal: diagnosis.observe.slice(0, 280) || '请学生写出解题依据，观察能否在新题中使用本考点。',
      hint: defaultOrigin && row.findings.some((finding) => finding.record.key === defaultOrigin.key && finding.lastExpression !== undefined)
        ? '先分别写出去括号、合并同类项后的算式。\n把合并后的算式和最终填答逐项比一比。'
        : diagnosis.resource.steps[0]?.when_stuck.slice(0, 280) || '先圈出已知条件。\n再说明第一步怎么做。',
      if_confirmed: '记录这次表现，再安排一道条件变化的题核对能否迁移。',
      if_unclear: '保留原判断，围绕仍缺少的步骤安排一次短练习或口头追问。',
    },
  });
  const [approved, setApproved] = useState<Plan | null>(null);
  const [busy, setBusy] = useState('加载');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [preview, setPreview] = useState(false);
  const [editing, setEditing] = useState<Observation | null>(null);
  const [response, setResponse] = useState<StudentWork | null>(null);
  const { student_id, quiz_id, indicator_id } = plan;
  const identity = {
    student_id, quiz_id, indicator_id, dataset: getDatasetId(),
    ...(plan.original_question_id ? { original_question_id: plan.original_question_id } : {}),
    ...(plan.original_record_id ? { original_record_id: plan.original_record_id } : {}),
  };
  const scope = '?' + new URLSearchParams(identity);
  useEffect(() => {
    const controller = new AbortController();
    setBusy('加载');
    setError('');
    request<{ plan: Plan | null }>(scope, 'GET', undefined, controller.signal)
      .then(({ plan: saved }) => { if (saved) { setPlan(saved); setApproved(saved); } })
      .catch((err: Error) => { if (!controller.signal.aborted) setError(err.message); })
      .finally(() => { if (!controller.signal.aborted) setBusy(''); });
    return () => controller.abort();
  }, [scope]);
  const dirty = !approved || JSON.stringify(plan) !== JSON.stringify(approved);
  const original: ProfileRecord | undefined = plan.original_question_id
    ? row.records.find((r) => r.included && r.questionId === plan.original_question_id && r.recordId === (plan.original_record_id || undefined))
    : undefined;
  const prior = row.findings.find((f) => f.record.key === original?.key);
  const attemptsFor = (id: string) => practice.filter((entry) => entry.nodeId === quiz_id && entry.indicatorId === indicator_id
    && entry.questionId === id && original && entry.originalQuestionId === original.questionId
    && entry.originalRecordId === original.recordId);
  const persist = async (next: Plan, message: string) => {
    setBusy('保存'); setError(''); setNotice('');
    try {
      const { plan: saved } = await request<{ plan: Plan }>('', 'PUT', next);
      setPlan(saved); setApproved(saved); setEditing(null); setResponse(null); setNotice(message);
      window.dispatchEvent(new Event('competition:followup-saved'));
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(''); }
  };
  const externalAi = canUseExternalAi();
  const generate = async () => {
    if (!externalAi) return;
    setBusy('生成'); setError(''); setNotice('');
    try {
      const { prompt } = await request<{ prompt: Prompt }>('/draft', 'POST', plan);
      setPlan((previous) => ({ ...previous, prompt }));
      setNotice('AI 草稿已填入。请复核提示与后续安排，再确认保存。');
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(''); }
  };
  const question = (id: string) => options.find((q) => q.id === id);
  const label = prior?.lastExpression !== undefined ? '核对过程与填答'
    : !row.sufficient ? '补充观察' : row.totals.score === row.totals.fullScore ? '观察方法迁移' : '订正失分步骤';
  const saveObservation = () => {
    if (!editing) return;
    const latest = attemptsFor(editing.question_id)[0];
    const studentWork = latest ? [
      ...(plan.student_work || []).filter((entry) => entry.question_id !== editing.question_id),
      { question_id: editing.question_id, response: recordedSteps(latest) },
    ] : plan.student_work;
    void persist({
      ...plan, student_work: studentWork,
      observations: [...plan.observations.filter((o) => o.question_id !== editing.question_id), editing],
    }, '教师观察已保存，档案已更新下一步。');
  };

  return <>
    <div className="profile-followup-before">
      <strong>{label}</strong><span>{row.totals.scoredCount} 条正式有效记录 · {row.totals.score}/{row.totals.fullScore} 分</span>
      {prior && <p>{prior.fact}</p>}
      {!prior && <p>{row.coverageNote}</p>}
      {original && <div className="teacher-actions">
        <Button type="link" onClick={() => onOpenAnswer(original)}>打开原答卷 · {questionLabel(original.questionId)}</Button>
        {original.answer && <TeachingSourceLink questionId={original.questionId} quizId={original.quizId}
          classId={bundle.students.find((student) => student.id === student_id)?.classId}
          studentId={student_id} answerId={original.answer.id} view="lecture" />}
      </div>}
    </div>
    <details className="profile-followup-editor">
      <summary>{approved ? '查看或调整补测安排' : '选择题目并确认补测安排'}</summary>
      <label>补测题（最多两道）</label>
      <Select mode="multiple" aria-label="选择补测题" maxCount={2} value={plan.question_ids} disabled={!!busy || plan.observations.length > 0 || !!plan.student_work?.length}
        onChange={(question_ids) => setPlan({ ...plan, question_ids })}
        options={options.map((q) => ({ value: q.id, label: `${questionLabel(q.id)} · ${q.stem}${q.done ? '（复练）' : ''}` }))} />
      {(plan.observations.length > 0 || !!plan.student_work?.length) && <p className="teacher-muted">已有作答或观察记录，保留题单供回查。提示和后续安排仍可修改。</p>}
      <div className="profile-followup-fields">{FIELDS.map(([key, text]) => <label key={key}>{text}
        <Input.TextArea aria-label={text} rows={2} maxLength={280} value={plan.prompt[key]} disabled={!!busy}
          onChange={(e) => setPlan({ ...plan, prompt: { ...plan.prompt, [key]: e.target.value } })} />
      </label>)}</div>
      <label className="followup-date">下次复查（可选）
        <Input type="date" aria-label="下次复查日期" value={plan.review_on || ''} disabled={!!busy}
          onChange={(event) => setPlan({ ...plan, review_on: event.target.value || null })} />
      </label>
      <div className="teacher-actions">
        <Button disabled={!externalAi || !plan.question_ids.length || (!!busy && busy !== '生成')} loading={busy === '生成'} onClick={() => void generate()}>AI 拟定观察与提示</Button>
        {!externalAi && <span className="teacher-muted">{LOCAL_DATA_NOTICE}</span>}
        <Button type="primary" disabled={!dirty || !plan.question_ids.length || (!!busy && busy !== '保存')}
          loading={busy === '保存'} onClick={() => void persist(plan, '补测安排已保存。')}>教师确认并保存</Button>
      </div>
    </details>
    {error && <Alert type="warning" showIcon message={error} />}
    {notice && <p className="teacher-result-note" role="status">{notice}</p>}
    {approved && !dirty && <div className="profile-followup-after">
      <h4>补测作答与下一步</h4>
      {approved.review_on && <p><b>下次复查</b> {approved.review_on}</p>}
      <p className="teacher-muted">跟进记录独立于原测验。可让学生在教师设备上作答，或由教师核对纸笔作答。</p>
      <Button onClick={() => setPreview(true)}>预览学生补测卡</Button>
      <ul>{approved.question_ids.map((id) => {
        const observation = approved.observations.find((item) => item.question_id === id);
        const work = approved.student_work?.find((item) => item.question_id === id);
        const attempts = attemptsFor(id);
        const live = attempts[0];
        const previousObservation = !live || work?.response === recordedSteps(live) ? observation : undefined;
        const canRun = original && !!pickQuestion(row.id, diagnosis, id);
        return <li key={id}>
          <strong>{questionLabel(id)} · {question(id)?.stem}</strong>
          {live && original && <FollowUpComparison key={live.id || live.at} original={original}
            attempt={live} coverageNote={row.coverageNote} explain={externalAi ? async () => {
              const data = await request<{ explanation: string }>('/explain', 'POST', {
                ...identity, question_id: id, result_kind: live.kind,
              });
              return data.explanation;
            } : undefined} />}
          {attempts.length > 1 && <details><summary>本题核对记录（{attempts.length} 次）</summary>
            {attempts.map((attempt, index) => <p key={attempt.id || `${attempt.at}|${index}`}>
              <time dateTime={attempt.at}>{new Date(attempt.at).toLocaleString('zh-CN')}</time> · {attempt.supports}
            </p>)}
          </details>}
          {work && !live && <WrittenFollowUp original={original} questionId={id} response={work.response} reviewed={!!observation} />}
          {observation ? <p><b>教师核对 · {observation.status}：</b>{observation.note}<br />
            <b>下一步：</b>{observation.status === '已观察到' ? approved.prompt.if_confirmed : approved.prompt.if_unclear}</p>
            : !live && !work && <p className="teacher-muted">等待补测作答或教师观察。</p>}
          <div className="teacher-actions">
            {canRun && <Button onClick={() => onStartPractice(original, id, approved.prompt.hint)}>打开分步核对 · {questionLabel(id)}</Button>}
            <Button onClick={() => setEditing(previousObservation || { question_id: id,
              status: live?.kind === 'correct' ? '已观察到' : live?.kind && !['manual', 'final_wrong'].includes(live.kind) ? '需要复练' : '证据不足',
              note: live ? `${live.supports} ${live.followUp}`.slice(0, 600) : '' })}>记录教师观察 · {questionLabel(id)}</Button>
          </div>
        </li>;
      })}</ul>
    </div>}
    <Modal title="学生补测卡" open={preview && !!approved && !dirty}
      footer={<Button onClick={() => { setPreview(false); setResponse(null); }}>关闭预览</Button>}
      onCancel={() => { setPreview(false); setResponse(null); }}>
      <p>请先独立作答，并写出主要步骤或理由。</p>
      {preview && <PracticeHints text={approved?.prompt.hint} />}
      <ol className="followup-card-questions">{approved?.question_ids.map((id) => {
        const canRun = original && !!pickQuestion(row.id, diagnosis, id);
        const work = approved.student_work?.find((item) => item.question_id === id);
        const reviewed = approved.observations.some((item) => item.question_id === id);
        return <li key={id}><strong>{questionLabel(id)} · {question(id)?.stem}</strong><br />
          {canRun ? <Button onClick={() => { setPreview(false); onStartPractice(original, id, approved.prompt.hint); }}>分步作答 · {questionLabel(id)}</Button>
            : reviewed ? <p>老师已核对。</p>
            : response?.question_id === id ? <div className="followup-card-response">
              <Input.TextArea aria-label={`补测作答 · ${questionLabel(id)}`} value={response.response} rows={3} maxLength={1500}
                onChange={(event) => setResponse({ ...response, response: event.target.value })} />
              <Button type="primary" loading={busy === '保存'} disabled={!response.response.trim()}
                onClick={() => void persist({ ...plan, student_work: [
                  ...(plan.student_work || []).filter((entry) => entry.question_id !== id),
                  { ...response, response: response.response.trim(), submitted_at: new Date().toISOString() },
                ] }, '学生作答已保存，等待教师核对。')}>交给老师核对</Button>
            </div> : <Button onClick={() => setResponse({ question_id: id, response: work?.response || '' })}>
              {work ? '查看或补充作答' : '写作答'} · {questionLabel(id)}
            </Button>}
        </li>;
      })}</ol>
      {preview && notice && <p role="status">{notice}</p>}
      {preview && error && <Alert type="warning" message={error} />}
    </Modal>
    <Modal title="记录教师观察" open={!!editing} onCancel={() => setEditing(null)} okText="保存观察"
      okButtonProps={{ disabled: !editing?.note.trim(), loading: busy === '保存' }}
      onOk={saveObservation}>
      {editing && <div className="profile-followup-fields">
        <Select aria-label="观察结果" value={editing.status} options={OUTCOMES.map((value) => ({ value }))}
          onChange={(status) => setEditing({ ...editing, status })} />
        <Input.TextArea aria-label="观察依据" rows={4} maxLength={600} placeholder="写下学生实际作答、关键步骤或仍缺少的证据"
          value={editing.note} onChange={(e) => setEditing({ ...editing, note: e.target.value })} />
        {error && <Alert type="warning" message={error} />}
      </div>}
    </Modal>
  </>;
}
