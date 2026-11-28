import { canUseExternalAi, LOCAL_DATA_NOTICE } from '../shared/externalAi';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Input, Modal, Typography } from 'antd';
import { getToken } from '../../api/authState';
import type { RealAnswer, TextbookPageRef } from '../dataSource';
import { getDatasetId } from '../dataSource';
import { openPageLabel, printedPageLabel } from '../shared/textbookPage';
import { questionLabel } from '../shared/teacherContext';
import './teacherLectureCard.css';

type Draft = { start: string; teach: string; check: string };
type Result = { draft: Draft; evidence_note: string | null };
type SavedCard = Draft & { revision: number; updated_at?: string };
class CardRequestError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}
const emptyDraft: Draft = { start: '', teach: '', check: '' };
const fieldLimit = 350;
const sections = [
  ['start', '从哪里开始讲', '写下要追问的原题或作答。'],
  ['teach', '怎样借教材讲', '写下例题中可借用的讲法。'],
  ['check', '讲完怎么确认', '留一句追问或一个当堂检查。'],
] as const;

async function read<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`/api/competition/${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', 'X-User-Token': getToken() ?? '' },
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) {
    throw new CardRequestError(typeof data?.detail === 'string' ? data.detail : '暂时无法处理讲评卡，请重试。', response.status);
  }
  return data as T;
}

function CardContent({ draft, cite }: { draft: Draft; cite?: () => void }) {
  return <dl>{sections.map(([key, label]) => <div key={key}><dt>{label}{cite && <button type="button" className="source-cite" onClick={cite} aria-label={`查看「${label}」的教材出处`}>出处</button>}</dt><dd>{draft[key]}</dd></div>)}</dl>;
}

export default function ReviewDraft({ questionId, quizId, quizName, classId, answer, page, stem, focusCard, onOpen, back, followUp }: {
  questionId: string;
  quizId: string;
  quizName: string;
  classId?: string;
  answer?: RealAnswer;
  page: TextbookPageRef;
  stem: string;
  focusCard?: boolean;
  onOpen: () => void;
  back?: React.ReactNode;
  followUp?: React.ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result>();
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [loadingSaved, setLoadingSaved] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<SavedCard | null>(null);
  const [reload, setReload] = useState(0);
  const [editor, setEditor] = useState<Draft | null>(null);
  const [preview, setPreview] = useState(false);
  const [notice, setNotice] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const studentId = answer?.studentId;
  const answerId = answer?.id;
  const scope = useMemo(() => ({
    question_id: questionId, quiz_id: quizId, material_id: page.material_id, pdf_page: page.pdf_page,
    dataset: getDatasetId(),
    ...(classId ? { class_id: classId } : {}),
    ...(studentId && answerId ? { student_id: studentId, answer_id: answerId } : {}),
  }), [questionId, quizId, page.material_id, page.pdf_page, classId, studentId, answerId]);

  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams(Object.entries(scope).map(([key, value]) => [key, String(value)]));
    read<{ card: SavedCard | null }>(`lecture-card?${query}`, { method: 'GET', signal: controller.signal })
      .then(({ card }) => { if (!controller.signal.aborted) setSaved(card); })
      .catch((err: Error) => { if (!controller.signal.aborted) setLoadError(err.message); })
      .finally(() => { if (!controller.signal.aborted) setLoadingSaved(false); });
    return () => controller.abort();
  }, [scope, reload]);

  useEffect(() => {
    if (!focusCard) return;
    const frame = requestAnimationFrame(() => {
      heading.current?.scrollIntoView({ block: 'start' });
      heading.current?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [focusCard]);

  const externalAi = canUseExternalAi();

  async function generate() {
    if (!externalAi) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const data = await read<Result>('review-draft', {
        method: 'POST',
        body: JSON.stringify(scope),
      });
      setResult(data);
      setNotice('AI 讲评草稿已生成，请核对后使用。');
    } catch (err) {
      setError(err instanceof Error ? err.message : '暂时无法生成讲评');
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!editor || !sections.every(([key]) => editor[key].trim())) return;
    setSaving(true);
    setError('');
    setConflict(false);
    setNotice('');
    const card = { start: editor.start.trim(), teach: editor.teach.trim(), check: editor.check.trim() };
    try {
      const data = await read<{ card: SavedCard }>('lecture-card', {
        method: 'PUT', body: JSON.stringify({ ...scope, card, revision: saved?.revision ?? 0 }),
      });
      setSaved(data.card);
      setEditor(null);
      setResult(undefined);
      setNotice(`讲评卡已保存到本题（${questionLabel(questionId)}）· 教师私有仅本人可见，在本页「教师讲评卡」随时预览/修改。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '暂时无法保存讲评卡');
      setConflict(err instanceof CardRequestError && err.status === 409);
    } finally {
      setSaving(false);
    }
  }

  const ready = !!editor && sections.every(([key]) => editor[key].trim());
  const activity = busy ? '正在生成讲评草稿，请稍候。' : saving ? '正在保存讲评卡。' : loadingSaved ? '正在读取已保存的讲评卡。' : '';
  const missingSteps = answer && (answer.stepsUnavailable || answer.steps.length === 0);
  const scoring = answer?.scoringPoints.filter((point) => point.earnedScore < point.fullScore)
    .map((point) => ` · ${point.label} ${point.earnedScore}/${point.fullScore}`);
  const reread = () => {
    setLoadingSaved(true);
    setLoadError('');
    setConflict(false);
    setReload((value) => value + 1);
  };

  return <div className="source-draft">
    <h3 className="teacher-lecture-heading" ref={heading} tabIndex={-1}>教师讲评卡</h3>
    <div className="source-actions">
      <Button loading={busy} disabled={!externalAi || !!editor || saving} onClick={() => void generate()}>{result ? '重新生成讲评' : '用这页教材备一段讲评'}</Button>
      {!externalAi && <Typography.Text type="secondary">{LOCAL_DATA_NOTICE}</Typography.Text>}
      {!editor && !saved && <Button type="text" disabled={busy || loadingSaved} onClick={() => setEditor(emptyDraft)}>自己填写</Button>}
      {saved && !editor && <>
        <Button type="primary" onClick={() => setPreview(true)}>预览讲评卡</Button>
        <Button type="text" disabled={busy} onClick={() => setEditor(saved)}>修改讲评卡</Button>
      </>}
      {followUp}
    </div>
    {saved && <p className="teacher-muted teacher-lecture-status">已保存讲评卡 · 本题 {questionLabel(questionId)} · 教师私有讲评卡库（仅本人可见）{saved.updated_at ? ` · ${new Date(saved.updated_at).toLocaleString('zh-CN')}` : ''}</p>}
    {activity && <p role="status" className="teacher-muted">{activity}</p>}
    {notice && <p role="status" className="teacher-result-note">{notice}</p>}
    {loadError && <Alert type="warning" showIcon message={loadError}
      action={<Button size="small" onClick={reread}>重新加载</Button>} />}
    {error && <Alert type="warning" showIcon message={error} action={conflict && <Button size="small" onClick={() => {
      setEditor(null); setError(''); reread();
    }}>退出编辑并重读</Button>} />}
    {result && <section aria-label="AI 讲评草稿" className="source-draft-result">
      <h3>AI 讲评草稿</h3>
      <p className="teacher-muted">{answer ? '依据教材原页和本题答卷生成，请教师核对后使用。' : '依据教材原页和原题生成。当前没有选定学生答卷。'}</p>
      {answer && <p className="source-evidence-note">原评分记录 · {answer.score}/{answer.fullScore} 分{scoring}</p>}
      {result.evidence_note && <p className="source-evidence-note">{result.evidence_note}</p>}
      <CardContent draft={result.draft} cite={onOpen} />
      <div className="source-actions">
        {!editor && <Button type="primary" disabled={busy || loadingSaved} onClick={() => setEditor(result.draft)}>修改并保存为讲评卡</Button>}
        <Button onClick={onOpen}>{openPageLabel(page.printed)}</Button>{back}
      </div>
    </section>}
    {editor && <section aria-label="编辑讲评卡" className="teacher-lecture-editor">
      <h4>编辑讲评卡</h4>
      {sections.map(([key, label, placeholder]) => <label key={key}>{label}
        <Input.TextArea aria-label={label} rows={2} maxLength={fieldLimit} placeholder={placeholder} disabled={saving}
          value={editor[key]} onChange={(event) => setEditor({ ...editor, [key]: event.target.value })} />
      </label>)}
      <div className="source-actions">
        <Button type="primary" disabled={!ready || loadingSaved || !!loadError || conflict} loading={saving} onClick={() => void save()}>确认保存讲评卡</Button>
        <Button disabled={saving} onClick={() => setEditor(null)}>取消</Button>
      </div>
    </section>}
    <Modal title={`教师讲评卡 · ${questionLabel(questionId)}`} open={preview && !!saved} onCancel={() => setPreview(false)}
      footer={<Button onClick={() => setPreview(false)}>关闭预览</Button>} width={620}>
      <div className="teacher-lecture-preview">
        <p className="teacher-muted">{quizName}{classId ? ` · 班级 ${classId}` : ''}{studentId ? ` · 学生 ${studentId}` : ''}</p>
        <p className="teacher-lecture-stem">{stem}</p>
        {answer && <p className="source-evidence-note">原评分记录 · {answer.score}/{answer.fullScore} 分{scoring}
          {missingSteps && ' · 未记录解题步骤'}</p>}
        {saved && <CardContent draft={saved} cite={onOpen} />}
        <div className="source-actions">
          <Button onClick={() => { setPreview(false); onOpen(); }}>{openPageLabel(page.printed)}</Button>
          {back}
          {followUp}
        </div>
        <p className="teacher-muted">{printedPageLabel(page.printed)} · {page.anchor}</p>
      </div>
    </Modal>
  </div>;
}
