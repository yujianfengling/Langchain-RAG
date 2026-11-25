import { printedPageLabel, openPageLabel } from '../shared/textbookPage';
import React, { useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Card, Col, Descriptions, Empty, Input, List, Menu, Modal, Row, Space, Tag, Typography,
} from 'antd';
import { BookOutlined, FileTextOutlined, LinkOutlined, NodeIndexOutlined } from '@ant-design/icons';
import { TOPICS, CLASSES, QUIZZES, quizLabel } from '../shared/ids';
import { questionLabel } from '../shared/teacherContext';
import { listKnowledgePoints, getKpStat } from '../classAnalytics/data';
import { listQuestionsByKp } from '../studentAnswers/data';
import { getCurriculum, getSourceByQuestion } from './data';
import TextbookDrawer from '../shared/TextbookDrawer';
import { getBundle, isPrivateCompetitionData } from '../dataSource';
import type { KnowledgePoint, TextbookSource } from '../shared/types';
import type { CurriculumIndicator } from '../dataSource';
import ReviewDraft from './ReviewDraft';
import { teachingConnections } from './learningSequence';
import './curriculum.css';

const { Title, Text, Paragraph } = Typography;

/** 官方切片具体页 → 教材抽屉所需来源对象（三个页码概念分开存展）。 */
function sourceFromPage(cur: CurriculumIndicator, idx: number): TextbookSource {
  const p = cur.textbook_pages[idx];
  return {
    docId: p.material_id,
    docName: `赛方教材切片 ${p.material_id}`,
    page: p.pdf_page,
    exampleTitle: p.anchor,
    pdfUrl: `/api/competition/official-pdf/${p.material_id}`,
    printedPage: p.printed,
    verified: p.verified,
    standard: cur.standard.code ? {
      code: cur.standard.code,
      pdfPage: cur.standard.pdf_page ?? null,
      printed: cur.standard.printed ?? null,
    } : undefined,
  };
}

const kpMatches = (kp: KnowledgePoint, indId: string) => kp.id === indId || kp.id.endsWith('-' + indId)
  || (kp as KnowledgePoint & { indicatorId?: string }).indicatorId === indId;

/** 官方 bundle 的 questions / learning_cards 实际还带 material_type、full_score、source、rubric 字段。 */
type RelatedMaterial = CurriculumIndicator['questions'][number] & {
  material_type?: string; full_score?: string; source?: string; rubric?: string;
};

const isLearningCard = (m: RelatedMaterial) => (m.material_type ? m.material_type === '学习卡' : !m.question_type);

/** 来源：赛事新编 或 切片文件名（完整路径放 title，便于核对）。 */
function materialSource(m: RelatedMaterial, bundleSource?: string): { label: string; full: string } {
  const raw = bundleSource || m.source || '';
  if (!raw) return { label: '出处待定位', full: '' };
  return { label: raw.includes('/') ? raw.slice(raw.lastIndexOf('/') + 1) : raw, full: raw };
}

