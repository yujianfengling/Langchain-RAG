/**
 * 班级学情模块 · 样例数据与读取（负责人可独立修改）
 * 考点对比表：两班在同一考点的得分率 + 有效作答数，三种状态要能区分。
 */
import type { KnowledgePoint, KpClassStat } from '../shared/types';
import { getBundle } from '../dataSource';

/** 演示样例：五个考点，覆盖三个正式主题和三种特殊状态 */
export const KNOWLEDGE_POINTS: KnowledgePoint[] = [
  {
    id: 'kp-term', topicId: 't-algebra', name: '合并同类项',
    stats: [
      { classId: 'class-a', scoreRate: 0.72, validAttempts: 26, status: '正常', topWrongQuestionIds: ['q-101'] },
      { classId: 'class-b', scoreRate: 0.85, validAttempts: 28, status: '正常', topWrongQuestionIds: [] },
    ],
  },
  {
    id: 'kp-eq', topicId: 't-equation', name: '一元一次方程的解法（移项）',
    stats: [
      // 重点：李明的部分得分样例题 q-201 在这里下钻
      { classId: 'class-a', scoreRate: 0.62, validAttempts: 28, status: '正常', topWrongQuestionIds: ['q-201'] },
      { classId: 'class-b', scoreRate: 0.81, validAttempts: 30, status: '正常', topWrongQuestionIds: ['q-201'] },
    ],
  },
  {
    id: 'kp-system', topicId: 't-equation', name: '二元一次方程组的消元',
    stats: [
      { classId: 'class-a', scoreRate: 0.55, validAttempts: 24, status: '正常', topWrongQuestionIds: ['q-202'] },
      // 真实零分：有 22 条有效作答且得分率确为 0（区别于"本次未测"/"记录不足"）
      { classId: 'class-b', scoreRate: 0.0, validAttempts: 22, status: '真实零分', topWrongQuestionIds: ['q-202'] },
    ],
  },
  {
    id: 'kp-ineq', topicId: 't-inequality', name: '一元一次不等式的解法',
    stats: [
      { classId: 'class-a', scoreRate: 0.68, validAttempts: 21, status: '正常', topWrongQuestionIds: ['q-301'] },
      // 本次未测：该班本次没有该题型的作答记录
      { classId: 'class-b', scoreRate: null, validAttempts: 0, status: '本次未测', topWrongQuestionIds: [] },
    ],
  },
  {
    id: 'kp-ineq-app', topicId: 't-inequality', name: '不等式组的实际应用',
    stats: [
      // 记录不足：仅 2 条有效作答，不足以判定
      { classId: 'class-a', scoreRate: null, validAttempts: 2, status: '记录不足', topWrongQuestionIds: [] },
      { classId: 'class-b', scoreRate: 0.7, validAttempts: 20, status: '正常', topWrongQuestionIds: [] },
    ],
  },
];

export function listKnowledgePoints(topicId?: string, quizId?: string): KnowledgePoint[] {
  let source = getBundle()?.kps || KNOWLEDGE_POINTS;
  if (quizId) {
    // 官方 bundle 的考点带 quizId（同一次测验才可比）；演示样例无此字段，不过滤。
    const withQuiz = source.filter((k) => (k as KnowledgePoint & { quizId?: string }).quizId);
    if (withQuiz.length) source = withQuiz.filter((k) => (k as KnowledgePoint & { quizId?: string }).quizId === quizId);
  }
  return topicId ? source.filter((k) => k.topicId === topicId) : source;
}

export function getKpStat(kp: KnowledgePoint, classId: string): KpClassStat | undefined {
  return kp.stats.find((s) => s.classId === classId);
}

export function getKnowledgePoint(kpId: string): KnowledgePoint | undefined {
  return KNOWLEDGE_POINTS.find((k) => k.id === kpId);
}

/**
 * 首屏「最值得看的一个差异」：两班都有有效得分率、且得分率差最大的考点。
 * 全部由当前 bundle / 演示样例现算，不写死任何数字。
 */
export interface HeadlineDiff {
  kpName: string;
  rateA: number;
  rateB: number;
  gap: number;
}
export function getHeadlineDiff(kps: KnowledgePoint[], classA: string, classB: string): HeadlineDiff | null {
  let best: HeadlineDiff | null = null;
  for (const kp of kps) {
    const a = kp.stats.find((s) => s.classId === classA);
    const b = kp.stats.find((s) => s.classId === classB);
    if (!a || !b || a.scoreRate == null || b.scoreRate == null) continue;
    const gap = Math.abs(a.scoreRate - b.scoreRate);
    if (!best || gap > best.gap) {
      best = { kpName: kp.name, rateA: a.scoreRate, rateB: b.scoreRate, gap };
    }
  }
  return best;
}

/** 官方三名代表性案例：展示字段 + 学生答卷页跳转参数（数据只来自 caseBook） */
export interface ShowcaseCase {
  studentId: string;
  caseName: string;        // case_name：展示案例类型（克制标签用这个）
  coverageVerdict: string; // case：考点覆盖判定（与 case_name 并列诚实展示，避免混淆）
  nodeId: string;
  indicatorId: string;
  score: number;
  fullScore: number;
  selectionReason: string;
  questionId: string;
  classId: string;
}
export function getShowcaseCases(): ShowcaseCase[] {
  const bundle = getBundle();
  const cases = bundle?.caseBook?.cases ?? [];
  return cases.slice(0, 3).map((c) => {
    const questionId = c.question_findings?.[0]?.item_id
      || bundle?.questions.find((q) => q.kpId === c.indicator_id)?.id
      || bundle?.questions[0]?.id
      || '';
    const classId = bundle?.students.find((s) => s.id === c.student_id)?.classId ?? '';
    return {
      studentId: c.student_id,
      caseName: c.case_name,
      coverageVerdict: c.case,
      nodeId: c.node_id,
      indicatorId: c.indicator_id,
      score: c.score,
      fullScore: c.full_score,
      selectionReason: c.selection_reason,
      questionId,
      classId,
    };
  });
}
