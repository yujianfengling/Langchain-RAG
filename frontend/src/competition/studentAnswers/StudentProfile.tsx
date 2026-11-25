import React, { useEffect, useMemo, useState } from 'react';
import { Button, Tooltip } from 'antd';
import {
  AimOutlined, BookOutlined, EditOutlined, FieldTimeOutlined, FileDoneOutlined, FlagOutlined,
  HistoryOutlined, LineChartOutlined, PartitionOutlined, PercentageOutlined, QuestionCircleOutlined,
  RadarChartOutlined, WarningOutlined,
} from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';
import { questionLabel } from '../shared/teacherContext';
import { getBundle, getSourceLabel, isPrivateCompetitionData } from '../dataSource';
import { readLiveEvidence } from '../livePretest/LivePretest';
import type { LiveEvidenceEntry } from '../livePretest/LivePretest';
import type { TextbookSource } from '../shared/types';
import { summarize } from '../shared/examReport';
import {
  buildStudentProfile, getProfilePracticeScopes, selectStudentPracticeEntries,
} from './studentProfileData';
import type { ProfileAnswerRef, ProfileIndicator, ProfileRecord, ProfileTotals } from './studentProfileData';
import ProfileEvidence from './ProfileEvidence';
import ProfileEvidenceMap from './ProfileEvidenceMap';
import ProfileFollowUp from './ProfileFollowUp';
import { ProfileTrendChart, ProfileRadarChart } from './ProfileCharts';
import AiDiagnosisCard from './AiDiagnosisCard';
import StudyPack from '../shared/StudyPack';
import './studentProfile.css';

export interface StudentProfileProps {
  studentId: string;
  quizId: string;
  focusQuestionId?: string;
  onOpenAnswer: (questionId: string, quizId: string, recordId?: string) => void;
  onOpenTextbook: (source: TextbookSource) => void;
  onStartPractice: (original: ProfileAnswerRef, practiceQuestion: string, hint?: string) => void;
}

const PRACTICE_LABELS: Record<LiveEvidenceEntry['kind'], string> = {
  mid_wrong: '去括号需要复查',
  combine_wrong: '合并同类项需要复查',
  transcription_wrong: '合并正确，请核对最终填答',
  final_wrong: '请补充合并后的过程末行',
  manual: '待人工核对输入',
  correct: '本次输入核对通过',
};
const numberText = (value: number) => Number(value.toFixed(4)).toString();
const scoreText = (totals: ProfileTotals) => totals.scoredCount
  ? `${numberText(totals.score)}/${numberText(totals.fullScore)} 分`
  : '暂无有效已评分得分';

function StatusCounts({ totals }: { totals: ProfileTotals }) {
  return <div className="teacher-muted student-profile-status-counts">
    <span>待评分 {totals.pendingCount} 条</span><span>未作答 {totals.unansweredCount} 条</span>
    {totals.zeroCount > 0 && <span>有效零分 {totals.zeroCount} 条</span>}
    {totals.quarantinedCount > 0 && <span>隔离待核 {totals.quarantinedCount} 条</span>}
    {totals.outOfScopeCount > 0 && <span>超范围 {totals.outOfScopeCount} 条</span>}
    {totals.unknownCount > 0 && <span>状态待核 {totals.unknownCount} 条</span>}
  </div>;
}

function RecordList({ records, onOpenAnswer }: { records: ProfileRecord[]; onOpenAnswer: StudentProfileProps['onOpenAnswer'] }) {
  return <ul className="student-profile-records">
    {records.map((record) => <li key={record.key}>
      <Button type="link" className="student-profile-link" onClick={() => onOpenAnswer(record.questionId, record.quizId, record.recordId)}>
        {record.questionId} · {record.included ? `${numberText(record.score!)}/${numberText(record.fullScore!)} 分` : record.status}
      </Button>
      <span className="teacher-muted">{record.quizId} · 记录 {record.recordId || '编号未提供'}</span>
      {!record.included && <div className="teacher-muted">
        原状态：{record.sourceStatus || '未提供'}；不计入得分与满分。
        {record.quarantined && ' 隔离待核。'}{record.outOfScope && ' 超出测验范围。'}
        {(record.quarantined || record.outOfScope) && record.score !== null && record.fullScore !== null
          && ` 原始分值 ${numberText(record.score)}/${numberText(record.fullScore)}（未计入）。`}
        {record.reasons.join('；')}
      </div>}
    </li>)}
  </ul>;
}

