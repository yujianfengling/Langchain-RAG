import React, { useMemo, useState } from 'react';
import {
  Alert, Button, Card, Collapse, Empty, Input, List, Space, Table, Tag, Typography,
} from 'antd';
import {
  BookOutlined, BulbOutlined, EditOutlined, HistoryOutlined, RightOutlined,
  RollbackOutlined,
} from '@ant-design/icons';
import { getSuggestion, getFeedback } from './data';
import { getStudentName } from '../shared/ids';
import { getBundle, isRealData } from '../dataSource';
import { getAnswer, listStudentEvents } from '../studentAnswers/data';
import { questionLabel } from '../shared/teacherContext';
import LivePretest, { pickQuestion } from '../livePretest/LivePretest';
import type { TextbookSource } from '../shared/types';
import type {
  Candidate, Diagnosis as OfficialDiagnosis, ExecutableTask, RealAnswer,
} from '../dataSource';

const { Text, Paragraph, Title } = Typography;

/**
 * 分步提示的标题来自官方诊断，个别标题会直接写出学生的正确过程末行与填答式。
 * 赛题要求提示不直接给答案：展示层把这类表达式改为引导语，原始表达式仍保留在
 * 同页「观察到的事实 / 评分证据」中，不修改官方 bundle 或统计。
 */
