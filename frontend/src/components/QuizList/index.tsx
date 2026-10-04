import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, Input, List, Radio, Space, Tag, Tooltip, Typography, message } from 'antd';
import { EyeOutlined, SendOutlined, CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import {
  PublicQuestion, AttemptResult, submitAnswer, revealAnswer, selfEvaluate,
} from '../../api/student';
import { isLoggedIn } from '../../api/auth';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

/**
 * 自测题列表（服务端权威判分）
 *
 * 关键约束：
 * - 题目 payload 不含标准答案；答案只能通过「提交作答」或「查看答案」获得
 * - 对错由后端判分，前端不自行判定，也不提交 is_correct
 * - 「查看答案」会被后端记录，此后该题的作答不计入独立掌握证据
 */
export interface QuizListProps {
  quizzes: PublicQuestion[];
  subjectId?: string;
  /** 来源任务：从学习计划进入时携带，用于把作答与任务关联 */
  sourceTaskId?: string;
  conversationId?: string;
  /** 完成一次作答后回调（供父组件刷新掌握度/错题/复习） */
  onAnswered?: (result: AttemptResult, question: PublicQuestion) => void;
  /** 紧凑模式（聊天卡片内使用） */
  compact?: boolean;
}

type QState = {
  choice?: string;
  text?: string;
  submitting?: boolean;
  result?: AttemptResult;
  answer?: { correct_option_id?: string | null; reference_answer?: string | null; explanation?: string | null };
  error?: string;
};

const typeLabel = (t: string) =>
  t === 'single_choice' ? '单选题' : t === 'judgement' ? '判断题' : '简答题';

const typeColor = (t: string) =>
  t === 'single_choice' ? 'cyan' : t === 'judgement' ? 'green' : 'orange';

const FROM_MATERIAL = { color: 'green', label: '你的资料', tip: '题目来自你上传的知识库资料' };

/**
 * 题目来源标签（2026-10-02）
 *
 * 本地资料覆盖不到时，出题会降级到网络资料或 AI 通用知识。
 * 必须在界面上说清楚，否则学生会把通用题当成"我上传的资料里有这道题"。
 */
const SOURCE_META: Record<string, { color: string; label: string; tip: string }> = {
  knowledge_base: FROM_MATERIAL,
  note: FROM_MATERIAL,
  quiz: FROM_MATERIAL,
  web_search: {
    color: 'blue', label: '网络资料',
    tip: '本地资料不足，题目由网络公开资料整理，答案已由 AI 重新核对',
  },
  general_knowledge: {
    color: 'orange', label: 'AI 通用知识',
    tip: '本地资料不足，题目由 AI 通用知识生成，表述可能与你的教材不同',
  },
};

/** 生成幂等令牌：同一次作答重复提交只计一次（刷新/重试安全）
 *
 * 修复（2026-10-03）：此前每次调用都带 `Date.now()` + 随机串，于是"重试/双击"
 * 必然生成新令牌，后端 `client_token` 幂等**永不命中** → 同一题被重复计数
 * （作答数/趋势虚高）。现在按题目缓存令牌，**只有提交成功后**才轮换：
 *   - 双击 / 失败重试：同一令牌 → 后端返回既有结果，不重复计分；
 *   - 学生有意重做：成功后轮换令牌 → 记为一次新的作答（保留重做语义）。
 */
const makeToken = (questionId: string, cache: Record<string, string>) => {
  if (!cache[questionId]) {
    cache[questionId] = `att-${questionId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  }
  return cache[questionId];
};

export const QuizList: React.FC<QuizListProps> = ({
  quizzes, subjectId, sourceTaskId, conversationId, onAnswered, compact,
}) => {
  const [states, setStates] = useState<Record<string, QState>>({});
  // 幂等令牌缓存（见 makeToken 注释）：按题目保存，成功后删除
  const tokenCache = useRef<Record<string, string>>({});
  const [loggedIn, setLoggedIn] = useState<boolean>(isLoggedIn());

  useEffect(() => {
    const sync = () => setLoggedIn(isLoggedIn());
    window.addEventListener('storage', sync);
    window.addEventListener('edu:auth-changed', sync);
    return () => {
      window.removeEventListener('storage', sync);
      window.removeEventListener('edu:auth-changed', sync);
    };
  }, []);

  if (!quizzes || !quizzes.length) return <Empty description="暂无自测题" />;

  const patch = (id: string, next: Partial<QState>) =>
    setStates((prev) => ({ ...prev, [id]: { ...prev[id], ...next } }));

  const handleSubmit = async (q: PublicQuestion) => {
    const st = states[q.id] || {};
    const answer = q.type === 'short_answer' ? (st.text || '').trim() : st.choice;
    if (!answer) {
      message.warning(q.type === 'short_answer' ? '请先填写答案' : '请先选择一个选项');
      return;
    }
    patch(q.id, { submitting: true, error: undefined });
    try {
      const res = await submitAnswer({
        question_id: q.id,
        user_answer: answer,
        subject_id: subjectId,
        client_token: makeToken(q.id, tokenCache.current),
        source_task_id: sourceTaskId,
        conversation_id: conversationId,
      });
      const data = (res as any)?.data as AttemptResult | undefined;
      if (data) {
        patch(q.id, { result: data, submitting: false });
      delete tokenCache.current[q.id];   // 成功后轮换：重做算新的一次作答
        onAnswered?.(data, q);
        if (data.grade_status === 'ai_graded') {
          message.info(data.ai_feedback
            ? `AI 评判：${data.ai_feedback}`
            : 'AI 已完成评判，不认同可自行改判');
        } else if (data.grade_status === 'pending_self_eval') {
          message.info('AI 判分不可用，已记录作答，请自行对照参考要点评估');
        } else if (data.is_correct) {
          message.success('回答正确');
        } else {
          message.warning('回答错误，建议复习该知识点');
        }
      } else {
        patch(q.id, { submitting: false, error: (res as any)?.message || '提交失败' });
      }
    } catch (e: any) {
      const msg = e?.response?.data?.message || '提交失败，请稍后重试';
      patch(q.id, { submitting: false, error: msg });
    }
  };

  const handleReveal = async (q: PublicQuestion) => {
    try {
      const res = await revealAnswer(q.id);
      const data = (res as any)?.data;
      patch(q.id, {
        answer: data || { explanation: '暂无解析' },
        error: undefined,
      });
      message.info('已显示答案：该题后续作答不再计入独立掌握证据');
    } catch (e: any) {
      patch(q.id, { error: e?.response?.data?.message || '查看答案失败' });
    }
  };

  const handleSelfEvaluate = async (q: PublicQuestion, isCorrect: boolean) => {
    const attemptId = states[q.id]?.result?.attempt_id;
    if (!attemptId) return;
    try {
      await selfEvaluate(attemptId, isCorrect);
      patch(q.id, {
        result: { ...(states[q.id].result as AttemptResult), grade_status: 'self_evaluated', is_correct: isCorrect },
      });
      message.success('已按你的判断记录（自评不等同客观判分）');
    } catch (e: any) {
      patch(q.id, { error: e?.response?.data?.message || '自评失败' });
    }
  };

  return (
    <List
      dataSource={quizzes}
      renderItem={(q, idx) => {
        const st = states[q.id] || {};
        const options = Array.isArray(q.options) ? q.options : [];
        const persisted = q.server_persisted !== false;
        const answered = !!st.result;
        return (
          <List.Item>
            <div style={{ width: '100%' }}>
              <div style={{ marginBottom: 8 }}>
                <Tag color={typeColor(q.type)}>{typeLabel(q.type)}</Tag>
                {q.source && SOURCE_META[q.source] && (
                  <Tooltip title={SOURCE_META[q.source].tip}>
                    <Tag color={SOURCE_META[q.source].color}>{SOURCE_META[q.source].label}</Tag>
                  </Tooltip>
                )}
                <Text strong>
                  {idx + 1}. {q.question}
                </Text>
              </div>

              {/* 选项选择区（单选/判断）——提交的是选项 ID */}
              {(q.type === 'single_choice' || q.type === 'judgement') && (
                // display:block —— antd 的 Radio.Group 默认 inline-block，
                // 否则后面的按钮会与第一个选项挤在同一行
                <Radio.Group
                  onChange={(e) => patch(q.id, { choice: e.target.value })}
                  value={st.choice}
                  disabled={answered || st.submitting}
                  style={{ display: 'block', marginBottom: 8 }}
                >
                  <Space direction="vertical">
                    {options.map((opt) => (
                      <Radio key={opt.id} value={opt.id}>
                        {opt.id}. {opt.text}
                      </Radio>
                    ))}
                  </Space>
                </Radio.Group>
              )}

              {q.type === 'short_answer' && (
                <TextArea
                  rows={compact ? 2 : 3}
                  value={st.text}
                  onChange={(e) => patch(q.id, { text: e.target.value })}
                  disabled={answered || st.submitting}
                  placeholder="请作答（简答题提交后由 AI 评判，你仍可自行改判）"
                  style={{ marginBottom: 8 }}
                />
              )}

              {/* 未入库题目：明确提示不可判分，不静默假装能提交 */}
              {!persisted && (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginBottom: 8 }}
                  message="该题未成功入库，无法记录作答与判分"
                />
              )}

              <div style={{ marginBottom: 4 }}>
              <Space wrap>
                <Button
                  type="primary"
                  size="small"
                  icon={<SendOutlined />}
                  loading={st.submitting}
                  disabled={!persisted || answered || !loggedIn}
                  onClick={() => handleSubmit(q)}
                >
                  提交作答
                </Button>
                <Button
                  size="small"
                  icon={<EyeOutlined />}
                  disabled={!persisted || !loggedIn || !!st.answer}
                  onClick={() => handleReveal(q)}
                >
                  查看答案
                </Button>
                {!loggedIn && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    登录后可提交作答并记录学习进度
                  </Text>
                )}
              </Space>
              </div>

              {st.error && (
                <Alert type="error" showIcon style={{ marginTop: 8 }} message={st.error} />
              )}

              {/* 判分结果（提交后才返回标准答案与解析） */}
              {answered && (
                <Alert
                  type={st.result?.is_correct === true ? 'success'
                    : st.result?.is_correct === false ? 'error' : 'info'}
                  showIcon
                  style={{ marginTop: 8 }}
                  icon={st.result?.is_correct === true ? <CheckCircleOutlined />
                    : st.result?.is_correct === false ? <CloseCircleOutlined /> : undefined}
                  message={
                    <Space direction="vertical" size={4}>
                      {st.result?.grade_status === 'graded' ? (
                        <Text strong>
                          {st.result.is_correct ? '回答正确' : '回答错误'}
                          {st.result.attempt_kind === 'after_reveal' && '（查看答案后作答，不计入独立掌握证据）'}
                          {st.result.attempt_kind === 'review' && '（复习重做）'}
                        </Text>
                      ) : st.result?.grade_status === 'ai_graded' ? (
                        <Text strong>
                          AI 评判：{st.result.is_correct ? '回答正确' : '回答错误'}
                          {st.result.attempt_kind === 'after_reveal' && '（查看答案后作答，不计入独立掌握证据）'}
                        </Text>
                      ) : (
                        <Text strong>
                          {st.result?.grade_status === 'self_evaluated' ? '已自评' : '简答题：待自评'}
                        </Text>
                      )}
                      <Text>
                        你的作答：{st.result?.user_answer_text || st.result?.user_answer || '—'}
                      </Text>
                      {st.result?.correct_option_id && (
                        <Text type="success">
                          正确答案：{st.result.correct_option_id}
                          {st.result.correct_option_text ? `（${st.result.correct_option_text}）` : ''}
                        </Text>
                      )}
                      {!st.result?.correct_option_id && st.result?.reference_answer && (
                        <Text type="secondary">参考要点：{st.result.reference_answer}</Text>
                      )}
                      {st.result?.explanation && (
                        <Text type="secondary">解析：{st.result.explanation}</Text>
                      )}
                      {st.result?.ai_feedback && (
                        <Text type="secondary">AI 评判理由：{st.result.ai_feedback}</Text>
                      )}
                      {(st.result?.grade_status === 'pending_self_eval'
                        || st.result?.grade_status === 'ai_graded') && (
                        <Space>
                          <Text type="secondary">
                            {st.result?.grade_status === 'ai_graded'
                              ? '不认同 AI 的评判？可以自行改判：'
                              : '请对照参考要点自评：'}
                          </Text>
                          <Button size="small" onClick={() => handleSelfEvaluate(q, true)}>我答对了</Button>
                          <Button size="small" danger onClick={() => handleSelfEvaluate(q, false)}>我答错了</Button>
                        </Space>
                      )}
                    </Space>
                  }
                />
              )}

              {/* 查看答案（后端已记录，后续作答将被标记为 after_reveal） */}
              {st.answer && !answered && (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginTop: 8 }}
                  message={
                    <Space direction="vertical" size={4}>
                      {st.answer.correct_option_id && (
                        <Text strong>正确答案：{st.answer.correct_option_id}</Text>
                      )}
                      {st.answer.reference_answer && (
                        <Paragraph style={{ marginBottom: 0 }}>
                          参考答案：{st.answer.reference_answer}
                        </Paragraph>
                      )}
                      {st.answer.explanation && (
                        <Text type="secondary">解析：{st.answer.explanation}</Text>
                      )}
                    </Space>
                  }
                />
              )}
            </div>
          </List.Item>
        );
      }}
    />
  );
};

export default QuizList;
