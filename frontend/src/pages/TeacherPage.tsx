import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Form, Input, Menu, Modal, Select, Space, Tag, Typography, message,
} from 'antd';
import {
  BarChartOutlined, BookOutlined, PlusOutlined, ReloadOutlined, SolutionOutlined,
  UserOutlined,
} from '@ant-design/icons';
import {
  TeacherCourse, createCourse, describeTeacherError, isForbiddenError, listTeacherCourses,
} from '../api/teacher';
import { isLoggedIn } from '../api/auth';
import { AUTH_CHANGED_EVENT } from '../components/UserAuth';
import { EmptyBlock, ErrorAlert, LoadingBlock, PermissionAlert } from '../components/Teacher/common';
import CoursePrepPanel from '../components/Teacher/CoursePrepPanel';
import ClassesPanel from '../components/Teacher/ClassesPanel';
import AssignmentsPanel from '../components/Teacher/AssignmentsPanel';
import AnalyticsPanel from '../components/Teacher/AnalyticsPanel';
import StudentsPanel from '../components/Teacher/StudentsPanel';
import { useIsNarrow } from '../hooks/useIsNarrow';

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

type MenuKey = 'prep' | 'materials' | 'plans' | 'classes' | 'assignments' | 'analytics' | 'students';

export interface TeacherPageProps {
  onNavigate?: (path: string) => void;
}

/**
 * 教师工作台（2026-10-05 重排）
 *
 * 入口按三组组织：
 * - 课程与备课：课程（课程列表 → 课程空间：资料 → 建库 → 教案生成/编辑/发布）、班级、课程资料、教案
 * - 作业与批改：作业与批改（承接现有面板）
 * - 学情分析：班级学情、学生详情（承接现有面板）
 *
 * 权限：全部由后端判定；这里的展示判断不构成安全边界。
 */
