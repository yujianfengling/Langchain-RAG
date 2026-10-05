import React, { useEffect, useState } from 'react';
import {
  Alert, Button, Card, List, Space, Tabs, Tag, Typography,
} from 'antd';
import {
  BookOutlined, FileTextOutlined, FolderOpenOutlined, LeftOutlined, PlusOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { TeacherCourse } from '../../api/teacher';
import { getKnowledgeStats } from '../../api/knowledge';
import MaterialsPanel from './MaterialsPanel';
import LessonPlansPanel from './LessonPlansPanel';
import ClassesPanel from './ClassesPanel';
import { EmptyBlock } from './common';

const { Title, Text } = Typography;

export type PrepTab = 'materials' | 'plans' | 'classes';

export interface CoursePrepPanelProps {
  courses: TeacherCourse[];
  loading?: boolean;
  courseId?: string;
  courseTitle?: string;
  initialTab?: PrepTab;
  focusPlanId?: string;
  onSelectCourse: (id: string) => void;
  onBackToList: () => void;
  onCreateCourse: () => void;
  /** 课程内数据变化（资料/建库等）→ 让父级刷新课程数量与下一步提示 */
  onChanged?: () => void;
}

/**
 * 「课程与备课」工作区（2026-10-05）
 *
 * 组织原则：先看到课程列表 → 进入某门课程 → 在同一个课程空间里
 * 上传资料 → 查看建库状态 → 生成教案 → 编辑 → 发布。
 * 课程上下文由本组件持有（courseId），资料与教案是两个页签，
 * 不再要求老师在多个菜单之间来回选课。
 */
const CoursePrepPanel: React.FC<CoursePrepPanelProps> = ({
  courses, loading, courseId, courseTitle, initialTab, focusPlanId,
  onSelectCourse, onBackToList, onCreateCourse, onChanged,
}) => {
  const [tab, setTab] = useState<PrepTab>(initialTab || 'materials');
  const [kbTick, setKbTick] = useState(0);
  useEffect(() => { setTab(initialTab || 'materials'); }, [initialTab, courseId]);

  const course = courses.find((c) => c.id === courseId) || null;

  // 课程知识库真实状态：用于给出"下一步该做什么"，不靠材料数量猜测
  const [kb, setKb] = useState<{ available: boolean; documentCount: number } | null>(null);
  useEffect(() => {
    let alive = true;
    const sid = course?.subject_id;
    if (!sid) { setKb(null); return () => { alive = false; }; }
    setKb(null);
    (async () => {
      try {
        const res: any = await getKnowledgeStats(sid);
        const st = res?.stats || {};
        if (alive) setKb({
          available: !!st.has_knowledge_base,
          documentCount: Number(st.document_count || 0),
        });
      } catch {
        if (alive) setKb({ available: false, documentCount: 0 });
      }
    })();
    return () => { alive = false; };
    // 切换页签或资料变化时重取（上传后立即刷新知识库状态与下一步，无需手动刷新）
  }, [course?.subject_id, tab, kbTick]);

  // 未进入具体课程：先看课程列表（首次使用也能一眼看到"从哪开始"）
  if (!course) {
    return (
      <Card
        size="small"
        title={<Space><BookOutlined />课程与备课</Space>}
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={onCreateCourse}>新建课程</Button>}
      >
        {loading ? (
          <Text type="secondary">正在加载课程…</Text>
        ) : courses.length === 0 ? (
          <EmptyBlock description="还没有课程。先创建一门课程，再上传资料、生成教案。">
            <Button type="primary" icon={<PlusOutlined />} onClick={onCreateCourse}>新建课程</Button>
          </EmptyBlock>
        ) : (
          <List
            dataSource={courses}
            renderItem={(c) => {
              const noMaterial = (c.material_count ?? 0) === 0;
              const draft = c.draft_plan_count ?? 0;
              return (
                <List.Item
                  actions={[
                    <Button key="enter" type="primary" onClick={() => onSelectCourse(c.id)}>
                      进入课程
                    </Button>,
                  ]}
                >
                  <List.Item.Meta
                    title={<Text strong>{c.title}</Text>}
                    description={
                      <Space size={4} wrap>
                        <Tag icon={<TeamOutlined />}>{c.class_count ?? 0} 个班</Tag>
                        <Tag>{c.student_count ?? 0} 名学生</Tag>
                        <Tag>{c.material_count ?? 0} 份资料</Tag>
                        <Tag color={draft > 0 ? 'orange' : 'default'}>教案草稿 {draft}</Tag>
                        <Tag color={noMaterial ? 'orange' : draft > 0 ? 'blue' : 'green'}>
                          {noMaterial
                            ? '下一步：上传课程资料'
                            : draft > 0 ? '下一步：编辑 / 发布教案' : '下一步：生成教案'}
                        </Tag>
                      </Space>
                    }
                  />
                </List.Item>
              );
            }}
          />
        )}
      </Card>
    );
  }

  return (
    <div>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
          <Space wrap>
            <Button size="small" icon={<LeftOutlined />} onClick={onBackToList}>返回课程列表</Button>
            <Title level={5} style={{ margin: 0 }}>{courseTitle || course.title}</Title>
            <Tag>{course.class_count ?? 0} 个班</Tag>
            <Tag>{course.material_count ?? 0} 份资料</Tag>
            <Tag color={(course.draft_plan_count ?? 0) > 0 ? 'orange' : 'default'}>
              教案草稿 {course.draft_plan_count ?? 0}
            </Tag>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            备课流程：上传资料 → 查看建库状态 → 生成教案 → 编辑 → 发布
          </Text>
        </Space>
      </Card>

      {/* 基于真实状态给"下一步"：上传资料 → 等待/重建建库 → 生成教案 → 编辑发布 */}
      {(() => {
        const mats = course.material_count ?? 0;
        const drafts = course.draft_plan_count ?? 0;
        let step: { type: string; label: string; desc: string; cta: string; tab: PrepTab };
        if (mats === 0) {
          step = { type: 'warning', label: '上传课程资料',
            desc: '本课程还没有课程资料；先上传资料，系统会自动构建本课程知识库。',
            cta: '去上传资料', tab: 'materials' };
        } else if (kb === null) {
          step = { type: 'info', label: '查看建库状态',
            desc: '正在读取知识库状态…', cta: '查看课程资料', tab: 'materials' };
        } else if (!kb.available) {
          step = { type: 'warning', label: '等待 / 重建知识库',
            desc: '资料已上传，但知识库当前不可用；可在课程资料页重建后重试。',
            cta: '去课程资料', tab: 'materials' };
        } else if (drafts === 0) {
          step = { type: 'success', label: '基于课程资料生成教案',
            desc: '知识库已就绪（' + kb.documentCount + ' 份文档），可以生成教案；生成结果会列出引用的课程资料依据。',
            cta: '去生成教案', tab: 'plans' };
        } else {
          step = { type: 'info', label: '编辑草稿并发布',
            desc: '已有 ' + drafts + ' 份草稿；可继续编辑保存新版本，确认后发布给学生。',
            cta: '去教案', tab: 'plans' };
        }
        return (
          <Alert
            type={step.type as any}
            showIcon
            style={{ marginBottom: 12 }}
            message={'下一步：' + step.label}
            description={step.desc}
            action={
              <Button size="small" type="primary" onClick={() => setTab(step.tab)}>
                {step.cta}
              </Button>
            }
          />
        );
      })()}

      <Tabs
        activeKey={tab}
        onChange={(k) => setTab(k as PrepTab)}
        items={[
          {
            key: 'materials',
            label: <Space><FolderOpenOutlined />课程资料</Space>,
            children: (
              <MaterialsPanel
                key={'materials-' + course.id}
                courseId={course.id}
                courseTitle={course.title}
                onBack={onBackToList}
                onChanged={() => { setKbTick((t) => t + 1); onChanged?.(); }}
              />
            ),
          },
          {
            key: 'plans',
            label: <Space><FileTextOutlined />教案</Space>,
            children: (
              <LessonPlansPanel
                key={'plans-' + course.id}
                courseId={course.id}
                courseTitle={course.title}
                focusPlanId={focusPlanId}
                onBack={onBackToList}
              />
            ),
          },
          {
            key: 'classes',
            label: <Space><TeamOutlined />班级</Space>,
            children: (
              <ClassesPanel
                key={'classes-' + course.id}
                courseId={course.id}
                courseTitle={course.title}
                onBack={onBackToList}
              />
            ),
          },
        ]}
      />
    </div>
  );
};

export default CoursePrepPanel;
