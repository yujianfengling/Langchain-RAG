import { quizLabel } from '../shared/ids';
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Alert, Button, Empty, Input, Select, Space, Tooltip, Typography } from 'antd';
import {
  ArrowLeftOutlined, BarChartOutlined, CheckSquareOutlined, DiffOutlined, EditOutlined, FallOutlined, FileTextOutlined,
  IdcardOutlined, LeftOutlined, PercentageOutlined, QuestionCircleOutlined, RightOutlined, RiseOutlined,
  RobotOutlined, TeamOutlined, WarningOutlined,
} from '@ant-design/icons';
import { getToken } from '../../api/authState';
import { summarize, percent } from '../shared/examReport';
import { searchStudents, getStudentName, CLASSES, QUIZZES } from '../shared/ids';
import { analysisReturnPath, questionLabel } from '../shared/teacherContext';
import { getAnswer, getQuestion, listAllQuestions, listStudentAnswers, listStudentEvents } from './data';
import LearningAdvice from '../learningAdvice/LearningAdvice';
import StudentProfile from './StudentProfile';
import TeachingSourceLink from '../curriculum/TeachingSourceLink';
import TextbookDrawer from '../shared/TextbookDrawer';
import StepDiffDrawer from './StepDiffDrawer';
import { getBundle, isRealData } from '../dataSource';
import type { AnswerEvent, RealAnswer } from '../dataSource';
import type { TextbookSource } from '../shared/types';

const { Title, Text, Paragraph } = Typography;

/** 状态优先于分值；未评分、隔离及超范围记录绝不显示为零分。 */
function answerStatus(event?: AnswerEvent, answer?: RealAnswer, outside = false): string {
  if (outside || event?.outOfScope) return '超出测验范围';
  if (event?.quarantined || (answer && (answer.score < 0 || answer.score > answer.fullScore))) return '隔离待核';
  if (event && event.status !== '有效得分' && event.status !== '已评分') return event.status || '状态待核';
  if (answer) return `${answer.score}/${answer.fullScore} 分`;
  return event ? '已评分·明细未提供' : '无记录';
}

function firstStudentAnswer(studentId: string, quizId: string) {
  const answers = listStudentAnswers(studentId, quizId) as RealAnswer[];
  const events = listStudentEvents(studentId, quizId);
  const allowed = getBundle()?.quizzes.find((q) => q.id === quizId)?.availableItems;
  const answer = answers.find((a) => {
    const event = events.find((e) => e.questionId === a.questionId && (!a.sourceRecordId || e.recordId === a.sourceRecordId));
    return (!allowed?.length || allowed.includes(a.questionId)) && !event?.quarantined && !event?.outOfScope
      && (!event || event.status === '有效得分' || event.status === '已评分') && a.score >= 0 && a.score <= a.fullScore;
  });
  return answer ? { questionId: answer.questionId, recordId: answer.sourceRecordId || null }
    : { questionId: events[0]?.questionId || '', recordId: events[0]?.recordId || null };
}

