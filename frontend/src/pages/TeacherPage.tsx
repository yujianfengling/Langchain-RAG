import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Form, Input, Menu, Modal, Select, Space, Tag, Typography, message,
} from 'antd';
import {
  BarChartOutlined, BookOutlined, DashboardOutlined, FileTextOutlined,
  FolderOpenOutlined, PlusOutlined, ReloadOutlined, TeamOutlined, UserOutlined,
} from '@ant-design/icons';
import {
  TeacherCourse, createCourse, describeTeacherError, isForbiddenError, listTeacherCourses,
} from '../api/teacher';
import { isLoggedIn } from '../api/auth';
import { AUTH_CHANGED_EVENT } from '../components/UserAuth';
import { EmptyBlock, ErrorAlert, LoadingBlock, PermissionAlert } from '../components/Teacher/common';
import DashboardPanel, { DashboardNavTarget } from '../components/Teacher/DashboardPanel';
import ClassesPanel from '../components/Teacher/ClassesPanel';
import MaterialsPanel from '../components/Teacher/MaterialsPanel';
import LessonPlansPanel from '../components/Teacher/LessonPlansPanel';
import AssignmentsPanel from '../components/Teacher/AssignmentsPanel';
import AnalyticsPanel from '../components/Teacher/AnalyticsPanel';
import StudentsPanel from '../components/Teacher/StudentsPanel';
import { useIsNarrow } from '../hooks/useIsNarrow';

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

type PanelKey = 'dashboard' | 'classes' | 'materials' | 'plans' | 'assignments' | 'analytics' | 'students';

export interface TeacherPageProps {
  /** 可选：外部路由跳转钩子（不传则仅在本页内切换） */
  onNavigate?: (path: string) => void;
}

const MENU_ITEMS = [
  { key: 'dashboard', icon: <DashboardOutlined />, label: '工作台' },
  { key: 'classes', icon: <TeamOutlined />, label: '班级管理' },
  { key: 'materials', icon: <FolderOpenOutlined />, label: '课程资料' },
  { key: 'plans', icon: <FileTextOutlined />, label: '教案' },
  { key: 'assignments', icon: <BookOutlined />, label: '作业与练习' },
  { key: 'analytics', icon: <BarChartOutlined />, label: '学情分析' },
  // 学生画像：逐个学生看（与「学情分析」的班级聚合互补）；不依赖具体课程，故不需要先选课
  { key: 'students', icon: <UserOutlined />, label: '学生画像' },
];

/**
 * 教师工作台
 *
 * 组织方式：工作台 → 班级管理 → 课程资料 → 教案 → 作业 → 学情
 * 权限：全部由后端判定；这里的展示判断不构成安全边界。
 */
