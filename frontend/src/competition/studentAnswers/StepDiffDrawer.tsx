import React, { useEffect, useMemo, useState } from 'react';
import { Button, Drawer, Empty, Tag, Typography, message } from 'antd';
import { BookOutlined, CopyOutlined, DiffOutlined } from '@ant-design/icons';
import { getBundle } from '../dataSource';
import type { RealAnswer } from '../dataSource';
import type { TextbookSource } from '../shared/types';
import { buildDiff, equivalent, hintForRow, splitRefSteps } from './stepDiff';
import './stepDiff.css';

const { Text, Paragraph } = Typography;

const STATUS_LABEL: Record<string, { color: string; label: string }> = {
  match: { color: 'green', label: '等价' },
  diverge: { color: 'red', label: '首个分歧步' },
  missing: { color: 'orange', label: '缺这一步' },
  extra: { color: 'orange', label: '多出一步' },
  after: { color: 'default', label: '受前步影响' },
};

/** 解题步骤 diff：参考解法 vs 学生解答并排对齐，首个分歧步标红；
 *  点任意步看两层提示与教这一步的教材例页。过程缺失时退化为结果级对照，不猜过程。 */
const StepDiffDrawer: React.FC<{
  open: boolean;
  onClose: () => void;
  questionId: string;
  answer: RealAnswer | null;
  onOpenTextbook: (source: TextbookSource) => void;
}> = ({ open, onClose, questionId, answer, onOpenTextbook }) => {
  const bundle = getBundle();
  const question = bundle?.questions.find((q) => q.id === questionId);
  const indicator = bundle?.curriculumMap?.indicators.find((i) => i.indicator_id === question?.kpId);
  const page = indicator?.textbook_pages?.find((p) => p.verified) || indicator?.textbook_pages?.[0];
  const refSteps = useMemo(() => splitRefSteps(question?.referenceAnalysis), [question?.referenceAnalysis]);
  const stuSteps = useMemo(() => (answer?.steps || []).map((s) => s.text), [answer?.steps]);
  const diff = useMemo(() => buildDiff(refSteps, stuSteps), [refSteps, stuSteps]);
  const [sel, setSel] = useState<number | null>(null);
  useEffect(() => { setSel(null); }, [questionId, answer?.id]);
  const activeIdx = sel ?? diff.first;
  const row = activeIdx != null ? diff.rows[activeIdx] : null;
  const hint = row ? hintForRow(row) : null;

  const textbookSource: TextbookSource | null = page ? {
    docId: page.material_id,
    docName: `赛方教材切片 ${page.material_id}`,
    page: page.pdf_page,
    exampleTitle: page.anchor,
    pdfUrl: `/api/competition/official-pdf/${page.material_id}`,
    printedPage: page.printed,
    verified: page.verified,
  } : null;

  const copyHint = async () => {
    if (!hint) return;
    const text = `第一层提示：${hint.focus}\n第二层提示：${hint.next}`;
    try {
      await navigator.clipboard.writeText(text);
      message.success('提示已复制');
    } catch {
      message.info('复制不可用，请手动选择文字');
    }
  };

  return <Drawer open={open} onClose={onClose} width={860} title={<span><DiffOutlined aria-hidden /> 解题步骤 diff · {questionId}</span>}>
    {!question ? <Empty description="题目不在当前数据中" /> : !answer || answer.stepsUnavailable || !stuSteps.length ? (
      <div className="stepdiff-result-only">
        <Paragraph type="secondary">未记录解题步骤，只有结果可对照；过程待补证，不猜测错因。</Paragraph>
        <div className="stepdiff-result-grid">
          <div><Text type="secondary">学生最终填答</Text><div className="stepdiff-cell">{answer?.finalAnswer || '—'}</div></div>
          <div><Text type="secondary">参考答案</Text><div className="stepdiff-cell">{question.referenceAnswer || '—'}</div></div>
        </div>
        <Tag color={answer?.finalAnswer && question.referenceAnswer && equivalent(answer.finalAnswer, question.referenceAnswer) ? 'green' : 'red'}>
          {answer?.finalAnswer && question.referenceAnswer && equivalent(answer.finalAnswer, question.referenceAnswer) ? '结果等价' : '结果不等价或不可解析'}
        </Tag>
        {textbookSource && <Button icon={<BookOutlined />} onClick={() => onOpenTextbook(textbookSource)}>看本考点教材例页</Button>}
      </div>
    ) : (
      <>
        <div className="stepdiff-head">
          <Tag>参考 {diff.refCount} 步</Tag>
          <Tag>学生 {diff.stuCount} 步</Tag>
          {diff.firstKind && <Tag color={diff.firstKind === 'diverge' ? 'red' : 'orange'}>{STATUS_LABEL[diff.firstKind].label}在第 {(diff.rows[diff.first!].stuIdx ?? diff.rows[diff.first!].refIdx ?? 0) + 1} 步</Tag>}
          {!diff.firstKind && <Tag color="green">逐步等价</Tag>}
        </div>
        <div className="stepdiff-cols"><span /><span>参考解法</span><span>学生解答</span></div>
        <div className="stepdiff-rows">
          {diff.rows.map((r, i) => (
            <button type="button" key={r.key}
              className={'stepdiff-row is-' + r.status + (i === diff.first ? ' is-first' : '') + (i === activeIdx ? ' is-active' : '')}
              onClick={() => setSel(i)}>
              <span className="stepdiff-idx">{(r.stuIdx ?? r.refIdx ?? 0) + 1}</span>
              <span className="stepdiff-cell">{r.ref || <Text type="secondary">（参考无对应步）</Text>}</span>
              <span className="stepdiff-cell">{r.stu || <Text type="secondary">（学生未写）</Text>}</span>
              <Tag color={STATUS_LABEL[r.status].color}>{r.status === 'diverge' && i !== diff.first ? '分歧' : STATUS_LABEL[r.status].label}</Tag>
            </button>
          ))}
        </div>
        <Paragraph type="secondary" className="stepdiff-note">首个分歧步之后的步骤受其影响，不单独判定对错；等价写法（如 4a-7b 与 -7b+4a）判对。</Paragraph>
        {row && hint && (
          <div className="stepdiff-hint">
            <div className="stepdiff-hint-head"><strong>第 {(row.stuIdx ?? row.refIdx ?? 0) + 1} 步 · 两层提示</strong>
              <span>
                {textbookSource && <Button size="small" icon={<BookOutlined />} onClick={() => onOpenTextbook(textbookSource)}>看教这一步的教材例页</Button>}
                <Button size="small" icon={<CopyOutlined />} onClick={() => void copyHint()}>复制提示</Button>
              </span>
            </div>
            <p><Text type="secondary">第一层 · 先关注哪一处：</Text>{hint.focus}</p>
            <p><Text type="secondary">第二层 · 再只做哪一步：</Text>{hint.next}</p>
            {row.diffs.length > 0 && <p className="stepdiff-diffs">{row.diffs.join('；')}</p>}
            {textbookSource?.printedPage && <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>教材页为本考点已核验例页（印刷页 {textbookSource.printedPage}），不是逐步映射；请结合该步技能对照使用。</Paragraph>}
          </div>
        )}
      </>
    )}
  </Drawer>;
};

export default StepDiffDrawer;