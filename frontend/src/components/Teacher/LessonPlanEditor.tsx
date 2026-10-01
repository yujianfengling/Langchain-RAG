import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Divider, Empty, Input, InputNumber, Space, Tag, Typography, message,
} from 'antd';
import {
  DeleteOutlined, PlusOutlined, SaveOutlined, UndoOutlined,
} from '@ant-design/icons';
import {
  LessonPlan, LessonPlanContent, PlanQA, PlanStep, updateLessonPlan, describeTeacherError,
} from '../../api/teacher';
import { formatTime } from './common';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

export interface LessonPlanEditorProps {
  /** 当前教案（含 content / version） */
  plan: LessonPlan;
  /** 保存成功（后端返回新版本）后回调 */
  onSaved: (plan: LessonPlan, tip: string) => void;
}

/** 规范化：后端返回的 content 可能缺少字段（旧版本/手写草稿） */
const normalize = (c?: Partial<LessonPlanContent> | null): LessonPlanContent => ({
  title: c?.title || '',
  objectives: Array.isArray(c?.objectives) ? c.objectives : [],
  key_points: Array.isArray(c?.key_points) ? c.key_points : [],
  difficulties: Array.isArray(c?.difficulties) ? c.difficulties : [],
  steps: Array.isArray(c?.steps)
    ? c.steps.map((s) => ({
      name: s?.name || '',
      minutes: s?.minutes ?? null,
      content: s?.content || '',
      materials: Array.isArray(s?.materials) ? s.materials : [],
    }))
    : [],
  examples: Array.isArray(c?.examples)
    ? c.examples.map((e) => ({ question: e?.question || '', answer: e?.answer || '' }))
    : [],
  homework: Array.isArray(c?.homework)
    ? c.homework.map((h) => ({ question: h?.question || '', answer: h?.answer || '' }))
    : [],
  evidence_note: c?.evidence_note || '',
});

const toLines = (arr: string[]): string => (arr || []).join('\n');
const fromLines = (text: string): string[] =>
  text.split('\n').map((s) => s.trim()).filter(Boolean);

