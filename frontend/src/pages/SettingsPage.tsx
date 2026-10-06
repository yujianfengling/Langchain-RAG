import React from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Layout, Typography, Space, Card } from 'antd';
import {
  SettingOutlined, InfoCircleOutlined, UserOutlined, DatabaseOutlined,
} from '@ant-design/icons';
import UserProfileDashboard from '../components/UserProfileDashboard';
import PersonalizationSettings from '../components/PersonalizationSettings';
import SpeechSettings from '../components/SpeechSettings';
import TeacherActivation from '../components/TeacherActivation';
import SubjectSelector from '../components/SubjectSelector';
import SystemStatusPanel from '../components/SystemStatusPanel';
import { getHealth } from '../api/health';

const { Header, Content } = Layout;
const { Title, Text, Paragraph } = Typography;

const SettingsPage: React.FC = () => {
  const [params] = useSearchParams();
  const [selectedSubject, setSelectedSubject] = React.useState<string>('');
  // 版本从后端取，不要在页面里硬编码（此前写死 1.0.0，而实际已是 2.2.1）
  const [version, setVersion] = React.useState<string>('');

  React.useEffect(() => {
    let alive = true;
    getHealth()
      .then((info) => { if (alive) setVersion(info?.version || ''); })
      .catch(() => { /* 后端未启动时不显示版本，也不弹错打扰用户 */ });
    return () => { alive = false; };
  }, []);

  // 头像菜单「讲解偏好」深链：滚动到偏好区（不新增页面）
  React.useEffect(() => {
    if (params.get('tab') !== 'preference') return;
    const el = document.getElementById('preference');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [params]);

  return (
    <Layout style={{ height: '100%' }}>
      <Header style={{ 
        background: '#fff',
        padding: '0 24px',
        borderBottom: '1px solid #f0f0f0',
        display: 'flex',
        alignItems: 'center',
      }}>
        <Space>
          <SettingOutlined style={{ fontSize: '20px', color: '#0f766e' }} />
          <Title level={4} style={{ margin: 0 }}>
            系统设置
          </Title>
        </Space>
      </Header>
      
      <Content style={{ 
        padding: '24px',
        background: '#f5f5f5',
        overflow: 'auto',
      }}>
        <div style={{ 
          background: '#fff',
          borderRadius: '12px',
          padding: '24px',
          boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
        }}>
          {/* 学习仪表盘 */}
          <div style={{ marginBottom: 24 }}>
            <UserProfileDashboard userId="default_user" subjectId={selectedSubject} />
          </div>

          {/* 个性化设置（讲解偏好）：头像菜单的「讲解偏好」直达这里 */}
          <div id="preference" style={{ marginBottom: 24 }}>
            <PersonalizationSettings userId="default_user" subjectId={selectedSubject} />
          </div>

          {/* 语音设置（语音输入/本地 ASR；回答朗读已移除，2026-10-01） */}
          <div style={{ marginBottom: 24 }}>
            <TeacherActivation />

        <SpeechSettings />
          </div>

          {/* 学科选择 */}
          <div style={{ marginBottom: 24 }}>
            <Title level={5} style={{ marginBottom: '16px' }}>
              <UserOutlined /> 学习学科
            </Title>
            <SubjectSelector value={selectedSubject} onChange={setSelectedSubject} />
          </div>

          {/* 资料知识库（个人/管理）：课程资料入口已在「我的课程」内，这里保留个人资料入口 */}
          <div style={{ marginBottom: 24 }}>
            <Title level={5} style={{ marginBottom: '16px' }}>
              <DatabaseOutlined /> 资料知识库（个人 / 管理）
            </Title>
            <Card size="small">
              <Space direction="vertical" style={{ width: '100%' }}>
                <Text type="secondary">
                  个人资料知识库的上传与索引管理入口。教师课程的「课程资料」在「我的课程 → 课程资料与知识库」里查看，
                  两套资料权限不同：课程资料由任课老师维护、只对本课程学生可见。
                </Text>
                <Link to="/knowledge">进入知识库管理</Link>
              </Space>
            </Card>
          </div>

          {/* 运行状态：外部能力与当日用量（观测数据的用户可见入口） */}
          <div style={{ marginBottom: 24 }}>
            <SystemStatusPanel />
          </div>

          {/* 系统信息部分 */}
          <div>
            <Title level={5} style={{ marginBottom: '16px' }}>
              <InfoCircleOutlined /> 系统信息
            </Title>
            
            <Card size="small">
              <Space direction="vertical" style={{ width: '100%' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text>系统名称</Text>
                  <Text>RAG与LangChain融合的智慧教育系统</Text>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text>版本</Text>
                  <Text>{version || '—'}</Text>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text>API文档</Text>
                  <Text>
                    <a href="/docs" target="_blank" rel="noopener noreferrer">
                      查看API文档
                    </a>
                  </Text>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text>健康检查</Text>
                  <Text>
                    <a href="/health" target="_blank" rel="noopener noreferrer">
                      检查系统状态
                    </a>
                  </Text>
                </div>
              </Space>
            </Card>
            
            <Paragraph type="secondary" style={{ marginTop: '16px', fontSize: '12px' }}>
              RAG与LangChain融合的智慧教育系统：基于课程资料的检索增强问答与教学辅助。
              支持多学科/课程知识库、资料级引用、智能问答与教学辅助。
            </Paragraph>

            {/* 技术说明：准确区分 LangChain 组件与自研逻辑，避免把普通函数调用说成"LangChain 链" */}
            <Card size="small" style={{ marginTop: 12 }}>
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                <Text strong>技术说明（RAG 与 LangChain）</Text>
                <Text style={{ fontSize: 12 }}>
                  LangChain 组件：文档加载（PyPDFLoader / TextLoader / UnstructuredMarkdownLoader）、
                  文本切分（RecursiveCharacterTextSplitter）、统一文档对象（langchain_core Document）、
                  本地向量化（HuggingFaceEmbeddings）；思维导图生成使用 LangChain 表达式链
                  （ChatOpenAI + ChatPromptTemplate + JsonOutputParser）。
                </Text>
                <Text style={{ fontSize: 12 }}>
                  项目自研：FAISS 向量存储与缓存（直接调用 faiss）、BM25 关键词检索、RRF 融合与重排、
                  检索阈值与降级、提示词与上下文组装、模型调用与流式输出、会话与学习闭环、权限校验。
                </Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  问答与检索主链路（chat_service + rag_service）是自研流程，不是 LangChain Chain；
                  引用只来自真实检索到的资料片段。
                </Text>
              </Space>
            </Card>
          </div>
        </div>
      </Content>
    </Layout>
  );
};

export default SettingsPage;