/**
 * 课标与教材模块 · 样例数据与读取（负责人可独立修改）
 * 课标要求 + 题目出处（questionId → 教材页码）+ 考点反查。
 *
 * 教材页内容（自制公开样例 PDF）：
 *   第 2 页 合并同类项 / 第 3 页 方程移项与检验 / 第 4 页 方程组消元
 *   第 5 页 一元一次不等式 / 第 6 页 不等式组应用
 */
import type { CurriculumStandard, TextbookSource } from '../shared/types';
import { textbookSource } from '../shared/ids';
import { getBundle } from '../dataSource';

export const CURRICULUM_STANDARDS: CurriculumStandard[] = [
  {
    kpId: 'kp-term', level: '理解',
    requirement: '理解同类项概念，会合并同类项进行整式的加减运算。',
  },
  {
    kpId: 'kp-eq', level: '掌握',
    requirement: '掌握一元一次方程的解法，理解移项要变号，会检验方程的解。',
  },
  {
    kpId: 'kp-system', level: '掌握',
    requirement: '会用代入消元法和加减消元法解二元一次方程组，能规范写出方程组的解。',
  },
  {
    kpId: 'kp-ineq', level: '掌握',
    requirement: '会解一元一次不等式，理解两边同乘除负数时不等号方向改变。',
  },
  {
    kpId: 'kp-ineq-app', level: '运用',
    requirement: '能根据实际问题列不等式组并求公共解集。',
  },
];

/** 题目出处映射（按 questionId 关联，答卷页反查教材就走这里） */
const QUESTION_SOURCES: Record<string, TextbookSource> = {
  'q-101': textbookSource(2, '第 2 页 · 例1 合并同类项'),
  'q-201': textbookSource(3, '第 3 页 · 例2 解方程 2x+3=11（移项与检验）'),
  'q-202': textbookSource(4, '第 4 页 · 例3 方程组消元'),
  'q-301': textbookSource(5, '第 5 页 · 例4 一元一次不等式'),
};

export function getCurriculum(kpId: string): CurriculumStandard | undefined {
  const ind = (kpId.match(/-(T\d+I\d+)$/) || [])[1] || kpId;
  const official = getBundle()?.curriculumMap?.indicators.find((c) => c.indicator_id === ind);
  if (official) {
    return {
      kpId, level: '理解',
      requirement: `${official.requirement}（课标整理编号 ${official.standard.code || '待定位'}）`,
    };
  }
  return CURRICULUM_STANDARDS.find((c) => c.kpId === kpId);
}

/** 从题目反查教材出处：官方按指标映射，演示样例按固定表 */
export function getSourceByQuestion(questionId: string): TextbookSource | undefined {
  const bundle = getBundle();
  if (bundle?.curriculumMap) {
    const q = bundle.questions.find((x) => x.id === questionId);
    const ind = q?.kpId;
    const cur = bundle.curriculumMap.indicators.find((c) => c.indicator_id === ind);
    const page = cur?.textbook_pages?.[0];
    if (cur && page) {
      return {
        docId: page.material_id, docName: `赛方教材切片 ${page.material_id}`, page: page.pdf_page,
        exampleTitle: `${cur.requirement}；${page.anchor}（${page.verified ? '已核对' : '待定位'}）`,
        pdfUrl: `/api/competition/official-pdf/${page.material_id}`,
        printedPage: page.printed, verified: page.verified,
        standard: cur.standard.code ? {
          code: cur.standard.code,
          pdfPage: cur.standard.pdf_page ?? null, printed: cur.standard.printed ?? null,
        } : undefined,
      };
    }
    if (cur) {
      return {
        docId: '待定位', docName: '教材页待定位', page: 1,
        exampleTitle: `${cur.requirement}；可确认范围：${cur.textbook_range}`, verified: false,
      };
    }
  }
  return QUESTION_SOURCES[questionId];
}

/** 从教材页码反查考点（点出处回到考点） */
export function getKpBySourcePage(page: number): string | undefined {
  const map: Record<number, string> = { 2: 'kp-term', 3: 'kp-eq', 4: 'kp-system', 5: 'kp-ineq' };
  return map[page];
}
