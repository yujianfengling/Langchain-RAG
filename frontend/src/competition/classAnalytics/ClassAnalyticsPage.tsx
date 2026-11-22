import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Card, Col, Collapse, Drawer, Empty, Radio, Row, Select, Space, Table, Tag, Tooltip, Typography,
} from 'antd';
import {
  ArrowRightOutlined, BarChartOutlined, CheckCircleOutlined, MinusCircleOutlined,
  WarningOutlined,
} from '@ant-design/icons';
// 班级学情负责人只需改本模块 data.ts；共享名册从 shared/ids 引入
import { CLASSES, TOPICS, QUIZZES, getStudentName } from '../shared/ids';
import { listKnowledgePoints, getKpStat } from './data';
import { getQuestion, listAnswersByQuestion } from '../studentAnswers/data';
import { getSourceByQuestion } from '../curriculum/data';
import type { KnowledgePoint, KpClassStat, StudentAnswer, TextbookSource } from '../shared/types';
import TextbookDrawer from '../shared/TextbookDrawer';
import ReviewPlanPanel from '../reviewPlan/ReviewPlanPanel';
import BarCompare, { type BarRow } from './BarCompare';
import { getBundle, isRealData } from '../dataSource';
import type { AnomalyRecord, CaseEntry, RealAnswer } from '../dataSource';

const { Title, Text, Paragraph } = Typography;

/** 状态徽标：本次未测 / 记录不足 / 真实零分 / 正常 要能一眼区分 */
const StatusTag: React.FC<{ stat: KpClassStat }> = ({ stat }) => {
  switch (stat.status) {
    case '本次未测':
      return <Tag icon={<MinusCircleOutlined />} color="default">本次未测</Tag>;
    case '记录不足':
      return <Tooltip title={`仅 ${stat.validAttempts} 条有效作答，不足以判定`}>
        <Tag color="default" style={{ color: '#9ca3af' }}>记录不足</Tag>
      </Tooltip>;
    case '真实零分':
      return <Tooltip title={`${stat.validAttempts} 条有效作答，得分率确为 0`}>
        <Tag icon={<WarningOutlined />} color="red">真实零分</Tag>
      </Tooltip>;
    default:
      return <Tag icon={<CheckCircleOutlined />} color="green">有效</Tag>;
  }
};

/** 口径切换：全部有效作答（默认） / 仅比较两班共同题目 */
type CompareScope = 'all' | 'common';

/** 共同口径汇总（全部由 itemBreakdown 现算，不写死任何数字） */
interface CommonScopeSummary { itemIds: string[]; score: number; full: number; events: number; }

/** 共同题目 = 所选测验+考点下两班 itemBreakdown 的 item_id 交集 */
function getCommonItemIds(statA?: KpClassStat | null, statB?: KpClassStat | null): string[] {
  const bIds = new Set((statB?.itemBreakdown ?? []).map((i) => i.item_id));
  return (statA?.itemBreakdown ?? []).filter((i) => bIds.has(i.item_id)).map((i) => i.item_id);
}

/** 按题目集合对某班 itemBreakdown 求和：score 和 / full_score 和 / event_count 和 */
function sumBreakdown(stat: KpClassStat | null | undefined, itemIds: string[]): CommonScopeSummary {
  const idSet = new Set(itemIds);
  const rows = (stat?.itemBreakdown ?? []).filter((i) => idSet.has(i.item_id));
  return {
    itemIds: rows.map((r) => r.item_id),
    score: rows.reduce((s, r) => s + r.score, 0),
    full: rows.reduce((s, r) => s + r.full_score, 0),
    events: rows.reduce((s, r) => s + r.event_count, 0),
  };
}

/** 得分率单元格：区分 null 状态；共同口径下按交集题现算 */
const RateCell: React.FC<{
  stat: KpClassStat | undefined; color: string;
  scope?: CompareScope; common?: CommonScopeSummary | null;
}> = ({ stat, color, scope = 'all', common }) => {
  if (!stat) return <Text type="secondary">—</Text>;
  if (scope === 'common') {
    if (!common || common.itemIds.length === 0) {
      return <Text type="secondary" style={{ color: '#9ca3af' }}>无法同题比较</Text>;
    }
    const rate = common.full > 0 ? common.score / common.full : 0;
    return (
      <Space direction="vertical" size={0}>
        <Text strong style={{ color, fontSize: 16 }}>{Math.round(rate * 100)}%</Text>
        <Text type="secondary" style={{ fontSize: 11 }}>
          {common.events} 条有效作答 · 共同 {common.itemIds.length} 题
        </Text>
      </Space>
    );
  }
  if (stat.status === '本次未测') return <Text type="secondary" style={{ color: '#9ca3af' }}>本次未测</Text>;
  if (stat.status === '记录不足') return <Text type="secondary" style={{ color: '#9ca3af' }}>记录不足（{stat.validAttempts} 条）</Text>;
  if (stat.status === '真实零分') return <Text strong style={{ color: '#dc2626' }}>0%（{stat.validAttempts} 条有效）</Text>;
  return (
    <Space direction="vertical" size={0}>
      <Text strong style={{ color, fontSize: 16 }}>{Math.round((stat.scoreRate ?? 0) * 100)}%</Text>
      <Text type="secondary" style={{ fontSize: 11 }}>{stat.validAttempts} 条有效作答</Text>
    </Space>
  );
};

