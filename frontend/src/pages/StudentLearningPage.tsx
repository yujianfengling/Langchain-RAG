import React, { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Spin } from 'antd';
import TutorWorkspace from '../components/TutorWorkspace';
import { useAuth } from '../context/AuthContext';
import { getKnowledgeMap } from '../api/tutor';

/**
 * AI 伴学（学生默认入口）
 *
 * 兼容从其它页面带参数进入：
 *   /student?subject=<subject_id>         指定学科
 *   /student?docs=<doc_id,doc_id>         指定回答依据的资料范围
 *   /student?node=<node_id>               直接打开某个知识点
 *   /student?task=<id>&kp=<知识点名>       任务页/学情页/知识图谱带入：按名字定位到地图节点
 *   /student?action=explain|practice|prereq|review  进入后自动发起对应学习动作
 */
const StudentLearningPage: React.FC = () => {
  const [params] = useSearchParams();
  const { user } = useAuth();
  const subjectId = params.get('subject') || undefined;
  const nodeId = params.get('node') || undefined;
  const kp = params.get('kp') || undefined;
  const action = params.get('action') || undefined;
  const docIds = useMemo(() => {
    const raw = params.get('docs');
    if (!raw) return undefined;
    return raw.split(',').map((s) => s.trim()).filter(Boolean);
  }, [params]);

  const [resolvedNode, setResolvedNode] = useState<string | undefined>(nodeId);
  const [resolving, setResolving] = useState(Boolean(kp && subjectId && !nodeId));
  useEffect(() => {
    if (!kp || !subjectId || nodeId) {
      setResolvedNode(nodeId);
      setResolving(false);
      return;
    }
    let alive = true;
    setResolving(true);
    getKnowledgeMap(subjectId).then((data: any) => {
      if (!alive) return;
      const nodes = data?.map?.nodes || data?.nodes || [];
      const hit = nodes.find((n: any) => n.label === kp && n.level !== 'chapter')
        || nodes.find((n: any) => n.label === kp)
        || nodes.find((n: any) => n.level !== 'chapter' && (n.label || '').includes(kp));
      setResolvedNode(hit?.node_id || undefined);
    }).catch(() => undefined).finally(() => { if (alive) setResolving(false); });
    return () => { alive = false; };
  }, [kp, subjectId, nodeId]);

  const initialAction = useMemo(() => {
    if (!action) return undefined;
    const topic = kp || '';
    if (action === 'practice') {
      return {
        prompt: topic ? `请围绕「${topic}」给我出 2 道巩固检测题。` : '请围绕当前知识点给我出 2 道巩固检测题。',
        ability: 'practice',
      };
    }
    if (action === 'review') {
      return {
        prompt: topic ? `我到了「${topic}」的遗忘复测节点，请出 2 道检验题帮我复测巩固。` : '请帮我复测当前知识点。',
        ability: 'practice',
      };
    }
    if (action === 'prereq') {
      return {
        prompt: topic ? `知识图谱提示我的前置概念「${topic}」存在薄弱，请先用通俗例子帮我补齐「${topic}」，再给 1 道小检查题。` : '请先帮我补齐当前知识点的前置基础。',
        ability: 'explain',
      };
    }
    if (action === 'explain') {
      return {
        prompt: topic ? `请结合课程资料和知识图谱关系，分步讲解「${topic}」的核心概念、公式与易错点。` : '请讲解当前知识点的核心概念与易错点。',
        ability: 'explain',
      };
    }
    return undefined;
  }, [action, kp]);

  if (resolving) {
    return <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spin /></div>;
  }

  return (
    <TutorWorkspace
      key={(user?.id || '') + '|' + (subjectId || '') + '|' + (docIds || []).join(',') + '|' + (resolvedNode || '') + '|' + (action || '') + '|' + (kp || '')}
      initialSubjectId={subjectId}
      initialDocIds={docIds}
      initialNodeId={resolvedNode}
      initialPrompt={initialAction?.prompt}
      initialAbility={initialAction?.ability}
      role="student"
    />
  );
};

export default StudentLearningPage;
