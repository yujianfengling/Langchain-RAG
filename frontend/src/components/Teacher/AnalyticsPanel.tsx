import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Col, Collapse, Descriptions, Divider, Empty, List, Modal, Progress,
  Radio, Row, Segmented, Select, Space, Statistic, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  BulbOutlined, DownloadOutlined, FileAddOutlined, ReloadOutlined, SearchOutlined,
  TeamOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import type { EChartsOption } from 'echarts';
import {
  ClassAnalytics, ExplainResult, KnowledgePointDetail, KnowledgePointStat, OptionDistributionItem,
  StudentTierRow, TeacherClass, WrongQuestionStat, describeTeacherError, explainClassAnalytics,
  getClassAnalytics, getKnowledgePointDetail, isForbiddenError, listClasses,
} from '../../api/teacher';
import {
  EmptyBlock, ErrorAlert, LoadingBlock, PermissionAlert, SourceTag, formatTime,
  questionStem, questionTypeColor, questionTypeLabel, ratioText,
} from './common';
import EChart from '../EChart';
import StudentAttemptsDrawer from './StudentAttemptsDrawer';
import type { KnowledgeGraphAction } from '../KnowledgeGraph';

const KnowledgeGraph = React.lazy(() => import('../KnowledgeGraph'));

const { Text, Title, Paragraph } = Typography;

export interface AnalyticsPanelProps {
  courseId: string;
  courseTitle?: string;
  subjectId?: string;
  focusClassId?: string;
  focusKnowledgePointId?: string;
  onBack?: () => void;
  onCreateRemedialAssignment?: (draft: {
    classId?: string;
    knowledgePoints: string[];
    title?: string;
    description?: string;
  }) => void;
  onCreateRemedialLessonPlan?: (draft: {
    classId?: string;
    topic: string;
    objectives?: string;
  }) => void;
}

const DAY_OPTIONS = [
  { label: '近 7 天', value: 7 },
  { label: '近 30 天', value: 30 },
  { label: '近 90 天', value: 90 },
];

const TIER_COLORS: Record<string, string> = {
  needs_attention: 'error',
  consolidating: 'warning',
  on_track: 'success',
  inactive: 'default',
};