const OfficialView: React.FC<{ params: URLSearchParams }> = ({ params }) => {
  const bundle = getBundle();
  const focusQuestionId = params.get('questionId');
  const focusedQuestion = bundle?.questions.find((q) => q.id === focusQuestionId);
  const focusLecture = params.get('view') === 'lecture';
  const quiz = bundle?.quizzes.find((q) => q.id === params.get('quizId'));
  const student = bundle?.students.find((s) => s.id === params.get('studentId'));
  const classId = student?.classId || params.get('classId');
  const cls = bundle?.classes.find((c) => c.id === classId);
  const answer = bundle?.answers.find((a) => a.id === params.get('answerId') && a.quizId === quiz?.id
    && a.questionId === focusedQuestion?.id && a.studentId === student?.id);
  const returnPath = params.get('backTo');
  const backTo = returnPath && /^\/(student-answers|class-analytics)(?:\?|$)/.test(returnPath) ? returnPath : undefined;
  const backParams = new URLSearchParams(backTo?.split('?')[1]);
  let followUp: React.ReactNode;
  let answerPath: string | undefined;
  if (answer && focusedQuestion && quiz) {
    const answerParams = new URLSearchParams({ studentId: answer.studentId, quizId: quiz.id, questionId: focusedQuestion.id });
    if (classId) answerParams.set('classId', classId);
    if (answer.sourceRecordId) answerParams.set('recordId', answer.sourceRecordId);
    const source = params.get('source');
    if (source) answerParams.set('source', source);
    const analysis = backTo?.startsWith('/class-analytics') ? backTo : backParams.get('returnTo');
    if (analysis && /^\/class-analytics(?:\?|$)/.test(analysis)) answerParams.set('returnTo', analysis);
    answerPath = '/student-answers?' + answerParams;
    answerParams.set('tab', 'profile');
    answerParams.set('followup', focusedQuestion.kpId);
    followUp = <Link className="teacher-source-link" to={'/student-answers?' + answerParams}>安排课后补测</Link>;
  }
  const backLabel = backTo?.startsWith('/class-analytics') ? '返回考试分析'
    : backParams.get('tab') === 'profile' ? '返回个人档案' : '返回原答卷';
  const back = (backTo || answerPath) && <Link className="teacher-source-link" to={backTo || answerPath!}>{backLabel}</Link>;
  const indicators = useMemo(() => bundle?.curriculumMap?.indicators ?? [], [bundle]);
  const [textbook, setTextbook] = useState<TextbookSource | null>(null);
  const [materialDetail, setMaterialDetail] = useState<RelatedMaterial | null>(null);
  const [search, setSearch] = useState('');
  const [, setParams] = useSearchParams();
  const focusedIndicator = indicators.find((x) =>
    x.indicator_id === focusedQuestion?.kpId);
  const statQuizId = quiz?.id || QUIZZES[0]?.id || '';
  const statClassId = classId || CLASSES[0]?.id || '';
  const defaultIndicatorId = useMemo(() => {
    let best: { id: string; rate: number } | null = null;
    for (const kp of listKnowledgePoints(undefined, statQuizId)) {
      const ind = (kp as KnowledgePoint & { indicatorId?: string }).indicatorId || kp.id.replace(/^.*-(T\d+I\d+)$/, '$1');
      const rate = getKpStat(kp, statClassId)?.scoreRate;
      if (rate != null && (best === null || rate < best.rate)) best = { id: ind, rate };
    }
    return best?.id || indicators[0]?.indicator_id || '';
  }, [statQuizId, statClassId, indicators]);
  const [indicatorId, setIndicatorId] = useState(() => focusedIndicator?.indicator_id || defaultIndicatorId);
  const current = indicators.find((x) => x.indicator_id === indicatorId) || indicators[0];
  const [openThemes, setOpenThemes] = useState<string[]>(() => [current?.theme_id || '']);
  const heading = useRef<HTMLHeadingElement>(null);
  const currentKp = listKnowledgePoints(undefined, statQuizId).find((kp) => kpMatches(kp, current?.indicator_id || ''));
  const currentStat = currentKp ? getKpStat(currentKp, statClassId) : undefined;
  const currentQuestionIds = useMemo(() => (current?.questions || []).map((q) => q.material_id), [current]);
  const classAnswers = useMemo(() => (bundle?.answers || []).filter((a) => a.quizId === statQuizId
    && currentQuestionIds.includes(a.questionId)
    && bundle?.students.some((s) => s.id === a.studentId && s.classId === statClassId)), [bundle, statQuizId, statClassId, currentQuestionIds]);
  const lostStudents = useMemo(() => [...new Set(classAnswers.filter((a) => a.score < a.fullScore).map((a) => a.studentId))], [classAnswers]);
  const questionRates = useMemo(() => currentQuestionIds.map((id) => {
    const rows = classAnswers.filter((a) => a.questionId === id);
    const score = rows.reduce((s, a) => s + a.score, 0); const full = rows.reduce((s, a) => s + a.fullScore, 0);
    return { id, rate: full > 0 ? score / full : null, count: rows.length, lost: new Set(rows.filter((a) => a.score < a.fullScore).map((a) => a.studentId)).size };
  }).sort((a, b) => (a.rate ?? 101) - (b.rate ?? 101)), [currentQuestionIds, classAnswers]);

  const focusedId = focusedIndicator?.indicator_id;
  const focusedTheme = focusedIndicator?.theme_id;
  React.useEffect(() => {
    if (!focusedId || !focusedTheme) return;
    setIndicatorId(focusedId);
    setOpenThemes([focusedTheme]);
    setSearch('');
  }, [focusQuestionId, focusedId, focusedTheme]);

  const keyword = search.trim().toLocaleLowerCase();
  const matching = useMemo(() => indicators.filter((x) =>
    [x.requirement, x.theme_name, ...x.textbook_pages.flatMap((p) => [p.anchor, p.printed]),
      ...x.questions.flatMap((q) => [q.name, q.material_id, questionLabel(q.material_id)])]
      .join(' ').toLocaleLowerCase().includes(keyword)), [indicators, keyword]);
  const themeIds = useMemo(() => Array.from(new Set(matching.map((x) => x.theme_id))), [matching]);
  React.useEffect(() => {
    if (!keyword || matching.length === 0 || matching.some((x) => x.indicator_id === indicatorId)) return;
    const next = matching[0];
    setIndicatorId(next.indicator_id);
    setOpenThemes([next.theme_id]);
  }, [keyword, matching, indicatorId]);

  if (!current) return <Empty description="教材资料尚未就绪" />;

  const selectIndicator = (next: CurriculumIndicator) => {
    setIndicatorId(next.indicator_id);
    setOpenThemes([next.theme_id]);
    setSearch('');
    requestAnimationFrame(() => heading.current?.focus());
  };
  const connections = teachingConnections.flatMap(({ from, to, reason }) => {
    const prerequisite = to === current.indicator_id;
    if (!prerequisite && from !== current.indicator_id) return [];
    const related = indicators.find((item) => item.indicator_id === (prerequisite ? from : to));
    return related ? [{ related, reason, prerequisite }] : [];
  });

  const navRate = (x: CurriculumIndicator) => {
    const kp = listKnowledgePoints(undefined, statQuizId).find((k) => kpMatches(k, x.indicator_id));
    return kp ? getKpStat(kp, statClassId)?.scoreRate ?? null : null;
  };
  const focusQuestion = (id: string) => { const next = new URLSearchParams(params); next.set('questionId', id); setParams(next); };

  return (
    <>
      {focusedQuestion ? <section className="source-context" aria-label="原题与答卷">
        <div className="source-context-top"><span>{[quiz && quizLabel(quiz), cls && (cls.id === cls.name ? '班级 ' + cls.name : cls.name),
          student && '学生 ' + student.name, questionLabel(focusedQuestion.id)].filter(Boolean).join(' · ')}</span>{back}</div>
        <p>{focusedQuestion.stem}</p>
        {answer ? <span className="source-answer">原答卷 · {answer.score}/{answer.fullScore} 分
          {answer.finalAnswer ? ' · 最终填答 ' + answer.finalAnswer : ''}{(answer.stepsUnavailable || !answer.steps?.length) && ' · 未记录解题步骤'}</span>
          : student ? <span className="teacher-muted">当前没有可用于生成讲评的原答卷。</span> : null}
        {!focusedIndicator && <p className="teacher-muted">本题暂无考点出处，可按章节查找。</p>}
      </section> : <div className="source-workbench-head">
        <p className="source-conclusion">
          {currentStat?.scoreRate != null
            ? <>本次{quizLabel(bundle?.quizzes.find((q) => q.id === statQuizId) || QUIZZES[0])}最需溯源：<strong>{current.requirement}</strong>（本班 {Math.round(currentStat.scoreRate * 100)}%、{lostStudents.length} 人失分），教材{current.textbook_pages[0] ? printedPageLabel(current.textbook_pages[0].printed) : '待定位'}，请结合下方课标与教材证据备课。</>
            : <>当前考点：<strong>{current.requirement}</strong>。本班暂无有效得分率，先核对课标与教材出处。</>}
          {current.standard.pdf_page != null && current.standard.pdf_page > 0 && <button type="button" className="source-cite"
            onClick={() => setTextbook({ docId: 'CR', docName: '义务教育数学课程标准（2022年版）', page: current.standard.pdf_page!, pdfUrl: '/api/competition/official-pdf/CR', printedPage: current.standard.printed == null ? undefined : String(current.standard.printed), exampleTitle: '义务教育数学课程标准（2022年版）' })}>课标</button>}
        </p>
        <div className="source-search">
          <Input.Search allowClear placeholder="搜索题目、考点或章节" aria-label="搜索题目、考点或章节"
            value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>
      </div>}
      <Row gutter={[24, 16]} className={`teacher-resource-layout source-layout${focusedQuestion ? ' source-layout-focused' : ''}`}>
        <Col xs={24} md={8} lg={7} className="source-sidebar">
          <nav className="teacher-resource-nav" aria-label="章节与考点">
            <h2 className="teacher-section-heading">考点导航 · 按本班失分排序</h2>
            {matching.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的考点" /> : (
              <div className="source-nav-groups">
                {themeIds.map((tid) => {
                  const items = [...matching.filter((x) => x.theme_id === tid)].sort((a, b) => (navRate(a) ?? 101) - (navRate(b) ?? 101));
                  const open = !!keyword || openThemes.includes(tid) || tid === current.theme_id;
                  return <details key={tid} className="source-nav-group" open={open}
                    onToggle={(e) => { const el = e.target as HTMLDetailsElement; setOpenThemes((prev) => el.open ? [...new Set([...prev, tid])] : prev.filter((t) => t !== tid)); }}>
                    <summary>{indicators.find((x) => x.theme_id === tid)?.theme_name || bundle?.topics.find((t) => t.id === tid)?.name || '教材章节'}</summary>
                    <ul className="source-nav-list">
                      {items.map((x) => {
                        const r = navRate(x);
                        return <li key={x.indicator_id}>
                          <button type="button" className={'source-nav-item' + (x.indicator_id === current.indicator_id ? ' is-active' : '')} onClick={() => selectIndicator(x)}>
                            <span>{x.requirement}</span>{r != null && <em className={r < 0.6 ? 'is-low' : ''}>{Math.round(r * 100)}%</em>}
                          </button>
                        </li>;
                      })}
                    </ul>
                  </details>;
                })}
              </div>
            )}
          </nav>
        </Col>
        <Col xs={24} md={16} lg={17} className="source-main">
          <section className="teacher-section source-primary" aria-label="教材条目">
            <p className="source-eyebrow">{current.theme_name}</p><h2 ref={heading} tabIndex={-1} className="teacher-section-heading">{current.requirement}</h2>
            {focusedQuestion && <p className="teacher-muted">{current === focusedIndicator
              ? '下面是本题考点对应的教材参考。'
              : '正在浏览其他考点。'}{focusedIndicator && current !== focusedIndicator && <Button type="link"
                onClick={() => selectIndicator(focusedIndicator)}>返回本题考点</Button>}</p>}
            <div className="source-evidence">
              <div className="source-ev-col">
                <h3>课标条款</h3>
                <Paragraph>{current.theme_goal || current.requirement}</Paragraph>
                <div className="teacher-muted">{current.standard.printed && <div>义务教育数学课程标准（2022年版） · 正文第 {current.standard.printed} 页</div>}</div>
                {current.standard.pdf_page != null && current.standard.pdf_page > 0 && <Button type="link" onClick={() => setTextbook({
                  docId: 'CR', docName: '义务教育数学课程标准（2022年版）',
                  page: current.standard.pdf_page!, pdfUrl: '/api/competition/official-pdf/CR',
                  printedPage: current.standard.printed == null ? undefined : String(current.standard.printed),
                  exampleTitle: '义务教育数学课程标准（2022年版）',
                })}>查看课标原页</Button>}
              </div>
              <div className="source-ev-col">
                <h3>教材出处</h3>
            {current.textbook_pages.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={`暂无已定位教材页${current.textbook_range ? `，教材范围：${current.textbook_range}` : ''}`} />
            ) : (
              <List dataSource={current.textbook_pages} renderItem={(p, i) => (
                <List.Item key={`${p.material_id}-${p.pdf_page}`} className="source-reference">
                  <Title level={4} style={{ margin: '0 0 8px' }}>{printedPageLabel(p.printed)}</Title>
                  <Paragraph>{p.anchor}</Paragraph>
                  <div className="source-actions">
                    <Button type="primary" icon={<FileTextOutlined />}
                      disabled={!Number.isInteger(p.pdf_page) || p.pdf_page < 1}
                      onClick={() => setTextbook(sourceFromPage(current, i))}>{openPageLabel(p.printed)}</Button>
                  </div>
                  {p.verified && current === focusedIndicator && focusedQuestion && quiz?.availableItems?.includes(focusedQuestion.id)
                    && (!params.get('studentId') || answer) && <ReviewDraft
                      key={`${quiz.id}|${classId}|${focusedQuestion.id}|${answer?.id}|${p.material_id}|${p.pdf_page}`}
                      questionId={focusedQuestion.id} quizId={quiz.id} quizName={quizLabel(quiz)} classId={classId ?? undefined} answer={answer}
                      stem={focusedQuestion.stem} focusCard={focusLecture && i === current.textbook_pages.findIndex((item) => item.verified)}
                      page={p} onOpen={() => setTextbook(sourceFromPage(current, i))} back={back} followUp={followUp} />}
                </List.Item>
              )} />
            )}
              </div>
              <div className="source-ev-col">
                <h3>本班题目 · 按错误率</h3>
                {questionRates.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本班此考点暂无有效作答" /> : (
                  <ul className="source-q-list">
                    {questionRates.slice(0, 6).map((q) => <li key={q.id}>
                      <button type="button" className="source-q-item" onClick={() => focusQuestion(q.id)}>
                        <span>{questionLabel(q.id)}</span>
                        <i className="source-q-heat"><em style={{ width: `${Math.round((q.rate ?? 0) * 100)}%` }} /></i>
                        <b>{q.rate == null ? '—' : `${Math.round(q.rate * 100)}%`}</b>
                        <small>{q.lost} 人失分</small>
                      </button>
                    </li>)}
                  </ul>
                )}
                {lostStudents.length > 0 && <div className="source-lost"><span className="teacher-muted">失分学生：</span>
                  {lostStudents.slice(0, 6).map((sid) => <Link key={sid} className="teacher-source-link"
                    to={`/student-answers?studentId=${sid}&quizId=${statQuizId}&tab=profile`}>{bundle?.students.find((s) => s.id === sid)?.name || sid}</Link>)}
                </div>}
              </div>
            </div>
            <div className="teacher-actions source-actions-row">
              <Link className="teacher-source-link" to={`/class-analytics?quizId=${statQuizId}&classId=${statClassId}`}>返回考试分析</Link>
              <Link className="teacher-source-link" to={`/student-answers?quizId=${statQuizId}&classId=${statClassId}`}>看本班学情</Link>
              {followUp}
            </div>
          </section>
          {connections.length > 0 && <section className="teacher-section source-sequence" aria-label="考点学习顺序">
            <h2 className="teacher-section-heading"><NodeIndexOutlined aria-hidden /> 教学衔接 · 先复习 / 接着学</h2>
            <p className="teacher-muted">备课时决定顺序：本考点的前置考点先复习、后续考点接着学，点击可切换查看。依据赛题考点依赖整理，可按本班表现调整。</p>
            <ul>
              {connections.map(({ related, reason, prerequisite }) => <li key={related.indicator_id}>
                <span className="source-sequence-kind">{prerequisite ? '可先复习' : '可接着学'}</span>
                <div>
                  <Button type="link" onClick={() => selectIndicator(related)}>{related.requirement}</Button>
                  <p>{reason}</p>
                </div>
              </li>)}
            </ul>
          </section>}
          <section className="teacher-section source-related">
            <h2 className="teacher-section-heading"><LinkOutlined aria-hidden /> 本考点可用题目与学习卡</h2>
            <List locale={{ emptyText: '暂无关联材料' }}
              dataSource={[...(current.questions || []), ...(current.learning_cards || [])] as RelatedMaterial[]}
              renderItem={(m, i) => {
                const card = isLearningCard(m);
                const src = materialSource(m, bundle?.questions.find((q) => q.id === m.material_id)?.sourceLocation);
                return (
                  <List.Item key={`${card ? 'card' : 'question'}-${m.material_id}-${i}`}>
                    <Button type="text" data-testid={`material-item-${m.material_id}`}
                      onClick={() => setMaterialDetail(m)}
                      style={{
                        width: '100%', height: 'auto', display: 'flex', alignItems: 'flex-start',
                        justifyContent: 'flex-start', gap: 8, flexWrap: 'wrap', textAlign: 'left',
                        whiteSpace: 'normal', padding: '6px 8px',
                      }}>
                      <Tag color={card ? 'green' : 'blue'} style={{ marginInlineEnd: 0 }}>{card ? '学习卡' : '题目'}</Tag>
                      <span style={{ flex: 1, minWidth: 200 }}>{m.name}</span>
                      <span className="teacher-muted" title={src.full || undefined} style={{ fontSize: 12 }}>来源：{src.label}</span>
                    </Button>
                  </List.Item>
                );
              }} />
          </section>
          <details className="source-audit source-audit-judge">
            <summary>数据复核明细（评委/答辩用 · 教师可忽略）</summary>
            <p>考点 {current.indicator_id} · 课标 {current.standard.code} · PDF 文件第 {current.standard.pdf_page ?? '—'} 页</p>
            {current.standard.note && <p>{current.standard.note}</p>}
            {current.textbook_pages.map((p) => <p key={`${p.material_id}|${p.pdf_page}`}>
              {p.material_id} · {printedPageLabel(p.printed)} ↔ PDF 文件第 {p.pdf_page} 页 · {p.verified ? '已核对具体页' : '待核对'}
            </p>)}
          </details>
        </Col>
      </Row>
      <Modal open={!!materialDetail} width={720} destroyOnHidden
        title={materialDetail ? (isLearningCard(materialDetail)
          ? `学习卡 · ${materialDetail.material_id}`
          : `题目详情 · ${questionLabel(materialDetail.material_id)}`) : ''}
        onCancel={() => setMaterialDetail(null)}
        footer={<Button onClick={() => setMaterialDetail(null)}>关闭</Button>}>
        {materialDetail && (() => {
          const m = materialDetail;
          const card = isLearningCard(m);
          const bq = bundle?.questions.find((q) => q.id === m.material_id);
          const src = materialSource(m, bq?.sourceLocation);
          const rubricLines = (m.rubric || '').split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
          const fallbackPoints = bundle?.answers.find((a) => a.questionId === m.material_id && a.scoringPoints?.length)?.scoringPoints || [];
          return (
            <div data-testid="material-detail-modal">
              <Space wrap style={{ marginBottom: 12 }}>
                <Tag color={card ? 'green' : 'blue'}>{card ? '学习卡' : '题目'}</Tag>
                {!card && (m.question_type || bq?.questionType) && <Tag>{m.question_type || bq?.questionType}</Tag>}
                <Text type="secondary" title={src.full || undefined}>来源：{src.label}</Text>
              </Space>
              {card ? (
                <Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{m.name}</Paragraph>
              ) : (
                <>
                  <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }} items={[
                    { key: 'type', label: '题型', children: m.question_type || bq?.questionType || '—' },
                    { key: 'score', label: '满分', children: `${m.full_score || (bq?.fullScore ?? '—')} 分` },
                    { key: 'stem', label: '题干', span: 2, children: bq?.stem || m.name },
                    { key: 'answer', label: '参考答案', span: 2, children: bq?.referenceAnswer || '暂无参考答案' },
                    { key: 'analysis', label: '参考解析', span: 2, children: bq?.referenceAnalysis || '暂无参考解析' },
                  ]} />
                  <Text strong>评分点</Text>
                  {rubricLines.length > 0 ? (
                    <ul style={{ margin: '8px 0 0', paddingInlineStart: 20 }}>
                      {rubricLines.map((line, k) => <li key={`${k}-${line}`}>{line}</li>)}
                    </ul>
                  ) : fallbackPoints.length > 0 ? (
                    <ul style={{ margin: '8px 0 0', paddingInlineStart: 20 }}>
                      {fallbackPoints.map((p) => <li key={p.id}>{p.label}（{p.fullScore} 分）</li>)}
                    </ul>
                  ) : <Paragraph type="secondary" style={{ margin: '8px 0 0' }}>暂无评分点明细</Paragraph>}
                </>
              )}
            </div>
          );
        })()}
      </Modal>
      <TextbookDrawer open={!!textbook} source={textbook} onClose={() => setTextbook(null)} />
    </>
  );
};

