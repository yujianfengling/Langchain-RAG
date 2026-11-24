import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Alert, Button, Input, Modal } from 'antd';
import { getBundle } from '../dataSource';
import type { Diagnosis as OfficialDiagnosis } from '../dataSource';
import { getStudentName } from '../shared/ids';
import { analysisReturnPath, questionLabel } from '../shared/teacherContext';
import { diffTerms, parseLinear, sameLinear, stripPrefix } from './expr';
import type { Linear } from './expr';

export interface LiveEvidenceEntry {
  at: string;
  studentId: string; nodeId: string; indicatorId: string; questionId: string;
  id?: string;
  intermediate: string; combined?: string; final: string;
  kind: 'mid_wrong' | 'combine_wrong' | 'transcription_wrong' | 'final_wrong' | 'manual' | 'correct';
  supports: string; followUp: string;
}

const STORE_KEY = 'competition.live.pretest.v1';
const scopeKey = (s: string, n: string, i: string) => `${s}|${n}|${i}`;

export function readLiveEvidence(s: string, n: string, i: string): LiveEvidenceEntry[] {
  try {
    const all = JSON.parse(localStorage.getItem(STORE_KEY) || '{}') as Record<string, LiveEvidenceEntry[]>;
    return all[scopeKey(s, n, i)] || [];
  } catch { return []; }
}

function writeLiveEvidence(s: string, n: string, i: string, entries: LiveEvidenceEntry[]): void {
  const all = JSON.parse(localStorage.getItem(STORE_KEY) || '{}') as Record<string, LiveEvidenceEntry[]>;
  if (entries.length) all[scopeKey(s, n, i)] = entries; else delete all[scopeKey(s, n, i)];
  localStorage.setItem(STORE_KEY, JSON.stringify(all));
}

interface SuitQuestion {
  id: string; stem: string; mid: Linear; final: Linear; done: boolean;
}

export function pickQuestion(ind: string, d: OfficialDiagnosis): SuitQuestion | undefined {
  const qs = (getBundle()?.questions || [])
    .filter((q) => q.kpId === ind && (q.materialType || '题目') === '题目'
      && q.stem.includes('化简') && q.stem.includes('(') && q.referenceAnalysis
      && d.candidates.some((c) => c.material_id === q.id && c.node_available));
  const cands: SuitQuestion[] = [];
  for (const q of qs) {
    const segs = (q.referenceAnalysis || '').split('；').map((x) => x.trim()).filter(Boolean);
    if (segs.length < 2) continue;
    const midRaw = stripPrefix(segs[0]);
    const finRaw = stripPrefix(segs[segs.length - 1]);
    const mid = parseLinear(midRaw);
    const fin = parseLinear(finRaw);
    // 参考过程必须等价但写法不同，才能观察展开和合并两个阶段。
    if (!mid || !fin || !sameLinear(mid, fin)) continue;
    if (midRaw.replace(/\s/g, '') === finRaw.replace(/\s/g, '')) continue;
    const done = d.candidates.find((c) => c.material_id === q.id)?.done ?? false;
    cands.push({ id: q.id, stem: q.stem, mid, final: fin, done });
  }
  cands.sort((a, b) => Number(a.done) - Number(b.done) || a.id.localeCompare(b.id));
  return cands[0];
}

