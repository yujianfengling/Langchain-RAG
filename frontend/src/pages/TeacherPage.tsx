import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Card, Form, Input, Menu, Modal, Select, Space, Typography, message,
} from 'antd';
import {
  BarChartOutlined, BulbOutlined, PlusOutlined, ReloadOutlined, UserOutlined,
} from '@ant-design/icons';
import {
  TeacherCourse, createCourse, describeTeacherError, isForbiddenError, listTeacherCourses,
} from '../api/teacher';
import { isLoggedIn } from '../api/auth';
import { AUTH_CHANGED_EVENT } from '../components/UserAuth';
import { EmptyBlock, ErrorAlert, LoadingBlock, PermissionAlert } from '../components/Teacher/common';
import AnalyticsPanel from '../components/Teacher/AnalyticsPanel';
import StudentsPanel from '../components/Teacher/StudentsPanel';
import { useIsNarrow } from '../hooks/useIsNarrow';

const { Title, Text } = Typography;
const { TextArea } = Input;

/**
 * 智慧学伴比赛版工作台（2026-11-18）
 *
 * 以现有教师端为基座，只保留考后分析相关三块：
 * - analytics 班级学情：复用 AnalyticsPanel（班级选择/知识点统计/错题/样本不足）
 * - students  学生答卷：复用 StudentsPanel + StudentAttemptsDrawer（个人答卷明细/改判）
 * - feedback  反馈与建议：比赛专用占位视图，四块比赛功能后续从这里接入
 *
 * 原「教学助手/课程与备课/作业与批改/班级管理」入口已从比赛导航卸下；
 * 对应组件文件保留，不删（大创原有模块，后续可能复用）。
 * 权限由后端判定，这里不构成安全边界。
 */
type SectionKey = 'analytics' | 'students' | 'feedback';
type ViewKey = SectionKey;

const SECTION_KEYS: SectionKey[] = ['analytics', 'students', 'feedback'];
const DEFAULT_SECTION: SectionKey = 'analytics';

/** 旧入口兼容：旧教师 section 统一回收到比赛学情工作台 */
const LEGACY_SECTION: Record<string, SectionKey> = {
  assistant: 'analytics',
  prep: 'analytics',
  materials: 'analytics',
  plans: 'analytics',
  classes: 'analytics',
  assignments: 'analytics',
  analytics: 'analytics',
  students: 'students',
  feedback: 'feedback',
};

export interface TeacherPageProps {
  onNavigate?: (path: string) => void;
}

/**
 * 教师工作台（2026-10-07 重构）
 *
 * 主导航收敛为四个入口：教学助手 / 课程与备课 / 作业与批改 / 学情分析。
 * 默认「教学助手」；section 可经 URL 查询参数（?section=assistant）深链，
 * 旧的 materials/plans/classes/students 入口兼容映射到对应区域，已有面板全部保留。
 *
 * 权限：全部由后端判定；这里的展示判断不构成安全边界。
 */
