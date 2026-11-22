import React, { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Card, Col, Empty, Input, List, Progress, Row, Select, Space, Tag, Typography,
} from 'antd';
import {
  ArrowLeftOutlined, CheckCircleOutlined, CloseCircleOutlined, FileSearchOutlined, UserOutlined,
} from '@ant-design/icons';
// 个人答卷负责人只需改本模块 data.ts；建议块通过组件入口接入（不动本页即可换建议实现）
import { searchStudents, getStudentName, CLASSES, QUIZZES } from '../shared/ids';
import {
  getAnswer, getQuestion, listQuestionsByKp, listStudentAnswers, getAnswerEvent, listStudentEvents,
} from './data';
import LearningAdvice from '../learningAdvice/LearningAdvice';
import TextbookDrawer from '../shared/TextbookDrawer';
import { getBundle, isRealData } from '../dataSource';
import type { RealAnswer } from '../dataSource';
import type { TextbookSource } from '../shared/types';

const { Title, Text, Paragraph } = Typography;

/**
 * 学生答卷（比赛版第二页）
 * - 定位口径：学生＋测验节点＋题目（＋记录编号）。指定测验找不到记录时如实显示状态，
 *   绝不自动拿另一场测验的答卷或诊断补上（官方数据有 37 组学生×题目跨节点重复）。
 * - 从班级页进来：URL 带 studentId/questionId/quizId/classId/kpId/recordId，直接定位。
 * - 官方模式直接进本页：选学生后列出其在所选测验的全部官方作答（含待评分/未作答状态）。
 * - 返回班级页：带上原筛选参数与数据源，老师可继续看下一个学生。
 */
