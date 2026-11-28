import { canUseExternalAi, LOCAL_DATA_NOTICE } from './externalAi';
import React, { useCallback, useMemo, useState } from 'react';
import { Button, Empty, Modal, Tabs, Tag, Typography } from 'antd';
import { useLocation, useNavigate } from 'react-router-dom';
import { assistantPath } from './teacherContext';
import type { EChartsOption } from 'echarts';
import EChart from './EChart';
import type { Bundle } from '../dataSource';
import type { ProfileAnswerRef } from '../studentAnswers/studentProfileData';
import { questionLabel } from './teacherContext';
import { printedPageLabel } from './textbookPage';

const { Text, Paragraph } = Typography;
const matchesIndicator = (kpId: string | undefined, ind: string) => !!kpId
  && (kpId === ind || kpId.endsWith('-' + ind) || kpId.split(/[,、|;]/).map((s) => s.trim()).includes(ind));

export interface StudyPackProps {
  bundle: Bundle;
  indicatorId: string;
  quizId: string;
  classId: string;
  studentId?: string;
  focusQuestionId?: string;
  open: boolean;
  onClose: () => void;
  onStartPractice?: (original: ProfileAnswerRef, practiceQuestion: string, hint?: string) => void;
}

/**
 * Gemini Notebook 式「学习包」：闪卡 / 思维导图 / 变式小测。
 * 全部由本地官方 bundle 现算生成（不联网、不外传）；变式小测复用正式练习核对，不自动改分。
 */
