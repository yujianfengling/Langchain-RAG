import { authHeaders } from './axios';
import apiClient from './axios';

/**
 * 统一伴学 API 客户端（2026-10-07）
 *
 * 与后端 /api/tutor/* 对齐。流式事件是**真实阶段**（stage）、**真实工具调用**
 * （tool_call/tool_result）、真实引用（sources）与最终回答（done）分开的，
 * 前端不要把 tool_call 渲染成回答，也不要假装调用了没发生的工具。
 */

export interface TutorScopeItem {
  doc_id: string;
  file_name: string;
  status?: string;
  source?: string;
}

export type GuidanceMode = 'direct' | 'socratic';

export interface TutorScope {
  mode: 'all' | 'selected' | 'empty';
  label: string;
  signature?: string;
  selected: TutorScopeItem[];
  invalid?: { doc_id?: string; reason?: string }[];
  all_count?: number;
}

export interface TutorMaterial {
  doc_id: string;
  file_name: string;
  status?: string;
  source?: string;
  material_id?: string | null;
}

export interface TutorSessionData {
  context: Record<string, any>;
  scope: TutorScope;
  materials: TutorMaterial[];
  abilities: { key: string; label: string }[];
  guidance_modes?: { key: GuidanceMode; label: string }[];
}

export interface TutorSource {
  id: number;
  file_name: string;
  page?: number | null;
  type?: string;
  snippet?: string;
  section?: string | null;
  score?: number | null;
  retrieval_type?: string | null;
  page_image_url?: string | null;
  bbox?: number[] | null;
  image_width?: number | null;
  image_height?: number | null;
}

export interface ToolTraceItem {
  tool: string;
  label: string;
  summary: string;
  ok: boolean;
}

export interface TutorDoneEvent {
  type: 'done';
  seq?: number;
  turn_id: string;
  answer: string;
  sources: TutorSource[];
  cited_source_ids: number[];
  artifacts: any[];
  tool_trace: ToolTraceItem[];
  message_type?: string;
  payload?: any;
  guidance_mode?: GuidanceMode;
  ask_user?: { question: string; options: string[] } | null;
  scope?: TutorScope;
  elapsed?: number;
  llm_calls?: number;
  tool_calls?: number;
}

export interface TutorStreamHandlers {
  onEvent: (ev: any) => void;
  onDone?: (ev: TutorDoneEvent) => void;
  onError?: (message: string) => void;
}

export interface WhiteboardElement {
  type: 'text' | 'line' | 'arrow' | 'rect' | 'circle' | 'curve' | string;
  x?: number; y?: number; x1?: number; y1?: number; x2?: number; y2?: number;
  w?: number; h?: number; r?: number; text?: string; size?: number;
  color?: string; points?: number[][];
}

export interface WhiteboardScene {
  title: string;
  width: number;
  height: number;
  topic?: string;
  elements: WhiteboardElement[];
}

export const generateWhiteboard = async (payload: {
  subject_id: string; topic: string; template?: string | null; model?: string | null; node_id?: string | null; doc_ids?: string[];
}): Promise<WhiteboardScene> => {
  const res: any = await apiClient.post('/tutor/whiteboard', payload);
  return (res?.data ?? res) as WhiteboardScene;
};

export const getTutorSession = async (subjectId: string): Promise<TutorSessionData> => {
  const res: any = await apiClient.get('/tutor/session', { params: { subject_id: subjectId } });
  return res?.data ?? res;
};

export const updateTutorSession = async (payload: {
  subject_id: string;
  ability?: string;
  guidance_mode?: GuidanceMode;
  doc_ids?: string[];
  node_id?: string | null;
  conversation_id?: string | null;
  goal?: string;
  deadline?: string;
  daily_minutes?: number;
}): Promise<{ context: Record<string, any>; scope: TutorScope }> => {
  const res: any = await apiClient.put('/tutor/session', payload);
  return res?.data ?? res;
};

export const getKnowledgeMap = async (subjectId: string): Promise<any> => {
  const res: any = await apiClient.get('/tutor/map', { params: { subject_id: subjectId } });
  return res?.data ?? res;
};

export const generateKnowledgeMap = async (payload: {
  subject_id: string; doc_ids?: string[]; force?: boolean;
}): Promise<any> => {
  const res: any = await apiClient.post('/tutor/map/generate', payload);
  return res?.data ?? res;
};

export const getMapNode = async (subjectId: string, nodeId: string): Promise<any> => {
  const res: any = await apiClient.get('/tutor/map/node/' + nodeId, {
    params: { subject_id: subjectId },
  });
  return res?.data ?? res;
};

export const setNodeStatus = async (subjectId: string, nodeId: string, status: string) => {
  const res: any = await apiClient.post('/tutor/map/node/status', {
    subject_id: subjectId, node_id: nodeId, status,
  });
  return res?.data ?? res;
};

export const answerPractice = async (payload: {
  subject_id: string; question_id: string; answer?: string;
  node_id?: string | null; client_token?: string;
}): Promise<any> => {
  const res: any = await apiClient.post('/tutor/practice/answer', payload);
  return res?.data ?? res;
};

