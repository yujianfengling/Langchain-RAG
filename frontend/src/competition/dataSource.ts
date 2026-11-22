/**
 * 比赛版 · 运行时数据源开关（集成层）
 * ============================================================
 * 默认仍用演示样例（保证已验收的 22 项交互不变）。
 * ?source=real    ：读 public/assets/competition-data/ 下的自造样例 bundle（演示测试用）。
 * ?source=official：走后端受控接口 /api/competition/official-bundle，
 *                   该接口需教师登录，从本地私有目录读取官方数据，
 *                   官方资料不会被打包进公开静态资源。
 *
 * 真实数据下：学习建议未接正式算法 → 一律显示「暂无学习建议」，
 * 绝不会把演示样例建议挂到真实学生身上。
 */
import type {
  ClassInfo, Topic, Quiz, KnowledgePoint, QuestionBankItem, StudentAnswer,
} from './shared/types';
import { applyRealIds } from './shared/ids';
import { getToken, subscribe as subscribeAuth } from '../api/authState';

export interface BundleManifest {
  mode: 'real';
  source: 'sample' | 'official';
  generated_at: string;
  counts: Record<string, number>;
  note?: string;
}

export type RealAnswer = StudentAnswer & {
  stepsUnavailable?: boolean;
  rubricUnavailable?: boolean;
  sourceRecordId?: string;
  sourceVersion?: string;
  finalAnswer?: string;
  superseded?: { record_id: string; version: string }[];
};

/** 全量事件状态索引（含待评分/未作答/隔离/超范围），用于「指定测验无记录」时如实显示状态 */
export interface AnswerEvent {
  studentId: string; questionId: string; quizId: string;
  status: string; recordId: string; version: string;
  score: number | null; fullScore: number | null; quarantined: boolean;
  outOfScope?: boolean;
}

/** 异常明细：隔离 / 超出测验范围，保留原始记录可查 */
export interface AnomalyRecord {
  record_id: string; event_id: string; student_id: string; class_id: string;
  item_id: string; node_id: string; indicator_id: string;
  kind: 'quarantined' | 'out_of_scope';
  score: number | null; full_score: number | null; status: string; reason: string;
}

/** 班级×考点×题目的有效作答分解（同题对比两种口径共用） */
export interface ItemBreakdown {
  item_id: string; score: number; full_score: number;
  event_count: number; observed_students: number;
}

export interface Reconciliation {
  raw_rows: number; merged_events: number;
  raw_status: Record<string, number>;
  included_scored_events: number; quarantined_events: number; out_of_scope_events: number;
}

export interface Bundle {
  manifest: BundleManifest;
  classes: ClassInfo[];
  topics: Topic[];
  quizzes: (Quiz & { availableItems?: string[] })[];
  students: { id: string; name: string; classId: string }[];
  kps: KnowledgePoint[];
  questions: (QuestionBankItem & { sourceLocation?: string; questionType?: string; materialType?: string; referenceAnalysis?: string; referenceAnswer?: string })[];
  answers: RealAnswer[];
  events?: AnswerEvent[];
  anomalies?: AnomalyRecord[];
  reconciliation?: Reconciliation;
  curriculumMap?: CurriculumMap;
  diagnoses?: DiagnosisBundle;
  caseBook?: CaseBook;
  replay?: ReplayBundle;
  reviewPlans?: ReviewPlans;
}

