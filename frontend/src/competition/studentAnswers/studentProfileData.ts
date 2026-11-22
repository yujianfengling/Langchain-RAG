import type { AnswerEvent, Bundle, CurriculumIndicator, Diagnosis, RealAnswer } from '../dataSource';
import type { KnowledgePoint, TextbookSource } from '../shared/types';
import type { LiveEvidenceEntry } from '../livePretest/LivePretest';

export type ProfileRecordStatus = '有效得分' | '有效零分' | '待评分' | '未作答' | '隔离待核' | '超出测验范围' | '状态待核';
export type ProfileIndicatorStatus = '本次未测' | '无记录' | '待评分' | '未作答' | '记录不足' | '有效零分' | '本组满分' | '本组表现较好' | '部分得分';

export interface ProfileAnswerRef {
  questionId: string;
  quizId: string;
  recordId?: string;
}

export interface ProfileRecord extends ProfileAnswerRef {
  key: string;
  status: ProfileRecordStatus;
  sourceStatus: string;
  score: number | null;
  fullScore: number | null;
  included: boolean;
  quarantined: boolean;
  outOfScope: boolean;
  answer?: RealAnswer;
  reasons: string[];
}

export interface ProfileTotals {
  score: number;
  fullScore: number;
  scoredCount: number;
  effectiveQuestionCount: number;
  recordCount: number;
  recordedQuestionCount: number;
  pendingCount: number;
  unansweredCount: number;
  zeroCount: number;
  quarantinedCount: number;
  outOfScopeCount: number;
  unknownCount: number;
}

export interface ProfileFinding {
  key: string;
  record: ProfileRecord;
  kind: string;
  fact: string;
  lastExpression?: string;
  finalAnswer?: string;
}

export interface ProfileIndicator {
  id: string;
  name: string;
  themeName: string;
  availableQuestionIds: string[];
  scopeKnown: boolean;
  status: ProfileIndicatorStatus;
  totals: ProfileTotals;
  records: ProfileRecord[];
  minEvidence: number | null;
  sufficient: boolean;
  coverageNote: string;
  findings: ProfileFinding[];
  textbook?: TextbookSource;
}

export interface ProfileQuiz {
  id: string;
  name: string;
  date: string;
  records: ProfileRecord[];
  totals: ProfileTotals;
  effectiveQuestionIds: string[];
}

export interface ProfileNextStep {
  indicatorId: string;
  title: string;
  reason: string;
  record?: ProfileRecord;
  textbook?: TextbookSource;
}

export interface StudentProfileData {
  student?: Bundle['students'][number];
  quiz?: Bundle['quizzes'][number];
  indicators: ProfileIndicator[];
  history: ProfileQuiz[];
  comparableQuestionIds: string[];
  nextSteps: ProfileNextStep[];
}

type ScopedKp = KnowledgePoint & { indicatorId?: string; quizId?: string };
const compareId = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true });
const finite = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value);
const scoredStatus = (status: string) => status === '有效得分' || status === '已评分';

/** 官方 kps 的 N01-T01I1 与 question.kpId 的 T01I1 使用同一指标键。 */
export function normalizeProfileIndicatorId(id: string, indicatorId?: string): string {
  return indicatorId || /-(T\d+I\d+)$/.exec(id)?.[1] || id;
}

function questionIndicators(bundle: Bundle, questionId: string): string[] {
  const question = bundle.questions.find((q) => q.id === questionId);
  return [...new Set([
    ...(question?.kpId ? question.kpId.split(/[,、|;]/).map((id) => normalizeProfileIndicatorId(id.trim())) : []),
    ...(bundle.curriculumMap?.indicators.filter((c) => c.questions.some((q) => q.material_id === questionId))
      .map((c) => c.indicator_id) || []),
  ])].filter(Boolean);
}

function indicatorMetadata(bundle: Bundle) {
  const result = new Map<string, { id: string; name: string; themeName: string; curriculum?: CurriculumIndicator }>();
  for (const curriculum of bundle.curriculumMap?.indicators || []) {
    result.set(curriculum.indicator_id, { id: curriculum.indicator_id, name: curriculum.requirement,
      themeName: curriculum.theme_name || bundle.topics.find((t) => t.id === curriculum.theme_id)?.name || curriculum.theme_id,
      curriculum });
  }
  for (const kp of bundle.kps as ScopedKp[]) {
    const id = normalizeProfileIndicatorId(kp.id, kp.indicatorId);
    if (!result.has(id)) result.set(id, { id, name: kp.name,
      themeName: bundle.topics.find((t) => t.id === kp.topicId)?.name || kp.topicId });
  }
  for (const question of bundle.questions) {
    for (const id of questionIndicators(bundle, question.id)) {
      if (!result.has(id)) result.set(id, { id, name: id, themeName: '' });
    }
  }
  return [...result.values()].sort((a, b) => compareId(a.id, b.id));
}