export const listArtifacts = async (subjectId?: string, kind?: string): Promise<any[]> => {
  const res: any = await apiClient.get('/tutor/artifacts', {
    params: { subject_id: subjectId, kind },
  });
  const data = res?.data ?? res;
  return data?.artifacts ?? [];
};

export const createArtifact = async (payload: {
  subject_id: string; kind: string; title: string;
  content: Record<string, any>; node_id?: string | null; doc_ids?: string[];
}): Promise<any> => {
  const res: any = await apiClient.post('/tutor/artifacts', payload);
  return (res?.data ?? res)?.artifact;
};

export const updateArtifact = async (artifactId: string, payload: {
  subject_id: string; title?: string; content?: Record<string, any>;
}): Promise<any> => {
  const res: any = await apiClient.put('/tutor/artifacts/' + artifactId, payload);
  return (res?.data ?? res)?.artifact;
};

export const deleteArtifact = async (artifactId: string): Promise<boolean> => {
  const res: any = await apiClient.delete('/tutor/artifacts/' + artifactId);
  return Boolean((res?.data ?? res)?.deleted);
};

export const getTurn = async (turnId: string, afterSeq = 0): Promise<any> => {
  const res: any = await apiClient.get('/tutor/turns/' + turnId, { params: { after_seq: afterSeq } });
  return res?.data ?? res;
};

export const listTurns = async (subjectId?: string, limit = 30, nodeId?: string,
                                conversationId?: string): Promise<any[]> => {
  const res: any = await apiClient.get('/tutor/turns', {
    params: {
      subject_id: subjectId, limit, node_id: nodeId,
      conversation_id: conversationId || undefined,
    },
  });
  return (res?.data ?? res)?.turns ?? [];
};

/** 历史抽屉的会话列表（2026-10-31）：按 conversation_id 聚合 + 旧散轮标只读 */
export const listTutorConversations = async (
  subjectId: string, limit = 50,
): Promise<{ conversations: any[]; loose_turns: any[] }> => {
  const res: any = await apiClient.get('/tutor/conversations', {
    params: { subject_id: subjectId, limit },
  });
  const data = (res?.data ?? res) || {};
  return { conversations: data.conversations || [], loose_turns: data.loose_turns || [] };
};

export const listQuestionsForPoint = async (
  subjectId: string,
  knowledgePoint: string,
  limit = 20,
): Promise<any[]> => {
  const res: any = await apiClient.get('/quiz/questions', {
    params: { subject_id: subjectId, knowledge_point: knowledgePoint, limit },
  });
  return (res?.data ?? res)?.questions ?? [];
};

export const deleteTurn = async (turnId: string): Promise<boolean> => {
  const res: any = await apiClient.delete('/tutor/turns/' + turnId);
  return Boolean((res?.data ?? res)?.deleted);
};

export const cancelTurn = async (turnId: string): Promise<boolean> => {
  const res: any = await apiClient.post('/tutor/turns/' + turnId + '/cancel');
  return Boolean((res?.data ?? res)?.cancelled);
};

/**
 * 发起一轮统一伴学（SSE）。
 * 返回 AbortController：组件卸载或用户点“停止”时中止请求。
 */
export const streamTutorTurn = (
  payload: {
    message: string; subject_id: string; ability?: string;
    guidance_mode?: GuidanceMode;
    doc_ids?: string[]; node_id?: string | null;
    conversation_id?: string | null; model?: string | null;
  },
  handlers: TutorStreamHandlers,
): AbortController => {
  const controller = new AbortController();

  (async () => {
    let response: Response;
    try {
      response = await fetch('/api/tutor/stream', {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
    } catch (error: any) {
      if (error?.name === 'AbortError') return;
      handlers.onError?.('网络请求失败，请检查后端是否启动');
      return;
    }
    if (!response.ok) {
      let detail = '请求失败 (HTTP ' + response.status + ')';
      try {
        const body = await response.json();
        detail = body?.detail || body?.message || detail;
      } catch { /* ignore */ }
      handlers.onError?.(detail);
      return;
    }
    const reader = response.body?.getReader();
    if (!reader) {
      handlers.onError?.('无法获取响应流');
      return;
    }
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const dataStr = trimmed.slice(5).trim();
          if (!dataStr) continue;
          let ev: any;
          try { ev = JSON.parse(dataStr); } catch { continue; }
          if (controller.signal.aborted) return;
          handlers.onEvent(ev);
          if (ev.type === 'done') { handlers.onDone?.(ev); return; }
          if (ev.type === 'error') {
            handlers.onError?.(ev.message || '运行失败');
            return;
          }
        }
      }
      // 响应正常结束但没有 done/error 也属于中断，不能让发送按钮永久保持加载。
      handlers.onError?.('连接提前结束，未收到完整回答，请重试');
    } catch (error: any) {
      if (error?.name === 'AbortError') return;
      handlers.onError?.('连接中断，请稍后重试');
    }
  })();

  return controller;
};

export const ABILITY_LABELS: Record<string, string> = {
  chat: '资料问答',
  explain: '引导讲解',
  practice: '练习检测',
  map: '知识地图',
  organize: '学习整理',
};