function indicatorTarget(row: ProfileIndicator, focusQuestionId?: string): ProfileAnswerRef | undefined {
  return row.records.find((r) => r.questionId === focusQuestionId)
    || row.findings[0]?.record || row.records.find((r) => r.included) || row.records[0];
}

function practiceTime(at: string): string {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? at : date.toLocaleString('zh-CN', { hour12: false });
}

function jumpToSection(id: string) {
  const section = document.getElementById(id);
  const main = section?.closest('.comp-main');
  if (!section || !main) return;
  const header = section.closest('.teacher-answers-page')?.querySelector('.teacher-page-header');
  const offset = section.getBoundingClientRect().top - main.getBoundingClientRect().top;
  main.scrollTo({ top: main.scrollTop + offset - (header?.clientHeight || 0) - 12 });
}

const viewFollowUp = () => jumpToSection('student-follow-up');

const StudentProfile: React.FC<StudentProfileProps> = ({ studentId, quizId, focusQuestionId, onOpenAnswer, onOpenTextbook, onStartPractice }) => {
  const bundle = getBundle();
  const [params, setParams] = useSearchParams();
  const profile = useMemo(() => buildStudentProfile(bundle, studentId, quizId), [bundle, studentId, quizId]);
  const [, setPracticeRevision] = useState(0);
  const preferredFollowup = params.get('followup');
  const practice = selectStudentPracticeEntries(getProfilePracticeScopes(bundle).flatMap(({ quizId: nodeId, indicatorId }) => {
    const saved = readLiveEvidence(studentId, nodeId, indicatorId);
    return Array.isArray(saved) ? saved.filter((entry) => entry && entry.nodeId === nodeId && entry.indicatorId === indicatorId) : [];
  }), studentId);
  useEffect(() => {
    const refresh = () => setPracticeRevision((value) => value + 1);
    window.addEventListener('focus', refresh);
    window.addEventListener('storage', refresh);
    return () => { window.removeEventListener('focus', refresh); window.removeEventListener('storage', refresh); };
  }, []);
  const open = (target: ProfileAnswerRef) => onOpenAnswer(target.questionId, target.quizId, target.recordId);
  const findings = [...new Map(profile.indicators.flatMap((row) => row.findings).map((f) => [f.key, f])).values()];
  const className = bundle?.classes.find((c) => c.id === profile.student?.classId)?.name || profile.student?.classId;
  const studentName = profile.student?.name || studentId;
  const returnedPractice = practice.find((entry) => entry.id === params.get('practiceRecord'));
  const historyChart = bundle && profile.history.map((quiz) => {
    const studentRate = quiz.totals.fullScore > 0 ? quiz.totals.score / quiz.totals.fullScore * 100 : null;
    const classStudents = bundle.students.filter(s => s.classId === profile.student?.classId);
    const rates = classStudents.map(s => summarize(bundle.answers.filter(a => a.studentId === s.id && a.quizId === quiz.id)))
      .filter(x => x.rate != null).map(x => x.rate!);
    const sampleRates = bundle.students.map(s => summarize(bundle.answers.filter(a=>a.studentId===s.id&&a.quizId===quiz.id)))
      .filter(x=>x.rate!=null).map(x=>x.rate!);
    return { id: quiz.id, name: quiz.name, date: quiz.date, studentRate,
      classRate: rates.length ? rates.reduce((sum, rate) => sum + rate, 0) / rates.length : null,
      sampleRate: sampleRates.length ? sampleRates.reduce((sum,rate)=>sum+rate,0)/sampleRates.length : null };
  }).sort((a,b) => a.date.localeCompare(b.date)) || [];
  const currentTotals = profile.history.find((q) => q.id === quizId)?.totals;
  const currentStudentRate = historyChart.find((q) => q.id === quizId)?.studentRate ?? null;
  const currentClassRate = historyChart.find((q) => q.id === quizId)?.classRate ?? null;
  const rateDelta = currentStudentRate != null && currentClassRate != null ? currentStudentRate - currentClassRate : null;
  const [packIndicator, setPackIndicator] = useState<string | null>(null);
  const packDefault = (profile.indicators.find((r) => r.status === '部分得分' || r.status === '有效零分')
    || profile.indicators.find((r) => r.totals.scoredCount > 0))?.id;
  // 原答卷记录号：优先用进入练习时的原题记录；若该参数为空（例如从案例入口进来时
  // 没有 recordId），按学生+测验+题目从已构建的正式档案里查，绝不回退到切换后的新题。
  const originalQuestionId = params.get('practiceOriginalQuestion') || focusQuestionId || '';
  const originalRecordId = params.get('practiceOriginalRecord')
    || profile.history.find((q) => q.id === quizId)?.records.find((r) => r.questionId === originalQuestionId)?.recordId
    || undefined;
  const viewPractice = () => document.querySelector('[data-testid="student-profile-practice"]')?.scrollIntoView({ block: 'start' });
  const followUpFromMap = (indicatorId: string, questionId: string, recordId?: string) => {
    const next = new URLSearchParams(params);
    next.set('followup', indicatorId);
    next.set('kpId', indicatorId);
    next.set('questionId', questionId);
    if (recordId) next.set('recordId', recordId); else next.delete('recordId');
    setParams(next, { replace: true });
    requestAnimationFrame(viewFollowUp);
  };
  useEffect(() => {
    if (!preferredFollowup) return;
    const frame = requestAnimationFrame(viewFollowUp);
    return () => cancelAnimationFrame(frame);
  }, [preferredFollowup]);

  return <div className="student-profile" data-testid="student-profile" data-student-id={studentId} data-quiz-id={quizId}>
    <header className="student-profile-intro">
      <h2>{studentName || '请选择学生'}的个人档案</h2>
      <p className="teacher-muted">
        {studentName !== studentId && studentId ? `编号 ${studentId} · ` : ''}{className ? `${className} · ` : ''}{getSourceLabel()}
      </p>
      {bundle && !profile.student && <p className="teacher-muted">当前名册未找到该学生，不补造姓名或作答记录。</p>}
      <div className="teacher-actions">
        {isPrivateCompetitionData() && <Button type="primary" icon={<FieldTimeOutlined aria-hidden />} onClick={viewFollowUp}>课后五分钟补测</Button>}
        {isPrivateCompetitionData() && profile.quiz && profile.student && <Button type="text" icon={<PartitionOutlined aria-hidden />} onClick={() => jumpToSection('student-evidence-map')}>看证据图</Button>}
        <Button type="text" icon={<HistoryOutlined aria-hidden />} onClick={() => jumpToSection('student-history')}>历次测验</Button>
        <Button type="text" icon={<FlagOutlined aria-hidden />} onClick={() => jumpToSection('student-next-steps')}>下一步安排</Button>
        <Button type="text" icon={<EditOutlined aria-hidden />} onClick={viewPractice}>查看练习记录（{practice.length} 条）</Button>
      </div>
      {returnedPractice && <p className="teacher-result-note" role="status" data-testid="profile-practice-notice">
        已记录{questionLabel(returnedPractice.questionId)}同类练习：{PRACTICE_LABELS[returnedPractice.kind]}。练习不计入原测验成绩。
        {focusQuestionId && <Button type="link" size="small" style={{ marginLeft: 12 }}
          data-testid="profile-open-original-answer"
          onClick={() => onOpenAnswer(originalQuestionId, quizId, originalRecordId || '')}>查看原答卷</Button>}
      </p>}
    </header>

    <section className="teacher-section student-profile-overview" aria-label="个人学情总览">
      <div className="teacher-section-heading"><h3><LineChartOutlined aria-hidden />学情总览</h3><span className="teacher-muted">{profile.quiz?.name || quizId}</span></div>
      <div className="student-profile-overview-grid">
        <article><span><FileDoneOutlined aria-hidden />本次有效得分</span><strong>{scoreText(currentTotals || {score:0,fullScore:0,scoredCount:0} as ProfileTotals)}</strong>
          <small>{currentTotals?.effectiveQuestionCount || 0} 道有效题目</small></article>
        <article><span><PercentageOutlined aria-hidden />本次得分率</span><strong>{currentStudentRate == null ? '—' : `${currentStudentRate.toFixed(0)}%`}</strong>
          <small>{rateDelta == null ? '暂无本班均值' : `${rateDelta >= 0 ? '高于' : '低于'}本班平均 ${Math.abs(rateDelta).toFixed(0)} 个百分点`}</small></article>
        <article><span><WarningOutlined aria-hidden />需要关注</span><strong>{profile.indicators.filter(row=>row.status==='部分得分'||row.status==='有效零分').length} 个考点</strong>
          <small>按本次有效作答定位</small></article>
        <article><span><QuestionCircleOutlined aria-hidden />记录不足</span><strong>{profile.indicators.filter(row=>row.status==='记录不足').length} 个考点</strong>
          <small>补充作答后再判断</small></article>
      </div>
      <div className="student-profile-charts">
        {historyChart.length > 1 && <figure className="student-profile-chart"><figcaption><LineChartOutlined aria-hidden />历次测验得分率趋势
          <Tooltip title="按各场有效已评分答卷计算。各场题目覆盖不同，同题变化见下方对照。两班样本均值覆盖赛题提供的两个班，不代表全年级全体。"><QuestionCircleOutlined aria-hidden className="teacher-note-hint" /></Tooltip></figcaption>
          <ProfileTrendChart data={historyChart} showSample /></figure>}
        {profile.indicators.some((r) => r.totals.scoredCount > 0) && <figure className="student-profile-chart"><figcaption><RadarChartOutlined aria-hidden />本次考点掌握雷达
          <Tooltip title="仅纳入本次有有效已评分记录的考点；缺记录不画成零分。"><QuestionCircleOutlined aria-hidden className="teacher-note-hint" /></Tooltip></figcaption>
          <ProfileRadarChart indicators={profile.indicators} /></figure>}
      </div>
      <div className="student-profile-focus-list">{profile.indicators.filter(row=>row.status==='部分得分'||row.status==='有效零分').sort((a,b)=>a.totals.fullScore? a.totals.score/a.totals.fullScore-(b.totals.fullScore?b.totals.score/b.totals.fullScore:1):0).slice(0,3).map(row=><Button key={row.id} icon={<WarningOutlined aria-hidden />} onClick={()=>document.querySelector(`[data-indicator-id="${row.id}"]`)?.scrollIntoView({block:'center',behavior:'smooth'})}>{row.name} · {scoreText(row.totals)}</Button>)}{packDefault && <Button type="primary" ghost icon={<BookOutlined aria-hidden />} data-testid="study-pack-open" onClick={()=>setPackIndicator(packDefault)}>生成学习包（闪卡/导图/变式测）</Button>}</div>
    </section>

    {bundle && profile.student && <AiDiagnosisCard profile={profile} studentId={studentId} quizId={quizId} studentName={studentName} />}

    <ProfileEvidence profile={profile} focusQuestionId={focusQuestionId} onOpenAnswer={open} />
    {bundle && isPrivateCompetitionData() && <ProfileEvidenceMap key={`${focusQuestionId}|${params.get('recordId')}`}
      bundle={bundle} profile={profile} focusQuestionId={focusQuestionId} focusRecordId={params.get('recordId')}
      practice={practice} onOpenAnswer={open} onOpenTextbook={onOpenTextbook} onFollowUp={followUpFromMap} />}
    {bundle && isPrivateCompetitionData() && <ProfileFollowUp key={preferredFollowup || 'default'} bundle={bundle} profile={profile}
      focusQuestionId={focusQuestionId} focusRecordId={params.get('recordId')} preferredIndicator={preferredFollowup} practice={practice}
      onOpenAnswer={open} onStartPractice={onStartPractice} />}

    <section className="teacher-section" aria-label="本次考点表现">
      <div className="teacher-section-heading"><h3><AimOutlined aria-hidden />本次考点表现</h3><span className="teacher-muted">
        {profile.quiz?.name || quizId}{profile.quiz?.date ? ` · ${profile.quiz.date}` : ''}
      </span></div>
      <details className="teacher-note-fold"><summary><QuestionCircleOutlined aria-hidden />统计口径说明</summary>
        <p className="teacher-muted">仅计算本学生、本测验有效已评分记录。零分是有效成绩；待评分、未作答、隔离及超范围不计入得分或满分。</p>
      </details>
      {!bundle ? <p className="student-profile-empty">暂无官方档案数据。演示样例不作为该生的正式成绩或诊断。</p>
        : !profile.quiz ? <p className="student-profile-empty">未找到所选测验，不使用其他测验记录补齐。</p>
          : <>
            <div className="student-profile-table-heading" aria-hidden="true"><span>考点</span><span>本次记录与状态</span><span>查看依据</span></div>
            <ul className="student-profile-indicators">
              {profile.indicators.map((row) => {
                const target = indicatorTarget(row, focusQuestionId);
                const focused = !!focusQuestionId && row.records.some((r) => r.questionId === focusQuestionId);
                return <li key={row.id} className={`student-profile-indicator${focused ? ' student-profile-focused' : ''}`}
                  data-indicator-id={row.id} data-status={row.status}>
                  <div>
                    <div className="teacher-muted">{row.themeName}</div>
                    {target ? <Button type="link" className="student-profile-link student-profile-indicator-title" onClick={() => open(target)}>{row.name}</Button>
                      : <strong>{row.name}</strong>}
                  </div>
                  <div>
                    <strong className="student-profile-status">{row.status}</strong>
                    <div>{scoreText(row.totals)} · {row.totals.scoredCount} 条有效记录</div>
                    <p className="teacher-muted">{row.coverageNote}</p>
                    <StatusCounts totals={row.totals} />
                  </div>
                  <div>
                    {target ? <Button size="small" onClick={() => open(target)}>查看 {target.questionId} 答卷</Button>
                      : row.availableQuestionIds.length > 0
                        ? <Button size="small" onClick={() => onOpenAnswer(row.availableQuestionIds[0], quizId)}>查看 {row.availableQuestionIds[0]}（无记录）</Button>
                        : <span className="teacher-muted">本次无可查看题目</span>}
                    {row.records.length > 0 && <details>
                      <summary>记录明细（{row.totals.recordCount} 条）</summary>
                      <RecordList records={row.records} onOpenAnswer={onOpenAnswer} />
                    </details>}
                  </div>
                </li>;
              })}
            </ul>
            <div className="student-profile-findings">
              <h4><WarningOutlined aria-hidden />失分与差异发现</h4>
              {findings.length ? <ul className="student-profile-list">
                {findings.map((finding) => <li key={finding.key} data-question-id={finding.record.questionId}
                  className={finding.record.questionId === focusQuestionId ? 'student-profile-focused' : undefined}>
                  <Button type="link" className="student-profile-link" onClick={() => open(finding.record)}>
                    {finding.record.questionId} · {finding.kind} · 查看本题答卷
                  </Button>
                  {finding.lastExpression === undefined && finding.finalAnswer === undefined && <p>{finding.fact}</p>}
                  {(finding.lastExpression !== undefined || finding.finalAnswer !== undefined) && <div className="student-profile-expressions">
                    <div><span className="teacher-muted">过程末行</span><div>{finding.lastExpression || '未提供'}</div></div>
                    <div><span className="teacher-muted">最终填答</span><div>{finding.finalAnswer || '未提供'}</div></div>
                  </div>}
                  <span className="teacher-muted">{finding.record.quizId} · 记录 {finding.record.recordId || '编号未提供'}；仅说明本题观察。</span>
                </li>)}
              </ul> : <p className="teacher-muted">当前没有可定位到有效答卷的失分或差异发现，不等同于已掌握全部考点。</p>}
            </div>
          </>}
    </section>

    <section id="student-history" className="teacher-section" aria-label="历次测验">
      <div className="teacher-section-heading"><h3><HistoryOutlined aria-hidden />历次测验</h3></div>
      <details className="teacher-note-fold"><summary><QuestionCircleOutlined aria-hidden />统计口径说明</summary>
        <p className="teacher-muted">得分 / 满分仅覆盖实际有效已评分记录，并非整张试卷总分。每条记录只计一次，不累加各考点，不含后续练习。</p>
      </details>
      {!profile.history.length ? <p className="student-profile-empty">暂无正式测验记录。</p> : <>
        {historyChart.length > 1 && <div className="student-profile-trend" aria-label="学生与班级历次测验得分率趋势">
          <ProfileTrendChart data={historyChart} showSample={false} height={220} />
          <p className="teacher-muted">各场按有效已评分答卷计算得分率。本班均值按有记录学生等权计算。未提供年级均值。</p>
        </div>}
        <ul className="student-profile-list">
          {profile.history.map((quiz) => {
            const target = quiz.records.find((r) => r.included) || quiz.records[0];
            return <li key={quiz.id} className="student-profile-history-row" data-history-quiz-id={quiz.id}>
              <div><strong>{quiz.name}</strong><div className="teacher-muted">{quiz.date || '日期未提供'}{quiz.id === quizId ? ' · 当前测验' : ''}</div></div>
              <div>
                <strong>{scoreText(quiz.totals)}</strong>
                <div>有效题量 {quiz.totals.effectiveQuestionCount} 题 · 已评分 {quiz.totals.scoredCount} 条 / 实际记录 {quiz.totals.recordCount} 条（{quiz.totals.recordedQuestionCount} 题）</div>
                <StatusCounts totals={quiz.totals} />
                {!quiz.totals.recordCount && <p className="teacher-muted">该生在此测验无记录，不视为零分或本次未测。</p>}
              </div>
              <div>
                {target && <Button size="small" onClick={() => open(target)}>查看 {quiz.name} 答卷</Button>}
                {quiz.records.length > 0 && <details><summary>逐题记录与状态</summary><RecordList records={quiz.records} onOpenAnswer={onOpenAnswer} /></details>}
              </div>
            </li>;
          })}
        </ul>
        <p className="student-profile-comparison">
          {profile.history.length < 2 ? '不足两场测验，暂无跨场可比题目。'
            : profile.comparableQuestionIds.length ? `各场共同有效题目：${profile.comparableQuestionIds.join('、')}。这里只列历史，不据此推断进步。`
              : '无可比题目：各场有效已评分题目交集为空，不用不同题群比较进步。'}
          <span className="teacher-muted"> 各场记录题量：{profile.history.map((q) => `${q.name} ${q.totals.recordedQuestionCount} 题（有效 ${q.totals.effectiveQuestionCount} 题）`).join('；')}。</span>
        </p>
      </>}
    </section>

    <section id="student-next-steps" className="teacher-section" aria-label="下一步安排">
      <div className="teacher-section-heading"><h3><FlagOutlined aria-hidden />下一步安排</h3></div>
      <details className="teacher-note-fold"><summary><QuestionCircleOutlined aria-hidden />使用说明</summary>
        <p className="teacher-muted">先看当前记录，再由教师在答卷与建议中调整任务或选择练习；这里不改变原评分。</p>
      </details>
      {!bundle || !profile.quiz || !profile.nextSteps.length ? <p className="student-profile-empty">暂无有依据的本次安排，请先补充正式记录。</p>
        : <ul className="student-profile-list">
          {profile.nextSteps.map((step) => <li key={step.indicatorId}>
            <strong>{step.title}</strong><span className="teacher-muted"> · {step.indicatorId}</span>
            <p>{step.reason}</p>
            <div className="teacher-actions">
              {step.record && <Button size="small" onClick={() => open(step.record!)}>查看答卷与建议 · {step.record.questionId}</Button>}
              {step.textbook && <Button size="small" onClick={() => onOpenTextbook(step.textbook!)}>看教材</Button>}
              {!step.record && <span className="teacher-muted">暂无当前记录答卷入口，需先收集作答。</span>}
              {step.record && <span className="teacher-muted">{step.record.quizId} · 记录 {step.record.recordId || '编号未提供'}</span>}
            </div>
          </li>)}
        </ul>}
    </section>

    <section className="teacher-section" aria-label="练习记录（不计入原测验成绩）" data-testid="student-profile-practice">
      <div className="teacher-section-heading"><h3><EditOutlined aria-hidden />练习记录（不计入原测验成绩）</h3><span className="teacher-muted">{practice.length} 条</span></div>
      <details className="teacher-note-fold"><summary><QuestionCircleOutlined aria-hidden />统计口径说明</summary>
        <p className="teacher-muted">当前浏览器保存的本人练习，包含不同测验与考点。仅核对当次输入，不替代正式记录，也不改变上方成绩或覆盖判断。</p>
      </details>
      {!practice.length ? <p className="student-profile-empty">暂无已保存的练习记录。完成练习并保存后，返回档案即可查看。</p>
        : <ul className="student-profile-list">
          {practice.map((entry, index) => {
            const officialRecords = profile.history.find((q) => q.id === entry.nodeId)?.records.filter((r) => r.questionId === entry.questionId) || [];
            const record = officialRecords.find((r) => r.included) || officialRecords[0];
            const label = PRACTICE_LABELS[entry.kind] || '判定未提供';
            return <li key={`${entry.nodeId}|${entry.indicatorId}|${entry.id || `${entry.at}-${index}`}`} data-practice-question-id={entry.questionId}>
              <div className="student-profile-practice-heading"><strong>{entry.questionId} · {label}</strong><time dateTime={entry.at}>{practiceTime(entry.at)}</time></div>
              <p className="teacher-muted">练习所在测验 {entry.nodeId} · {entry.indicatorId}</p>
              <dl className="student-profile-practice-inputs">
                <div><dt>去括号后</dt><dd>{entry.intermediate || '未填写'}</dd></div>
                <div><dt>合并后过程末行</dt><dd>{entry.combined || '未记录，不能声称核验此阶段'}</dd></div>
                <div><dt>最终填答</dt><dd>{entry.final || '未填写'}</dd></div>
              </dl>
              <p><span className="teacher-muted">后续建议：</span>{entry.followUp || '未保存后续建议'}</p>
              <details><summary>查看已有完整结果</summary>
                <p>判定：{label}（{entry.kind}）</p><p>{entry.supports || '未保存判定依据'}</p><p>{entry.followUp || '未保存后续建议'}</p>
                <p className="teacher-muted">学生 {entry.studentId} · 测验 {entry.nodeId} · 考点 {entry.indicatorId} · 实际练习题 {entry.questionId}<br />
                  练习记录编号：{entry.id || '旧记录未提供编号'} · 保存时间：{entry.at}。以上为已保存结果，不重新判分。</p>
              </details>
              <div className="teacher-actions">
                <Button size="small" onClick={() => onOpenAnswer(entry.questionId, entry.nodeId, record?.recordId)}>回看练习题 {entry.questionId}</Button>
                <span className="teacher-muted">{record ? `原测验记录：${record.status}；与本次练习分开显示。` : '原测验本题无记录；回看仍显示无记录，不生成官方答卷。'}</span>
              </div>
            </li>;
          })}
        </ul>}
    </section>
    {bundle && packIndicator && <StudyPack bundle={bundle} indicatorId={packIndicator} quizId={quizId}
      classId={profile.student?.classId || ''} studentId={studentId} focusQuestionId={focusQuestionId}
      open={!!packIndicator} onClose={() => setPackIndicator(null)} onStartPractice={onStartPractice} />}
  </div>;
};

export default StudentProfile;
