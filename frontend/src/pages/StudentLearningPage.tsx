import React, { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Spin } from 'antd';
import TutorWorkspace from '../components/TutorWorkspace';
import { useAuth } from '../context/AuthContext';
import { getKnowledgeMap } from '../api/tutor';

/**
 * AI 伴学（学生默认入口，2026-10-07）
 *
 * 兼容从其它页面带参数进入：
 *   /student?subject=<subject_id>         指定学科
 *   /student?docs=<doc_id,doc_id>         指定回答依据的资料范围
 *   /student?node=<node_id>               直接打开某个知识点
 *   /student?task=<id>&kp=<知识点名>       任务页带入：按名字定位到地图节点
 */
const StudentLearningPage: React.FC = () => {
  const [params] = useSearchParams();
  const { user } = useAuth();
  const subjectId = params.get('subject') || undefined;
  const nodeId = params.get('node') || undefined;
  const kp = params.get('kp') || undefined;
  const docIds = useMemo(() => {
    const raw = params.get('docs');
    if (!raw) return undefined;
    return raw.split(',').map((s) => s.trim()).filter(Boolean);
  }, [params]);

  // 任务页只带知识点名字：先解析成地图节点再挂载工作区，否则伴学不知道围绕谁讲
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

  if (resolving) {
    return <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spin /></div>;
  }

  return (
    <TutorWorkspace
        key={(user?.id || '') + '|' + (subjectId || '') + '|' + (docIds || []).join(',') + '|' + (resolvedNode || '')}
        initialSubjectId={subjectId}
        initialDocIds={docIds}
        initialNodeId={resolvedNode}
      role="student"
    />
  );
};

export default StudentLearningPage;