/** 演示样例视图：保留原结构供回归使用，但不再自称「官方」，空状态文案改为中性。 */
const DemoView: React.FC<{ focusQuestionId: string | null }> = ({ focusQuestionId }) => {
  const [search, setSearch] = useState('');
  const [topicId, setTopicId] = useState(TOPICS[0].id);
  const kps = useMemo(() => listKnowledgePoints(topicId), [topicId]);
  const [kpId, setKpId] = useState<string>(kps[0]?.id || '');
  const [textbook, setTextbook] = useState<TextbookSource | null>(null);

  React.useEffect(() => {
    if (!focusQuestionId) return;
    for (const t of TOPICS) {
      const found = listKnowledgePoints(t.id).find((k) =>
        listQuestionsByKp(k.id).some((q) => q.id === focusQuestionId));
      if (found) { setTopicId(t.id); setKpId(found.id); break; }
    }
  }, [focusQuestionId]);

  const currentKp: KnowledgePoint | undefined = kps.find((k) => k.id === kpId) || kps[0];
  const demoKeyword = search.trim();
  const matchingKps = useMemo(() => TOPICS.flatMap((topic) => listKnowledgePoints(topic.id)
    .filter((kp) => kp.name.includes(demoKeyword)).map((kp) => ({ topicId: topic.id, kp }))), [demoKeyword]);
  React.useEffect(() => {
    if (!demoKeyword || matchingKps.length === 0 || matchingKps.some(({ kp }) => kp.id === kpId)) return;
    const first = matchingKps[0];
    setTopicId(first.topicId);
    setKpId(first.kp.id);
  }, [demoKeyword, matchingKps, kpId]);
  const curriculum = currentKp ? getCurriculum(currentKp.id) : undefined;
  const questions = currentKp ? listQuestionsByKp(currentKp.id) : [];
  const openTopic = (tid: string) => {
    setTopicId(tid);
    const first = listKnowledgePoints(tid)[0];
    setKpId(first?.id || '');
  };

  return (
    <>
      {focusQuestionId && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="从答卷题目反查出处"
          description={`已定位到题目 ${focusQuestionId} 所属考点，并可打开对应教材页。`} />
      )}
      <Row gutter={16}>
        <Col xs={24} md={7}>
          <Card size="small" title="章节与考点" style={{ marginBottom: 12 }}>
            <Input.Search allowClear placeholder="搜索考点" aria-label="搜索演示考点"
              value={search} onChange={(event) => setSearch(event.target.value)} style={{ marginBottom: 12 }} />
            <Menu mode="inline" selectedKeys={[currentKp?.id || '']} onClick={({ key }) => {
              const topic = TOPICS.find((t) => listKnowledgePoints(t.id).some((k) => k.id === key));
              if (topic) setTopicId(topic.id);
              setKpId(key);
            }}
              items={TOPICS.map((t) => ({
                key: `topic-${t.id}`, label: t.name,
                children: listKnowledgePoints(t.id).filter((k) => k.name.includes(search.trim()))
                  .map((k) => ({ key: k.id, label: k.name })),
              })).filter((t) => t.children.length > 0)}
              openKeys={search.trim() ? TOPICS.map((t) => `topic-${t.id}`) : [`topic-${topicId}`]}
              onOpenChange={(keys) => {
                const last = keys[keys.length - 1];
                if (last?.startsWith('topic-')) openTopic(last.replace('topic-', ''));
              }} />
          </Card>
        </Col>
        <Col xs={24} md={17}>
          {!currentKp ? <Card><Empty description="请选择考点" /></Card> : (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Card size="small" title={<Space><BookOutlined />考点：{currentKp.name}</Space>}>
                <details key={currentKp.id}>
                  <summary style={{ cursor: 'pointer' }}>课程要求</summary>
                  {curriculum ? (
                    <><Text type="secondary">{curriculum.level}</Text>
                      <Paragraph style={{ marginTop: 8 }}>{curriculum.requirement}</Paragraph></>
                  ) : <Text type="secondary">该考点暂无课程要求</Text>}
                </details>
              </Card>
              <Card size="small" title="对应题目与教材出处">
                {questions.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该考点暂无关联题目" />
                ) : (
                  <List dataSource={questions} renderItem={(q) => (
                    <List.Item key={q.id}>
                      <Space direction="vertical" size={4} style={{ width: '100%' }}>
                        <Space wrap>
                          <Tag>{q.id}</Tag>
                          <Text strong style={{ color: q.id === focusQuestionId ? '#b45309' : undefined }}>{q.stem}</Text>
                          {q.id === focusQuestionId && <Tag color="gold">来自答卷</Tag>}
                        </Space>
                        <Space wrap>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            <LinkOutlined /> 出处：{getSourceByQuestion(q.id)
                              ? `${getSourceByQuestion(q.id)!.docName} · 第 ${getSourceByQuestion(q.id)!.page} 页`
                              : '暂无出处'}
                          </Text>
                          {getSourceByQuestion(q.id) && (
                            <Button size="small" type="link" icon={<FileTextOutlined />}
                              onClick={() => setTextbook(getSourceByQuestion(q.id)!)}>打开第 {getSourceByQuestion(q.id)!.page} 页</Button>
                          )}
                        </Space>
                      </Space>
                    </List.Item>
                  )} />
                )}
              </Card>
            </Space>
          )}
        </Col>
      </Row>
      <TextbookDrawer open={!!textbook} source={textbook} onClose={() => setTextbook(null)} />
    </>
  );
};

const CurriculumPage: React.FC = () => {
  const [params] = useSearchParams();
  const focusQuestionId = params.get('questionId');
  const official = isPrivateCompetitionData() && !!getBundle()?.curriculumMap;

  return (
    <div className="competition-page">
      <div className="teacher-page-header">
        <Title level={2} className="competition-title"><BookOutlined aria-hidden /> 课标与教材</Title>
        <Text className="teacher-muted">{official ? '官方赛题资料' : '演示样例 · 非正式教材'}</Text>
      </div>
      {official
        ? <OfficialView params={params} />
        : <DemoView focusQuestionId={focusQuestionId} />}
    </div>
  );
};

export default CurriculumPage;
