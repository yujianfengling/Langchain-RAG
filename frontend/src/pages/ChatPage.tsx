import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  Layout, Button, Drawer, Empty, Space, Typography, message, Popconfirm, Spin, Collapse, Select,
} from 'antd';
import {
  MessageOutlined, PlusOutlined, DeleteOutlined, RocketOutlined,
  AimOutlined, ExperimentOutlined, SettingOutlined, LoginOutlined,
} from '@ant-design/icons';
import ChatBox, { ChatPreset } from '../components/ChatBox';
import { getSubjects } from '../api/subjects';
import { listMyCourses } from '../api/studentCourses';
import { getConversations, createConversation, deleteConversation } from '../api/conversations';
import { Subject, Conversation } from '../types';
import LearningProgressPanel from '../components/LearningProgressPanel';
import LearningPathPanel from '../components/LearningPathPanel';
import KnowledgeExtractionPanel from '../components/KnowledgeExtractionPanel';
import CollapsiblePanel from '../components/CollapsiblePanel';
import PersonalizationSettings from '../components/PersonalizationSettings';

const { Content } = Layout;
const { Text } = Typography;

/** 记住上次进入的课程（刷新/返回后若仍有访问权限就恢复） */
const LAST_SUBJECT_KEY = 'edu_last_subject';

/**
 * AI 学习助手页
 *
 * 2026-10-05：贯通课程上下文。
 * - 进入助手时自动恢复上次课程；只有一门课就自动选中；多门给出选择入口；
 *   没有课程时给"加入课程"入口。
 * - 课程选择器只显示**真实课程名**（课程标题），不再显示学科注册表里自动生成的
 *   时间戳式名称。
 * - 切换课程/选择历史会话时，先切学科再切会话，避免把课程 A 的对话归到课程 B。
 */
