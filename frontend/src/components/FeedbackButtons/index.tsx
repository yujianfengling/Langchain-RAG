import React, { useState } from 'react';
import { Space, Button, Tooltip, Modal, Input, message } from 'antd';
import { LikeOutlined, DislikeOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { submitFeedback } from '../../api/agent';

interface FeedbackButtonsProps {
  /** 关联的真实用户交互记录 ID（由后端回写到消息上） */
  interactionId?: string | null;
  onFeedback?: (type: 'good' | 'bad') => void;
}

/**
 * 回答反馈按钮
 *
 * 阶段一修正：
 * - 用户身份由后端依据登录令牌判定，前端不再提交 user_id
 * - interaction_id 使用后端回写的真实交互 ID（此前误用 message_id，导致关联失败）
 */
const FeedbackButtons: React.FC<FeedbackButtonsProps> = ({
  interactionId,
  onFeedback,
}) => {
  const [feedback, setFeedback] = useState<'good' | 'bad' | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [detailText, setDetailText] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleQuickFeedback = async (type: 'good' | 'bad') => {
    if (feedback === type) return;
    setFeedback(type);
    setSubmitting(true);
    try {
      await submitFeedback({
        interaction_id: interactionId || undefined,
        feedback_type: 'rating',
        feedback_score: type === 'good' ? 1 : -1,
      });
      if (type === 'good') {
        message.success('感谢您的反馈！');
      } else {
        setShowDetail(true);
      }
      onFeedback?.(type);
    } catch (error) {
      console.error('提交反馈失败:', error);
      message.error('反馈提交失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDetailSubmit = async () => {
    setSubmitting(true);
    try {
      await submitFeedback({
        interaction_id: interactionId || undefined,
        feedback_type: 'correction',
        feedback_score: -1,
        feedback_detail: detailText,
      });
      message.success('感谢您的详细反馈，我们会改进！');
      setShowDetail(false);
      setDetailText('');
    } catch (error) {
      console.error('提交详细反馈失败:', error);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Space size="small">
        <Tooltip title="回答有帮助">
          <Button
            type="text"
            size="small"
            icon={<LikeOutlined style={{ color: feedback === 'good' ? '#52c41a' : undefined }} />}
            onClick={() => handleQuickFeedback('good')}
            loading={submitting && feedback === 'good'}
            disabled={feedback !== null && feedback !== 'good'}
          />
        </Tooltip>
        <Tooltip title="回答需要改进">
          <Button
            type="text"
            size="small"
            icon={<DislikeOutlined style={{ color: feedback === 'bad' ? '#ff4d4f' : undefined }} />}
            onClick={() => handleQuickFeedback('bad')}
            loading={submitting && feedback === 'bad'}
            disabled={feedback !== null && feedback !== 'bad'}
          />
        </Tooltip>
        {feedback === 'good' && (
          <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 14 }} />
        )}
      </Space>

      <Modal
        title="帮助我们改进"
        open={showDetail}
        onOk={handleDetailSubmit}
        onCancel={() => setShowDetail(false)}
        confirmLoading={submitting}
        okText="提交"
        cancelText="取消"
      >
        <p style={{ marginBottom: 8 }}>请告诉我们哪里可以改进：</p>
        <Input.TextArea
          value={detailText}
          onChange={(e) => setDetailText(e.target.value)}
          placeholder="例如：回答不够准确、缺少例子、太复杂..."
          rows={4}
          maxLength={500}
          showCount
        />
      </Modal>
    </>
  );
};

export default FeedbackButtons;
