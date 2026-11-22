import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, Collapse, List, Modal, Radio, Select, Space, Spin, Table, Tag, Typography } from 'antd';
import { CheckOutlined, DeleteOutlined, TeamOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { getBundle } from '../dataSource';
import type { PlanComparison, ReviewPlan, ReviewPlans } from '../dataSource';
import { readLiveEvidence } from '../livePretest/LivePretest';
import { getToken } from '../../api/authState';

const { Text, Paragraph, Title } = Typography;

const APPLIED_KEY = 'competition.reviewplan.applied.v1';

interface Applied { at: string; strategy: string; budget: number; scope?: string; changes: string[]; plan: ReviewPlan }

interface ExportReview {
  files: { name: string; bytes: number }[];
  review: {
    node_id: string; class_id: string; theme: string; budget: number;
    lecture_items: string[]; lecture_assignments: number;
    pretest_items: string[]; pretest_assignments: number;
  };
  consistency: { matches_current: boolean; note: string };
}

const displayReason = (text: string) => text
  .replace(/可定位失分点/g, '可定位失分观察项')
  .replace(/已定位失分点/g, '已覆盖观察项')
  .replace(/未覆盖失分点/g, '未覆盖观察项')
  .replace(/预算外的失分点/g, '预算外的观察项');

function readApplied(): Applied | undefined {
  try { return JSON.parse(localStorage.getItem(APPLIED_KEY) || 'null') || undefined; } catch { return undefined; }
}

/**
 * 讲评工作台（比赛版右区）。
 * 范围跟当前选择走：测验/讲评班级来自页面，主题来自选中考点或主题筛选；
 * bundle 固定方案（N01/DC01/T01）直接复用，其余范围调受控接口现场生成
 * （POST /competition/review-plan/recompute，无现场证据时 new 与 base 相同，取 base 即方案）。
 * 预算 3→2 的影响用前后条形小图表达；只有真的变化才高亮。
 */
const ReviewPlanPanel: React.FC<{
  quizId: string; classId: string; planTheme: string;
  onPlanThemeChange: (themeId: string) => void;
  onGoStudent: (studentId: string, questionId: string, indicator: string) => void;
}> = ({ quizId, classId, planTheme, onPlanThemeChange, onGoStudent }) => {
  const bundle = getBundle();
  const rp = bundle?.reviewPlans;
  const topics = bundle?.topics || [];
  const themeName = topics.find((t) => t.id === planTheme)?.name || planTheme;
  // 预算/策略跨页保持（补测「带回讲评方案」跳回时不被重置；会话级）
  const PLAN_UI_KEY = 'competition.plan.ui.v1';
  const readPlanUi = (): { budget: number; strategy: 'common' | 'pretest' } => {
    try {
      const v = JSON.parse(sessionStorage.getItem(PLAN_UI_KEY) || 'null');
      if (v && (v.budget === 2 || v.budget === 3) && (v.strategy === 'common' || v.strategy === 'pretest')) return v;
    } catch { /* 忽略 */ }
    return { budget: 3, strategy: 'common' as const };
  };
  const initUi = readPlanUi();
  const [strategy, setStrategy] = useState<'common' | 'pretest'>(initUi.strategy);
  const [budget, setBudget] = useState<number>(initUi.budget);
  const [budgetOpen, setBudgetOpen] = useState(false);
  useEffect(() => {
    try { sessionStorage.setItem(PLAN_UI_KEY, JSON.stringify({ budget, strategy })); } catch { /* 忽略 */ }
  }, [budget, strategy]);
  const [live, setLive] = useState<{ key: string; data: ReviewPlans } | null>(null);
  const [liveLoading, setLiveLoading] = useState(false);
  const [liveError, setLiveError] = useState('');
  const [destOpen, setDestOpen] = useState(false);
  const [openSteps, setOpenSteps] = useState<Set<string>>(new Set());
  const [recomputed, setRecomputed] = useState<{ base: ReviewPlan; new: ReviewPlan; changes: string[]; audit: PlanComparison } | null>(null);
  const [applied, setApplied] = useState<Applied | undefined>(readApplied());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [exportOpen, setExportOpen] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState('');
  const [exportReview, setExportReview] = useState<ExportReview | null>(null);

  const scopeKey = `${quizId}|${classId}|${planTheme}`;
  const isBundledScope = !!rp && quizId === rp.node_id && classId === rp.class_id && planTheme === rp.theme;

  const collectLive = () => {
    const out: { student_id: string; item_id: string; kind: string; record_id: string }[] = [];
    // 现场证据按「学生×节点×指标」存键：收集该主题下全部指标的现场记录
    const inds = (bundle?.curriculumMap?.indicators ?? [])
      .filter((x) => x.theme_id === planTheme).map((x) => x.indicator_id);
    for (const s of bundle?.students || []) {
      if (s.classId !== classId) continue;
      for (const ind of inds) {
        for (const e of readLiveEvidence(s.id, quizId, ind)) {
          out.push({ student_id: s.id, item_id: e.questionId, kind: e.kind, record_id: e.id || `现场-${e.at}` });
        }
      }
    }
    return out;
  };

  /** 现场生成/重算：plans=完整方案结构（各策略×预算），base/new=当前策略预算下的单个方案 */
  const fetchPlan = async (withEvidence: boolean) => {
    const token = getToken();
    const res = await fetch('/api/competition/review-plan/recompute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { 'X-User-Token': token } : {}) },
      body: JSON.stringify({
        node_id: quizId, class_id: classId, theme: planTheme, budget, strategy,
        extra_evidence: withEvidence ? collectLive() : [],
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    return res.json() as Promise<{ plans: ReviewPlans; new_plans: ReviewPlans; base: ReviewPlan; new: ReviewPlan; changes: string[]; audit: PlanComparison }>;
  };

  const scopeKeyRef = useRef(scopeKey);
  scopeKeyRef.current = scopeKey;
  // 范围不在 bundle 固定方案内时现场生成一次（换范围即换方案）
  useEffect(() => {
    if (isBundledScope || !rp) return;
    if (live && live.key === scopeKey) return;
    let cancelled = false;
    setLiveLoading(true);
    setLiveError('');
    fetchPlan(false)
      .then((payload) => { if (!cancelled) setLive({ key: scopeKeyRef.current, data: payload.plans }); })
      .catch((e: unknown) => { if (!cancelled) setLiveError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLiveLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey, isBundledScope]);

  // 「带回讲评方案」：补测页提交后设置的标记，回到这里自动用现场证据重算一次
  const autoRan = useRef('');
  useEffect(() => {
    if (sessionStorage.getItem('competition.plan.autoRecompute') !== '1') return;
    if (autoRan.current === scopeKey) return;
    autoRan.current = scopeKey;
    sessionStorage.removeItem('competition.plan.autoRecompute');
    if (collectLive().length === 0) return;
    setTimeout(() => { void recompute(); document.querySelector('[data-testid="review-plan"]')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 400);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey]);

  const sourceData: ReviewPlans | undefined = isBundledScope ? rp : (live?.key === scopeKey ? live.data : undefined);
  const inScope = !!sourceData;
  const usingApplied = inScope && applied?.scope === scopeKey && applied?.strategy === strategy
    && applied?.budget === budget && !!applied?.plan.workload;
  const plan: ReviewPlan | undefined = useMemo(
    () => usingApplied ? applied?.plan : (inScope ? sourceData?.strategies[strategy]?.budgets[String(budget)] : undefined),
    [usingApplied, applied, inScope, sourceData, strategy, budget],
  );

  const reviewExport = async () => {
    setExportOpen(true);
    setExportBusy(true);
    setExportError('');
    setExportReview(null);
    try {
      const token = getToken();
      const res = await fetch('/api/competition/export-review', {
        method: 'GET', cache: 'no-store',
        headers: token ? { 'X-User-Token': token } : {},
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setExportReview(await res.json());
    } catch (e) {
      setExportError(`成果包复核失败：${e instanceof Error ? e.message : '请检查本地服务'}`);
    } finally { setExportBusy(false); }
  };

  async function recompute() {
    // 竞态防护：响应回来时若方案范围已切换，丢弃本次结果（旧数据不得覆盖新方案）
    const myKey = scopeKeyRef.current;
    setBusy(true);
    setError('');
    try {
      const payload = await fetchPlan(true);
      if (scopeKeyRef.current !== myKey) { setBusy(false); return; }
      setLive({ key: myKey, data: payload.plans });
      setRecomputed({ base: payload.base, new: payload.new, changes: payload.changes, audit: payload.audit });
    } catch (e) {
      if (scopeKeyRef.current !== myKey) { setBusy(false); return; }
      setRecomputed(null);
      setError(`重算失败：${e instanceof Error ? e.message : '请检查本地服务'}`);
    } finally { setBusy(false); }
  }

  const studentChips = (studs: string[], item: string, ind: string) => (
    <Space wrap size={4}>
      {studs.map((s) => (
        <Button key={s} size="small" type="link" style={{ padding: 0, height: 'auto' }}
                onClick={() => onGoStudent(s, item, ind)}>{s}</Button>
      ))}
    </Space>
  );

  if (!rp && !liveLoading) return null;
  if (!isBundledScope && liveError) {
    return (
      <Card data-testid="review-plan" size="small" title={<Space><TeamOutlined />讲评工作台</Space>}>
        <Alert type="warning" showIcon
               message={`没能生成 ${quizId} / ${classId} / ${themeName} 的讲评方案：${liveError}`}
               description="可稍后重试，或用下方按钮换一个主题；班级统计与失分明细不受影响。" />
        <Space style={{ marginTop: 8 }}>
          <Button size="small" onClick={() => { setLive(null); setLiveError(''); }}>重试</Button>
          <Button size="small" onClick={() => onPlanThemeChange(rp?.theme || 'T01')}>看 {rp?.node_id} 固定方案</Button>
        </Space>
      </Card>
    );
  }

  const diff = sourceData?.budget_diff_3_to_2[strategy];
  const workload3 = sourceData?.strategies[strategy]?.budgets['3']?.workload;
  const workload2 = sourceData?.strategies[strategy]?.budgets['2']?.workload;
  const cmp = sourceData?.comparison[String(budget)];
  const naiveWorkload = cmp?.naive_plan?.workload;
  const commonWorkload = sourceData?.strategies.common?.budgets[String(budget)]?.workload;
  const choices = strategy === 'common'
    ? (plan?.lecture_items || []).map((x) => ({ itemId: x.item_id, reason: x.rank_reason, students: x.lost_students }))
    : (plan?.pretest_tasks || []).map((x) => ({ itemId: x.item_id, reason: [x.rank_reason, x.observe].filter(Boolean).join('；'), students: x.students }));

  // ===== 预算 3→2 的影响（前后条形小图的数据；全部按实际名单现算） =====
  const plan3 = sourceData?.strategies[strategy]?.budgets['3'];
  const plan2Ref = sourceData?.strategies[strategy]?.budgets['2'];
  const removedIds = diff?.removed_items ?? [];
  const removedRoster = removedIds.flatMap((id) =>
    (plan3?.lecture_items.find((x) => x.item_id === id)?.lost_students
      || plan3?.pretest_tasks.find((x) => x.item_id === id)?.students) ?? []);
  const lowerStudents = new Set([
    ...(plan2Ref?.lecture_items.flatMap((x) => x.lost_students) ?? []),
    ...(plan2Ref?.pretest_tasks.flatMap((t) => t.students) ?? []),
  ]);
  const emptied = diff?.affected_students ?? [];
  const shortWhy = (why: string) => {
    const m = why.match(/失分点 (\d+) 个、失分人数 (\d+)/);
    if (m) return `可定位观察项 ${m[1]} 个 · ${m[2]} 人`;
    const m2 = why.match(/证据不足学生 (\d+) 人/);
    if (m2) return `${m2[1]} 人待观察`;
    return displayReason(why);
  };
  const keptLecture = (diff?.kept ?? []).filter((k) => plan?.lecture_items.some((x) => x.item_id === k.item_id));
  const keptPretest = (diff?.kept ?? []).filter((k) => plan?.pretest_tasks.some((x) => x.item_id === k.item_id));
  const lectureStudents = new Set(plan?.lecture_items.flatMap((x) => x.lost_students) ?? []);
  const pretestStudents = new Set(plan?.pretest_tasks.flatMap((t) => t.students) ?? []);
  /** 每个失去任务学生的去向（按预算 2 的实际名单） */
  const destinationOf = (s: string) => {
    const l2 = (plan2Ref?.lecture_items ?? []).filter((x) => x.lost_students.includes(s)).map((x) => x.item_id);
    const p2g = (plan2Ref?.pretest_tasks ?? []).filter((t) => t.students.includes(s)).map((t) => t.item_id);
    if (!l2.length && !p2g.length) return `${s}：讲评无、补测无——任务清单因此变空，需要教师单独安排`;
    return `${s}：讲评 ${l2.length ? l2.join('、') : '无'}；补测 ${p2g.length ? p2g.join('、') : '无'}`;
  };
  const stillCount = removedRoster.filter((s) => lowerStudents.has(s)).length;
  const impactLine = removedIds.length === 0
    ? '预算 3→2 不改变本题组'
    : emptied.length > 0
      ? `少讲 ${removedIds.join('、')}，${emptied.join('、')} 需要另作安排`
      : `少讲 ${removedIds.join('、')}，${stillCount} 人仍有安排`;
  const impactBars = [
    { key: 'lecture', label: strategy === 'common' ? '讲评任务' : '补测任务',
      before: workload3?.[strategy === 'common' ? 'lecture_assignments' : 'pretest_assignments'] ?? 0,
      after: workload2?.[strategy === 'common' ? 'lecture_assignments' : 'pretest_assignments'] ?? 0 },
    { key: 'pretest', label: strategy === 'common' ? '补测任务' : '讲评任务',
      before: workload3?.[strategy === 'common' ? 'pretest_assignments' : 'lecture_assignments'] ?? 0,
      after: workload2?.[strategy === 'common' ? 'pretest_assignments' : 'lecture_assignments'] ?? 0 },
  ];
  const impactMax = Math.max(1, ...impactBars.flatMap((b) => [b.before, b.after]));

  const ImpactBars = () => (
    <>
      {impactBars.map((b) => {
        const changed = b.before !== b.after;
        return (
          <div key={b.key} className="comp-impact-row">
            <span className="comp-impact-label">{b.label}</span>
            <span className="comp-impact-bars">
              <span className="comp-impact-track"><span className="comp-impact-fill" style={{ width: `${(b.before / impactMax) * 100}%` }} /></span>
              <span className="comp-impact-track"><span className={'comp-impact-fill' + (changed ? ' is-changed' : ' is-same')} style={{ width: `${(b.after / impactMax) * 100}%` }} /></span>
            </span>
            <span className="comp-impact-val">
              {changed ? `${b.before} → ${b.after} 份（少 ${b.before - b.after}）` : `${b.after} 份（不变）`}
            </span>
          </div>
        );
      })}
    </>
  );

  if (!isBundledScope && liveLoading) {
    return (
      <Card data-testid="review-plan" size="small" title={<Space><TeamOutlined />讲评工作台 · {classId} · {themeName}</Space>}>
        <Space role="status"><Spin size="small" /><Text type="secondary">正在按 {quizId} / {classId} / {themeName} 生成讲评方案…</Text></Space>
      </Card>
    );
  }
  if (!inScope) {
    return (
      <Card data-testid="review-plan" size="small" title={<Space><TeamOutlined />讲评工作台 · {classId} · {themeName}</Space>}>
        <Alert type="info" showIcon message="该范围暂无可用的讲评方案"
               description="可能是该测验未考这个主题，或没有可定位的失分记录。可换一个主题，或先看左侧失分明细。" />
      </Card>
    );
  }

  const teachReason = (reason: string) => {
    const m = reason.match(/失分人数 (\d+)/);
    if (m) return `${m[1]} 名学生在这题失分`;
    const m2 = reason.match(/证据不足学生 (\d+) 人/);
    if (m2) return `${m2[1]} 名学生需要在这题补观察`;
    return displayReason(reason);
  };
  const lostPointsOf = (itemId: string) =>
    (plan?.lecture_items ?? []).find((x) => x.item_id === itemId)?.lost_points ?? [];

  return (
    <Card data-testid="review-plan" size="small" style={{ marginBottom: 12 }}
          styles={{ body: { padding: '8px 12px' } }}
          title={<Space><TeamOutlined />讲评 · {classId} · {themeName}</Space>}
          extra={<Space size={6}>
            <Tag>{quizId}</Tag>
            {!isBundledScope && <Tag color="cyan">现场生成</Tag>}
            {usingApplied && applied && <Tag color="green" icon={<CheckOutlined />}>已应用会话方案</Tag>}
          </Space>}>
      {/* 控制行：预算贴着标题直接改；主题可换；重算是主动作 */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', alignItems: 'center', marginBottom: 8 }}>
        <Space wrap size={6}>
          <Text type="secondary" style={{ cursor: 'pointer' }} onClick={() => setBudgetOpen(true)}>
            {strategy === 'common' ? '预算（不含补测）' : '补测预算'}
          </Text>
          <Select aria-label="方案题目预算" size="small" style={{ width: 92 }}
                  open={budgetOpen} onDropdownVisibleChange={setBudgetOpen}
                  value={budget} onChange={(v) => { setBudget(v); setRecomputed(null); }}
                  options={[{ value: 3, label: '3 道' }, { value: 2, label: '2 道' }]} />
          <Text type="secondary">主题</Text>
          <Select size="small" aria-label="方案主题" value={planTheme} onChange={onPlanThemeChange}
                  style={{ width: 150 }} options={topics.map((t) => ({ value: t.id, label: t.name }))} />
        </Space>
        <Space wrap size={6}>
          <Button size="small" icon={<ThunderboltOutlined />} loading={busy} onClick={() => { void recompute(); }}>用现场证据重算</Button>
          {applied && (
            <Button size="small" danger icon={<DeleteOutlined />} onClick={() => {
              localStorage.removeItem(APPLIED_KEY); setApplied(undefined);
            }}>清除会话应用</Button>
          )}
        </Space>
      </div>

      {error && <Alert type="error" showIcon message={error} />}

      {/* 题单：老师最需要的东西放最前 */}
      {plan && (
        <>
          <Title level={5} style={{ fontSize: 14, margin: '2px 0 6px' }}>
            {strategy === 'common' ? '今天讲评题' : '今天补测题'}（{choices.length} 道）
            {budget === 2 && choices.length === 2 && <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>预算 2 道，无第三题</Text>}
          </Title>
          {choices.length === 0 ? (
            <Alert type="info" showIcon
                   message="该范围内没有可安排的题目"
                   description="可能没有可定位的失分记录或证据不足学生；可换个主题，或先看左侧失分明细。" />
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
              {choices.map((x) => {
                const stem = bundle?.questions.find((q) => q.id === x.itemId)?.stem.replace(/\s+/g, ' ').trim() || '暂无题干';
                const lost = lostPointsOf(x.itemId);
                const open = openSteps.has(x.itemId);
                return (
                  <Card key={x.itemId} data-testid={`${strategy === 'common' ? 'lecture' : 'pretest'}-choice-${x.itemId}`}
                    size="small" style={{ flex: '1 1 240px', minWidth: 0 }}
                    styles={{ body: { padding: '8px 10px', fontSize: 12, lineHeight: 1.6 } }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                      <Text strong style={{ color: '#1d4ed8' }}>{x.itemId}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>{teachReason(x.reason)}</Text>
                    </div>
                    <div title={stem} style={{ margin: '3px 0', fontSize: 13 }}>
                      {stem.length > 72 ? `${stem.slice(0, 72)}…` : stem}
                    </div>
                    {strategy === 'common' && lost.length > 0 && (
                      <>
                        <Button type="link" size="small" style={{ padding: 0 }}
                                onClick={() => setOpenSteps((prev) => {
                                  const n = new Set(prev);
                                  if (n.has(x.itemId)) n.delete(x.itemId); else n.add(x.itemId);
                                  return n;
                                })}>
                          {open ? '收起失分步骤' : '看失分步骤'}
                        </Button>
                        {open && (
                          <List size="small" style={{ marginTop: 4 }} header={`失分步骤（${lost.length} 处，点学生进答卷）`}
                                dataSource={lost} renderItem={(p) => (
                            <List.Item style={{ padding: '3px 0' }}>
                              <Text style={{ fontSize: 12 }}>
                                <Button size="small" type="link" style={{ padding: 0, height: 'auto', fontSize: 12 }}
                                        onClick={() => onGoStudent(p.student, x.itemId, (plan.lecture_items.find((l) => l.item_id === x.itemId)
                                          || plan.pretest_tasks.find((t) => t.item_id === x.itemId))?.indicator || (sourceData?.indicators?.[0] ?? ''))}>{p.student}</Button>
                                {' '}· {p.label} · {p.record_id}
                              </Text>
                            </List.Item>
                          )} />
                        )}
                      </>
                    )}
                    {strategy === 'pretest' && (
                      <div style={{ marginTop: 2, lineHeight: 1.5 }}>
                        <Text type="secondary" style={{ fontSize: 11 }}>安排学生：</Text>
                        <Space wrap size={2}>
                          {x.students.map((s) => (
                            <Button key={s} size="small" type="link"
                              style={{ padding: 0, height: 'auto', fontSize: 11 }}
                              onClick={() => onGoStudent(s, x.itemId, (plan.lecture_items.find((l) => l.item_id === x.itemId)
                                || plan.pretest_tasks.find((t) => t.item_id === x.itemId))?.indicator || (sourceData?.indicators?.[0] ?? ''))}>{s}</Button>
                          ))}
                        </Space>
                      </div>
                    )}
                  </Card>
                );
              })}
            </div>
          )}
          {plan.workload && (
            <div data-testid="plan-workload" className="comp-workload">
              {plan.workload.lecture_budget === 0 ? (
                <span>不安排讲评；分组补测 {plan.workload.pretest_questions}/{plan.workload.pretest_budget} 道</span>
              ) : (
                <span>讲评 {plan.workload.lecture_questions}/{plan.workload.lecture_budget} 道 ·
                  另有分组补测 {plan.workload.pretest_questions}/{plan.workload.pretest_budget} 道（不占讲评预算）·
                  去重 {plan.workload.total_unique_questions} 道</span>
              )}
              <span> · 共 <b>{plan.workload.total_assignments} 份学生任务</b>（人题，按实际名单计，同题不同类型各算一份）</span>
            </div>
          )}
          <Space wrap size={[4, 4]} style={{ marginBottom: 4 }}>
            <Tag color="blue">覆盖学生 {plan.covered_students.length}（讲评 {lectureStudents.size} ∪ 补测 {pretestStudents.size} 去重）</Tag>
            <Tag>已覆盖观察项 {plan.covered_lost_points}</Tag>
            <Tag color="orange">未覆盖观察项 {plan.uncovered.lost_points.length}</Tag>
            {plan.coverage_audit && <Tag>观察项总数（分母）{plan.coverage_audit.total_points}</Tag>}
            <Tag>未安排补测学生 {plan.uncovered.insufficient_students.length}</Tag>
            <Tag>观察项单位：学生 × 题 × 评分点</Tag>
          </Space>

          {/* 预算真的改到 2 之后：题单旁给实际结果；默认不显示 */}
          {budget === 2 && (
            <div data-testid="budget-tradeoff" className="comp-impact" style={{ marginTop: 4 }}>
              <div className="comp-impact-note" style={{ marginTop: 0 }}>
                <b>{impactLine}</b>
                {removedIds.length > 0 && (
                  <Button type="link" size="small" style={{ padding: 0, marginLeft: 6 }}
                          onClick={() => setDestOpen((v) => !v)}>
                    {destOpen ? '收起去向' : `看 ${removedRoster.length} 份任务的去向`}
                  </Button>
                )}
              </div>
              {destOpen && removedIds.length > 0 && (
                <>
                  <ImpactBars />
                  <List size="small" dataSource={removedRoster}
                        renderItem={(s) => {
                          const removedItem = removedIds[0];
                          const removedInd = (plan3?.lecture_items ?? []).find((x) => x.item_id === removedItem)?.indicator
                            || (plan3?.pretest_tasks ?? []).find((x) => x.item_id === removedItem)?.indicator || '';
                          return (
                            <List.Item style={{ padding: '3px 0' }}>
                              <Text style={{ fontSize: 12 }}>
                                <Button size="small" type="link" style={{ padding: 0, height: 'auto', fontSize: 12 }}
                                        onClick={() => onGoStudent(s, removedItem, removedInd)}>{s}</Button>
                                {destinationOf(s).slice(s.length)}
                              </Text>
                            </List.Item>
                          );
                        }} />
                  {(keptLecture.length > 0 || keptPretest.length > 0) && (
                    <Text type="secondary" style={{ fontSize: 11.5 }}>
                      保留：{keptLecture.map((k) => `${k.item_id}（${shortWhy(k.why || '')}）`).join('、') || '讲评题无'}
                      {keptPretest.length > 0 && `；补测组 ${keptPretest.map((k) => k.item_id).join('、')} 照旧`}
                    </Text>
                  )}
                  <div className="comp-impact-units">单位：任务份数按人题（学生 × 题）计；覆盖学生为去重人数；观察项 = 学生 × 题 × 评分点。</div>
                </>
              )}
            </div>
          )}
        </>
      )}

      {/* 现场证据重算结果（带回讲评方案后直接定位到这里） */}
      {recomputed && (
        <Card data-testid="plan-recompute" size="small" style={{ marginTop: 10 }} title="带回现场证据后的重算结果">
          <div className="comp-record-line" style={{ marginBottom: 8 }}>
            方案范围：{quizId} · {classId} · {themeName} · {strategy === 'common' ? '优先讲共性失分' : '优先给证据不足学生补测'} · 预算 {budget} 道
          </div>
          <Alert type={recomputed.audit.assignments.changed ? 'warning' : 'success'} showIcon
                 message={recomputed.audit.assignments.changed ? '学生任务实际变化' : '本次补测未改变学生任务'}
                 description={recomputed.audit.explanation} />
          <Table size="small" pagination={false} rowKey="metric" style={{ marginTop: 8 }} dataSource={[
            { metric: '观察项总数（分母）', before: recomputed.audit.before.total_points, after: recomputed.audit.after.total_points },
            { metric: '已覆盖观察项', before: recomputed.audit.before.covered_points, after: recomputed.audit.after.covered_points },
            { metric: '未覆盖观察项', before: recomputed.audit.before.uncovered_points, after: recomputed.audit.after.uncovered_points },
            { metric: '其中官方记录观察', before: recomputed.audit.before.official_points, after: recomputed.audit.after.official_points },
            { metric: '其中现场新增观察', before: recomputed.audit.before.live_points, after: recomputed.audit.after.live_points },
          ]} columns={[{ title: '观察项：学生 × 题 × 评分点，不是得分', dataIndex: 'metric' },
            { title: '重算前', dataIndex: 'before' }, { title: '重算后', dataIndex: 'after' }]} />
          <List size="small" style={{ marginTop: 8 }} header="具体新增／移除的未覆盖观察项"
            dataSource={[
              ...recomputed.audit.added_points.map((p) => ({ ...p, operation: '新增' })),
              ...recomputed.audit.removed_points.map((p) => ({ ...p, operation: '移除' })),
            ]} locale={{ emptyText: '无观察项增减' }}
            renderItem={(p) => <List.Item><Text style={{ fontSize: 12 }}>
              {p.operation} · {p.student} / {p.item} / {p.point_id} · {p.label} · {p.record_id} · {p.live ? '现场输入' : '官方记录'}
            </Text></List.Item>} />
          <Paragraph style={{ marginTop: 8 }}>
            学生任务分配核对：新增 {recomputed.audit.assignments.added.length} 份，
            移除 {recomputed.audit.assignments.removed.length} 份，保留 {recomputed.audit.assignments.unchanged_count} 份。
          </Paragraph>
          {recomputed.audit.assignments.changed && <List size="small" dataSource={[
            ...recomputed.audit.assignments.added.map((a) => ({ ...a, operation: '新增任务' })),
            ...recomputed.audit.assignments.removed.map((a) => ({ ...a, operation: '移除任务' })),
          ]} renderItem={(a) => <List.Item>{a.operation}：{a.student} / {a.item_id} / {a.type === 'lecture' ? '讲评' : '补测'}</List.Item>} />}
          <Space>
            <Button size="small" type="primary" onClick={() => {
              const a: Applied = { at: new Date().toISOString(), strategy, budget, scope: scopeKey, changes: recomputed.changes, plan: recomputed.new };
              localStorage.setItem(APPLIED_KEY, JSON.stringify(a)); setApplied(a);
            }}>确认应用到当前演示会话</Button>
            <Button size="small" onClick={() => setRecomputed(null)}>放弃</Button>
          </Space>
          <div style={{ marginTop: 6 }}><Text type="secondary" style={{ fontSize: 12 }}>
            应用后上方方案、分组任务和观察项计数同步更新；官方答卷、评分与班级统计保持原样。
          </Text></div>
        </Card>
      )}

      {/* 次要操作与口径对照：有明确名称的次级入口 */}
      {plan && (
        <Collapse size="small" style={{ marginTop: 8 }} items={[
          {
            key: 'strategy', label: '策略与复核',
            children: <>
              <Space wrap size={6} style={{ marginBottom: 8 }}>
                <Text type="secondary">选题策略</Text>
                <Radio.Group value={strategy} onChange={(e) => { setStrategy(e.target.value); setRecomputed(null); }}
                             options={[{ label: '优先讲共性失分', value: 'common' }, { label: '优先给证据不足学生补测', value: 'pretest' }]}
                             optionType="button" size="small" />
                <Button size="small" onClick={reviewExport}>成果包复核</Button>
              </Space>
              {(usingApplied || strategy === 'pretest') && (
                <Paragraph type="secondary" style={{ fontSize: 12 }}>此对照不代表当前已应用会话方案或补测优先策略。</Paragraph>
              )}
            </>,
          },
          {
            key: 'rosters', label: '名单与评分点（点学生进答卷）',
            children: <>
              <List size="small" header="讲评名单及评分点" dataSource={plan.lecture_items} renderItem={(x) => (
                <List.Item>
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    <Text strong>{x.item_id} · {x.lost_students.length} 人 · {displayReason(x.rank_reason)}</Text>
                    {studentChips(x.lost_students, x.item_id, x.indicator)}
                    <List size="small" header={`评分点依据（${x.lost_points.length} 观察项）`} dataSource={x.lost_points} renderItem={(p) => (
                      <List.Item><Text style={{ fontSize: 12 }}>{p.student} · {p.point_id} · {p.label} · {p.record_id}</Text></List.Item>)} />
                  </Space>
                </List.Item>)} />
              <List size="small" header="补测观察任务" dataSource={plan.pretest_tasks} renderItem={(t) => (
                <List.Item>
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    <Text strong>{t.item_id} · {t.students.length} 人</Text>
                    <Text type="secondary">{displayReason([t.rank_reason, t.observe].filter(Boolean).join('；'))}</Text>
                    {studentChips(t.students, t.item_id, t.indicator)}
                  </Space>
                </List.Item>)} />
            </>,
          },
          ...(plan.workload ? [
            {
              key: 'groups', label: '补测分组与任务量',
              children: <Table size="small" pagination={false} rowKey="item_id" dataSource={plan.workload.groups}
                columns={[
                  { title: '补测分组（名单可重叠）', dataIndex: 'item_id' },
                  { title: '人数', render: (_, g) => g.students.length },
                  { title: '每生题数', dataIndex: 'questions_per_student' },
                  { title: '实际任务量（人题）', dataIndex: 'assignments' },
                ]} />,
            },
            {
              key: 'loads', label: '每名学生的实际任务清单',
              children: <Table size="small" pagination={false} rowKey="student" dataSource={plan.workload.student_tasks}
                columns={[{ title: '学生', dataIndex: 'student' },
                  { title: '讲评题', render: (_, r) => r.lecture_items.join('、') || '—' },
                  { title: '补测题', render: (_, r) => r.pretest_items.join('、') || '—' },
                  { title: '合计任务', dataIndex: 'task_count' }]} />,
            },
          ] : []),
          {
            key: 'u', label: `未覆盖观察项（${plan.uncovered.lost_points.length} 项 / ${plan.uncovered.insufficient_students.length} 生待安排）`,
            children: <Space direction="vertical" size={4} style={{ width: '100%' }}>
              {plan.uncovered.reasons.map((r) => <Text key={r} type="secondary" style={{ fontSize: 12 }}>{displayReason(r)}</Text>)}
              <List size="small" dataSource={plan.uncovered.lost_points} renderItem={(p) => (
                <List.Item><Text style={{ fontSize: 12 }}>{p.student} · {p.item} · {p.point_id} · {p.label}</Text></List.Item>)} />
              <Text type="secondary" style={{ fontSize: 12 }}>
                证据不足学生：{plan.uncovered.insufficient_students.map((s) => `${s.student}(${s.indicator} ${s.effective}/${s.min})`).join('、') || '无'}
              </Text>
            </Space>,
          },
          ...(cmp ? [{
            key: 'comparison', label: '对照：按失分人数选题 vs 本方案',
            children: <>
              <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 6 }}>
                两列讲评预算同为 {budget} 道，额外补测单列，不是同总预算。观察项按学生 × 题 × 评分点计数，不是得分。
              </Paragraph>
              <Table size="small" pagination={false} rowKey="k"
                dataSource={[
                  { k: '讲评预算（道）', a: naiveWorkload?.lecture_budget ?? '—', b: commonWorkload?.lecture_budget ?? '—' },
                  { k: '实际讲评题数（道）', a: naiveWorkload?.lecture_questions ?? '—', b: commonWorkload?.lecture_questions ?? '—' },
                  { k: '讲评题', a: cmp.naive.lecture_items.join(','), b: cmp.plan.lecture_items.join(',') },
                  { k: '覆盖学生数', a: cmp.naive.covered_students.length, b: cmp.plan.covered_students.length },
                  { k: '已覆盖观察项', a: cmp.naive.covered_lost_points, b: cmp.plan.covered_lost_points },
                  { k: '其中可定位失分观察项', a: cmp.naive.located_points, b: cmp.plan.located_points },
                  { k: '讲评分配（人题）', a: naiveWorkload?.lecture_assignments ?? '—', b: commonWorkload?.lecture_assignments ?? '—' },
                  { k: '额外补测题数（道）', a: naiveWorkload?.pretest_questions ?? '—', b: commonWorkload?.pretest_questions ?? '—' },
                  { k: '额外补测分配（人题）', a: naiveWorkload?.pretest_assignments ?? '—', b: commonWorkload?.pretest_assignments ?? '—' },
                  { k: '总分配量（人题）', a: naiveWorkload?.total_assignments ?? '—', b: commonWorkload?.total_assignments ?? '—' },
                ]}
                columns={[{ title: '指标', dataIndex: 'k' }, { title: '按失分人数', dataIndex: 'a' }, { title: '讲评方案（共性失分）', dataIndex: 'b' }]} />
              <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 6, marginBottom: 0 }}>{displayReason(cmp.tradeoff)}</Paragraph>
            </>,
          }] : []),
        ]} />
      )}
      <Modal title="成果包复核" open={exportOpen} onCancel={() => setExportOpen(false)} width={720}
        footer={<Space>
          <Button loading={exportBusy} onClick={reviewExport}>重新复核</Button>
          <Button onClick={() => setExportOpen(false)}>关闭</Button>
        </Space>}>
        <div data-testid="export-review" aria-busy={exportBusy}>
          {exportBusy && <Space role="status"><Spin size="small" /><Text>正在读取磁盘成果包并复核一致性…</Text></Space>}
          {exportError && <Alert type="error" showIcon message={exportError} />}
          {exportReview && <>
            <Alert type={exportReview.consistency.matches_current ? 'success' : 'warning'} showIcon
              message={exportReview.consistency.matches_current ? '磁盘成果包与当前系统建议一致' : '磁盘成果包与当前系统建议不一致'}
              description={exportReview.consistency.note} />
            <Title level={5} style={{ margin: '12px 0 6px' }}>磁盘文件</Title>
            <Table size="small" pagination={false} rowKey="name" dataSource={exportReview.files}
              columns={[{ title: '文件名', dataIndex: 'name', render: (name: string) => <span style={{ overflowWrap: 'anywhere' }}>{name}</span> },
                { title: '大小（bytes）', dataIndex: 'bytes', width: 130 }]} />
            <Title level={5} style={{ margin: '12px 0 6px' }}>磁盘实际方案摘要</Title>
            <Paragraph style={{ marginBottom: 6 }}>
              节点 {exportReview.review.node_id} / 班级 {exportReview.review.class_id} / 主题 {exportReview.review.theme}；
              讲评预算 {exportReview.review.budget} 道。
            </Paragraph>
            <div>讲评题：{exportReview.review.lecture_items.join('、') || '无'}；讲评分配 {exportReview.review.lecture_assignments} 人题</div>
            <div>补测题：{exportReview.review.pretest_items.join('、') || '无'}；补测分配 {exportReview.review.pretest_assignments} 人题</div>
            <Text type="secondary" style={{ fontSize: 12 }}>以上来自接口读取的磁盘成果包，不以页面预算或会话应用替代。</Text>
          </>}
        </div>
      </Modal>
    </Card>
  );
};

export default ReviewPlanPanel;
