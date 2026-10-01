import React, { useEffect, useState } from 'react';
import { Card, Space, Switch, Tag, Typography, Button, Alert } from 'antd';
import { AudioOutlined, SoundOutlined } from '@ant-design/icons';
import { authHeaders } from '../../api/axios';

const { Text, Paragraph } = Typography;

/**
 * 语音设置（2026-10-01：只保留**语音输入**）
 *
 * 提供：
 * - 语音输入开关（录音 → 本地转写 → 填入输入框，由学生确认后发送）
 * - 本地语音识别状态（可用性 / 引擎 / 不可用原因）
 *
 * 注意：回答朗读（TTS）功能已整体移除——本页不再有自动朗读开关、音色选择与合成状态。
 */

interface SpeechStatus {
  local?: {
    running?: boolean;
    reason?: string;
    asr?: { available?: boolean; engine?: string; model?: string; error?: string };
  };
  capability?: { backend?: string; available?: boolean; reason?: string };
}

const SpeechSettings: React.FC = () => {
  const [voiceInput, setVoiceInput] = useState<boolean>(
    () => localStorage.getItem('voice_input_enabled') !== '0');
  const [status, setStatus] = useState<SpeechStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);

  const loadStatus = async () => {
    setLoadingStatus(true);
    try {
      const resp = await fetch('/api/audio/status', { headers: authHeaders() });
      const body = await resp.json();
      setStatus(body?.data || null);
    } catch {
      setStatus(null);
    } finally {
      setLoadingStatus(false);
    }
  };

  useEffect(() => {
    loadStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    localStorage.setItem('voice_input_enabled', voiceInput ? '1' : '0');
  }, [voiceInput]);

  const local = status?.local;
  const asrOk = !!local?.asr?.available;

  return (
    <Card size="small" title={<span><SoundOutlined /> 语音（语音输入）</span>}>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space>
            <AudioOutlined />
            <Text>语音输入</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              （录音后本地转写，文字先进入输入框，你确认后再发送）
            </Text>
          </Space>
          <Switch checked={voiceInput} onChange={setVoiceInput} />
        </div>

        <Space wrap>
          <Tag color={asrOk ? 'green' : 'red'}>
            本地语音识别：{asrOk ? `可用（${local?.asr?.engine || 'local'}）` : '不可用'}
          </Tag>
          <Button size="small" onClick={loadStatus} loading={loadingStatus}>刷新状态</Button>
        </Space>

        {!asrOk && (
          <Alert
            type="warning"
            showIcon
            message="本地语音识别未就绪"
            description={
              <span>
                {local?.reason || local?.asr?.error || '本地语音服务未启动'}
                <br />
                文字问答、学习计划、自测判分等功能不受影响；需要语音输入时可按
                <Text code>docs/LOCAL_SPEECH.md</Text> 的步骤安装并启动本地语音服务。
              </span>
            }
          />
        )}

        <Paragraph type="secondary" style={{ fontSize: 12, margin: 0 }}>
          文字回答始终正常流式显示；语音输入只用于把你说的话转成文本，不会朗读回答。
        </Paragraph>
      </Space>
    </Card>
  );
};

export default SpeechSettings;