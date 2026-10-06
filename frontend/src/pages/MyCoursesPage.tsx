import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Card, Collapse, Empty, List, Space, Spin, Table, Tabs, Tag, Typography,
} from 'antd';
import {
  ArrowLeftOutlined, BookOutlined, FileTextOutlined, FormOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { useCourse, CourseItem } from '../context/CourseContext';
import {
  listCourseLessonPlans, listCourseMaterials, listMyAssignments,
} from '../api/studentCourses';
import { getMastery } from '../api/student';
import { MarkdownBlock } from '../components/Teacher/common';

const { Title, Text, Paragraph } = Typography;

const suggestionOf = (p: any): string => {
  const level = p?.latest_view?.latest_mastery_level ?? p?.mastery_level;
  const wrong = p?.latest_view?.latest_independent_wrong ?? (p?.evidence_breakdown?.independent_wrong || 0);
  if (level === null || level === undefined) return '待评估（还没有足够作答证据）';
  if (wrong > 0 && level < 0.4) return '先复习该知识点的错题，再做一次小测';
  if (level < 0.6) return '再练 1–2 道同类题巩固';
  if (level < 0.8) return '间隔一天后复测一次';
  return '保持，按计划复习';
};

const MyCoursesPage: React.FC = () => {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const ctx = useCourse();
  const courseId = params.get('course') || '';
  const tab = params.get('tab') || 'materials';
  const course = ctx.courses.find((c) => c.course_id === courseId) || null;

  if (ctx.status === 'loading') return <div style={{ display: 'flex', justifyContent: 'center', padding: 64 }}><Spin tip="正在加载课程…" /></div>;
  if (ctx.status === 'error') {
    return <div style={{ maxWidth: 720, margin: '40px auto', padding: 16 }}>
      <Alert type="error" showIcon message="课程列表加载失败" description={ctx.error || ''}
             action={<Button icon={<ReloadOutlined />} onClick={() => ctx.refresh()}>重试</Button>} />
    </div>;
  }

  if (!course) {
    return (
      <div style={{ maxWidth: 1040, margin: '0 auto', padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
          <Title level={4} style={{ margin: 0 }}>我的课程</Title>
          <Button type="text" icon={<ReloadOutlined />} style={{ marginLeft: 'auto' }} onClick={() => ctx.refresh()}>刷新</Button>
        </div>
        {ctx.forbidden && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={ctx.forbidden} />}
        {ctx.courses.length === 0 ? (
          <Empty description="还没有加入课程。请向老师要一个班级邀请码，在「开始学习」页加入。">
            <Button type="primary" onClick={() => navigate('/student')}>去加入课程</Button>
          </Empty>
        ) : (
          <List
            grid={{ gutter: 12, xs: 1, sm: 2, lg: 3 }}
            dataSource={ctx.courses}
            renderItem={(c) => (
              <List.Item>
                <Card hoverable size="small" onClick={() => setParams({ course: c.course_id, tab: 'materials' })}>
                  <Text strong style={{ fontSize: 15 }}>{c.title}</Text>
                  <div style={{ marginTop: 6 }}>
                    <Tag color="blue">{c.class_name || '我的班级'}</Tag>
                    <Tag>授课：{c.teacher_name || '—'}</Tag>
                  </div>
                  <Paragraph type="secondary" style={{ margin: '8px 0 0', fontSize: 12 }} ellipsis={{ rows: 2 }}>
                    {c.description || '进入课程查看资料、教案、作业与学习情况'}
                  </Paragraph>
                </Card>
              </List.Item>
            )}
          />
        )}
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 1040, margin: '0 auto', padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => setParams({})}>全部课程</Button>
        <BookOutlined style={{ color: '#0f766e' }} />
        <Title level={5} style={{ margin: 0 }}>{course.title}</Title>
        <Tag color="blue">{course.class_name || '我的班级'}</Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>授课：{course.teacher_name || '—'}</Text>
        <Button size="small" type="primary" style={{ marginLeft: 'auto' }}
                onClick={() => { ctx.select(course.course_id); navigate('/student'); }}>
          基于本课程资料提问
        </Button>
      </div>
      <Tabs
        activeKey={tab}
        onChange={(k) => setParams({ course: course.course_id, tab: k })}
        items={[
          { key: 'materials', label: '课程资料', children: <MaterialsTab course={course} /> },
          { key: 'plans', label: '教案', children: <PlansTab course={course} /> },
          { key: 'assignments', label: '作业', children: <AssignmentsTab course={course} /> },
          { key: 'progress', label: '学习情况', children: <ProgressTab course={course} /> },
        ]}
      />
    </div>
  );
};

/** 课程资料：如实说明“基于本课程资料”，不承诺限定单份文件（接口暂无此能力） */
const MaterialsTab: React.FC<{ course: CourseItem }> = ({ course }) => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const res: any = await listCourseMaterials(course.course_id);
      setData(res && res.data ? res.data : null);
    } catch (e: any) {
      setError((e && e.response && e.response.data && e.response.data.message) || '资料加载失败');
    } finally { setLoading(false); }
  }, [course.course_id]);
  useEffect(() => { load(); }, [load]);
  if (loading) return <Spin />;
  if (error) return <Alert type="error" showIcon message={error} action={<Button onClick={load}>重试</Button>} />;
  const materials = (data && data.materials) || [];
  const kb = (data && data.knowledge_base) || null;
  return (
    <div>
      <Alert type="info" showIcon style={{ marginBottom: 10 }}
             message={(kb && kb.available && kb.stats && kb.stats.status === 'ready')
               ? '课程知识库可用于检索（已收录 ' + (kb.document_count || 0) + ' 份文档）'
               : '课程知识库状态：' + ((kb && kb.stats && kb.stats.status) || (kb && kb.error) || '未知')}
             description="提问时使用「基于本课程资料」，回答会标注引用到的原文片段；接口暂不支持只限定某一份文件。" />
      {materials.length === 0 ? <Empty description="这门课还没有可查看的资料" /> : (
        <List
          size="small" dataSource={materials}
          renderItem={(m: any) => (
            <List.Item>
              <Space><FileTextOutlined style={{ color: '#0f766e' }} />
                <Text>{m.filename || m.name}</Text>
                <Tag>{m.status === 'ready' ? '可用' : (m.status || '处理中')}</Tag>
              </Space>
            </List.Item>
          )}
        />
      )}
    </div>
  );
};

