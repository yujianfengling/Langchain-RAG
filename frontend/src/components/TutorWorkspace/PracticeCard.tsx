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
  /** 后端带回的"我上次的作答"（历史回看时用；本次会话内的提交走内部 state） */
  attempt?: any;
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
  /** 2026-10-31（ui-simplify）：作答后的低干扰行动入口（复用现有能力，不新增系统） */
  onAskExplain?: () => void;
  onPracticeMore?: () => void;
  onNextNode?: () => void;
  nextNodeLabel?: string | null;
}> = ({ questions, subjectId, nodeId, onAnswered,
       onAskExplain, onPracticeMore, onNextNode, nextNodeLabel }) => {
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
        // 后端带回来的"上次作答"要能当成本次结果展示——否则已答过的题看起来
        // 像没做过，学生会重复作答，历史记录与知识点状态也就对不上了。
        const prior = q.attempt ? { attempt: q.attempt, prior: true } : undefined;
        const result = results[q.id] || prior;
        const attempt = result?.attempt;
        const options = (q.options || []).map((o: any) =>
          typeof o === 'string' ? { id: o, text: o } : { id: o.id, text: o.text });
        const priorAnswer = (result as any)?.prior
          ? (attempt?.user_answer || attempt?.user_answer_text || '')
          : '';
        const selected = answers[q.id] || priorAnswer;
        const optionText = (id?: string | null) => {
          const found = options.find((o: any) => o.id === id);
          return found ? found.text : (id || '');
        };
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
                value={selected || undefined}
                disabled={Boolean(result)}
                onChange={(e) => setAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))}
              >
                <Space direction="vertical" size={4}>
                  {options.map((o: any) => (
                    <Radio key={o.id} value={o.id}>
                      <span>
                        {o.text}
                        {selected === o.id && (
                          <Tag color="blue" style={{ marginLeft: 6 }}>你的选择</Tag>
                        )}
                        {(result as any)?.prior && attempt?.correct_option_id === o.id
                          && selected !== o.id && (
                          <Tag color="green" style={{ marginLeft: 6 }}>正确答案</Tag>
                        )}
                      </span>
                    </Radio>
                  ))}
                </Space>
              </Radio.Group>
            ) : (
              <Input.TextArea
                rows={2}
                disabled={Boolean(result)}
                placeholder="写下你的答案"
                value={answers[q.id] || priorAnswer}
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
                message={(attempt.is_correct ? '答对' : '答错')
                  + ((result as any)?.prior ? '（这是你上次的作答）' : '本次')}
                description={(
                  <div style={{ fontSize: 12 }}>
                    {(result as any)?.prior && selected && (
                      <div>你的作答：{optionText(attempt.user_answer) || attempt.user_answer_text || selected}</div>
                    )}
                    {attempt.explanation && <div>{attempt.explanation}</div>}
                    {attempt.standard_answer && <div>参考答案：{attempt.standard_answer}</div>}
                    {(result as any)?.prior && attempt.correct_option_id && (
                      <div>正确答案：{optionText(attempt.correct_option_id) || attempt.correct_option_id}</div>
                    )}
                    {result?.node && (
                      <div style={{ marginTop: 4 }}>
                        这个知识点：{result.node.status_label}
                        {result.node.mastery_level != null &&
                          '（掌握度约 ' + Math.round(result.node.mastery_level * 100) + '%）'}
                      </div>
                    )}
                    {/* 2026-10-31（ui-simplify）：作答后明确下一步——答错可直接
                        换例子讲解/再练一道；答对可继续下一知识点。学生决定何时点。 */}
                    {!attempt.is_correct && (onAskExplain || onPracticeMore) && (
                      <Space size={4} wrap style={{ marginTop: 8 }}>
                        {onAskExplain && (
                          <Button size="small" onClick={onAskExplain}>换个例子讲解</Button>
                        )}
                        {onPracticeMore && (
                          <Button size="small" onClick={onPracticeMore}>再练一道</Button>
                        )}
                      </Space>
                    )}
                    {/* 只有可靠归属的答对题、且存在**不同于当前**的下一节点时才显示；
                        "待确认"归属的题不引导继续（不计入精确掌握）。 */}
                    {attempt.is_correct && onNextNode && nextNodeLabel
                      && q.knowledge_point !== '待确认' && (
                      <div style={{ marginTop: 8 }}>
                        <Button size="small" type="primary" onClick={onNextNode}>
                          继续：{nextNodeLabel}
                        </Button>
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
