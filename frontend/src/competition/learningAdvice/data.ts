/**
 * 学习建议模块 · 样例数据与读取（负责人可独立修改）
 * 练习建议（教材例题 / 练习题顺序 / 分步提示）+ 学生反馈预览。
 * 与 q-201（移项先错、除法对、缺检验）的诊断口径保持一致。
 */
import type { PracticeSuggestion, StudentFeedback } from '../shared/types';
import { textbookSource } from '../shared/ids';
import { isRealData } from '../dataSource';

export const PRACTICE_SUGGESTIONS: PracticeSuggestion[] = [
  {
    id: 'sug-eq', kpId: 'kp-eq',
    textbookExample:
      '教材例题（第 3 页）：2x + 3 = 11 —— 移项时 +3 要变号为 −3，得 2x = 11 − 3 = 8，x = 4；最后把 x=4 代回原式检验。',
    textbookSource: textbookSource(3, '第 3 页 · 例2 解方程 2x+3=11（移项与检验）'),
    exercises: [
      {
        order: 1,
        title: '移项变号：5x + 2 = 17',
        hint: '先把 +2 移到右边，记得变号',
        expandableHint: '先把 +2 从等号左边移走，检查它在右边有没有变号；再把 x 的系数除掉。做完把结果代回原方程，看左右两边是否相等。这里不给最终数值，请学生自己算完再核对。',
      },
      {
        order: 2,
        title: '移项变号：4x − 7 = 9',
        hint: '−7 移到右边变成 +7',
        expandableHint: '这一题先只处理符号：把 −7 移到右边后它应该变成什么？确认后再合并常数项，最后一步才除去 x 的系数。算完代回原式自查。',
      },
      {
        order: 3,
        title: '代回检验：把上面两题的解代回原式',
        hint: '解完一定要代回原方程看左右是否相等',
        expandableHint: '把你自己算出的数分别代回左边和右边，分开算两遍再比较。若两边不相等，先回头查移项那一步的符号，不要急着改最后的除法。',
      },
    ],
  },
  {
    id: 'sug-term', kpId: 'kp-term',
    textbookExample: '教材例题（第 2 页）：3x + 2y − x + 5y = (3x − x) + (2y + 5y) = 2x + 7y。',
    textbookSource: textbookSource(2, '第 2 页 · 例1 合并同类项'),
    exercises: [
      {
        order: 1,
        title: '合并同类项：4a + 3b − 2a + b',
        hint: 'a 与 a 合并，b 与 b 合并',
        expandableHint: '先用括号把含 a 的项圈在一起、含 b 的项圈在一起，再分别合并系数。留意每一项前面的正负号属于哪一项；字母不同的项不能相加。',
      },
    ],
  },
  {
    id: 'sug-system', kpId: 'kp-system',
    textbookExample: '教材例题（第 4 页）：x+y=5，x−y=1，两式相加消元得 x=3，y=2。',
    textbookSource: textbookSource(4, '第 4 页 · 例3 方程组消元'),
    exercises: [
      {
        order: 1,
        title: '解方程组：x + y = 8，x − y = 2',
        hint: '两式相加消去 y',
        expandableHint: '先观察 y 的系数：两式相加还是相减能把它消掉？消元后只解一个未知数，再把这个值代回任意一个原式求另一个。最后用大括号写出完整的一组解。',
      },
    ],
  },
  {
    id: 'sug-ineq', kpId: 'kp-ineq',
    textbookExample: '教材例题（第 5 页）：2x − 3 > 5，移项得 2x > 8，x > 4；乘除负数时不等号要反向。',
    textbookSource: textbookSource(5, '第 5 页 · 例4 一元一次不等式'),
    exercises: [
      {
        order: 1,
        title: '解不等式：−2x + 1 > 5',
        hint: '两边除以 −2 时不等号要反向',
        expandableHint: '先按解不等式的顺序把常数项移到右边，再处理 x 的系数。做到「两边同除以负数」那一步时停一下：不等号方向要不要改？改完再随便取一个数代回原式验证范围。',
      },
    ],
  },
];

/** 学生反馈预览：说法与答卷诊断一致（移项先错、除法本身对、缺检验） */
export const STUDENT_FEEDBACKS: StudentFeedback[] = [
  {
    studentId: 'stu-001',
    summary:
      '这次方程题最先错在移项：+3 移到右边应该变成 −3，你写成了 +3，所以后面等式从 2x=14 开始就偏了。'
      + '不过由 2x=14 算出 x=7 这一步除法本身是对的，只是用错了前面的等式。另外答卷没有代回检验，错过了自查。',
    todo: [
      '完成练习 1、2：练移项变号（+2→−2，−7→+7）',
      '完成练习 3：把答案代回原方程检验',
      '回看教材第 3 页例2的移项与检验步骤',
    ],
    hints: [
      { title: '移项口诀', detail: '移项要变号：+ 变 −，− 变 +。2x + 3 = 11 应变成 2x = 11 − 3。' },
      { title: '检验方法', detail: '把求得的 x 代回原方程。代 x=7：2×7+3=17≠11，就能发现不对；正确 x=4 时 2×4+3=11。' },
    ],
  },
  {
    studentId: 'stu-002',
    summary: '移项、求解、代回检验三步都做对了，掌握扎实，可以继续练习带括号的方程。',
    todo: ['尝试带括号的方程，保持检验习惯'],
    hints: [{ title: '列方程思路', detail: '先去括号，再移项，最后检验。' }],
  },
  {
    studentId: 'stu-003',
    summary: '方程组消元思路正确，x=3 算对了；最后要规范写出「x=3，y=2」的解。',
    todo: ['重做 q-202 并规范写出方程组的解'],
    hints: [{ title: '作答规范', detail: '解出所有未知数后，用大括号写出方程组的解。' }],
  },
];

/**
 * 真实数据模式：正式推荐算法尚未接入，一律返回空。
 * 绝不把 PRACTICE_SUGGESTIONS / STUDENT_FEEDBACKS 里的虚构建议挂到真实学生身上。
 */
export function getSuggestion(kpId: string): PracticeSuggestion | undefined {
  if (isRealData()) return undefined;
  return PRACTICE_SUGGESTIONS.find((s) => s.kpId === kpId);
}

export function getFeedback(studentId: string): StudentFeedback | undefined {
  if (isRealData()) return undefined;
  return STUDENT_FEEDBACKS.find((f) => f.studentId === studentId);
}
