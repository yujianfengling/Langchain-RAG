import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Dropdown } from 'antd';
import { DownOutlined, LogoutOutlined } from '@ant-design/icons';
import { useAuth } from '../../context/AuthContext';
import { getSourceLabel, getSourceNotice } from '../dataSource';
import { analysisReturnPath } from './teacherContext';
import ErrorBoundary from './ErrorBoundary';

const NAV = [
  { path: '/class-analytics', label: '考试分析' },
  { path: '/student-answers', label: '学生答卷' },
];

const CompetitionChrome: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { logout } = useAuth();
  const source = getSourceLabel();
  const official = source === '官方赛题数据';
  const [notice, setNotice] = useState<string | null>(getSourceNotice());
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
          <button type="button" className="teacher-text-link" aria-current={location.pathname === '/curriculum' ? 'page' : undefined}
            onClick={() => go('/curriculum')}>教材资料</button>
          {!official && <button type="button" className="comp-source is-demo"
            onClick={() => window.location.assign('/class-analytics?source=official')}>
            {source} · 切换官方数据
          </button>}
          <Dropdown trigger={['click']} menu={{ items: [{ key: 'logout', icon: <LogoutOutlined />,
            label: '退出登录', onClick: () => { void logout(); } }] }}>
            <button type="button" className="comp-account" aria-label="账号菜单">教师 <DownOutlined /></button>
          </Dropdown>
        </div>
      </header>
      {notice && <div className="comp-notice" role="status">
        官方数据暂未就绪，当前显示演示样例：{notice}
        <button type="button" onClick={() => window.location.reload()}>重试</button>
      </div>}
      <main className="comp-main"><ErrorBoundary>{children}</ErrorBoundary></main>
    </div>
  );
};

export default CompetitionChrome;