const StudentAnswersPage: React.FC = () => {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const real = isRealData();
  const bundle = getBundle();
  const studentId = params.get('studentId') || '';
  const questionId = params.get('questionId') || '';
  const quizId = params.get('quizId') || QUIZZES[0]?.id || '';
  const recordId = params.get('recordId') || undefined;
  const activeTab = params.get('tab') === 'profile' ? 'profile' : 'answers';
  const [keyword, setKeyword] = useState('');
  const [textbook, setTextbook] = useState<TextbookSource | null>(null);
  const [diffOpen, setDiffOpen] = useState(false);
  const [aiAnalysis, setAiAnalysis] = useState('');
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState('');
  const students = useMemo(() => searchStudents(keyword), [keyword]);
  const roster = searchStudents('');
  const selectedQuiz = QUIZZES.find(q => q.id === quizId);
  const quizName = selectedQuiz ? quizLabel(selectedQuiz) : quizId;
  const question = questionId ? getQuestion(questionId) : undefined;
  const available = bundle?.quizzes.find((q) => q.id === quizId)?.availableItems;
  const studentAnswers = listStudentAnswers(studentId, quizId) as RealAnswer[];
  const studentEvents = listStudentEvents(studentId, quizId);

  // 克隆全部查询参数，保留 returnTo、reviewBudget、reviewStrategy 等分析上下文。
  const patchParams = (patch: Record<string, string | null>, state?: { practiceHint: string }) => {
    const next = new URLSearchParams(params);
    Object.entries(patch).forEach(([key, value]) => {
      if (value == null || value === '') next.delete(key); else next.set(key, value);
    });
    setParams(next, { replace: true, state });
  };

  const selectStudent = (sid: string) => {
    const first = firstStudentAnswer(sid, quizId);
    patchParams({ studentId: sid, quizId, classId: roster.find((s) => s.id === sid)?.classId || null,
      tab: 'profile', practiceRecord: null, followup: null, followupPractice: null, questionId: first.questionId, recordId: first.recordId, kpId: getQuestion(first.questionId)?.kpId || null });
    setKeyword('');
    setTextbook(null);
  };

  // 直接带学生进入时，也定位本测验第一份真实答卷；不从别的测验补记录。
  useEffect(() => {
    if (!studentId || questionId || recordId) return;
    const first = firstStudentAnswer(studentId, quizId);
    if (!first.questionId) return;
    const next = new URLSearchParams(params);
    next.set('quizId', quizId);
    next.set('questionId', first.questionId);
    if (first.recordId) next.set('recordId', first.recordId); else next.delete('recordId');
    const kp = getQuestion(first.questionId)?.kpId;
    if (kp) next.set('kpId', kp); else next.delete('kpId');
    setParams(next, { replace: true });
  }, [studentId, questionId, quizId, recordId, params, setParams]);

  const questionEvents = studentEvents.filter((e) => e.questionId === questionId);
  const event = questionEvents.find((e) => !recordId || e.recordId === recordId);
  const rawAnswer = studentId && questionId ? getAnswer(studentId, questionId, quizId, recordId) as RealAnswer | undefined : undefined;
  const outside = !!(available?.length && questionId && !available.includes(questionId));
  const status = answerStatus(event, rawAnswer, outside);
  const answer = rawAnswer && !outside && !event?.quarantined && !event?.outOfScope
    && (!event || event.status === '有效得分' || event.status === '已评分')
    && rawAnswer.score >= 0 && rawAnswer.score <= rawAnswer.fullScore ? rawAnswer : undefined;
  const missingRecord = !!recordId && !event && !rawAnswer;
  const otherQuizEvents = studentId && questionId
    ? listStudentEvents(studentId).filter((e) => e.questionId === questionId && e.quizId !== quizId) : [];
  const otherStudentQuizzes = QUIZZES.filter((q) => q.id !== quizId
    && (listStudentEvents(studentId, q.id).length || listStudentAnswers(studentId, q.id).length));

  const questionIds = Array.from(new Set([
    ...(!real ? listAllQuestions().filter((q) => q.fullScore > 0).map((q) => q.id) : []),
    ...studentEvents.map((e) => e.questionId), ...studentAnswers.map((a) => a.questionId),
    ...(questionId ? [questionId] : []),
  ])).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const questionIndex = questionIds.indexOf(questionId);
  const selectQuestion = (id: string) => {
    const a = studentAnswers.find((x) => x.questionId === id);
    const e = studentEvents.find((x) => x.questionId === id);
    patchParams({ questionId: id, recordId: a?.sourceRecordId || e?.recordId || null, kpId: getQuestion(id)?.kpId || null, followupPractice: null });
    setTextbook(null);
  };
  const selectQuiz = (id: string) => {
    const first = firstStudentAnswer(studentId, id);
    const qid = questionId || first.questionId;
    const e = listStudentEvents(studentId, id).find((x) => x.questionId === qid);
    const a = listStudentAnswers(studentId, id).find((x) => x.questionId === qid) as RealAnswer | undefined;
    patchParams({ quizId: id, questionId: qid || null, recordId: a?.sourceRecordId || e?.recordId || null,
      kpId: getQuestion(qid)?.kpId || null, followup: null, followupPractice: null });
    setTextbook(null);
  };
  const navStatus = (id: string) => {
    const e = studentEvents.find((x) => x.questionId === id);
    const a = studentAnswers.find((x) => x.questionId === id && (!e || !x.sourceRecordId || x.sourceRecordId === e.recordId));
    return answerStatus(e, a, !!(available?.length && !available.includes(id)));
  };

  const finding = answer && real ? bundle?.diagnoses?.diagnoses.find((d) =>
    d.student_id === studentId && d.node_id === quizId && d.indicator_id === question?.kpId
  )?.question_findings.find((f) => f.item_id === questionId && f.kind === '过程与填答不一致'
    && f.record_id === answer.sourceRecordId) : undefined;
  const steps = answer?.steps || [];
  const lastStep = steps[steps.length - 1];
  const normalize = (value: string) => value.replace(/\s/g, '').replace(/[−–]/g, '-');
  const moveLastStep = !!(finding?.last_expr && lastStep && normalize(lastStep.text).includes(normalize(finding.last_expr)));
  const visibleSteps = moveLastStep ? steps.slice(0, -1) : steps;
  const className = CLASSES.find((c) => c.id === roster.find((s) => s.id === studentId)?.classId)?.name;
  const overviewClassId = CLASSES.some(c => c.id === params.get('classId')) ? params.get('classId')! : CLASSES[0]?.id || '';
  const classStudents = roster.filter(s => s.classId === overviewClassId).map(student => ({ student,
    ...summarize(studentAnswersFor(student.id)) }));
  const classTopicSummary = bundle ? [...new Set(bundle.answers.filter(a => a.quizId === quizId
    && classStudents.some(s => s.student.id === a.studentId)).flatMap(a => bundle.questions.find(q => q.id === a.questionId)?.kpId || []))].map(indicator => {
      const ids = new Set(bundle.questions.filter(q => q.kpId === indicator).map(q => q.id));
      const rows = bundle.answers.filter(a => a.quizId === quizId && ids.has(a.questionId)
        && classStudents.some(s => s.student.id === a.studentId));
      const name = bundle.curriculumMap?.indicators.find(i => i.indicator_id === indicator)?.requirement || indicator;
      return { indicator, name, ...summarize(rows) };
    }).filter(row => row.full > 0).sort((a,b) => (a.rate ?? 100) - (b.rate ?? 100)) : [];
  function studentAnswersFor(id: string) { return bundle?.answers.filter(a => a.studentId === id && a.quizId === quizId) || []; }
  const classFull = classStudents.reduce((sum, s) => sum + s.full, 0);
  const classMean = classFull > 0 ? classStudents.reduce((sum, s) => sum + s.score, 0) / classFull * 100 : null;
  const bandCounts = [0,0,0,0];
  classStudents.forEach(s => { if (s.rate != null) bandCounts[Math.min(3,Math.floor(s.rate / 25))] += 1; });
  // 分组呈现学生：需关注置顶（警示色），正常与无记录弱化，避免 21 个等权 chips 无焦点。
  const sortedClassStudents = [...classStudents].sort((a,b)=>(a.rate??-1)-(b.rate??-1));
  const lowestStudent = classStudents.filter(s=>s.rate!=null).sort((a,b)=>a.rate!-b.rate!)[0];
  const studentGroups = [
    { key: 'attention', label: '需关注（得分率低于 60%）', rows: sortedClassStudents.filter(s=>s.rate!=null && s.rate<60) },
    { key: 'normal', label: '表现正常（得分率 60% 及以上）', rows: sortedClassStudents.filter(s=>s.rate!=null && s.rate>=60) },
    { key: 'none', label: '无有效记录', rows: sortedClassStudents.filter(s=>s.rate==null) },
  ].filter(group=>group.rows.length>0);
  const advice = question?.kpId ? <LearningAdvice key={`${studentId}|${quizId}|${questionId}|${recordId || ''}`}
    kpId={question.kpId} studentId={studentId} questionId={questionId} nodeId={quizId}
    recordId={recordId || answer?.sourceRecordId || event?.recordId} onOpenTextbook={setTextbook} /> : null;
  const analyzeAnswer = async () => {
    if (!answer || !question) return;
    setAiBusy(true); setAiError('');
    try {
      const token = getToken();
      const response = await fetch('/api/competition/analyze-answer', { method: 'POST', headers: {
        'Content-Type': 'application/json', ...(token ? { 'X-User-Token': token } : {}),
      }, body: JSON.stringify({ stem: question.stem, answer: answer.finalAnswer || '', steps: answer.steps.map(s => s.text),
        score: answer.score, full_score: answer.fullScore,
        rubric: answer.scoringPoints.map(s => ({ point: s.label, earned: s.earnedScore, full: s.fullScore, evidence: s.evidence })) }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `分析失败（${response.status}）`);
      setAiAnalysis(payload.analysis);
    } catch (error) { setAiError(error instanceof Error ? error.message : '分析失败，请重试。'); }
    finally { setAiBusy(false); }
  };
  const openProfileAnswer = (id: string, node: string, record?: string) => {
    // 保留 practiceRecord / practiceOriginal*：切到别的题后回档案时，练习提示与
    // 「查看原答卷」要仍指向这次练习的原题，而不是被清掉或直接跳到新题。
    patchParams({ tab: 'answers', questionId: id, quizId: node, recordId: record || null, kpId: getQuestion(id)?.kpId || null });
    document.querySelector('.comp-main')?.scrollTo({ top: 0 });
  };

  return (
    <div className="competition-page teacher-answers-page">
      <header className="teacher-page-header">
        <Title level={2} className="competition-title">{activeTab === 'profile' && studentId ? <><IdcardOutlined aria-hidden /> 学生个人档案</> : <><FileTextOutlined aria-hidden /> 学生答卷</>}</Title>
        {studentId && <span className="teacher-current-context">{className && (/^DC\d+$/.test(className) ? '班级 ' + className : className)} · 学生 {getStudentName(studentId)} · {quizName}</span>}
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(analysisReturnPath(params))}>返回考试分析</Button>
      </header>

      <section className="teacher-section" aria-label="查找学生答卷" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, minWidth: 0 }}>
          <Select aria-label="选择测验" value={quizId || undefined} style={{ width: 210, maxWidth: '100%' }}
            options={QUIZZES.map((q) => ({ value: q.id, label: quizLabel(q) }))} onChange={selectQuiz} />
          {!studentId && <Select aria-label="学情班级" value={overviewClassId} style={{ width: 140, maxWidth: '100%' }}
            options={CLASSES.map(c => ({ value:c.id,label:c.name===c.id?`班级 ${c.id}`:c.name }))}
            onChange={id => patchParams({ classId:id })} />}
          <Input.Search aria-label="搜索学生姓名或编号" placeholder="搜索学生姓名或编号" allowClear
            value={keyword} onChange={(e) => setKeyword(e.target.value)} style={{ width: 230, maxWidth: '100%' }} />
          <Select aria-label="选择学生" placeholder="选择学生" value={studentId || undefined}
            style={{ width: 220, maxWidth: '100%' }} onChange={selectStudent}
            options={roster.map((s) => ({ value: s.id, label: `${s.name} · ${CLASSES.find((c) => c.id === s.classId)?.name || s.classId}` }))} />
          <Text className="teacher-muted" style={{ overflowWrap: 'anywhere' }}>
            {studentId ? `${className ? `${className} / ` : ''}${getStudentName(studentId)}${questionId ? ` / ${questionLabel(questionId)}` : ''}` : '选择学生后打开本次测验第一份答卷'}
          </Text>
        </div>
        {keyword.trim() && (
          <div role="region" aria-label="学生搜索结果" aria-live="polite" style={{ marginTop: 12 }}>
            {students.length ? <>
              <Text className="teacher-muted">找到 {students.length} 名学生</Text>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 6 }}>
                {students.map((s) => <Button key={s.id} onClick={() => selectStudent(s.id)} style={{ height: 'auto', whiteSpace: 'normal', textAlign: 'left' }}>
                  {s.name}{s.name !== s.id ? ` · ${s.id}` : ''} · {CLASSES.find((c) => c.id === s.classId)?.name || s.classId}
                </Button>)}
              </div>
            </> : <Text>未找到匹配的学生，请尝试姓名或学生编号。</Text>}
          </div>
        )}
      </section>

      {studentId && <div role="tablist" aria-label="学生资料" className="teacher-student-tabs">
        <button type="button" id="student-profile-tab" role="tab" aria-selected={activeTab === 'profile'} aria-controls="student-profile-panel"
          onClick={() => { patchParams({ tab: 'profile', pretest: null }); setTextbook(null); }}>个人档案</button>
        <button type="button" id="student-answers-tab" role="tab" aria-selected={activeTab === 'answers'} aria-controls="student-answers-panel"
          onClick={() => { patchParams({ tab: 'answers' }); setTextbook(null); }}>答卷</button>
      </div>}
      {!studentId ? <section className="teacher-section teacher-learning-overview" aria-label="班级学情总览">
        <div className="teacher-section-heading"><Title level={3} style={{margin:0}}><TeamOutlined aria-hidden /> 班级学情总览</Title><Text type="secondary">{CLASSES.find(c=>c.id===overviewClassId)?.name===overviewClassId?`班级 ${overviewClassId}`:CLASSES.find(c=>c.id===overviewClassId)?.name} · {quizName}</Text></div>
        {lowestStudent && <div className="teacher-learning-guide" role="note">
          <span><WarningOutlined aria-hidden /> 建议先看 <strong>{lowestStudent.student.name}</strong>（{percent(lowestStudent.rate)}），本班最低。</span>
          <Button type="primary" size="small" onClick={()=>selectStudent(lowestStudent.student.id)}>查看 {lowestStudent.student.name} 档案</Button>
        </div>}
        <div className="teacher-learning-kpis"><article><span><PercentageOutlined aria-hidden />班级平均得分率</span><strong>{percent(classMean)}</strong><small>{classStudents.filter(s=>s.rate!=null).length}/{classStudents.length} 人有有效答卷</small></article>
          <article><span><RiseOutlined aria-hidden />本班最高</span><strong>{classStudents.filter(s=>s.rate!=null).sort((a,b)=>b.rate!-a.rate!)[0]?.student.name || '—'}</strong><small>{percent(classStudents.filter(s=>s.rate!=null).sort((a,b)=>b.rate!-a.rate!)[0]?.rate ?? null)}</small></article>
          <article><span><FallOutlined aria-hidden />本班最低</span><strong>{classStudents.filter(s=>s.rate!=null).sort((a,b)=>a.rate!-b.rate!)[0]?.student.name || '—'}</strong><small>{percent(classStudents.filter(s=>s.rate!=null).sort((a,b)=>a.rate!-b.rate!)[0]?.rate ?? null)}</small></article></div>
        <section className="teacher-learning-block"><h3><BarChartOutlined aria-hidden />得分率分布</h3><div className="teacher-learning-bands">{['0–24%','25–49%','50–74%','75–100%'].map((label,i)=><div key={label}><span>{label}</span><strong>{bandCounts[i]} 人</strong><i><b style={{width:`${classStudents.length?bandCounts[i]/classStudents.length*100:0}%`}}/></i></div>)}</div></section>
        <section className="teacher-learning-block"><h3><WarningOutlined aria-hidden />需要先看的考点</h3>{classTopicSummary.slice(0,3).map(row=><Button type="text" className="teacher-learning-topic" key={row.indicator}
          onClick={()=>navigate(`/class-analytics?quizId=${encodeURIComponent(quizId)}&classId=${encodeURIComponent(overviewClassId)}&kpId=${encodeURIComponent(row.indicator)}`)}>
          <span>{row.name}</span><strong>{percent(row.rate)}</strong><small>{row.count} 条有效作答</small>
        </Button>)}</section>
        <section className="teacher-learning-block"><h3><TeamOutlined aria-hidden />本班学生</h3>{studentGroups.map(group=><div key={group.key} className={`teacher-learning-group teacher-learning-group-${group.key}`}>
          <h4 className="teacher-learning-group-title">{group.label}<span>{group.rows.length} 人</span></h4>
          <div className="teacher-learning-students">{group.rows.map(({student,rate,count})=><Button key={student.id} onClick={()=>selectStudent(student.id)}>{student.name} · {percent(rate)} · {count} 题</Button>)}</div>
        </div>)}</section>
        <details className="teacher-note-fold"><summary><QuestionCircleOutlined aria-hidden />统计口径说明</summary>
          <p className="teacher-muted">均值按有记录学生等权计算。得分率只覆盖本次有效已评分作答，不等同于整卷成绩；缺少作答不计为零分。选择学生可查看个人档案、逐题证据和答卷分析。</p>
        </details>
      </section> : activeTab === 'profile' ? (
        <div role="tabpanel" id="student-profile-panel" aria-labelledby="student-profile-tab">
          <StudentProfile key={`${studentId}|${quizId}`} studentId={studentId} quizId={quizId} focusQuestionId={questionId}
            onStartPractice={(original, practiceQuestion, hint) => patchParams({
              tab: 'answers', questionId: original.questionId, quizId: original.quizId, recordId: original.recordId || null,
              kpId: getQuestion(original.questionId)?.kpId || null, pretest: '1', followupPractice: practiceQuestion,
            }, hint ? { practiceHint: hint } : undefined)}
            onOpenAnswer={openProfileAnswer} onOpenTextbook={setTextbook} />
        </div>
      ) : (
        <div className="teacher-answer-layout" role="tabpanel" id="student-answers-panel" aria-labelledby="student-answers-tab">
          <nav className="teacher-question-nav" aria-label="答卷题号">
            <div className="teacher-section-heading">题目</div>
            {questionIds.map((id) => <Button key={id} block type={id === questionId ? 'primary' : 'text'}
              aria-current={id === questionId ? 'page' : undefined} onClick={() => selectQuestion(id)}
              style={{ height: 'auto', textAlign: 'left', whiteSpace: 'normal', marginBottom: 4, padding: '8px 10px' }}>
              <div>{questionLabel(id)}</div><small>{navStatus(id)}</small>
            </Button>)}
          </nav>
          <main className="teacher-answer-body" style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
            <Select className="teacher-question-select" aria-label="选择题号" value={questionId || undefined}
              placeholder="选择题目" style={{ width: '100%', marginBottom: 12 }} onChange={selectQuestion}
              options={questionIds.map((id) => ({ value: id, label: `${questionLabel(id)} · ${navStatus(id)}` }))} />
            {!questionId ? <section className="teacher-section">
              <Empty description="该学生在所选测验没有作答记录，不以其他测验记录替代。" />
              {otherStudentQuizzes.length > 0 && <Space wrap>
                <Text>查看其他测验：</Text>{otherStudentQuizzes.map((q) => <Button key={q.id} onClick={() => selectQuiz(q.id)}>{q.name}</Button>)}
              </Space>}
            </section> : (
              <article className="teacher-section">
                <div className="teacher-actions" style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
                  <Text strong>{questionLabel(questionId)}</Text>
                  <Space size={8}>
                    <Button size="small" icon={<LeftOutlined />} disabled={questionIndex <= 0} onClick={() => selectQuestion(questionIds[questionIndex - 1])}>上一题</Button>
                    <Button size="small" disabled={questionIndex < 0 || questionIndex >= questionIds.length - 1} onClick={() => selectQuestion(questionIds[questionIndex + 1])}>下一题<RightOutlined /></Button>
                  </Space>
                </div>
                <section aria-label="原题与得分">
                  <Paragraph strong style={{ fontSize: 17, whiteSpace: 'pre-wrap' }}>{question?.stem || '题干未提供'}</Paragraph>
                  <Paragraph><Text strong>{answer ? `得分 ${answer.score} / ${answer.fullScore} 分` : missingRecord ? '指定记录未找到' : status}</Text></Paragraph>
                  {question && <TeachingSourceLink questionId={questionId} quizId={quizId}
                    classId={roster.find((s) => s.id === studentId)?.classId} studentId={studentId} answerId={answer?.id} />}
                  {answer && real && <span style={{ marginInlineStart: 16 }}><TeachingSourceLink questionId={questionId} quizId={quizId}
                    classId={roster.find((s) => s.id === studentId)?.classId} studentId={studentId} answerId={answer.id} view="lecture" /></span>}
                </section>

                {answer ? <div className={steps.length ? 'teacher-evidence-grid' : 'teacher-short-answer'}>
                  <div>

                  <section style={{ marginTop: 20 }} aria-label="学生步骤和最终答案">
                    <h3 className="teacher-section-heading"><EditOutlined aria-hidden /> 学生作答
                      {question?.questionType === '解答题' && (
                        <Button size="small" icon={<DiffOutlined aria-hidden />} style={{ marginLeft: 10 }} onClick={() => setDiffOpen(true)}>步骤 diff</Button>
                      )}
                    </h3>
                    {answer.stepsUnavailable || !steps.length ? <Paragraph className="teacher-muted">未记录解题步骤</Paragraph> : visibleSteps.length > 0 ? (
                      <ol className="competition-steps" style={{ paddingLeft: 0, listStyle: 'none' }}>
                        {visibleSteps.map((step) => <li key={step.step} style={{ whiteSpace: 'pre-wrap' }}>{step.text}</li>)}
                      </ol>
                    ) : null}
                    {finding ? <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px 32px', padding: '12px 0' }}>
                      <div><Text className="teacher-muted">过程末行</Text><div><Text strong>{moveLastStep ? lastStep.text : finding.last_expr || '未提供'}</Text></div></div>
                      <div><Text className="teacher-muted">最终填答</Text><div><Text strong>{answer.finalAnswer || finding.final_answer || '未提供'}</Text></div></div>
                    </div> : <Paragraph><Text className="teacher-muted">最终答案：</Text>{answer.finalAnswer || '未单独记录最终填答'}</Paragraph>}
                    <div className="teacher-actions teacher-ai-analysis">
                      <Button icon={<RobotOutlined aria-hidden />} onClick={() => void analyzeAnswer()} loading={aiBusy}>AI 分析这份答卷</Button>
                      <Tooltip title="依据本题原答卷与评分点生成，结论供教师核对"><QuestionCircleOutlined aria-hidden className="teacher-note-hint" /></Tooltip>
                    </div>
                    {aiError && <Alert type="warning" showIcon message={aiError} />}
                    {aiAnalysis && <Alert className="teacher-ai-result" type="info" showIcon message="AI 答卷分析" description={<div style={{ whiteSpace: 'pre-wrap' }}>{aiAnalysis}</div>} />}
                  </section>
                  <section style={{ marginTop: 20 }} aria-label="评分点">
                    <h3 className="teacher-section-heading"><CheckSquareOutlined aria-hidden /> 评分点</h3>
                    {answer.rubricUnavailable || !answer.scoringPoints.length ? <Paragraph className="teacher-muted">未提供评分点</Paragraph> : (
                      <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                        {answer.scoringPoints.map((sp) => <li key={sp.id} style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 18px', padding: '6px 0' }}>
                          <span>评分标准 · {sp.label}</span><span style={{ whiteSpace: 'nowrap' }}>学生得分 {sp.earnedScore} / {sp.fullScore} 分</span>
                        </li>)}
                      </ul>
                    )}
                    <details style={{ marginTop: 12 }}>
                      <summary style={{ cursor: 'pointer' }}>评分依据</summary>
                      <div className="teacher-muted" style={{ marginTop: 8, fontSize: 13 }}>
                        <div>测验：{quizName}（{quizId}） · 学生：{getStudentName(studentId)}（{studentId}） · 题目：{questionId}</div>
                        {real && <div>来源记录：{answer.sourceRecordId || '未提供'} · 修订版本：{answer.sourceVersion || '未提供'}</div>}
                        {!!answer.superseded?.length && <div>被覆盖记录：{answer.superseded.map((s) => `${s.record_id}（${s.version}）`).join('、')}</div>}
                        <div>以本学生、本测验、本题的有效记录评分为准；待评分、未作答、隔离、超范围及无记录均不按 0 分处理。教师调整不改变原评分。</div>
                        {answer.scoringPoints.map((sp) => <div key={sp.id}>原始评分证据：{sp.evidence}</div>)}
                      </div>
                    </details>
                  </section>
                  </div>
                  <aside className="teacher-evidence-advice" aria-label="下一步安排">{advice}</aside>
                </div> : <section style={{ marginTop: 12 }}>
                  <Alert type="info" showIcon message={missingRecord ? '所指定的记录不属于当前学生、测验与题目，或已被修订覆盖。' :
                    status === '无记录' ? '本测验没有该学生的本题记录。' : `${status}：保留原状态，不当作 0 分。`}
                    description="不会用其他学生、测验或记录的答卷补齐。" />
                  {missingRecord && (questionEvents.length > 0 || studentAnswers.some((a) => a.questionId === questionId)) && (
                    <Button style={{ marginTop: 8 }} onClick={() => selectQuestion(questionId)}>查看本题当前记录</Button>
                  )}
                  <details style={{ marginTop: 12 }}><summary style={{ cursor: 'pointer' }}>评分依据</summary>
                    <div className="teacher-muted" style={{ marginTop: 8 }}>学生：{studentId} · 测验：{quizId} · 题目：{questionId}<br />
                      来源记录：{event?.recordId || recordId || '无'} · 修订版本：{event?.version || '无'}<br />
                      {event ? `原始状态：${event.status}；${event.outOfScope || outside ? '超范围，不计入统计。' : event.quarantined ? '隔离待核，不计入得分。' : '只计入有效已评分记录。'}` : '未找到当前范围的匹配记录。'}
                    </div>
                  </details>
                </section>}

                {questionEvents.length > 1 && <Select aria-label="选择作答记录" style={{ width: '100%', marginTop: 12 }}
                  value={event?.recordId} options={questionEvents.map((e, i) => ({ value: e.recordId, label: `第 ${i + 1} 次记录 · ${answerStatus(e, studentAnswers.find((a) => a.sourceRecordId === e.recordId))}` }))}
                  onChange={(id) => patchParams({ recordId: id })} />}
                {!answer && <div style={{ marginTop: 20 }}>{advice}</div>}
                {otherQuizEvents.length > 0 && <div style={{ marginTop: 20 }}>
                  <Text className="teacher-muted">本题在其他测验的记录：</Text>
                  <Space wrap>{otherQuizEvents.map((e) => <Button key={`${e.quizId}|${e.recordId}`} type="link" style={{ height: 'auto', whiteSpace: 'normal', textAlign: 'left' }}
                    onClick={() => { patchParams({ quizId: e.quizId, recordId: e.recordId }); setTextbook(null); }}>
                    {QUIZZES.find((q) => q.id === e.quizId)?.name || e.quizId} · {answerStatus(e, getAnswer(studentId, questionId, e.quizId, e.recordId) as RealAnswer | undefined)}
                  </Button>)}</Space>
                </div>}
              </article>
            )}
          </main>
        </div>
      )}
      <TextbookDrawer open={!!textbook} source={textbook} onClose={() => setTextbook(null)} />
      <StepDiffDrawer open={diffOpen} onClose={() => setDiffOpen(false)} questionId={questionId} answer={answer || null} onOpenTextbook={setTextbook} />
    </div>
  );
};

export default StudentAnswersPage;
