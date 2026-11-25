/**
 * 共享名册与稳定编号（集成负责人维护）
 * 四块功能的样例都引用这里的 ID，换真实数据时保持编号对应即可。
 */
import type { Topic, ClassInfo, Quiz, TextbookSource } from './types';

/** 三个正式赛题主题 */
export const TOPICS: Topic[] = [
  { id: 't-algebra', name: '代数式与整式运算', description: '整式的加减、合并同类项' },
  { id: 't-equation', name: '方程与方程组', description: '一元一次方程、二元一次方程组' },
  { id: 't-inequality', name: '不等式与不等式组', description: '一元一次不等式、不等式组应用' },
];

/** 两个班 */
export const CLASSES: ClassInfo[] = [
  { id: 'class-a', name: '七年级（1）班', color: 'blue' },
  { id: 'class-b', name: '七年级（2）班', color: 'teal' },
];

/** 测验 */
export const QUIZZES: Quiz[] = [
  {
    id: 'quiz-mid',
    name: '七年级数学 · 第一次阶段测验',
    date: '2026-11-10',
    topicIds: ['t-algebra', 't-equation', 't-inequality'],
  },
  {
    id: 'quiz-algebra',
    name: '代数式与整式运算单元小测',
    date: '2026-10-28',
    topicIds: ['t-algebra'],
  },
];

/** 学生名册 */
export const STUDENTS: { id: string; name: string; classId: string }[] = [
  { id: 'stu-001', name: '李明', classId: 'class-a' },
  { id: 'stu-002', name: '王雨', classId: 'class-a' },
  { id: 'stu-003', name: '张辰', classId: 'class-b' },
  { id: 'stu-004', name: '陈曦', classId: 'class-b' },
  { id: 'stu-005', name: '赵敏', classId: 'class-a' },
];

/** 自制公开样例教材（public/assets/，后端 /assets 静态挂载） */
export const DEMO_TEXTBOOK = {
  docId: 'demo-textbook',
  docName: '七年级数学 · 考后分析样例教材',
  url: '/assets/demo-textbook.pdf',
};

/** 教材出处快捷构造（页内容见 PDF：2整式 / 3方程移项 / 4方程组 / 5不等式 / 6应用） */
export function textbookSource(page: number, exampleTitle: string): TextbookSource {
  return { docId: DEMO_TEXTBOOK.docId, docName: DEMO_TEXTBOOK.docName, page, exampleTitle };
}

/** 学生姓名 / 搜索（共享） */
export function getStudentName(studentId: string): string {
  return STUDENTS.find((s) => s.id === studentId)?.name || studentId;
}

export function searchStudents(keyword: string): typeof STUDENTS {
  const kw = keyword.trim().toLowerCase();
  if (!kw) return STUDENTS;
  return STUDENTS.filter((s) => s.name.toLowerCase().includes(kw) || s.id.toLowerCase().includes(kw));
}

/**
 * 真实数据接入点：由 dataSource 在 ?source=real 时调用，就地替换共享名册。
 * 这样页面与 getStudentName / searchStudents 无需改动即可使用真实学生与班级。
 * 演示模式不调用本函数，原有 22 项交互保持不变。
 */
export function applyRealIds(payload: {
  topics: Topic[];
  classes: ClassInfo[];
  quizzes: Quiz[];
  students: { id: string; name: string; classId: string }[];
}): void {
  TOPICS.splice(0, TOPICS.length, ...payload.topics);
  CLASSES.splice(0, CLASSES.length, ...payload.classes);
  QUIZZES.splice(0, QUIZZES.length, ...payload.quizzes);
  STUDENTS.splice(0, STUDENTS.length, ...payload.students);
}

export function quizLabel(quiz: Quiz): string {
  const title = quiz.name === quiz.id && quiz.description?.split('：')[0];
  return title ? title + '（' + quiz.id + '）' : quiz.name;
}