/** 教案编辑器：分区块编辑 → 保存为新版本（不覆盖历史） */
const LessonPlanEditor: React.FC<LessonPlanEditorProps> = ({ plan, onSaved }) => {
  const [title, setTitle] = useState(plan.title || '');
  const [content, setContent] = useState<LessonPlanContent>(normalize(plan.content));
  const [saving, setSaving] = useState(false);

  // 切换教案 / 切换版本时重置编辑态
  useEffect(() => {
    setTitle(plan.title || '');
    setContent(normalize(plan.content));
  }, [plan.id, plan.version, plan.title, plan.content]);

  const dirty = useMemo(
    () => JSON.stringify(normalize(plan.content)) !== JSON.stringify(content) || title !== (plan.title || ''),
    [plan.content, plan.title, content, title],
  );

  const patch = (p: Partial<LessonPlanContent>) => setContent((prev) => ({ ...prev, ...p }));

  const patchStep = (idx: number, p: Partial<PlanStep>) =>
    setContent((prev) => ({
      ...prev,
      steps: prev.steps.map((s, i) => (i === idx ? { ...s, ...p } : s)),
    }));

  const patchQA = (key: 'examples' | 'homework', idx: number, p: Partial<PlanQA>) =>
    setContent((prev) => {
      const list = prev[key].map((q, i) => (i === idx ? { ...q, ...p } : q));
      return key === 'examples' ? { ...prev, examples: list } : { ...prev, homework: list };
    });

  const removeAt = (key: 'steps' | 'examples' | 'homework', idx: number) =>
    setContent((prev) => {
      if (key === 'steps') return { ...prev, steps: prev.steps.filter((_, i) => i !== idx) };
      if (key === 'examples') return { ...prev, examples: prev.examples.filter((_, i) => i !== idx) };
      return { ...prev, homework: prev.homework.filter((_, i) => i !== idx) };
    });

  const handleReset = () => {
    setTitle(plan.title || '');
    setContent(normalize(plan.content));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await updateLessonPlan(plan.id, {
        content,
        title: title.trim() || undefined,
        note: `教师编辑（基于 v${plan.version ?? plan.current_version ?? 1}）`,
      });
      const saved = res?.data || plan;
      const v = saved?.version ?? ((plan.version ?? 1) + 1);
      onSaved(saved, `已保存为 v${v}，历史版本保留`);
    } catch (e) {
      message.error(describeTeacherError(e, '保存教案失败').message);
    } finally {
      setSaving(false);
    }
  };

  const evidence = plan.evidence || [];
  const minutesTotal = content.steps.reduce((sum, s) => sum + (Number(s.minutes) || 0), 0);

  return (
    <Card
      size="small"
      title={
        <Space size={8} wrap>
          <Text strong>教案编辑</Text>
          <Tag color="blue">当前 v{plan.version ?? plan.current_version ?? 1}</Tag>
          {plan.status === 'published' ? <Tag color="success">已发布</Tag> : <Tag>草稿</Tag>}
          {dirty && <Tag color="orange">有未保存修改</Tag>}
        </Space>
      }
      extra={
        <Space>
          <Button size="small" icon={<UndoOutlined />} disabled={!dirty || saving} onClick={handleReset}>
            放弃修改
          </Button>
          <Button
            size="small"
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            onClick={handleSave}
          >
            保存为新版本
          </Button>
        </Space>
      }
    >
      {/* 资料不足提示：必须显式，不能静默 */}
      {plan.insufficient_evidence && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="课程资料不足"
          description="生成这份教案时，课程知识库没有检索到可用资料，内容主要来自模型已有知识，请务必人工核对后再使用。建议先上传课程资料并重建知识库。"
        />
      )}

      {/* AI / 版本信息 */}
      <Space direction="vertical" size={2} style={{ marginBottom: 12 }}>
        <Space size={8} wrap>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {plan.ai_generated ? 'AI 生成' : '教师创建'}
            {plan.ai_model ? ` · 模型 ${plan.ai_model}` : ''}
            {plan.version_note ? ` · ${plan.version_note}` : ''}
            {plan.version_created_at ? ` · ${formatTime(plan.version_created_at)}` : ''}
            {plan.version_count ? ` · 共 ${plan.version_count} 个版本` : ''}
          </Text>
        </Space>
        {plan.class_summary && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            班级学情参考：
            {plan.class_summary.used
              ? '已使用该班汇总学情'
              : `未使用（${plan.class_summary.reason || '无可用学情'}）`}
          </Text>
        )}
      </Space>

      <div style={{ marginBottom: 12 }}>
        <Text strong>教案名称</Text>
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="教案名称"
          style={{ marginTop: 4 }}
          maxLength={120}
        />
      </div>

      <Divider orientation="left" plain style={{ margin: '8px 0' }}>教学目标</Divider>
      <TextArea
        rows={3}
        value={toLines(content.objectives)}
        onChange={(e) => patch({ objectives: fromLines(e.target.value) })}
        placeholder="每行一条教学目标"
      />

      <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>重点 / 难点</Divider>
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>教学重点（每行一条）</Text>
          <TextArea
            rows={2}
            value={toLines(content.key_points)}
            onChange={(e) => patch({ key_points: fromLines(e.target.value) })}
          />
        </div>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>教学难点（每行一条）</Text>
          <TextArea
            rows={2}
            value={toLines(content.difficulties)}
            onChange={(e) => patch({ difficulties: fromLines(e.target.value) })}
          />
        </div>
      </Space>

      <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>
        教学步骤（合计 {minutesTotal} 分钟）
      </Divider>
      {content.steps.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有教学步骤" />
      ) : (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          {content.steps.map((s, idx) => (
            <Card key={idx} size="small" type="inner">
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                <Space wrap style={{ width: '100%' }}>
                  <Input
                    value={s.name}
                    onChange={(e) => patchStep(idx, { name: e.target.value })}
                    placeholder={`步骤 ${idx + 1} 名称`}
                    style={{ width: 240 }}
                  />
                  <InputNumber
                    min={0}
                    max={600}
                    value={s.minutes ?? undefined}
                    onChange={(v) => patchStep(idx, { minutes: v == null ? null : Number(v) })}
                    addonAfter="分钟"
                    style={{ width: 140 }}
                  />
                  <Button
                    size="small"
                    danger
                    type="text"
                    icon={<DeleteOutlined />}
                    onClick={() => removeAt('steps', idx)}
                  >
                    删除
                  </Button>
                </Space>
                <TextArea
                  rows={3}
                  value={s.content}
                  onChange={(e) => patchStep(idx, { content: e.target.value })}
                  placeholder="该步骤的教学内容"
                />
                {!!(s.materials && s.materials.length) && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    关联资料（保留原值，暂不在此编辑）：{s.materials.join('、')}
                  </Text>
                )}
              </Space>
            </Card>
          ))}
        </Space>
      )}
      <Button
        size="small"
        icon={<PlusOutlined />}
        style={{ marginTop: 8 }}
        onClick={() => patch({ steps: [...content.steps, { name: '', minutes: null, content: '', materials: [] }] })}
      >
        添加步骤
      </Button>

      <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>例题</Divider>
      {content.examples.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有例题" />
      ) : (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          {content.examples.map((q, idx) => (
            <Card key={idx} size="small" type="inner">
              <TextArea
                rows={2}
                value={q.question}
                onChange={(e) => patchQA('examples', idx, { question: e.target.value })}
                placeholder="题目"
              />
              <TextArea
                rows={2}
                value={q.answer}
                onChange={(e) => patchQA('examples', idx, { answer: e.target.value })}
                placeholder="解答"
                style={{ marginTop: 6 }}
              />
              <Button
                size="small" type="text" danger icon={<DeleteOutlined />}
                style={{ marginTop: 4 }} onClick={() => removeAt('examples', idx)}
              >
                删除
              </Button>
            </Card>
          ))}
        </Space>
      )}
      <Button
        size="small"
        icon={<PlusOutlined />}
        style={{ marginTop: 8 }}
        onClick={() => patch({ examples: [...content.examples, { question: '', answer: '' }] })}
      >
        添加例题
      </Button>

      <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>课后练习</Divider>
      {content.homework.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有课后练习" />
      ) : (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          {content.homework.map((h, idx) => (
            <Card key={idx} size="small" type="inner">
              <TextArea
                rows={2}
                value={h.question}
                onChange={(e) => patchQA('homework', idx, { question: e.target.value })}
                placeholder="题目"
              />
              <TextArea
                rows={2}
                value={h.answer}
                onChange={(e) => patchQA('homework', idx, { answer: e.target.value })}
                placeholder="参考答案"
                style={{ marginTop: 6 }}
              />
              <Button
                size="small" type="text" danger icon={<DeleteOutlined />}
                style={{ marginTop: 4 }} onClick={() => removeAt('homework', idx)}
              >
                删除
              </Button>
            </Card>
          ))}
        </Space>
      )}
      <Button
        size="small"
        icon={<PlusOutlined />}
        style={{ marginTop: 8 }}
        onClick={() => patch({ homework: [...content.homework, { question: '', answer: '' }] })}
      >
        添加课后练习
      </Button>

      <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>编写依据说明</Divider>
      <TextArea
        rows={2}
        value={content.evidence_note}
        onChange={(e) => patch({ evidence_note: e.target.value })}
        placeholder="说明本教案的依据与取舍（可选）"
      />

      <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>
        依据资料（{evidence.length}）
      </Divider>
      {evidence.length === 0 ? (
        <Alert
          type="warning"
          showIcon
          message="没有可展示的依据资料"
          description="本次生成没有检索到任何课程资料片段，请核对上方「课程资料不足」提示。"
        />
      ) : (
        <Space size={6} wrap>
          {evidence.map((e, i) => (
            <Tag key={`${e.file_name}-${i}`} color={e.source === 'retrieval' ? 'blue' : 'default'}>
              {e.file_name || '未知文件'}
              {e.page != null ? ` · 第 ${e.page} 页` : ''}
              {e.source === 'selected_material' ? '（教师指定）' : ''}
            </Tag>
          ))}
        </Space>
      )}

      <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12, marginBottom: 0 }}>
        保存会生成新版本，历史版本不会被覆盖（可随时在「版本」中回看）；重新生成教案也会新建一份草稿，不影响本次编辑结果。
      </Paragraph>
    </Card>
  );
};

export default LessonPlanEditor;
