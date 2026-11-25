import React, { useMemo } from 'react';
import { Button, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';
import { isPrivateCompetitionData } from '../dataSource';
import type { StudentProfileData } from './studentProfileData';

const { Text } = Typography;
const rateOf = (score: number, full: number) => full > 0 ? score / full * 100 : null;
const pct = (v: number | null) => v == null ? '—' : `${v.toFixed(0)}%`;

export interface AiDiagnosisCardProps {
  profile: StudentProfileData;
  studentId: string;
  quizId: string;
  studentName: string;
}

/**
 * 首屏 AI 学情诊断：本地即时结论始终可见（不联网）；
 * 深度分析统一跳转 AI 助手页，自动发送带学生/测验上下文的提示词，结果需教师核对。
 */
const AiDiagnosisCard: React.FC<AiDiagnosisCardProps> = ({ profile, studentId, quizId, studentName }) => {
  const navigate = useNavigate();

  const current = profile.history.find((q) => q.id === quizId)?.totals;
  const weak = useMemo(() => profile.indicators
    .filter((r) => r.status === '部分得分' || r.status === '有效零分')
    .sort((a, b) => (rateOf(a.totals.score, a.totals.fullScore) ?? 101) - (rateOf(b.totals.score, b.totals.fullScore) ?? 101)), [profile.indicators]);
  const insufficient = profile.indicators.filter((r) => r.status === '记录不足');

  return <section className="teacher-section student-ai-diagnosis" aria-label="AI 学情诊断" data-testid="ai-diagnosis">
    <div className="teacher-section-heading"><h3 style={{ margin: 0 }}>AI 学情诊断</h3>
      <Text type="secondary">本地即时结论 + AI 助手深度分析</Text></div>
    <div className="student-ai-local">
      <p><strong>本次总体：</strong>{current && current.scoredCount
        ? <>有效得分 {current.score}/{current.fullScore} 分（得分率 {pct(rateOf(current.score, current.fullScore))}），{current.scoredCount} 条有效记录。</>
        : '本次暂无有效已评分记录，不据此判断掌握情况。'}</p>
      <p><strong>薄弱考点：</strong>{weak.length
        ? weak.slice(0, 4).map((r) => `${r.name}（${pct(rateOf(r.totals.score, r.totals.fullScore))}）`).join('、')
        : '本次未定位到有效失分考点，不等同于全部掌握。'}</p>
      {insufficient.length > 0 && <p><strong>证据不足：</strong>{insufficient.length} 个考点记录不足，需补充同考点作答后再判断，不按零分处理。</p>}
      {profile.nextSteps[0] && <p><strong>建议下一步：</strong>{profile.nextSteps[0].title} —— {profile.nextSteps[0].reason}</p>}
    </div>
    <div className="teacher-actions student-ai-actions">
      <Button type="primary" data-testid="jump-assistant-student"
        onClick={() => navigate(`/assistant?prompt=${encodeURIComponent(`请分析学生 ${studentId} 在测验 ${quizId} 的本次与历次学情，指出薄弱与下一步。`)}&autosend=1`)}>
        AI 深度分析（跳转 AI 助手）
      </Button>
      <Text type="secondary">{isPrivateCompetitionData() ? `跳转 AI 助手为 ${studentName || studentId} 自动发送学情分析请求，结果需教师核对。` : '演示样例下同样可跳转，结果仅供演示。'}</Text>
    </div>
  </section>;
};

export default AiDiagnosisCard;