export function judgeInputs(midRaw: string, finalRaw: string, q: SuitQuestion, combinedRaw = ''): {
  kind: LiveEvidenceEntry['kind']; diffs: string[]; hint: string; follow: string; supports: string;
} {
  const mid = parseLinear(midRaw);
  const fin = parseLinear(finalRaw);
  const hasCombined = combinedRaw.trim().length > 0;
  const combined = hasCombined ? parseLinear(combinedRaw) : null;
  if (!mid || !fin || (hasCombined && !combined)) {
    const missing = [!mid ? '「去括号后的式子」' : '', !fin ? '「最终结果」' : '',
      hasCombined && !combined ? '「合并后过程末行」' : ''].filter(Boolean).join('与');
    return {
      kind: 'manual', diffs: [],
      hint: `${missing}无法可靠解析（缺失、含不支持的写法或超出整式线性范围）。请补全/改用常见等价写法重新输入，或交由教师核对。`,
      follow: '暂不生成自动判断：保留为「待人工核对」，教师核对后再决定后续安排。',
      supports: '本次输入未产生可自动判定的证据。',
    };
  }
  if (!sameLinear(mid, q.mid)) {
    const diffs = diffTerms(mid, q.mid);
    return {
      kind: 'mid_wrong', diffs,
      hint: `中间变形已出错：${diffs.join('；')}。先检查去括号时括号前符号与系数分配（负号要变号、系数要乘到每一项）。`,
      follow: '后续任务聚焦去括号或符号检查：重做本题去括号一步，再做一道含负系数括号的同指标题。',
      supports: `新增证据：${q.id} 去括号一步与参考不等价（${diffs.join('；')}），支持「去括号/符号」专项补练。`,
    };
  }
  if (combined && !sameLinear(combined, q.final)) {
    const diffs = diffTerms(combined, q.final);
    return {
      kind: 'combine_wrong', diffs,
      hint: `去括号式等价，但合并后过程末行不等价：${diffs.join('；')}。已观察到的首次差异在合并同类项一步。`,
      follow: '后续任务聚焦合并同类项：分别整理各字母项的系数和常数项，再核对过程末行；暂不归因为填答问题。',
      supports: `新增证据：${q.id} 展开式正确、合并后过程末行不等价，支持「合并同类项」检查；不推测学生出错原因。`,
    };
  }
  if (!sameLinear(fin, q.final)) {
    const diffs = diffTerms(fin, q.final);
    return combined ? {
      kind: 'transcription_wrong', diffs,
      hint: `${diffs.join('；')}。展开与合并均等价，最终填答不一致；只观察输入结果，不断定是粗心或抄写错误。`,
      follow: '后续任务聚焦最终填答核对：将过程末行与填答栏逐项对照，再确认提交；已通过的去括号和合并步骤无需按本次证据重练。',
      supports: `新增证据：${q.id} 前两步正确，最终填答与过程末行不一致，支持「最终填答核对」。`,
    } : {
      kind: 'final_wrong', diffs,
      hint: `中间式正确、最终填答有误：${diffs.join('；')}。未填写合并后过程末行，暂不能区分合并同类项错误与最终填答不一致。`,
      follow: '后续任务聚焦结果核对：先补充合并后过程末行，再判断需要合并练习还是最终填答核对。',
      supports: `新增证据：${q.id} 去括号正确但结果不等价；缺少合并过程，原因待核实。`,
    };
  }
  if (/[()（）]/.test(midRaw) || (combined && /[()（）]/.test(combinedRaw))) {
    return {
      kind: 'manual', diffs: [], hint: '表达式等价，但仍含括号，未提供要求的展开过程；不能据此确认完成了去括号或合并步骤。',
      follow: '请补充不带括号的展开式与合并后过程末行，或由教师核对步骤。',
      supports: `${q.id} 只能确认表达式等价，步骤完成情况待人工核对。`,
    };
  }
  return {
    kind: 'correct', diffs: [],
    hint: combined ? '去括号式、合并后过程末行、最终填答均与参考等价；确认本次输入表现，不推断稳定掌握。' : '两个输入均与参考等价；未单独提供合并过程，不声称核验了全部三个步骤。',
    follow: '本次表现可确认；但官方证据覆盖判断保留不变，补充观察任务继续保留。',
    supports: `新增证据：${q.id} 两步均正确，支持「结果核对通过」；不改变官方评分与班级统计。`,
  };
}

const KIND_LABEL: Record<LiveEvidenceEntry['kind'], string> = {
  mid_wrong: '去括号需要复查', combine_wrong: '合并同类项需要复查',
  transcription_wrong: '合并正确，请核对最终填答', final_wrong: '请补充合并后的过程末行',
  manual: '请核对输入的写法', correct: '本次输入核对通过',
};