const TeacherPage: React.FC<TeacherPageProps> = () => {
  const isNarrow = useIsNarrow();
  const [loggedIn, setLoggedIn] = useState(isLoggedIn());
  const [menuKey, setMenuKey] = useState<MenuKey>('prep');

  const [courses, setCourses] = useState<TeacherCourse[]>([]);
  const [coursesLoading, setCoursesLoading] = useState(false);
  const [coursesError, setCoursesError] = useState<string | null>(null);
  const [coursesForbidden, setCoursesForbidden] = useState<string | null>(null);
  const [courseId, setCourseId] = useState<string | undefined>();
  // 课程与备课：true = 显示课程列表；false = 已进入某门课程的备课空间
  const [prepList, setPrepList] = useState(true);

  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<{ title: string; description?: string; subject_name?: string }>();

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
      message.success('课程《' + (created?.title || values.title) + '》已创建，下一步：上传课程资料');
      setCreateOpen(false);
      form.resetFields();
      await loadCourses();
      if (created?.id) {
        setCourseId(created.id);
        setPrepList(false);       // 直接进入新课程的备课空间
        setMenuKey('prep');
      }
    } catch (e) {
      message.error(describeTeacherError(e, '创建课程失败').message);
    } finally {
      setCreating(false);
    }
  };

  const currentCourse = useMemo(
    () => courses.find((c) => c.id === courseId) || null,
    [courses, courseId],
  );

  // 收敛为三个主入口：班级 / 资料 / 教案都在"课程与备课"的课程空间内；
  // 学生明细从"学情分析"进入（不再单独占一级菜单）。
  const MENU_ITEMS = [
    { key: 'prep', icon: <BookOutlined />, label: '课程与备课' },
    { key: 'assignments', icon: <SolutionOutlined />, label: '作业与批改' },
    { key: 'analytics', icon: <BarChartOutlined />, label: '学情分析' },
  ];

  const handleMenuClick = ({ key }: { key: string }) => {
    const k = key as MenuKey;
    setMenuKey(k);
    if (k === 'prep') setPrepList(true);
  };

  const renderPanel = () => {
    if (menuKey === 'prep' || menuKey === 'materials' || menuKey === 'plans') {
      return (
        <CoursePrepPanel
          courses={courses}
          loading={coursesLoading}
          courseId={prepList ? undefined : courseId}
          courseTitle={currentCourse?.title}
          initialTab={menuKey === 'plans' ? 'plans' : 'materials'}
          onSelectCourse={(id) => { setCourseId(id); setPrepList(false); }}
          onBackToList={() => setPrepList(true)}
          onCreateCourse={() => setCreateOpen(true)}
          onChanged={loadCourses}
        />
      );
    }

    if (!courseId) {
      return (
        <Card size="small">
          <EmptyBlock description="该功能需要先有一门课程。请先在「课程」里选择或创建课程。">
            <Button type="primary" icon={<BookOutlined />}
                    onClick={() => { setMenuKey('prep'); setPrepList(true); }}>
              去课程
            </Button>
          </EmptyBlock>
        </Card>
      );
    }

    switch (menuKey) {
      case 'classes':
        return (
          <ClassesPanel
            key={'classes-' + courseId}
            courseId={courseId}
            courseTitle={currentCourse?.title}
            onBack={() => { setMenuKey('prep'); setPrepList(false); }}
          />
        );
      case 'assignments':
        return (
          <AssignmentsPanel
            key={'assignments-' + courseId}
            courseId={courseId}
            courseTitle={currentCourse?.title}
            onBack={() => { setMenuKey('prep'); setPrepList(false); }}
            onOpenMaterials={() => { setMenuKey('prep'); setPrepList(false); }}
          />
        );
      case 'analytics':
        return (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Button icon={<UserOutlined />} onClick={() => setMenuKey('students')}>
              学生明细
            </Button>
            <AnalyticsPanel
              key={'analytics-' + courseId}
              courseId={courseId}
              courseTitle={currentCourse?.title}
              onBack={() => { setMenuKey('prep'); setPrepList(false); }}
            />
          </Space>
        );
      case 'students':
        // 学生明细从"学情分析"进入，返回也回到学情
        return <StudentsPanel onBack={() => setMenuKey('analytics')} />;
      default:
        return null;
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
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        <Space size={12} wrap>
          <Title level={4} style={{ margin: 0 }}>教师工作台</Title>
          {currentCourse && !prepList && <Tag color="blue">当前课程：{currentCourse.title}</Tag>}
        </Space>
        <Space wrap>
          <Select
            style={{ width: 240 }}
            placeholder={courses.length ? '选择课程' : '还没有课程'}
            value={courseId}
            onChange={(v) => { setCourseId(v); setPrepList(false); }}
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

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {/* 宽屏：左侧竖向导航；窄屏（≤900px）：顶部紧凑横向导航，避免菜单占据大半屏 */}
        {!isNarrow && (
          <Card size="small" style={{ width: 176, minWidth: 176 }}>
            <Menu
              mode="inline"
              selectedKeys={[menuKey]}
              items={MENU_ITEMS}
              onClick={handleMenuClick}
              style={{ border: 'none' }}
            />
          </Card>
        )}

        <div style={{ flex: 1, minWidth: isNarrow ? 0 : 480 }}>
          {isNarrow && (
            <Menu
              mode="horizontal"
              selectedKeys={[menuKey]}
              items={MENU_ITEMS}
              onClick={handleMenuClick}
              style={{ marginBottom: 12 }}
            />
          )}
          {coursesLoading && courses.length === 0
            ? <LoadingBlock tip="正在加载课程…" />
            : renderPanel()}
        </div>
      </div>

      <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 16, marginBottom: 0 }}>
        所有权限判断以后端为准：这里隐藏或显示的按钮只影响展示，不构成安全边界。
        「学生加入课程」由学生在「我的学习」页输入班级邀请码完成。
      </Paragraph>

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
