/**
 * 个人答卷模块 · 样例数据与读取（负责人可独立修改）
 * 题干台账 + 原始步骤 + 评分点 + 诊断。评分点加总必须等于 score。
 *
 * 重点样例 q-201（李明，移项先错）：原始答卷
 *   2x+3=11 → 2x=11+3=14 → x=7（没有代回检验）
 * 真正先错的是移项；由 2x=14 算出 x=7 除法本身正确，只是沿用前面错误的等式；
 * 没有检验步骤，所以"结果检验"不给分。
 * 演示评分（虚构，接真实数据时以赛方评分点为准）：2/2 + 0/4 + 2/2 + 0/2 = 4/10。
 */
import type { QuestionBankItem, StudentAnswer } from '../shared/types';
import { getBundle } from '../dataSource';
import type { AnswerEvent, RealAnswer } from '../dataSource';
import { STUDENTS } from '../shared/ids';

/** 题目台账（题干 + 分值 + 考点归属；出处由课标模块按 questionId 映射） */
export const QUESTION_BANK: QuestionBankItem[] = [
  { id: 'q-101', kpId: 'kp-term', stem: '合并同类项：3x + 2y − x + 5y', fullScore: 8 },
  { id: 'q-201', kpId: 'kp-eq', stem: '解方程：2x + 3 = 11，写出完整步骤', fullScore: 10 },
  { id: 'q-202', kpId: 'kp-system', stem: '解方程组：x + y = 5，x − y = 1', fullScore: 12 },
  { id: 'q-301', kpId: 'kp-ineq', stem: '解不等式：2x − 3 > 5', fullScore: 8 },
];

/** 演示样例答卷 */
export const STUDENT_ANSWERS: StudentAnswer[] = [
  {
    id: 'ans-201-001', studentId: 'stu-001', questionId: 'q-201', quizId: 'quiz-mid',
    steps: [
      { step: 1, text: '2x + 3 = 11' },
      { step: 2, text: '2x = 11 + 3 = 14' },
      { step: 3, text: 'x = 7' },
      // 说明：原始答卷没有第 4 步"代回检验"，故检验评分点 0/2
    ],
    score: 4, fullScore: 10,
    scoringPoints: [
      {
        id: 'sp1', label: '正确写出原方程', fullScore: 2, earnedScore: 2, ok: true,
        evidence: '第 1 步 2x + 3 = 11 抄写正确',
      },
      {
        id: 'sp2', label: '正确移项（+3 移到右边应变为 −3）', fullScore: 4, earnedScore: 0, ok: false,
        evidence: '第 2 步写成 2x = 11 + 3 = 14，移项没有变号；这里应是 2x = 11 − 3 = 8。这是本题第一个错误。',
      },
      {
        id: 'sp3', label: '在本人上一行基础上正确完成除法', fullScore: 2, earnedScore: 2, ok: true,
        evidence: '由本人第 2 行的 2x = 14 得 x = 7，除法本身计算正确；但最终答案受前一步移项错误影响，正确值应为 x = 4。',
      },
      {
        id: 'sp4', label: '把答案代回原方程检验', fullScore: 2, earnedScore: 0, ok: false,
        evidence: '原始答卷止于 x = 7，没有代入检验步骤；若检验 2×7+3=17≠11 应能发现自己错了。',
      },
    ],
    diagnosis:
      '失分从移项开始：+3 移到等号右边应变为 −3（正确是 2x = 11 − 3 = 8），学生写成了 +3。'
      + '由错误的 2x = 14 算出 x = 7，这一步除法本身算对了，但沿用了前面的错误等式。'
      + '另外答卷没有把答案代回原方程检验，错过了自查机会。',
    nextStep:
      '先专项练习移项变号，再养成把答案代回原方程检验的习惯。右侧建议按顺序完成。',
  },
  {
    id: 'ans-201-002', studentId: 'stu-002', questionId: 'q-201', quizId: 'quiz-mid',
    steps: [
      { step: 1, text: '2x + 3 = 11' },
      { step: 2, text: '2x = 11 − 3 = 8' },
      { step: 3, text: 'x = 4' },
      { step: 4, text: '检验：2×4 + 3 = 11，成立' },
    ],
    score: 10, fullScore: 10,
    scoringPoints: [
      { id: 'sp1', label: '正确写出原方程', fullScore: 2, earnedScore: 2, ok: true, evidence: '第 1 步正确' },
      { id: 'sp2', label: '正确移项', fullScore: 4, earnedScore: 4, ok: true, evidence: '11 − 3 = 8，变号正确' },
      { id: 'sp3', label: '正确完成除法', fullScore: 2, earnedScore: 2, ok: true, evidence: 'x = 4 正确' },
      { id: 'sp4', label: '代回原方程检验', fullScore: 2, earnedScore: 2, ok: true, evidence: '第 4 步代回检验成立' },
    ],
    diagnosis: '移项、求解、检验步骤完整正确，掌握良好。',
    nextStep: '可尝试含括号的方程，保持检验习惯。',
  },
  {
    id: 'ans-202-003', studentId: 'stu-003', questionId: 'q-202', quizId: 'quiz-mid',
    steps: [
      { step: 1, text: 'x + y = 5，x − y = 1' },
      { step: 2, text: '两式相加：2x = 6，x = 3' },
      { step: 3, text: '代入得 y = 2' },
    ],
    score: 9, fullScore: 12,
    scoringPoints: [
      { id: 'sp1', label: '列出方程组', fullScore: 3, earnedScore: 3, ok: true, evidence: '正确抄写' },
      { id: 'sp2', label: '消元求 x', fullScore: 5, earnedScore: 5, ok: true, evidence: '两式相加 2x=6，x=3 正确' },
      { id: 'sp3', label: '回代求 y 并写出解', fullScore: 4, earnedScore: 1, ok: false, evidence: '算出 y=2 但未写成方程组的解的形式' },
    ],
    diagnosis: '消元思路正确，但最后没有规范写出方程组的解。',
    nextStep: '解完后规范写出「x=3，y=2」的解。',
  },
];