function OptionDistributionBars({ items }: { items?: OptionDistributionItem[] }) {
  if (!items || !items.length) return null;
  const totalSelected = items.reduce((s, x) => s + x.count, 0);
  if (totalSelected === 0) return null;
  return (
    <div style={{ marginTop: 6, padding: '6px 10px', background: '#f8fafc', borderRadius: 6 }}>
      <Text type="secondary" style={{ fontSize: 11, display: 'block', marginBottom: 4 }}>
        选项分布（最新一次作答，不含标准答案）：
      </Text>
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        {items.map((opt) => (
          <div key={opt.option_id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Text style={{ width: 22, fontSize: 12, fontWeight: 600 }}>{opt.option_id}.</Text>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                <Text ellipsis style={{ maxWidth: '75%' }}>{opt.text}</Text>
                <Text type="secondary">{opt.count} 人 ({Math.round(opt.ratio * 100)}%)</Text>
              </div>
              <Progress
                percent={Math.round(opt.ratio * 100)}
                showInfo={false}
                size="small"
                strokeColor={opt.ratio >= 0.4 ? '#f59e0b' : '#3b82f6'}
              />
            </div>
          </div>
        ))}
      </Space>
    </div>
  );
}

function buildMarkdownReport(data: ClassAnalytics): string {
  const lines: string[] = [
    `# 班级学情诊断报告：${data.class_name}`,
    '',
    `- 课程：${data.course_title}`,
    `- 统计时间：${data.range.start} ~ ${data.range.end}`,
    `- 生成时间：${data.generated_at}`,
    `- 样本状态：${data.enough_data ? '样本充足' : `样本不足（${data.insufficient_reason || '—'}）`}`,
    '',
    '## 一、核心结论',
    data.headline || '（无）',
    '',
    '## 二、参与与任务完成',
    `- 在班学生：${data.participation?.student_count ?? 0} 人，有作答记录：${data.participation?.answered_student_count ?? 0} 人（参与率 ${ratioText(data.participation?.attempt_student_ratio)}）`,
    `- 作答题次：${data.participation?.attempt_count ?? 0} 次，已判分：${data.participation?.graded_count ?? 0} 次`,
  ];
  if (data.task_completion) {
    lines.push(`- 任务完成：${data.task_completion.completed}/${data.task_completion.total}（${ratioText(data.task_completion.ratio)}）`);
  }
  if ((data.prerequisite_alerts || []).length > 0) {
    lines.push('', '## 三、知识图谱前置薄弱溯因');
    data.prerequisite_alerts!.forEach((a) => {
      lines.push(`- **${a.knowledge_point}**：${a.summary}`);
    });
  }
  if ((data.knowledge_points || []).length > 0) {
    lines.push('', '## 四、知识点作答统计', '| 知识点 | 样本量 | 错误次数 | 错误率 | 涉及学生 | 状态 |', '|---|---|---|---|---|---|');
    data.knowledge_points.forEach((k) => {
      lines.push(`| ${k.knowledge_point} | ${k.attempt_count} | ${k.wrong_count} | ${ratioText(k.wrong_rate)} | ${k.student_count} | ${k.enough_data ? '充足' : '不足'} |`);
    });
  }
  if ((data.error_causes || []).length > 0) {
    lines.push('', '## 五、全班错因分布');
    data.error_causes!.forEach((c) => {
      lines.push(`- ${c.label}：${c.count} 次（${Math.round(c.ratio * 100)}%）`);
    });
  }
  if (data.student_tiers?.students?.length) {
    const need = data.student_tiers.students.filter((s) => s.tier === 'needs_attention');
    lines.push('', '## 六、学生分层概览');
    lines.push(
      `- 需重点干预：${data.student_tiers.counts.needs_attention} 人；`
      + `波动巩固中：${data.student_tiers.counts.consolidating} 人；`
      + `稳步掌握：${data.student_tiers.counts.on_track} 人；`
      + `暂无足够证据：${data.student_tiers.counts.inactive} 人`,
    );
    if (need.length) {
      lines.push(`- 需重点跟进名单：${need.map((s) => `${s.display_name}（错误率 ${ratioText(s.wrong_rate)}）`).join('、')}`);
    }
  }
  return lines.join('\n');
}

/** 班级学情分析：一句话结论 / 班级知识图谱热力 / 风险四象限 / 趋势双轴图 / 错因与选项分布 / 学生分层 / 一键补练与教案 */
const AnalyticsPanel: React.FC<AnalyticsPanelProps> = ({
  courseId, courseTitle, subjectId, focusClassId, focusKnowledgePointId,
  onBack, onCreateRemedialAssignment, onCreateRemedialLessonPlan,
}) => {
  const [classes, setClasses] = useState<TeacherClass[]>([]);
  const [classId, setClassId] = useState<string | undefined>(focusClassId);
  const [days, setDays] = useState(30);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState<string | null>(null);
  const [data, setData] = useState<ClassAnalytics | null>(null);

  const [explaining, setExplaining] = useState(false);
  const [explain, setExplain] = useState<ExplainResult | null>(null);

  const [tierFilter, setTierFilter] = useState<string>('all');
  const [attemptsFor, setAttemptsFor] = useState<{ id: string; name: string } | null>(null);

  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<KnowledgePointDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailKpId, setDetailKpId] = useState<string | null>(null);

  const loadClasses = useCallback(async () => {
    try {
      const res = await listClasses(courseId);
      const list = res?.data?.classes || [];
      setClasses(list);
      setClassId((prev) => (prev && list.some((c) => c.id === prev) ? prev : list[0]?.id));
    } catch (e) {
      const f = describeTeacherError(e, '加载班级失败');
      if (isForbiddenError(e)) setForbidden(f.message);
      else message.error(f.message);
    }
  }, [courseId]);

  const load = useCallback(async (cid?: string, d: number = days) => {
    if (!cid) { setData(null); return; }
    setLoading(true);
    setError(null);
    setForbidden(null);
    setExplain(null);
    try {
      const res = await getClassAnalytics(cid, d);
      setData(res?.data || null);
    } catch (e) {
      const f = describeTeacherError(e, '加载学情失败');
      if (isForbiddenError(e)) setForbidden(f.message);
      else setError(f.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [days]);

  const openDetail = useCallback(async (kpId: string) => {
    if (!classId) return;
    setDetailOpen(true);
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    setDetailKpId(kpId);
    try {
      const res = await getKnowledgePointDetail(classId, kpId, days);
      setDetail(res?.data || null);
    } catch (e) {
      setDetailError(describeTeacherError(e, '加载知识点详情失败').message);
    } finally {
      setDetailLoading(false);
    }
  }, [classId, days]);

  useEffect(() => { loadClasses(); }, [loadClasses]);
  useEffect(() => { load(classId, days); }, [classId, days, load]);
  useEffect(() => {
    if (classId && focusKnowledgePointId) openDetail(focusKnowledgePointId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [classId, focusKnowledgePointId]);

  const handleExplain = async () => {
    if (!classId) return;
    setExplaining(true);
    setExplain(null);
    try {
      const res = await explainClassAnalytics(classId, days);
      setExplain(res?.data || null);
      if (!res?.data?.enough_data) {
        message.info('样本量不足，后端未调用模型');
      }
    } catch (e) {
      message.error(describeTeacherError(e, 'AI 解读失败').message);
    } finally {
      setExplaining(false);
    }
  };

  const handleExportReport = () => {
    if (!data) return;
    const md = buildMarkdownReport(data);
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `学情诊断_${data.class_name}_${data.range.end}.md`;
    a.click();
    URL.revokeObjectURL(url);
    message.success('已导出 Markdown 学情诊断报告');
  };

  const triggerRemedialAssign = useCallback((kps: string[], title?: string, desc?: string) => {
    if (!onCreateRemedialAssignment) return;
    onCreateRemedialAssignment({
      classId,
      knowledgePoints: kps,
      title,
      description: desc,
    });
  }, [classId, onCreateRemedialAssignment]);

  const triggerRemedialPlan = useCallback((topic: string, objectives?: string) => {
    if (!onCreateRemedialLessonPlan) return;
    onCreateRemedialLessonPlan({ classId, topic, objectives });
  }, [classId, onCreateRemedialLessonPlan]);

  const handleGraphAction = useCallback((action: KnowledgeGraphAction) => {
    if (action.type === 'teacher_drill' && action.kpId) {
      void openDetail(action.kpId);
    } else if (action.type === 'teacher_assign') {
      triggerRemedialAssign([action.label], `巩固练习：${action.label}`);
    } else if (action.type === 'teacher_plan') {
      triggerRemedialPlan(`${action.label} 专题讲评与巩固`);
    }
  }, [openDetail, triggerRemedialAssign, triggerRemedialPlan]);

  // ECharts 1：知识点风险四象限散点图（样本量 × 错误率）
  const kpScatterOption = useMemo<EChartsOption>(() => {
    const kps = (data?.knowledge_points || []).filter((k) => k.attempt_count > 0);
    const maxAttempts = Math.max(5, ...kps.map((k) => k.attempt_count));
    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          const item = params.data?.kp as KnowledgePointStat | undefined;
          if (!item) return '';
          return `<b>${item.knowledge_point}</b><br/>`
            + `作答样本：${item.attempt_count} 次（涉及 ${item.student_count} 人）<br/>`
            + `错误率：${ratioText(item.wrong_rate)}（错 ${item.wrong_count} 次）<br/>`
            + `${item.enough_data ? '<span style="color:#059669">样本充足 · 点击下钻</span>' : '<span style="color:#8b5cf6">样本不足，仅供参考</span>'}`;
        },
      },
      grid: { left: 48, right: 24, top: 24, bottom: 38 },
      xAxis: { type: 'value', name: '样本量', min: 0, max: Math.ceil(maxAttempts * 1.15), minInterval: 1 },
      yAxis: { type: 'value', name: '错误率%', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
      series: [{
        type: 'scatter',
        symbolSize: (val: any) => Math.max(14, Math.min(36, 12 + Number(val[2] || 1) * 4)),
        data: kps.map((k) => ({
          name: k.knowledge_point,
          value: [k.attempt_count, Math.round((k.wrong_rate ?? 0) * 100), k.student_count],
          kp: k,
          itemStyle: {
            color: !k.enough_data
              ? '#8b5cf6'
              : (k.wrong_rate ?? 0) >= 0.45 ? '#dc2626'
                : (k.wrong_rate ?? 0) >= 0.25 ? '#f59e0b' : '#10b981',
            opacity: 0.85,
          },
        })),
        label: {
          show: true,
          position: 'top',
          formatter: (p: any) => {
            const name = String(p.data?.name || '');
            return name.length > 8 ? `${name.slice(0, 7)}…` : name;
          },
          fontSize: 11,
          color: '#334155',
        },
      }],
    };
  }, [data?.knowledge_points]);

  // ECharts 2：每日作答量 + 正确率双轴图
  const trendChartOption = useMemo<EChartsOption>(() => {
    const rows = data?.trend || [];
    return {
      tooltip: { trigger: 'axis' },
      legend: { bottom: 0, textStyle: { fontSize: 12 } },
      grid: { left: 42, right: 44, top: 26, bottom: 38 },
      xAxis: { type: 'category', data: rows.map((r) => r.date.slice(5)), axisLabel: { fontSize: 11 } },
      yAxis: [
        { type: 'value', name: '作答次', minInterval: 1 },
        { type: 'value', name: '正确率%', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
      ],
      series: [
        {
          name: '作答次数',
          type: 'bar',
          barMaxWidth: 24,
          itemStyle: { color: '#93c5fd', borderRadius: [3, 3, 0, 0] },
          data: rows.map((r) => r.attempt_count),
        },
        {
          name: '当日正确率%',
          type: 'line',
          yAxisIndex: 1,
          smooth: true,
          symbolSize: 7,
          itemStyle: { color: '#0f766e' },
          lineStyle: { width: 2.5, color: '#0f766e' },
          data: rows.map((r) => (r.wrong_rate == null ? null : Math.round((1 - r.wrong_rate) * 100))),
        },
      ],
    };
  }, [data?.trend]);

  // ECharts 3：全班错因分布条形图
  const errorCauseOption = useMemo<EChartsOption>(() => {
    const causes = data?.error_causes || [];
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 82, right: 32, top: 12, bottom: 20 },
      xAxis: { type: 'value', minInterval: 1 },
      yAxis: { type: 'category', data: causes.map((c) => c.label).reverse(), axisLabel: { fontSize: 12 } },
      series: [{
        type: 'bar',
        barMaxWidth: 20,
        itemStyle: { color: '#f97316', borderRadius: [0, 4, 4, 0] },
        label: {
          show: true,
          position: 'right',
          formatter: (p: any) => {
            const c = causes[causes.length - 1 - p.dataIndex];
            return c ? `${c.count}次 (${Math.round(c.ratio * 100)}%)` : '';
          },
          fontSize: 11,
        },
        data: causes.map((c) => c.count).reverse(),
      }],
    };
  }, [data?.error_causes]);

  const filteredTierStudents = useMemo<StudentTierRow[]>(() => {
    const list = data?.student_tiers?.students || [];
    if (tierFilter === 'all') return list;
    return list.filter((s) => s.tier === tierFilter);
  }, [data?.student_tiers?.students, tierFilter]);

  if (forbidden) return <PermissionAlert message={forbidden} onBack={onBack} />;

  const classOptions = classes.map((c) => ({ value: c.id, label: c.name }));
  const enough = !!data?.enough_data;
  const effectiveSubjectId = data?.subject_id || subjectId || '';
  const weakKpNames = (data?.weak_knowledge_points || []).slice(0, 3).map((k) => k.knowledge_point);

  return (
    <div>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        <Title level={5} style={{ margin: 0 }}>
          学情分析{courseTitle ? ` · ${courseTitle}` : ''}
        </Title>
        <Space wrap>
          <Select
            size="small"
            style={{ width: 200 }}
            placeholder={classes.length ? '选择班级' : '该课程还没有班级'}
            value={classId}
            onChange={(v) => setClassId(v)}
            options={classOptions}
            disabled={classes.length === 0}
          />
          <Radio.Group size="small" value={days} onChange={(e) => setDays(e.target.value as number)}>
            {DAY_OPTIONS.map((o) => (
              <Radio.Button key={o.value} value={o.value}>{o.label}</Radio.Button>
            ))}
          </Radio.Group>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => load(classId, days)} loading={loading}>
            刷新
          </Button>
          <Button size="small" icon={<DownloadOutlined />} disabled={!data} onClick={handleExportReport}>
            导出报告
          </Button>
          <Tooltip title="后端会基于已算出的汇总数字、错因与前置溯因生成教学建议；样本不足时不会调用模型">
            <Button
              size="small"
              type="primary"
              icon={<BulbOutlined />}
              loading={explaining}
              disabled={!classId || !data}
              onClick={handleExplain}
            >
              AI 解读
            </Button>
          </Tooltip>
        </Space>
      </Space>

      {error && <ErrorAlert message={error} onRetry={() => load(classId, days)} />}

      {!classId ? (
        <Card size="small">
          <EmptyBlock description="该课程还没有班级，暂无可分析的学情。请先在「课程与备课 → 班级」中新建班级。" />
        </Card>
      ) : loading ? (
        <LoadingBlock tip="正在统计学情…" />
      ) : !data ? (
        <Card size="small"><Empty description="暂无学情数据" /></Card>
      ) : (
        <>
          {/* 一句话学情结论 + 一键教学行动 */}
          <Card size="small" style={{ marginBottom: 12 }}>
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
                <Space size={8} wrap>
                  <Text strong>{data.class_name}</Text>
                  <Text type="secondary">{data.course_title}</Text>
                  <Tag color="blue">{data.range.start} ~ {data.range.end}</Tag>
                  <Tag color={enough ? 'success' : 'warning'}>
                    {enough ? '样本充足' : '样本不足'}
                  </Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>生成于 {formatTime(data.generated_at)}</Text>
                </Space>
                {enough && weakKpNames.length > 0 && (
                  <Space size={6} wrap>
                    {onCreateRemedialAssignment && (
                      <Button
                        size="small"
                        type="primary"
                        icon={<ThunderboltOutlined />}
                        onClick={() => triggerRemedialAssign(weakKpNames)}
                      >
                        一键布置薄弱点补练
                      </Button>
                    )}
                    {onCreateRemedialLessonPlan && (
                      <Button
                        size="small"
                        icon={<FileAddOutlined />}
                        onClick={() => triggerRemedialPlan(
                          `${weakKpNames.slice(0, 2).join('、')} 讲评与巩固`,
                          `针对本班薄弱点（${weakKpNames.join('、')}）讲评高频错题并安排变式巩固。`,
                        )}
                      >
                        带入下节教案
                      </Button>
                    )}
                  </Space>
                )}
              </Space>
              <Alert
                type={enough ? 'info' : 'warning'}
                showIcon
                message={data.headline || (enough ? '样本量已达标' : '数据不足，仅展示事实计数')}
                description={
                  !enough ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      结论门槛：已判分作答 ≥ {data.thresholds?.min_attempts ?? 5} 次，且参与学生 ≥ {data.thresholds?.min_students ?? 2} 人。
                    </Text>
                  ) : undefined
                }
              />
            </Space>
          </Card>

          {/* 参与与完成：事实性数字 */}
          <Row gutter={[16, 12]} style={{ marginBottom: 12 }}>
            <Col xs={24} lg={12}>
              <Card size="small" title="参与情况" extra={<SourceTag source={data.participation?.source} />}>
                <Space size={24} wrap>
                  <Statistic title="在班学生" value={data.participation?.student_count ?? 0} suffix="人" />
                  <Statistic title="有作答记录" value={data.participation?.answered_student_count ?? 0} suffix="人" />
                  <Statistic title="学生参与率" value={ratioText(data.participation?.attempt_student_ratio)} />
                  <Statistic title="作答题次" value={data.participation?.attempt_count ?? 0} suffix="次" />
                  <Statistic title="已判分" value={data.participation?.graded_count ?? 0} suffix="次" />
                </Space>
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card size="small" title="任务完成情况" extra={<SourceTag source={data.task_completion?.source} />}>
                {data.task_completion ? (
                  <Space size={24} wrap>
                    <Statistic title="任务总数" value={data.task_completion.total} suffix="个" />
                    <Statistic title="已完成" value={data.task_completion.completed} suffix="个" />
                    <Statistic title="完成率" value={ratioText(data.task_completion.ratio)} />
                  </Space>
                ) : (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="统计范围内没有任务数据" />
                )}
              </Card>
            </Col>
          </Row>

          {/* 班级知识图谱热力视图（把知识图谱接入教师核心学情诊断） */}
          {effectiveSubjectId && (
            <div style={{ marginBottom: 12 }}>
              <React.Suspense fallback={<Card size="small"><LoadingBlock tip="加载班级知识图谱…" /></Card>}>
                <KnowledgeGraph
                  subjectId={effectiveSubjectId}
                  mode="teacher"
                  classId={classId}
                  days={days}
                  onAction={handleGraphAction}
                />
              </React.Suspense>
            </div>
          )}

          {/* 图谱前置薄弱预警（当未展图谱或同时有路线先修依赖时同样可见） */}
          {enough && (data.prerequisite_alerts || []).length > 0 && (
            <Card size="small" title="前置知识薄弱溯因" style={{ marginBottom: 12 }}>
              <List
                size="small"
                dataSource={data.prerequisite_alerts}
                renderItem={(a) => {
                  const firstPre = a.weak_prerequisites?.[0]?.concept;
                  return (
                    <List.Item
                      actions={[
                        firstPre && onCreateRemedialAssignment ? (
                          <Button
                            key="assign"
                            size="small"
                            type="link"
                            onClick={() => triggerRemedialAssign([firstPre, a.knowledge_point], `前置与薄弱点联练：${firstPre} → ${a.knowledge_point}`)}
                          >
                            布置前置补练
                          </Button>
                        ) : null,
                        a.knowledge_point_id ? (
                          <Button key="drill" size="small" type="link" onClick={() => openDetail(a.knowledge_point_id!)}>
                            下钻
                          </Button>
                        ) : null,
                      ].filter(Boolean)}
                    >
                      <List.Item.Meta
                        title={
                          <Space size={6} wrap>
                            <Text strong>{a.knowledge_point}</Text>
                            <Tag color="error">错误率 {ratioText(a.wrong_rate)}</Tag>
                          </Space>
                        }
                        description={a.summary}
                      />
                    </List.Item>
                  );
                }}
              />
            </Card>
          )}

          {/* 可视化图表区：知识点风险四象限 + 每日作答趋势 + 错因分布 */}
          <Row gutter={[16, 12]} style={{ marginBottom: 12 }}>
            <Col xs={24} lg={12}>
              <Card
                size="small"
                title="知识点风险分布（样本量 × 错误率）"
                extra={<Text type="secondary" style={{ fontSize: 12 }}>点击气泡下钻</Text>}
              >
                {!enough || !(data.knowledge_points || []).length ? (
                  <EmptyBlock description="样本充足后展示知识点风险分布图" />
                ) : (
                  <EChart
                    option={kpScatterOption}
                    height={250}
                    ariaLabel="知识点样本量与错误率散点图"
                    onClick={(params: any) => {
                      const kp = params.data?.kp as KnowledgePointStat | undefined;
                      if (kp?.knowledge_point_id) void openDetail(kp.knowledge_point_id);
                    }}
                  />
                )}
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card size="small" title="每日作答量与正确率趋势">
                {(data.trend || []).length === 0 ? (
                  <EmptyBlock description="统计范围内没有作答记录" />
                ) : (
                  <EChart option={trendChartOption} height={250} ariaLabel="每日作答量与正确率双轴趋势图" />
                )}
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 12]} style={{ marginBottom: 12 }}>
            <Col xs={24} lg={10}>
              <Card size="small" title="全班错因分布">
                {(data.error_causes || []).length === 0 ? (
                  <EmptyBlock description="暂无学生自报的错因标签（学生在答错后可一键勾选错因）" />
                ) : (
                  <EChart option={errorCauseOption} height={220} ariaLabel="全班错因分布条形图" />
                )}
              </Card>
            </Col>
            <Col xs={24} lg={14}>
              <Card
                size="small"
                title={<Space><TeamOutlined />学生分层与预警</Space>}
                extra={
                  data.student_tiers ? (
                    <Segmented
                      size="small"
                      value={tierFilter}
                      onChange={(v) => setTierFilter(String(v))}
                      options={[
                        { label: `全部 (${data.student_tiers.students.length})`, value: 'all' },
                        { label: `需干预 (${data.student_tiers.counts.needs_attention})`, value: 'needs_attention' },
                        { label: `巩固中 (${data.student_tiers.counts.consolidating})`, value: 'consolidating' },
                        { label: `稳步 (${data.student_tiers.counts.on_track})`, value: 'on_track' },
                      ]}
                    />
                  ) : null
                }
              >
                {!data.student_tiers?.students?.length ? (
                  <EmptyBlock description="本班暂无学生分层数据" />
                ) : (
                  <Table<StudentTierRow>
                    rowKey="user_id"
                    size="small"
                    dataSource={filteredTierStudents}
                    pagination={{ pageSize: 5, hideOnSinglePage: true }}
                    columns={[
                      { title: '学生', dataIndex: 'display_name' },
                      {
                        title: '分层',
                        dataIndex: 'tier',
                        width: 110,
                        render: (t: string, r: StudentTierRow) => (
                          <Tag color={TIER_COLORS[t] || 'default'}>{r.tier_label}</Tag>
                        ),
                      },
                      { title: '已判分题', dataIndex: 'attempt_count', width: 90 },
                      {
                        title: '错误率',
                        dataIndex: 'wrong_rate',
                        width: 90,
                        render: (v: number | null) => ratioText(v),
                      },
                      {
                        title: '操作',
                        width: 110,
                        render: (_: unknown, r: StudentTierRow) => (
                          <Button
                            size="small"
                            type="link"
                            onClick={() => setAttemptsFor({ id: r.user_id, name: r.display_name })}
                          >
                            明细 / 改判
                          </Button>
                        ),
                      },
                    ]}
                  />
                )}
              </Card>
            </Col>
          </Row>

          {/* 知识点作答明细表 */}
          <Card
            size="small"
            title="知识点作答明细"
            style={{ marginBottom: 12 }}
            extra={enough ? <SourceTag source="quiz_attempts（学生作答与后端判分记录）" /> : undefined}
          >
            {!enough ? (
              <EmptyBlock description={`数据不足，未展示知识点掌握结论。${data.insufficient_reason || ''}`} />
            ) : (data.knowledge_points || []).length === 0 ? (
              <EmptyBlock description="统计范围内没有已判分的作答记录" />
            ) : (
              <Table<KnowledgePointStat>
                rowKey={(r) => r.knowledge_point_id || r.knowledge_point}
                size="small"
                dataSource={data.knowledge_points}
                pagination={false}
                scroll={{ x: 820 }}
                columns={[
                  { title: '知识点', dataIndex: 'knowledge_point' },
                  {
                    title: '样本量', dataIndex: 'attempt_count', width: 100,
                    sorter: (a, b) => a.attempt_count - b.attempt_count,
                  },
                  {
                    title: '错误次数', dataIndex: 'wrong_count', width: 100,
                    sorter: (a, b) => a.wrong_count - b.wrong_count,
                  },
                  {
                    title: '错误率', dataIndex: 'wrong_rate', width: 100,
                    render: (v: number | null) => ratioText(v),
                    sorter: (a, b) => (a.wrong_rate ?? 0) - (b.wrong_rate ?? 0),
                  },
                  { title: '涉及学生', dataIndex: 'student_count', width: 95 },
                  {
                    title: '样本状态', dataIndex: 'enough_data', width: 110,
                    render: (v: boolean) => v ? <Tag color="green">样本充足</Tag> : <Tag>数据不足</Tag>,
                  },
                  {
                    title: '操作', width: 210,
                    render: (_: unknown, r: KnowledgePointStat) => (
                      <Space size={2}>
                        <Button
                          size="small"
                          type="link"
                          icon={<SearchOutlined />}
                          disabled={!r.knowledge_point_id}
                          onClick={() => r.knowledge_point_id && openDetail(r.knowledge_point_id)}
                        >
                          下钻
                        </Button>
                        {onCreateRemedialAssignment && r.wrong_count > 0 && (
                          <Button
                            size="small"
                            type="link"
                            onClick={() => triggerRemedialAssign([r.knowledge_point], `巩固练习：${r.knowledge_point}`)}
                          >
                            补练
                          </Button>
                        )}
                      </Space>
                    ),
                  },
                ]}
              />
            )}
          </Card>

          {/* 常见错题（含客观题选项分布，不含标准答案） */}
          <Card
            size="small"
            title="高频错题与选项分布（不含标准答案）"
            style={{ marginBottom: 12 }}
            extra={enough ? <SourceTag source="quiz_attempts（学生作答与后端判分记录）" /> : undefined}
          >
            {!enough ? (
              <EmptyBlock description="数据不足，未展示错题统计" />
            ) : (data.wrong_questions || []).length === 0 ? (
              <EmptyBlock description="统计范围内没有答错的题目" />
            ) : (
              <List
                size="small"
                dataSource={data.wrong_questions || []}
                renderItem={(w: WrongQuestionStat) => (
                  <List.Item>
                    <div style={{ width: '100%' }}>
                      <Space size={8} wrap style={{ marginBottom: 4 }}>
                        <Tag color={questionTypeColor(w.question_type || w.type)}>
                          {questionTypeLabel(w.question_type || w.type)}
                        </Tag>
                        {w.knowledge_point && <Tag color="purple">{w.knowledge_point}</Tag>}
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          作答 {w.attempt_count} 次 · 错 {w.wrong_count} 次 · 错误率 {ratioText(w.wrong_rate)}
                          {w.student_count != null ? ` · 涉及 ${w.student_count} 名学生` : ''}
                        </Text>
                      </Space>
                      <Paragraph style={{ marginBottom: 4 }}>
                        {questionStem(w) || `（题干不可用，题目 ID：${w.question_id}）`}
                      </Paragraph>
                      {w.option_distribution?.length ? (
                        <OptionDistributionBars items={w.option_distribution} />
                      ) : Array.isArray(w.options) && w.options.length > 0 ? (
                        <Space direction="vertical" size={0}>
                          {w.options.map((o) => (
                            <Text key={o.id} type="secondary" style={{ fontSize: 12 }}>
                              {o.id}. {o.text}
                            </Text>
                          ))}
                        </Space>
                      ) : null}
                    </div>
                  </List.Item>
                )}
              />
            )}
          </Card>

          {/* 原始日表折叠收纳 */}
          {(data.trend || []).length > 0 && (
            <Collapse
              size="small"
              items={[{
                key: 'raw-trend',
                label: '查看每日原始作答计数表',
                children: (
                  <Table
                    size="small"
                    rowKey="date"
                    dataSource={data.trend}
                    pagination={false}
                    columns={[
                      { title: '日期', dataIndex: 'date', width: 120 },
                      { title: '作答次数', dataIndex: 'attempt_count', width: 110 },
                      { title: '错误次数', dataIndex: 'wrong_count', width: 110 },
                      { title: '参与学生', dataIndex: 'student_count', width: 110 },
                      { title: '当日错误率', dataIndex: 'wrong_rate', width: 120, render: (v: number | null) => ratioText(v) },
                    ]}
                  />
                ),
              }]}
            />
          )}

          {/* AI 解读 */}
          {explain && (
            <Card size="small" title="AI 解读与教学建议" style={{ marginTop: 12 }}>
              {!explain.enough_data ? (
                <Alert
                  type="info"
                  showIcon
                  message="样本量不足，未生成 AI 结论"
                  description={explain.reason || explain.note}
                />
              ) : (
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  <Paragraph style={{ marginBottom: 0 }}>
                    <Text strong>解读：</Text>{explain.explanation || '（模型未返回解读内容）'}
                  </Paragraph>
                  <div>
                    <Text strong>教学建议：</Text>
                    <List size="small" dataSource={explain.suggestions || []} renderItem={(s) => <List.Item>{s}</List.Item>} />
                  </div>
                  {(explain.cautions || []).length > 0 && (
                    <div>
                      <Text strong>数据局限提醒：</Text>
                      <List size="small" dataSource={explain.cautions} renderItem={(c) => <List.Item>{c}</List.Item>} />
                    </div>
                  )}
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {explain.note} · 模型：{explain.model || '—'}
                  </Text>
                </Space>
              )}
            </Card>
          )}
        </>
      )}

      {/* 知识点下钻 */}
      <Modal
        title={`知识点下钻：${detail?.knowledge_point?.knowledge_point || ''}`}
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={detail ? (
          <Space>
            {onCreateRemedialAssignment && (
              <Button
                type="primary"
                icon={<ThunderboltOutlined />}
                onClick={() => {
                  const kpName = detail.knowledge_point.knowledge_point;
                  const pre = (detail.prerequisite_diagnosis?.weak_prerequisites || []).map((p) => p.concept);
                  const targets = [...new Set([...pre.slice(0, 1), kpName])];
                  setDetailOpen(false);
                  triggerRemedialAssign(targets, `巩固练习：${kpName}`);
                }}
              >
                一键布置巩固练习
              </Button>
            )}
            {onCreateRemedialLessonPlan && (
              <Button
                icon={<FileAddOutlined />}
                onClick={() => {
                  const kpName = detail.knowledge_point.knowledge_point;
                  const summary = detail.prerequisite_diagnosis?.summary || '';
                  setDetailOpen(false);
                  triggerRemedialPlan(`${kpName} 错题讲评与巩固`, summary || `重点攻克「${kpName}」高频错题`);
                }}
              >
                一键带入教案
              </Button>
            )}
            <Button onClick={() => setDetailOpen(false)}>关闭</Button>
          </Space>
        ) : null}
        width={800}
      >
        {detailLoading ? <LoadingBlock tip="加载下钻数据…" /> : detailError ? (
          <ErrorAlert message={detailError} onRetry={() => { if (detailKpId) openDetail(detailKpId); }} />
        ) : !detail ? (
          <Empty description="暂无数据" />
        ) : (
          <>
            <Descriptions size="small" column={2} style={{ marginBottom: 8 }}>
              <Descriptions.Item label="统计范围">{detail.range.start} ~ {detail.range.end}</Descriptions.Item>
              <Descriptions.Item label="样本状态">
                {detail.enough_data ? <Tag color="green">样本充足</Tag> : <Tag color="orange">数据不足，仅供参考</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="作答次数">{detail.knowledge_point?.attempt_count ?? 0}</Descriptions.Item>
              <Descriptions.Item label="错误次数">
                {detail.knowledge_point?.wrong_count ?? 0}（错误率 {ratioText(detail.knowledge_point?.wrong_rate)}）
              </Descriptions.Item>
            </Descriptions>

            {detail.prerequisite_diagnosis?.summary && (
              <Alert
                type={detail.prerequisite_diagnosis.root_cause_type === 'prerequisite_gap' ? 'warning' : 'info'}
                showIcon
                style={{ marginBottom: 10 }}
                message="知识图谱前置溯因"
                description={detail.prerequisite_diagnosis.summary}
              />
            )}

            {(detail.error_causes || []).length > 0 && (
              <div style={{ marginBottom: 10 }}>
                <Text type="secondary" style={{ fontSize: 12, marginRight: 8 }}>本知识点错因分布：</Text>
                <Space size={6} wrap>
                  {detail.error_causes!.map((c) => (
                    <Tag key={c.code} color="orange">{c.label} · {c.count}次 ({Math.round(c.ratio * 100)}%)</Tag>
                  ))}
                </Space>
              </div>
            )}

            <Divider orientation="left" plain>错得最多的题目（含选项分布，不含答案）</Divider>
            {detail.questions.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有答错的题目" />
            ) : (
              <List
                size="small"
                dataSource={detail.questions}
                renderItem={(q) => (
                  <List.Item>
                    <div style={{ width: '100%' }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        作答 {q.attempt_count} 次 · 错 {q.wrong_count} 次
                      </Text>
                      <Paragraph style={{ marginBottom: 4 }}>
                        {questionStem(q) || `（题干不可用，题目 ID：${q.question_id}）`}
                      </Paragraph>
                      <OptionDistributionBars items={q.option_distribution} />
                    </div>
                  </List.Item>
                )}
              />
            )}

            <Divider orientation="left" plain>相关课程资料</Divider>
            {detail.materials.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本课程没有已就绪的资料" />
            ) : (
              <Space size={6} wrap>
                {detail.materials.map((m) => (
                  <Tag key={m.material_id} color={m.matched ? 'blue' : 'default'}>
                    {m.filename}
                    {m.matched ? '（文件名命中）' : ''}
                  </Tag>
                ))}
              </Space>
            )}

            <Divider orientation="left" plain>建议动作</Divider>
            <List size="small" dataSource={detail.next_actions} renderItem={(a) => <List.Item>{a}</List.Item>} />
          </>
        )}
      </Modal>

      <StudentAttemptsDrawer
        open={!!attemptsFor}
        studentId={attemptsFor?.id || ''}
        studentName={attemptsFor?.name}
        onClose={() => setAttemptsFor(null)}
        onRegraded={() => load(classId, days)}
      />
    </div>
  );
};

export default AnalyticsPanel;
