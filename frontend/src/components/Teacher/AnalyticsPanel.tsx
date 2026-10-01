import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, Card, Col, Descriptions, Divider, Empty, List, Modal, Radio, Row,
  Select, Space, Statistic, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import { BulbOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import {
  ClassAnalytics, ExplainResult, KnowledgePointDetail, KnowledgePointStat, TeacherClass,
  WrongQuestionStat, describeTeacherError, explainClassAnalytics, getClassAnalytics,
  getKnowledgePointDetail, isForbiddenError, listClasses,
} from '../../api/teacher';
import {
  EmptyBlock, ErrorAlert, LoadingBlock, PermissionAlert, SourceTag, formatTime,
  questionStem, questionTypeColor, questionTypeLabel, ratioText,
} from './common';

const { Text, Title, Paragraph } = Typography;

export interface AnalyticsPanelProps {
  courseId: string;
  courseTitle?: string;
  focusClassId?: string;
  focusKnowledgePointId?: string;
  onBack?: () => void;
}

const DAY_OPTIONS = [
  { label: '近 7 天', value: 7 },
  { label: '近 30 天', value: 30 },
  { label: '近 90 天', value: 90 },
];

/** 学情分析：参与与完成情况 / 知识点作答 / 常见错题 / 趋势 / 下钻 / AI 解读 */
const AnalyticsPanel: React.FC<AnalyticsPanelProps> = ({
  courseId, courseTitle, focusClassId, focusKnowledgePointId, onBack,
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
    // 仅在初始聚焦时触发一次
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

  if (forbidden) return <PermissionAlert message={forbidden} onBack={onBack} />;

  const classOptions = classes.map((c) => ({ value: c.id, label: c.name }));
  const enough = !!data?.enough_data;

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
          <Tooltip title="后端会基于已算出的汇总数字生成解释；样本不足时不会调用模型">
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
          <EmptyBlock description="该课程还没有班级，暂无可分析的学情。请先在「班级管理」中新建班级。" />
        </Card>
      ) : loading ? (
        <LoadingBlock tip="正在统计学情…" />
      ) : !data ? (
        <Card size="small"><Empty description="暂无学情数据" /></Card>
      ) : (
        <>
          {/* 数据范围与来源 */}
          <Card size="small" style={{ marginBottom: 12 }}>
            <Space size={8} wrap>
              <Text strong>{data.class_name}</Text>
              <Text type="secondary">{data.course_title}</Text>
              <Tag color="blue">统计范围 {data.range.start} ~ {data.range.end}</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>生成于 {formatTime(data.generated_at)}</Text>
              {(data.sources || []).map((s) => <SourceTag key={s} source={s} />)}
            </Space>
          </Card>

          {/* 数据是否充足：不足时不下结论 */}
          {enough ? (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 12 }}
              message="样本量达到下结论的下限"
              description={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  结论门槛：已判分作答 ≥ {data.thresholds?.min_attempts ?? '—'} 次，且参与学生 ≥ {data.thresholds?.min_students ?? '—'} 人。
                </Text>
              }
            />
          ) : (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message="数据不足，未展示知识点掌握情况等结论"
              description={
                <Space direction="vertical" size={2}>
                  <Text>{data.insufficient_reason || '样本量不足，无法给出可靠结论'}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    结论门槛：已判分作答 ≥ {data.thresholds?.min_attempts ?? '—'} 次，且参与学生 ≥ {data.thresholds?.min_students ?? '—'} 人。
                    下方仅展示参与与完成情况等事实性数字。
                  </Text>
                </Space>
              }
            />
          )}

          {/* 参与与完成：事实性数字，照常展示 */}
          <Row gutter={[16, 12]} style={{ marginBottom: 12 }}>
            <Col xs={24} lg={12}>
              <Card size="small" title="参与情况" extra={<SourceTag source={data.participation?.source} />}>
                <Space size={28} wrap>
                  <Statistic title="在班学生" value={data.participation?.student_count ?? 0} suffix="人" />
                  <Statistic title="有作答记录" value={data.participation?.answered_student_count ?? 0} suffix="人" />
                  <Statistic title="学生参与率" value={ratioText(data.participation?.attempt_student_ratio)} />
                  <Statistic title="作答题次" value={data.participation?.attempt_count ?? 0} suffix="次" />
                  <Statistic title="已判分" value={data.participation?.graded_count ?? 0} suffix="次" />
                </Space>
                <Divider style={{ margin: '10px 0 6px' }} />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  统计范围仅包含本班在班学生在该课程学科下的作答记录。
                </Text>
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card size="small" title="任务完成情况" extra={<SourceTag source={data.task_completion?.source} />}>
                {data.task_completion ? (
                  <>
                    <Space size={28} wrap>
                      <Statistic title="任务总数" value={data.task_completion.total} suffix="个" />
                      <Statistic title="已完成" value={data.task_completion.completed} suffix="个" />
                      <Statistic title="完成率" value={ratioText(data.task_completion.ratio)} />
                    </Space>
                    <Divider style={{ margin: '10px 0 6px' }} />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      按学习任务（含教师布置的作业）的计划日期落在统计范围内统计。
                    </Text>
                  </>
                ) : (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="统计范围内没有任务数据" />
                )}
              </Card>
            </Col>
          </Row>

          {/* 知识点作答情况：数据不足时不展示结论 */}
          <Card
            size="small"
            title="知识点作答情况"
            style={{ marginBottom: 12 }}
            extra={enough ? <SourceTag source="quiz_attempts（学生作答与后端判分记录）" /> : undefined}
          >
            {!enough ? (
              <EmptyBlock description={`数据不足，未展示知识点掌握情况。${data.insufficient_reason || ''}`} />
            ) : (data.knowledge_points || []).length === 0 ? (
              <EmptyBlock description="统计范围内没有已判分的作答记录" />
            ) : (
              <>
                <Table<KnowledgePointStat>
                  rowKey={(r) => r.knowledge_point_id || r.knowledge_point}
                  size="small"
                  dataSource={data.knowledge_points}
                  pagination={false}
                  scroll={{ x: 820 }}
                  columns={[
                    { title: '知识点', dataIndex: 'knowledge_point' },
                    {
                      title: '样本量（作答次数）', dataIndex: 'attempt_count', width: 150,
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
                    {
                      title: '涉及学生', dataIndex: 'student_count', width: 100,
                    },
                    {
                      title: '数据是否充足', dataIndex: 'enough_data', width: 130,
                      render: (v: boolean) => v
                        ? <Tag color="green">样本充足</Tag>
                        : <Tag>数据不足</Tag>,
                    },
                    {
                      title: '操作', width: 110,
                      render: (_: unknown, r: KnowledgePointStat) => (
                        <Button
                          size="small"
                          type="link"
                          icon={<SearchOutlined />}
                          disabled={!r.knowledge_point_id}
                          onClick={() => r.knowledge_point_id && openDetail(r.knowledge_point_id)}
                        >
                          下钻
                        </Button>
                      ),
                    },
                  ]}
                />
                <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
                  每行来源：{data.knowledge_points[0]?.source || '作答记录'}。样本不足的知识点排在后面对标「数据不足」，不建议据此下结论。
                </Paragraph>
              </>
            )}
          </Card>

          {/* 常见错题（不含标准答案） */}
          <Card
            size="small"
            title="常见错题（题干与选项，不含标准答案）"
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
                      {Array.isArray(w.options) && w.options.length > 0 && (
                        <Space direction="vertical" size={0}>
                          {w.options.map((o) => (
                            <Text key={o.id} type="secondary" style={{ fontSize: 12 }}>
                              {o.id}. {o.text}
                            </Text>
                          ))}
                        </Space>
                      )}
                    </div>
                  </List.Item>
                )}
              />
            )}
          </Card>

          {/* 趋势（原始计数，不下结论） */}
          <Card
            size="small"
            title="每日作答趋势（原始计数）"
            extra={<SourceTag source="quiz_attempts（学生作答与后端判分记录）" />}
          >
            {(data.trend || []).length === 0 ? (
              <EmptyBlock description="统计范围内没有作答记录" />
            ) : (
              <>
                <Table
                  size="small"
                  rowKey="date"
                  dataSource={data.trend}
                  pagination={false}
                  scroll={{ x: 620 }}
                  columns={[
                    { title: '日期', dataIndex: 'date', width: 120 },
                    { title: '作答次数', dataIndex: 'attempt_count', width: 110 },
                    { title: '错误次数', dataIndex: 'wrong_count', width: 110 },
                    { title: '参与学生', dataIndex: 'student_count', width: 110 },
                    {
                      title: '当日错误率', dataIndex: 'wrong_rate', width: 120,
                      render: (v: number | null) => ratioText(v),
                    },
                  ]}
                />
                <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
                  趋势只是每天的原始计数，单日样本往往很少，请不要据此判断学习效果变化。
                </Paragraph>
              </>
            )}
          </Card>

          {/* AI 解读：样本不足时如实说明未调用模型 */}
          {explain && (
            <Card size="small" title="AI 解读与教学建议" style={{ marginTop: 12 }}>
              {!explain.enough_data ? (
                <Alert
                  type="info"
                  showIcon
                  message="样本量不足，未生成 AI 结论"
                  description={
                    <Space direction="vertical" size={2}>
                      <Text>{explain.note || '样本量不足，未调用模型'}</Text>
                      {explain.reason && <Text type="secondary">原因：{explain.reason}</Text>}
                    </Space>
                  }
                />
              ) : (
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  <Paragraph style={{ marginBottom: 0 }}>
                    <Text strong>解读：</Text>{explain.explanation || '（模型未返回解读内容）'}
                  </Paragraph>
                  <div>
                    <Text strong>教学建议：</Text>
                    {(explain.suggestions || []).length === 0 ? (
                      <Text type="secondary">（无）</Text>
                    ) : (
                      <List
                        size="small"
                        dataSource={explain.suggestions}
                        renderItem={(s) => <List.Item>{s}</List.Item>}
                      />
                    )}
                  </div>
                  <div>
                    <Text strong>数据局限提醒：</Text>
                    {(explain.cautions || []).length === 0 ? (
                      <Text type="secondary">（无）</Text>
                    ) : (
                      <List
                        size="small"
                        dataSource={explain.cautions}
                        renderItem={(c) => <List.Item>{c}</List.Item>}
                      />
                    )}
                  </div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {explain.note || '解释由 AI 生成，数字来自程序统计'} · 模型：{explain.model || '—'}
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
        footer={null}
        width={780}
      >
        {detailLoading ? <LoadingBlock tip="加载下钻数据…" /> : detailError ? (
          <ErrorAlert
            message={detailError}
            onRetry={() => { if (detailKpId) openDetail(detailKpId); }}
          />
        ) : !detail ? (
          <Empty description="暂无数据" />
        ) : (
          <>
            <Descriptions size="small" column={2} style={{ marginBottom: 8 }}>
              <Descriptions.Item label="统计范围">
                {detail.range.start} ~ {detail.range.end}
              </Descriptions.Item>
              <Descriptions.Item label="数据是否充足">
                {detail.enough_data
                  ? <Tag color="green">样本充足</Tag>
                  : <Tag color="orange">数据不足，仅供参考</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="作答次数">
                {detail.knowledge_point?.attempt_count ?? 0}
              </Descriptions.Item>
              <Descriptions.Item label="错误次数">
                {detail.knowledge_point?.wrong_count ?? 0}
              </Descriptions.Item>
            </Descriptions>

            {!detail.enough_data && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 8 }}
                message="该知识点样本量不足，下面的错题与建议仅供参考"
              />
            )}

            <Divider orientation="left" plain>错得最多的题目（不含答案）</Divider>
            {detail.questions.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有答错的题目" />
            ) : (
              <List
                size="small"
                dataSource={detail.questions}
                renderItem={(q) => (
                  <List.Item>
                    <div style={{ width: '100%' }}>
                      <Space size={8} wrap style={{ marginBottom: 4 }}>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          作答 {q.attempt_count} 次 · 错 {q.wrong_count} 次
                        </Text>
                      </Space>
                      <Paragraph style={{ marginBottom: 4 }}>
                        {questionStem(q) || `（题干不可用，题目 ID：${q.question_id}）`}
                      </Paragraph>
                      {Array.isArray(q.options) && q.options.length > 0 && (
                        <Space direction="vertical" size={0}>
                          {q.options.map((o) => (
                            <Text key={o.id} type="secondary" style={{ fontSize: 12 }}>
                              {o.id}. {o.text}
                            </Text>
                          ))}
                        </Space>
                      )}
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
                    {m.matched ? '（文件名命中知识点）' : ''}
                    {m.visibility === 'teacher_only' ? ' · 仅教师可见' : ''}
                  </Tag>
                ))}
              </Space>
            )}

            <Divider orientation="left" plain>建议动作</Divider>
            {detail.next_actions.length === 0 ? (
              <Text type="secondary">暂无建议</Text>
            ) : (
              <List
                size="small"
                dataSource={detail.next_actions}
                renderItem={(a) => <List.Item>{a}</List.Item>}
              />
            )}

            <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
              下钻结果只做匿名汇总，不包含学生姓名与个人对话内容。
            </Paragraph>
          </>
        )}
      </Modal>
    </div>
  );
};

export default AnalyticsPanel;
