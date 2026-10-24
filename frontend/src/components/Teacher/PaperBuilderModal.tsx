import React, { useState } from 'react';
import {
  Alert, Button, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography, message,
} from 'antd';
import { DownloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { exportPaper, previewPaper, type PaperPreview } from '../../api/paper';

const { Text } = Typography;

const TYPE_OPTIONS = [
  { value: 'single_choice', label: '单选题' },
  { value: 'judgement', label: '判断题' },
  { value: 'short_answer', label: '简答题' },
];

/**
 * 教师端智能组卷：按知识点/题型/时长选题 → 预览 → 导出 Word。
 * 预览不下发答案；答案只出现在导出的 Word 里。
 */
const PaperBuilderModal: React.FC<{
  open: boolean;
  onClose: () => void;
  subjectId: string;
}> = ({ open, onClose, subjectId }) => {
  const [count, setCount] = useState(10);
  const [duration, setDuration] = useState<number | null>(45);
  const [types, setTypes] = useState<string[]>(['single_choice', 'judgement', 'short_answer']);
  const [kps, setKps] = useState('');
  const [preview, setPreview] = useState<PaperPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const points = kps.split(/[，,]/).map((s) => s.trim()).filter(Boolean);

  const build = async () => {
    if (!subjectId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await previewPaper({
        subject_id: subjectId,
        knowledge_points: points.length ? points : undefined,
        count,
        types,
        duration_min: duration,
      });
      setPreview(data);
    } catch (e: any) {
      setError(e?.response?.data?.message || e?.response?.data?.detail || '组卷失败');
    } finally {
      setLoading(false);
    }
  };

  const download = async () => {
    if (!preview?.items.length) return;
    setExporting(true);
    try {
      const blob = await exportPaper({
        subject_id: subjectId,
        knowledge_points: points.length ? points : undefined,
        count,
        types,
        duration_min: duration,
        title: '智能组卷',
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'paper.docx';
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      message.success('试卷已导出');
    } catch (e: any) {
      message.error(e?.response?.data?.message || '导出失败，请稍后重试');
    } finally {
      setExporting(false);
    }
  };

  return (
    <Modal title="智能组卷" open={open} onCancel={onClose} width={860} footer={null} destroyOnClose>
      <Space wrap style={{ marginBottom: 10 }}>
        <InputNumber min={1} max={50} value={count} onChange={(v) => setCount(Number(v) || 10)}
                     addonBefore="题量" style={{ width: 130 }} />
        <InputNumber min={5} max={180} value={duration} onChange={(v) => setDuration(Number(v) || null)}
                     addonBefore="时长" addonAfter="分钟" style={{ width: 170 }} />
        <Select mode="multiple" value={types} onChange={setTypes} options={TYPE_OPTIONS}
                style={{ minWidth: 220 }} placeholder="题型" />
        <Input value={kps} onChange={(e) => setKps(e.target.value)}
               placeholder="知识点（逗号分隔，留空=全部）" style={{ width: 260 }} />
        <Button type="primary" icon={<ThunderboltOutlined />} loading={loading} onClick={build}>
          生成预览
        </Button>
        <Button icon={<DownloadOutlined />} disabled={!preview?.items.length}
                loading={exporting} onClick={download}>
          导出 Word
        </Button>
      </Space>

      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 10 }} />}
      {preview && (
        <>
          <Space size={8} wrap style={{ marginBottom: 8 }}>
            <Tag color="blue">已选 {preview.selected_count} 题</Tag>
            <Tag>预估 {preview.estimated_minutes} 分钟</Tag>
            <Tag>题库可用 {preview.available_count} 题</Tag>
            {preview.gaps.length > 0 && <Tag color="orange">缺口：{preview.gaps.join('、')}</Tag>}
          </Space>
          <Table
            size="small"
            rowKey="id"
            pagination={false}
            dataSource={preview.items}
            columns={[
              { title: '#', width: 44, render: (_v, _r, i) => i + 1 },
              { title: '题型', dataIndex: 'type', width: 90,
                render: (t) => TYPE_OPTIONS.find((o) => o.value === t)?.label || t },
              { title: '题目', dataIndex: 'question', ellipsis: true },
              { title: '知识点', dataIndex: 'knowledge_point', width: 140, ellipsis: true },
              { title: '分钟', dataIndex: 'estimated_minutes', width: 60 },
            ]}
          />
          <Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
            预览不含答案；导出的 Word 末尾附参考答案与解析。
          </Text>
        </>
      )}
    </Modal>
  );
};

export default PaperBuilderModal;