const TeacherPage: React.FC<TeacherPageProps> = () => {
  const isNarrow = useIsNarrow();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawSection = searchParams.get('section') || DEFAULT_SECTION;

  const navSelected: SectionKey = (SECTION_KEYS as string[]).includes(rawSection)
    ? (rawSection as SectionKey)
    : (LEGACY_SECTION[rawSection] || DEFAULT_SECTION);

  const [view, setView] = useState<ViewKey>(navSelected);
  const [loggedIn, setLoggedIn] = useState(isLoggedIn());

  const [courses, setCourses] = useState<TeacherCourse[]>([]);
  const [coursesLoading, setCoursesLoading] = useState(false);
  const [coursesError, setCoursesError] = useState<string | null>(null);
  const [coursesForbidden, setCoursesForbidden] = useState<string | null>(null);
  const [courseId, setCourseId] = useState<string | undefined>();

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

  // URL 查询参数 → 视图（含旧入口兼容；浏览器前进/后退也同步）
  useEffect(() => {
    setView(navSelected);
  }, [navSelected]);

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
        selectSection('analytics');
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

  const MENU_ITEMS = [
    { key: 'analytics', icon: <BarChartOutlined />, label: '班级学情' },
    { key: 'students', icon: <UserOutlined />, label: '学生答卷' },
    { key: 'feedback', icon: <BulbOutlined />, label: '反馈与建议' },
  ];

  const selectSection = useCallback((key: ViewKey) => {
    setView(key);
    setSearchParams({ section: key }, { replace: true });
  }, [setSearchParams]);

  const handleMenuClick = ({ key }: { key: string }) => {
    selectSection(key as SectionKey);
  };

  const renderPanel = () => {
    // 旧教师工作台已被 competition/ 三页面取代（不再挂路由）。
    // 本文件保留仅为复用其中的学情/作答装配参考；feedback 占位视图已并入学生答卷页。
    if (view === 'feedback') {
      return (
        <Card size="small">
          <EmptyBlock description="反馈与学习建议已并入比赛版「学生答卷」页（src/competition/）。" />
        </Card>
      );
    }

    // 班级学情 / 学生答卷需要一门课程作为数据范围（沿用现状：读原作答/班级/题库）
    if (!courseId) {
      return (
        <Card size="small">
          <EmptyBlock description="还没有课程。请先在右上角选择一门课程，或新建一门课程后再查看学情与答卷。">
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建课程</Button>
          </EmptyBlock>
        </Card>
      );
    }

    switch (view) {
      case 'analytics':
        return (
          <AnalyticsPanel
            key={'analytics-' + courseId}
            courseId={courseId}
            courseTitle={currentCourse?.title}
          />
        );
      case 'students':
        return <StudentsPanel />;
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

  const TITLE_MAP: Record<ViewKey, string> = {
    analytics: '班级学情',
    students: '学生答卷',
    feedback: '反馈与练习建议',
  };
  const SUBTITLE_MAP: Record<ViewKey, string> = {
    analytics: '班级整体作答与知识点掌握情况，定位共性问题。',
    students: '逐个学生的逐题作答明细，查看答案、判定与改判记录。',
    feedback: '比赛专用视图：学生反馈与个性化练习建议，四块功能从这里接入。',
  };
  const sectionTitle = TITLE_MAP[view];
  const sectionSubtitle = SUBTITLE_MAP[view];

  return (
    <div className="app-page app-page-wide">
      <header className="page-header">
        <div className="page-heading">
          <div className="page-eyebrow">教师工作台</div>
          <Title level={1} className="page-title">{sectionTitle}</Title>
          <Text className="page-subtitle">{sectionSubtitle}</Text>
        </div>
        <Space className="page-actions" wrap>
          <Select
            aria-label="当前课程"
            style={{ width: 220 }}
            placeholder={courses.length ? '选择课程' : '还没有课程'}
            value={courseId}
            onChange={(v) => setCourseId(v)}
            loading={coursesLoading}
            options={courses.map((c) => ({ value: c.id, label: c.title }))}
            notFoundContent="还没有课程，请先新建"
          />
          <Button type="text" icon={<ReloadOutlined />} aria-label="刷新课程" onClick={loadCourses} loading={coursesLoading} />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建课程</Button>
        </Space>
      </header>

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
        {/* 比赛版主导航在全局顶栏（班级学情 / 学生答卷 / 反馈与建议），
            这里仅在窄屏保留横向切换。 */}
        <div style={{ flex: 1, minWidth: isNarrow ? 0 : 480 }}>
          {isNarrow && (
            <Menu
              mode="horizontal"
              selectedKeys={[view]}
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

      <details style={{ marginTop: 20, color: 'var(--color-text-muted)', fontSize: 12 }}>
        <summary style={{ cursor: 'pointer' }}>关于教师权限</summary>
        <p style={{ margin: '8px 0 0' }}>
          所有权限判断以后端为准；界面中按钮的隐藏或显示只影响展示，不构成安全边界。
        </p>
      </details>

      <Modal
        title="新建课程"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); form.resetFields(); }}
        onOk={() => form.submit()}
        okText="创建"
        cancelText="取消"
        confirmLoading={creating}
        destroyOnHidden
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
