import React, { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Card,
  Row,
  Col,
  Button,
  Modal,
  Form,
  Input,
  Upload,
  Tag,
  Space,
  Statistic,
  Popconfirm,
  message,
  Spin,
  Empty,
  Typography,
  List,
} from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  CloudServerOutlined,
  InboxOutlined,
  FileTextOutlined,
  BuildOutlined,
  ReloadOutlined,
  EyeOutlined,
} from '@ant-design/icons';
import type { UploadProps } from 'antd';
import KnowledgeGraph from '../KnowledgeGraph';
import { SkeletonCard } from '../AnimateMotion';
import { playOpenSound, playCloseSound, playSuccessSound, playTapSound } from '../../utils/sound';
import { KnowledgeBase, Subject } from '../../types';
import { getSubjects, createSubject, deleteSubject } from '../../api/subjects';
import {
  getKnowledgeBases,
  uploadFile,
  getDocuments,
  deleteDocument,
  rebuildKnowledge,
  getKnowledgeStats,
} from '../../api/knowledge';

const { Dragger } = Upload;
const { Text, Title } = Typography;

// 学科详情视图组件
const SubjectDetailView: React.FC<{
  subject: Subject;
  onBack: () => void;
  onRefresh: () => void;
}> = ({ subject, onBack, onRefresh }) => {
  const [documents, setDocuments] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [building, setBuilding] = useState(false);
  const [uploading, setUploading] = useState(false);

  const subjectId = subject.subject_id;

  // 加载文档列表和统计信息
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [docsRes, statsRes] = await Promise.allSettled([
        getDocuments(subjectId),
        getKnowledgeStats(subjectId),
      ]);
      
      if (docsRes.status === 'fulfilled') {
        const docsData = docsRes.value;
        setDocuments(docsData?.documents || []);
      }
      if (statsRes.status === 'fulfilled') {
        // API直接返回 {subject_id, status, stats}，无需.data
        setStats(statsRes.value);
      }
    } catch (error) {
      console.error('加载数据失败:', error);
    } finally {
      setLoading(false);
    }
  }, [subjectId]);

  useEffect(() => {
    // 等展开动画完全结束（450ms）后再加载数据，避免动画期间主线程被数据请求占用
    const timer = setTimeout(() => {
      loadData();
    }, 450);
    return () => clearTimeout(timer);
  }, [loadData]);

  // 上传文件
  const handleUpload = async (file: File) => {
    const allowedTypes = ['.pdf', '.docx', '.txt', '.md', '.jpg', '.jpeg', '.png', '.webp'];
    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    
    if (!allowedTypes.includes(ext)) {
      message.error(`不支持的文件格式: ${ext}，支持: ${allowedTypes.join(', ')}`);
      return false;
    }

    if (file.size > 20 * 1024 * 1024) {
      message.error('文件大小不能超过20MB');
      return false;
    }

    setUploading(true);
    try {
      await uploadFile(subjectId, file);
      message.success(`文件 ${file.name} 上传成功`);
      loadData();
      onRefresh();
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '上传失败');
    } finally {
      setUploading(false);
    }
    return false;
  };

  // 删除文档
  const handleDeleteDocument = async (filename: string) => {
    try {
      await deleteDocument(subjectId, filename);
      message.success('文档已删除');
      loadData();
      onRefresh();
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败');
    }
  };

  // 查看文档（统一弹窗预览）
  const [previewDoc, setPreviewDoc] = useState<{ name: string; url: string } | null>(null);

  const handleViewDocument = (filename: string) => {
    const encodedFilename = encodeURIComponent(filename)
      .replace(/\(/g, '%28')
      .replace(/\)/g, '%29');
    const encodedSubject = encodeURIComponent(subjectId);
    const url = `http://localhost:8000/api/knowledge/${encodedSubject}/documents/${encodedFilename}/file`;
    // 所有文档统一走弹窗预览
    setPreviewDoc({ name: filename, url });
  };

  // 构建知识库（独立操作，不再自动生成知识图谱；图谱由用户单独点击生成）
  const handleBuild = async () => {
    if (documents.length === 0) {
      message.warning('请先上传文档');
      return;
    }

    setBuilding(true);
    try {
      await rebuildKnowledge(subjectId);
      message.success('知识库构建成功');
      playSuccessSound();
      loadData();
      onRefresh();
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '构建失败');
    } finally {
      setBuilding(false);
    }
  };

  const uploadProps: UploadProps = {
    name: 'file',
    multiple: true,
    beforeUpload: handleUpload,
    showUploadList: false,
    accept: '.pdf,.docx,.txt,.md,.jpg,.jpeg,.png,.webp',
  };

  return (
    <div>
      {/* 顶部导航 */}
      <div style={{ marginBottom: 16 }}>
        <Button type="link" onClick={() => { playCloseSound(); onBack(); }} style={{ padding: 0 }}>
          ← 返回学科列表
        </Button>
      </div>

      {/* 学科信息卡片 */}
      <Card style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col flex="auto">
            <Title level={4} style={{ margin: 0 }}>{subject.name}</Title>
            <Text type="secondary">学科ID: {subjectId}</Text>
            {subject.description && (
              <div style={{ marginTop: 4 }}>
                <Text>{subject.description}</Text>
              </div>
            )}
          </Col>
          <Col>
            <Space>
              {loading ? (
                <Tag color="default">加载中...</Tag>
              ) : (
              <Tag color={stats?.status === 'ready' ? 'success' : stats?.status === 'building' ? 'processing' : stats?.status === 'failed' ? 'error' : 'default'}>
                {stats?.status === 'ready' ? '已构建' : stats?.status === 'building' ? '构建中' : stats?.status === 'failed' ? '构建失败' : '未构建'}
              </Tag>
              )}
            </Space>
          </Col>
        </Row>
      </Card>

      {/* 统计信息 */}
      {stats && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={8}>
            <Card>
              <Statistic title="文档数量" value={stats.stats?.document_count || 0} />
            </Card>
          </Col>
          <Col span={8}>
            <Card>
              <Statistic title="分块数量" value={stats.stats?.chunk_count || 0} />
            </Card>
          </Col>
          <Col span={8}>
            <Card>
              <Statistic 
                title="知识库状态" 
                value={stats?.status === 'ready' ? '已就绪' : stats?.status === 'building' ? '构建中' : stats?.status === 'failed' ? '构建失败' : '未构建'} 
                valueStyle={{ color: stats?.status === 'ready' ? '#3f8600' : stats?.status === 'failed' ? '#cf1322' : '#999' }}
              />
            </Card>
          </Col>
        </Row>
      )}

      {/* 文件上传区域 */}
      <Card title="上传文档" style={{ marginBottom: 16 }}>
        <Dragger {...uploadProps} disabled={uploading}>
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">
            {uploading ? '上传中...' : '点击或拖拽文件到此区域上传'}
          </p>
          <p className="ant-upload-hint">
            支持 PDF、DOCX、TXT、Markdown、图片（JPG/PNG/WEBP）格式，单个文件最大 20MB
          </p>
        </Dragger>
      </Card>

      {/* 文档列表 */}
      <Card 
        title={`已上传文档 (${documents.length})`}
        extra={
          <Space>
            <Button 
              icon={<ReloadOutlined />} 
              onClick={loadData}
              loading={loading}
            >
              刷新
            </Button>
            <Button
              type="primary"
              icon={<BuildOutlined />}
              onClick={handleBuild}
              loading={building}
              disabled={documents.length === 0}
            >
              构建知识库
            </Button>
          </Space>
        }
      >
        {loading ? (
          <div style={{ padding: '16px 0' }}>
            <SkeletonCard height={48} />
            <div style={{ height: 10 }} />
            <SkeletonCard height={48} />
            <div style={{ height: 10 }} />
            <SkeletonCard height={48} />
          </div>
        ) : documents.length === 0 ? (
          <Empty description="暂无文档，请上传文件" />
        ) : (
          <List
            loading={loading}
            dataSource={documents}
            renderItem={(doc: any, index: number) => (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: Math.min(index * 0.03, 0.3), duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
              >
              <List.Item
                actions={[
                  <Button
                    type="link"
                    icon={<EyeOutlined />}
                    onClick={() => { playTapSound(); handleViewDocument(doc.name); }}
                  >
                    查看
                  </Button>,
                  <Popconfirm
                    title="确定删除此文档？"
                    onConfirm={() => handleDeleteDocument(doc.name)}
                  >
                    <Button type="text" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                ]}
              >
                <List.Item.Meta
                  avatar={<FileTextOutlined style={{ fontSize: 24, color: '#0f766e' }} />}
                  title={doc.name}
                  description={
                    <Space>
                      <Text type="secondary">大小: {doc.size || '-'}</Text>
                      <Text type="secondary">类型: {doc.type || '-'}</Text>
                    </Space>
                  }
                />
              </List.Item>
              </motion.div>
            )}
          />
        )}
      </Card>

      {/* 知识图谱 */}
      <KnowledgeGraph subjectId={subjectId} />

      {/* 文档预览弹窗 */}
      <Modal
        title={previewDoc?.name || '文档预览'}
        open={!!previewDoc}
        onCancel={() => setPreviewDoc(null)}
        footer={null}
        width="80%"
        style={{ top: 20 }}
        destroyOnClose
      >
        {previewDoc && (
          <div style={{ width: '100%', height: '70vh' }}>
            <iframe
              src={previewDoc.url}
              style={{ width: '100%', height: '100%', border: 'none' }}
              title="文档预览"
            />
          </div>
        )}
      </Modal>
    </div>
  );
};

