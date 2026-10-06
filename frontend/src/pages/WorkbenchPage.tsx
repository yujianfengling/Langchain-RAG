import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Alert, Button, Card, Input, Select, Space, Spin, Tag, Typography, message } from 'antd';
import {
  BookOutlined, DatabaseOutlined, BulbOutlined, FormOutlined, ScheduleOutlined,
  LoginOutlined, ReloadOutlined, ArrowRightOutlined,
} from '@ant-design/icons';
import ChatBox, { ChatPreset } from '../components/ChatBox';
import { useCourse } from '../context/CourseContext';
import { useIsNarrow } from '../hooks/useIsNarrow';
import { joinCourseByCode } from '../api/studentCourses';
import { getHome } from '../api/student';
import { isLoggedIn } from '../api/auth';
import UserAuth from '../components/UserAuth';

const { Title, Text, Paragraph } = Typography;

/**
 * 学习工作台（2026-10-06 改版）：把“学习首页”和“学习助手”合并成一屏。
 *
 * 目标：第一次使用的人打开就知道在哪里提问。
 * - 顶部：当前课程 + 课程资料入口（真正的课程名，来自 /student/courses）
 * - 主区：提问输入（复用已验证的 ChatBox：RAG 回答、真实引用、语音、图片）
 * - 快捷动作：讲懂一个知识点 / 练一道题 / 安排今天（直接走真实流程）
 * 状态明确区分：加载中 / 加载失败 / 无课程 / 无权限（绝不吞成空列表）。
 */
const WorkbenchPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const ctx = useCourse();
  const isNarrow = useIsNarrow();
  const [preset, setPreset] = useState<ChatPreset | undefined>(undefined);
  const [todo, setTodo] = useState<{ tasks: number; reviews: number; assignments: number } | null>(null);
  const [joinCode, setJoinCode] = useState('');
  const [joining, setJoining] = useState(false);

  const loggedIn = isLoggedIn();
  const subjectId = ctx.currentSubjectId;
  const isCourse = ctx.isCourseSubject(subjectId);

  const loadTodo = useCallback(async () => {
    const sid = ctx.currentSubjectId;
    if (!sid) { setTodo(null); return; }
    try {
      const home: any = await getHome(sid);
      const d = home && home.data ? home.data : {};
      setTodo({
        tasks: (d.today_tasks || []).length + (d.overdue_tasks || []).length,
        reviews: (d.pending_reviews || []).length,
        assignments: 0,
      });
    } catch (e) { setTodo(null); }
  }, [ctx.currentSubjectId]);

  useEffect(() => { loadTodo(); }, [loadTodo]);

  // 深链预设（从"作业与复习"进入）：先切到目标课程，再应用模式
  const consumedRef = React.useRef<string>('');
  useEffect(() => {
    const incoming = (location.state as any)?.preset;
    if (!incoming) return;
    const stamp = JSON.stringify(incoming);
    if (consumedRef.current === stamp) return;
    consumedRef.current = stamp;
    if (incoming.subjectId && incoming.subjectId !== ctx.currentSubjectId) {
      const c = ctx.courses.find((x) => x.subject_id === incoming.subjectId);
      if (c) ctx.select(c.course_id);
      else ctx.selectSubject(incoming.subjectId);
    }
    setPreset({
      mode: incoming.mode, input: incoming.input, taskId: incoming.taskId,
      autoStart: incoming.autoStart, review: incoming.review,
    });
    navigate(location.pathname, { replace: true, state: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state]);

  const handleJoin = async () => {
    const code = joinCode.trim();
    if (!code) { message.warning('请输入教师提供的班级邀请码'); return; }
    setJoining(true);
    try {
      await joinCourseByCode(code);
      message.success('已加入课程');
      setJoinCode('');
      await ctx.refresh();
    } catch (e: any) {
      message.error((e && e.response && e.response.data && e.response.data.message) || '加入失败，请核对邀请码');
    } finally { setJoining(false); }
  };

  const quick = (p: ChatPreset) => setPreset(p);

  // ---------- 未登录 ----------
  if (!loggedIn) {
    return (
      <div style={{ maxWidth: 720, margin: '48px auto', padding: 16 }}>
        <Card>
          <Title level={4} style={{ marginTop: 0 }}>开始学习</Title>
          <Paragraph type="secondary">
            登录后即可按课程提问、核对资料原文、完成作业与复习。登录入口在左侧（手机端在右上角）。
          </Paragraph>
          <UserAuth />
        </Card>
      </div>
    );
  }

  // ---------- 加载中 / 失败 / 无权限 ----------
  if (ctx.status === 'loading') {
    return <div style={{ display: 'flex', justifyContent: 'center', padding: 64 }}><Spin tip="正在加载课程…" /></div>;
  }
  if (ctx.status === 'error') {
    return (
      <div style={{ maxWidth: 720, margin: '40px auto', padding: 16 }}>
        <Alert
          type="error" showIcon message="课程列表加载失败" description={ctx.error || '请检查网络后重试'}
          action={<Button icon={<ReloadOutlined />} onClick={() => ctx.refresh()}>重试</Button>}
        />
      </div>
    );
  }

  const header = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
      <BookOutlined style={{ color: '#0f766e' }} />
      <Text strong style={{ fontSize: 15 }}>
        {ctx.current ? ctx.current.title : (isCourse ? '' : '个人资料')}
      </Text>
      {ctx.current && <Tag color="blue">{ctx.current.class_name || '我的班级'}</Tag>}
      {ctx.current && (
        <Button size="small" icon={<DatabaseOutlined />}
                onClick={() => navigate('/student/courses?course=' + ctx.current!.course_id + '&tab=materials')}>
          课程资料
        </Button>
      )}
      <div style={{ marginLeft: 'auto', minWidth: 200 }}>
        <Select
          size="middle" style={{ width: '100%' }} value={ctx.current ? ctx.current.course_id : undefined}
          placeholder="切换课程"
          options={[
            ...ctx.courses.map((c) => ({ value: c.course_id, label: c.title })),
            ...ctx.personalSubjects.map((s) => ({ value: 'personal:' + s.subject_id, label: s.name + '（个人资料）' })),
          ]}
          onChange={(v) => {
            if (String(v).startsWith('personal:')) ctx.selectSubject(String(v).slice(9));
            else ctx.select(v);
          }}
        />
      </div>
    </div>
  );

  // ---------- 有多门课程但还没选：给一个简洁的课程选择（不静默替用户选课） ----------
  if (ctx.courses.length > 1 && !subjectId) {
    return (
      <div style={{ maxWidth: 720, margin: '32px auto', padding: 16 }}>
        <Card>
          <Title level={4} style={{ marginTop: 0 }}>选择要学习的课程</Title>
          <Paragraph type="secondary">每门课的提问、作业与复习相互独立，选定后可在顶部随时切换。</Paragraph>
          <Space direction="vertical" style={{ width: '100%' }} size={8}>
            {ctx.courses.map((c) => (
              <Button key={c.course_id} block size="large" style={{ textAlign: 'left', height: 'auto', padding: '10px 14px' }}
                      onClick={() => ctx.select(c.course_id)}>
                <Text strong>{c.title}</Text>
                <div><Text type="secondary" style={{ fontSize: 12 }}>
                  {c.class_name || '我的班级'} · 授课：{c.teacher_name || '—'}</Text></div>
              </Button>
            ))}
          </Space>
          {ctx.personalSubjects.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>个人资料：</Text>
              {ctx.personalSubjects.map((s) => (
                <Button key={s.subject_id} size="small" style={{ marginLeft: 8 }} onClick={() => ctx.selectSubject(s.subject_id)}>
                  {s.name}
                </Button>
              ))}
            </div>
          )}
        </Card>
      </div>
    );
  }

  // ---------- 无课程：只给加入入口 + 个人资料次级入口 ----------
  if (ctx.courses.length === 0 && !subjectId) {
    return (
      <div style={{ maxWidth: 720, margin: '32px auto', padding: 16 }}>
        <Card>
          <Title level={4} style={{ marginTop: 0 }}>还没有加入课程</Title>
          <Paragraph type="secondary">向老师要一个班级邀请码，加入后就能按课程资料提问、做作业与复习。</Paragraph>
          <Space.Compact style={{ width: '100%' }}>
            <Input placeholder="输入班级邀请码，例如 PL7F93" value={joinCode}
                   onChange={(e) => setJoinCode(e.target.value)} onPressEnter={handleJoin} />
            <Button type="primary" icon={<LoginOutlined />} loading={joining} onClick={handleJoin}>加入课程</Button>
          </Space.Compact>
          {ctx.forbidden && <Alert style={{ marginTop: 12 }} type="warning" showIcon message={ctx.forbidden} />}
          {ctx.personalSubjects.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <Text type="secondary">或者先在自己的个人资料里学习：</Text>
              <div style={{ marginTop: 8 }}>
                {ctx.personalSubjects.map((s) => (
                  <Button key={s.subject_id} style={{ marginRight: 8 }} onClick={() => ctx.selectSubject(s.subject_id)}>
                    {s.name}
                  </Button>
                ))}
              </div>
            </div>
          )}
        </Card>
      </div>
    );
  }

  const hero = (
    <div style={{ marginBottom: 10 }}>
      <Title level={4} style={{ margin: 0, fontSize: 19 }}>想问点什么？</Title>
      <Paragraph type="secondary" style={{ margin: '4px 0 8px' }}>
        输入问题，或粘贴一道不会的题。
      </Paragraph>
      <Space size={8} wrap>
        <Button icon={<BulbOutlined />} onClick={() => quick({ mode: 'chat', input: '请用简单的语言讲懂：', autoStart: false })}>
          讲懂一个知识点
        </Button>
        <Button icon={<FormOutlined />} onClick={() => quick({ mode: 'quiz', input: '根据本课程资料出一道练习题', autoStart: true })}>
          练一道题
        </Button>
        <Button icon={<ScheduleOutlined />} onClick={() => quick({ mode: 'agent', input: '帮我安排今天的学习', autoStart: true })}>
          安排今天
        </Button>
        {todo && (todo.tasks + todo.reviews > 0) && (
          <Button type="link" onClick={() => navigate('/student/todo')}>
            今天有 {todo.tasks + todo.reviews} 项待办 <ArrowRightOutlined />
          </Button>
        )}
      </Space>
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: isNarrow ? 'calc(100vh - 52px)' : '100vh', minHeight: 0 }}>
      <div style={{ padding: '14px 16px 0' }}>
        {header}
        {!isCourse && subjectId && (
          <Alert type="info" showIcon style={{ marginBottom: 8 }}
                 message="当前是个人资料（不是教师课程资料）" />
        )}
        {ctx.forbidden && <Alert type="warning" showIcon style={{ marginBottom: 8 }} message={ctx.forbidden} />}
      </div>
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <ChatBox
          subjectId={subjectId}
          subjects={ctx.subjects}
          isCourseSubject={isCourse}
          preset={preset}
          onPresetConsumed={() => setPreset(undefined)}
          headerSlot={hero}
        />
      </div>
    </div>
  );
};

export default WorkbenchPage;