const LivePretest: React.FC<{
  studentId: string; nodeId: string; indicatorId: string;
  diagnosis: OfficialDiagnosis;
  openSignal?: number;
  hideCard?: boolean;
  onEvidence?: (entries: LiveEvidenceEntry[]) => void;
}> = ({ studentId, nodeId, indicatorId, diagnosis, openSignal, hideCard, onEvidence }) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const viewedQuestionId = searchParams.get('questionId');
  const [open, setOpen] = useState(false);
  const [ready, setReady] = useState(false);
  const resultRef = useRef<HTMLElement | null>(null);
  const lastSignal = useRef(openSignal ?? 0);
  useEffect(() => {
    if (openSignal != null && openSignal !== lastSignal.current) {
      lastSignal.current = openSignal;
      setOpen(true);
    }
  }, [openSignal]);
  const pretestRequested = searchParams.get('pretest') === '1';
  useEffect(() => {
    if (!pretestRequested) return;
    setOpen(true);
    const next = new URLSearchParams(searchParams);
    next.delete('pretest');
    setSearchParams(next, { replace: true });
  }, [pretestRequested, searchParams, setSearchParams]);
  const [midInput, setMidInput] = useState('');
  const [finalInput, setFinalInput] = useState('');
  const [combinedInput, setCombinedInput] = useState('');
  const [result, setResult] = useState<ReturnType<typeof judgeInputs> | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (result) resultRef.current?.scrollIntoView({ block: 'nearest' });
  }, [result, saved]);
  const [saveError, setSaveError] = useState('');
  const [version, setVersion] = useState(0);
  void version;
  const question = useMemo(() => pickQuestion(indicatorId, diagnosis), [indicatorId, diagnosis]);
  const entries = readLiveEvidence(studentId, nodeId, indicatorId);
  const previous = result && saved && entries.length > 1 ? entries[entries.length - 2] : null;
  const changed = previous ? [
    previous.intermediate !== midInput ? '去括号后' : '',
    (previous.combined || '') !== combinedInput ? '合并后' : '',
    previous.final !== finalInput ? '最终答案' : '',
  ].filter(Boolean) : [];

  if (!question) return hideCard && !open ? null : (
    <Modal open={open} title="同类题练习" onCancel={() => setOpen(false)} footer={<Button onClick={() => setOpen(false)}>返回答卷</Button>}>
      <p>当前考点暂无可在线核对的同类题，请教师自行选题。</p>
    </Modal>
  );

  const submit = () => {
    const nextResult = judgeInputs(midInput, finalInput, question, combinedInput);
    setResult(nextResult);
    setSaveError('');
    setSaved(false);
    const entry: LiveEvidenceEntry = {
      at: new Date().toISOString(), studentId, nodeId, indicatorId, questionId: question.id,
      id: `LIVE-${crypto.randomUUID()}`, intermediate: midInput, combined: combinedInput, final: finalInput,
      kind: nextResult.kind, supports: nextResult.supports, followUp: nextResult.follow,
    };
    try {
      const next = [...entries, entry];
      writeLiveEvidence(studentId, nodeId, indicatorId, next);
      onEvidence?.(next);
      setSaved(true);
      setVersion((v) => v + 1);
    } catch {
      setSaveError('本次结果尚未保存。请允许浏览器本地存储后重新查看结果，再带回讲评方案。');
    }
  };

  const headline = result?.kind === 'combine_wrong'
    ? (result.diffs.some((d) => d.includes('常数')) ? '合并同类项时，常数项需要复查' : '合并同类项时，请复查各项系数')
    : result ? KIND_LABEL[result.kind] : '';
  const clearInputs = () => { setMidInput(''); setCombinedInput(''); setFinalInput(''); setResult(null); setSaved(false); setSaveError(''); };

  return <>
    {!hideCard && <Button onClick={() => setOpen(true)}>做一道同类题</Button>}
    <Modal open={open} onCancel={() => setOpen(false)} afterOpenChange={setReady} width={720} footer={null} className="teacher-practice-modal"
      title={<span>同类题练习 <span className="teacher-muted" style={{ marginLeft: 16 }}>{getStudentName(studentId)}</span></span>}
      styles={{ body: { maxHeight: 'calc(100dvh - 128px)', overflowY: 'auto' } }}>
      <div data-testid="live-pretest" data-question-id={question.id} data-ready={ready && open ? 'true' : 'false'}>
        <div className="teacher-practice-question">
          <span className="teacher-muted">{questionLabel(question.id)}</span>
          <h2>{question.stem}</h2>
          {viewedQuestionId && viewedQuestionId !== question.id && <p className="teacher-muted">本题为{questionLabel(viewedQuestionId)}的同类练习</p>}
        </div>
        <p>请学生做完这道题，再照着作答录入。</p>
        <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="teacher-practice-form">
          <label htmlFor="practice-expanded">去括号后</label>
          <Input id="practice-expanded" aria-label="去括号后" maxLength={512} value={midInput}
            onChange={(e) => { setMidInput(e.target.value); setResult(null); setSaved(false); }} />
          <label htmlFor="practice-combined">合并后（可选）</label>
          <Input id="practice-combined" aria-label="合并后（可选）" maxLength={512} value={combinedInput}
            onChange={(e) => { setCombinedInput(e.target.value); setResult(null); setSaved(false); }} />
          <label htmlFor="practice-final">最终答案</label>
          <Input id="practice-final" aria-label="最终答案" maxLength={512} value={finalInput}
            onChange={(e) => { setFinalInput(e.target.value); setResult(null); setSaved(false); }} />
          <div className="teacher-actions"><Button type="primary" htmlType="submit">查看结果</Button><Button type="text" onClick={clearInputs}>重新输入</Button></div>
        </form>
        <p className="teacher-muted teacher-practice-disclaimer">练习记录，不计入原测验成绩</p>
        {result && <section ref={resultRef} data-testid="pretest-result" className="teacher-practice-result" aria-live="polite">
          <h3>{headline}</h3>
          {result.kind === 'manual' || result.kind === 'final_wrong' ? <p>{result.hint}</p> : null}
          {result.kind === 'correct' && <p className="teacher-muted">{combinedInput.trim() ? '三处输入均等价。' : '未记录合并过程，仅核对了两处输入。'}原有补充观察安排继续保留。</p>}
          {previous && <p className="teacher-muted">{changed.length ? `与上次相比，只改了${changed.join('、')}。` : '与上次输入相同，判断不变。'}</p>}
          {saveError && <Alert type="error" message={saveError} />}
          {saved && <div className="teacher-actions teacher-practice-return-actions">
            <Button type="primary" onClick={() => {
              sessionStorage.setItem('competition.plan.autoRecompute', '1');
              sessionStorage.setItem('competition.plan.practiceReturn', JSON.stringify({ studentId, nodeId, indicatorId, questionId: question.id }));
              setOpen(false);
              navigate(analysisReturnPath(searchParams));
            }}>带回讲评方案</Button>
            <Button onClick={() => {
              const next = new URLSearchParams(searchParams);
              next.set('tab', 'profile');
              next.delete('pretest');
              const latest = entries[entries.length - 1];
              if (latest?.id) next.set('practiceRecord', latest.id);
              // 结果提示留在档案里时，用户可能再切题；把这次练习对应的原题与记录号
              // 一并带上，「查看原答卷」才不会跳到切换后的新题。
              if (viewedQuestionId) {
                next.set('practiceOriginalQuestion', viewedQuestionId);
                const viewedRecord = searchParams.get('recordId');
                if (viewedRecord) next.set('practiceOriginalRecord', viewedRecord);
                else next.delete('practiceOriginalRecord');
              }
              setOpen(false);
              navigate({ pathname: '/student-answers', search: next.toString() });
            }}>返回个人档案</Button>
          </div>}
          <details><summary>查看判断依据</summary><p>{result.hint}</p><p>{result.supports}</p><p>{result.follow}</p>
            <p className="teacher-muted">仅核对线性表达式是否等价，不据此判断稳定掌握；本题{question.done ? '为已做题复练' : '截至本测验未做过'}。</p>
          </details>
        </section>}
        {entries.length > 0 && <details className="teacher-practice-history"><summary>练习记录（{entries.length} 条）</summary>
          {entries.map((e, i) => <p key={e.id || `${e.at}-${i}`}>{questionLabel(e.questionId)} · {KIND_LABEL[e.kind]}<br />{e.intermediate} → {e.combined || '未记录合并过程'} → {e.final}</p>)}
          <Button danger size="small" onClick={() => {
            try { writeLiveEvidence(studentId, nodeId, indicatorId, []); clearInputs(); setVersion((v) => v + 1); onEvidence?.([]); }
            catch { setSaveError('未能清除记录，请检查浏览器本地存储。'); }
          }}>清除本人的练习记录</Button>
        </details>}
      </div>
    </Modal>
  </>;
};

export default LivePretest;
