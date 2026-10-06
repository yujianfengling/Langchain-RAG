import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { listMyCourses } from '../api/studentCourses';
import { listTeacherCourses } from '../api/teacher';
import { getSubjects } from '../api/subjects';
import { useAuth } from './AuthContext';
import { Subject } from '../types';

/**
 * 统一的“当前课程”上下文（2026-10-06 改版）
 *
 * 为什么需要：此前首页用 /student/courses、助手页用 /subjects，两处各存一份选择，
 * 于是出现“首页有课、助手没课 / 助手显示旧课程、范围却是个人资料”的矛盾。
 * 现在只有一处课程来源：
 *   - 学生：/student/courses（只有真正加入的课程，后端权威）
 *   - 教师/管理员：/teacher/courses（自己开设的课程）
 * 本地只保存“课程 ID”，恢复时**必须**仍在真实列表中，否则清除。
 *
 * 明确区分四种状态，绝不把失败吞成空列表：
 *   loading（加载中） / ready（成功，可能为空） / error（请求失败，可重试） / forbidden（无权限）
 */
export interface CourseItem {
  course_id: string;
  title: string;
  subject_id: string;
  class_name?: string | null;
  teacher_name?: string | null;
  description?: string | null;
  material_count?: number | null;
  student_count?: number | null;
  assignment_count?: number | null;
}

export type CourseStatus = 'loading' | 'ready' | 'error';

interface CourseContextValue {
  status: CourseStatus;
  error: string | null;
  forbidden: string | null;
  courses: CourseItem[];
  /** 供助手选择器使用的学科列表：真实课程在前，个人学科在后（不混为一套） */
  subjects: Subject[];
  /** 学生个人资料学科（非教师课程），次级入口用 */
  personalSubjects: Subject[];
  current: CourseItem | null;
  currentSubjectId: string;
  select: (courseId: string) => void;
  selectSubject: (subjectId: string) => void;
  refresh: () => Promise<void>;
  isCourseSubject: (subjectId: string) => boolean;
}

const CourseContext = createContext<CourseContextValue | null>(null);
const LAST_COURSE_KEY = 'edu_current_course';
const LAST_SUBJECT_KEY = 'edu_last_subject';

