import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Card, Form, Input, Menu, Modal, Select, Space, Typography, message,
} from 'antd';
import {
  BarChartOutlined, BookOutlined, PlusOutlined, ReloadOutlined, RobotOutlined,
  SolutionOutlined, UserOutlined,
} from '@ant-design/icons';
import {
  TeacherCourse, createCourse, describeTeacherError, isForbiddenError, listTeacherCourses,
} from '../api/teacher';
import { isLoggedIn } from '../api/auth';
import { AUTH_CHANGED_EVENT } from '../components/UserAuth';
import { EmptyBlock, ErrorAlert, LoadingBlock, PermissionAlert } from '../components/Teacher/common';
import CoursePrepPanel from '../components/Teacher/CoursePrepPanel';
import AssignmentsPanel from '../components/Teacher/AssignmentsPanel';
import AnalyticsPanel from '../components/Teacher/AnalyticsPanel';
import StudentsPanel from '../components/Teacher/StudentsPanel';
import TutorAssistantPanel from '../components/Teacher/TutorAssistantPanel';
import { useIsNarrow } from '../hooks/useIsNarrow';

const { Title, Text } = Typography;
const { TextArea } = Input;

/** 教师主导航四个入口（2026-10-07 重构，默认「教学助手」） */
type SectionKey = 'assistant' | 'prep' | 'assignments' | 'analytics';
type ViewKey = SectionKey | 'students';
type PrepTab = 'materials' | 'plans' | 'classes';

const SECTION_KEYS: SectionKey[] = ['assistant', 'prep', 'assignments', 'analytics'];

/** 旧入口兼容：materials/plans/classes 归到「课程与备课」，students 归到「学情分析」 */
const LEGACY_SECTION: Record<string, SectionKey> = {
  assistant: 'assistant',
  prep: 'prep',
  materials: 'prep',
  plans: 'prep',
  classes: 'prep',
  assignments: 'assignments',
  analytics: 'analytics',
  students: 'analytics',
};
const LEGACY_PREP_TAB: Record<string, PrepTab> = {
  materials: 'materials',
  plans: 'plans',
  classes: 'classes',
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
  const rawSection = searchParams.get('section') || 'assistant';
  const rawView = searchParams.get('view');

  const navSelected: SectionKey = (SECTION_KEYS as string[]).includes(rawSection)
    ? (rawSection as SectionKey)
    : (LEGACY_SECTION[rawSection] || 'assistant');

  const [view, setView] = useState<ViewKey>(navSelected);
  const [prepTab, setPrepTab] = useState<PrepTab>(LEGACY_PREP_TAB[rawSection] || 'materials');
  const [prepList, setPrepList] = useState(!LEGACY_PREP_TAB[rawSection]);
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
    if (rawView === 'students') { setView('students'); return; }
    setView(navSelected);
    if (LEGACY_PREP_TAB[rawSection]) {
      setPrepTab(LEGACY_PREP_TAB[rawSection]);
      setPrepList(false);
    }
    // navSelected 由 rawSection 派生，依赖 rawSection / rawView 即可
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rawSection, rawView]);

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
        setPrepList(false);
        selectSection('prep');
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
    { key: 'assistant', icon: <RobotOutlined />, label: '教学助手' },
    { key: 'prep', icon: <BookOutlined />, label: '课程与备课' },
    { key: 'assignments', icon: <SolutionOutlined />, label: '作业与批改' },
    { key: 'analytics', icon: <BarChartOutlined />, label: '学情分析' },
  ];

  const selectSection = useCallback((key: ViewKey) => {
    setView(key);
    if (key === 'students') setSearchParams({ section: 'analytics', view: 'students' }, { replace: true });
    else setSearchParams({ section: key }, { replace: true });
  }, [setSearchParams]);

  const handleMenuClick = ({ key }: { key: string }) => {
    const k = key as SectionKey;
    if (k === 'prep') setPrepList(true);
    selectSection(k);
  };

  const goPrep = useCallback(() => {
    setPrepTab('materials');
    setPrepList(true);
    selectSection('prep');
  }, [selectSection]);

  const goCourseWorkspace = useCallback(() => {
    setPrepList(false);
    selectSection('prep');
  }, [selectSection]);

  const renderPanel = () => {
    if (view === 'assistant') {
      return (
        <TutorAssistantPanel
          subjectId={currentCourse?.subject_id}
          courseId={currentCourse?.id}
          courseTitle={currentCourse?.title}
          onGoPrep={goPrep}
        />
      );
    }

    if (view === 'prep') {
      return (
        <CoursePrepPanel
          courses={courses}
          loading={coursesLoading}
          courseId={prepList ? undefined : courseId}
          courseTitle={currentCourse?.title}
          initialTab={prepTab}
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
          <EmptyBlock description="该功能需要先有一门课程。请先在「课程与备课」里选择或创建课程。">
            <Button type="primary" icon={<BookOutlined />} onClick={goPrep}>去课程与备课</Button>
          </EmptyBlock>
        </Card>
      );
    }

    switch (view) {
      case 'assignments':
        return (
          <AssignmentsPanel
            key={'assignments-' + courseId}
            courseId={courseId}
            courseTitle={currentCourse?.title}
            subjectId={currentCourse?.subject_id}
            onBack={goCourseWorkspace}
            onOpenMaterials={() => { setPrepTab('materials'); setPrepList(false); selectSection('prep'); }}
          />
        );
      case 'analytics':
        return (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Button icon={<UserOutlined />} onClick={() => selectSection('students')}>学生明细</Button>
            <AnalyticsPanel
              key={'analytics-' + courseId}
              courseId={courseId}
              courseTitle={currentCourse?.title}
              onBack={goCourseWorkspace}
            />
          </Space>
        );
      case 'students':
        return <StudentsPanel onBack={() => selectSection('analytics')} />;
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

  const sectionTitle = view === 'students' ? '学生明细' : {
    assistant: '教学助手',
    prep: '课程与备课',
    assignments: '作业与批改',
    analytics: '学情分析',
  }[view];
  const sectionSubtitle = view === 'students' ? '查看课程中的学生与学习表现。' : {
    assistant: '围绕当前课程整理思路、准备教学内容。',
    prep: '维护课程资料、教案与班级，让教学内容有序沉淀。',
    assignments: '布置作业、跟进提交并完成批改。',
    analytics: '基于真实学习数据发现共性问题和个体需求。',
  }[view];

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
            onChange={(v) => { setCourseId(v); setPrepList(false); }}
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
        {/* 主导航在全局侧边栏（教学助手 / 课程与备课 / 作业与批改 / 学情分析），
            这里不再重复第二套导航；窄屏时全局导航在抽屉里，所以保留顶部横向切换。 */}
        <div style={{ flex: 1, minWidth: isNarrow ? 0 : 480 }}>
          {isNarrow && (
            <Menu
              mode="horizontal"
              selectedKeys={[view === 'students' ? 'analytics' : view]}
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
