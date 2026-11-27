import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Button, Dropdown } from 'antd';
import { DownOutlined, LogoutOutlined, UploadOutlined } from '@ant-design/icons';
import { useAuth } from '../../context/AuthContext';
import { getBundle, getSourceLabel, getSourceNotice, isPrivateCompetitionData } from '../dataSource';
import { analysisReturnPath, assistantPath } from './teacherContext';
import ErrorBoundary from './ErrorBoundary';
import ImportPanel from './ImportPanel';

const NAV = [
  { path: '/class-analytics', label: '考试分析' },
  { path: '/student-answers', label: '学生学情' },
  { path: '/curriculum', label: '课标与教材' },
  { path: '/assistant', label: 'AI 助手' },
];

const CompetitionChrome: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { logout } = useAuth();
  const source = getSourceLabel();
  const official = isPrivateCompetitionData();
  const [notice, setNotice] = useState<string | null>(getSourceNotice());
  const [importOpen, setImportOpen] = useState(false);
  useEffect(() => {
    const onFail = (e: Event) => setNotice((e as CustomEvent<string>).detail || null);
    const onReady = () => setNotice(null);
    window.addEventListener('competition:source-failed', onFail);
    window.addEventListener('competition:source-ready', onReady);
    return () => {
      window.removeEventListener('competition:source-failed', onFail);
      window.removeEventListener('competition:source-ready', onReady);
    };
  }, []);

  const go = (path: string) => {
    if (location.pathname === path) return;
    const current = new URLSearchParams(location.search);
    if (path === '/assistant') {
      const fromExam = location.pathname === '/class-analytics';
      const bundle = getBundle();
      navigate(assistantPath(current, {
        fromExam,
        classId: fromExam ? bundle?.classes.find((c) => c.id === current.get('classId'))?.id || bundle?.classes[0]?.id : undefined,
        quizId: fromExam ? bundle?.quizzes.find((q) => q.id === current.get('quizId'))?.id || bundle?.quizzes[0]?.id : undefined,
      }));
      return;
    }
    if (path === '/class-analytics' && location.pathname !== path) {
      navigate(analysisReturnPath(current));
      return;
    }
    const next = new URLSearchParams();
    const keep = current.get('source');
    if (keep) next.set('source', keep);
    if (path === '/student-answers') {
      for (const key of ['quizId', 'classId']) {
        const value = current.get(key);
        if (value) next.set(key, value);
      }
      next.set('returnTo', analysisReturnPath(current));
    }
    navigate(`${path}${next.size ? `?${next}` : ''}`);
  };

  return (
    <div className="comp-shell teacher-shell">
      <header className="comp-header">
        <button className="comp-brand" onClick={() => go('/class-analytics')} type="button">
          <span className="comp-brand-text"><strong>智慧学伴</strong><small>教师端</small></span>
        </button>
        <nav className="comp-nav" aria-label="主导航">
          {NAV.map((item) => (
            <button key={item.path} type="button" className={'comp-nav-item' + (location.pathname === item.path ? ' is-current' : '')}
              aria-current={location.pathname === item.path ? 'page' : undefined} onClick={() => go(item.path)}>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="comp-right">
          <Button size="small" type="text" icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>导入评测数据</Button>
          <Dropdown trigger={['click']} menu={{ items: [{ key: 'logout', icon: <LogoutOutlined />,
            label: '退出登录', onClick: () => { void logout(); } }] }}>
            <button type="button" className="comp-account" aria-label="账号菜单">教师 <DownOutlined /></button>
          </Dropdown>
        </div>
      </header>
      {getBundle()?.manifest.independentEvaluation && <div className="comp-source">{source}</div>}
      {!official && <button type="button" className="comp-source is-demo"
        onClick={() => window.location.assign('/class-analytics?source=official')}>
        {source} · 切换官方数据
      </button>}
      {notice && <div className="comp-notice" role="status">
        官方数据暂未就绪，当前显示演示样例：{notice}
        <button type="button" onClick={() => window.location.reload()}>重试</button>
      </div>}
      <main className="comp-main"><ErrorBoundary>{children}</ErrorBoundary></main>
      <ImportPanel open={importOpen} onClose={() => setImportOpen(false)} />
    </div>
  );
};

export default CompetitionChrome;