const TeacherPage: React.FC<TeacherPageProps> = () => {
  const isNarrow = useIsNarrow();
  const [loggedIn, setLoggedIn] = useState(isLoggedIn());
  const [activeKey, setActiveKey] = useState<PanelKey>('dashboard');

  const [courses, setCourses] = useState<TeacherCourse[]>([]);
  const [coursesLoading, setCoursesLoading] = useState(false);
  const [coursesError, setCoursesError] = useState<string | null>(null);
  const [coursesForbidden, setCoursesForbidden] = useState<string | null>(null);
  const [courseId, setCourseId] = useState<string | undefined>();

  const [focus, setFocus] = useState<{
    classId?: string; planId?: string; knowledgePointId?: string;
  }>({});

  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<{ title: string; description?: string; subject_name?: string }>();

  // 登录态变化时同步（与 StudentPage 保持一致的做法）
  useEffect(() => {
    const sync = () => setLoggedIn(isLoggedIn());
    window.addEventListener('storage', sync);
    window.addEventListener(AUTH_CHANGED_EVENT, sync);
    return () => {
      window.removeEventListener('storage', sync);
      window.removeEventListener(AUTH_CHANGED_EVENT, sync);
    };
  }, []);

  const loadCourses = useCallback(async () => {
    if (!isLoggedIn()) {
      setCourses([]);
      return;
    }
    setCoursesLoading(true);
    setCoursesError(null);
    setCoursesForbidden(null);
    try {
      const res = await listTeacherCourses();
      const list = res?.data?.courses || [];
      setCourses(list);
      setCourseId((prev) => (prev && list.some((c) => c.id === prev) ? prev : list[0]?.id));
    } catch (e) {
      const f = describeTeacherError(e, '加载课程失败');
      if (isForbiddenError(e)) setCoursesForbidden(f.message);
      else setCoursesError(f.message);
    } finally {
      setCoursesLoading(false);
    }
  }, []);

  useEffect(() => { loadCourses(); }, [loadCourses, loggedIn]);

  const handleCreateCourse = async (values: {
    title: string; description?: string; subject_name?: string;
  }) => {
    setCreating(true);
    try {
      const res = await createCourse({
        title: values.title.trim(),
        description: values.description,
        subject_name: values.subject_name?.trim() || undefined,
      });
      const created = res?.data;
      message.success(`课程《${created?.title || values.title}》已创建`);
      setCreateOpen(false);
      form.resetFields();
      await loadCourses();
      if (created?.id) setCourseId(created.id);
      setActiveKey('classes');
    } catch (e) {
      message.error(describeTeacherError(e, '创建课程失败').message);
    } finally {
      setCreating(false);
    }
  };

  const handleDashboardNav = (target: DashboardNavTarget) => {
    if (target.courseId) setCourseId(target.courseId);
    setFocus({
      classId: target.classId,
      planId: target.planId,
      knowledgePointId: target.knowledgePointId,
    });
    setActiveKey(target.key);
  };

  const currentCourse = useMemo(
    () => courses.find((c) => c.id === courseId) || null,
    [courses, courseId],
  );

  // 工作台与学生画像都是跨课程的，不强制先选课程
  const courseRequired = activeKey !== 'dashboard' && activeKey !== 'students';
  const needCourse = courseRequired && !courseId;

  const renderPanel = () => {
    if (needCourse) {
      return (
        <Card size="small">
          <EmptyBlock description="请先选择或创建一门课程">
            <Space>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                新建课程
              </Button>
            </Space>
          </EmptyBlock>
        </Card>
      );
    }
    switch (activeKey) {
      case 'classes':
        return (
          <ClassesPanel
            key={`classes-${courseId}`}
            courseId={courseId!}
            courseTitle={currentCourse?.title}
            focusClassId={focus.classId}
            onBack={() => setActiveKey('dashboard')}
          />
        );
      case 'materials':
        return (
          <MaterialsPanel
            key={`materials-${courseId}`}
            courseId={courseId!}
            courseTitle={currentCourse?.title}
            onBack={() => setActiveKey('dashboard')}
          />
        );
      case 'plans':
        return (
          <LessonPlansPanel
            key={`plans-${courseId}-${focus.planId || ''}`}
            courseId={courseId!}
            courseTitle={currentCourse?.title}
            focusPlanId={focus.planId}
            onBack={() => setActiveKey('dashboard')}
          />
        );
      case 'assignments':
        return (
          <AssignmentsPanel
            key={`assignments-${courseId}`}
            courseId={courseId!}
            courseTitle={currentCourse?.title}
            onBack={() => setActiveKey('dashboard')}
          />
        );
      case 'analytics':
        return (
          <AnalyticsPanel
            key={`analytics-${courseId}-${focus.classId || ''}-${focus.knowledgePointId || ''}`}
            courseId={courseId!}
            courseTitle={currentCourse?.title}
            focusClassId={focus.classId}
            focusKnowledgePointId={focus.knowledgePointId}
            onBack={() => setActiveKey('dashboard')}
          />
        );
      case 'students':
        return <StudentsPanel onBack={() => setActiveKey('dashboard')} />;
      case 'dashboard':
      default:
        return <DashboardPanel onOpen={handleDashboardNav} />;
    }
  };

  if (!loggedIn) {
    return (
      <div style={{ padding: 24, maxWidth: 900, margin: '0 auto' }}>
        <Title level={4}>教师工作台</Title>
        <Card>
          <Alert
            type="info"
            showIcon
            message="请先登录"
            description="课程、班级、资料、教案、作业与学情都归属到具体账号，请先在「登录 / 注册」中登录教师账号。"
          />
        </Card>
      </div>
    );
  }

  return (
    <div style={{ padding: 24, maxWidth: 1280, margin: '0 auto' }}>
      {/* 顶部：课程选择与新建 */}
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        <Space size={12} wrap>
          <Title level={4} style={{ margin: 0 }}>教师工作台</Title>
          {currentCourse && <Tag color="blue">当前课程：{currentCourse.title}</Tag>}
        </Space>
        <Space wrap>
          <Select
            style={{ width: 240 }}
            placeholder={courses.length ? '选择课程' : '还没有课程'}
            value={courseId}
            onChange={(v) => {
              setCourseId(v);
              setFocus({});
            }}
            loading={coursesLoading}
            options={courses.map((c) => ({ value: c.id, label: c.title }))}
            notFoundContent="还没有课程，请先新建"
          />
          <Button icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建课程</Button>
          <Button icon={<ReloadOutlined />} onClick={loadCourses} loading={coursesLoading}>刷新</Button>
        </Space>
      </Space>

      {coursesForbidden && (
        <div style={{ marginBottom: 12 }}>
          <PermissionAlert message={coursesForbidden} />
        </div>
      )}
      {coursesError && (
        <div style={{ marginBottom: 12 }}>
          <ErrorAlert message={coursesError} onRetry={loadCourses} />
        </div>
      )}
      {!coursesLoading && !coursesError && courses.length === 0 && activeKey === 'dashboard' && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="你还没有课程"
          description="点击右上角「新建课程」创建第一门课程。创建课程后即可建班级、发班级邀请码、上传资料、生成教案与布置作业。"
          action={<Button size="small" type="primary" onClick={() => setCreateOpen(true)}>新建课程</Button>}
        />
      )}

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {/* 左侧导航 */}
        <Card size="small" style={{ width: isNarrow ? '100%' : 168, minWidth: isNarrow ? 0 : 168 }}>
          <Menu
            mode="inline"
            selectedKeys={[activeKey]}
            items={MENU_ITEMS}
            onClick={({ key }) => { setActiveKey(key as PanelKey); setFocus({}); }}
            style={{ border: 'none' }}
          />
        </Card>

        {/* 右侧内容 */}
        <div style={{ flex: 1, minWidth: isNarrow ? 0 : 480 }}>
          {coursesLoading && activeKey === 'dashboard' && courses.length === 0
            ? <LoadingBlock tip="正在加载课程…" />
            : renderPanel()}
        </div>
      </div>

      <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 16, marginBottom: 0 }}>
        所有权限判断以后端为准：这里隐藏或显示的按钮只影响展示，不构成安全边界。
        「学生加入课程」由学生在「我的学习」页输入班级邀请码完成。
      </Paragraph>

      {/* 新建课程 */}
      <Modal
        title="新建课程"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); form.resetFields(); }}
        onOk={() => form.submit()}
        okText="创建"
        cancelText="取消"
        confirmLoading={creating}
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="创建课程会同时为该课程创建一个独立知识库（学科），用于存放课程资料与检索依据。"
        />
        <Form form={form} layout="vertical" onFinish={handleCreateCourse}>
          <Form.Item
            label="课程名称"
            name="title"
            rules={[{ required: true, message: '请输入课程名称' }]}
          >
            <Input placeholder="例如：数据结构与算法" maxLength={80} />
          </Form.Item>
          <Form.Item label="课程简介（可选）" name="description">
            <TextArea rows={3} placeholder="简要说明课程内容与适用对象" maxLength={500} />
          </Form.Item>
          <Form.Item
            label="知识库显示名（可选）"
            name="subject_name"
            extra="不填则使用课程名称作为知识库（学科）名称。"
          >
            <Input placeholder="例如：数据结构" maxLength={60} />
          </Form.Item>
        </Form>
        <Text type="secondary" style={{ fontSize: 12 }}>
          创建课程需要教师身份，但创建课程本身不会授予身份；教师身份只能由管理员开通，
          或凭教师邀请码在「设置 → 教师身份」自助开通。课程会记录创建者，可审计、可追溯。
        </Text>
      </Modal>
    </div>
  );
};

export default TeacherPage;
