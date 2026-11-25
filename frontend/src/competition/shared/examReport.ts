import type { Bundle, RealAnswer } from '../dataSource';

export function summarize(rows: RealAnswer[]) {
  const score = rows.reduce((sum, a) => sum + a.score, 0);
  const full = rows.reduce((sum, a) => sum + a.fullScore, 0);
  return { score, full, count: rows.length, rate: full > 0 ? score / full * 100 : null };
}
export const percent = (value: number | null) => value == null ? '—' : value.toFixed(1) + '%';
export function examReport(bundle: Bundle, quiz: string) {
  // answers is the pipeline's scored, merged, in-scope answer set.
  const rows = bundle.answers.filter(a => a.quizId === quiz);
  const people = bundle.students.map(student => ({ ...student, ...summarize(rows.filter(a => a.studentId === student.id)) }));
  // 班级平均得分率用合并口径：范围内有效得分和÷满分和，与后端管线/成果包一致，可独立复算。
  const mean = (list: typeof people) => {
    const full = list.reduce((sum, p) => sum + p.full, 0);
    const score = list.reduce((sum, p) => sum + p.score, 0);
    return full > 0 ? score / full * 100 : null;
  };
  const classes = bundle.classes.map(c => {
    const members = people.filter(p => p.classId === c.id);
    const scored = members.filter(p => p.rate != null).sort((a,b) => b.rate! - a.rate!);
    return { ...c, members, scored, mean: mean(members), high: scored.filter(s=>s.rate===scored[0]?.rate),
      low: scored.filter(s=>s.rate===scored[scored.length-1]?.rate) };
  });
  return { rows, people, classes, mean: mean(people) };
}
