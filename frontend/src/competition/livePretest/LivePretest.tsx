import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Col, Collapse, Input, List, Modal, Row, Space, Tag, Typography } from 'antd';
import { DeleteOutlined, ExperimentOutlined, RedoOutlined, RollbackOutlined } from '@ant-design/icons';
import { getBundle } from '../dataSource';
import type { Diagnosis as OfficialDiagnosis } from '../dataSource';
import { diffTerms, parseLinear, sameLinear, stripPrefix } from './expr';
import type { Linear } from './expr';

const { Text, Paragraph, Title } = Typography;

/** 现场作答证据：单独保存、单独标注，不写回官方答卷/评分/统计 */
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
  try {
    const all = JSON.parse(localStorage.getItem(STORE_KEY) || '{}') as Record<string, LiveEvidenceEntry[]>;
    if (entries.length) all[scopeKey(s, n, i)] = entries; else delete all[scopeKey(s, n, i)];
    localStorage.setItem(STORE_KEY, JSON.stringify(all));
  } catch { /* 隐私模式不持久化 */ }
}

interface SuitQuestion {
  id: string; stem: string; mid: Linear; final: Linear; done: boolean;
}

/** 选题规则：同指标、整式化简、含括号、参考解析可解析为两个不同规范形；优先截至本测验未做过 */
function pickQuestion(ind: string, d: OfficialDiagnosis): SuitQuestion | undefined {
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
    // 参考的两步必须等价（化简正确）但写法不同（展开式 vs 合并式），才值得两步观察
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
  mid_wrong: '中间变形出错', combine_wrong: '合并同类项有差异',
  transcription_wrong: '过程正确·最终填答不一致',
  final_wrong: '中间正确·填答有误', manual: '待人工核对', correct: '两步正确',
};

/** 判定第一句：老师最该记住的话（技术判定保留在下一行，便于核对口径）。 */
const VERDICT_HEADLINE: Record<LiveEvidenceEntry['kind'], string> = {
  mid_wrong: '去括号这一步需要复查',
  combine_wrong: '合并这一步需要复查',
  transcription_wrong: '合并正确，核对最终填答',
  final_wrong: '最终填答需要核对',
  manual: '超出自动判断范围，待人工核对',
  correct: '现场作答全部等价',
};

/**
 * 现场补测（首版聚焦整式化简）：评委现场输入两步答案，程序判定并给出
 * 提示 / 新增证据 / 后续安排；作答单独标注保存，可清除重来。
 */
