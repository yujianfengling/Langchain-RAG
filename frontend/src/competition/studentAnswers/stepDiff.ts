/** 解题步骤 diff：参考解法与学生解答的单调等价对齐。
 *  参考解析可能是细分子步（a 项/b 项分开），学生可能合并步骤，
 *  因此不按行号硬对齐：每个学生步向前找首个等价的参考步；
 *  找不到 = 首个分歧步；参考剩余未匹配 = 缺步；学生多出 = 多步。
 *  等价口径复用 expr.ts 的规范线性形（等价写法判对），不可解析时退回收尾归一字符串比较。 */
import { parseLinear, sameLinear, stripPrefix, diffTerms } from '../livePretest/expr';

export type DiffStatus = 'match' | 'diverge' | 'missing' | 'extra' | 'after';

export interface DiffRow {
  key: string;
  stuIdx: number | null;
  refIdx: number | null;
  stu: string | null;
  ref: string | null;
  status: DiffStatus;
  diffs: string[];
}

export interface StepDiff {
  rows: DiffRow[];
  first: number | null;
  firstKind: 'diverge' | 'missing' | 'extra' | null;
  refCount: number;
  stuCount: number;
}

const clean = (s: string): string => stripPrefix(
  (s || '').replace(/^\s*\d+\s*[.、．)]\s*/, '').replace(/[。．.]+\s*$/, '').trim(),
);

export function equivalent(a: string, b: string): boolean {
  const na = clean(a);
  const nb = clean(b);
  if (!na || !nb) return false;
  if (na === nb) return true;
  const pa = parseLinear(na);
  const pb = parseLinear(nb);
  return !!pa && !!pb && sameLinear(pa, pb);
}

export function splitRefSteps(referenceAnalysis: string | undefined): string[] {
  return (referenceAnalysis || '').split(/[;；]/).map((s) => s.trim()).filter(Boolean);
}

export function buildDiff(refSteps: string[], stuSteps: string[]): StepDiff {
  const rows: DiffRow[] = [];
  let ri = 0;
  let first: number | null = null;
  let firstKind: StepDiff['firstKind'] = null;
  stuSteps.forEach((stu, si) => {
    if (first !== null) {
      rows.push({ key: `s${si}`, stuIdx: si, refIdx: null, stu, ref: null, status: 'after', diffs: [] });
      return;
    }
    let hit = -1;
    for (let j = ri; j < refSteps.length; j++) {
      if (equivalent(refSteps[j], stu)) { hit = j; break; }
    }
    if (hit >= 0) {
      rows.push({ key: `s${si}`, stuIdx: si, refIdx: hit, stu, ref: refSteps[hit], status: 'match', diffs: [] });
      ri = hit + 1;
      return;
    }
    const expected = refSteps[ri] ?? null;
    const pa = parseLinear(clean(stu));
    const pb = expected ? parseLinear(clean(expected)) : null;
    const diffs = pa && pb ? diffTerms(pa, pb) : [];
    const kind = expected === null ? 'extra' : 'diverge';
    rows.push({ key: `s${si}`, stuIdx: si, refIdx: expected === null ? null : ri, stu, ref: expected, status: kind, diffs });
    first = rows.length - 1;
    firstKind = kind;
  });
  if (first === null) {
    for (let j = ri; j < refSteps.length; j++) {
      rows.push({ key: `r${j}`, stuIdx: null, refIdx: j, stu: null, ref: refSteps[j], status: 'missing', diffs: [] });
      if (first === null) { first = rows.length - 1; firstKind = 'missing'; }
    }
  }
  return { rows, first, firstKind, refCount: refSteps.length, stuCount: stuSteps.length };
}

/** 分歧步的两层本地提示（不依赖大模型）：先关注哪一处，再只做哪一步。 */
export function hintForRow(row: DiffRow): { focus: string; next: string } {
  const n = (row.stuIdx ?? row.refIdx ?? 0) + 1;
  if (row.status === 'missing') {
    return {
      focus: `参考第 ${n} 步「${row.ref}」学生没有写：先问这一步打算做什么变形。`,
      next: `只补写第 ${n} 步，写出变形依据，再对照参考检查是否等价。`,
    };
  }
  if (row.status === 'extra') {
    return {
      focus: '参考解法之外多出一步：先问这一步的依据是什么，不直接判错。',
      next: '让学生说明该步与上一步的关系，再决定保留或回到参考路径。',
    };
  }
  const hasBracket = /[()（）]/.test(row.ref || '');
  const stuBracket = /[()（）]/.test(row.stu || '');
  const focus = row.diffs.length
    ? `先看${row.diffs[0].replace(/：.*$/, '')}的符号与系数：负号要变号、系数要乘到每一项。`
    : stuBracket && !hasBracket
      ? '先看列式：题里的数量关系是否译成了正确的式子；括号与系数的含义要和题意一致。'
      : hasBracket
        ? '先看去括号这一步：括号前的符号与系数是否分配到每一项。'
        : '先看这一步的变形依据：等式两边是否同加同减、同乘同除（除数不为 0）。';
  return {
    focus,
    next: `只重做第 ${n} 步并写出依据，对照参考「${row.ref || '该步'}」检查是否等价；通过后再做下一步。`,
  };
}