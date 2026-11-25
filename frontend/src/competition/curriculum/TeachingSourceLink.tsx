import type { ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import './curriculum.css';

export default function TeachingSourceLink({ questionId, quizId, classId, studentId, answerId, backTo, view, children }: {
  questionId: string;
  quizId: string;
  classId?: string;
  studentId?: string;
  answerId?: string;
  backTo?: () => string;
  view?: 'lecture';
  children?: ReactNode;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams({ questionId, quizId });
  const source = new URLSearchParams(location.search).get('source');
  if (source) params.set('source', source);
  if (classId) params.set('classId', classId);
  if (studentId) params.set('studentId', studentId);
  if (answerId) params.set('answerId', answerId);
  if (view) params.set('view', view);
  params.set('backTo', location.pathname + location.search);
  return <Link className="teacher-source-link" to={'/curriculum?' + params} onClick={(event) => {
    if (!backTo || event.button !== 0 || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    event.preventDefault();
    params.set('backTo', backTo());
    navigate('/curriculum?' + params);
  }}>{children ?? (view === 'lecture' ? '教师讲评卡' : '查教学出处')}</Link>;
}
