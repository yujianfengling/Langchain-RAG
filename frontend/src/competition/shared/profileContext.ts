import type { Bundle } from '../dataSource';
import { buildStudentProfile, getProfilePracticeScopes, selectStudentPracticeEntries } from '../studentAnswers/studentProfileData';
import { readLiveEvidence } from '../livePretest/LivePretest';

/**
 * 个人学情档案的紧凑上下文（跨测验历史 + 考点掌握 + 错因 + 练习/补测证据 + 下一步）。
 * 供 AI 诊断 / AI 出题 / AI 教案共用，确保大模型真正参考个人档案而非单题切片。
 * 全部本地现算、脱敏聚合，不含原始答卷全文。
 */
export function buildProfileContext(bundle: Bundle, studentId: string, quizId: string) {
  const profile = buildStudentProfile(bundle, studentId, quizId);
  const history = profile.history.map((q) => ({
    quiz: q.id, name: q.name, date: q.date, score: q.totals.score, full: q.totals.fullScore,
    rate: q.totals.fullScore > 0 ? q.totals.score / q.totals.fullScore * 100 : null,
    scored: q.totals.scoredCount, pending: q.totals.pendingCount, unanswered: q.totals.unansweredCount,
  }));
  const indicators = profile.indicators.filter((r) => r.totals.recordCount > 0).map((r) => ({
    id: r.id, name: r.name, status: r.status, score: r.totals.score, full: r.totals.fullScore,
    events: r.totals.scoredCount, sufficient: r.sufficient,
  }));
  const findings = profile.indicators.flatMap((r) => r.findings).slice(0, 10).map((f) => ({ kind: f.kind, fact: f.fact }));
  const practice = selectStudentPracticeEntries(getProfilePracticeScopes(bundle).flatMap(({ quizId: node, indicatorId }) => {
    const saved = readLiveEvidence(studentId, node, indicatorId);
    return Array.isArray(saved) ? saved : [];
  }), studentId).slice(0, 8).map((e) => ({ question: e.questionId, kind: e.kind, at: e.at }));
  const nextSteps = profile.nextSteps.slice(0, 6).map((s) => ({ title: s.title, reason: s.reason }));
  return { history, indicators, findings, practice, next_steps: nextSteps };
}

export type ProfileContext = ReturnType<typeof buildProfileContext>;