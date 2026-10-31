import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Card, Checkbox, Collapse, Empty, Form, Input, List, Modal, Popconfirm,
  Progress, Space, Spin, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  BuildOutlined, DatabaseOutlined, DeleteOutlined,
  FileImageOutlined, FileTextOutlined, PlusOutlined, ReadOutlined, ReloadOutlined, SyncOutlined,
  TeamOutlined, UserOutlined,
} from '@ant-design/icons';
import { useCourse } from '../context/CourseContext';
import { createSubject } from '../api/subjects';
import {
  buildPageIndex, deleteDocument, getDocuments, getPageIndexStatus, getKnowledgeStats,
  rebuildKnowledge,
} from '../api/knowledge';
import apiClient from '../api/axios';
import { getTutorSession } from '../api/tutor';
import { getHome } from '../api/student';
import { joinCourseByCode } from '../api/studentCourses';
import UploadDropzone from '../components/UploadDropzone';
import { SkeletonCard } from '../components/AnimateMotion';

// 知识图谱依赖 echarts（较大），只在学生真正展开"概念关系分析"时才加载，
// 避免资料库首屏为了一个次要功能多下载近 1MB（AGENTS 第 7 节：拆包要实测首屏）。
const KnowledgeGraph = React.lazy(() => import('../components/KnowledgeGraph'));

const { Title, Text, Paragraph } = Typography;

/**
 * 资料库（2026-10-07 统一伴学重构）
 *
 * 产品主线：创建学科 → 上传资料 → 查看处理进度 → 知识库就绪 → 开始伴学。
 * 个人学科与教师课程统一成同构卡片，但来源与可操作范围清楚区分：
 * 学生只能读课程资料，不能上传/删除/重建。
 */

interface KnowledgeDoc {
  name: string;
  size: string;
  type: string;
  upload_time: string;
}

interface StatsPayload {
  has_knowledge_base?: boolean;
  document_count?: number;
  chunk_count?: number;
  total_size_mb?: number;
}

interface StatsResponse {
  status?: string;
  stats?: StatsPayload;
}

interface HomePayload {
  last_position?: { knowledge_point?: string | null } | null;
  today_tasks?: unknown[];
  overdue_tasks?: unknown[];
  pending_reviews?: unknown[];
}

interface HomeResponse { data?: HomePayload }

type KbState = 'ready' | 'processing' | 'empty' | 'unknown';

interface SubjectMeta {
  docCount: number | null;
  state: KbState;
  progressText: string;
}

interface SubjectCardData {
  subject_id: string;
  name: string;
  source: 'course' | 'personal';
  className?: string;
  teacherName?: string;
  description?: string;
  docCount: number | null;
  state: KbState;
  progressText: string;
}

/** 单份资料的建页结果（服务端 doc_state，刷新后仍在） */
interface PageDocState {
  doc_id: string;
  file_name: string;
  total_pages: number;
  indexed_pages: number;
  covered_to: number;
  truncated: boolean;
  uncovered_from: number | null;
  uncovered_pages: number;
  updated_at: string;
}

/** 正在跑的建页任务（内存态，进程重启即消失；已完成页永远以数据库为准） */
interface PageActiveTask {
  doc_id: string;
  file_name: string;
  done: number;
  total: number;
  page: number;
}

interface PageStatusPayload {
  pages?: number;
  documents?: number;
  updated_at?: string | null;
  document_details?: PageDocState[];
  active?: PageActiveTask[];
}

/** 只有 PDF/PPT 会有页面索引；其它格式不该显示建页状态 */
const isPageIndexed = (name: string): boolean =>
  /\.(pdf|pptx|ppt)$/i.test(name || '');

const PAGE_POLL_MS = 2000;
const PAGE_QUEUE_GRACE_MS = 8000;

const describeError = (e: unknown, fallback = '操作失败，请重试'): string => {
  const err = e as {
    response?: { data?: { message?: string; detail?: string } };
    message?: string;
  };
  const detail = err?.response?.data?.detail;
  return err?.response?.data?.message
    || (typeof detail === 'string' ? detail : '')
    || err?.message
    || fallback;
};

/** 生成稳定、后端可接受的学科 ID（中文名退化为随机短串，用户无需关心） */
const makeSubjectId = (name: string): string => {
  const ascii = name.trim().toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 20);
  const suffix = Math.random().toString(36).slice(2, 7);
  return ascii ? 'sub-' + ascii + '-' + suffix : 'sub-' + suffix + Date.now().toString(36).slice(-3);
};

const stateFromStats = (res: StatsResponse | null): KbState => {
  const s = res?.stats;
  if (!s) return 'unknown';
  if (s.has_knowledge_base && res?.status === 'ready') return 'ready';
  if (s.has_knowledge_base) return 'processing';
  if ((s.document_count ?? 0) > 0) return 'processing';
  return 'empty';
};

/* 2026-10-31 减法：状态不再用彩色标签（绿/橙逐卡片重复），统一灰字；
   处理中等异常状态仍需要醒目时由详情区的 Alert 承担。 */
