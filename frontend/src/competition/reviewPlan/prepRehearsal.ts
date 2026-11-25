import type { Bundle, RealAnswer } from '../dataSource';
import { pickQuestion, readLiveEvidence } from '../livePretest/LivePretest';
import type { LiveEvidenceEntry } from '../livePretest/LivePretest';

export type PrepEvidence = { answer: RealAnswer; indicatorId: string };
export type PrepStep = {
  key: string; questionId: string; stem: string; indicatorId: string; label: string; minutes: number;
  evidence: PrepEvidence[];
};
export type PrepObservation = { key: string; entry: LiveEvidenceEntry; original: PrepEvidence };
type FollowUp = { studentId: string; reason: string; evidence?: PrepEvidence };
export type PrepPlan = {
  lessons: PrepStep[]; used: number; covered: string[]; waiting: FollowUp[]; individual: FollowUp[];
  probe?: { focus: PrepEvidence; questionId: string; stem: string; reason: string; minutes: number };
};
export type PrepSettings = { minutes: number; lock: string; observationKey: string };
const LECTURE_MINUTES = 3;
const PROBE_MINUTES = 2;
const byId = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true });

export function getPrepContext(bundle: Bundle, quizId: string, classId: string) {
  const roster = bundle.students.filter((s) => s.classId === classId);
  const ids = new Set(roster.map((s) => s.id));
  const scope = bundle.quizzes.find((q) => q.id === quizId)?.availableItems;
  const questions = new Map(bundle.questions.map((q) => [q.id, q]));
  const answers = bundle.answers.filter((a) => a.quizId === quizId && ids.has(a.studentId)
    && (!scope || scope.includes(a.questionId)) && a.fullScore > 0 && a.score >= 0 && a.score <= a.fullScore);
  const byQuestion = new Map<string, RealAnswer[]>();
  for (const answer of answers) {
    byQuestion.set(answer.questionId, [...(byQuestion.get(answer.questionId) || []), answer]);
  }
  const steps = [...byQuestion].flatMap(([questionId, records]) => {
    const q = questions.get(questionId);
    if (!q || !q.fullScore) return [];
    const byPoint = new Map<string, PrepStep>();
    for (const answer of records.filter((a) => a.score < a.fullScore)) {
      const rubric = answer.scoringPoints;
      const consistent = !answer.rubricUnavailable && rubric.length > 0
        && Math.abs(rubric.reduce((sum, p) => sum + p.earnedScore, 0) - answer.score) < 0.001
        && Math.abs(rubric.reduce((sum, p) => sum + p.fullScore, 0) - answer.fullScore) < 0.001
        && rubric.every((p) => p.earnedScore >= 0 && p.earnedScore <= p.fullScore);
      const points = consistent ? rubric.filter((p) => p.earnedScore < p.fullScore)
        : [{ id: 'review', label: '核对原答卷中的失分' }];
      for (const point of points) {
        const step = byPoint.get(point.id) || { key: questionId + '|' + point.id, questionId, stem: q.stem,
          indicatorId: q.kpId, label: point.label, minutes: LECTURE_MINUTES, evidence: [] };
        if (!step.evidence.some((row) => row.answer.id === answer.id)) step.evidence.push({ answer, indicatorId: q.kpId });
        byPoint.set(point.id, step);
      }
    }
    if (!byPoint.size) return [{ key: questionId + '|teacher', questionId, stem: q.stem,
      indicatorId: q.kpId, label: '用原题核对解题方法', minutes: LECTURE_MINUTES,
      evidence: [] as PrepEvidence[] }];
    return [...byPoint.values()];
  }).sort((a, b) => byId(a.key, b.key));
  const diagnoses = (bundle.diagnoses?.diagnoses || []).filter((d) => d.node_id === quizId && ids.has(d.student_id));
  const origins = new Map<string, PrepEvidence>();
  for (const answer of answers) {
    const q = questions.get(answer.questionId);
    if (q && answer.sourceRecordId) origins.set(`${answer.studentId}|${answer.questionId}|${answer.sourceRecordId}`,
      { answer, indicatorId: q.kpId });
  }
  const practices = diagnoses.flatMap((d) => readLiveEvidence(d.student_id, quizId, d.indicator_id).flatMap((entry) => {
    const original = origins.get(`${d.student_id}|${entry.originalQuestionId}|${entry.originalRecordId}`);
    if (!entry.id || entry.studentId !== d.student_id || entry.nodeId !== quizId || entry.indicatorId !== d.indicator_id
      || original?.indicatorId !== d.indicator_id || !d.candidates.some((c) => c.material_id === entry.questionId && c.node_available)) return [];
    return [{ key: entry.id, entry, original }];
  })).sort((a, b) => b.entry.at.localeCompare(a.entry.at));
  return { roster, answers, steps, diagnoses, questions, practices };
}

type Context = ReturnType<typeof getPrepContext>;
const studentIds = (steps: PrepStep[]) => [...new Set(steps.flatMap((s) => s.evidence.map((e) => e.answer.studentId)))];

