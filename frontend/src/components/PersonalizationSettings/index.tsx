import React, { useState, useEffect } from 'react';
import { Card, Form, Radio, Slider, Switch, Button, message, Space, Typography, Divider } from 'antd';
import {
  SettingOutlined,
  SaveOutlined,
  EyeOutlined,
  AudioOutlined,
  ReadOutlined,
  ToolOutlined,

} from '@ant-design/icons';
import { updateLearningStyle, setLearningPreference, getUserProfile } from '../../api/agent';

const { Text } = Typography;

interface PersonalizationSettingsProps {
  userId?: string;
  subjectId?: string;
  onSettingsChange?: () => void;
}

const PersonalizationSettings: React.FC<PersonalizationSettingsProps> = ({
  userId = 'default_user',
  onSettingsChange,
}) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const loadSettings = async () => {
    setLoading(true);
    try {
      const res = await getUserProfile(userId);
      const profile = res?.data;
      if (profile) {
        form.setFieldsValue({
          learningStyle: profile.learning_style || 'balanced',
          detailLevel: profile.preferences?.detail_level || 50,
          autoVoice: profile.preferences?.auto_voice === 'true',
        });
      }
    } catch (error) {
      console.error('加载设置失败:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSettings();
  }, [userId]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const values = form.getFieldsValue();

      // 更新学习风格
      await updateLearningStyle(userId, values.learningStyle);

      // 更新其他偏好
      await setLearningPreference(userId, 'detail_level', values.detailLevel.toString());
      await setLearningPreference(userId, 'auto_voice', values.autoVoice.toString());

      message.success('设置已保存');
      onSettingsChange?.();
    } catch (error) {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const learningStyleOptions = [
    {
      value: 'visual',
      label: (
        <Space>
          <EyeOutlined />
          <span>视觉型</span>
        </Space>
      ),
      desc: '多使用图表、思维导图、流程图',
    },
    {
      value: 'auditory',
      label: (
        <Space>
          <AudioOutlined />
          <span>听觉型</span>
        </Space>
      ),
      desc: '多用口语化表达，适合朗读理解',
    },
    {
      value: 'reading',
      label: (
        <Space>
          <ReadOutlined />
          <span>阅读型</span>
        </Space>
      ),
      desc: '提供结构化的文字说明',
    },
    {
      value: 'kinesthetic',
      label: (
        <Space>
          <ToolOutlined />
          <span>动手型</span>
        </Space>
      ),
      desc: '多提供实践练习和动手操作建议',
    },
    {
      value: 'balanced',
      label: (
        <Space>
          <SettingOutlined />
          <span>均衡型</span>
        </Space>
      ),
      desc: '综合各种方式，适合大多数学习者',
    },
  ];

  return (
    <Card
      title={
        <Space>
          <SettingOutlined />
          <span>个性化设置</span>
        </Space>
      }
      loading={loading}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          learningStyle: 'balanced',
          detailLevel: 50,
          autoVoice: false,
        }}
      >
        {/* 学习风格 */}
        <Form.Item
          label={<Text strong>学习风格</Text>}
          name="learningStyle"
          extra="选择您偏好的学习方式，AI将根据此调整回答风格"
        >
          <Radio.Group>
            <Space direction="vertical" style={{ width: '100%' }}>
              {learningStyleOptions.map((option) => (
                <Radio key={option.value} value={option.value} style={{ width: '100%' }}>
                  <div>
                    <div>{option.label}</div>
                    <Text type="secondary" style={{ fontSize: 12 }}>{option.desc}</Text>
                  </div>
                </Radio>
              ))}
            </Space>
          </Radio.Group>
        </Form.Item>

        <Divider />

        {/* 回答详细程度 */}
        <Form.Item
          label={<Text strong>回答详细程度</Text>}
          name="detailLevel"
          extra="调整AI回答的详细程度"
        >
          <Slider
            min={0}
            max={100}
            marks={{
              0: '简洁',
              50: '适中',
              100: '详细',
            }}
            tooltip={{ formatter: (value) => `${value}%` }}
          />
        </Form.Item>

        <Divider />

        {/* 自动语音回复 */}
        <Form.Item
          label={<Text strong>自动语音回复</Text>}
          name="autoVoice"
          valuePropName="checked"
          extra="开启后，AI回答将自动朗读"
        >
          <Switch checkedChildren="开" unCheckedChildren="关" />
        </Form.Item>

        <Divider />

        {/* 保存按钮 */}
        <Form.Item>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            onClick={handleSave}
            loading={saving}
            block
          >
            保存设置
          </Button>
        </Form.Item>
      </Form>
    </Card>
  );
};

export default PersonalizationSettings;
