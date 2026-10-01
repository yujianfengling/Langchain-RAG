import React, { useMemo } from 'react';
import { Alert, Button, Empty, Space, Spin, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ExclamationCircleOutlined, LockOutlined } from '@ant-design/icons';
import MarkdownIt from 'markdown-it';

const { Text, Paragraph } = Typography;

// Markdown 渲染器（html:false，避免直接注入后端返回的原始 HTML）
const md: MarkdownIt = new MarkdownIt({ html: false, linkify: true, breaks: true });

/** 权限不足（403）：显示后端返回的中文原因，而不是空白页 */
export const PermissionAlert: React.FC<{ message?: string; onBack?: () => void }> = ({
  message, onBack,
}) => (
  <Alert
    type="error"
    showIcon
    icon={<LockOutlined />}
    message={message || '你不是该课程的授课教师，无法查看这些内容'}
    description={
      <Space direction="vertical" size={8}>
        <Text type="secondary">
          权限由后端判定。如果你认为这是误判，请确认登录账号是否为该课程的创建教师，或联系管理员。
        </Text>
        {onBack && (
          <Button size="small" icon={<ArrowLeftOutlined />} onClick={onBack}>
            返回工作台
          </Button>
        )}
      </Space>
    }
  />
);

/** 通用失败提示（保留后端中文原因 + 重试入口） */
export const ErrorAlert: React.FC<{ message: string; onRetry?: () => void }> = ({
  message, onRetry,
}) => (
  <Alert
    type="error"
    showIcon
    icon={<ExclamationCircleOutlined />}
    message={message}
    action={onRetry ? <Button size="small" onClick={onRetry}>重试</Button> : undefined}
  />
);

export const LoadingBlock: React.FC<{ tip?: string }> = ({ tip = '加载中…' }) => (
  <div style={{ textAlign: 'center', padding: '32px 0' }}>
    <Spin tip={tip}>
      <div style={{ height: 40 }} />
    </Spin>
  </div>
);

/** 空状态（没有数据就显示"暂无"，不填模拟数据） */
export const EmptyBlock: React.FC<{ description: string; children?: React.ReactNode }> = ({
  description, children,
}) => (
  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description}>
    {children}
  </Empty>
);

export const formatBytes = (n?: number | null): string => {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
};

/** 展示时间：把 ISO 的 T 换成空格，去掉毫秒与秒后溢出 */
export const formatTime = (s?: string | null): string =>
  s ? String(s).replace('T', ' ').slice(0, 19) : '—';

/** 比率展示：null / undefined 表示没有样本，显示"—"而不是 0% */
export const ratioText = (v?: number | null): string =>
  v == null ? '—' : `${Math.round(v * 100)}%`;

export const MATERIAL_STATUS_META: Record<string, { color: string; label: string }> = {
  ready: { color: 'success', label: '已就绪' },
  processing: { color: 'processing', label: '处理中' },
  pending: { color: 'default', label: '待处理' },
  failed: { color: 'error', label: '处理失败' },
};

export const materialStatusMeta = (status?: string | null) =>
  MATERIAL_STATUS_META[status || ''] || { color: 'default', label: status || '未知状态' };

export const visibilityLabel = (v?: string | null): string =>
  v === 'teacher_only' ? '仅教师可见' : '学生可见';

export const memberStatusMeta = (status?: string | null): { color: string; label: string } => {
  if (status === 'active') return { color: 'success', label: '在班' };
  if (status === 'left') return { color: 'warning', label: '已退班' };
  if (status === 'removed') return { color: 'error', label: '已被移出' };
  return { color: 'default', label: status || '未知' };
};

export const ASSIGNMENT_STATUS_META: Record<string, { color: string; label: string }> = {
  draft: { color: 'default', label: '草稿' },
  published: { color: 'success', label: '已发布' },
  closed: { color: 'warning', label: '已关闭' },
};

export const assignmentStatusMeta = (status?: string | null) =>
  ASSIGNMENT_STATUS_META[status || ''] || { color: 'default', label: status || '未知' };

/** 题型展示（后端字段为 type，兼容 question_type） */
export const questionTypeLabel = (t?: string | null): string =>
  t === 'single_choice' ? '单选题' : t === 'judgement' ? '判断题' : t === 'short_answer' ? '简答题' : (t || '题目');

export const questionTypeColor = (t?: string | null): string =>
  t === 'single_choice' ? 'cyan' : t === 'judgement' ? 'green' : t === 'short_answer' ? 'orange' : 'default';

/** 题干/选项字段在不同接口里有 stem/question 两种命名，这里统一取值 */
export const questionStem = (q: { stem?: string | null; question?: string | null }): string =>
  q?.stem || q?.question || '';

/** 来源标注（学情每个数字都要能说明出处） */
export const SourceTag: React.FC<{ source?: string | null }> = ({ source }) => {
  if (!source) return null;
  const short = source.includes('quiz_attempts') ? '作答记录'
    : source.includes('learning_tasks') ? '学习任务'
      : source;
  return <Tag color="blue">来源：{short}</Tag>;
};

/** Markdown 预览（教案预览，纯本地渲染，无外链资源） */
export const MarkdownBlock: React.FC<{ text: string }> = ({ text }) => {
  const html = useMemo(() => md.render(text || ''), [text]);
  return (
    <div
      style={{ maxHeight: '60vh', overflow: 'auto', lineHeight: 1.75 }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
};

/** 段落式只读文本（用于展示长说明） */
export const SecondaryNote: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
    {children}
  </Paragraph>
);
