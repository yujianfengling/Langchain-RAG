import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Button, Card, Col, Row, Segmented, Space, Tabs, Tag, Typography } from 'antd';
import {
  AudioOutlined, BarChartOutlined, DatabaseOutlined, InfoCircleOutlined,
  RobotOutlined, SafetyCertificateOutlined, SettingOutlined, SlidersOutlined,
  UserOutlined,
} from '@ant-design/icons';
import PersonalizationSettings from '../components/PersonalizationSettings';
import SpeechSettings from '../components/SpeechSettings';
import TeacherActivation from '../components/TeacherActivation';
import SubjectSelector from '../components/SubjectSelector';
import SystemStatusPanel from '../components/SystemStatusPanel';
import { getHealth } from '../api/health';
import { useAuth } from '../context/AuthContext';
import { useCourse } from '../context/CourseContext';
import { useIsNarrow } from '../hooks/useIsNarrow';
import { readNavMode, writeNavMode, type NavMode } from '../components/AppChrome/navMode';

const { Title, Text, Paragraph } = Typography;

type SettingsTabKey = 'appearance' | 'study' | 'account' | 'system';

const TAB_ALIASES: Record<string, SettingsTabKey> = {
  preference: 'appearance',
  appearance: 'appearance',
  study: 'study',
  speech: 'study',
  account: 'account',
  teacher: 'account',
  system: 'system',
  status: 'system',
};