const StudyPack: React.FC<StudyPackProps> = ({ bundle, indicatorId, quizId, classId, studentId, focusQuestionId, open, onClose, onStartPractice }) => {
  const [flipped, setFlipped] = useState<number | null>(null);
  const indicator = bundle.curriculumMap?.indicators.find((i) => i.indicator_id === indicatorId);
  const questions = useMemo(() => bundle.questions.filter((q) => matchesIndicator(q.kpId, indicatorId)), [bundle, indicatorId]);
  const classAnswers = useMemo(() => bundle.answers.filter((a) => a.quizId === quizId
    && bundle.students.some((s) => s.id === a.studentId && s.classId === classId)), [bundle, quizId, classId]);
  const rateOf = useCallback((qid: string) => {
    const rows = classAnswers.filter((a) => a.questionId === qid);
    const full = rows.reduce((s, a) => s + a.fullScore, 0);
    return full > 0 ? rows.reduce((s, a) => s + a.score, 0) / full : null;
  }, [classAnswers]);
  const studentRecords = useMemo(() => studentId
    ? bundle.answers.filter((a) => a.studentId === studentId && a.quizId === quizId) : [], [bundle, studentId, quizId]);
  const origin: ProfileAnswerRef | undefined = useMemo(() => {
    const rec = studentRecords.find((r) => r.questionId === focusQuestionId) || studentRecords.find((r) => r.score < r.fullScore) || studentRecords[0];
    return rec ? { questionId: rec.questionId, quizId, recordId: rec.sourceRecordId } : undefined;
  }, [studentRecords, focusQuestionId, quizId]);

  const cards = useMemo(() => questions.slice(0, 5).map((q) => {
    const lost = studentRecords.find((r) => r.questionId === q.id)?.scoringPoints.filter((p) => p.earnedScore < p.fullScore) || [];
    const back = q.referenceAnswer || q.referenceAnalysis
      || (lost.length ? lost.map((p) => `${p.label}（应得 ${p.fullScore} 分）`).join('；') : null)
      || (indicator?.textbook_pages[0] ? `${indicator.textbook_pages[0].anchor}（${printedPageLabel(indicator.textbook_pages[0].printed)}）` : '见教材对应页');
    return { id: q.id, front: `${questionLabel(q.id)} · ${q.stem}`, back, lost: lost.length };
  }), [questions, studentRecords, indicator]);

  const treeOption = useMemo<EChartsOption>(() => {
    const themeId = indicator?.theme_id;
    const siblings = (bundle.curriculumMap?.indicators || []).filter((i) => i.theme_id === themeId).slice(0, 4);
    const color = (r: number | null) => r == null ? '#94a3b8' : r < 0.6 ? '#d62728' : r < 0.85 ? '#ff7f0e' : '#2ca02c';
    const root = {
      name: indicator?.theme_name || themeId || '主题',
      children: siblings.map((i) => {
        const qs = bundle.questions.filter((q) => matchesIndicator(q.kpId, i.indicator_id)).slice(0, 4);
        const rates = qs.map((q) => rateOf(q.id)).filter((r): r is number => r != null);
        const avg = rates.length ? rates.reduce((s, r) => s + r, 0) / rates.length : null;
        return {
          name: i.requirement.length > 14 ? i.requirement.slice(0, 13) + '…' : i.requirement,
          itemStyle: { color: color(avg) },
          children: qs.map((q) => ({ name: questionLabel(q.id), value: rateOf(q.id), itemStyle: { color: color(rateOf(q.id)) } })),
        };
      }),
    };
    return {
      tooltip: { trigger: 'item', formatter: (p: unknown) => { const d = p as { name?: string; value?: unknown }; return `${d.name}${typeof d.value === 'number' ? ` · 得分率 ${Math.round(d.value * 100)}%` : ''}`; } },
      series: [{ type: 'tree', data: [root], orient: 'LR', top: '4%', left: '14%', bottom: '4%', right: '18%',
        symbolSize: 9, initialTreeDepth: 2, expandAndCollapse: true,
        label: { position: 'left', verticalAlign: 'middle', align: 'right', fontSize: 12 },
        leaves: { label: { position: 'right', align: 'left' } },
        lineStyle: { color: '#c7d2e0', width: 1.2 }, emphasis: { focus: 'descendant' } }],
    };
  }, [bundle, indicator, rateOf]);

  const variants = useMemo(() => questions.filter((q) => q.id !== focusQuestionId).slice(0, 3), [questions, focusQuestionId]);
  const navigate = useNavigate();
  const location = useLocation();
  const externalAi = canUseExternalAi();

  return <Modal open={open} onCancel={onClose} width={880} footer={<Button onClick={onClose}>关闭</Button>}
    title={`学习包 · ${indicator?.requirement || indicatorId}`}>
    <Tabs defaultActiveKey="cards" items={[
      { key: 'cards', label: `闪卡（${cards.length}）`, children: cards.length ? <div className="study-pack-cards">
        <p className="teacher-muted">点击卡片翻面：正面原题，背面参考答案/失分点/教材出处。本地生成，不外传。</p>
        {cards.map((c, i) => <button key={c.id} type="button" className={'study-card' + (flipped === i ? ' is-flipped' : '')} onClick={() => setFlipped(flipped === i ? null : i)}>
          <span className="study-card-face">{flipped === i ? c.back : c.front}</span>
          <span className="study-card-hint">{flipped === i ? '点击看原题' : '点击翻面'}{c.lost > 0 && <Tag color="red">本人失分 {c.lost} 点</Tag>}</span>
        </button>)}
      </div> : <Empty description="该考点暂无可生成闪卡的题目" /> },
      { key: 'map', label: '思维导图', children: <div>
        <p className="teacher-muted">主题 → 考点 → 题目，节点颜色＝本班得分率（红&lt;60%、橙&lt;85%、绿≥85%）。点击节点展开/收起。</p>
        <EChart option={treeOption} height={320} ariaLabel="主题考点题目思维导图" />
      </div> },
      { key: 'quiz', label: `变式小测（${variants.length}）`, children: variants.length ? <div className="study-pack-quiz">
        <p className="teacher-muted">同考点换一道题核对能否迁移。练习走正式核对流程，不自动改动原测验成绩。</p>
        {variants.map((q) => <div className="study-quiz-row" key={q.id}>
          <div><strong>{questionLabel(q.id)}</strong><Paragraph style={{ margin: 0 }}>{q.stem}</Paragraph>
            <Text type="secondary">本班得分率 {rateOf(q.id) == null ? '—' : `${Math.round(rateOf(q.id)! * 100)}%`}</Text></div>
          {onStartPractice && origin
            ? <Button size="small" type="primary" onClick={() => onStartPractice(origin, q.id)}>练这道</Button>
            : <Text type="secondary">需选定学生与原答卷后才能练习</Text>}
        </div>)}
      </div> : <Empty description="该考点暂无其它可练习题" /> },
      { key: 'ai', label: 'AI 出题', children: <div className="study-pack-quiz">
        <p className="teacher-muted">跳转 AI 助手，自动发送本考点＋学生上下文的出题请求（换数字/情境、解法不变）。AI 生成·需教师核对，不入正式卷。</p>
        <Button type="primary" disabled={!externalAi} data-testid="jump-assistant-questions"
          onClick={() => navigate(assistantPath(new URLSearchParams(location.search), {
            classId, quizId, prompt: `请针对学生 ${studentId || ''} 的考点 ${indicatorId} 薄弱点出2道变式题并给解析。`,
          }))}>AI 生成变式题（跳转 AI 助手）</Button>
        {!externalAi && <p className="teacher-muted">{LOCAL_DATA_NOTICE}</p>}
      </div> },
    ]} />
  </Modal>;
};

export default StudyPack;
