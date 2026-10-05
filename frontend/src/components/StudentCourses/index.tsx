import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Badge, Button, Card, Descriptions, Empty, Input, List, Modal, Radio,
  Space, Spin, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  BookOutlined, CheckCircleOutlined, DatabaseOutlined, FileTextOutlined, LoginOutlined,
  ReloadOutlined, TeamOutlined,
} from '@ant-design/icons';
import {
  AssignmentQuestion, MyCourse, StudentAssignment,
  completeAssignment, describeCourseError, getAssignment, joinCourseByCode,
  leaveCourse, listCourseLessonPlans, listCourseMaterials, listMyAssignments, listMyCourses,
} from '../../api/studentCourses';
import { submitAnswer } from '../../api/student';
import { MarkdownBlock } from '../Teacher/common';

const { Text, Paragraph } = Typography;

/**
 * 学生侧「我的课程」（教师端联动）
 *
 * 学生在这里：
 * - 用老师给的班级邀请码加入课程
 * - 看到自己加入的课程与授课教师
 * - 看到老师布置的作业，进入作答（后端权威判分，不含标准答案）
 *
 * 界面不承担权限职责：未加入的课程 / 未发布的作业在后端就取不到。
 */
export interface StudentCoursesProps {
  /** 入班成功后回调：把该课程的学科告知父组件（用于自动选中学科，避免错题本"看不到东西"） */
  onJoined?: (subjectId?: string) => void;
  /** 深链目标：打开「我的课程」时自动弹出这份作业（首页「去作答」按钮用） */
  focusAssignmentId?: string | null;
  /** 深链已消费回调：父组件据此把 assignment 参数从网址移除 */
  onFocusConsumed?: () => void;
  /** 课程/作业状态变化（提交作业、入班、退班）→ 让首页立即刷新，不必手动刷新 */
  onChanged?: () => void;
}