const PlansTab: React.FC<{ course: CourseItem }> = ({ course }) => {
  const [plans, setPlans] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const res: any = await listCourseLessonPlans(course.course_id);
      const d = res && res.data ? res.data : {};
      setPlans(Array.isArray(d) ? d : (d.lesson_plans || d.plans || []));
    } catch (e: any) {
      setError((e && e.response && e.response.data && e.response.data.message) || '教案加载失败');
    } finally { setLoading(false); }
  }, [course.course_id]);
  useEffect(() => { load(); }, [load]);
  if (loading) return <Spin />;
  if (error) return <Alert type="error" showIcon message={error} action={<Button onClick={load}>重试</Button>} />;
  if (plans.length === 0) return <Empty description="老师还没有发布教案" />;
  return (
    <Collapse accordion items={plans.map((p: any, i: number) => ({
      key: String(p.id || i),
      label: <Space><Text strong>{p.title || '教案'}</Text>
        <Tag color={p.status === 'published' ? 'green' : 'default'}>{p.status === 'published' ? '已发布' : (p.status || '草稿')}</Tag>
        {p.published_at && <Text type="secondary" style={{ fontSize: 12 }}>{String(p.published_at).slice(0, 10)}</Text>}</Space>,
      children: <MarkdownBlock text={p.content || p.body || p.markdown || '（无内容）'} />,
    }))} />
  );
};