const LivePretest: React.FC<{
  studentId: string; nodeId: string; indicatorId: string;
  diagnosis: OfficialDiagnosis;
  /** 主建议卡的「现场补测」按钮递增此值即可直接打开弹层 */
  openSignal?: number;
  /** 只渲染弹层不渲染外层卡（答卷页右栏已有一个补测入口时用，避免双入口） */
  hideCard?: boolean;
  onEvidence?: (entries: LiveEvidenceEntry[]) => void;
}> = ({ studentId, nodeId, indicatorId, diagnosis, openSignal, hideCard, onEvidence }) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  // 老师正在查看的答卷题（可能与系统选出的补测题不同，弹层需写明两者的关系）
  const viewedQuestionId = searchParams.get('questionId') || undefined;
  const [open, setOpen] = useState(false);
  // 主建议卡递增 openSignal 时直接打开（跳过初始渲染的那次）
  const lastSignal = useRef(openSignal ?? 0);
  useEffect(() => {
    if (openSignal != null && openSignal !== lastSignal.current) {
      lastSignal.current = openSignal;
      setOpen(true);
    }
  }, [openSignal]);
  // 导航「现场补测」入口带 pretest=1 时直接打开；同一路由内只改查询参数时
  // 组件不会重挂载，所以必须监听参数本身，用完立即从 URL 移除避免反复打开。
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
  const [version, setVersion] = useState(0);
  void version;

  const question = useMemo(() => pickQuestion(indicatorId, diagnosis), [indicatorId, diagnosis]);
  const entries = readLiveEvidence(studentId, nodeId, indicatorId);
  // 本次提交已写入 entries，取倒数第二条作为「上一次」，只对照真实改动的那一栏。
  const prevSubmitted = result && entries.length > 1 ? entries[entries.length - 2] : null;
  const changed = prevSubmitted ? {
    mid: prevSubmitted.intermediate !== midInput,
    combined: (prevSubmitted.combined || '') !== combinedInput,
    final: prevSubmitted.final !== finalInput,
  } : { mid: false, combined: false, final: false };
  const changedLabels = [
    changed.mid ? '① 去括号后的式子' : '',
    changed.combined ? '② 合并后过程末行' : '',
    changed.final ? '③ 最终填答' : '',
  ].filter(Boolean);

  if (!question) {
    // 无合适补测题也要给可见反馈：导航「现场补测」打开弹层说明，不再无声无息
    return (
      <>
        <Card size="small" title={<Space><ExperimentOutlined />现场补测</Space>}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            该考点暂无适合两步观察的整式化简题（需参考解析可解析为「去括号后」与「最终结果」两个不同式子），待教师补充资源。
          </Text>
          <div style={{ marginTop: 8 }}>
            <Button size="small" onClick={() => setOpen(true)}>查看说明</Button>
          </div>
        </Card>
        <Modal open={open} onCancel={() => setOpen(false)} footer={null} width={640}
               title={<Space><ExperimentOutlined />现场补测<Tag>{studentId} / {nodeId} / {indicatorId}</Tag></Space>}>
          <Alert type="info" showIcon
                 message="该考点暂无适合两步观察的补测题"
                 description={`${studentId} 在 ${nodeId}/${indicatorId} 下没有可自动判定的两步观察题。可换一名学生或其他考点；若有纸质题目，可先线下补测再人工记录。`} />
        </Modal>
      </>
    );
  }

  const submit = () => {
    const r = judgeInputs(midInput, finalInput, question, combinedInput);
    setResult(r);
    const entry: LiveEvidenceEntry = {
      at: new Date().toISOString(), studentId, nodeId, indicatorId, questionId: question.id,
      id: `LIVE-${crypto.randomUUID()}`,
      intermediate: midInput, combined: combinedInput, final: finalInput,
      kind: r.kind, supports: r.supports, followUp: r.follow,
    };
    const next = [...entries, entry];
    writeLiveEvidence(studentId, nodeId, indicatorId, next);
    onEvidence?.(next);
    setVersion((v) => v + 1);
  };

  return (
    <>
      {!hideCard && (
        <Card size="small" title={<Space><ExperimentOutlined />现场补测</Space>}>
          <Paragraph style={{ fontSize: 12, marginBottom: 8 }}>
            {question.id} · 同指标 {indicatorId}；官方成绩不变。
          </Paragraph>
          <Button type="primary" size="small" onClick={() => setOpen(true)}>开始补测</Button>
        </Card>
      )}
      <Modal open={open} onCancel={() => setOpen(false)} width={1100} centered footer={null}
        title={<Space><ExperimentOutlined />现场补测<Tag>{studentId} / {nodeId} / {question.id}</Tag><Tag color="cyan">现场输入·演示证据，非官方成绩</Tag></Space>}
        styles={{ body: { maxHeight: 'calc(100dvh - 160px)', overflowY: 'auto' } }}>
        <div data-testid="live-pretest" data-question-id={question.id}>
          <Title level={4} style={{ margin: '8px 0' }}>{question.id} · {question.stem}</Title>
          {viewedQuestionId && viewedQuestionId !== question.id && (
            <Alert type="info" showIcon style={{ marginBottom: 10 }}
                   message={`你正在查看的答卷题是 ${viewedQuestionId}；系统实际选出的补测题是 ${question.id}`}
                   description="本次补测的作答与记录都记在补测题名下。两题同考点，观察结论适用于同一能力点。" />
          )}
          <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 14 }}>
            为什么选这道题：{question.done ? '复练观察' : '截至本测验未做'}；未填合并末行时，暂不能区分合并错误与填答不一致。只判等价，不猜原因。
            现场记录请以本弹层的题号（{question.id}）为准——系统按考点自动选了最适合两步观察的题。
          </Paragraph>
          <Row gutter={20}>
            <Col xs={24} md={12}>
              <Card size="small" title="输入过程与最终填答" style={{ background: '#f8fafc' }}>
                <Space direction="vertical" size={14} style={{ width: '100%' }}>
                  <div style={{ width: '100%' }} className={changed.mid ? 'comp-field-changed' : ''}>
                    <Text>① 去括号后的式子</Text>
                    <span className="comp-field-help">学生去括号后、未合并的式子。</span>
                    <Input aria-label="去括号后的式子" placeholder="填写不含括号的展开式" maxLength={512}
                      value={midInput} onChange={(e) => { setMidInput(e.target.value); setResult(null); }} />
                  </div>
                  <div style={{ width: '100%' }} className={changed.combined ? 'comp-field-changed' : ''}>
                    <Text>② 合并后过程末行（选填）</Text>
                    <span className="comp-field-help">过程最后一行的合并结果；可留空。</span>
                    <Input aria-label="合并后过程末行" placeholder="记录合并同类项后得到的式子" maxLength={512}
                      value={combinedInput} onChange={(e) => { setCombinedInput(e.target.value); setResult(null); }} />
                  </div>
                  <div style={{ width: '100%' }} className={changed.final ? 'comp-field-changed' : ''}>
                    <Text>③ 最终填答</Text>
                    <span className="comp-field-help">学生最终写进答案栏的式子。</span>
                    <Input aria-label="最终填答" placeholder="填写最终提交的式子" maxLength={512}
                      value={finalInput} onChange={(e) => { setFinalInput(e.target.value); setResult(null); }} />
                  </div>
                  <Space><Button type="primary" onClick={submit}>提交判断</Button>
                    <Button icon={<RedoOutlined />} onClick={() => {
                      setMidInput(''); setCombinedInput(''); setFinalInput(''); setResult(null);
                    }}>重新输入</Button>
                  </Space>
                </Space>
              </Card>
            </Col>
            <Col xs={24} md={12}>
              <div data-testid="pretest-result">
                {result ? (
                  <>
                    <div className="comp-diff-note">
                      {prevSubmitted
                        ? (changedLabels.length === 0
                          ? '三处输入与上一次完全相同，判定和安排不变。'
                          : changedLabels.length === 1 && changed.combined
                            ? '只改了合并末行，去括号与填答没变。'
                            : `这次改了：${changedLabels.join('、')}；其余输入保持一致。`)
                        : '首次提交。只改其中一处再提交一次，这里会写明是哪一个输入带来了不同的后续安排。'}
                    </div>
                    <Alert type={result.kind === 'correct' ? 'success' : result.kind === 'manual' ? 'info' : 'warning'} showIcon
                      message={<>
                        <div className="comp-verdict">
                          {result.kind === 'correct' && combinedInput.trim() ? '三处输入均等价' : VERDICT_HEADLINE[result.kind]}
                        </div>
                        <div className="comp-verdict-sub">
                          判定：{result.kind === 'correct' && combinedInput.trim() ? '三处输入均等价' : KIND_LABEL[result.kind]}
                        </div>
                      </>}
                      description={result.hint} />
                    <div className="comp-pretest-two" style={{ marginTop: 10 }}>
                      <div><Tag color="green">发现</Tag><Text style={{ fontSize: 12.5 }}>{result.supports}</Text></div>
                      <div style={{ marginTop: 4 }}><Tag color="blue">下一步</Tag><Text style={{ fontSize: 12.5 }}>{result.follow}</Text></div>
                      {result.kind === 'correct' && (
                        <div style={{ marginTop: 4 }}><Text type="secondary" style={{ fontSize: 12 }}>{diagnosis.coverage.note}</Text></div>
                      )}
                    </div>
                  </>
                ) : <Alert type="info" showIcon message="等待本次提交" description="提交后这里给出判定与下一步安排；改输入后需重新提交。" />}
              </div>
            </Col>
          </Row>
          {entries.length > 0 && <Collapse size="small" style={{ marginTop: 12 }} items={[{
            key: 'history', label: `现场作答记录（${entries.length} 条，单独保存）`,
            children: <List size="small" dataSource={entries} style={{ maxHeight: 180, overflowY: 'auto' }} renderItem={(e) => (
              <List.Item><Text style={{ fontSize: 12 }}>{e.questionId} · {KIND_LABEL[e.kind]} · 输入[{e.intermediate} / {e.combined || '未记录合并过程'} / {e.final}] · {e.supports}</Text></List.Item>
            )} />,
          }]} />}
          <Space style={{ marginTop: 12 }} wrap>
            <Button onClick={() => setOpen(false)}>关闭补测，返回答卷</Button>
            {entries.length > 0 && (
              <Button icon={<RollbackOutlined />} onClick={() => {
                // 带回讲评方案：回原筛选的班级页，方案区自动用现场证据重算并展示变化
                sessionStorage.setItem('competition.plan.autoRecompute', '1');
                setOpen(false);
                const classId = getBundle()?.students.find((s) => s.id === studentId)?.classId || 'DC01';
                const sp = new URLSearchParams({ quizId: nodeId, classId, kpId: indicatorId });
                const source = new URLSearchParams(window.location.search).get('source');
                if (source) sp.set('source', source);
                navigate({ pathname: '/class-analytics', search: sp.toString() });
              }}>
                带回讲评方案
              </Button>
            )}
            {entries.length > 0 && <Button danger icon={<DeleteOutlined />} onClick={() => {
              writeLiveEvidence(studentId, nodeId, indicatorId, []);
              setMidInput(''); setCombinedInput(''); setFinalInput(''); setResult(null);
              setVersion((v) => v + 1); onEvidence?.([]);
            }}>清除现场作答</Button>}
          </Space>
        </div>
      </Modal>
    </>
  );
};

export default LivePretest;