function safeHintTitle(title: string): string {
  return title
    .replace(/官方『解答过程』末行得到\s*[^\s，,；;。]+，?/g, '官方过程末行的结果，')
    .replace(/『学生最终作答』栏填\s*[^\s，,；;。]+/g, '学生填在最终作答栏的结果')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

function studentFacingText(text: string): string {
  return text
    .replace(/\b[QL]\d{2,3}\b/g, '对应材料')
    .replace(/\bT\d+I\d+\b/g, '当前考点')
    .replace(/\b(?:DR|LIVE-)[A-Za-z0-9-]+\b/g, '本次记录');
}

/**
 * 教师调整 v2：整套任务更换（材料+依据+步骤+提示+复查条件是一整套）。
 * 只记录选择，不写回原始答卷 / 官方评分 / 班级统计。
 */
type TeacherChoice = {
  mode: 'replace' | 'pretest';
  task: ExecutableTask;
  at: string;
};

const CHOICE_KEY = 'competition.teacher.adjustments.v2';

function readChoice(key: string): TeacherChoice | undefined {
  try {
    const raw = localStorage.getItem(CHOICE_KEY);
    if (!raw) return undefined;
    const c = (JSON.parse(raw) as Record<string, TeacherChoice | undefined>)[key];
    // 形状校验：旧 v1（materialId/reason 散字段）或损坏数据一律忽略
    if (!c || (c.mode !== 'replace' && c.mode !== 'pretest')) return undefined;
    if (!c.task || typeof c.task.material_id !== 'string' || !Array.isArray(c.task.steps)) return undefined;
    if (typeof c.task.type !== 'string' || typeof c.task.reason !== 'string' || typeof c.task.review !== 'string') return undefined;
    if (!c.task.steps.every((step) => typeof step.order === 'number' && typeof step.title === 'string' && typeof step.when_stuck === 'string')) return undefined;
    return c;
  } catch { return undefined; }
}

function writeChoice(key: string, value: TeacherChoice | undefined): void {
  try {
    const all = JSON.parse(localStorage.getItem(CHOICE_KEY) || '{}') as Record<string, TeacherChoice>;
    if (value) all[key] = value; else delete all[key];
    localStorage.setItem(CHOICE_KEY, JSON.stringify(all));
  } catch { /* 隐私模式下不持久化，界面仍可用 */ }
}

/** 官方教材出处（从诊断里的教材页码构造，走受控接口） */
function diagnosisTextbook(d: OfficialDiagnosis): TextbookSource | undefined {
  if (!d.textbook?.material_id || d.textbook.material_id === '待定位' || !d.textbook.pdf_page) return undefined;
  return {
    docId: d.textbook.material_id,
    docName: `赛方教材切片 ${d.textbook.material_id}`,
    page: d.textbook.pdf_page,
    exampleTitle: `${d.textbook.anchor || d.indicator_id}；教材印刷页 ${d.textbook.printed || '范围待定位'}`,
    pdfUrl: `/api/competition/official-pdf/${d.textbook.material_id}`,
    printedPage: d.textbook.printed,
    verified: d.textbook.verified,
  };
}

/** 候选 → 整套替换任务（题目候选 / 学习卡候选） */
function buildReplaceTask(c: Candidate, ind: string): ExecutableTask {
  const mid = c.material_id;
  const name = c.name || '';
  const review = `完成后记录新的作答证据编号；看 ${ind} 同类评分点是否得分。`;
  if (c.material_type === '学习卡') {
    return {
      material_id: mid,
      type: '替换练习',
      reason: `教师从候选中替换为 ${mid}（${name.slice(0, 30)}；学习卡，对照 ${ind} 的要求自学核对）`,
      steps: [
        { order: 1, title: `阅读学习卡 ${mid}：${name.slice(0, 40)}`, when_stuck: '先提示圈出学习卡上的关键条件与示例步骤，再提示对照自己的失分步骤。' },
        { order: 2, title: `按学习卡 ${mid} 的方法完成一道同类题并自查每一步`, when_stuck: '先提示写出每步依据，再对照学习卡示例核对。' },
      ],
      review,
    };
  }
  const done = c.done;
  return {
    material_id: mid,
    type: done ? '复练（已做过，如实标注）' : '替换练习',
    reason: `教师从候选中替换为 ${mid}（${name.slice(0, 30)}；${done ? '该生已做过，安排复练' : '该生未做过'}）`,
    steps: [
      { order: 1, title: `独立完成 ${mid}：${name.slice(0, 40)}`, when_stuck: '先提示核对已知条件与目标，再提示可用方法；不公布答案。' },
      { order: 2, title: '对照官方评分点自查每一步', when_stuck: '先提示写出每步依据，再对照评分点。' },
    ],
    review,
  };
}

/** 「改为先补测」：用 official.pretest_questions 构造整套任务 */
function buildPretestTask(d: OfficialDiagnosis): ExecutableTask {
  const qs = d.pretest_questions || [];
  const minEv = d.coverage?.min_evidence ?? 3;
  const ind = d.indicator_id;
  const cardId = d.candidates.find((c) => c.material_type === '学习卡')?.material_id || d.resource.material_id;
  const review = `看到≥${minEv} 条有效已评分记录后重新计算考点得分率；无记录节点仍标为缺记录。`;
  if (!qs.length) {
    return {
      material_id: cardId,
      type: '先补测',
      reason: `教师判断证据不足：先补测，但本节点暂无可用补测题（pretest_questions 为空），缺少 ${ind} 的同指标新题资源，需教师补充后再收集有效记录。`,
      steps: [
        { order: 1, title: `先用学习卡 ${cardId} 对照 ${ind} 的要求，等待教师补充补测题`, when_stuck: '先提示核对已知条件与目标，再提示可用方法；不公布答案。' },
      ],
      review,
    };
  }
  return {
    material_id: qs[0] || cardId,
    type: '先补测',
    reason: `教师判断证据不足：先补测 ${qs.join('、')}，收集同指标有效记录后再判断。`,
    steps: qs.map((qid, i) => ({
      order: i + 1,
      title: `完成 ${qid}：观察 ${ind} 的已知条件整理与同类评分点表现`,
      when_stuck: '先提示核对已知条件与目标，再提示可用方法；不公布答案。',
    })),
    review,
  };
}

const fmtRate = (r: number | null): string => (r == null ? '—' : `${(r * 100).toFixed(1)}%`);

/** 答卷末尾的教师建议；诊断与回放按需展开，教师调整整套任务且不改官方数据。 */
const LearningAdvice: React.FC<{
  kpId: string;
  studentId: string;
  questionId?: string;
  nodeId?: string;
  recordId?: string;
  onOpenTextbook: (source: TextbookSource) => void;
}> = ({ kpId, studentId, questionId, nodeId, recordId, onOpenTextbook }) => {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [hintIndex, setHintIndex] = useState(0);
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [replayOpen, setReplayOpen] = useState(false);
  const [pretestSignal, setPretestSignal] = useState(0);
  const [manualId, setManualId] = useState('');
  const [manualError, setManualError] = useState('');
  const [version, setVersion] = useState(0);

  const bundle = getBundle();
  const ind = (kpId.match(/-(T\d+I\d+)$/) || [])[1] || kpId;
  const official: OfficialDiagnosis | undefined = useMemo(() => {
    if (!isRealData() || !bundle?.diagnoses || !nodeId) return undefined;
    return bundle.diagnoses.diagnoses.find((d) => d.student_id === studentId && d.indicator_id === ind
      && d.node_id === nodeId);
  }, [bundle, studentId, ind, nodeId]);

  const scopedAnswer = questionId && nodeId ? getAnswer(studentId, questionId, nodeId, recordId) as RealAnswer | undefined : undefined;
  const scopedEvent = questionId && nodeId ? listStudentEvents(studentId, nodeId).find((e) =>
    e.questionId === questionId && (!recordId || e.recordId === recordId)) : undefined;
  const missingRequestedRecord = !!recordId && !scopedAnswer && !scopedEvent;
  const choiceKey = `${studentId}|${nodeId || ''}|${ind}`;
  const choice = readChoice(choiceKey);
  void version; // 触发重读

  const suggestion = getSuggestion(kpId);
  const feedback = getFeedback(studentId);

  // 指定测验下无诊断：如实说明，绝不拿另一场测验的诊断补上
  if (isRealData() && (!official || missingRequestedRecord)) {
    return (
      <section aria-label="建议">
        <h3 className="teacher-section-heading">建议</h3>
        <Paragraph>请先核对本测验的作答记录。</Paragraph>
        <details><summary style={{ cursor: 'pointer' }}>建议依据</summary>
          <Paragraph className="teacher-muted" style={{ marginTop: 8 }}>
            {missingRequestedRecord ? '指定记录与当前学生、测验、题目不匹配，不沿用其他记录的建议。'
              : '当前学生在本测验没有可用的该考点诊断，不使用其他学生或测验的诊断替代。'}
          </Paragraph>
        </details>
      </section>
    );
  }

  if (official) {
    const resource = official.resource;
    const effective: ExecutableTask = choice?.task ?? resource;
    // 记录不匹配或状态无效时，不把考点结论当成本题得分/失分。
    const currentEvent = scopedEvent;
    const available = bundle?.quizzes.find((q) => q.id === official.node_id)?.availableItems;
    const inScope = !available?.length || !!questionId && available.includes(questionId);
    const currentAnswer = scopedAnswer && inScope && !currentEvent?.quarantined && !currentEvent?.outOfScope
      && (!currentEvent || currentEvent.status === '有效得分' || currentEvent.status === '已评分')
      && scopedAnswer.score >= 0 && scopedAnswer.score <= scopedAnswer.fullScore ? scopedAnswer : undefined;
    const currentFinding = currentAnswer ? official.question_findings.find((f) => f.item_id === questionId
      && f.record_id === currentAnswer.sourceRecordId && f.kind === '过程与填答不一致') : undefined;
    const currentCase = currentAnswer ? (currentAnswer.score >= currentAnswer.fullScore ? '满分' : currentAnswer.score === 0 ? '未得分' : '部分得分') : null;
    const candidates = official.candidates.filter((c) => c.node_available);
    const textbook = diagnosisTextbook(official);
    // 调整为指定材料后，在线入口也必须打开该材料，而非悄悄执行原推荐题。
    const liveDiagnosis: OfficialDiagnosis = choice ? { ...official,
      candidates: official.candidates.filter((c) => c.material_id === effective.material_id),
    } : official;
    const onlineQuestion = pickQuestion(ind, liveDiagnosis);
    const hints = effective.steps || [];
    const currentHint = hints[hintIndex];
    // 案例册：case_name 是赛题要求的「展示案例类型」，case 是「考点覆盖判定」，两者口径不同。
    // 只取 focus（讲解重点）文本展示，绝不覆盖诊断结论。
    const caseEntry = bundle?.caseBook?.cases.find((c) => c.student_id === studentId
      && c.indicator_id === ind && c.node_id === official.node_id);
    const caseFocus = caseEntry?.focus;
    const scenario = bundle?.replay?.scenarios.find((s) => s.student_id === studentId
      && s.indicator_id === ind && s.node_id === (nodeId || official.node_id));

    const applyChoice = (mode: TeacherChoice['mode'], task: ExecutableTask) => {
      writeChoice(choiceKey, { mode, task, at: new Date().toISOString() });
      setHintIndex(0); setManualError(''); setVersion((v) => v + 1);
    };

    const missingSteps = currentAnswer && (currentAnswer.stepsUnavailable || !currentAnswer.steps.length);
    const evidenceInsufficient = official.case === '记录不足' || !official.coverage.sufficient;
    const stem = bundle?.questions.find(q => q.id === questionId)?.stem || '';
    const needsSubstitution = /代入|(?:当|若)?\s*[a-z]\s*[=＝].*?(?:值|求|计算)/i.test(stem);
    /** 主建议一句话：老师看完就知道现在做什么（依据在下方按需展开） */
    const mainAction = choice
      ? `请按调整后的「${effective.type}」安排 ${questionLabel(effective.material_id)}${onlineQuestion ? '。' : '（由教师安排）。'}`
      : currentFinding
        ? '请学生核对最后一步和填答栏。'
        : evidenceInsufficient
          ? (onlineQuestion ? '这个考点的有效记录不足，先做一道同类题补充观察，再判断掌握情况。'
            : '这个考点的有效记录不足，请先补充同指标作答记录，再判断是否需要迁移练习。')
          : currentCase === '满分'
            ? (onlineQuestion ? '本题已满分，可做一道同类题观察迁移。' : '本题已满分，可由教师自行选拓展题。')
            : missingSteps
              ? needsSubstitution ? '请学生写出代入后的算式，再核对计算。' : '请学生补写解题过程，再对照评分标准核对。'
              : currentAnswer
                ? (onlineQuestion ? '请学生订正失分步骤，再做一道同类题。' : '请学生核对失分步骤，再由教师安排订正。')
                : '请先核对本题作答状态，再安排补充观察。';
    const factIsCurrentDifference = (text: string) => !!currentFinding && (
      text === currentFinding.fact || text.includes(currentFinding.fact)
      || [currentFinding.last_expr, currentFinding.final_answer].some((expr) => !!expr && text.includes(expr))
      || text.includes(currentFinding.record_id) && /末行|填答|不一致/.test(text)
    );

    /** 手动输入材料编号：用 bundle.questions / curriculumMap 校验存在性 */
    const confirmManual = () => {
      const id = manualId.trim();
      if (!id) { setManualError('请输入材料编号。'); return; }
      const answers = bundle?.answers || [];
      const done = answers.some((a) => a.studentId === studentId && a.questionId === id);
      const doneFull = answers.some((a) => a.studentId === studentId && a.questionId === id
        && a.score != null && !!a.fullScore && a.score >= a.fullScore);
      const mk = (materialId: string, materialType: string, name: string, questionType: string): Candidate => ({
        material_id: materialId, material_type: materialType, question_type: questionType,
        name, done, done_full: doneFull, node_available: true,
      });
      let found: Candidate | undefined;
      for (const ci of bundle?.curriculumMap?.indicators || []) {
        const m = ci.materials.find((x) => x.material_id === id);
        if (m) { found = mk(m.material_id, m.material_type, m.name, m.question_type || ''); break; }
        const lc = ci.learning_cards.find((x) => x.material_id === id);
        if (lc) { found = mk(id, '学习卡', lc.name, lc.question_type || ''); break; }
        const qq = ci.questions.find((x) => x.material_id === id);
        if (qq) { found = mk(id, '题目', qq.name, qq.question_type || ''); break; }
      }
      if (!found) {
        const q = bundle?.questions.find((x) => x.id === id);
        if (q) found = mk(q.id, q.materialType || '题目', q.stem, q.questionType || '');
      }
      if (!found) {
        setManualError(`未找到材料编号 ${id}：请在官方题库或课程映射中核对后再输入。`);
        return;
      }
      applyChoice('replace', buildReplaceTask(found, ind));
      setManualId('');
    };

    // 前后对照：只列变化
    const diffs: string[] = [];
    if (resource.material_id !== effective.material_id) diffs.push(`材料 ${resource.material_id} → ${effective.material_id}`);
    if (resource.type !== effective.type) diffs.push(`类型 ${resource.type} → ${effective.type}`);
    if (resource.steps.length !== effective.steps.length) diffs.push(`步骤 ${resource.steps.length} → ${effective.steps.length}`);
    if (resource.reason !== effective.reason) diffs.push('理由 已更新');
    if (resource.review !== effective.review) diffs.push('复查条件 已更新');

    const replayRows = scenario ? [
      { k: '得分', b: `${scenario.before.score}/${scenario.before.full_score}`, a: `${scenario.after.score}/${scenario.after.full_score}` },
      { k: '得分率', b: fmtRate(scenario.before.rate), a: fmtRate(scenario.after.rate) },
      { k: '有效记录数', b: String(scenario.before.event_count), a: String(scenario.after.event_count) },
      { k: '考点判定', b: scenario.before.case, a: scenario.after.case },
      { k: '建议材料', b: scenario.before.resource.material_id, a: scenario.after.resource.material_id },
      { k: '建议类型', b: scenario.before.resource.type, a: scenario.after.resource.type },
    ] : [];

    return (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <section aria-label="建议">
          <h3 className="teacher-section-heading">建议</h3>
          <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
            <Text strong>{mainAction}</Text>
            <Button size="small" type="link" icon={<EditOutlined />} aria-expanded={adjustOpen}
              onClick={() => setAdjustOpen((v) => !v)}>调整建议</Button>
          </div>
          {missingSteps && currentCase !== '满分' && <p className="teacher-muted">只记录了最终填答，具体失分步骤待核实。</p>}
          <Space wrap style={{ marginTop: 12 }}>
            {onlineQuestion && <Button type="primary" onClick={() => setPretestSignal((v) => v + 1)}>做一道同类题</Button>}
            {textbook && <Button icon={<BookOutlined />} onClick={() => onOpenTextbook(textbook)}>看教材</Button>}
            <Button onClick={() => setFeedbackOpen((v) => !v)} aria-expanded={feedbackOpen}>预览学生看到的内容</Button>
          </Space>
          {choice && !onlineQuestion && <div className="teacher-muted" style={{ marginTop: 8, fontSize: 12 }}>
            当前材料不支持在线作答，请按任务步骤由教师安排；下方预览已同步整套调整。
          </div>}
        </section>

        {/* 教师调整（整套任务更换，前后对照，可恢复；不动官方数据） */}
        {adjustOpen && (
            <div style={{ marginTop: 12, borderTop: '1px dashed #d9d9d9', paddingTop: 10 }}>
              <Title level={5} style={{ fontSize: 13 }}>教师调整（前后对照）</Title>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <Card size="small" style={{ flex: '1 1 45%', background: '#f6f8fb' }}>
                  <Text strong>系统原建议</Text>
                  <div>{resource.material_id} · {resource.type}</div>
                  <Text type="secondary" style={{ fontSize: 11 }}>{resource.reason.slice(0, 70)}…</Text>
                  <div style={{ marginTop: 4 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      步骤 {resource.steps.length} 条 · 复查：{resource.review.slice(0, 40)}…
                    </Text>
                  </div>
                </Card>
                <Card size="small" style={{ flex: '1 1 45%', background: '#fff7e6' }}>
                  <Text strong>当前方案</Text>
                  <div>{effective.material_id} · {effective.type}</div>
                  <Text type="secondary" style={{ fontSize: 11 }}>{effective.reason.slice(0, 70)}…</Text>
                  <div style={{ marginTop: 4 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      步骤 {effective.steps.length} 条 · 复查：{effective.review.slice(0, 40)}…
                    </Text>
                  </div>
                </Card>
              </div>
              {choice && (
                <div style={{ marginTop: 8 }}>
                  <Text strong style={{ fontSize: 12 }}>本次改动</Text>
                  {diffs.length ? (
                    <List size="small" dataSource={diffs}
                          renderItem={(t) => <List.Item><Text style={{ fontSize: 12 }}>{t}</Text></List.Item>} />
                  ) : (
                    <div><Text type="secondary" style={{ fontSize: 12 }}>与系统原建议一致，无变化。</Text></div>
                  )}
                </div>
              )}
              <Space wrap style={{ marginTop: 8 }}>
                {candidates.map((c) => (
                  <Button key={c.material_id} size="small"
                          onClick={() => applyChoice('replace', buildReplaceTask(c, ind))}>
                    替换为 {c.material_id}
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      （{c.done_full ? '已做过且满分' : c.done ? '复练·已做过' : '未做过'}）
                    </Text>
                  </Button>
                ))}
                <Button size="small" type="primary" ghost
                        onClick={() => applyChoice('pretest', buildPretestTask(official))}>
                  改为先补测
                </Button>
                {choice && (
                  <Button size="small" icon={<RollbackOutlined />} onClick={() => {
                    writeChoice(choiceKey, undefined);
                    setHintIndex(0); setManualError(''); setVersion((v) => v + 1);
                  }}>恢复系统建议</Button>
                )}
              </Space>
              <Space.Compact style={{ width: '100%', marginTop: 8 }}>
                <Input size="small" placeholder="手动输入材料编号（题目或学习卡）"
                       value={manualId}
                       onChange={(e) => { setManualId(e.target.value); setManualError(''); }}
                       onPressEnter={confirmManual} />
                <Button size="small" onClick={confirmManual}>确认选择</Button>
              </Space.Compact>
              {manualError && (
                <div style={{ marginTop: 4 }}>
                  <Text type="danger" style={{ fontSize: 12 }}>{manualError}</Text>
                </div>
              )}
              <Alert type="warning" showIcon style={{ marginTop: 8 }}
                     message="教师选择单独保存，原始答卷、官方评分与班级统计不受影响。" />
            </div>
          )}

        {/* 现场补测（整式化简两步观察；单独保存，不写回官方） */}
        {onlineQuestion && <LivePretest
          key={`${studentId}|${official.node_id}|${ind}|${questionId}|${recordId || ''}|${choice?.mode || 'system'}|${effective.material_id}`}
          studentId={studentId}
          nodeId={official.node_id}
          indicatorId={ind}
          diagnosis={liveDiagnosis}
          openSignal={pretestSignal}
          hideCard
        />}

        {/* 诊断与隔离回放只在教师主动展开后阅读。 */}
        <details>
          <summary style={{ cursor: 'pointer' }}>建议依据</summary>
            <div style={{ marginTop: 12 }}>
              <Space wrap>
                {currentCase && (
                  <Tag color={currentCase === '满分' ? 'green' : 'orange'}>
                    本题：{currentCase}{questionId ? `（${questionId}）` : ''}
                  </Tag>
                )}
                <Tag color={official.case === '表现较好' ? 'green' : official.case === '记录不足' ? 'default' : 'orange'}>
                  考点覆盖：{official.case}（{official.coverage.event_count}/{official.coverage.min_evidence} 条）
                </Tag>
                <Tag>{official.node_id}/{official.indicator_id}</Tag>
                <Tag>得分率 {official.rate == null ? '—' : `${(official.rate * 100).toFixed(2)}%`}</Tag>
              </Space>
              <div style={{ marginTop: 8 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>{official.coverage.note}</Text>
              </div>
              {questionId && !currentAnswer && (
                <div style={{ marginTop: 8 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    本题在所选测验无已评分作答，本题判断不适用；考点覆盖与补测任务如下。
                  </Text>
                </div>
              )}
              {caseEntry && (
                <Paragraph style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }} type="secondary">
                  「展示案例」为赛题分类；「考点覆盖」按该生在此考点的有效记录数判定。
                  {caseEntry.selection_reason ? ` 入选理由：${caseEntry.selection_reason}` : ''}
                </Paragraph>
              )}
              {caseFocus && !currentFinding && (
                <Paragraph style={{ fontSize: 12, marginTop: 6, marginBottom: 0 }}>
                  <Text strong style={{ fontSize: 12 }}>讲解重点（案例册）：</Text>{caseFocus}
                </Paragraph>
              )}

              <div className="comp-next-task" style={{ marginTop: 10 }}>
                <Space wrap size={6} style={{ marginBottom: 4 }}>
                  <Tag color="blue">{effective.type}</Tag>
                  <Text strong>选用材料：{effective.material_id}</Text>
                </Space>
                <Paragraph style={{ fontSize: 12.5, marginBottom: 0 }}>{effective.reason}</Paragraph>
              </div>

              <Title level={5} style={{ fontSize: 13, marginTop: 10 }}>分步提示（一次一条）</Title>
              {currentHint ? (
                <Card size="small" style={{ background: '#fffbeb', marginBottom: 8 }}>
                  <Text strong>第 {currentHint.order} 步 · {safeHintTitle(currentHint.title)}</Text>
                  <div style={{ marginTop: 4 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>卡住时：{currentHint.when_stuck}</Text>
                  </div>
                </Card>
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>已展开全部提示；答案与解析不在这里提供。</Text>
              )}
              <Space>
                <Button size="small" disabled={hintIndex >= hints.length - 1}
                        onClick={() => setHintIndex((i) => Math.min(i + 1, hints.length - 1))}>
                  下一条提示
                </Button>
                <Button size="small" type="text" onClick={() => setHintIndex(0)}>从头开始</Button>
              </Space>

              <div className="comp-review-note" style={{ marginTop: 10 }}>
                <Text strong style={{ fontSize: 12 }}>复查条件</Text>
                <div style={{ fontSize: 12.5, marginTop: 2 }}>{effective.review}</div>
              </div>

              <Title level={5} style={{ fontSize: 13, marginTop: 12 }}>观察到的事实</Title>
              <List size="small" dataSource={official.facts.filter((fact) => !factIsCurrentDifference(fact))}
                    renderItem={(t) => <List.Item><Text style={{ fontSize: 12 }}>{t}</Text></List.Item>} />
              <div className="comp-review-note" style={{ marginTop: 8 }}>
                <Text strong style={{ fontSize: 12 }}>推测（需核实）</Text>
                <div style={{ fontSize: 12.5, marginTop: 2 }}>{official.inference}</div>
              </div>
              <div className="comp-review-note" style={{ marginTop: 8 }}>
                <Text strong style={{ fontSize: 12 }}>复查条件</Text>
                <div style={{ fontSize: 12.5, marginTop: 2 }}>{official.observe}</div>
              </div>

              {official.secondary_actions.length > 0 && (
                <div style={{ marginTop: 12, borderTop: '1px dashed #e5e7eb', paddingTop: 10 }}>
                  <Title level={5} style={{ fontSize: 13 }}>考点补充观察</Title>
                  {official.secondary_actions.map((a, i) => (
                    <div key={`${a.material_id}-${i}`} style={i > 0 ? { marginTop: 10, borderTop: '1px dashed #f0f0f0', paddingTop: 10 } : undefined}>
                      <Paragraph style={{ fontSize: 12, marginBottom: 6 }}>{a.reason}</Paragraph>
                      <Space wrap size={4}>
                        {a.question_ids.map((qid) => <Tag key={qid} color="blue">{qid}</Tag>)}
                      </Space>
                      <div style={{ marginTop: 6 }}>
                        <Text type="secondary" style={{ fontSize: 12 }}>复查：{a.review}</Text>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {scenario && (
                <Card size="small" style={{ marginTop: 12 }}
                      title={<Space><HistoryOutlined />证据回放</Space>}
                      extra={<Tag color="purple">自造回放</Tag>}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    隔离副本用正式管线重算，非官方结果，不进入官方统计。
                  </Text>
                  <div style={{ marginTop: 8 }}>
                    <Button size="small" onClick={() => setReplayOpen((v) => !v)}>
                      {replayOpen ? '收起对照' : '展开对照'}
                    </Button>
                  </div>
                  {replayOpen && (
                    <div style={{ marginTop: 10 }}>
                      <Paragraph style={{ fontSize: 12, marginBottom: 8 }}>{scenario.mutation.description}</Paragraph>
                      <Table size="small" pagination={false}
                             rowKey={(r) => `${r.record_id}-${r.version}`}
                             dataSource={scenario.mutation.added_records}
                             columns={[
                               { title: '记录号', dataIndex: 'record_id' },
                               { title: '版本', dataIndex: 'version' },
                               { title: '得分', render: (_, r) => `${r.score}/${r.full_score}` },
                               { title: '状态', dataIndex: 'status' },
                             ]} />
                      <Table size="small" pagination={false} style={{ marginTop: 10 }}
                             rowKey="k" dataSource={replayRows}
                             columns={[
                               { title: '指标', dataIndex: 'k' },
                               { title: '回放前', dataIndex: 'b' },
                               { title: '回放后', dataIndex: 'a' },
                             ]} />
                      {scenario.changes.length > 0 && (
                        <List size="small" style={{ marginTop: 8 }} dataSource={scenario.changes}
                              renderItem={(t) => <List.Item><Text style={{ fontSize: 12 }}>{t}</Text></List.Item>} />
                      )}
                      <Button size="small" icon={<RollbackOutlined />} style={{ marginTop: 8 }}
                              onClick={() => setReplayOpen(false)}>
                        恢复官方视图
                      </Button>
                    </div>
                  )}
                </Card>
              )}
            </div>
        </details>

        {feedbackOpen && (
          <section className="teacher-section" aria-label="学生内容预览" style={{ marginTop: 8 }}>
            <h3 className="teacher-section-heading">学生内容预览 · {getStudentName(studentId)}</h3>
            <Paragraph className="teacher-muted">{choice ? '教师调整后的任务' : '系统任务'} · 仅展示当前学生的内容</Paragraph>
            <Paragraph><Text strong>{effective.type}</Text></Paragraph>
            <Paragraph>{studentFacingText(effective.reason)}</Paragraph>
            {!onlineQuestion && <Paragraph className="teacher-muted">本任务由教师安排，当前材料不提供在线作答入口。</Paragraph>}
            <ol style={{ paddingLeft: 24 }}>
              {effective.steps.map((step) => <li key={step.order} value={step.order} style={{ marginBottom: 12 }}>
                <div>{studentFacingText(safeHintTitle(step.title))}</div>
                <details style={{ marginTop: 4 }}><summary style={{ cursor: 'pointer' }}>需要提示时展开</summary>
                  <Text className="teacher-muted">{studentFacingText(step.when_stuck)}</Text>
                </details>
              </li>)}
            </ol>
            <Text strong>复查条件</Text>
            <Paragraph style={{ marginTop: 4, marginBottom: 0 }}>{studentFacingText(effective.review)}</Paragraph>
          </section>
        )}
      </Space>
    );
  }

  if (!suggestion) {
    return (
      <Card size="small" title={<Space><BulbOutlined />学习建议</Space>}>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
               description={isRealData() ? '暂无学习建议（正式推荐尚未接入）' : '该考点暂无练习建议（演示样例）'} />
      </Card>
    );
  }

  // 演示模式：原有建议保持不变（回归 22/22）
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Card size="small" title={<Space><BulbOutlined />学习建议</Space>} extra={<Tag color="gold">演示样例</Tag>}>
        <Paragraph strong style={{ marginBottom: 6 }}>先看教材例题</Paragraph>
        <Alert type="info" showIcon message={suggestion.textbookExample} style={{ marginBottom: 10 }} />
        <Button size="small" type="link" icon={<BookOutlined />}
                onClick={() => onOpenTextbook(suggestion.textbookSource)}>
          打开教材第 {suggestion.textbookSource.page} 页
        </Button>
        <Paragraph strong style={{ marginTop: 12, marginBottom: 6 }}>再做练习题（按顺序）</Paragraph>
        <List size="small" dataSource={suggestion.exercises}
          renderItem={(ex) => (
            <List.Item>
              <div style={{ width: '100%' }}>
                <Space size={6} wrap><Tag color="blue">第 {ex.order} 题</Tag><Text>{ex.title}</Text></Space>
                <div style={{ marginTop: 4 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>提示：{ex.hint}</Text>
                </div>
                <Collapse ghost size="small" style={{ marginTop: 2 }} items={[{
                  key: 'hint',
                  label: <Button type="link" size="small" style={{ fontSize: 12, padding: 0 }}>展开分步提示</Button>,
                  children: <Text type="secondary" style={{ fontSize: 12 }}>{ex.expandableHint}</Text>,
                }]} />
              </div>
            </List.Item>
          )} />
      </Card>
      <Card size="small" title={<Space><RightOutlined />学生反馈</Space>}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          只显示「{getStudentName(studentId)}」本人本次的反馈、要做的题与提示。
        </Text>
        <div style={{ marginTop: 10 }}>
          <Button type="primary" onClick={() => setFeedbackOpen(true)}>预览学生看到的内容</Button>
        </div>
      </Card>
      {feedbackOpen && (
        <Card size="small" title={`反馈预览 · ${getStudentName(studentId)}`}
              extra={<Button size="small" onClick={() => setFeedbackOpen(false)}>收起</Button>}>
          {feedback ? (
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              <Alert type="success" showIcon message={feedback.summary} />
              <div>
                <Title level={5} style={{ fontSize: 13 }}>要做的题</Title>
                <List size="small" dataSource={feedback.todo} renderItem={(t) => <List.Item>{t}</List.Item>} />
              </div>
              <div>
                <Title level={5} style={{ fontSize: 13 }}>可展开的提示</Title>
                <Collapse size="small" items={feedback.hints.map((h, i) => ({
                  key: String(i), label: h.title, children: h.detail,
                }))} />
              </div>
            </Space>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该学生暂无反馈（演示样例）" />
          )}
        </Card>
      )}
    </Space>
  );
};

export default LearningAdvice;