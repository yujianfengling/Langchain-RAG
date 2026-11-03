import React, { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import TutorWorkspace from '../components/TutorWorkspace';
import { useAuth } from '../context/AuthContext';

/**
 * AI 伴学（学生默认入口，2026-10-07）
 *
 * 兼容从其它页面带参数进入：
 *   /student?subject=<subject_id>         指定学科
 *   /student?docs=<doc_id,doc_id>         指定回答依据的资料范围
 *   /student?node=<node_id>               直接打开某个知识点
 */
const StudentLearningPage: React.FC = () => {
  const [params] = useSearchParams();
  const { user } = useAuth();
  const subjectId = params.get('subject') || undefined;
  const nodeId = params.get('node') || undefined;
  const docIds = useMemo(() => {
    const raw = params.get('docs');
    if (!raw) return undefined;
    return raw.split(',').map((s) => s.trim()).filter(Boolean);
  }, [params]);

  return (
    <TutorWorkspace
        key={(user?.id || '') + '|' + (subjectId || '') + '|' + (docIds || []).join(',') + '|' + (nodeId || '')}
        initialSubjectId={subjectId}
        initialDocIds={docIds}
        initialNodeId={nodeId}
      role="student"
    />
  );
};

export default StudentLearningPage;