/**
 * 班级学情 · 讲评工作台（比赛版首页）
 * 老师路线：选测验和班级 → 图表看出值得关注的考点 → 点柱条查本班失分 →
 * 看题目/学生答卷 → 右侧定讲评题与补测 → 补测结果带回方案确认。
 * 图表与明细表共用同一数据来源与口径；选择跟随 URL，返回时恢复位置。
 */
const ClassAnalyticsPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const quizzes = useMemo(() => QUIZZES, []);
  // 挂载时从 URL 恢复一次（useState 惰性初始化只执行一次），之后的 URL 同步不会覆盖用户操作。
  const [quizId, setQuizId] = useState(() => {
    const urlQuiz = searchParams.get('quizId');
    return urlQuiz && QUIZZES.some((q) => q.id === urlQuiz) ? urlQuiz : quizzes[0]?.id || '';
  });
  const [topicId, setTopicId] = useState<string>(() => searchParams.get('topic') || 'all');
  // 口径切换：默认「各班全部有效作答」；演示模式（无 itemBreakdown）隐藏切换、只用默认口径
  const [scope, setScope] = useState<CompareScope>(() => (searchParams.get('scope') === 'common' ? 'common' : 'all'));
  const [view, setView] = useState<'chart' | 'table'>('chart');
  const [classA, setClassA] = useState(() => {
    const urlClass = searchParams.get('classId');
    return urlClass && CLASSES.some((c) => c.id === urlClass) ? urlClass : CLASSES[0].id;
  });
  const [classB, setClassB] = useState(() => {
    const defaultB = CLASSES[1]?.id || CLASSES[0].id;
    // 恢复的 classA 若正好是默认班级 B，交换为默认班级 A，避免两班相同
    return classA === defaultB ? CLASSES[0].id : defaultB;
  });
  const [selected, setSelected] = useState<{ kp: KnowledgePoint; classId: string } | null>(() => {
    const urlKp = searchParams.get('kpId');
    if (!urlKp) return null;
    // kpId 可能是指标号（T01I2）或复合 id（N01-T01I2）
    const kp = listKnowledgePoints(undefined, quizId).find(
      (k) => (k as KnowledgePoint & { indicatorId?: string }).indicatorId === urlKp || k.id === urlKp,
    );
    return kp ? { kp, classId: classA } : null;
  });
  const [textbook, setTextbook] = useState<TextbookSource | null>(null);
  const [showRest, setShowRest] = useState(false);
  // 抽屉只在显式点柱条/考点名时打开；从 URL 恢复的考点仅作上下文（否则返回班级页时抽屉挡住讲评台）

  // 用户切换测验/班级/主题时把筛选同步到 URL（replace，不产生历史记录），便于复制链接。
  // 用 ref 读取最新 searchParams，保留 source 等其他参数，且不因 URL 变化重跑本 effect。
  const searchParamsRef = useRef(searchParams);
  searchParamsRef.current = searchParams;
  useEffect(() => {
    const next = new URLSearchParams(searchParamsRef.current);
    next.set('quizId', quizId);
    next.set('classId', classA);
    if (topicId !== 'all') next.set('topic', topicId); else next.delete('topic');
    if (scope !== 'all') next.set('scope', scope); else next.delete('scope');
    const kpKey = selected
      ? ((selected.kp as KnowledgePoint & { indicatorId?: string }).indicatorId || selected.kp.id)
      : '';
    if (kpKey) next.set('kpId', kpKey); else next.delete('kpId');
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quizId, classA, topicId, scope, selected]);

  // 返回时恢复阅读位置：按完整 URL 存取 .comp-main 滚动位置
  const urlKey = `${location.pathname}?${searchParams.toString()}`;
  useEffect(() => {
    const sc = document.querySelector('.comp-main');
    if (!sc) return;
    const onScroll = () => {
      try { sessionStorage.setItem('competition.scroll:' + urlKey, String(sc.scrollTop)); } catch { /* 隐私模式忽略 */ }
    };
    sc.addEventListener('scroll', onScroll, { passive: true });
    return () => sc.removeEventListener('scroll', onScroll);
  }, [urlKey]);
  const restoredKey = useRef('');
  useEffect(() => {
    if (restoredKey.current === urlKey) return;
    restoredKey.current = urlKey;
    const t = setTimeout(() => {
      const v = sessionStorage.getItem('competition.scroll:' + urlKey);
      const sc = document.querySelector('.comp-main');
      if (v && sc) sc.scrollTo({ top: Number(v) });
    }, 350);
    return () => clearTimeout(t);
  }, [urlKey]);

  /** 切换测验：选中考点不属于新测验时清空，避免跨测验残留 */
  const handleQuizChange = (id: string) => {
    setQuizId(id);
    setSelected((prev) => {
      if (!prev) return prev;
      return listKnowledgePoints(undefined, id).some((k) => k.id === prev.kp.id) ? prev : null;
    });
  };

  const allKps = useMemo(
    () => listKnowledgePoints(topicId === 'all' ? undefined : topicId, quizId),
    [topicId, quizId],
  );

  // 只有官方/真实 bundle 带 itemBreakdown 时才提供图表与共同题目口径
  const hasBreakdown = useMemo(
    () => allKps.some((kp) => kp.stats.some((s) => (s.itemBreakdown?.length ?? 0) > 0)),
    [allKps],
  );
  const effectiveScope: CompareScope = hasBreakdown ? scope : 'all';
  useEffect(() => { if (!hasBreakdown) setView('table'); }, [hasBreakdown]);

  /** 首屏结论：两班差距最大的考点。 */
  const topGap = useMemo(() => {
    let best: { kp: KnowledgePoint; name: string; short: string; gap: number; a: number; b: number } | null = null;
    for (const kp of allKps) {
      const a = kp.stats.find((s) => s.classId === classA);
      const b = kp.stats.find((s) => s.classId === classB);
      if (!a || !b || a.scoreRate == null || b.scoreRate == null) continue;
      const gap = Math.abs(a.scoreRate - b.scoreRate);
      if (!best || gap > best.gap) best = { kp, name: kp.name, short: (kp as KnowledgePoint & { indicatorId?: string }).indicatorId || '', gap, a: a.scoreRate, b: b.scoreRate };
    }
    return best;
  }, [allKps, classA, classB]);

  /** 官方案例册的三名代表性学生；演示样例没有案例册时为空。 */
  const caseEntries = useMemo(() => getBundle()?.caseBook?.cases ?? [], []);
  const goCase = (c: CaseEntry) => {
    const bundle = getBundle();
    const fromFinding = c.question_findings[0]?.item_id;
    // 记录不足等没有本题发现的案例：优先落在该生在此考点实际作答过的题，
    // 让老师先看到真实证据；确实没有作答时再退到课标首题（页面会如实显示状态）。
    const answered = bundle?.answers.find((a) => a.studentId === c.student_id && a.quizId === c.node_id
      && bundle.questions.find((q) => q.id === a.questionId)?.kpId === c.indicator_id)?.questionId;
    const fromMap = bundle?.curriculumMap?.indicators
      .find((x) => x.indicator_id === c.indicator_id)?.questions[0]?.material_id;
    const qid = fromFinding || answered || fromMap || '';
    const cls = bundle?.students.find((s) => s.id === c.student_id)?.classId || classA;
    const sp = new URLSearchParams({
      studentId: c.student_id, questionId: qid, quizId: c.node_id,
      classId: cls, kpId: c.indicator_id,
    });
    const source = searchParamsRef.current.get('source');
    if (source) sp.set('source', source);
    navigate({ pathname: '/student-answers', search: sp.toString() });
  };

  const columns = [
    {
      title: '考点', dataIndex: 'name', width: '32%',
      render: (_: unknown, kp: KnowledgePoint) => (
        <Space direction="vertical" size={2}>
          <Text strong>{kp.name}</Text>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {TOPICS.find((t) => t.id === kp.topicId)?.name}
          </Text>
        </Space>
      ),
    },
    {
      title: () => <span style={{ color: '#2563eb' }}>{CLASSES.find((c) => c.id === classA)?.name}（讲评）</span>,
      width: '28%',
      render: (_: unknown, kp: KnowledgePoint) => {
        const stat = getKpStat(kp, classA);
        const common = effectiveScope === 'common'
          ? sumBreakdown(stat, getCommonItemIds(stat, getKpStat(kp, classB)))
          : null;
        return (
          <button
            type="button"
            onClick={() => { setSelected({ kp, classId: classA }); setDrawerOpen(true); }}
            style={{ background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left', padding: 4, width: '100%' }}
          >
            <RateCell stat={stat} color="#2563eb" scope={effectiveScope} common={common} />
            {stat && <StatusTag stat={stat} />}
          </button>
        );
      },
    },
    {
      title: () => <span style={{ color: '#0d9488' }}>{CLASSES.find((c) => c.id === classB)?.name}（对照）</span>,
      width: '28%',
      render: (_: unknown, kp: KnowledgePoint) => {
        const stat = getKpStat(kp, classB);
        const common = effectiveScope === 'common'
          ? sumBreakdown(stat, getCommonItemIds(getKpStat(kp, classA), stat))
          : null;
        return (
          <button
            type="button"
            onClick={() => { setSelected({ kp, classId: classB }); setDrawerOpen(true); }}
            style={{ background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left', padding: 4, width: '100%' }}
          >
            <RateCell stat={stat} color="#0d9488" scope={effectiveScope} common={common} />
            {stat && <StatusTag stat={stat} />}
          </button>
        );
      },
    },
  ];

  /** 图表行：与明细表同源（listKnowledgePoints/getKpStat），共同题口径按交集现算 */
  const barRows: BarRow[] = useMemo(() => {
    const sorted = [...allKps].sort((x, y) => {
      const gx = Math.abs((getKpStat(x, classA)?.scoreRate ?? 0) - (getKpStat(x, classB)?.scoreRate ?? 0));
      const gy = Math.abs((getKpStat(y, classA)?.scoreRate ?? 0) - (getKpStat(y, classB)?.scoreRate ?? 0));
      return gy - gx;
    });
    return sorted.map((kp) => {
      const ind = (kp as KnowledgePoint & { indicatorId?: string }).indicatorId || kp.id;
      const mk = (classId: string): BarRow['cells'][string] => {
        const stat = getKpStat(kp, classId);
        if (!stat) return undefined;
        const commonIds = effectiveScope === 'common'
          ? getCommonItemIds(stat, getKpStat(kp, classId === classA ? classB : classA))
          : [];
        const common = effectiveScope === 'common' && commonIds.length
          ? (() => { const s = sumBreakdown(stat, commonIds); return { rate: s.full > 0 ? s.score / s.full : 0, score: s.score, full: s.full, events: s.events, items: s.itemIds.length }; })()
          : null;
        return {
          rate: stat.scoreRate, status: stat.status, validAttempts: stat.validAttempts,
          score: stat.score ?? 0, maxScore: stat.maxScore ?? 0,
          common: effectiveScope === 'common' ? common : null,
        };
      };
      return { id: kp.id, indicatorId: ind, name: kp.name, cells: { [classA]: mk(classA), [classB]: mk(classB) } };
    });
  }, [allKps, classA, classB, effectiveScope]);

  /** 可比较 = 至少一个班有可用读数（含真实零分 0%）；未测/记录不足/无共同题收进「查看其余考点」 */
  const { comparableRows, restRows } = useMemo(() => {
    const usable = (row: BarRow) => [row.cells[classA], row.cells[classB]].some((c) => {
      if (!c) return false;
      if (effectiveScope === 'common') return !!c.common;
      return c.rate != null;
    });
    const cmp: BarRow[] = [];
    const rest: BarRow[] = [];
    for (const row of barRows) (usable(row) ? cmp : rest).push(row);
    return { comparableRows: cmp, restRows: rest };
  }, [barRows, classA, classB, effectiveScope]);

  /** 点柱条/考点名：记录柱位用于关闭抽屉后还焦点 */
  const lastBar = useRef<{ kp: string; classId: string } | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const onBarSelect = (indicatorId: string, classId: string) => {
    const kp = allKps.find((k) => (k as KnowledgePoint & { indicatorId?: string }).indicatorId === indicatorId);
    if (!kp) return;
    lastBar.current = { kp: indicatorId, classId };
    setSelected({ kp, classId });
    setDrawerOpen(true);
  };
  const onKpSelect = (indicatorId: string) => onBarSelect(indicatorId, classA);
  const closeDetail = () => {
    setDrawerOpen(false);
    setSelected(null);
    const bar = lastBar.current;
    if (bar) setTimeout(() => {
      document.querySelector(`[data-kp="${bar.kp}"][data-class="${bar.classId}"]`)
        ?.scrollIntoView({ block: 'center' });
      (document.querySelector(`[data-kp="${bar.kp}"][data-class="${bar.classId}"]`) as HTMLElement | null)?.focus();
    }, 120);
  };

  const selectedStat = selected ? getKpStat(selected.kp, selected.classId) : null;
  const detailQuestions = selectedStat?.topWrongQuestionIds
    .map((qid) => getQuestion(qid)).filter(Boolean) || [];

  // ===== 失分明细：两种口径 + 异常明细 + 对账摘要（全部来自 bundle，现算不写死） =====
  const bundle = getBundle();
  const reconciliation = bundle?.reconciliation;
  const otherClassId = selected ? (selected.classId === classA ? classB : classA) : null;
  const otherStat = selected && otherClassId ? getKpStat(selected.kp, otherClassId) : null;
  const selectedIndicatorId = selected
    ? ((selected.kp as KnowledgePoint & { indicatorId?: string }).indicatorId || '')
    : '';
  // 共同题目交集与本班/对方班的交集汇总
  const panelCommonIds = selected ? getCommonItemIds(selectedStat, otherStat) : [];
  const panelCommon = selected ? sumBreakdown(selectedStat, panelCommonIds) : null;
  const panelHasBreakdown = ((selectedStat?.itemBreakdown?.length ?? 0) + (otherStat?.itemBreakdown?.length ?? 0)) > 0;
  // 差集：仅本班有 / 仅对方班有
  const commonIdSet = new Set(panelCommonIds);
  const onlyMineIds = (selectedStat?.itemBreakdown ?? []).map((i) => i.item_id).filter((id) => !commonIdSet.has(id));
  const onlyOtherIds = (otherStat?.itemBreakdown ?? []).map((i) => i.item_id).filter((id) => !commonIdSet.has(id));
  // 待核异常：匹配选中班级 + 测验 + 考点指标；测验归属通过 events 的 recordId→quizId 判定
  const matchedAnomalies = useMemo<AnomalyRecord[]>(() => {
    const anomalies = bundle?.anomalies;
    if (!anomalies?.length || !selected || !selectedIndicatorId) return [];
    const eventQuiz = new Map((bundle?.events ?? []).map((e) => [e.recordId, e.quizId]));
    return anomalies.filter((a) => {
      if (a.class_id !== selected.classId || a.indicator_id !== selectedIndicatorId) return false;
      const evQuiz = eventQuiz.get(a.record_id);
      return evQuiz == null || evQuiz === quizId;
    });
  }, [bundle, selected, selectedIndicatorId, quizId]);

  // 讲评方案的主题：只在显式动作时改变（主题筛选 / 失分明细里点「安排讲评」/ 工作台内换主题），
  // 浏览柱条不翻转方案（盲测发现选考点会连带换掉讲评主题，收起后又弹回，老师体感是页面自己在动）。
  const [planThemeOverride, setPlanThemeOverride] = useState<string | null>(null);
  useEffect(() => { setPlanThemeOverride(null); }, [quizId]);
  const planTheme = useMemo(() => {
    if (planThemeOverride) return planThemeOverride;
    if (topicId !== 'all') return topicId;
    return bundle?.reviewPlans?.theme || 'T01';
  }, [planThemeOverride, topicId, bundle]);

  const goStudent = (a: StudentAnswer, questionId: string) => {
    // 把测验/班级/考点/题目一起带到答卷页；同节点多条独立作答用 recordId 区分
    const params = new URLSearchParams({
      studentId: a.studentId, questionId,
      quizId, classId: selected?.classId || '',
      kpId: (selected?.kp as KnowledgePoint & { indicatorId?: string })?.indicatorId || selected?.kp.id || '',
    });
    const recordId = (a as RealAnswer).sourceRecordId;
    if (recordId) params.set('recordId', recordId);
    const source = searchParamsRef.current.get('source');
    if (source) params.set('source', source);
    navigate({ pathname: '/student-answers', search: params.toString() });
  };

  return (
    <div className="competition-page" style={{ paddingTop: 10 }}>
      {/* 页首：标题 + 测验 + 备课班/对照班（一行放完）；数据来源标识只在顶栏出现一次 */}
      <div className="competition-header" data-testid="class-context"
           style={{ alignItems: 'center', marginBottom: 10, flexWrap: 'wrap', gap: '8px 14px' }}>
        <Title level={3} className="competition-title" style={{ fontSize: 21, marginBottom: 0, marginRight: 4 }}>
          班级学情
        </Title>
        <Space wrap size={8}>
          <Select size="small" aria-label="当前测验" value={quizId} onChange={handleQuizChange} style={{ width: 200 }}
            options={quizzes.map((q) => ({ value: q.id, label: `测验 ${q.name}（${q.date}）` }))} />
          <Text type="secondary" style={{ fontSize: 12 }}>讲评</Text>
          <Select size="small" aria-label="讲评班级" value={classA} onChange={setClassA} style={{ width: 116 }}
            options={CLASSES.map((c) => ({ value: c.id, label: c.name }))} />
          <Text type="secondary" style={{ fontSize: 12 }}>对照</Text>
          <Select size="small" aria-label="对照班级" value={classB} onChange={setClassB} style={{ width: 116 }}
            options={CLASSES.map((c) => ({ value: c.id, label: c.name }))} />
        </Space>
      </div>

      <Row gutter={16} style={{ marginBottom: 10 }}>
        {/* 左区：两班考点对比（图表为主视觉，可切明细表；口径/主题筛选在这里） */}
        <Col xs={24} xl={15} style={{ minWidth: 0 }}>
          <Card size="small" title={<Space><BarChartOutlined />两班考点对比</Space>}
                styles={{ body: { padding: '8px 12px' } }}>
            {topGap && (
              <div className="comp-gap-callout">
                <span className="comp-gap-text">
                  两班差距最大：<b>{topGap.name.replace(/^T\d+I\d+ · /, '')}</b>
                  {' '}{(topGap.a * 100).toFixed(1)}% vs {(topGap.b * 100).toFixed(1)}%
                  （差 {(topGap.gap * 100).toFixed(1)} 个百分点）
                </span>
                <Button size="small" type="link" onClick={() => { setSelected({ kp: topGap.kp, classId: classA }); setDrawerOpen(true); }}>
                  查看依据
                </Button>
              </div>
            )}
            {/* 口径与明细：次级入口收拢（demo 保持展开供回归） */}
            <div className="comp-table-filters">
              <Space wrap size={[10, 4]}>
                <Radio.Group value={topicId} onChange={(e) => setTopicId(e.target.value)}
                  options={[{ label: '全部主题', value: 'all' }, ...TOPICS.map((t) => ({ label: t.name, value: t.id }))]}
                  optionType="button" size="small" />
                {hasBreakdown && (
                  <Radio.Group value={effectiveScope} onChange={(e) => setScope(e.target.value as CompareScope)}
                    options={[
                      { label: '各班全部有效作答', value: 'all' },
                      { label: '仅比较共同题目', value: 'common' },
                    ]}
                    optionType="button" size="small" />
                )}
                {hasBreakdown && view === 'chart' && (
                  <Button size="small" type="text" onClick={() => setView('table')}>看明细表</Button>
                )}
                {!hasBreakdown && view === 'table' && (
                  <Button size="small" type="text" onClick={() => setView('chart')}>看图表</Button>
                )}
              </Space>
            </div>

            {view === 'chart' && hasBreakdown ? (
              <>
                <BarCompare
                  rows={comparableRows}
                  lectureClassId={classA} compareClassId={classB}
                  lectureClassName={CLASSES.find((c) => c.id === classA)?.name || classA}
                  compareClassName={CLASSES.find((c) => c.id === classB)?.name || classB}
                  scope={effectiveScope}
                  selected={selected ? { kpId: selected.kp.id, classId: selected.classId } : null}
                  onSelect={onBarSelect}
                  onSelectKp={onKpSelect}
                />
                {restRows.length > 0 && (
                  <>
                    <Button type="link" size="small" style={{ padding: 0, marginTop: 4 }}
                            onClick={() => setShowRest((v) => !v)}>
                      {showRest ? '收起其余考点' : `查看其余考点（${restRows.length}）· 本次未测或证据不足`}
                    </Button>
                    {showRest && (
                      <BarCompare
                        rows={restRows}
                        lectureClassId={classA} compareClassId={classB}
                        lectureClassName={CLASSES.find((c) => c.id === classA)?.name || classA}
                        compareClassName={CLASSES.find((c) => c.id === classB)?.name || classB}
                        scope={effectiveScope}
                        selected={selected ? { kpId: selected.kp.id, classId: selected.classId } : null}
                        onSelect={onBarSelect}
                        onSelectKp={onKpSelect}
                      />
                    )}
                  </>
                )}
              </>
            ) : allKps.length === 0 ? (
              <Empty description="该主题暂无考点（演示样例）" />
            ) : (
              <Table
                rowKey="id" dataSource={allKps} columns={columns as never}
                pagination={false} size="middle"
                rowClassName={(r) => selected?.kp.id === r.id ? 'competition-row-selected' : ''}
              />
            )}
          </Card>

          {/* 失分明细抽屉：点柱条立即进入视区；关闭后焦点回到原柱条 */}
          <Drawer open={drawerOpen && !!selected} onClose={closeDetail} width={560} destroyOnClose={false}
                  title={selected ? `失分明细 · ${CLASSES.find((c) => c.id === selected.classId)?.name} · ${selected.kp.name.replace(/^T\d+I\d+ · /, '')}` : ''}
                  extra={<Space size={4}>
                    {selected && selected.classId === classA && (
                      <Button size="small" type="primary"
                              onClick={() => {
                                setPlanThemeOverride(selectedIndicatorId.slice(0, 3) || planTheme);
                                setTimeout(() => document.querySelector('[data-testid="review-plan"]')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 150);
                                closeDetail();
                              }}>
                        安排讲评
                      </Button>
                    )}
                  </Space>}>
              {selected && (
              <>
              <Paragraph><Text strong>{selected.kp.name}</Text></Paragraph>
              {/* 全量对账（两班全部测验）：原始行→归并事件→纳入统计的全链路计数 */}
              {isRealData() && reconciliation && (
                <div style={{ marginBottom: 8 }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    全量对账（两班全部测验）：原始行 {reconciliation.raw_rows} · 归并事件 {reconciliation.merged_events} ·
                    原始状态 {Object.entries(reconciliation.raw_status).map(([k, v]) => `${k} ${v}`).join(' / ')} ·
                    实际纳入统计 {reconciliation.included_scored_events} · 隔离 {reconciliation.quarantined_events} ·
                    超范围 {reconciliation.out_of_scope_events}
                  </Text>
                </div>
              )}
              {selectedStat && isRealData() && (
                <Card size="small" style={{ background: '#f6f8fb', marginBottom: 10 }}
                  title={<Space><BarChartOutlined />统计依据</Space>}>
                  <div style={{ marginBottom: 4 }}>
                    <Text strong style={{ fontSize: 12 }}>口径一 · 各班全部有效作答</Text>
                  </div>
                  <Space wrap size={6}>
                    <Tag color="blue">得分和 {selectedStat.score ?? '—'} / 满分和 {selectedStat.maxScore ?? '—'}</Tag>
                    <Tag>得分率 {((selectedStat.scoreRate ?? 0) * 100).toFixed(2)}% = {selectedStat.score ?? '—'}÷{selectedStat.maxScore ?? '—'}</Tag>
                    <Tag>有效作答 {selectedStat.validAttempts} 条</Tag>
                    <Tag>有记录 {selectedStat.observedStudents ?? '—'} / 班级 {selectedStat.classSize ?? '—'} 人</Tag>
                    <Tag>参与题目 {selectedStat.itemCount ?? '—'} 道</Tag>
                  </Space>
                  <div style={{ marginTop: 6 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      本班本考点：待评分 {selectedStat.pendingEvents ?? 0} · 未作答 {selectedStat.unansweredEvents ?? 0}
                      · 分数越界隔离 {selectedStat.quarantinedEvents ?? 0}（不计入得分与分母）
                    </Text>
                  </div>
                  <div style={{ marginTop: 4 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      口径：同节点同指标，按作答事件保留最大修订版本，只取已评分且有效的记录。
                    </Text>
                  </div>
                  {/* 口径二：仅比较共同题目（与口径一同时展示，便于对照） */}
                  {hasBreakdown && (
                    <div style={{ marginTop: 10, borderTop: '1px dashed #e5e7eb', paddingTop: 8 }}>
                      <Text strong style={{ fontSize: 12 }}>口径二 · 仅比较共同题目</Text>
                      {!panelHasBreakdown || panelCommonIds.length === 0 || !panelCommon ? (
                        <div style={{ marginTop: 4 }}>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            两班没有共同的有效作答题目，无法同题比较
                          </Text>
                        </div>
                      ) : (
                        <>
                          <Space wrap size={6} style={{ marginTop: 4 }}>
                            <Tag color="cyan">得分和 {panelCommon.score} / 满分和 {panelCommon.full}</Tag>
                            <Tag>
                              得分率 {(panelCommon.full > 0 ? (panelCommon.score / panelCommon.full) * 100 : 0).toFixed(2)}%
                              {' '}= {panelCommon.score}÷{panelCommon.full}
                            </Tag>
                            <Tag>有效作答 {panelCommon.events} 条</Tag>
                            <Tag>覆盖题目 {panelCommon.itemIds.length} 道</Tag>
                          </Space>
                          <div style={{ marginTop: 4 }}>
                            <Text type="secondary" style={{ fontSize: 11 }}>
                              共同口径以「覆盖题目数」代替人数：itemBreakdown 只有题目级计数，无法按学生去重。
                            </Text>
                          </div>
                          <div style={{ marginTop: 4 }}>
                            <Text type="secondary" style={{ fontSize: 12 }}>共同题目：</Text>
                            {panelCommonIds.map((id) => <Tag key={id}>{id}</Tag>)}
                          </div>
                        </>
                      )}
                      {onlyMineIds.length > 0 && (
                        <div style={{ marginTop: 4 }}>
                          <Text type="secondary" style={{ fontSize: 12 }}>仅本班有：</Text>
                          {onlyMineIds.map((id) => <Tag key={id} color="orange">{id}</Tag>)}
                        </div>
                      )}
                      {onlyOtherIds.length > 0 && (
                        <div style={{ marginTop: 4 }}>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            仅对方班有（{CLASSES.find((c) => c.id === otherClassId)?.name}）：
                          </Text>
                          {onlyOtherIds.map((id) => <Tag key={id} color="purple">{id}</Tag>)}
                        </div>
                      )}
                      <div style={{ marginTop: 6 }}>
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          共同题目对照只统一了题目范围；学生覆盖与作答分布仍可能不同。
                        </Text>
                      </div>
                    </div>
                  )}
                </Card>
              )}
              {/* 待核异常：即使该考点两班都「本次未测」也渲染（forceRender 保证内容始终在 DOM） */}
              {isRealData() && selectedIndicatorId && (
                <Collapse
                  size="small" ghost style={{ marginBottom: 10, background: '#fff7ed' }}
                  items={[{
                    key: 'anomalies',
                    forceRender: true,
                    label: (
                      <Text style={{ fontSize: 12 }}>
                        待核异常（不计入统计）{matchedAnomalies.length > 0 ? ` · ${matchedAnomalies.length} 条` : ''}
                      </Text>
                    ),
                    children: matchedAnomalies.length === 0 ? (
                      <Text type="secondary" style={{ fontSize: 12 }}>该班此考点没有待核异常记录</Text>
                    ) : (
                      <Space direction="vertical" size={6} style={{ width: '100%' }}>
                        {matchedAnomalies.map((a) => (
                          <div key={a.record_id} style={{ fontSize: 12 }}>
                            <Space wrap size={6}>
                              <Text code>{a.record_id}</Text>
                              <Tag color={a.kind === 'quarantined' ? 'red' : 'orange'}>
                                {a.kind === 'quarantined' ? '分数越界·隔离待核' : '超出测验范围·待核'}
                              </Tag>
                              <Text type="secondary">{a.score ?? '—'}/{a.full_score ?? '—'}</Text>
                            </Space>
                            <div><Text type="secondary" style={{ fontSize: 11 }}>{a.reason}</Text></div>
                          </div>
                        ))}
                      </Space>
                    ),
                  }]}
                />
              )}
              {detailQuestions.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该班此考点没有记录到的失分题" />
              ) : (
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  {detailQuestions.map((q) => {
                    const answers = listAnswersByQuestion(q!.id, { quizId, classId: selected.classId });
                    const source = getSourceByQuestion(q!.id);
                    return (
                      <Card size="small" key={q!.id} style={{ background: '#fffbeb' }}>
                        <Space direction="vertical" size={4} style={{ width: '100%' }}>
                          <Text strong>{q!.stem}</Text>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            教材出处：{source ? `${source.docName} 第 ${source.page} 页` : '暂无出处'}
                          </Text>
                          {source && <Button size="small" type="link"
                            onClick={() => setTextbook(source)}>
                            查看教材原页
                          </Button>}
                          <Text type="secondary" style={{ fontSize: 12 }}>相关学生答卷：</Text>
                          {answers.length === 0 ? (
                            <Text type="secondary">（演示样例中该题暂无学生作答明细）</Text>
                          ) : answers.map((a) => (
                            <Button key={a.id} type="link" size="small"
                              icon={<ArrowRightOutlined />}
                              onClick={() => goStudent(a, q!.id)}>
                              {getStudentName(a.studentId)} · 得分 {a.score}/{a.fullScore}
                            </Button>
                          ))}
                        </Space>
                      </Card>
                    );
                  })}
                </Space>
              )}
              {isRealData() && selectedStat && (
                <div style={{ marginTop: 10 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>计入统计的题目：</Text>
                  <div>
                    {selectedStat.topWrongQuestionIds.length > 0
                      ? selectedStat.topWrongQuestionIds.map((qid) => <Tag key={qid}>{qid}</Tag>)
                      : <Text type="secondary">本班该考点未记录到部分得分题</Text>}
                  </div>
                  {selectedStat.observedStudents != null && selectedStat.classSize != null
                    && selectedStat.observedStudents < selectedStat.classSize && (
                    <Alert type="warning" showIcon style={{ marginTop: 8 }}
                      message={`覆盖差异：仅 ${selectedStat.observedStudents}/${selectedStat.classSize} 人有记录`}
                      description="结论只限定在现有记录；未作答或缺记录不按 0 分处理。" />
                  )}
                </div>
              )}
              </>
              )}
            </Drawer>
        </Col>

        {/* 右区：讲评工作台（范围跟当前选择走：选方程考点就是方程的方案） */}
        <Col xs={24} xl={9} style={{ minWidth: 0 }}>
          <ReviewPlanPanel
            quizId={quizId}
            classId={classA}
            planTheme={planTheme}
            onPlanThemeChange={(t) => { setTopicId(t === 'all' ? 'all' : t); }}
            onGoStudent={(sid, qid, ind) => {
              const sp = new URLSearchParams({ studentId: sid, questionId: qid, quizId, classId: classA, kpId: ind });
              const source = searchParamsRef.current.get('source');
              if (source) sp.set('source', source);
              navigate({ pathname: '/student-answers', search: sp.toString() });
            }}
          />
        </Col>
      </Row>

      {/* 三名代表性学生案例（赛题硬要求）：次级紧凑入口 */}
      {isRealData() && caseEntries.length > 0 && (
        <div className="comp-case-strip" data-testid="case-strip">
          <span className="comp-case-strip-label">代表案例</span>
          {caseEntries.map((c) => (
            <div key={`${c.student_id}|${c.indicator_id}`} className="comp-case-mini" role="button" tabIndex={0}
                 onClick={() => goCase(c)}
                 onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') goCase(c); }}>
              <span className={`comp-case-mini-type is-${c.case_name === '表现较好' ? 'good' : c.case_name === '记录不足' ? 'few' : 'partial'}`}>
                {c.case_name}
              </span>
              <strong>{c.student_id}</strong>
              <span className="comp-case-mini-why">
                {(c.focus || c.selection_reason || '').replace(/^讲解重点：/, '').replace(/。.*$/, '')}
              </span>
              <span className="comp-case-mini-go">查看作答 →</span>
            </div>
          ))}
        </div>
      )}

      <TextbookDrawer open={!!textbook} source={textbook} onClose={() => setTextbook(null)} />
    </div>
  );
};

export default ClassAnalyticsPage;