export interface TextbookPageRef {
  material_id: string; pdf_page: number; printed: string;
  anchor: string; verified: boolean;
}
export interface CurriculumIndicator {
  indicator_id: string; theme_id: string; requirement: string;
  /** 官方《课程要求》表里的主题名与目标；演示产物可能没有 */
  theme_name?: string; theme_goal?: string;
  standard: {
    code: string; file?: string; pdf_page?: number | null; printed?: number | null;
    verified?: boolean; note?: string;
  };
  textbook_range: string; textbook_pages: TextbookPageRef[];
  /** 该指标是否已定位到逐页核对的具体教材页 */
  locating_status?: string;
  materials: { material_id: string; material_type: string; question_type?: string; name: string; full_score: string; source: string }[];
  learning_cards: { material_id: string; name: string; question_type?: string }[];
  questions: { material_id: string; name: string; question_type?: string }[];
}
export interface CurriculumMap {
  generated_at: string; indicators: CurriculumIndicator[];
}
/** 本题观察：具体失分评分点 / 过程与填答不一致（第一层） */
export interface QuestionFinding {
  item_id: string; evidence_id: string; record_id: string;
  kind: '过程与填答不一致' | '部分得分';
  score: number | null; full_score: number | null;
  lost_points: { point_id: string; label: string; earned: number; full: number }[];
  fact: string; inference: string; action: string;
  last_expr?: string; final_answer?: string;
}
export interface TaskStep { order: number; title: string; when_stuck: string }
/** 可执行任务：材料+依据+步骤+提示+复查条件是一整套，教师调整时整套更换 */
export interface ExecutableTask {
  material_id: string; type: string; reason: string;
  steps: TaskStep[]; review: string; kind?: string;
}
export interface SecondaryAction {
  kind: string; material_id: string; question_ids: string[];
  reason: string; review: string;
}
export interface Candidate {
  material_id: string; material_type: string; question_type: string; name: string;
  done: boolean; done_full: boolean; node_available: boolean;
}
export interface Diagnosis {
  student_id: string; node_id: string; indicator_id: string;
  /** 考点覆盖判定（第二层）：记录不足 / 表现较好 / 部分得分 */
  case: string;
  /** 本题判定（第一层）：部分得分 / 满分 / null */
  question_case: string | null;
  question_findings: QuestionFinding[];
  coverage: { event_count: number; min_evidence: number; sufficient: boolean; note: string };
  facts: string[]; inference: string; observe: string; evidence_ids: string[];
  pending_event_count?: number; unanswered_event_count?: number;
  score: number; full_score: number; rate: number | null; event_count: number;
  resource: ExecutableTask;
  secondary_actions: SecondaryAction[];
  pretest_questions: string[];
  candidates: Candidate[];
  textbook: TextbookPageRef & { pdf_page?: number | null };
  special: QuestionFinding[];
  case_reason?: string;
}
export interface DiagnosisBundle {
  generated_at: string;
  threshold: { advice: number; good: number; min_evidence: number };
  diagnoses: Diagnosis[];
}
export interface CaseEntry extends Diagnosis {
  case_name: string; showcase_level: string; focus: string; selection_reason: string;
}
export interface CaseBook {
  generated_at: string; selection_rule: string;
  cases: CaseEntry[];
}
/** 自造证据回放：隔离副本上用正式管线重算的前后对照，绝不混入官方统计 */
export interface ReplayState {
  score: number; full_score: number; rate: number | null; event_count: number;
  case: string; question_case: string | null;
  resource: { material_id: string; type: string; reason: string };
}
export interface ReplayScenario {
  id: string; label: string;
  student_id: string; node_id: string; indicator_id: string;
  mutation: {
    description: string;
    added_records: { record_id: string; event_id: string; version: number; score: number; full_score: number; status: string }[];
  };
  before: ReplayState; after: ReplayState; changes: string[];
}
export interface ReplayBundle {
  generated_at: string; label: string; scenarios: ReplayScenario[];
}

/** 班级讲评方案（首版 N01/DC01/T01；预算 2/3；两策略 + naive 对照） */
export interface PlanLectureItem {
  item_id: string; indicator: string; rank_reason: string;
  lost_students: string[];
  lost_points: { student: string; point_id: string; label: string; record_id: string }[];
}
export interface PlanPretestTask {
  item_id: string; indicator: string; students: string[]; observe: string; rank_reason?: string;
}
export interface CoverageAudit {
  total_points: number; covered_points: number; uncovered_points: number;
  official_points: number; live_points: number; unit: string;
}
export interface PlanWorkload {
  lecture_budget: number; pretest_budget: number; lecture_questions: number; pretest_questions: number;
  total_unique_questions: number; lecture_assignments: number; pretest_assignments: number; total_assignments: number;
  groups: { item_id: string; indicator: string; students: string[]; questions_per_student: number; assignments: number }[];
  student_tasks: { student: string; lecture_items: string[]; pretest_items: string[]; task_count: number }[];
}
export interface PlanComparison {
  before: CoverageAudit; after: CoverageAudit;
  added_points: { student: string; item: string; point_id: string; label: string; record_id: string; live: boolean }[];
  removed_points: { student: string; item: string; point_id: string; label: string; record_id: string; live: boolean }[];
  assignments: { changed: boolean; added: { student: string; item_id: string; type: string }[];
    removed: { student: string; item_id: string; type: string }[]; unchanged_count: number };
  explanation: string;
}
export interface ReviewPlan {
  strategy: string; budget: number;
  coverage_audit: CoverageAudit;
  workload: PlanWorkload;
  lecture_items: PlanLectureItem[]; pretest_tasks: PlanPretestTask[];
  covered_students: string[]; covered_lost_points: number;
  uncovered: {
    lost_points: { student: string; item: string; point_id: string; label: string }[];
    insufficient_students: { student: string; indicator: string; effective: number; min: number }[];
    reasons: string[];
  };
  dropped_if_lower_budget: { item_id: string; lost_students: string[]; reason: string }[];
}
export interface ReviewPlans {
  generated_at: string; node_id: string; class_id: string; theme: string;
  indicators: string[]; budgets: number[];
  strategies: Record<string, { budgets: Record<string, ReviewPlan> }>;
  comparison: Record<string, {
    naive_plan: ReviewPlan;
    naive: { covered_students: string[]; covered_lost_points: number; located_points: number; pretest_tasks: number; lecture_items: string[] };
    plan: { covered_students: string[]; covered_lost_points: number; located_points: number; pretest_tasks: number; lecture_items: string[] };
    tradeoff: string;
  }>;
  budget_diff_3_to_2: Record<string, { removed_items: string[]; affected_students: string[]; kept: { item_id: string; why: string }[] }>;
  session_extra_evidence: unknown[];
}