const StudentAnswersPage: React.FC = () => {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const fromClass = params.has('studentId');
  const real = isRealData();

  const studentId = params.get('studentId') || '';
  const questionId = params.get('questionId') || '';
  const quizId = params.get('quizId') || QUIZZES[0]?.id || '';
  const [keyword, setKeyword] = useState('');
  const [textbook, setTextbook] = useState<TextbookSource | null>(null);

  const patchParams = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    Object.entries(patch).forEach(([k, v]) => { if (v === null || v === '') next.delete(k); else next.set(k, v); });
    setParams(next, { replace: true });
  };

  const students = useMemo(() => searchStudents(keyword), [keyword]);
  const question = questionId ? getQuestion(questionId) : undefined;
  const kpQuestions = question?.kpId ? listQuestionsByKp(question.kpId) : [];
  const recordId = params.get('recordId') || undefined;
  const answer = (studentId && questionId
    ? getAnswer(studentId, questionId, quizId || undefined, recordId)
    : undefined) as RealAnswer | undefined;

  // 指定测验无有效作答时，查全量事件索引如实显示状态（不跨测验补）
  const event = studentId && questionId && quizId ? getAnswerEvent(studentId, questionId, quizId) : undefined;
  const otherQuizEvents = useMemo(() => (
    studentId && questionId
      ? listStudentEvents(studentId).filter((e) => e.questionId === questionId && e.quizId !== quizId)
      : []
  ), [studentId, questionId, quizId]);

  // 本题「过程末行 vs 最终填答」关键差异：来自结构化诊断，不在页面里另写一套判断
  const finding = useMemo(() => {
    if (!real || !answer || !question?.kpId) return undefined;
    const ind = question.kpId;
    const d = getBundle()?.diagnoses?.diagnoses.find(
      (x) => x.student_id === studentId && x.node_id === quizId && x.indicator_id === ind);
    return d?.question_findings.find((f) => f.item_id === questionId && f.kind === '过程与填答不一致');
  }, [real, answer, question, studentId, quizId, questionId]);

  const backToClass = () => {
    // 恢复班级页筛选：测验 / 两班 / 主题 / 选中考点 / 数据源
    const q = new URLSearchParams();
    const source = params.get('source');
    const classId = params.get('classId');
    const kpId = params.get('kpId');
    if (source) q.set('source', source);
    if (quizId) q.set('quizId', quizId);
    if (classId) q.set('classId', classId);
    if (kpId) q.set('kpId', kpId);
    for (const key of ['reviewBudget', 'reviewStrategy']) {
      const value = params.get(key);
      if (value) q.set(key, value);
    }
    navigate({ pathname: '/class-analytics', search: q.toString() });
  };

  const adviceKpId = question?.kpId || '';
  const quizName = QUIZZES.find((q) => q.id === quizId)?.name || quizId;
  const studentQuizAnswers = useMemo(() => (
    real && studentId ? listStudentAnswers(studentId, quizId || undefined) : []
  ), [real, studentId, quizId]);
  const studentQuizEvents = useMemo(() => (
    real && studentId ? listStudentEvents(studentId, quizId || undefined) : []
  ), [real, studentId, quizId]);
  const availableQuestions = useMemo(() => {
    const bundle = getBundle();
    const available = bundle?.quizzes.find((q) => q.id === quizId)?.availableItems;
    return (bundle?.questions || []).filter((q) => q.fullScore > 0 && (!available?.length || available.includes(q.id)));
  }, [quizId]);

  const statusTag = (status: string, quarantined?: boolean) => {
    if (quarantined) return <Tag color="red">分数越界·隔离待核</Tag>;
    if (status === '待评分') return <Tag color="gold">待评分</Tag>;
    if (status === '未作答') return <Tag>未作答</Tag>;
    return <Tag color="green">已评分</Tag>;
  };

  return (
    <div className="competition-page">
      <div className="competition-header">
        <div>
          <Title level={2} className="competition-title">学生答卷与学习建议</Title>
        </div>
        <Space>
          {fromClass && <Button icon={<ArrowLeftOutlined />} onClick={backToClass}>返回班级学情</Button>}
        </Space>
      </div>

      {/* 学生与测验选择：作答按 学生＋测验＋题目 定位 */}
      <Card size="small" style={{ marginBottom: 16 }}
            title={<Space><UserOutlined />选择学生{real ? '与测验' : ''}</Space>}>
        <Space wrap>
          <Input.Search
            placeholder="搜索学生姓名或编号" allowClear value={keyword}
            onChange={(e) => setKeyword(e.target.value)} style={{ width: 240 }}
          />
          <Select
            aria-label="选择学生" placeholder="或选择学生" value={studentId || undefined}
            onChange={(v) => patchParams({ studentId: v, classId: students.find((s) => s.id === v)?.classId || null, questionId: null, recordId: null, kpId: null })}
            style={{ width: 240 }}
            options={students.map((s) => ({
              value: s.id,
              label: `${s.name}（${CLASSES.find((c) => c.id === s.classId)?.name || s.classId}）`,
            }))}
          />
          {real && (
            <Select
              aria-label="选择测验" value={quizId || undefined} style={{ width: 180 }}
              onChange={(v) => patchParams({ quizId: v, recordId: null })}
              options={QUIZZES.map((q) => ({ value: q.id, label: `${q.name}（${q.date}）` }))}
            />
          )}
          {real && studentId && <Select aria-label="选择题目" showSearch optionFilterProp="label"
            placeholder="选择题目或现场补测题" value={questionId || undefined} style={{ width: 310 }}
            options={availableQuestions.map((q) => ({ value: q.id, label: `${q.id} · ${q.stem.slice(0, 25)}${studentQuizEvents.some((e) => e.questionId === q.id) ? '' : '（本次无记录，可补测）'}` }))}
            onChange={(id) => {
              const q = availableQuestions.find((x) => x.id === id);
              patchParams({ questionId: id, recordId: null, kpId: q?.kpId || null });
            }} />}
          {studentId && <Tag color="blue">当前：{getStudentName(studentId)}</Tag>}
        </Space>
      </Card>
      {!studentId ? (
        <Card title="从哪里开始：三名代表性学生案例">
          <Paragraph type="secondary" style={{ fontSize: 12.5 }}>
            以下三例来自官方案例册；也可用上方搜索框或下拉选择任意学生。
          </Paragraph>
          <Space wrap>
            {(getBundle()?.caseBook?.cases ?? []).map((c) => {
              const bundle = getBundle();
              const fromFinding = c.question_findings[0]?.item_id;
              // 与班级页案例卡同口径：优先落该生实际作答过的题，让老师先看到真实证据
              const answered = bundle?.answers.find((a) => a.studentId === c.student_id && a.quizId === c.node_id
                && bundle.questions.find((q) => q.id === a.questionId)?.kpId === c.indicator_id)?.questionId;
              const qid = fromFinding || answered
                || bundle?.curriculumMap?.indicators.find((x) => x.indicator_id === c.indicator_id)?.questions[0]?.material_id
                || '';
              return (
                <Button key={`${c.student_id}|${c.indicator_id}`}
                        onClick={() => patchParams({
                          studentId: c.student_id, questionId: qid, quizId: c.node_id,
                          kpId: c.indicator_id, recordId: null,
                          classId: bundle?.students.find((s) => s.id === c.student_id)?.classId || null,
                        })}>
                  {c.case_name} · {c.student_id} · {c.node_id}/{c.indicator_id}
                </Button>
              );
            })}
          </Space>
          {(getBundle()?.caseBook?.cases ?? []).length === 0 && (
            <Empty description="请先搜索或选择一名学生" />
          )}
        </Card>
      ) : !questionId ? (
        real ? (
          <Card title={`${getStudentName(studentId)} 在「${quizName}」的官方作答`}>
            <Paragraph type="secondary">
              共 {studentQuizEvents.length} 条作答事件（含待评分/未作答）；点一道题查看作答详情与建议。
            </Paragraph>
            {studentQuizEvents.length === 0 ? (
              <Empty description="该学生在所选测验下没有作答记录；不会用其他测验的记录替代。" />
            ) : (
              <List
                size="small"
                dataSource={studentQuizEvents}
                renderItem={(e) => {
                  const q = getQuestion(e.questionId);
                  const scored = studentQuizAnswers.find((a) => a.questionId === e.questionId);
                  return (
                    <List.Item>
                      <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
                        <Button type="link" style={{ padding: 0, height: 'auto', whiteSpace: 'normal', textAlign: 'left' }}
                          onClick={() => patchParams({ studentId: e.studentId, quizId: e.quizId, questionId: e.questionId, recordId: e.recordId, kpId: q?.kpId || null })}>
                          {e.questionId} · {q?.stem?.slice(0, 46) || '题干未提供'}
                        </Button>
                        <Space size={6}>
                          {statusTag(e.status, e.quarantined)}
                          {scored && <Tag color="blue">{scored.score}/{scored.fullScore}</Tag>}
                          <Text type="secondary" style={{ fontSize: 11 }}>{e.recordId}</Text>
                        </Space>
                      </Space>
                    </List.Item>
                  );
                }}
              />
            )}
          </Card>
        ) : (
          <Card title={`${getStudentName(studentId)} 的作答`}>
            <Paragraph type="secondary">选择一道题查看作答详情（演示样例）。</Paragraph>
            <List
              dataSource={kpQuestions.length ? kpQuestions : [
                { id: 'q-201', stem: '解方程：2x + 3 = 11' },
                { id: 'q-202', stem: '解方程组：x + y = 5，x − y = 1' },
                { id: 'q-301', stem: '解不等式：2x − 3 > 5' },
              ]}
              renderItem={(q: { id: string; stem: string }) => (
                <List.Item>
                  <Button type="link" onClick={() => patchParams({ questionId: q.id, recordId: null })}>{q.stem}</Button>
                </List.Item>
              )}
            />
          </Card>
        )
      ) : !answer ? (
        real ? (
          <Row gutter={16}>
            <Col xs={24} lg={14}>
              <Alert type="warning" showIcon
                message={
                  event?.outOfScope ? '该题超出所选测验的可用题范围，待核（不计入统计）'
                    : event?.quarantined ? '该题记录分数越界，已隔离待核（不计入得分）'
                      : event?.status === '待评分' ? '该题在所选测验待评分（不计入得分率）'
                        : event?.status === '未作答' ? '该题在所选测验未作答（按状态保留，不当作 0 分）'
                          : '该学生在所选测验下无此题作答记录'
                }
                description={<>
                  <div>测验：{quizName} · 学生：{getStudentName(studentId)} · 题目：{questionId}。
                    不会用其他测验的答卷或诊断替代。</div>
                  <div style={{ marginTop: 6 }}>
                    <Text type="secondary">右侧仍显示该考点已生成的诊断与补测任务（覆盖判断独立于本题状态）。</Text>
                  </div>
                  {otherQuizEvents.length > 0 && (
                    <div style={{ marginTop: 6 }}>
                      <Text type="secondary">该题在其他测验有记录：</Text>
                      {otherQuizEvents.map((e) => (
                        <Button key={e.recordId} size="small" type="link"
                          onClick={() => patchParams({ quizId: e.quizId, recordId: e.recordId })}>
                          切换到 {e.quizId}（{e.recordId}）
                        </Button>
                      ))}
                    </div>
                  )}
                </>}
                action={<Button onClick={() => patchParams({ questionId: null, recordId: null })}>换一道题</Button>} />
            </Col>
            <Col xs={24} lg={10}>
              <LearningAdvice
                kpId={adviceKpId}
                studentId={studentId}
                questionId={questionId}
                nodeId={quizId || undefined}
                onOpenTextbook={setTextbook}
              />
            </Col>
          </Row>
        ) : (
          <Alert type="warning" showIcon
            message="该学生在这道题上暂无作答明细（演示样例）"
            description="可返回选择其他学生或题目。真实作答数据接入后此处显示服务返回值。"
            action={<Button onClick={() => patchParams({ questionId: null, recordId: null })}>换一道题</Button>} />
        )
      ) : (
        <Row gutter={16}>
          {/* 左：题干 + 关键差异 + 得分 + 评分点 + 步骤（sticky，右侧建议滚动时保持可见） */}
          <Col xs={24} lg={14} className="comp-answer-sticky">
            <Card size="small" title={<Space><FileSearchOutlined />作答详情</Space>}
                  extra={<Space size={4}><Tag>{quizId}</Tag><Tag color="blue">{getStudentName(studentId)}</Tag></Space>}>
              <Paragraph strong style={{ fontSize: 15 }}>{question?.stem}</Paragraph>
              <Space wrap style={{ marginBottom: 12 }}>
                <Tag>满分 {answer.fullScore}</Tag>
                <Tag color={answer.score / answer.fullScore >= 0.6 ? 'green' : 'red'}>
                  得分 {answer.score}
                </Tag>
                <Progress percent={Math.round(answer.score / answer.fullScore * 100)}
                          size="small" style={{ width: 180 }} />
              </Space>

              {real && (
                <div className="comp-record-line">
                  以官方记录为准：来源记录 <Text code>{answer.sourceRecordId || '未提供'}</Text>
                  {' '}· 修订版本 {answer.sourceVersion || '未提供'}
                  {answer.superseded?.length
                    ? `（旧记录 ${answer.superseded.map((s) => s.record_id).join('、')} 已被覆盖）`
                    : ''}
                </div>
              )}

              {real && finding ? (
                <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                  message="过程末行与最终填答需核对"
                  description={
                    <Space size={12} wrap>
                      {finding.last_expr != null && <Tag color="orange">过程末行：{finding.last_expr}</Tag>}
                      {finding.final_answer != null && <Tag color="red">最终填答：{finding.final_answer}</Tag>}
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        官方评分保持不变；不一致原因需向本人核实（见右侧建议）。
                      </Text>
                    </Space>
                  } />
              ) : real && answer.finalAnswer ? (
                <Paragraph style={{ marginBottom: 12 }}>
                  <Text type="secondary">官方『学生最终作答』栏：</Text>
                  <Text strong>{answer.finalAnswer}</Text>
                </Paragraph>
              ) : null}

              <Title level={5} style={{ fontSize: 14 }}>评分点（哪一步丢分）</Title>
              {answer.rubricUnavailable || answer.scoringPoints.length === 0 ? (
                <Alert type="warning" showIcon message="官方评分点未提供（不虚构评分明细）" />
              ) : (
                <List size="small" dataSource={answer.scoringPoints}
                  renderItem={(sp) => (
                    <List.Item>
                      <Space align="start" size={8}>
                        {sp.ok
                          ? <CheckCircleOutlined style={{ color: '#16a34a', marginTop: 3 }} />
                          : <CloseCircleOutlined style={{ color: '#dc2626', marginTop: 3 }} />}
                        <div>
                          <Text strong style={{ color: sp.ok ? '#16a34a' : '#b45309' }}>
                            {sp.label}
                          </Text>
                          <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                            {sp.earnedScore}/{sp.fullScore} 分
                          </Text>
                          <div><Text type="secondary" style={{ fontSize: 12 }}>证据：{sp.evidence}</Text></div>
                        </div>
                      </Space>
                    </List.Item>
                  )}
                />
              )}

              <Title level={5} style={{ fontSize: 14, marginTop: 12 }}>原始解题步骤</Title>
              {answer.stepsUnavailable || answer.steps.length === 0 ? (
                <Alert type="warning" showIcon message="原始步骤未提供（官方记录中该字段为空，如实说明）" />
              ) : (
                <ol className="competition-steps">
                  {answer.steps.map((s) => (
                    <li key={s.step}><Text>{s.text}</Text></li>
                  ))}
                </ol>
              )}

              {!real && answer.diagnosis && (
                <Alert type="warning" showIcon style={{ marginTop: 10 }}
                       message="诊断" description={answer.diagnosis} />
              )}
            </Card>
          </Col>

          {/* 右：结构化诊断 + 可执行建议 + 教师调整 + 回放 + 学生预览 */}
          <Col xs={24} lg={10}>
            {!real && answer.nextStep && (
              <Card size="small" title="下一步安排" style={{ marginBottom: 12 }}>
                <Paragraph style={{ marginBottom: 0 }}>{answer.nextStep}</Paragraph>
              </Card>
            )}
            <LearningAdvice
              kpId={adviceKpId}
              studentId={studentId}
              questionId={questionId}
              nodeId={quizId || undefined}
              onOpenTextbook={setTextbook}
            />
          </Col>
        </Row>
      )}

      <TextbookDrawer open={!!textbook} source={textbook} onClose={() => setTextbook(null)} />
    </div>
  );
};

export default StudentAnswersPage;