const StudentCourses: React.FC<StudentCoursesProps> = ({
  onJoined, focusAssignmentId, onFocusConsumed, onChanged,
}) => {
  const [loading, setLoading] = useState(false);
  /** 是否已完成过一次加载：深链判断"没找到"必须等加载完，否则首帧就误判 */
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [courses, setCourses] = useState<MyCourse[]>([]);
  const [assignments, setAssignments] = useState<StudentAssignment[]>([]);
  const [code, setCode] = useState('');
  const [joining, setJoining] = useState(false);

  const [openAssignment, setOpenAssignment] = useState<any>(null);
  const [lessonPlans, setLessonPlans] = useState<any[] | null>(null);
  const [plansCourse, setPlansCourse] = useState<MyCourse | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, any>>({});
  // 提交后的结果小结（作业"完成"与"答对"分开：答错也算完成，并已进入错题/复习）
  const [submitResult, setSubmitResult] =
    useState<{ correct: number; wrong: number; total: number } | null>(null);
  // 课程资料与知识库（学生可见范围 + 真实建库状态）
  const [materialsCourse, setMaterialsCourse] = useState<MyCourse | null>(null);
  const [materials, setMaterials] = useState<any[] | null>(null);
  const [materialsKb, setMaterialsKb] = useState<any>(null);
  const [materialsLoading, setMaterialsLoading] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [courseRes, assignRes] = await Promise.allSettled([
      listMyCourses(), listMyAssignments(),
    ]);
    if (courseRes.status === 'fulfilled') {
      setCourses((courseRes.value as any)?.data?.courses || []);
    } else {
      setError(describeCourseError((courseRes as any).reason));
    }
    if (assignRes.status === 'fulfilled') {
      setAssignments((assignRes.value as any)?.data?.assignments || []);
    }
    setLoading(false);
    setLoaded(true);
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleJoin = async () => {
    const value = code.trim().toUpperCase();
    if (!value) {
      message.warning('请输入老师给的班级邀请码');
      return;
    }
    setJoining(true);
    try {
      const res: any = await joinCourseByCode(value);
      message.success(
        res?.data?.already_member
          ? '你已经在这个班级里了'
          : `已加入《${res?.data?.course?.title || '课程'}》`,
      );
      setCode('');
      await load();
      // 让上层把学科切到刚加入的课程：学生的错题/复习/掌握度都按"当前学科"查。
      // 这里重新拉一次"我的课程"取 subject_id，避免依赖 join 接口的返回结构。
      try {
        const mine: any = await listMyCourses();
        const sid = ((mine?.data?.courses) || []).find((c: MyCourse) => c.subject_id)?.subject_id;
        onJoined?.(sid);
      } catch {
        /* 取不到就保持原样：学生仍可在上方手动选学科 */
      }
    } catch (e) {
      message.error(describeCourseError(e));
    } finally {
      setJoining(false);
    }
  };

  const handleLeave = (course: MyCourse) => {
    Modal.confirm({
      title: `退出《${course.title}》的${course.class_name}？`,
      content: '退出后将看不到老师新布置的内容；已有的作答与错题记录会保留。',
      okText: '退出班级',
      cancelText: '取消',
      onOk: async () => {
        try {
          await leaveCourse(course.class_id);
          message.success('已退出班级');
          await load();
        } catch (e) {
          message.error(describeCourseError(e));
        }
      },
    });
  };

  const openAssignmentDetail = useCallback(async (assignment: StudentAssignment) => {
    setResults({});
    setAnswers({});
    setSubmitResult(null);
    try {
      const res: any = await getAssignment(assignment.id);
      setOpenAssignment(res?.data || null);
      const map: Record<string, any> = {};
      Object.entries((res?.data?.results || {}) as Record<string, any>).forEach(([qid, r]) => {
        map[qid] = r;
      });
      setResults(map);
    } catch (e) {
      message.error(describeCourseError(e));
    }
  }, []);

  // 深链：/student?tab=courses&assignment=<id> 直接打开这份作业（首页「去作答」按钮）
  const focusedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!focusAssignmentId || focusedRef.current === focusAssignmentId) return;
    const target = assignments.find((a) => a.id === focusAssignmentId);
    if (target) {
      focusedRef.current = focusAssignmentId;
      openAssignmentDetail(target);
      onFocusConsumed?.();
    } else if (loaded && !loading) {
      // 目标不存在（已下线/不属于本班）：停止重试并如实告知，不假装打开成功
      focusedRef.current = focusAssignmentId;
      message.warning('没有找到这份作业（可能已被老师下线，或不属于你的班级）');
      onFocusConsumed?.();
    }
  }, [focusAssignmentId, assignments, loaded, loading, openAssignmentDetail, onFocusConsumed]);

  // 幂等令牌缓存（2026-10-03 修复）：此前 token 里带 `Date.now()`，每次点击都不同，
  // 后端 client_token 幂等永不命中 → 同一题重复提交会被重复计数。
  // 现在按 (作业, 题目) 缓存，成功后轮换（重做仍是新的一次作答）。
  const tokenCache = useRef<Record<string, string>>({});
  const assignmentToken = (aid: string, qid: string) => {
    const key = `${aid}_${qid}`;
    if (!tokenCache.current[key]) {
      tokenCache.current[key] = `asg_${aid}_${qid}_${Date.now()}`;
    }
    return tokenCache.current[key];
  };

  const submitOne = async (q: AssignmentQuestion) => {
    const picked = answers[q.id];
    if (!picked) {
      message.warning('请先选择一个答案');
      return;
    }
    setSubmitting(q.id);
    try {
      const res: any = await submitAnswer({
        question_id: q.id,
        user_answer: picked,
        client_token: assignmentToken(String(openAssignment?.id), q.id),
        source_task_id: openAssignment?.my_tasks?.[0]?.id,
        subject_id: openAssignment?.subject_id,
      });
      const data = res?.data || {};
      setResults((prev) => ({ ...prev, [q.id]: data }));
      delete tokenCache.current[`${openAssignment?.id}_${q.id}`];   // 成功后轮换
      message[data.is_correct ? 'success' : 'warning'](
        data.is_correct ? '回答正确' : `回答错误，正确答案：${data.correct_option_id || '见解析'}`);
    } catch (e) {
      message.error(describeCourseError(e));
    } finally {
      setSubmitting(null);
    }
  };

  const finishAssignment = async () => {
    try {
      await completeAssignment(openAssignment.id);
      const vals: any[] = Object.values(results);
      setSubmitResult({
        correct: vals.filter((r) => r?.is_correct === true).length,
        wrong: vals.filter((r) => r?.is_correct === false).length,
        total: (openAssignment.questions || []).length,
      });
      message.success('作业已提交');
      await load();
      // 首页与"我的课程"是同一路由下的不同视图，提交后立即通知首页刷新
      onChanged?.();
    } catch (e) {
      message.error(describeCourseError(e));
    }
  };

  /** 查看本课程资料与知识库状态（学生只看得到"学生可见"的资料） */
  const openMaterials = async (course: MyCourse) => {
    setMaterialsCourse(course);
    setMaterials(null);
    setMaterialsKb(null);
    setMaterialsLoading(true);
    try {
      const res: any = await listCourseMaterials(course.course_id);
      setMaterials(res?.data?.materials || []);
      setMaterialsKb(res?.data?.knowledge_base || null);
    } catch (e) {
      setMaterials([]);
      message.error(describeCourseError(e));
    } finally {
      setMaterialsLoading(false);
    }
  };

  const questionText = (q: AssignmentQuestion) => q.question || q.stem || '（题干缺失）';

  // 作业"是否完成"（每题作答）与"是否答对"分开：答错也允许提交
  const answeredCount = Object.keys(results).length;
  const totalCount = (openAssignment?.questions || []).length;
  const allAnswered = totalCount > 0 && answeredCount >= totalCount;

  /** 查看本课程**已发布**的教案（草稿在后端就不下发） */
  const openLessonPlans = async (course: MyCourse) => {
    setPlansCourse(course);
    setLessonPlans(null);
    try {
      const res: any = await listCourseLessonPlans(course.course_id);
      setLessonPlans(res?.data?.lesson_plans || []);
    } catch (e) {
      message.error(describeCourseError(e));
      setLessonPlans([]);
    }
  };

  return (
    <div>
      <Card
        size="small"
        title={<Space><TeamOutlined />我的课程</Space>}
        extra={
          <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        }
        style={{ marginBottom: 12 }}
      >
        <Space direction="vertical" style={{ width: '100%' }} size={10}>
          <Space wrap>
            <Input
              placeholder="输入老师给的班级邀请码，例如 K7M2QP"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onPressEnter={handleJoin}
              style={{ width: 280 }}
              maxLength={12}
            />
            <Button type="primary" icon={<LoginOutlined />} loading={joining} onClick={handleJoin}>
              加入课程
            </Button>
            <Text type="secondary" style={{ fontSize: 12 }}>
              班级邀请码由任课教师提供（教师工作台 → 班级管理）
            </Text>
          </Space>

          {loading ? <Spin /> : courses.length === 0 ? (
            <Empty description="还没有加入任何课程。向老师索取班级邀请码后在上方加入。" />
          ) : (
            <List
              dataSource={courses}
              renderItem={(c) => (
                <List.Item
                  actions={[
                    <Button key="kb" size="small" type="link"
                            icon={<DatabaseOutlined />}
                            onClick={() => openMaterials(c)}>课程资料与知识库</Button>,
                    <Button key="plans" size="small" type="link"
                            icon={<FileTextOutlined />}
                            onClick={() => openLessonPlans(c)}>已发布教案</Button>,
                    <Button key="leave" size="small" danger type="link"
                            onClick={() => handleLeave(c)}>退出班级</Button>,
                  ]}
                >
                  <List.Item.Meta
                    avatar={<BookOutlined style={{ fontSize: 20, color: '#4f46e5' }} />}
                    title={<Space><Text strong>{c.title}</Text>
                      <Tag color="blue">{c.class_name}</Tag></Space>}
                    description={
                      <Space wrap size={12}>
                        <Text type="secondary">授课教师：{c.teacher_name}</Text>
                        {c.joined_at && <Text type="secondary">加入时间：{c.joined_at.slice(0, 10)}</Text>}
                        <Text type="secondary">已布置作业：{c.assignment_count ?? 0} 次</Text>
                        {c.description && <Text type="secondary">{c.description}</Text>}
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          )}
        </Space>
      </Card>

      {error && <Alert type="warning" showIcon message={error} style={{ marginBottom: 12 }} />}

      <Card
        size="small"
        title={<Badge count={assignments.length} size="small" offset={[10, -2]}>老师布置的作业</Badge>}
      >
        {assignments.length === 0 ? (
          <Empty description="老师还没有布置作业（草稿状态的作业不会提前显示）" />
        ) : (
          <List
            dataSource={assignments}
            renderItem={(a) => {
              const done = Object.keys(a.results || {}).length;
              const total = a.questions?.length || a.question_count || 0;
              return (
                <List.Item
                  actions={[
                    <Button key="open" type="link" size="small"
                            onClick={() => openAssignmentDetail(a)}>进入作答</Button>,
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <Space wrap>
                        <Text strong>{a.title}</Text>
                        <Tag color={a.status === 'closed' ? 'default' : 'green'}>
                          {a.status === 'closed' ? '已关闭' : '进行中'}
                        </Tag>
                        {a.due_date && <Tag color="orange">截止 {a.due_date}</Tag>}
                        {a.course_title && <Tag>{a.course_title}</Tag>}
                      </Space>
                    }
                    description={
                      <Space wrap size={12}>
                        <Text type="secondary">题量：{total} 题</Text>
                        <Text type="secondary">
                          已作答：{done} / {total}
                          {total > 0 && done >= total ? '（已完成）' : ''}
                        </Text>
                        {(a.knowledge_points || []).length > 0 && (
                          <Text type="secondary">知识点：{a.knowledge_points!.join('、')}</Text>
                        )}
                        {a.description && <Text type="secondary">{a.description}</Text>}
                      </Space>
                    }
                  />
                </List.Item>
              );
            }}
          />
        )}
      </Card>

      <Modal
        open={!!openAssignment}
        title={openAssignment?.title}
        onCancel={() => setOpenAssignment(null)}
        width={760}
        footer={submitResult ? [
          <Button key="home" type="primary"
                  onClick={() => { setOpenAssignment(null); navigate('/student'); }}>
            返回学习首页
          </Button>,
          <Button key="stay" onClick={() => setOpenAssignment(null)}>留在本页</Button>,
        ] : [
          <Button key="close" onClick={() => setOpenAssignment(null)}>关闭</Button>,
          <Tooltip key="submit-tip"
                   title={allAnswered ? '' : '还有 ' + Math.max(0, totalCount - answeredCount) + ' 题未作答'}>
            <Button key="submit" type="primary" icon={<CheckCircleOutlined />}
                    disabled={!allAnswered}
                    onClick={finishAssignment}>提交作业</Button>
          </Tooltip>,
        ]}
      >
        {openAssignment && (submitResult ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              type="success"
              showIcon
              message="作业已提交"
              description={
                '共 ' + submitResult.total + ' 题：答对 ' + submitResult.correct
                + ' 题，答错 ' + submitResult.wrong + ' 题。'
                + (submitResult.wrong > 0
                  ? '答错的题已进入错题本，并会安排后续复习。'
                  : '全部答对，继续保持。')
              }
            />
            <Text type="secondary">
              「完成作业」和「全部答对」是两件事：只要每题都作答过就能提交；
              答错的题仍会按正常流程进入错题与复习。
            </Text>
          </Space>
        ) : (
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="课程">{openAssignment.course_title || '—'}</Descriptions.Item>
              <Descriptions.Item label="截止时间">
                {openAssignment.due_date || '未设置'}
              </Descriptions.Item>
            </Descriptions>
            {openAssignment.description && <Paragraph>{openAssignment.description}</Paragraph>}
            <Alert
              type="info"
              showIcon
              message="作答由后端判分，交卷前不会显示标准答案"
            />
            <Alert
              type={allAnswered ? 'success' : 'warning'}
              showIcon
              message={'已作答 ' + answeredCount + ' / ' + totalCount + ' 题'}
              description={allAnswered
                ? '已全部作答，可以提交作业。'
                : '还有 ' + Math.max(0, totalCount - answeredCount) + ' 题未作答，全部作答后才能提交。'}
            />
            {(openAssignment.questions || []).map((q: AssignmentQuestion, idx: number) => {
              const r = results[q.id];
              return (
                <Card key={q.id} size="small" type="inner" title={`第 ${idx + 1} 题`}>
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Text>{questionText(q)}</Text>
                    <Radio.Group
                      value={answers[q.id]}
                      disabled={!!r}
                      onChange={(e) => setAnswers((p) => ({ ...p, [q.id]: e.target.value }))}
                    >
                      <Space direction="vertical">
                        {(q.options || []).map((o) => (
                          <Radio key={o.id} value={o.id}>{o.id}. {o.text}</Radio>
                        ))}
                      </Space>
                    </Radio.Group>
                    {!r ? (
                      <Button size="small" type="primary" loading={submitting === q.id}
                              onClick={() => submitOne(q)}>提交本题</Button>
                    ) : (
                      <Alert
                        type={r.is_correct ? 'success' : 'error'}
                        showIcon
                        message={r.is_correct ? '回答正确' : '回答错误'}
                        description={r.explanation}
                      />
                    )}
                  </Space>
                </Card>
              );
            })}
          </Space>
        ))}
      </Modal>

      <Modal
        open={!!plansCourse}
        title={plansCourse ? `《${plansCourse.title}》已发布的教案` : '教案'}
        onCancel={() => { setPlansCourse(null); setLessonPlans(null); }}
        footer={null}
        width={760}
      >
        {lessonPlans === null ? <Spin /> : lessonPlans.length === 0 ? (
          <Empty description="老师还没有发布教案（草稿不会提前显示）" />
        ) : (
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            {lessonPlans.map((p: any) => (
              <Card
                key={p.id}
                size="small"
                title={<Space><Text strong>{p.title}</Text>
                  {p.topic && <Tag>{p.topic}</Tag>}
                  {p.hours ? <Tag>{p.hours} 课时</Tag> : null}
                  {p.version ? <Tag color="blue">v{p.version}</Tag> : null}</Space>}
                extra={p.published_at ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    发布于 {String(p.published_at).slice(0, 10)}
                  </Text>
                ) : null}
              >
                <LessonPlanView plan={p} />
              </Card>
            ))}
          </Space>
        )}
      </Modal>

      {/* 课程资料与知识库：学生可见范围 + 真实建库状态 */}
      <Modal
        open={!!materialsCourse}
        title={materialsCourse ? '《' + materialsCourse.title + '》课程资料与知识库' : '课程资料'}
        onCancel={() => { setMaterialsCourse(null); setMaterials(null); setMaterialsKb(null); }}
        footer={null}
        width={720}
      >
        {materialsLoading ? <Spin /> : (
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            <Alert
              type={materialsKb?.available ? 'success' : 'warning'}
              showIcon
              message={materialsKb?.available
                ? '课程知识库可用（已收录 ' + (materialsKb?.document_count ?? 0) + ' 份文档）'
                : '课程知识库当前不可用'}
              description={materialsKb?.available
                ? '在「学习助手」里基于本课程资料提问时，回答会引用这里的文件与原文片段。'
                : (materialsKb?.error || '老师还没有上传资料，或资料尚未建库完成。')}
            />
            {materials === null ? <Spin /> : materials.length === 0 ? (
              <Empty description="本课程还没有对学生可见的资料" />
            ) : (
              <List
                dataSource={materials}
                renderItem={(m: any) => (
                  <List.Item>
                    <List.Item.Meta
                      title={
                        <Space wrap>
                          <FileTextOutlined />
                          <Text>{m.filename}</Text>
                          <Tag>{m.status === 'ready' ? '已就绪' : (m.status || '处理中')}</Tag>
                        </Space>
                      }
                      description={
                        <Space wrap size={12}>
                          {m.size_bytes != null && (
                            <Text type="secondary">{Math.round(m.size_bytes / 1024)} KB</Text>
                          )}
                          <Text type="secondary">
                            {m.visibility === 'students' ? '学生可见' : '仅教师可见'}
                          </Text>
                          {m.created_at && (
                            <Text type="secondary">上传于 {String(m.created_at).slice(0, 10)}</Text>
                          )}
                        </Space>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Space>
        )}
      </Modal>

      {/* 个人资料知识库：与"教师课程资料"区分开，入口仍然可达 */}
      <Card
        size="small"
        style={{ marginTop: 12 }}
        title={<Space><DatabaseOutlined />个人资料知识库</Space>}
        extra={
          <Button size="small" type="primary" onClick={() => navigate('/knowledge')}>
            管理我的资料
          </Button>
        }
      >
        <Text type="secondary" style={{ fontSize: 12 }}>
          这里是你个人上传/维护的资料知识库，与上面老师课程的「课程资料」是两套：
          课程资料由任课老师维护、只对本课程学生可见；个人资料仅你自己使用。
        </Text>
      </Card>
    </div>
  );
};

/** 学生视角的教案展示：按教案结构渲染（空项不显示，不填充占位内容） */
const LessonPlanView: React.FC<{ plan: any }> = ({ plan }) => {
  const c = plan?.content || {};
  const blocks: Array<[string, any]> = [
    ['教学目标', c.objectives],
    ['教学重点', c.key_points],
    ['教学难点', c.difficulties],
  ];
  return (
    <Space direction="vertical" style={{ width: '100%' }} size={8}>
      {blocks.map(([label, items]) => (
        Array.isArray(items) && items.length > 0 ? (
          <div key={label}>
            <Text strong>{label}</Text>
            <ul style={{ margin: '4px 0 0 18px' }}>
              {items.map((it: any, i: number) => <li key={i}>{String(it)}</li>)}
            </ul>
          </div>
        ) : null
      ))}
      {Array.isArray(c.steps) && c.steps.length > 0 && (
        <div>
          <Text strong>教学步骤与时间分配</Text>
          <ul style={{ margin: '4px 0 0 18px' }}>
            {c.steps.map((s: any, i: number) => (
              <li key={i}>
                {s.name || '环节'}{s.minutes ? `（${s.minutes} 分钟）` : ''}
                {s.content ? `：${s.content}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      {Array.isArray(c.homework) && c.homework.length > 0 && (
        <div>
          <Text strong>课后练习</Text>
          <ol style={{ margin: '4px 0 0 18px' }}>
            {c.homework.map((h: any, i: number) => (
              <li key={i}>{h.question || String(h)}</li>
            ))}
          </ol>
        </div>
      )}
      {c.evidence_note && (
        <Alert type="info" showIcon message={c.evidence_note} />
      )}
      <details>
        <summary style={{ cursor: 'pointer', fontSize: 12, color: '#64748b' }}>查看完整教案（Markdown）</summary>
        <MarkdownBlock text={`# ${plan?.title || ''}

` + renderPlanMarkdown(plan)} />
      </details>
    </Space>
  );
};

/** 把教案结构转成 Markdown（学生端"完整教案"折叠区使用） */
const renderPlanMarkdown = (plan: any): string => {
  const c = plan?.content || {};
  const lines: string[] = [];
  const section = (title: string, items: any[], ordered = false) => {
    if (!Array.isArray(items) || items.length === 0) return;
    lines.push(`## ${title}`);
    items.forEach((it, i) => {
      const text = typeof it === 'string' ? it
        : it?.question ? it.question : it?.name ? `${it.name}${it.minutes ? `（${it.minutes} 分钟）` : ''}${it.content ? `：${it.content}` : ''}` : JSON.stringify(it);
      lines.push(ordered ? `${i + 1}. ${text}` : `- ${text}`);
    });
    lines.push('');
  };
  section('教学目标', c.objectives);
  section('教学重点', c.key_points);
  section('教学难点', c.difficulties);
  section('教学步骤与时间分配', c.steps);
  section('例题与活动', c.examples, true);
  section('课后练习', c.homework, true);
  if (Array.isArray(plan?.evidence) && plan.evidence.length) {
    lines.push('## 依据的课程资料');
    plan.evidence.forEach((e: any) => lines.push(`- ${e.file_name || '—'}${e.page ? ` 第${e.page}页` : ''}`));
  }
  return lines.join('\n');
};

export default StudentCourses;