export function getQuestion(questionId: string): QuestionBankItem | undefined {
  const bank = getBundle()?.questions || QUESTION_BANK;
  return bank.find((q) => q.id === questionId);
}

export function listQuestionsByKp(kpId: string): QuestionBankItem[] {
  const bank = getBundle()?.questions || QUESTION_BANK;
  return bank.filter((q) => q.kpId === kpId);
}

export function listAllQuestions(): QuestionBankItem[] {
  return getBundle()?.questions || QUESTION_BANK;
}

function studentClassMap(): Record<string, string> {
  const roster = getBundle()?.students || STUDENTS;
  return Object.fromEntries(roster.map((s) => [s.id, s.classId]));
}

/**
 * 按 学生＋题目（＋测验节点＋记录编号）定位作答。
 * 指定 quizId 时只在该节点内找；找不到返回 undefined，绝不跨测验拿另一场记录补上。
 * 官方数据同一节点同一题至多一条归并后事件；若有多条独立作答，用 recordId 区分。
 */
export function getAnswer(studentId: string, questionId: string, quizId?: string, recordId?: string): StudentAnswer | undefined {
  const answers = getBundle()?.answers || STUDENT_ANSWERS;
  let list = answers.filter((a) => a.studentId === studentId && a.questionId === questionId);
  if (quizId) list = list.filter((a) => a.quizId === quizId);
  if (recordId) list = list.filter((a) => (a as RealAnswer).sourceRecordId === recordId);
  return list[0];
}

/** 班级下钻用：按题目列出作答，限定测验节点与班级范围，避免混入另一场/另一班。 */
export function listAnswersByQuestion(questionId: string, opts?: { quizId?: string; classId?: string }): StudentAnswer[] {
  const answers = getBundle()?.answers || STUDENT_ANSWERS;
  const classOf = studentClassMap();
  return answers.filter((a) => a.questionId === questionId
    && (!opts?.quizId || a.quizId === opts.quizId)
    && (!opts?.classId || classOf[a.studentId] === opts.classId));
}

/** 某学生在所选测验的全部有效作答（官方模式直接进答卷页时的列表）。 */
export function listStudentAnswers(studentId: string, quizId?: string): StudentAnswer[] {
  const answers = getBundle()?.answers || STUDENT_ANSWERS;
  return answers.filter((a) => a.studentId === studentId && (!quizId || a.quizId === quizId));
}

/** 全量事件状态索引：指定测验无有效作答时，如实显示 待评分/未作答/无记录。 */
export function getAnswerEvent(studentId: string, questionId: string, quizId: string): AnswerEvent | undefined {
  return getBundle()?.events?.find((e) => e.studentId === studentId && e.questionId === questionId && e.quizId === quizId);
}

export function listStudentEvents(studentId: string, quizId?: string): AnswerEvent[] {
  return (getBundle()?.events || []).filter((e) => e.studentId === studentId && (!quizId || e.quizId === quizId));
}

/** 校验：每个样例答卷的评分点加总等于总分（开发期自检，构建时也会被调用） */
export function checkScoreConsistency(): { ok: boolean; detail: string } {
  const answers = getBundle()?.answers || STUDENT_ANSWERS;
  const bad = answers.filter((a) =>
    a.scoringPoints.reduce((sum, sp) => sum + sp.earnedScore, 0) !== a.score
    || a.scoringPoints.reduce((sum, sp) => sum + sp.fullScore, 0) !== a.fullScore);
  return bad.length === 0
    ? { ok: true, detail: `${answers.length} 条答卷评分点加总与总分一致` }
    : { ok: false, detail: `不一致的答卷：${bad.map((a) => a.id).join(', ')}` };
}