let _bundle: Bundle | null = null;
let _loaded = false;
let _retrySubscribed = false;
let _notice: string | null = null;

export function isRealData(): boolean {
  return _bundle !== null;
}

/** 数据源降级原因（官方接口不可用等），供外壳显示「当前是演示样例 + 重试」。 */
export function getSourceNotice(): string | null {
  return _notice;
}

export function getBundle(): Bundle | null {
  return _bundle;
}

export function getSourceLabel(): string {
  if (!_bundle) return '演示样例数据';
  return _bundle.manifest.source === 'official' ? '官方赛题数据' : 'CSV样例数据（非官方）';
}

/**
 * 应用启动时调用。
 * 正式提交版默认就是官方赛题数据（老师第一次打开不需要知道任何参数）；
 * ?source=demo 显式回到虚构演示样例（仅回归测试用），?source=real 读自造样例 bundle。
 * 官方接口不可用时降级演示，但必须给出可见原因与重试入口，不静默冒充官方数据。
 */
export async function initDataSource(): Promise<void> {
  if (_loaded) return;
  _loaded = true;
  if (typeof window === 'undefined') return;
  const mode = new URLSearchParams(window.location.search).get('source') || 'official';
  if (mode === 'demo') return;
  if (mode !== 'real' && mode !== 'official') return;

  try {
    let bundle: Bundle;
    if (mode === 'official') {
      // 受控接口：教师登录后可访问；服务端从本地私有目录读取，不进公开静态资源。
      const token = getToken();
      // 未登录时不要发请求（否则控制台会留一条 401）。登录后由 subscribeAuth 整页重载再取。
      if (!token) throw new Error('尚未登录，登录后自动加载官方赛题数据');
      const res = await fetch('/api/competition/official-bundle', {
        cache: 'no-store',
        headers: token ? { 'X-User-Token': token } : undefined,
      });
      if (!res.ok) throw new Error(`受控接口 HTTP ${res.status}`);
      const payload = await res.json();
      bundle = {
        manifest: payload.manifest, classes: payload.classes, topics: payload.topics,
        quizzes: payload.quizzes, students: payload.students, kps: payload.kps,
        questions: payload.questions, answers: payload.answers, events: payload.events,
        anomalies: payload.anomalies,
        reconciliation: payload.reconciliation || payload.manifest?.reconciliation,
        curriculumMap: payload.curriculum_map, diagnoses: payload.diagnoses, caseBook: payload.case_book,
        replay: payload.replay, reviewPlans: payload.review_plans,
      };
    } else {
      // 自造样例静态 bundle（仅演示测试）
      const base = '/assets/competition-data/';
      const fetchJson = async (name: string) => {
        const res = await fetch(base + name, { cache: 'no-store' });
        if (!res.ok) throw new Error(`${name} HTTP ${res.status}`);
        return res.json();
      };
      const [manifest, classes, topics, quizzes, students, kps, questions, answers] = await Promise.all([
        fetchJson('manifest.json'), fetchJson('classes.json'), fetchJson('topics.json'),
        fetchJson('quizzes.json'), fetchJson('students.json'), fetchJson('kps.json'),
        fetchJson('questions.json'), fetchJson('answers.json'),
      ]);
      bundle = { manifest, classes, topics, quizzes, students, kps, questions, answers };
    }
    if (bundle.manifest.mode !== 'real') throw new Error('manifest.mode 不是 real');
    _bundle = bundle;
    _notice = null;
    window.dispatchEvent(new CustomEvent('competition:source-ready'));
    applyRealIds({ topics: bundle.topics, classes: bundle.classes, quizzes: bundle.quizzes, students: bundle.students });
    console.info('[competition] 已切换到数据源', bundle.manifest.source, bundle.manifest.counts);
  } catch (e) {
    _bundle = null;
    _notice = e instanceof Error ? e.message : String(e);
    console.warn('[competition] 数据源加载失败，继续使用演示样例：', e);
    window.dispatchEvent(new CustomEvent('competition:source-failed', { detail: _notice }));
    // 官方模式常见于「先打开页面 → 再登录」：登录拿到 token 后重新加载一次即可读到受控接口。
    if (mode === 'official' && !_retrySubscribed && !getToken()) {
      _retrySubscribed = true;
      subscribeAuth(() => {
        // 登录拿到 token 后整页重载：确保已渲染的组件也拿到官方 bundle。
        if (getToken() && !_bundle) window.location.reload();
      });
    }
  }
}
