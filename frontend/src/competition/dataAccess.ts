/**
 * 智慧学伴比赛版 · 统一接线层（集成负责人维护）
 * ============================================================
 * 本文件只做统一导出与跨模块接线，**不含样例数据实现**。
 * 四块功能的数据实现分别放在自己的模块目录：
 *   - 班级学情：./classAnalytics/data.ts
 *   - 个人答卷：./studentAnswers/data.ts
 *   - 学习建议：./learningAdvice/data.ts
 *   - 课标与教材：./curriculum/data.ts
 *
 * 共享的类型与 ID 名册在 ./shared/。各负责人接真实数据时主要改自己模块的数据文件；
 * 需要调整跨模块跳转或公共导出时才动本文件。
 */

// 共享类型 + ID 名册
export * from './shared/types';
export {
  TOPICS, CLASSES, QUIZZES, STUDENTS, DEMO_TEXTBOOK,
  textbookSource, getStudentName, searchStudents,
} from './shared/ids';

// 各模块数据读取（统一出口，页面仍可直接从模块 data 引入）
export {
  listKnowledgePoints, getKpStat, getKnowledgePoint,
} from './classAnalytics/data';
export {
  getQuestion, listQuestionsByKp, listAllQuestions, getAnswer,
  listAnswersByQuestion, listStudentAnswers, getAnswerEvent, listStudentEvents,
  checkScoreConsistency,
} from './studentAnswers/data';
export {
  getSuggestion, getFeedback,
} from './learningAdvice/data';
export {
  getCurriculum, getSourceByQuestion, getKpBySourcePage,
} from './curriculum/data';
