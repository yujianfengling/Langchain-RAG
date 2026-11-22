/**
 * 比赛版独立外壳（替换大创 AppChrome）。
 *
 * 设计意图：老师登录后第一眼就能确认「我在哪个系统、现在看的是哪份数据、
 * 从哪儿开始」。刻意不引入大创遗留的隐藏下拉手势、滚轮换页、进入学生主页等交互，
 * 也不显示与本次比赛无关的系统降级横幅。
 *
 * 滚动容器在 .comp-main，页面内容可以完整滚动，不会被 overflow:hidden 裁掉。
 */
import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Dropdown } from 'antd';
import { DownOutlined, LogoutOutlined } from '@ant-design/icons';
import { useAuth } from '../../context/AuthContext';
import { getBundle, getSourceLabel, getSourceNotice } from '../dataSource';
import ErrorBoundary from './ErrorBoundary';

const NAV = [
  { path: '/class-analytics', label: '班级学情' },
  { path: '/student-answers', label: '学生答卷' },
  { path: '/curriculum', label: '课标与教材' },
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
    // 显式演示/自造样例模式在导航时保留来源；默认官方模式不需要参数。
    const keep = new URLSearchParams(location.search).get('source');
    navigate(keep && keep !== 'official' ? `${path}?source=${keep}` : path);
  };

  const switchToOfficial = () => {
    window.location.assign('/class-analytics?source=official&quizId=N01&classId=DC01&kpId=T01I2');
  };

  /** 「现场补测」独立入口：带着当前学生/题目直达补测面板，不让老师自己找按钮。 */
  const goPretest = () => {
    const sp = new URLSearchParams();
    if (location.pathname === '/student-answers') {
      new URLSearchParams(location.search).forEach((v, k) => { if (k !== 'pretest') sp.set(k, v); });
    }
    if (!sp.get('studentId')) {
      const bundle = getBundle();
      const c = bundle?.caseBook?.cases[0];
      if (c) {
        const qid = c.question_findings[0]?.item_id
          || bundle?.curriculumMap?.indicators.find((x) => x.indicator_id === c.indicator_id)?.questions[0]?.material_id
          || '';
        sp.set('studentId', c.student_id);
        sp.set('questionId', qid);
        sp.set('quizId', c.node_id);
        sp.set('kpId', c.indicator_id);
        sp.set('classId', bundle?.students.find((s) => s.id === c.student_id)?.classId || '');
      }
    }
    const keep = new URLSearchParams(location.search).get('source');
    if (keep && keep !== 'official' && !sp.get('source')) sp.set('source', keep);
    sp.set('pretest', '1');
    navigate({ pathname: '/student-answers', search: sp.toString() });
  };

  return (
    <div className="comp-shell">
      <header className="comp-header">
        <div className="comp-brand" onClick={() => go('/class-analytics')} role="button" tabIndex={0}
             onKeyDown={(e) => { if (e.key === 'Enter') go('/class-analytics'); }}>
          <span className="comp-mark">伴</span>
          <span className="comp-brand-text">
            <strong>智慧学伴</strong>
            <small>七年级数学 · 考后分析</small>
          </span>
        </div>

        <nav className="comp-nav" aria-label="主导航">
          {NAV.map((item) => {
            const active = location.pathname === item.path;
            return (
              <button key={item.path} type="button"
                      className={'comp-nav-item' + (active ? ' is-current' : '')}
                      aria-current={active ? 'page' : undefined}
                      onClick={() => go(item.path)}>
                {item.label}
              </button>
            );
          })}
          <button type="button" className="comp-nav-item comp-nav-cta" onClick={goPretest}>
            现场补测
          </button>
        </nav>

        <div className="comp-right">
          {official ? (
            <span className="comp-source is-official" title="数据来自赛题组统一提供的模拟学情数据">
              <span className="comp-dot" />官方赛题数据
            </span>
          ) : (
            <button type="button" className="comp-source is-demo" onClick={switchToOfficial}
                    title="当前为非官方数据，点击切换到官方赛题数据">
              {source === '演示样例数据' ? '演示样例（非赛题数据）' : source} · 切换官方
            </button>
          )}
          <Dropdown
            trigger={['click']}
            menu={{
              items: [{
                key: 'logout', icon: <LogoutOutlined />, label: '退出登录',
                onClick: () => { void logout(); },
              }],
            }}
          >
            <button type="button" className="comp-account" aria-label="账号菜单">
              <span className="comp-avatar">师</span>
              <span>教师</span>
              <DownOutlined style={{ fontSize: 10 }} />
            </button>
          </Dropdown>
        </div>
      </header>

      {notice && (
        <div className="comp-notice" role="status">
          官方数据暂未就绪，当前显示演示样例：{notice}
          <button type="button" onClick={() => window.location.reload()}>重试</button>
        </div>
      )}

      <main className="comp-main"><ErrorBoundary>{children}</ErrorBoundary></main>
    </div>
  );
};

export default CompetitionChrome;