const ChatPage: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selectedSubject, setSelectedSubject] = useState<string>('');
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [courseSubjectIds, setCourseSubjectIds] = useState<string[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [preset, setPreset] = useState<ChatPreset | undefined>(undefined);
  // 选择逻辑只跑一次（避免用户手动清空后被自动重新选上）
  const selectionDoneRef = useRef(false);

  const rememberSubject = useCallback((sid: string) => {
    try { if (sid) localStorage.setItem(LAST_SUBJECT_KEY, sid); } catch { /* 隐私模式忽略 */ }
  }, []);

  // 加载"我有权访问的课程/学科"：课程优先，并保留真实课程名
  const loadSubjects = useCallback(async () => {
    try {
      const [reg, mine] = await Promise.all([
        getSubjects().catch(() => [] as Subject[]),
        listMyCourses().then((r: any) => r?.data?.courses || []).catch(() => [] as any[]),
      ]);
      const merged = new Map<string, Subject>();
      for (const c of mine) {
        if (!c?.subject_id) continue;
        merged.set(c.subject_id, { ...(c as any), subject_id: c.subject_id,
                                   name: c.title || c.subject_id });
      }
      for (const s of reg) {
        if (s?.subject_id && !merged.has(s.subject_id)) merged.set(s.subject_id, s);
      }
      setSubjects([...merged.values()]);
      setCourseSubjectIds(mine.filter((c: any) => c?.subject_id).map((c: any) => c.subject_id));
      return [...merged.values()];
    } catch (error) {
      console.error('加载学科列表失败:', error);
      return [] as Subject[];
    }
  }, []);

  const loadConversations = useCallback(async (sid?: string) => {
    setLoading(true);
    try {
      const res: any = await getConversations(sid || undefined);
      setConversations(res?.data || []);
    } catch (error) {
      console.error('加载会话列表失败:', error);
      message.error('加载会话列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadSubjects(); }, [loadSubjects]);

  // 学科变化 → 只加载该学科的会话（未选则展示全部，供用户挑选）
  useEffect(() => {
    if (selectedSubject) loadConversations(selectedSubject);
    else loadConversations();
  }, [selectedSubject, loadConversations]);

  /**
   * 首次进入的课程选择：
   * 1) 上次课程且仍有访问权限 → 恢复；
   * 2) 没有历史选择、只有一门可访问课程 → 自动选中；
   * 3) 多门 → 保持未选，由选择器给出清楚入口；
   * 4) 没有课程 → 显示"加入课程"。
   */
  useEffect(() => {
    if (selectionDoneRef.current) return;
    if (subjects.length === 0) return;
    selectionDoneRef.current = true;
    const ids = new Set(subjects.map((s) => s.subject_id));
    let current = selectedSubject;
    // 之前选的课程已经不可访问（如退出班级）→ 清掉，再按"只剩一门就自动选"处理
    if (current && !ids.has(current)) {
      current = '';
      setSelectedSubject('');
      try { localStorage.removeItem(LAST_SUBJECT_KEY); } catch { /* 忽略 */ }
    }
    if (current) return;
    let stored = '';
    try { stored = localStorage.getItem(LAST_SUBJECT_KEY) || ''; } catch { /* 忽略 */ }
    if (stored && ids.has(stored)) {
      setSelectedSubject(stored);
      return;
    }
    if (stored && !ids.has(stored)) {
      try { localStorage.removeItem(LAST_SUBJECT_KEY); } catch { /* 忽略 */ }
    }
    if (courseSubjectIds.length === 1) {
      setSelectedSubject(courseSubjectIds[0]);
      rememberSubject(courseSubjectIds[0]);
    }
  }, [subjects, courseSubjectIds, selectedSubject, rememberSubject]);

  // 预设跳转：先切学科（并记住），再应用模式；清空旧会话避免串台
  useEffect(() => {
    const incoming = (location.state as any)?.preset as
      (ChatPreset & { subjectId?: string }) | undefined;
    if (!incoming) return;
    if (incoming.subjectId) {
      setSelectedSubject(incoming.subjectId);
      setActiveConvId('');
      rememberSubject(incoming.subjectId);
      selectionDoneRef.current = true;
    }
    setPreset({
      mode: incoming.mode, input: incoming.input, taskId: incoming.taskId,
      autoStart: incoming.autoStart, review: incoming.review,
    });
    navigate(location.pathname, { replace: true, state: null });
  }, [location.state, location.pathname, navigate, rememberSubject]);

  const handleSubjectChange = (sid: string) => {
    selectionDoneRef.current = true;
    setSelectedSubject(sid);
    setActiveConvId('');
    rememberSubject(sid);
  };

  const handleNewConversation = async () => {
    if (!selectedSubject) {
      message.warning('请先选择课程');
      return;
    }
    setCreating(true);
    try {
      const res: any = await createConversation(selectedSubject);
      const conv = res?.data;
      if (conv?.id) {
        setActiveConvId(conv.id);
        await loadConversations(selectedSubject);
      }
    } catch (error) {
      message.error('创建会话失败');
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteConversation = async (convId: string) => {
    try {
      await deleteConversation(convId);
      if (activeConvId === convId) setActiveConvId('');
      await loadConversations(selectedSubject || undefined);
      message.success('会话已删除');
    } catch (error) {
      message.error('删除会话失败');
    }
  };

  /**
   * 打开历史会话：如果它属于另一门课程，先切到那门课再打开，
   * 避免"课程 A 的会话显示在课程 B 下"。
   */
  const handleOpenConversation = (conv: Conversation) => {
    const sid = conv.subject_id;
    if (sid && sid !== selectedSubject) {
      selectionDoneRef.current = true;
      setSelectedSubject(sid);
      rememberSubject(sid);
    }
    setActiveConvId(conv.id);
    setHistoryOpen(false);
  };

  const grouped = useMemo(() => {
    const map = new Map<string, Conversation[]>();
    for (const c of conversations) {
      const list = map.get(c.subject_id) || [];
      list.push(c);
      map.set(c.subject_id, list);
    }
    return Array.from(map.entries());
  }, [conversations]);

  const subjectName = useCallback((sid: string) => {
    return subjects.find((s) => s.subject_id === sid)?.name || sid;
  }, [subjects]);

  const currentCourseName = subjects.find((s) => s.subject_id === selectedSubject)?.name || '';
  const hasCourses = courseSubjectIds.length > 0;

  const historyListNode = conversations.length === 0 ? (
    <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description={<span style={{ fontSize: 12, color: '#94a3b8' }}>还没有历史对话</span>}
    />
  ) : (
    grouped.map(([sid, convs]) => (
      <div key={sid} style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 12, color: '#475569', fontWeight: 600, padding: '4px 8px' }}>
          {subjectName(sid)}
        </div>
        {convs.map((c) => (
          <div
            key={c.id}
            onClick={() => handleOpenConversation(c)}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '8px 10px', borderRadius: 8, cursor: 'pointer',
              background: activeConvId === c.id ? '#f0fdfa' : 'transparent',
            }}
          >
            <Space size={8} style={{ minWidth: 0, flex: 1 }}>
              <MessageOutlined style={{ color: activeConvId === c.id ? '#0f766e' : '#94a3b8', fontSize: 13 }} />
              <Text style={{ fontSize: 13, color: activeConvId === c.id ? '#0f766e' : '#0f172a' }} ellipsis>
                {c.title || '新对话'}
              </Text>
            </Space>
            <Popconfirm
              title="删除该会话？"
              description="会话及其全部消息将被删除"
              okText="删除" cancelText="取消"
              onConfirm={(e) => { e?.stopPropagation(); handleDeleteConversation(c.id); }}
              onCancel={(e) => e?.stopPropagation()}
            >
              <Button type="text" size="small" danger icon={<DeleteOutlined />}
                onClick={(e) => e.stopPropagation()} />
            </Popconfirm>
          </div>
        ))}
      </div>
    ))
  );

  return (
    <Layout style={{ height: '100%' }}>
      <Content style={{
        padding: 16,
        background: '#f8fafc',
        overflow: 'hidden',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
      }}>
        <Space style={{ width: '100%', marginBottom: 10 }} size={8} wrap>
          <Button icon={<MessageOutlined />} onClick={() => setHistoryOpen(true)}>
            历史对话
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            disabled={!selectedSubject}
            loading={creating}
            onClick={handleNewConversation}
          >
            新建对话
          </Button>
          <div style={{ flex: 1, minWidth: 180, maxWidth: 360 }}>
            <Select
              value={selectedSubject || undefined}
              onChange={handleSubjectChange}
              placeholder={hasCourses
                ? (courseSubjectIds.length > 1 ? '请选择课程' : '选择课程')
                : '选择要学习的课程'}
              options={subjects.map((s) => ({ value: s.subject_id, label: s.name }))}
              showSearch
              optionFilterProp="label"
              size="middle"
              style={{ width: '100%' }}
            />
          </div>
          {!hasCourses && (
            <Button icon={<LoginOutlined />} onClick={() => navigate('/student?tab=courses')}>
              加入课程
            </Button>
          )}
          {!selectedSubject && subjects.length > 1 && (
            <Text type="secondary">请先选择课程，再开始提问</Text>
          )}
        </Space>
        <ChatBox
          subjectId={selectedSubject}
          subjects={subjects}
          isCourseSubject={courseSubjectIds.includes(selectedSubject)}
          conversationId={activeConvId || undefined}
          onConversationCreated={(id) => {
            setActiveConvId(id);
            loadConversations(selectedSubject);
          }}
          preset={preset}
          onPresetConsumed={() => setPreset(undefined)}
        />
      </Content>

      <Drawer
        placement="left"
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        width={300}
        title={<Space size={8}><MessageOutlined />历史对话
          {currentCourseName && (
            <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              {currentCourseName}
            </Text>
          )}
        </Space>}
      >
        {loading ? (
          <div style={{ textAlign: 'center', padding: '24px 0' }}><Spin size="small" /></div>
        ) : historyListNode}

        <Collapse
          ghost
          style={{ marginTop: 12 }}
          items={[{
            key: 'tools',
            label: '学习工具',
            children: (
              <>
                <CollapsiblePanel title="学习进度" icon={<RocketOutlined />} defaultOpen={false}>
                  <LearningProgressPanel userId="default_user" subjectId={selectedSubject} compact={true} />
                </CollapsiblePanel>
                <CollapsiblePanel title="学习路径" icon={<AimOutlined />} defaultOpen={false}>
                  <LearningPathPanel userId="default_user" subjectId={selectedSubject} />
                </CollapsiblePanel>
                <CollapsiblePanel title="知识扩展" icon={<ExperimentOutlined />} defaultOpen={false}>
                  <KnowledgeExtractionPanel userId="default_user" subjectId={selectedSubject} />
                </CollapsiblePanel>
                <CollapsiblePanel title="个性化设置" icon={<SettingOutlined />} defaultOpen={false}>
                  <PersonalizationSettings userId="default_user" subjectId={selectedSubject} />
                </CollapsiblePanel>
              </>
            ),
          }]}
        />
      </Drawer>
    </Layout>
  );
};

export default ChatPage;