/** With at most two teaching slots, compare every single step and every pair of different questions. */
function chooseLessons(steps: PrepStep[], minutes: number, lock: string) {
  const capacity = Math.floor(minutes / LECTURE_MINUTES);
  let best: PrepStep[] = [];
  let bestValue = -1;
  const consider = (items: PrepStep[]) => {
    if (lock && !items.some((item) => item.questionId === lock)) return;
    const people = studentIds(items).length;
    const observations = items.reduce((sum, item) => sum + item.evidence.length, 0);
    // Roster size dominates ties. Then prefer more observed losses and fewer teaching slots.
    const value = people * 1_000_000 + observations * 10 - items.length;
    if (value > bestValue) { best = items; bestValue = value; }
  };
  if (capacity) {
    for (const [index, step] of steps.entries()) {
      consider([step]);
      if (capacity > 1) for (const next of steps.slice(index + 1)) {
        if (next.questionId !== step.questionId && next.evidence.length && step.evidence.length) consider([step, next]);
        else if (next.questionId !== step.questionId && lock && (next.questionId === lock || step.questionId === lock)
          && (next.evidence.length || step.evidence.length)) consider([step, next]);
      }
    }
  }
  return best.sort((a, b) => Number(b.questionId === lock) - Number(a.questionId === lock)
    || b.evidence.length - a.evidence.length || byId(a.key, b.key));
}

const followUpFor: Record<LiveEvidenceEntry['kind'], string> = {
  mid_wrong: '先复练去括号，核对符号和系数',
  combine_wrong: '先复练合并同类项',
  transcription_wrong: '过程正确，先核对末行与最终填答',
  final_wrong: '缺少合并步骤，先请学生补写',
  manual: '先由教师核对这份作答',
  correct: '本次核对通过，换一道题观察能否独立完成',
};

export function rehearse(context: Context, settings: PrepSettings, probeFirst: boolean): PrepPlan {
  const observation = context.practices.find((p) => p.key === settings.observationKey);
  const relevant = context.diagnoses.filter((d) => !settings.lock
    || d.indicator_id === context.questions.get(settings.lock)?.kpId);
  const suspects = relevant.flatMap((d) => d.question_findings.flatMap((f) => {
    if (f.kind !== '过程与填答不一致' || (settings.lock && f.item_id !== settings.lock)) return [];
    const answer = context.answers.find((a) => a.studentId === d.student_id && a.questionId === f.item_id && a.sourceRecordId === f.record_id);
    const resolved = observation && observation.original.answer.id === answer?.id
      && observation.entry.kind !== 'manual' && observation.entry.kind !== 'final_wrong';
    const item = pickQuestion(d.indicator_id, d);
    return answer && item && !resolved ? [{ focus: { answer, indicatorId: d.indicator_id },
      questionId: item.id, stem: item.stem, reason: '原作答的过程末行与最终填答不同，先分别写合并后的算式和最终答案。',
      minutes: PROBE_MINUTES }] : [];
  })).sort((a, b) => byId(a.focus.answer.studentId, b.focus.answer.studentId));
  const fallback = relevant.flatMap((d) => {
    if (d.coverage.sufficient) return [];
    const answer = context.answers.find((a) => a.studentId === d.student_id
      && context.questions.get(a.questionId)?.kpId === d.indicator_id && (!settings.lock || a.questionId === settings.lock));
    const item = d.pretest_questions.map((id) => context.questions.get(id))
      .find((q) => q && d.candidates.some((c) => c.material_id === q.id && !c.done && c.node_available)
        && q.questionType?.includes('填空'));
    if (!answer || !item || answer.id === observation?.original.answer.id) return [];
    return [{ focus: { answer, indicatorId: d.indicator_id }, questionId: item.id, stem: item.stem,
      reason: '这个考点的作答记录偏少，先看学生能否独立写出关键步骤。', minutes: PROBE_MINUTES }];
  });
  const probe = probeFirst ? suspects[0] || fallback[0] : undefined;
  const lessons = chooseLessons(context.steps, settings.minutes - (probe?.minutes || 0), settings.lock);
  const covered = studentIds(lessons).sort(byId);
  const individual = new Map<string, FollowUp>();
  const taught = new Set(lessons.map((step) => step.key));
  for (const step of context.steps.filter((step) => !taught.has(step.key))) for (const evidence of step.evidence) {
    const studentId = evidence.answer.studentId;
    if (!individual.has(studentId)) individual.set(studentId, {
      studentId, reason: `第 ${Number(evidence.answer.questionId.slice(1))} 题还有失分步骤未安排`, evidence,
    });
  }
  const plannedIndicators = new Set(lessons.map((step) => step.indicatorId));
  if (probe) plannedIndicators.add(probe.focus.indicatorId);
  const waiting = new Map<string, FollowUp>();
  for (const diagnosis of context.diagnoses.filter((d) => !d.coverage.sufficient && plannedIndicators.has(d.indicator_id))) {
    const studentId = diagnosis.student_id;
    if (waiting.has(studentId)) continue;
    const answer = context.answers.find((a) => a.studentId === studentId && context.questions.get(a.questionId)?.kpId === diagnosis.indicator_id
      && lessons.some((step) => step.questionId === a.questionId))
      || context.answers.find((a) => a.studentId === studentId && context.questions.get(a.questionId)?.kpId === diagnosis.indicator_id);
    waiting.set(studentId, { studentId, reason: '这个考点的作答记录不足，安排后仍需再看',
      ...(answer ? { evidence: { answer, indicatorId: diagnosis.indicator_id } } : {}) });
  }
  if (observation) individual.set(observation.entry.studentId, { studentId: observation.entry.studentId,
    reason: followUpFor[observation.entry.kind], evidence: observation.original });
  return {
    lessons, used: lessons.reduce((sum, step) => sum + step.minutes, probe?.minutes || 0), covered, probe,
    individual: [...individual.values()].sort((a, b) => byId(a.studentId, b.studentId)),
    waiting: [...waiting.values()].sort((a, b) => byId(a.studentId, b.studentId)),
  };
}