const AssignmentsTab: React.FC<{ course: CourseItem }> = ({ course }) => {
  const navigate = useNavigate();
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const res: any = await listMyAssignments();
      const all = (res && res.data && res.data.assignments) || [];
      setItems(all.filter((a: any) => a.course_id === course.course_id));
    } catch (e: any) {
      setError((e && e.response && e.response.data && e.response.data.message) || '作业加载失败');
    } finally { setLoading(false); }
  }, [course.course_id]);
  useEffect(() => { load(); }, [load]);
  if (loading) return <Spin />;
  if (error) return <Alert type="error" showIcon message={error} action={<Button onClick={load}>重试</Button>} />;
  if (items.length === 0) return <Empty description="这门课还没有作业" />;
  return (
    <List
      dataSource={items}
      renderItem={(a: any) => {
        const done = Object.keys(a.results || {}).length;
        const total = (a.questions || []).length || a.question_count || 0;
        return (
          <List.Item actions={[
            <Button key="go" type="primary" ghost icon={<FormOutlined />}
                    onClick={() => navigate('/student/assignments?assignment=' + a.id)}>去作答</Button>,
          ]}>
            <List.Item.Meta
              title={<Space><Text strong>{a.title}</Text>
                <Tag color={a.status === 'published' ? 'green' : 'default'}>{a.status === 'published' ? '进行中' : '已关闭'}</Tag></Space>}
              description={<Text type="secondary" style={{ fontSize: 12 }}>
                已完成 {done}/{total}{a.due_date ? ' · 截止 ' + String(a.due_date).slice(0, 10) : ''}</Text>}
            />
          </List.Item>
        );
      }}
    />
  );
};

const ProgressTab: React.FC<{ course: CourseItem }> = ({ course }) => {
  const [points, setPoints] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [basis, setBasis] = useState<any[]>([]);
  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const res: any = await getMastery(course.subject_id);
      const d = res && res.data ? res.data : {};
      setPoints(Array.isArray(d) ? d : (d.points || []));
    } catch (e: any) {
      setError((e && e.response && e.response.data && e.response.data.message) || '学习情况加载失败');
    } finally { setLoading(false); }
  }, [course.subject_id]);
  useEffect(() => { load(); }, [load]);
  if (loading) return <Spin />;
  if (error) return <Alert type="error" showIcon message={error} action={<Button onClick={load}>重试</Button>} />;
  if (points.length === 0) return <Empty description="这门课还没有作答证据，先做一次练习就会出现学习情况" />;
  return (
    <div>
      <Table
        rowKey={(r: any) => r.knowledge_point_id || r.knowledge_point}
        size="small" pagination={false} dataSource={points}
        columns={[
          { title: '知识点', dataIndex: 'knowledge_point' },
          { title: '当前情况', render: (_: any, r: any) => {
            const level = r.latest_view ? r.latest_view.latest_mastery_level : r.mastery_level;
            const n = r.latest_view ? r.latest_view.latest_evidence_count : r.evidence_count;
            if (level === null || level === undefined) return <Tag>待评估</Tag>;
            return <Tag color={level >= 0.8 ? 'green' : level >= 0.6 ? 'blue' : 'orange'}>
              {Math.round(level * 100)}%（{n || 0} 次作答）</Tag>;
          } },
          { title: '建议行动', render: (_: any, r: any) => <Text style={{ fontSize: 13 }}>{suggestionOf(r)}</Text> },
        ]}
      />
      <div style={{ marginTop: 10 }}>
        <Button size="small" type="link" onClick={() => setBasis(basis.length ? [] : points)}>
          {basis.length ? '收起计算依据' : '查看计算依据（按最新一次有效作答）'}
        </Button>
        {basis.length > 0 && (
          <List size="small" dataSource={basis} renderItem={(r: any) => (
            <List.Item>
              <Text style={{ fontSize: 12 }} type="secondary">
                {r.knowledge_point}：{r.reason || '按作答记录累计'}
              </Text>
            </List.Item>
          )} />
        )}
      </div>
    </div>
  );
};

export default MyCoursesPage;