/** 仅本学生、本测验。事件状态优先；没有事件索引时才使用实际 answer 的分值。 */
export function getProfileRecords(bundle: Bundle, studentId: string, quizId: string): ProfileRecord[] {
  const allowed = bundle.quizzes.find((q) => q.id === quizId)?.availableItems;
  const events = (bundle.events || []).filter((e) => e.studentId === studentId && e.quizId === quizId);
  const answers = bundle.answers.filter((a) => a.studentId === studentId && a.quizId === quizId);
  const result = new Map<string, ProfileRecord>();
  const consumed = new Set<RealAnswer>();
  const append = (event: AnswerEvent | undefined, answer: RealAnswer | undefined) => {
    const questionId = event?.questionId || answer!.questionId;
    const recordId = event?.recordId || answer?.sourceRecordId || undefined;
    const key = `${quizId}|${questionId}|${recordId || answer?.id || 'event'}`;
    if (result.has(key)) return;
    const score = event ? event.score : answer!.score;
    const fullScore = event ? event.fullScore : answer!.fullScore;
    const sourceStatus = event?.status ?? '有效得分';
    const validScore = finite(score) && finite(fullScore) && fullScore > 0 && score >= 0 && score <= fullScore;
    const outOfScope = !!event?.outOfScope || (allowed !== undefined && !allowed.includes(questionId));
    const quarantined = !!event?.quarantined || (scoredStatus(sourceStatus)
      && finite(score) && finite(fullScore) && (score < 0 || fullScore <= 0 || score > fullScore));
    const included = !outOfScope && !quarantined && scoredStatus(sourceStatus) && validScore;
    const status: ProfileRecordStatus = outOfScope ? '超出测验范围' : quarantined ? '隔离待核'
      : included ? score === 0 ? '有效零分' : '有效得分'
      : sourceStatus === '待评分' ? '待评分' : sourceStatus === '未作答' ? '未作答' : '状态待核';
    const reasons = (bundle.anomalies || []).filter((a) => a.student_id === studentId && a.node_id === quizId
      && a.item_id === questionId && a.record_id === recordId).map((a) => a.reason);
    result.set(key, { key, questionId, quizId, recordId, status, sourceStatus,
      score: finite(score) ? score : null, fullScore: finite(fullScore) ? fullScore : null,
      included, quarantined, outOfScope, answer, reasons: [...new Set(reasons)] });
  };
  for (const event of events) {
    const answer = answers.find((a) => a.questionId === event.questionId && a.sourceRecordId === event.recordId)
      || (events.filter((e) => e.questionId === event.questionId).length === 1
        ? answers.find((a) => a.questionId === event.questionId && !a.sourceRecordId) : undefined);
    if (answer) consumed.add(answer);
    append(event, answer);
  }
  for (const answer of answers) {
    if (consumed.has(answer)) continue;
    // 无来源编号的旧 answer 不与同题事件重复计分，也不猜它属于哪次独立作答。
    if (!answer.sourceRecordId && events.some((e) => e.questionId === answer.questionId)) continue;
    append(undefined, answer);
  }
  return [...result.values()].sort((a, b) => compareId(a.questionId, b.questionId) || compareId(a.key, b.key));
}

/** 按唯一实际记录求和，不累加各考点（同一道题可能关联多个指标）。 */
export function summarizeProfileRecords(records: ProfileRecord[]): ProfileTotals {
  const unique = [...new Map(records.map((r) => [r.key, r])).values()];
  const scored = unique.filter((r) => r.included);
  return {
    score: scored.reduce((sum, r) => sum + r.score!, 0),
    fullScore: scored.reduce((sum, r) => sum + r.fullScore!, 0),
    scoredCount: scored.length,
    effectiveQuestionCount: new Set(scored.map((r) => r.questionId)).size,
    recordCount: unique.length,
    recordedQuestionCount: new Set(unique.map((r) => r.questionId)).size,
    pendingCount: unique.filter((r) => r.sourceStatus === '待评分').length,
    unansweredCount: unique.filter((r) => r.sourceStatus === '未作答').length,
    zeroCount: scored.filter((r) => r.score === 0).length,
    quarantinedCount: unique.filter((r) => r.quarantined).length,
    outOfScopeCount: unique.filter((r) => r.outOfScope).length,
    unknownCount: unique.filter((r) => r.status === '状态待核').length,
  };
}

