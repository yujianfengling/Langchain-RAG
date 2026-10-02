import React, { useState, useEffect } from 'react';
import { Card, List, Button, Space, Tag, message, Spin, Typography } from 'antd';
import { 
  SyncOutlined, 
  CheckCircleOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons';
import { ModelInfo, CurrentModel } from '../../types';
import { getAvailableModels, getCurrentModel, switchModel } from '../../api/models';
import './ModelSelector.css';

const { Text, Title } = Typography;

interface ModelSelectorProps {
  onModelChange?: (provider: string) => void;
}

const ModelSelector: React.FC<ModelSelectorProps> = ({ onModelChange }) => {
  const [currentModel, setCurrentModel] = useState<CurrentModel | null>(null);
  const [availableModels, setAvailableModels] = useState<ModelInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);

  // 加载模型信息
  const loadModelInfo = async () => {
    setLoading(true);
    try {
      const [currentResponse, availableResponse] = await Promise.all([
        getCurrentModel(),
        getAvailableModels(),
      ]);
      
      setCurrentModel(currentResponse.data);
      // 后端返回 data 即为模型数组
      const models = availableResponse.data;
      setAvailableModels(Array.isArray(models) ? models : []);
    } catch (error) {
      console.error('加载模型信息失败:', error);
      message.error('加载模型信息失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadModelInfo();
  }, []);

  // 切换模型
  const handleSwitchModel = async (provider: string) => {
    setSwitching(provider);
    try {
      await switchModel(provider);
      // 后端不会改动默认模型：如实说"已选择"，不谎称"已切换"
      message.success(`已选择 ${provider}（本次会话后续请求生效）`);
      await loadModelInfo(); // 重新加载信息
      onModelChange?.(provider);
    } catch (error) {
      console.error('切换模型失败:', error);
      message.error('切换模型失败');
    } finally {
      setSwitching(null);
    }
  };

  // 获取状态标签
  const getStatusTag = (model: ModelInfo) => {
    if (model.available) {
      return <Tag color="success" icon={<CheckCircleOutlined />}>可用</Tag>;
    } else {
      return <Tag color="error" icon={<ExclamationCircleOutlined />}>不可用</Tag>;
    }
  };

  // 获取提供商图标
  const getProviderIcon = (provider: string) => {
    switch (provider.toLowerCase()) {
      case 'mimo':
        return '⚡';
      case 'deepseek':
        return '🤖';
      case 'qwen':
        return '🧠';
      default:
        return '🔧';
    }
  };

  // 获取提供商描述
  const getProviderDescription = (provider: string) => {
    switch (provider.toLowerCase()) {
      case 'mimo':
        return '小米MiMo - 小米AI大模型';
      case 'deepseek':
        return 'DeepSeek API - 云端大语言模型';
      case 'qwen':
        return 'Qwen - 通义千问大模型';
      default:
        return '未知模型提供商';
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '40px' }}>
        <Spin size="large" tip="加载模型信息中..." />
      </div>
    );
  }

  return (
    <div>
      {/* 当前模型信息 */}
      {currentModel && (
        <Card style={{ marginBottom: '16px' }}>
          <Space align="start">
            <div style={{ fontSize: '32px' }}>
              {getProviderIcon(currentModel.provider)}
            </div>
            <div>
              <Title level={5} style={{ margin: 0 }}>
                当前使用模型
              </Title>
              <Text type="secondary">
                {currentModel.provider} - {currentModel.model}
              </Text>
              <div style={{ marginTop: '8px' }}>
                <Tag color={currentModel.provider_info?.api_key_configured ? 'success' : 'warning'}>
                  {currentModel.provider_info?.api_key_configured ? '已配置' : '未配置API Key'}
                </Tag>
              </div>
            </div>
          </Space>
        </Card>
      )}

      {/* 可用模型列表 */}
      <Title level={5} style={{ marginBottom: '16px' }}>
        可用模型
      </Title>
      
      <List
        grid={{ gutter: 16, xs: 1, sm: 2, md: 2, lg: 3 }}
        dataSource={availableModels}
        renderItem={(model) => (
          <List.Item>
            <Card
              hoverable
              style={{
                border: currentModel?.provider === model.provider ? '2px solid #0f766e' : undefined,
              }}
            >
              <Space direction="vertical" style={{ width: '100%' }}>
                <Space>
                  <span style={{ fontSize: '24px' }}>
                    {getProviderIcon(model.provider)}
                  </span>
                  <div>
                    <Text strong>{model.provider}</Text>
                    <br />
                    <Text type="secondary" style={{ fontSize: '12px' }}>
                      {model.model}
                    </Text>
                  </div>
                </Space>
                
                <Text type="secondary" style={{ fontSize: '12px' }}>
                  {getProviderDescription(model.provider)}
                </Text>
                
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  {getStatusTag(model)}
                  
                  <Button
                    type={currentModel?.provider === model.provider ? 'primary' : 'default'}
                    size="small"
                    icon={currentModel?.provider === model.provider ? <CheckCircleOutlined /> : <SyncOutlined />}
                    loading={switching === model.provider}
                    onClick={() => handleSwitchModel(model.provider)}
                    disabled={!model.available || currentModel?.provider === model.provider}
                  >
                    {currentModel?.provider === model.provider ? '当前使用' : '切换'}
                  </Button>
                </div>
              </Space>
            </Card>
          </List.Item>
        )}
      />
    </div>
  );
};

export default ModelSelector;