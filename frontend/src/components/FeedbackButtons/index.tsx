import React, { useState } from 'react';
import { Space, Button, Tooltip, Modal, Input, message } from 'antd';
import { LikeOutlined, DislikeOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { submitFeedback } from '../../api/agent';

interface FeedbackButtonsProps {
  messageId?: string;
  userId?: string;
  onFeedback?: (type: 'good' | 'bad') => void;
}

const FeedbackButtons: React.FC<FeedbackButtonsProps> = ({
  messageId,
  userId = 'default_user',
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
        user_id: userId,
        interaction_id: messageId,
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
    } finally {
      setSubmitting(false);
    }
  };

  const handleDetailSubmit = async () => {
    setSubmitting(true);
    try {
      await submitFeedback({
        user_id: userId,
        interaction_id: messageId,
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