export const CourseProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { status: authStatus, user } = useAuth();
  const [status, setStatus] = useState<CourseStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState<string | null>(null);
  const [courses, setCourses] = useState<CourseItem[]>([]);
  const [personal, setPersonal] = useState<Subject[]>([]);
  const [currentId, setCurrentId] = useState<string>('');
  const [personalSubjectId, setPersonalSubjectId] = useState<string>('');

  const load = useCallback(async () => {
    // 未登录 / 校验中：**不发起**任何课程请求（2026-10-07 修复未登录刷个人接口）
    if (authStatus !== 'authenticated') {
      setStatus('ready'); setCourses([]); setPersonal([]); setError(null); setForbidden(null);
      setCurrentId(''); setPersonalSubjectId('');
      return;
    }
    setStatus('loading'); setError(null); setForbidden(null);
    const role = user?.role || 'student';
    try {
      let items: CourseItem[] = [];
      if (role === 'teacher' || role === 'admin') {
        const res: any = await listTeacherCourses();
        const raw = (res && res.data && res.data.courses) || [];
        items = raw.map((c: any) => ({
          course_id: c.id, title: c.title, subject_id: c.subject_id,
          description: c.description, material_count: c.material_count,
          student_count: c.student_count,
        }));
      } else {
        const res: any = await listMyCourses();
        const raw = (res && res.data && res.data.courses) || [];
        items = raw.map((c: any) => ({
          course_id: c.course_id, title: c.title, subject_id: c.subject_id,
          class_name: c.class_name, teacher_name: c.teacher_name,
          description: c.description, assignment_count: c.assignment_count,
        }));
      }
      setCourses(items);
      // 个人资料学科（学生自己的知识库）：只作为次级入口，明确与课程分开
      let personalList: Subject[] = [];
      try {
        const reg: any = await getSubjects();
        const all: Subject[] = Array.isArray(reg) ? reg : ((reg && reg.data && (reg.data.subjects || reg.data)) || []);
        const courseSubjectIds = new Set(items.map((c) => c.subject_id));
        personalList = (Array.isArray(all) ? all : []).filter(
          (s) => s && s.subject_id && !courseSubjectIds.has(s.subject_id));
      } catch (e) {
        personalList = [];
      }
      setPersonal(personalList);
      setStatus('ready');

      // 恢复/自动选择：只在真实列表内恢复，失效即清除
      setCurrentId((prev) => {
        let stored = '';
        try { stored = localStorage.getItem(LAST_COURSE_KEY) || ''; } catch (e) { stored = ''; }
        // 旧版本只记了学科：能映射到真实课程就迁移过来（保证刷新/返回后课程一致）
        if (!stored) {
          try {
            const legacy = localStorage.getItem(LAST_SUBJECT_KEY) || '';
            const hit = legacy ? items.find((c) => c.subject_id === legacy) : undefined;
            if (hit) { stored = hit.course_id; localStorage.setItem(LAST_COURSE_KEY, hit.course_id); }
          } catch (e) { /* 忽略 */ }
        }
        const candidate = prev || stored;
        if (candidate && items.some((c) => c.course_id === candidate)) return candidate;
        if (candidate) { try { localStorage.removeItem(LAST_COURSE_KEY); } catch (e) { /* 忽略 */ } }
        if (items.length === 1) {
          try { localStorage.setItem(LAST_COURSE_KEY, items[0].course_id); } catch (e) { /* 忽略 */ }
          return items[0].course_id;
        }
        return '';
      });
      setPersonalSubjectId((prev) => {
        if (prev && personalList.some((s) => s.subject_id === prev)) return prev;
        return '';
      });
    } catch (e: any) {
      const httpStatus = e && e.response ? e.response.status : null;
      const msg = (e && e.response && e.response.data && e.response.data.message)
        || (e && e.message) || '课程列表加载失败';
      setCourses([]);
      if (httpStatus === 403) {
        setForbidden(msg); setStatus('ready');
      } else {
        setError(msg); setStatus('error');
      }
    }
  }, [authStatus, user]);

  useEffect(() => { load(); }, [load]);

  const select = useCallback((courseId: string) => {
    setCurrentId(courseId);
    setPersonalSubjectId('');
    try {
      if (courseId) {
        localStorage.setItem(LAST_COURSE_KEY, courseId);
        const c = courses.find((x) => x.course_id === courseId);
        if (c) localStorage.setItem(LAST_SUBJECT_KEY, c.subject_id);
      } else {
        localStorage.removeItem(LAST_COURSE_KEY);
      }
    } catch (e) { /* 隐私模式忽略 */ }
  }, [courses]);

  const selectSubject = useCallback((subjectId: string) => {
    setPersonalSubjectId(subjectId);
    setCurrentId('');
    try {
      if (subjectId) localStorage.setItem(LAST_SUBJECT_KEY, subjectId);
      localStorage.removeItem(LAST_COURSE_KEY);
    } catch (e) { /* 忽略 */ }
  }, []);

  const current = useMemo(
    () => courses.find((c) => c.course_id === currentId) || null, [courses, currentId]);
  const subjects = useMemo<Subject[]>(() => {
    const list: Subject[] = courses.map((c) => ({ subject_id: c.subject_id, name: c.title }));
    for (const s of personal) if (!list.some((x) => x.subject_id === s.subject_id)) list.push(s);
    return list;
  }, [courses, personal]);
  const isCourseSubject = useCallback(
    (subjectId: string) => courses.some((c) => c.subject_id === subjectId), [courses]);

  const value: CourseContextValue = {
    status, error, forbidden, courses, subjects, personalSubjects: personal,
    current, currentSubjectId: current ? current.subject_id : personalSubjectId,
    select, selectSubject, refresh: load, isCourseSubject,
  };
  return <CourseContext.Provider value={value}>{children}</CourseContext.Provider>;
};

export const useCourse = (): CourseContextValue => {
  const ctx = useContext(CourseContext);
  if (!ctx) throw new Error('useCourse 必须在 CourseProvider 内使用');
  return ctx;
};