const SettingsPage: React.FC = () => {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const isNarrow = useIsNarrow('(max-width: 960px)');
  const { user } = useAuth();
  const { currentSubjectId } = useCourse();

  const [selectedSubject, setSelectedSubject] = useState<string>(currentSubjectId || '');
  const [version, setVersion] = useState<string>('');
  const [navMode, setNavMode] = useState<NavMode>(readNavMode);
  const initialTab = TAB_ALIASES[params.get('tab') || ''] || 'appearance';
  const [activeTab, setActiveTab] = useState<SettingsTabKey>(initialTab);

  useEffect(() => {
    if (currentSubjectId && !selectedSubject) setSelectedSubject(currentSubjectId);
  }, [currentSubjectId, selectedSubject]);

  useEffect(() => {
    const mapped = TAB_ALIASES[params.get('tab') || ''];
    if (mapped) setActiveTab(mapped);
  }, [params]);

  useEffect(() => {
    let alive = true;
    getHealth()
      .then((info) => { if (alive) setVersion(info?.version || ''); })
      .catch(() => { /* 后端未启动时不显示版本，也不弹错打扰用户 */ });
    return () => { alive = false; };
  }, []);

  const handleTabChange = (key: string) => {
    const next = (TAB_ALIASES[key] || 'appearance') as SettingsTabKey;
    setActiveTab(next);
    setParams((prev) => {
      const updated = new URLSearchParams(prev);
      updated.set('tab', next);
      return updated;
    }, { replace: true });
  };

  return (
    <div className="app-page app-page-wide">
      {/* 紧凑页头 */}
      <Card size="small" styles={{ body: { padding: '12px 18px' } }} style={{ marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
          <Space size={10} align="center">
            <SettingOutlined style={{ fontSize: 20, color: 'var(--color-primary)' }} />
            <div>
              <Title level={4} style={{ margin: 0, fontSize: 17 }}>系统设置</Title>
              <Text type="secondary" style={{ fontSize: 12 }}>
                按模块管理导航偏好、讲解风格、语音输入、身份权限与系统运行状态
              </Text>
            </div>
          </Space>
          <Space size={8} wrap>
            <Button icon={<BarChartOutlined />} onClick={() => navigate('/me')}>返回个人学情</Button>
            <Button type="primary" icon={<RobotOutlined />} onClick={() => navigate('/student')}>进入 AI 伴学</Button>
          </Space>
        </div>
      </Card>

      {/* 左侧分组标签（窄屏自动切顶部），每页控制在一屏内 */}
      <Card size="small" styles={{ body: { padding: isNarrow ? '10px 14px 16px' : '16px 20px' } }}>
        <Tabs
          tabPosition={isNarrow ? 'top' : 'left'}
          activeKey={activeTab}
          onChange={handleTabChange}
          items={[
            {
              key: 'appearance',
              label: (
                <Space size={6}>
                  <SlidersOutlined />
                  <span>界面与偏好</span>
                </Space>
              ),
              children: (
                <Row gutter={[16, 16]}>
                  <Col xs={24} lg={10}>
                    <Space direction="vertical" size={14} style={{ width: '100%' }}>
                      <Card size="small" type="inner" title="导航方式">
                        <Space direction="vertical" size={10} style={{ width: '100%' }}>
                          <Text type="secondary" style={{ fontSize: 13 }}>
                            顶栏平时收成顶部拉条，展开即可切页；左轮不留导航条，在空白处右键（或长按）打开并用滚轮换页。
                          </Text>
                          <Segmented
                            value={navMode}
                            onChange={(value) => {
                              const next: NavMode = value === 'cylinder' ? 'cylinder' : 'top';
                              setNavMode(next);
                              writeNavMode(next);
                            }}
                            options={[
                              { label: '顶栏模式（默认）', value: 'top' },
                              { label: '左轮模式', value: 'cylinder' },
                            ]}
                          />
                        </Space>
                      </Card>

                      <Card size="small" type="inner" title="个人学情首页">
                        <Space direction="vertical" size={10} style={{ width: '100%' }}>
                          <Text type="secondary" style={{ fontSize: 13 }}>
                            登录后默认进入「个人学情」首页，集中展示掌握诊断、知识图谱热力、遗忘预警、错因画像与教师批改评语。
                          </Text>
                          <Button type="primary" ghost size="small" icon={<BarChartOutlined />} onClick={() => navigate('/me')}>
                            前往个人学情
                          </Button>
                        </Space>
                      </Card>
                    </Space>
                  </Col>

                  <Col xs={24} lg={14}>
                    <div id="preference">
                      <PersonalizationSettings userId={user?.id} subjectId={selectedSubject || currentSubjectId || ''} />
                    </div>
                  </Col>
                </Row>
              ),
            },
            {
              key: 'study',
              label: (
                <Space size={6}>
                  <AudioOutlined />
                  <span>学科与语音</span>
                </Space>
              ),
              children: (
                <Row gutter={[16, 16]}>
                  <Col xs={24} lg={12}>
                    <SpeechSettings />
                  </Col>
                  <Col xs={24} lg={12}>
                    <Space direction="vertical" size={14} style={{ width: '100%' }}>
                      <Card size="small" type="inner" title={<Space><UserOutlined />当前学习学科</Space>}>
                        <SubjectSelector value={selectedSubject} onChange={setSelectedSubject} />
                      </Card>

                      <Card size="small" type="inner" title={<Space><DatabaseOutlined />资料知识库（个人 / 课程）</Space>}>
                        <Space direction="vertical" size={8} style={{ width: '100%' }}>
                          <Text type="secondary" style={{ fontSize: 13 }}>
                            个人资料库用于管理自己上传的课件与索引；教师课程资料由任课老师维护、仅本班学生可见，两者权限隔离。
                          </Text>
                          <Link to="/student/materials">
                            <Button size="small">进入资料库管理</Button>
                          </Link>
                        </Space>
                      </Card>
                    </Space>
                  </Col>
                </Row>
              ),
            },
            {
              key: 'account',
              label: (
                <Space size={6}>
                  <SafetyCertificateOutlined />
                  <span>身份与权限</span>
                </Space>
              ),
              children: (
                <Row gutter={[16, 16]}>
                  <Col xs={24} lg={14}>
                    <TeacherActivation />
                  </Col>
                  <Col xs={24} lg={10}>
                    <Card size="small" type="inner" title="账号与角色说明">
                      <Space direction="vertical" size={8} style={{ width: '100%' }}>
                        <Space size={8}>
                          <Text type="secondary">当前账号：</Text>
                          <Text strong>{user?.display_name || user?.username || '—'}</Text>
                          <Tag color={user?.role === 'teacher' || user?.role === 'admin' ? 'blue' : 'default'}>
                            {user?.role === 'admin' ? '管理员' : user?.role === 'teacher' ? '教师' : '学生'}
                          </Tag>
                        </Space>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          修改显示姓名、更换头像可在「个人学情 → 账号与偏好」完成。
                        </Text>
                        <Button size="small" onClick={() => navigate('/me')}>去个人资料</Button>
                      </Space>
                    </Card>
                  </Col>
                </Row>
              ),
            },
            {
              key: 'system',
              label: (
                <Space size={6}>
                  <InfoCircleOutlined />
                  <span>运行状态与关于</span>
                </Space>
              ),
              children: (
                <Row gutter={[16, 16]}>
                  <Col xs={24} lg={13}>
                    <SystemStatusPanel />
                  </Col>
                  <Col xs={24} lg={11}>
                    <Space direction="vertical" size={14} style={{ width: '100%' }}>
                      <Card size="small" type="inner" title="系统信息">
                        <Space direction="vertical" size={8} style={{ width: '100%', fontSize: 13 }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                            <Text type="secondary">系统名称</Text>
                            <Text>知学 · RAG 智慧教育系统</Text>
                          </div>
                          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                            <Text type="secondary">当前版本</Text>
                            <Text>{version || '—'}</Text>
                          </div>
                          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                            <Text type="secondary">开发接口</Text>
                            <Space size={12}>
                              <a href="/docs" target="_blank" rel="noopener noreferrer">API 文档</a>
                              <a href="/health" target="_blank" rel="noopener noreferrer">健康检查</a>
                            </Space>
                          </div>
                        </Space>
                      </Card>

                      <Card size="small" type="inner" title="架构说明（RAG 与 LangChain）">
                        <Space direction="vertical" size={6} style={{ width: '100%' }}>
                          <Paragraph style={{ marginBottom: 4, fontSize: 12 }}>
                            <strong>LangChain 组件：</strong>文档加载、递归切分、统一 Document 模型、本地向量化与结构化输出链。
                          </Paragraph>
                          <Paragraph style={{ marginBottom: 4, fontSize: 12 }}>
                            <strong>项目自研：</strong>FAISS + BM25 混合检索、RRF 重排、图谱增强检索、学情追踪（BKT + FSRS）、工具化伴学与权限隔离。
                          </Paragraph>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            所有回答引用与学情结论均基于真实资料片段和真实作答记录。
                          </Text>
                        </Space>
                      </Card>
                    </Space>
                  </Col>
                </Row>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
};

export default SettingsPage;
