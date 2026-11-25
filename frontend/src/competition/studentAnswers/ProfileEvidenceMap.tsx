import { useEffect, useState } from 'react';
import { Button, Select } from 'antd';
import { getToken } from '../../api/authState';
import TeachingSourceLink from '../curriculum/TeachingSourceLink';
import { teachingConnections } from '../curriculum/learningSequence';
import type { Bundle } from '../dataSource';
import { getDatasetId } from '../dataSource';
import type { LiveEvidenceEntry } from '../livePretest/LivePretest';
import { questionLabel } from '../shared/teacherContext';
import { openPageLabel, printedPageLabel } from '../shared/textbookPage';
import type { TextbookSource } from '../shared/types';
import type { ProfileAnswerRef, ProfileIndicator, ProfileRecord, StudentProfileData } from './studentProfileData';
import './profileEvidenceMap.css';

interface EvidenceMapProps {
  bundle: Bundle;
  profile: StudentProfileData;
  focusQuestionId?: string;
  focusRecordId?: string | null;
  practice: LiveEvidenceEntry[];
  onOpenAnswer: (answer: ProfileAnswerRef) => void;
  onOpenTextbook: (source: TextbookSource) => void;
  onFollowUp: (indicatorId: string, questionId: string, recordId?: string) => void;
}

function EvidenceStatus({ observed }: { observed: boolean }) {
  return <span className={`profile-map-status ${observed ? 'is-observed' : 'is-pending'}`}>
    {observed ? '已观察' : '待补证'}
  </span>;
}

function displayedRecord(records: ProfileRecord[], questionId: string, recordId?: string | null) {
  const matching = records.filter((record) => record.questionId === questionId);
  if (recordId) return matching.find((record) => record.recordId === recordId);
  return matching.find((record) => record.included && record.answer) || matching[0];
}

function verifiedTextbook(row: ProfileIndicator | undefined) {
  return row?.textbook?.verified === true ? row.textbook : undefined;
}

interface SavedFollowUp {
  observations: { question_id: string; status: string }[];
  student_work?: { question_id: string }[];
  review_on?: string;
}

function FollowUpEvidence({ scope, practice, onOpen }: {
  scope?: string;
  practice?: LiveEvidenceEntry;
  onOpen?: () => void;
}) {
  const [saved, setSaved] = useState<SavedFollowUp | null>(null);
  const [requestFailed, setRequestFailed] = useState(false);
  useEffect(() => {
    if (!scope) return;
    const controller = new AbortController();
    let latest = 0;
    const refresh = () => {
      const current = ++latest;
      void (async () => {
        try {
          const response = await fetch(`/api/competition/follow-up?${scope}`, {
            signal: controller.signal, headers: { 'X-User-Token': getToken() || '' },
          });
          if (!response.ok) throw new Error('follow-up lookup');
          const data = await response.json() as { plan: SavedFollowUp | null };
          if (!controller.signal.aborted && current === latest) {
            setSaved(data.plan);
            setRequestFailed(false);
          }
        } catch {
          if (!controller.signal.aborted && current === latest) setRequestFailed(true);
        }
      })();
    };
    refresh();
    window.addEventListener('competition:followup-saved', refresh);
    return () => {
      controller.abort();
      window.removeEventListener('competition:followup-saved', refresh);
    };
  }, [scope]);
  const observations = saved?.observations || [];
  const hasUsefulPractice = !!practice && practice.kind !== 'manual' && practice.kind !== 'final_wrong';
  const observedIds = new Set(observations.map((item) => item.question_id));
  const unreviewed = (saved?.student_work || []).filter((item) => !observedIds.has(item.question_id)).length;
  const observed = observations.some((item) => item.status !== '证据不足') || hasUsefulPractice;
  return <>
    <h4>补测 <EvidenceStatus observed={observed} /></h4>
    {observations.length > 0 && <p>教师已核对 {observations.length} 题，其中 {observations.filter((item) => item.status === '证据不足').length} 题需补证。</p>}
    {unreviewed > 0 && <p>{unreviewed} 题已作答，等教师核对。</p>}
    {practice && <p>{practice.supports.split(practice.questionId).join(questionLabel(practice.questionId))}</p>}
    {!observations.length && !unreviewed && !practice && <p>{requestFailed
      ? '暂无法读取已保存的补测记录。'
      : saved ? '补测安排已保存，等待学生作答或教师核对。' : '还没有本考点的补测记录。'}</p>}
    {saved?.review_on && <p className="teacher-muted">计划复查：{saved.review_on}</p>}
    {onOpen && <Button type="link" onClick={onOpen}>打开补测安排</Button>}
  </>;
}