function getFindings(records: ProfileRecord[], diagnosis?: Diagnosis): ProfileFinding[] {
  const result: ProfileFinding[] = [];
  for (const record of records.filter((r) => r.included)) {
    const findings = (diagnosis?.question_findings || []).filter((f) => f.item_id === record.questionId
      && !!record.recordId && f.record_id === record.recordId);
    for (const finding of findings) {
      result.push({ key: `${record.key}|${finding.kind}`, record, kind: finding.kind, fact: finding.fact,
        lastExpression: finding.last_expr, finalAnswer: finding.final_answer });
    }
    if (!findings.length && record.score! < record.fullScore!) {
      const lost = record.answer?.scoringPoints.filter((p) => p.earnedScore < p.fullScore)
        .map((p) => `${p.label}（${p.earnedScore}/${p.fullScore}）`).join('、');
      result.push({ key: `${record.key}|score`, record, kind: '本题失分',
        fact: `${record.questionId} 得分 ${record.score}/${record.fullScore}；${lost || '未提供失分评分点明细，请结合答卷核对。'}` });
    }
  }
  return [...new Map(result.map((f) => [f.key, f])).values()];
}

function getTextbook(curriculum?: CurriculumIndicator, diagnosis?: Diagnosis): TextbookSource | undefined {
  const page = curriculum?.textbook_pages.find((p) => p.material_id && p.material_id !== '待定位' && p.pdf_page > 0)
    || diagnosis?.textbook;
  if (!page?.material_id || page.material_id === '待定位' || !page.pdf_page || page.pdf_page < 1) return undefined;
  return {
    docId: page.material_id, docName: `赛方教材切片 ${page.material_id}`, page: page.pdf_page,
    exampleTitle: `${curriculum?.requirement || diagnosis?.indicator_id || ''}；${page.anchor || ''}`,
    pdfUrl: `/api/competition/official-pdf/${encodeURIComponent(page.material_id)}`,
    printedPage: page.printed, verified: page.verified,
    standard: curriculum?.standard.code ? { code: curriculum.standard.code,
      pdfPage: curriculum.standard.pdf_page ?? null, printed: curriculum.standard.printed ?? null } : undefined,
  };
}

/** 记录少于正式 coverage.min_evidence 时，即使全满分也不提升为掌握判断。 */
export function getProfileIndicatorStatus(totals: ProfileTotals, tested: boolean | null, sufficient: boolean,
  goodThreshold?: number): ProfileIndicatorStatus {
  if (tested === false) return '本次未测';
  if (!totals.recordCount) return '无记录';
  if (!totals.scoredCount) {
    if (totals.pendingCount) return '待评分';
    if (totals.unansweredCount) return '未作答';
    return '记录不足';
  }
  if (!sufficient) return '记录不足';
  if (totals.score === 0) return '有效零分';
  if (totals.score === totals.fullScore) return '本组满分';
  if (finite(goodThreshold) && totals.score / totals.fullScore >= goodThreshold) return '本组表现较好';
  return '部分得分';
}

export function buildProfileNextSteps(indicators: ProfileIndicator[]): ProfileNextStep[] {
  return indicators.filter((row) => row.status !== '本次未测').map((row) => {
    const mismatch = row.findings.find((f) => f.kind === '过程与填答不一致');
    const lost = row.findings[0];
    const record = mismatch?.record || lost?.record || row.records.find((r) => r.included) || row.records[0];
    let title: string;
    let reason: string;
    if (mismatch) {
      title = '核对过程末行与最终填答';
      reason = `${record!.questionId} 的过程与填答不同，只确认差异，不推断出错原因。`;
    } else if (!row.totals.scoredCount) {
      title = row.totals.pendingCount ? '先完成评分，再补充观察' : row.totals.unansweredCount ? '核对未作答状态，补充观察' : '需补充观察';
      reason = '本测验暂无可计分记录，不以缺记录判断不会。';
    } else if (!row.sufficient) {
      title = '需补充观察';
      reason = `本组 ${row.totals.score}/${row.totals.fullScore} 分、${row.totals.scoredCount} 条有效记录；${row.coverageNote}`;
    } else if (row.totals.score === row.totals.fullScore) {
      title = '可由教师选拓展题';
      reason = `本组 ${row.totals.scoredCount} 条已评分记录均为满分，仅说明本组表现。`;
    } else {
      title = '核对本题失分步骤';
      reason = record ? `${record.questionId} 有实际失分记录，先对照评分点，再由教师选择后续安排。` : '结合本人答卷核对评分点。';
    }
    if (mismatch && !row.sufficient) reason += ` ${row.coverageNote}`;
    return { indicatorId: row.id, title, reason, record, textbook: row.textbook };
  });
}

