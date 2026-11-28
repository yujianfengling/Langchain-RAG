import { Button } from 'antd';
import { questionLabel } from '../shared/teacherContext';
import { compareRepeatedItems, summarizeEvidence } from './profileInsights';
import type { ProfileAnswerRef, ProfileRecord, StudentProfileData } from './studentProfileData';

export default function ProfileEvidence({ profile, focusQuestionId, onOpenAnswer }: {
  profile: StudentProfileData;
  focusQuestionId?: string;
  onOpenAnswer: (target: ProfileAnswerRef) => void;
}) {
  const current = profile.history.find((q) => q.id === profile.quiz?.id);
  if (!current) return null;
  const evidence = summarizeEvidence(current.records);
  const repeat = compareRepeatedItems(profile);
  const priorities = [...evidence.genuineLosses].sort((a, b) =>
    Number(b.record.questionId === focusQuestionId) - Number(a.record.questionId === focusQuestionId));
  const scope = profile.quiz?.availableItems?.length;
  const score = (record: ProfileRecord) => `${record.score}/${record.fullScore} 分`;
  return <section className="teacher-section profile-evidence" aria-label="作答证据分析">
    <h3>作答证据分析</h3>
    <div className="profile-evidence-grid">
      <div>
        <h4>本次记录覆盖</h4>
        <p><strong>{current.totals.effectiveQuestionCount}{scope == null ? '' : ` / ${scope}`} 题</strong> 有有效得分记录</p>
        <p className="teacher-muted">分母为本次测验公布的题目范围。未覆盖题目不计零分。</p>
        <div className="profile-evidence-counts"><span>满分 <b>{evidence.full}</b></span><span>部分得分 <b>{evidence.partial}</b></span><span>零分 <b>{evidence.zero}</b></span></div>
        <p className="teacher-muted">按作答记录计。{evidence.withSteps} 条有解题步骤，{evidence.rubricCount} 条评分点和总分可以互相核对。</p>
      </div>
      <div>
        <h4>优先查看的失分评分点</h4>
        {evidence.genuineLosses.length === 0 ? <p className="teacher-muted">未找到可与题目总分对上的失分评分点，可查看原答卷核对。</p>
          : <><ul>{priorities.slice(0, 4).map(({ record, point, loss }) => <li key={`${record.key}|${point.id}`}>
            <Button type="link" onClick={() => onOpenAnswer(record)}>{questionLabel(record.questionId)}</Button>
            <span>评分点「{point.label}」{point.earnedScore === 0 ? `未得分（失 ${loss} 分）` : `失 ${loss} 分`}</span>
          </li>)}</ul>
          {priorities.length > 4 && <p className="teacher-muted">优先显示当前题和失分较多的评分点，其余见本次考点表现。</p>}</>}
      </div>
    </div>
    <div className="profile-same-items">
      <h4>与上一场的共同题对照</h4>
      {!repeat ? <p className="teacher-muted">当前测验没有上一场可对照。</p>
        : repeat.rows.length === 0 ? <p className="teacher-muted">与 {repeat.previous.name} 没有可直接对照的有效题目。</p>
          : <><p className="teacher-muted">仅比较同题、同满分且两场各有一份有效记录的题目。单题变化仅反映这两次作答。</p>
            <ul>{repeat.rows.map(({ id, before, after }) => <li key={id}>
              <strong>{questionLabel(id)}</strong>
              <Button type="link" onClick={() => onOpenAnswer(before)}>{repeat.previous.name} · {score(before)}</Button>
              <span aria-hidden="true">→</span>
              <Button type="link" onClick={() => onOpenAnswer(after)}>{repeat.current.name} · {score(after)}</Button>
            </li>)}</ul></>}
    </div>
  </section>;
}