export default function ProfileEvidenceMap({
  bundle, profile, focusQuestionId, focusRecordId, practice, onOpenAnswer, onOpenTextbook, onFollowUp,
}: EvidenceMapProps) {
  const [selection, setSelection] = useState('');
  const quiz = profile.quiz;
  const student = profile.student;
  if (!quiz || !student) return null;

  const records = profile.history.find((entry) => entry.id === quiz.id)?.records || [];
  const knownIds = new Set(records.map((record) => record.questionId));
  if (focusQuestionId && bundle.questions.some((question) => question.id === focusQuestionId)) knownIds.add(focusQuestionId);
  const questions = bundle.questions.filter((question) => knownIds.has(question.id));
  if (!questions.length) return null;

  const current = questions.find((question) => question.id === selection)
    || questions.find((question) => question.id === focusQuestionId)
    || questions[0];
  const exactRecordId = current.id === focusQuestionId ? focusRecordId : null;
  const record = displayedRecord(records, current.id, exactRecordId);
  const indicator = profile.indicators.find((row) => row.id === current.kpId);
  const observation = indicator?.findings.find((finding) => finding.record.key === record?.key);
  const textbook = verifiedTextbook(indicator);
  const canFollowUp = !!indicator && bundle.diagnoses?.diagnoses.some((diagnosis) =>
    diagnosis.student_id === student.id && diagnosis.node_id === quiz.id && diagnosis.indicator_id === indicator.id
    && diagnosis.candidates.some((candidate) => candidate.material_type === '题目' && candidate.node_available
      && bundle.questions.some((question) => question.id === candidate.material_id && !question.questionType?.includes('选择'))));
  const latestPractice = record?.included ? practice.find((entry) =>
    entry.studentId === student.id && entry.nodeId === quiz.id && entry.indicatorId === indicator?.id
    && entry.originalQuestionId === current.id
    && (record.recordId ? entry.originalRecordId === record.recordId : !entry.originalRecordId)) : undefined;
  const followUpScope = canFollowUp && indicator
    ? new URLSearchParams({ student_id: student.id, quiz_id: quiz.id, indicator_id: indicator.id, dataset: getDatasetId(),
      ...(record?.included && record.answer ? { original_question_id: current.id,
        ...(record.recordId ? { original_record_id: record.recordId } : {}) } : {}) }).toString()
    : undefined;
  const connections = !indicator ? [] : teachingConnections.flatMap((link) => {
    if (link.from !== indicator.id && link.to !== indicator.id) return [];
    const prior = link.to === indicator.id;
    const related = profile.indicators.find((row) => row.id === (prior ? link.from : link.to));
    return related ? [{ related, prior, reason: link.reason }] : [];
  });

  return <section id="student-evidence-map" className="teacher-section profile-evidence-map" aria-label="证据图">
    <div className="profile-map-heading">
      <div><h3>证据图</h3><p className="teacher-muted">从一道题看作答、教材和下一步。</p></div>
      <Select aria-label="证据图题目" value={current.id} onChange={setSelection} popupMatchSelectWidth={false}
        options={questions.map((question) => ({ value: question.id, label: questionLabel(question.id) }))} />
    </div>

    <div className="profile-map-question">
      <div><strong>{questionLabel(current.id)}</strong>{indicator && <span>{indicator.name}</span>}</div>
      <p>{current.stem}</p>
    </div>

    <ol className="profile-map-stages">
      <li>
        <h4>原答卷 <EvidenceStatus observed={!!record?.included} /></h4>
        {record?.included
          ? <p>本题 {record.score}/{record.fullScore} 分。{observation?.fact || (record.answer?.stepsUnavailable ? '未记录解题步骤。' : '')}</p>
          : <p>{record ? `本题${record.status}。` : '本题没有有效作答记录。'}</p>}
        <Button type="link" onClick={() => onOpenAnswer({ questionId: current.id, quizId: quiz.id, recordId: record?.recordId || exactRecordId || undefined })}>
          {record ? '打开本题原答卷' : '查看本题记录'}
        </Button>
      </li>
      <li>
        <h4>本题考点</h4>
        {indicator ? <p><EvidenceStatus observed={indicator.sufficient} /> {indicator.totals.scoredCount} 条有效记录。
          {!indicator.sufficient && ' 暂不足以判断整个考点。'}</p> : <p>没有登记考点。</p>}
        {textbook && <>
          <p className="teacher-muted">{printedPageLabel(textbook.printedPage)}</p>
          <Button type="link" onClick={() => onOpenTextbook(textbook)}>{openPageLabel(textbook.printedPage)}</Button>
          <TeachingSourceLink view="lecture" questionId={current.id} quizId={quiz.id} classId={student.classId}
            studentId={record?.included && record.answer ? student.id : undefined} answerId={record?.included ? record.answer?.id : undefined}>
            看教师讲评卡
          </TeachingSourceLink>
        </>}
        {!textbook && <p className="teacher-muted">暂无已核对的教材原页。</p>}
      </li>
      <li>
        <FollowUpEvidence key={followUpScope || current.id} scope={followUpScope} practice={latestPractice}
          onOpen={canFollowUp && indicator ? () => onFollowUp(indicator.id, current.id, record?.recordId) : undefined} />
      </li>
    </ol>

    {connections.length > 0 && <div className="profile-map-branches">
      <h4>和这题直接相关的考点</h4>
      <ul>{connections.map(({ related, prior, reason }) => {
        const example = related.records.find((r) => r.included && r.answer);
        const source = verifiedTextbook(related);
        return <li key={related.id}>
          <div><span className="profile-map-direction">{prior ? '可先复习' : '可接着学'}</span>
            <strong>{related.name}</strong> <EvidenceStatus observed={related.sufficient} /></div>
          <p>{reason}</p>
          <div className="profile-map-related-links">
            {example && <Button type="link" onClick={() => onOpenAnswer(example)}>看本考点作答 · {questionLabel(example.questionId)}</Button>}
            {source && <Button type="link" onClick={() => onOpenTextbook(source)}>{openPageLabel(source.printedPage)}</Button>}
            {!example && !source && <span className="teacher-muted">暂无可打开的作答或教材。</span>}
          </div>
        </li>;
      })}</ul>
    </div>}
    <p className="teacher-muted profile-map-note">补测单独记录，原测验得分保持原值。</p>
  </section>;
}
