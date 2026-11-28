import { LOCAL_DATA_NOTICE } from '../shared/externalAi';
import { useState } from 'react';
import { Button } from 'antd';
import type { LiveEvidenceEntry } from '../livePretest/LivePretest';
import { questionLabel } from '../shared/teacherContext';
import type { ProfileRecord } from './studentProfileData';

const changes: Record<LiveEvidenceEntry['kind'], string> = {
  mid_wrong: '先练去括号，核对括号前的符号和系数。',
  combine_wrong: '先复练合并同类项，逐项核对系数。',
  transcription_wrong: '重点核对过程末行和最终填答。',
  final_wrong: '先请学生补写合并后的算式，再决定复练哪一步。',
  manual: '先由教师核对作答，再决定复练哪一步。',
  correct: '这次核对通过，可换一道题观察能否独立完成。',
};

function OriginalWork({ original }: { original: ProfileRecord }) {
  const source = original.answer;
  const lastStep = source && source.steps[source.steps.length - 1]?.text;
  return <div className="followup-answer-box">
    <strong>原作答 · {questionLabel(original.questionId)}</strong>
    <dl>
      <div><dt>过程末行</dt><dd>{source?.stepsUnavailable || !lastStep ? '未记录' : lastStep}</dd></div>
      <div><dt>最终填答</dt><dd>{source?.finalAnswer || '未记录'}</dd></div>
      <div><dt>原测验得分</dt><dd>{original.score}/{original.fullScore} 分</dd></div>
    </dl>
  </div>;
}

export function WrittenFollowUp({ original, questionId, response, reviewed }: {
  original?: ProfileRecord;
  questionId: string;
  response: string;
  reviewed: boolean;
}) {
  return <div className="followup-comparison followup-comparison-grid">
    {original && <OriginalWork original={original} />}
    <div className="followup-answer-box followup-pending-response">
      <strong>新作答 · {questionLabel(questionId)}</strong>
      <p>{response}</p>
      <span className="teacher-muted">{reviewed ? '教师已核对' : '待教师核对'}</span>
    </div>
  </div>;
}

export default function FollowUpComparison({ original, attempt, coverageNote, explain }: {
  original: ProfileRecord;
  attempt: LiveEvidenceEntry;
  coverageNote: string;
  explain?: () => Promise<string>;
}) {
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function ask() {
    if (!explain) return;
    setBusy(true);
    setError('');
    try { setNote(await explain()); }
    catch (err) { setError(err instanceof Error ? err.message : '暂时无法生成说明'); }
    finally { setBusy(false); }
  }
  return <section className="followup-comparison" aria-label="原作答与补测对照">
    <div className="followup-comparison-grid">
      <OriginalWork original={original} />
      <div className="followup-answer-box">
        <strong>新作答 · {questionLabel(attempt.questionId)}</strong>
        <dl>
          <div><dt>去括号后</dt><dd>{attempt.intermediate || '未填写'}</dd></div>
          <div><dt>合并后</dt><dd>{attempt.combined || '未填写'}</dd></div>
          <div><dt>最终填答</dt><dd>{attempt.final || '未填写'}</dd></div>
        </dl>
      </div>
    </div>
    <div className="followup-next">
      <p><b>这次建议</b> {changes[attempt.kind]}</p>
      <p>{attempt.supports}</p>
      <p><b>仍需保留</b> {coverageNote} 原测验评分保持不变。</p>
      <Button type="link" title={!explain ? LOCAL_DATA_NOTICE : undefined} disabled={!explain} loading={busy} onClick={() => void ask()}>{note ? '重新说明调整理由' : '请 AI 说明调整理由'}</Button>
      {note && <p className="followup-ai-note">{note}</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  </section>;
}