// 主知识库管理组件
const KnowledgeManager: React.FC = () => {
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [createModalVisible, setCreateModalVisible] = useState(false);
  const [creating, setCreating] = useState(false);
  const [selectedSubject, setSelectedSubject] = useState<Subject | null>(null);
  const [form] = Form.useForm();

  // 加载数据
  const loadData = async () => {
    setLoading(true);
    try {
      const [subjectsRes, kbRes] = await Promise.allSettled([
        getSubjects(),
        getKnowledgeBases(),
      ]);
      
      if (subjectsRes.status === 'fulfilled') {
        setSubjects(subjectsRes.value || []);
      }
      if (kbRes.status === 'fulfilled') {
        setKnowledgeBases(kbRes.value?.subjects || []);
      }
    } catch (error) {
      console.error('加载数据失败:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // 创建学科
  const handleCreateSubject = async (values: any) => {
    setCreating(true);
    try {
      // 自动生成 subject_id
      const subjectId = values.name
        .toLowerCase()
        .replace(/[^a-z0-9\u4e00-\u9fa5]/g, '_')
        .replace(/_+/g, '_')
        .substring(0, 20) || `subject_${Date.now()}`;
      
      await createSubject(subjectId, values.name, values.description);
      message.success('学科创建成功');
      setCreateModalVisible(false);
      form.resetFields();
      loadData();
    } catch (error: any) {
      const detail = error?.response?.data?.detail;
      if (detail?.includes('已存在')) {
        message.error('学科ID已存在，请使用其他名称');
      } else {
        message.error(detail || '创建失败');
      }
    } finally {
      setCreating(false);
    }
  };

  // 删除学科
  const handleDeleteSubject = async (subjectId: string) => {
    try {
      await deleteSubject(subjectId);
      message.success('学科已删除');
      loadData();
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败');
    }
  };

  // 获取学科的知识库状态
  const getSubjectKBStatus = (subjectId: string) => {
    const kb = knowledgeBases.find(k => k.subject_id === subjectId);
    return kb?.status || 'empty';
  };

  // 如果选中了学科，显示详情视图
  // 苹果风格果冻动画：展开/返回完全对称镜像
  if (selectedSubject) {
    return (
      <AnimatePresence mode="wait">
        <motion.div
          key={selectedSubject.subject_id}
          layoutId={`subject-${selectedSubject.subject_id}`}
          style={{ borderRadius: 14, overflow: 'hidden', height: '100%', willChange: 'transform, opacity' }}
          // 展开：纯 transform 放大（GPU加速不掉帧）
          initial={{ opacity: 0, scale: 0.85, y: 16 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          // 返回：layoutId 缩回卡片（保留现有果冻效果）
          exit={{ opacity: 0 }}
          transition={{ layout: { type: 'spring', stiffness: 320, damping: 24 }, opacity: { duration: 0.12 }, scale: { duration: 0.2, ease: [0.22, 1, 0.36, 1] }, y: { duration: 0.2, ease: [0.22, 1, 0.36, 1] } }}
        >
          <SubjectDetailView
            subject={selectedSubject}
            onBack={() => setSelectedSubject(null)}
            onRefresh={loadData}
          />
        </motion.div>
      </AnimatePresence>
    );
  }

  return (
    <div>
      {/* 标题栏 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>知识库管理</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadData} loading={loading}>
            刷新
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setCreateModalVisible(true)}
          >
            创建学科
          </Button>
        </Space>
      </div>

      {/* 学科列表 */}
      <AnimatePresence mode="wait">
      {loading ? (
        <div style={{ textAlign: 'center', padding: '50px 0' }}>
          <Spin size="large" tip="加载中..." />
        </div>
      ) : subjects.length === 0 ? (
        <Card>
          <Empty
            description="暂无学科，请创建新学科"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          >
            <Button type="primary" onClick={() => setCreateModalVisible(true)}>
              创建第一个学科
            </Button>
          </Empty>
        </Card>
      ) : (
        <Row gutter={[16, 16]}>
          {subjects.map((subject) => {
            const status = getSubjectKBStatus(subject.subject_id);
            return (
              <Col xs={24} sm={12} lg={8} key={subject.subject_id}>
                <motion.div
                  layoutId={`subject-${subject.subject_id}`}
                  style={{ borderRadius: 12, willChange: 'transform' }}
                  whileHover={{ y: -5, boxShadow: '0 8px 24px rgba(0,0,0,0.08)' }}
                  whileTap={{ scale: 0.98 }}
                  transition={{ type: 'spring', stiffness: 260, damping: 20 }}
                >
                <Card
                  hoverable
                  onClick={() => { playOpenSound(); setSelectedSubject(subject); }}
                  actions={[
                    <Popconfirm
                      title="确定删除此学科？"
                      description="删除后将同时删除相关知识库"
                      onConfirm={(e) => {
                        e?.stopPropagation();
                        handleDeleteSubject(subject.subject_id);
                      }}
                      onCancel={(e) => e?.stopPropagation()}
                    >
                      <Button 
                        type="text" 
                        danger 
                        icon={<DeleteOutlined />}
                        onClick={(e) => e.stopPropagation()}
                      >
                        删除
                      </Button>
                    </Popconfirm>
                  ]}
                >
                  <Card.Meta
                    avatar={<CloudServerOutlined style={{ fontSize: 32, color: '#0f766e' }} />}
                    title={
                      <Space>
                        {subject.name}
                        <Tag color={status === 'ready' ? 'success' : status === 'building' ? 'processing' : status === 'failed' ? 'error' : 'default'}>
                          {status === 'ready' ? '已构建' : status === 'building' ? '构建中' : status === 'failed' ? '构建失败' : '未构建'}
                        </Tag>
                      </Space>
                    }
                    description={
                      <div>
                        <div>{subject.description || '暂无描述'}</div>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          ID: {subject.subject_id}
                        </Text>
                      </div>
                    }
                  />
                </Card>
                </motion.div>
              </Col>
            );
          })}
        </Row>
      )}
      </AnimatePresence>

      {/* 创建学科弹窗 */}
      <Modal
        title="创建新学科"
        open={createModalVisible}
        onCancel={() => {
          setCreateModalVisible(false);
          form.resetFields();
        }}
        footer={null}
        width={480}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleCreateSubject}
          style={{ marginTop: 16 }}
        >
          <Form.Item
            label="学科名称"
            name="name"
            rules={[{ required: true, message: '请输入学科名称' }]}
          >
            <Input placeholder="例如：Python基础、机器学习" />
          </Form.Item>

          <Form.Item
            label="学科描述"
            name="description"
          >
            <Input.TextArea
              rows={3}
              placeholder="简要描述学科内容（可选）"
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Space>
              <Button onClick={() => {
                setCreateModalVisible(false);
                form.resetFields();
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit" loading={creating}>
                创建
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default KnowledgeManager;