/** 无数据源时返回诚实空态，不读取 demo 分数或按学生编号造诊断。 */
export function buildStudentProfile(bundle: Bundle | null, studentId: string, quizId: string): StudentProfileData {
  if (!bundle) return { indicators: [], history: [], comparableQuestionIds: [], nextSteps: [] };
  const student = bundle.students.find((s) => s.id === studentId);
  const quiz = bundle.quizzes.find((q) => q.id === quizId);
  const currentRecords = getProfileRecords(bundle, studentId, quizId);
  const metadata = indicatorMetadata(bundle);
  const indicators = metadata.map((meta): ProfileIndicator => {
    const diagnosis = bundle.diagnoses?.diagnoses.find((d) => d.student_id === studentId && d.node_id === quizId
      && normalizeProfileIndicatorId(d.indicator_id) === meta.id);
    const records = currentRecords.filter((r) => questionIndicators(bundle, r.questionId).includes(meta.id));
    const availableQuestionIds = (quiz?.availableItems || []).filter((id) => questionIndicators(bundle, id).includes(meta.id));
    const scopeKnown = quiz?.availableItems !== undefined;
    const totals = summarizeProfileRecords(records);
    const threshold = diagnosis?.coverage?.min_evidence ?? bundle.diagnoses?.threshold.min_evidence;
    const minEvidence = finite(threshold) && threshold > 0 ? threshold : null;
    const sufficient = minEvidence !== null && totals.scoredCount >= minEvidence && diagnosis?.coverage?.sufficient !== false;
    const status = getProfileIndicatorStatus(totals, scopeKnown ? availableQuestionIds.length > 0 : null,
      sufficient, bundle.diagnoses?.threshold.good);
    const coverageNote = status === '本次未测' ? '正式题目范围未覆盖此考点，不是学生缺记录。'
      : !records.length ? `${scopeKnown ? '本次题目范围覆盖此考点，' : '正式题目范围未提供，'}该生无记录，需补充观察。`
      : minEvidence === null ? '未提供正式覆盖阈值，不推断整体掌握。'
      : !sufficient ? `${totals.scoredCount} 条有效记录${totals.scoredCount < minEvidence ? `不足 ${minEvidence} 条` : '，正式诊断仍标为覆盖不足'}，不推断整体掌握。`
      : `达到正式最少 ${minEvidence} 条记录要求，仅描述本次已观察题目。`;
    return { id: meta.id, name: meta.name, themeName: meta.themeName, availableQuestionIds, scopeKnown,
      status, totals, records, minEvidence, sufficient, coverageNote,
      findings: getFindings(records, diagnosis), textbook: getTextbook(meta.curriculum, diagnosis) };
  });
  // 不从 indicators 合计，防止多指标题重复计分。
  const history = bundle.quizzes.map((q): ProfileQuiz => {
    const records = q.id === quizId ? currentRecords : getProfileRecords(bundle, studentId, q.id);
    return { id: q.id, name: q.name, date: q.date, records, totals: summarizeProfileRecords(records),
      effectiveQuestionIds: [...new Set(records.filter((r) => r.included).map((r) => r.questionId))].sort(compareId) };
  }).sort((a, b) => a.date.localeCompare(b.date) || compareId(a.id, b.id));
  const comparableQuestionIds = history.length > 1
    ? history[0].effectiveQuestionIds.filter((id) => history.every((q) => q.effectiveQuestionIds.includes(id))) : [];
  return { student, quiz, indicators, history, comparableQuestionIds, nextSteps: buildProfileNextSteps(indicators) };
}

/** 跨测验、跨指标读取本人本地练习；不把练习并入任何正式统计。 */
export function getProfilePracticeScopes(bundle: Bundle | null): { quizId: string; indicatorId: string }[] {
  if (!bundle) return [];
  const quizzes = new Set([...bundle.quizzes.map((q) => q.id),
    ...(bundle.diagnoses?.diagnoses.map((d) => d.node_id) || []),
    ...bundle.answers.map((a) => a.quizId), ...(bundle.events || []).map((e) => e.quizId)]);
  const indicators = new Set([...indicatorMetadata(bundle).map((i) => i.id),
    ...(bundle.diagnoses?.diagnoses.map((d) => d.indicator_id) || [])]);
  return [...quizzes].flatMap((quizId) => [...indicators].map((indicatorId) => ({ quizId, indicatorId })));
}

export function selectStudentPracticeEntries(entries: LiveEvidenceEntry[], studentId: string): LiveEvidenceEntry[] {
  return entries.filter((e) => e && e.studentId === studentId && typeof e.questionId === 'string'
    && typeof e.nodeId === 'string' && typeof e.indicatorId === 'string' && typeof e.at === 'string'
    && typeof e.intermediate === 'string' && typeof e.final === 'string'
    && (e.combined === undefined || typeof e.combined === 'string')
    && typeof e.supports === 'string' && typeof e.followUp === 'string')
    .slice().sort((a, b) => b.at.localeCompare(a.at));
}