const KbStatusTag: React.FC<{ state: KbState }> = ({ state }) => {
  const text = state === 'ready' ? '知识库就绪'
    : state === 'processing' ? '待处理'
    : state === 'empty' ? '暂无资料' : '状态未知';
  return <span style={{ color: 'var(--color-text-secondary)' }}>{text}</span>;
};

const MaterialsPage: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const ctx = useCourse();

  const [meta, setMeta] = useState<Record<string, SubjectMeta>>({});
  const [selectedId, setSelectedId] = useState('');
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [detailStats, setDetailStats] = useState<StatsResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [pdfPreview, setPdfPreview] = useState<{
    name: string; mode: 'pages' | 'blob'; url?: string; docId?: string;
    /** url 为空串表示这一页没有可用页面图（建页失败/被取消），前端如实占位而不是跳过 */
    pages?: Array<{ page: number; url: string }>;
    total?: number; loading: boolean;
  } | null>(null);
  /** 预览代际号：关掉弹窗或换一份资料后，仍在飞的取图批次要作废并释放 blob */
  const previewSeq = useRef(0);

  /**
   * 带登录态查看资料。
   * 之前的 "查看" 是 <a href="/api/...">，浏览器直接跳转不会带 X-User-Token，
   * 所以后端只能返回 {"detail":"请先登录"}。
   * PDF 优先走页面索引（逐页 WebP），在应用内连续滚动，不依赖浏览器 PDF 插件。
   */
  const revokePdfPreview = (preview: typeof pdfPreview) => {
    if (!preview) return;
    if (preview.url) URL.revokeObjectURL(preview.url);
    preview.pages?.forEach((page) => { if (page.url) URL.revokeObjectURL(page.url); });
  };

  const closePdfPreview = () => {
    previewSeq.current += 1;      // 让在飞的批次失效
    revokePdfPreview(pdfPreview);
    setPdfPreview(null);
  };

  const fetchPageUrl = async (docId: string, page: number) => {
    const res = await apiClient.get(
      `/knowledge/${encodeURIComponent(selectedId)}/pages/image`,
      { params: { doc_id: docId, page }, responseType: 'blob' },
    );
    const blob = res instanceof Blob ? res : (res as any)?.data;
    return URL.createObjectURL(new Blob([blob], { type: 'image/webp' }));
  };

  /**
   * 按页预览：先开窗、再逐批取图（一批 4 页）。
   * 一次并发几十上百个 blob 请求会同时压住浏览器和服务端，而且用户要等全部
   * 下载完才看到第一页；教材动辄两三百页，必须先出图再补齐。
   * 资料能不能学，取决于知识库检索，**不取决于这里是否加载完**。
   */
  const streamPagePreview = async (docName: string, docId: string, pageNumbers: number[]) => {
    const seq = ++previewSeq.current;
    revokePdfPreview(pdfPreview);
    setPdfPreview({
      name: docName, mode: 'pages', docId, pages: [],
      total: pageNumbers.length, loading: true,
    });
    const loaded: Array<{ page: number; url: string }> = [];
    const BATCH = 4;
    for (let i = 0; i < pageNumbers.length; i += BATCH) {
      if (previewSeq.current !== seq) return;
      const batch = pageNumbers.slice(i, i + BATCH);
      const urls = await Promise.all(
        batch.map((p) => fetchPageUrl(docId, p).catch(() => '')));
      if (previewSeq.current !== seq) {
        // 弹窗已关或已切到别的资料：这批 blob 没人用了，必须释放
        urls.forEach((u) => { if (u) URL.revokeObjectURL(u); });
        return;
      }
      batch.forEach((p, k) => loaded.push({ page: p, url: urls[k] || '' }));
      const finished = i + BATCH >= pageNumbers.length;
      setPdfPreview((prev) => (prev && previewSeq.current === seq
        ? { ...prev, pages: [...loaded], loading: !finished }
        : prev));
    }
  };

  const viewDocument = async (docName: string) => {
    if (!selectedId) return;
    const isPdf = docName.toLowerCase().endsWith('.pdf');
    if (!isPdf) {
      try {
        const res = await apiClient.get(
          `/knowledge/${encodeURIComponent(selectedId)}/documents/${encodeURIComponent(docName)}/file`,
          { responseType: 'blob' },
        );
        const blob = res instanceof Blob ? res : (res as any)?.data;
        const url = URL.createObjectURL(new Blob([blob], {
          type: (blob as Blob)?.type || 'application/octet-stream',
        }));
        const link = document.createElement('a');
        link.href = url;
        link.download = docName;
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 30000);
      } catch (e: any) {
        message.error(e?.response?.data?.detail || '文件下载失败，请确认登录状态');
      }
      return;
    }

    try {
      const pageRes = await apiClient.get(
        `/knowledge/${encodeURIComponent(selectedId)}/documents/${encodeURIComponent(docName)}/pages`,
      );
      const data = pageRes?.data ?? pageRes;
      const pages = data?.pages || [];
      if (pages.length > 0) {
        void streamPagePreview(docName, data.doc_id || `f:${docName}`,
                               pages.map((p: any) => Number(p.page)));
        return;
      }
    } catch (e: any) {
      if (e?.response?.status !== 404) {
        message.error(e?.response?.data?.detail || 'PDF 页面读取失败');
        return;
      }
    }

    // 没有页面索引时回退到原文件
    try {
      const res = await apiClient.get(
        `/knowledge/${encodeURIComponent(selectedId)}/documents/${encodeURIComponent(docName)}/file`,
        { responseType: 'blob' },
      );
      const blob = res instanceof Blob ? res : (res as any)?.data;
      const url = URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }));
      revokePdfPreview(pdfPreview);
      setPdfPreview({ name: docName, mode: 'blob', url, loading: false });
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '文件打开失败，请确认登录状态');
    }
  };
  const [detailError, setDetailError] = useState<string | null>(null);
  const [selectedDocs, setSelectedDocs] = useState<string[]>([]);
  const [rebuilding, setRebuilding] = useState(false);
  const [pageIndexing, setPageIndexing] = useState(false);
  const [pageStatus, setPageStatus] = useState<PageStatusPayload | null>(null);
  /** 刚提交的建页任务（key = doc_id，`*` 表示整学科）→ 提交时刻，用于跨越服务端登记空窗 */
  const [pagePending, setPagePending] = useState<Record<string, number>>({});
  const [creating, setCreating] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [joinOpen, setJoinOpen] = useState(false);
  const [joinCode, setJoinCode] = useState('');
  const [joining, setJoining] = useState(false);
  const [form] = Form.useForm<{ name: string; description?: string }>();

  // 从"首次使用空状态"或其它页面带参数进入：直接打开对应流程
  useEffect(() => {
    const want = searchParams.get('create') || searchParams.get('join');
    if (!want) return;
    if (want === 'join') setJoinOpen(true); else setModalOpen(true);
    const next = new URLSearchParams(searchParams);
    next.delete('create');
    next.delete('join');
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleJoin = async () => {
    const code = joinCode.trim();
    if (!code) { message.warning('请输入教师提供的班级邀请码'); return; }
    setJoining(true);
    try {
      await joinCourseByCode(code);
      message.success('已加入课程');
      setJoinOpen(false);
      setJoinCode('');
      await ctx.refresh();
      const c = ctx.courses.find((x) => x.title);
      if (c) setSelectedId(c.subject_id);
    } catch (e: any) {
      message.error(e?.response?.data?.message || e?.response?.data?.detail || '加入失败，请核对邀请码');
    } finally {
      setJoining(false);
    }
  };

  const allIds = useMemo(() => {
    const set = new Set<string>();
    ctx.courses.forEach((c) => set.add(c.subject_id));
    ctx.personalSubjects.forEach((s) => set.add(s.subject_id));
    return Array.from(set);
  }, [ctx.courses, ctx.personalSubjects]);

  const loadMeta = useCallback(async (ids: string[]) => {
    if (ids.length === 0) { setMeta({}); return; }
    const entries = await Promise.all(ids.map(async (sid): Promise<[string, SubjectMeta]> => {
      let docCount: number | null = null;
      let state: KbState = 'unknown';
      try {
        const st = (await getKnowledgeStats(sid)) as unknown as StatsResponse;
        const s = st?.stats || {};
        docCount = typeof s.document_count === 'number' ? s.document_count : null;
        state = stateFromStats(st);
      } catch {
        // 尚未建库时 stats 会 404：退化为直接数资料文件，卡片不能一直显示“读取中”
        try {
          const docRes = await getDocuments(sid);
          const n = (docRes?.documents || []).length;
          docCount = n;
          state = n > 0 ? 'processing' : 'empty';
        } catch {
          state = 'unknown';
        }
      }
      let progressText = '尚未开始';
      try {
        const homeRes = (await getHome(sid)) as unknown as HomeResponse;
        const d = homeRes?.data;
        const kp = d?.last_position?.knowledge_point;
        const pending = (d?.today_tasks?.length || 0)
          + (d?.overdue_tasks?.length || 0)
          + (d?.pending_reviews?.length || 0);
        progressText = kp ? '继续：' + kp : pending > 0 ? pending + ' 项待完成' : '尚未开始';
      } catch {
        // 学习进度读取失败不影响资料卡片本身
      }
      return [sid, { docCount, state, progressText }];
    }));
    const next: Record<string, SubjectMeta> = {};
    for (const [sid, m] of entries) next[sid] = m;
    setMeta(next);
  }, []);

  const loadDetail = useCallback(async (sid: string) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      const [docRes, statRes] = await Promise.all([
        getDocuments(sid),
        getKnowledgeStats(sid)
          .then((s) => s as unknown as StatsResponse)
          .catch(() => null),
      ]);
      const list: KnowledgeDoc[] = (docRes?.documents || []) as KnowledgeDoc[];
      setDocs(list);
      setDetailStats(statRes);
      setSelectedDocs((prev) => prev.filter((n) => list.some((d) => d.name === n)));
    } catch (e) {
      setDetailError(describeError(e, '资料加载失败'));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const loadPageStatus = useCallback(async (sid: string) => {
    if (!sid) return;
    try {
      const res = await getPageIndexStatus(sid);
      const data = ((res as any)?.data ?? res) as PageStatusPayload;
      setPageStatus(data || {});
    } catch {
      // 读状态失败不打扰用户：覆盖范围沿用上一次结果，后台建页本身不受影响
    }
  }, []);

  useEffect(() => {
    if (ctx.status === 'ready') { void loadMeta(allIds); }
  }, [ctx.status, allIds, loadMeta]);

  useEffect(() => {
    if (ctx.status === 'ready' && !selectedId && allIds.length === 1) {
      setSelectedId(allIds[0]);
    }
  }, [ctx.status, allIds, selectedId]);

  useEffect(() => {
    if (selectedId) { void loadDetail(selectedId); }
  }, [selectedId, loadDetail]);

  // 页面索引状态：切换学科就读一次（刷新后状态从服务端恢复，不靠前端记忆）
  useEffect(() => {
    if (!selectedId) { setPageStatus(null); setPagePending({}); return; }
    void loadPageStatus(selectedId);
  }, [selectedId, loadPageStatus]);

  /** 正在跑的建页任务（服务端权威） */
  const pageActive = useMemo<PageActiveTask[]>(
    () => pageStatus?.active || [], [pageStatus]);
  const pageDocs = useMemo<PageDocState[]>(
    () => pageStatus?.document_details || [], [pageStatus]);

  /**
   * 提交建页后到服务端登记任务之间有空窗（要开文档、算 SHA），
   * 这段窗口里 active 还是空的；不给宽限就会看到"点了没反应"。
   */
  const isPending = (key: string) => {
    const ts = pagePending[key];
    return !!ts && Date.now() - ts < PAGE_QUEUE_GRACE_MS;
  };
  const docBusy = (docId: string) =>
    pageActive.some((a) => a.doc_id === docId) || isPending(docId);
  const subjectBuilding = pageActive.length > 0 || isPending('*');

  // 有任务在跑（或刚提交）才轮询；跑完自动停，不做无意义的常驻请求
  useEffect(() => {
    if (!selectedId) return;
    if (pageActive.length === 0 && Object.keys(pagePending).length === 0) return;
    const timer = window.setTimeout(() => {
      void loadPageStatus(selectedId);
      setPagePending((prev) => {
        const now = Date.now();
        const next: Record<string, number> = {};
        for (const [key, ts] of Object.entries(prev)) {
          if (now - ts < PAGE_QUEUE_GRACE_MS) next[key] = ts;
        }
        return Object.keys(next).length === Object.keys(prev).length ? prev : next;
      });
    }, PAGE_POLL_MS);
    return () => window.clearTimeout(timer);
  }, [selectedId, pageActive.length, pagePending, loadPageStatus]);

  const cards = useMemo<SubjectCardData[]>(() => {
    const out: SubjectCardData[] = [];
    for (const c of ctx.courses) {
      const m = meta[c.subject_id];
      out.push({
        subject_id: c.subject_id,
        name: c.title,
        source: 'course',
        className: c.class_name || undefined,
        teacherName: c.teacher_name || undefined,
        description: c.description || undefined,
        docCount: m ? m.docCount : (c.material_count ?? null),
        state: m ? m.state : 'unknown',
        progressText: m ? m.progressText : '尚未开始',
      });
    }
    for (const s of ctx.personalSubjects) {
      const m = meta[s.subject_id];
      out.push({
        subject_id: s.subject_id,
        name: s.name,
        source: 'personal',
        description: s.description || undefined,
        docCount: m ? m.docCount : null,
        state: m ? m.state : 'unknown',
        progressText: m ? m.progressText : '尚未开始',
      });
    }
    return out;
  }, [ctx.courses, ctx.personalSubjects, meta]);

  const selectedCard = cards.find((c) => c.subject_id === selectedId) || null;
  const writable = selectedId ? !ctx.isCourseSubject(selectedId) : false;
  const selectedState = detailStats ? stateFromStats(detailStats) : (selectedCard?.state || 'unknown');

  const submitCreate = async () => {
    let values: { name: string; description?: string };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setCreating(true);
    try {
      const sid = makeSubjectId(values.name);
      await createSubject(sid, values.name.trim(), values.description?.trim() || undefined);
      message.success('学科已创建');
      setModalOpen(false);
      form.resetFields();
      await ctx.refresh();
      setSelectedId(sid);
    } catch (e) {
      message.error(describeError(e, '创建失败，请重试'));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (name: string) => {
    if (!selectedId) return;
    try {
      await deleteDocument(selectedId, name);
      message.success('已删除');
      setSelectedDocs((prev) => prev.filter((n) => n !== name));
      // 删除后服务端已同步失效该资料的页面索引与页面图；
      // 若正开着它的按页预览，必须关掉——否则看到的还是已删资料的页面图。
      if (pdfPreview && pdfPreview.name === name) closePdfPreview();
      await Promise.all([loadDetail(selectedId), loadMeta(allIds), loadPageStatus(selectedId)]);
    } catch (e) {
      message.error(describeError(e, '删除失败，请重试'));
    }
  };

  const handleRebuild = async () => {
    if (!selectedId) return;
    setRebuilding(true);
    try {
      await rebuildKnowledge(selectedId);
      message.success('知识库重建完成');
      await loadDetail(selectedId);
      await loadMeta(allIds);
    } catch (e) {
      message.error(describeError(e, '重建失败，请重试'));
    } finally {
      setRebuilding(false);
    }
  };

  const handleBuildPages = async () => {
    if (!selectedId) return;
    setPageIndexing(true);
    try {
      await buildPageIndex(selectedId, {});
      setPagePending((prev) => ({ ...prev, '*': Date.now() }));
      message.info('建页任务已开始，进度按资料显示在下方；资料现在就能用于伴学，不必等建页完成');
      await loadPageStatus(selectedId);
    } catch (e) {
      message.error(describeError(e, '页面索引建立失败，请重试'));
    } finally {
      setPageIndexing(false);
    }
  };

  /**
   * 单份资料的建页：首次建立 / 继续处理超出单文档上限的剩余页 / 失败页重试。
   * pageFrom>1 就是"接着上次没覆盖到的页继续"，已建好的页服务端会跳过。
   */
  const handleBuildDocPages = async (docId: string, pageFrom = 1) => {
    if (!selectedId) return;
    try {
      await buildPageIndex(selectedId, { docId, pageFrom });
      setPagePending((prev) => ({ ...prev, [docId]: Date.now() }));
      message.info(pageFrom > 1 ? `从第 ${pageFrom} 页继续建页…` : '该资料的建页任务已开始');
      await loadPageStatus(selectedId);
    } catch (e) {
      message.error(describeError(e, '建页失败，请重试'));
    }
  };

  /** 用所选资料进入伴学：doc_id 必须来自后端统一资料范围（课程为 m:xx，个人为 f:文件名） */
  const startScoped = async () => {
    if (!selectedId) return;
    const chosen = selectedDocs.slice();
    let ids: string[] = [];
    if (chosen.length > 0) {
      try {
        const session = await getTutorSession(selectedId);
        const byName = new Map<string, string>();
        for (const m of session.materials || []) byName.set(m.file_name, m.doc_id);
        ids = chosen.map((n) => byName.get(n) || 'f:' + n);
      } catch {
        ids = chosen.map((n) => 'f:' + n);
      }
    }
    const params = new URLSearchParams({ subject: selectedId });
    if (ids.length > 0) params.set('docs', ids.join(','));
    navigate('/student?' + params.toString());
  };

  const toggleDoc = (name: string, checked: boolean) => {
    setSelectedDocs((prev) => (checked ? [...prev, name] : prev.filter((n) => n !== name)));
  };

  /**
   * 每份资料的建页状态行：进行中进度 / 覆盖范围 / 失败可重试 / 超上限可继续。
   * 状态全部来自服务端（doc_state + 活跃任务），所以刷新页面后仍然对得上。
   */
  const pageStateRow = (doc: KnowledgeDoc) => {
    if (!isPageIndexed(doc.name)) return null;
    const st = pageDocs.find((d) => d.file_name === doc.name);
    const docId = st?.doc_id || `f:${doc.name}`;
    const act = pageActive.find((a) => a.file_name === doc.name);
    const busy = docBusy(docId);
    // 从没建过页也没有任务在跑：不占版面，工具栏的「建立页面索引」已经能发起
    if (!st && !act && !busy) return null;
    const missing = st ? Math.max(0, st.covered_to - st.indexed_pages) : 0;
    const rowStyle: React.CSSProperties = {
      display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8,
      marginTop: 4, paddingLeft: 26,
    };
    return (
      <div style={rowStyle}>
        {act ? (
          <>
            <Progress
              percent={act.total > 0 ? Math.round((act.done / act.total) * 100) : 0}
              size="small" style={{ width: 110, margin: 0 }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              建页中 {act.done}/{act.total}（第 {act.page} 页）
            </Text>
          </>
        ) : busy ? (
          <Text type="secondary" style={{ fontSize: 12 }}>建页任务已提交，正在启动…</Text>
        ) : st ? (
          <>
            <Tag color={st.truncated || missing > 0 ? 'orange' : 'green'} style={{ marginInlineEnd: 0 }}>
              页面 {st.indexed_pages}/{st.total_pages}
            </Tag>
            {missing > 0 && (
              <>
                <Text type="secondary" style={{ fontSize: 12 }}>{missing} 页未成功</Text>
                {writable && (
                  <Button size="small" type="link" style={{ padding: 0, height: 'auto' }}
                          onClick={() => void handleBuildDocPages(docId, 1)}>
                    重试
                  </Button>
                )}
              </>
            )}
            {st.truncated && (
              <>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  第 {st.uncovered_from} 页起未覆盖（共 {st.uncovered_pages} 页）
                </Text>
                {writable && (
                  <Button size="small" type="link" style={{ padding: 0, height: 'auto' }}
                          onClick={() => void handleBuildDocPages(
                            docId, st.uncovered_from || st.covered_to + 1)}>
                    继续处理
                  </Button>
                )}
              </>
            )}
          </>
        ) : null}
      </div>
    );
  };

  if (ctx.status === 'loading') {
    return (
      <div className="app-page">
        <div className="material-grid">
          <SkeletonCard height={220} />
          <SkeletonCard height={220} />
          <SkeletonCard height={220} />
        </div>
      </div>
    );
  }

  return (
    <div className="app-page">
      <header className="page-header">
        <div className="page-heading">
          <Title level={1} className="page-title">资料库</Title>
        </div>
        <Space className="page-actions" wrap>
          <Button icon={<TeamOutlined />} onClick={() => setJoinOpen(true)}>加入课程</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
            创建学科
          </Button>
        </Space>
      </header>

      {ctx.status === 'error' && (
        <Alert
          type="error" showIcon style={{ marginBottom: 12 }}
          message="资料列表加载失败"
          description={ctx.error || undefined}
          action={<Button icon={<SyncOutlined />} onClick={() => ctx.refresh()}>重试</Button>}
        />
      )}
      {ctx.forbidden && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={ctx.forbidden} />}

      {cards.length === 0 ? (
        <Card>
          <Empty description="还没有学科。创建一个学科并上传资料，就能开始伴学。">
            <Space>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>创建学科</Button>
              {ctx.courses.length > 0 && (
                <Button onClick={() => navigate('/student/courses')}>查看我的课程</Button>
              )}
            </Space>
          </Empty>
        </Card>
      ) : (
        <List
          className="material-grid"
          grid={{ gutter: 16, xs: 1, sm: 2, lg: 3 }}
          dataSource={cards}
          renderItem={(item) => {
            const active = item.subject_id === selectedId;
            return (
              <List.Item>
                <Card
                  className={'material-subject-card' + (active ? ' is-active' : '')}
                  onClick={() => setSelectedId(item.subject_id)}
                >
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                    <div className="material-card-icon">
                      {item.source === 'course' ? <TeamOutlined /> : <UserOutlined />}
                    </div>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div className="material-card-title" title={item.name}>{item.name}</div>
                      {/* 2026-10-31 减法：类型从彩色 Tag 降为灰字，与班级/教师同行 */}
                      <Space size={4} wrap style={{ marginTop: 5 }}>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {item.source === 'course' ? '课程' : '个人'}
                          {item.className ? ' · ' + item.className : ''}
                          {item.teacherName ? ' · ' + item.teacherName : ''}
                        </Text>
                      </Space>
                    </div>
                  </div>

                  {/* 只展示用户自己写的描述。原先没有描述时会塞一句通用口号
                      （"上传自己的资料，建立专属知识库"），每张卡片都一样，
                      纯占高度不传递信息（2026-10-30 精简）。 */}
                  {item.description && (
                    <Paragraph type="secondary" ellipsis={{ rows: 2 }} className="material-card-description">
                      {item.description}
                    </Paragraph>
                  )}

                  <div className="material-card-meta">
                    <span>{item.docCount === null ? '资料数量读取中' : '资料 ' + item.docCount + ' 份'}</span>
                    <KbStatusTag state={item.state} />
                  </div>

                  <div className="material-card-progress" title={item.progressText}>{item.progressText}</div>

                  <div className="material-card-actions">
                    <Button
                      type="primary"
                      icon={<ReadOutlined />}
                      onClick={(e) => {
                        e.stopPropagation();
                        navigate('/student?subject=' + encodeURIComponent(item.subject_id));
                      }}
                    >
                      {item.progressText === '尚未开始' ? '开始学习' : '继续学习'}
                    </Button>
                    <Button
                      type="text"
                      onClick={(e) => { e.stopPropagation(); setSelectedId(item.subject_id); }}
                    >
                      管理资料
                    </Button>
                  </div>
                </Card>
              </List.Item>
            );
          }}
        />
      )}

      {selectedId && selectedCard && (
        <Card
          className="material-detail"
          title={(
            <Space wrap>
              <DatabaseOutlined style={{ color: 'var(--color-primary)' }} />
              <Text strong style={{ fontSize: 15 }}>{selectedCard.name}</Text>
            </Space>
          )}
          extra={<Button type="text" icon={<ReloadOutlined />} onClick={() => loadDetail(selectedId)}>刷新</Button>}
        >
          {detailError && (
            <Alert
              type="error" showIcon style={{ marginBottom: 10 }}
              message={detailError}
              action={<Button icon={<SyncOutlined />} onClick={() => loadDetail(selectedId)}>重试</Button>}
            />
          )}

          <Space size={16} wrap style={{ marginBottom: 10 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>资料 {docs.length} 份</Text>
            {typeof detailStats?.stats?.chunk_count === 'number' && (
              <Text type="secondary" style={{ fontSize: 12 }}>索引 {detailStats.stats.chunk_count} 段</Text>
            )}
            {typeof detailStats?.stats?.total_size_mb === 'number' && (
              <Text type="secondary" style={{ fontSize: 12 }}>{detailStats.stats.total_size_mb} MB</Text>
            )}
            <KbStatusTag state={selectedState} />
          </Space>

          {!writable && (
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
              课程资料由授课教师维护，你可以阅读并用于伴学，但不能修改。
            </Text>
          )}

          {/* 拖拽导入区：始终可见（已有资料时也能继续拖入新增文件） */}
          <UploadDropzone
            subjectId={selectedId}
            writable={writable}
            onFinished={() => {
              // 导入结束后：资料明细、学科卡片数量与建库状态都要立刻更新
              void loadDetail(selectedId);
              void loadMeta(allIds);
            }}
          />

          {writable && (
            <Space style={{ marginBottom: 8 }} wrap>
              <Button icon={<BuildOutlined />} loading={rebuilding} disabled={docs.length === 0} onClick={handleRebuild}>
                重建知识库
              </Button>
              <Tooltip title="为 PDF/PPT 逐页生成页面图与文字，图片型课件也能按页检索、按页看原文。资料入库后即可开始伴学，建页只影响「看原页」。">
                <Button icon={<FileImageOutlined />}
                        loading={pageIndexing || subjectBuilding}
                        disabled={docs.length === 0} onClick={handleBuildPages}>
                  {subjectBuilding ? '正在建页…' : '建立页面索引'}
                </Button>
              </Tooltip>
            </Space>
          )}

          {docs.length > 0 && selectedState !== 'ready' && (
            <Alert
              type="warning" showIcon style={{ marginBottom: 10 }}
              message="资料已上传，但知识库尚未就绪"
              description={writable ? '重建知识库后，这些资料才能用于伴学检索。' : '知识库由授课教师维护，可先阅读资料。'}
              action={writable ? (
                <Button size="small" icon={<BuildOutlined />} loading={rebuilding} onClick={handleRebuild}>重建知识库</Button>
              ) : undefined}
            />
          )}

          {detailLoading ? (
            <Spin />
          ) : docs.length === 0 ? (
            <Empty
              description={writable
                ? '还没有资料，把课件或资料包拖到上面的区域，系统会自动解析并建库'
                : '该课程暂无资料'}
            />
          ) : (
            <>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, margin: '6px 0' }}>
                <Checkbox
                  indeterminate={selectedDocs.length > 0 && selectedDocs.length < docs.length}
                  checked={docs.length > 0 && selectedDocs.length === docs.length}
                  onChange={(e) => setSelectedDocs(e.target.checked ? docs.map((d) => d.name) : [])}
                >
                  全选
                </Checkbox>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {selectedDocs.length > 0 ? '已选 ' + selectedDocs.length + ' 份' : '不勾选则使用全部资料'}
                </Text>
                <Button
                  type="primary"
                  icon={<ReadOutlined />}
                  style={{ marginLeft: 'auto' }}
                  onClick={startScoped}
                >
                  {selectedDocs.length > 0 ? '用所选 ' + selectedDocs.length + ' 份资料开始伴学' : '用全部资料开始学习'}
                </Button>
              </div>

              <List
                size="small"
                dataSource={docs}
                renderItem={(doc) => {
                  return (
                    <List.Item
                      actions={[
                        <Button key="view" type="link" size="small"
                                onClick={() => void viewDocument(doc.name)}>查看</Button>,
                        writable ? (
                          <Popconfirm
                            key="del"
                            title="删除这份资料？"
                            description="删除后需重建知识库才会从检索中移除。"
                            okText="删除" cancelText="取消"
                            onConfirm={() => handleDelete(doc.name)}
                          >
                            <Button type="text" danger size="small" icon={<DeleteOutlined />}>删除</Button>
                          </Popconfirm>
                        ) : null,
                      ].filter(Boolean) as React.ReactNode[]}
                    >
                      <div style={{ minWidth: 0, width: '100%' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                          <Checkbox
                            checked={selectedDocs.includes(doc.name)}
                            onChange={(e) => toggleDoc(doc.name, e.target.checked)}
                          />
                          <FileTextOutlined style={{ color: 'var(--color-primary)', flexShrink: 0 }} />
                          <Text
                            ellipsis
                            title={doc.name}
                            className="material-doc-name"
                          >
                            {doc.name}
                          </Text>
                          <Tag style={{ flexShrink: 0 }}>{(doc.type || '').replace('.', '').toUpperCase()}</Tag>
                          <Text type="secondary" style={{ fontSize: 12, marginLeft: 'auto', flexShrink: 0 }}>
                            {doc.size} · {doc.upload_time}
                          </Text>
                        </div>
                        {pageStateRow(doc)}
                      </div>
                    </List.Item>
                  );
                }}
              />

              {/* 检索用知识图谱（概念关系分析）：与"学习路线"是两个东西，这里明确标注，
                  旧的知识库页面删除后由此入口承接，功能不会变成"找不到"。 */}
              <Collapse
                ghost
                style={{ marginTop: 12, borderTop: '1px solid var(--color-border-light)' }}
                items={[{
                  key: 'graph',
                  label: (
                    <span>
                      知识图谱（概念关系分析）
                      <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                        用于查看资料里的概念关系，不是学习路线
                      </Text>
                    </span>
                  ),
                  children: (
                    <React.Suspense fallback={<SkeletonCard height={280} />}>
                      <KnowledgeGraph subjectId={selectedId} />
                    </React.Suspense>
                  ),
                }]}
              />
            </>
          )}
        </Card>
      )}

      <Modal
        title="创建学科"
        open={modalOpen}
        onCancel={() => { setModalOpen(false); form.resetFields(); }}
        onOk={submitCreate}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
        destroyOnHidden
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item
            name="name"
            label="学科名称"
            rules={[
              { required: true, message: '请输入学科名称' },
              { max: 40, message: '名称不超过 40 个字' },
            ]}
          >
            <Input placeholder="例如：数据结构、大学物理" maxLength={40} autoFocus />
          </Form.Item>
          <Form.Item
            name="description"
            label="描述（可选）"
            rules={[{ max: 120, message: '描述不超过 120 个字' }]}
          >
            <Input.TextArea placeholder="一句话说明这个学科要学什么" rows={2} maxLength={120} showCount />
          </Form.Item>
        </Form>
      </Modal>

      {/* 加入教师课程：次级入口（学生凭教师给的邀请码加入） */}
      <Modal
        title="加入课程"
        open={joinOpen}
        onCancel={() => setJoinOpen(false)}
        onOk={handleJoin}
        confirmLoading={joining}
        okText="加入课程"
        cancelText="取消"
        destroyOnHidden
      >
        <Alert
          type="info" showIcon style={{ marginBottom: 12 }}
          message="向老师要一个班级邀请码，加入后就能用课程资料提问、做作业与复习。"
        />
        <Input
          placeholder="输入班级邀请码，例如 PL7F93"
          value={joinCode}
          onChange={(e) => setJoinCode(e.target.value)}
          onPressEnter={handleJoin}
          autoFocus
        />
      </Modal>

      <Modal
        title={pdfPreview?.name || 'PDF 预览'}
        open={!!pdfPreview}
        onCancel={closePdfPreview}
        footer={null}
        width={1000}
        destroyOnHidden
        styles={{ body: { padding: 0 } }}
      >
        {pdfPreview && (
          pdfPreview.mode === 'pages' ? (
            <div style={{
              maxHeight: '78vh', overflowY: 'auto', padding: 10,
              borderRadius: 8, background: '#e9eef2',
            }}>
              <Text type="secondary" style={{ display: 'block', fontSize: 11, marginBottom: 8 }}>
                已载入 {(pdfPreview.pages || []).length} / 共 {pdfPreview.total ?? (pdfPreview.pages || []).length} 页
                {pdfPreview.loading ? '（边看边载入）' : ''}
              </Text>
              {(pdfPreview.pages || []).map((page, index, arr) => (
                <div key={page.page} style={{ marginBottom: index === arr.length - 1 ? 0 : 12 }}>
                  <Text type="secondary" style={{ display: 'block', fontSize: 11, marginBottom: 4 }}>
                    第 {page.page} 页
                  </Text>
                  {page.url ? (
                    <img
                      src={page.url}
                      alt={`${pdfPreview.name} 第 ${page.page} 页`}
                      loading="lazy"
                      style={{
                        width: '100%', display: 'block', background: '#fff',
                        borderRadius: 6, boxShadow: '0 2px 10px rgba(15,23,42,.08)',
                      }}
                    />
                  ) : (
                    <div style={{
                      padding: '26px 12px', textAlign: 'center', background: '#fff',
                      borderRadius: 6, border: '1px dashed var(--color-border, #d9d9d9)',
                    }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        这一页没有可用的页面图（建页失败或被取消），可在资料列表点「重试」
                      </Text>
                    </div>
                  )}
                </div>
              ))}
              {pdfPreview.loading && (
                <div style={{ padding: 18, textAlign: 'center' }}><Spin size="small" /></div>
              )}
            </div>
          ) : (
            <iframe
              title={pdfPreview.name}
              src={pdfPreview.url}
              style={{ width: '100%', height: '75vh', border: 0, borderRadius: 8 }}
            />
          )
        )}
      </Modal>
    </div>
  );
};

export default MaterialsPage;