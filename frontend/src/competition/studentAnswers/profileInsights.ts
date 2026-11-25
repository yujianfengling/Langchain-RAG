import type { ProfileQuiz, ProfileRecord, StudentProfileData } from './studentProfileData';

export function summarizeEvidence(records: ProfileRecord[]) {
  const scored = records.filter((r) => r.included);
  const withRubric = scored.filter(({ answer }) => answer && !answer.rubricUnavailable
    && answer.scoringPoints.length > 0
    && answer.scoringPoints.every((p) => Number.isFinite(p.earnedScore) && p.earnedScore >= 0 && p.earnedScore <= p.fullScore)
    && Math.abs(answer.scoringPoints.reduce((sum, p) => sum + p.earnedScore, 0) - answer.score) < 0.001
    && Math.abs(answer.scoringPoints.reduce((sum, p) => sum + p.fullScore, 0) - answer.fullScore) < 0.001);
  const genuineLosses = withRubric.flatMap((record) => record.answer!.scoringPoints
    .filter((point) => point.earnedScore < point.fullScore)
    .map((point) => ({ record, point, loss: point.fullScore - point.earnedScore })))
    .sort((a, b) => b.loss - a.loss || a.record.questionId.localeCompare(b.record.questionId, undefined, { numeric: true }));
  return {
    full: scored.filter((r) => r.score === r.fullScore).length,
    partial: scored.filter((r) => r.score! > 0 && r.score! < r.fullScore!).length,
    zero: scored.filter((r) => r.score === 0).length,
    withSteps: scored.filter(({ answer }) => answer && !answer.stepsUnavailable && answer.steps.some((step) => step.text.trim())).length,
    rubricCount: withRubric.length,
    genuineLosses,
  };
}

function singleScoredRecord(quiz: ProfileQuiz, questionId: string): ProfileRecord | undefined {
  const matching = quiz.records.filter((r) => r.questionId === questionId && r.included);
  return matching.length === 1 ? matching[0] : undefined;
}

/** Compare the same items with one valid record per assessment and identical full marks. */
export function compareRepeatedItems(profile: StudentProfileData) {
  const current = profile.history.find((q) => q.id === profile.quiz?.id);
  if (!current) return null;
  const previous = profile.history[profile.history.indexOf(current) - 1];
  if (!previous) return null;
  const rows = current.effectiveQuestionIds.flatMap((id) => {
    const before = singleScoredRecord(previous, id);
    const after = singleScoredRecord(current, id);
    return before && after && before.fullScore === after.fullScore ? [{ id, before, after }] : [];
  });
  return { previous, current, rows };
}
