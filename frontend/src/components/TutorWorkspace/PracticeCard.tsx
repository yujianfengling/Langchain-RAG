import React, { useState } from 'react';
import { Alert, Button, Input, Radio, Space, Tag, Typography, message } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, SendOutlined } from '@ant-design/icons';
import { answerPractice } from '../../api/tutor';

const { Text } = Typography;

export interface PracticeQuestion {
  id: string;
  question: string;
  type?: string;
  options?: any[];
  knowledge_point?: string;
  node_id?: string | null;
}

const TYPE_LABEL: Record<string, string> = {
  single_choice: '单选',
  multiple_choice: '多选',
  judgement: '判断',
  short_answer: '简答',
};

/**
 * 练习与检测卡片。
 *
 * 判分完全由后端完成（不接受前端传入的 is_correct）；
 * 结果里区分"本次检测通过"与"需要复习"，不会因为一次答对就显示"完全掌握"。
 */
const PracticeCard: React.FC<{
  questions: PracticeQuestion[];
  subjectId: string;
  nodeId?: string | null;
  onAnswered?: (result: any) => void;
}> = ({ questions, subjectId, nodeId, onAnswered }) => {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const submit = async (q: PracticeQuestion) => {
    const value = (answers[q.id] || '').trim();
    if (!value) { message.warning('请先作答'); return; }
    setBusy(q.id);
    try {
      const data = await answerPractice({
        subject_id: subjectId,
        question_id: q.id,
        answer: value,
        node_id: nodeId || q.node_id || null,
        client_token: 'tutor-' + q.id + '-' + Date.now(),
      });
      setResults((prev) => ({ ...prev, [q.id]: data }));
      onAnswered?.(data);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交失败，请稍后重试');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="tutor-practice">
      {questions.map((q, index) => {
        const result = results[q.id];
        const attempt = result?.attempt;
        const options = (q.options || []).map((o: any) =>
          typeof o === 'string' ? { id: o, text: o } : { id: o.id, text: o.text });
        return (
          <div className="tutor-practice-item" key={q.id}>
            <div className="tutor-practice-head">
              <Tag color="blue">{TYPE_LABEL[q.type || ''] || q.type || '题目'}</Tag>
              <span className="tutor-practice-index">第 {index + 1} 题</span>
              {q.knowledge_point && <Text type="secondary" style={{ fontSize: 12 }}>{q.knowledge_point}</Text>}
            </div>
            <div className="tutor-practice-stem">{q.question}</div>
            {options.length > 0 ? (
              <Radio.Group
                value={answers[q.id]}
                disabled={Boolean(result)}
                onChange={(e) => setAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))}
              >
                <Space direction="vertical" size={4}>
                  {options.map((o: any) => (
                    <Radio key={o.id} value={o.id}>{o.text}</Radio>
                  ))}
                </Space>
              </Radio.Group>
            ) : (
              <Input.TextArea
                rows={2}
                disabled={Boolean(result)}
                placeholder="写下你的答案"
                value={answers[q.id] || ''}
                onChange={(e) => setAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))}
              />
            )}
            {!result && (
              <Button type="primary" size="small" icon={<SendOutlined />}
                      loading={busy === q.id} onClick={() => submit(q)} style={{ marginTop: 8 }}>
                提交作答
              </Button>
            )}
            {attempt && (
              <Alert
                style={{ marginTop: 8 }}
                type={attempt.is_correct ? 'success' : 'warning'}
                showIcon
                icon={attempt.is_correct ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
                message={attempt.is_correct ? '本次答对' : '本次答错'}
                description={(
                  <div style={{ fontSize: 12 }}>
                    {attempt.explanation && <div>{attempt.explanation}</div>}
                    {attempt.standard_answer && <div>参考答案：{attempt.standard_answer}</div>}
                    {result?.node && (
                      <div style={{ marginTop: 4 }}>
                        这个知识点：{result.node.status_label}
                        {result.node.mastery_level != null &&
                          '（掌握度约 ' + Math.round(result.node.mastery_level * 100) + '%）'}
                      </div>
                    )}
                  </div>
                )}
              />
            )}
          </div>
        );
      })}
    </div>
  );
};

export default PracticeCard;
