/**
 * 智慧学伴比赛版 · 共享类型与 ID 关联约定
 * ============================================================
 * 集成负责人维护。四块功能都依赖这里的类型与稳定编号约定，
 * 但各自的样例数据放在自己的模块目录里，互不干扰。
 *
 * 关联主键（跨模块稳定）：
 *   quizId → kpId（考点）→ questionId（题目）→ studentId（学生）
 *   kpId → 课标要求 / 教材出处（页码）
 */

/** 赛题主题（正式三个） */
export interface Topic {
  id: string;
  name: string;
  description: string;
}

/** 班级（两班对比，颜色仅前端视觉区分） */
export interface ClassInfo {
  id: string;
  name: string;
  color: 'blue' | 'teal';
}

/** 测验 */
export interface Quiz {
  description?: string;
  id: string;
  name: string;
  date: string;
  topicIds: string[];
}

/** 考点的单班统计状态（三种要能区分 + 正常） */
export type MasteryStatus = '正常' | '本次未测' | '记录不足' | '真实零分';

export interface KpClassStat {
  classId: string;
  /** 得分率 0~1；本次未测/记录不足时为 null */
  scoreRate: number | null;
  /** 有效作答数 */
  validAttempts: number;
  status: MasteryStatus;
  /** 主要失分题目 ID */
  topWrongQuestionIds: string[];
  /** 官方证据明细 */
  score?: number; maxScore?: number; itemCount?: number;
  observedStudents?: number; classSize?: number;
  pendingEvents?: number; unansweredEvents?: number; quarantinedEvents?: number;
  outOfScopeEvents?: number;
  /** 班级×题目有效作答分解（同题对比两种口径共用） */
  itemBreakdown?: { item_id: string; score: number; full_score: number; event_count: number; observed_students: number }[];
}

/** 考点（对比表的一行） */
export interface KnowledgePoint {
  id: string;
  topicId: string;
  name: string;
  stats: KpClassStat[];
}

/** 教材出处（指向自制样例 PDF 的页码） */
export interface TextbookSource {
  docId: string;
  docName: string;
  page: number;
  exampleTitle: string;
  /** 官方教材：受控接口 URL；演示样例走 public/assets 的 PDF */
  pdfUrl?: string;
  /** 教材正文印刷页（与 PDF 文件页分开） */
  printedPage?: string;
  /** 课标文件页 / 正文页 */
  standard?: { code: string; pdfPage: number | null; printed: number | null };
  verified?: boolean;
}

/** 评分点（定位"哪一步丢分"） */
export interface ScoringPoint {
  id: string;
  label: string;
  fullScore: number;
  earnedScore: number;
  ok: boolean;
  evidence: string;
}

/** 原始解题步骤 */
export interface AnswerStep {
  step: number;
  text: string;
}

/** 学生答卷（题干/步骤/评分点，个人答卷模块维护） */
export interface StudentAnswer {
  id: string;
  studentId: string;
  questionId: string;
  quizId: string;
  steps: AnswerStep[];
  score: number;
  fullScore: number;
  scoringPoints: ScoringPoint[];
  diagnosis: string;
  nextStep: string;
}

/** 练习建议（学习建议模块维护） */
export interface PracticeSuggestion {
  id: string;
  kpId: string;
  textbookExample: string;
  textbookSource: TextbookSource;
  exercises: { order: number; title: string; hint: string; expandableHint: string }[];
}

/** 学生反馈（预览用） */
export interface StudentFeedback {
  studentId: string;
  summary: string;
  todo: string[];
  hints: { title: string; detail: string }[];
}

/** 课标要求（课标模块维护） */
export interface CurriculumStandard {
  kpId: string;
  requirement: string;
  level: '了解' | '理解' | '掌握' | '运用';
}

/** 题目台账（题干 + 分值 + 考点归属；出处由课标模块按 questionId 映射） */
export interface QuestionBankItem {
  id: string;
  kpId: string;
  stem: string;
  fullScore: number;
  questionType?: string;
  materialType?: string;
  referenceAnalysis?: string;
  referenceAnswer?: string;
  sourceLocation?: string;
}
