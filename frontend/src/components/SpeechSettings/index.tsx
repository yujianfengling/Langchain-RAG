import React, { useEffect, useState } from 'react';
import { Card, Space, Switch, Select, Tag, Typography, Button, Alert, message } from 'antd';
import { AudioOutlined, SoundOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { authHeaders } from '../../api/axios';
import { fetchVoiceInfo, ttsPlayer, VoiceInfo } from '../../utils/ttsPlayer';

const { Text, Paragraph } = Typography;

/**
 * 语音设置（2026-09-26）
 *
 * 提供：
 * - 语音输入开关（录音 → 本地转写 → 填入输入框，由学生确认后发送）
 * - 自动朗读开关（回答流式生成时，形成完整句子即开始朗读）
 * - 音色选择（本地 CosyVoice 预置音色）
 * - 语音服务状态（本地 ASR / 本地 TTS 分别显示，不可用时给出原因）
 */

interface SpeechStatus {
  local?: {
    running?: boolean;
    reason?: string;
    asr?: { available?: boolean; engine?: string; model?: string; error?: string };
    tts?: { available?: boolean; engine?: string; error?: string };
    vram?: { allocated_mb?: number; reserved_mb?: number };
  };
  capability?: { backend?: string; available?: boolean; reason?: string };
}

const SpeechSettings: React.FC = () => {
  const [voiceInput, setVoiceInput] = useState<boolean>(
    () => localStorage.getItem('voice_input_enabled') !== '0');
  const [autoRead, setAutoRead] = useState<boolean>(
    () => localStorage.getItem('auto_voice_reply') === '1');
  const [voice, setVoice] = useState<string>(() => localStorage.getItem('tts_voice') || '');
  const [voiceInfo, setVoiceInfo] = useState<VoiceInfo | null>(null);
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
    const info = await fetchVoiceInfo();
    setVoiceInfo(info);
    if (!voice && info.default) {
      setVoice(info.default);
      localStorage.setItem('tts_voice', info.default);
      ttsPlayer.setVoice(info.default);
    }
  };

  useEffect(() => {
    loadStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    localStorage.setItem('voice_input_enabled', voiceInput ? '1' : '0');
  }, [voiceInput]);

  useEffect(() => {
    localStorage.setItem('auto_voice_reply', autoRead ? '1' : '0');
  }, [autoRead]);

  const onVoiceChange = (value: string) => {
    setVoice(value);
    localStorage.setItem('tts_voice', value);
    ttsPlayer.setVoice(value);
    message.success(`已选择音色：${value}`);
  };

  const warmup = async () => {
    message.loading({ content: '正在预热本地语音模型…', key: 'warmup', duration: 0 });
    try {
      const resp = await fetch('/api/audio/warmup', { method: 'POST', headers: authHeaders() });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body?.message || '预热失败');
      message.success({ content: '本地语音模型已就绪', key: 'warmup' });
    } catch (e: any) {
      message.error({ content: e?.message || '预热失败', key: 'warmup' });
    }
    loadStatus();
  };

  const local = status?.local;
  const asrOk = !!local?.asr?.available;
  const ttsOk = !!local?.tts?.available;

  return (
    <Card size="small" title={<span><SoundOutlined /> 语音（本地 ASR / TTS）</span>}>
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

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space>
            <SoundOutlined />
            <Text>自动朗读回答</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              （回答边生成边朗读，形成完整句子就开始；可随时暂停/停止）
            </Text>
          </Space>
          <Switch checked={autoRead} onChange={setAutoRead} />
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space>
            <Text>朗读音色</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              （本地 CosyVoice 预置音色，可安全分发）
            </Text>
          </Space>
          <Select
            size="small"
            style={{ width: 160 }}
            value={voice || voiceInfo?.default || undefined}
            onChange={onVoiceChange}
            placeholder={voiceInfo?.available ? '选择音色' : '音色不可用'}
            disabled={!voiceInfo?.available}
            options={(voiceInfo?.voices || []).map((v) => ({ value: v, label: v }))}
          />
        </div>

        <Space wrap>
          <Tag color={asrOk ? 'green' : 'red'}>
            本地语音识别：{asrOk ? `可用（${local?.asr?.engine || 'local'}）` : '不可用'}
          </Tag>
          <Tag color={ttsOk ? 'green' : 'red'}>
            本地语音合成：{ttsOk ? `可用（${local?.tts?.engine || 'local'}）` : '不可用'}
          </Tag>
          {local?.vram?.allocated_mb != null && (
            <Tag color="blue">显存占用 {Math.round(local.vram.allocated_mb)} MB</Tag>
          )}
          <Button size="small" icon={<ThunderboltOutlined />} loading={loadingStatus}
            onClick={warmup}>
            预热语音模型
          </Button>
          <Button size="small" onClick={loadStatus} loading={loadingStatus}>刷新状态</Button>
        </Space>

        {(!asrOk || !ttsOk) && (
          <Alert
            type="warning"
            showIcon
            message="本地语音服务未就绪"
            description={
              <span>
                {local?.reason || local?.asr?.error || local?.tts?.error || '本地语音服务未启动'}
                <br />
                文字问答、学习计划、自测判分等功能不受影响；语音模型缺失时可按
                <Text code>docs/LOCAL_SPEECH.md</Text> 的步骤安装并启动本地语音服务。
              </span>
            }
          />
        )}

        <Paragraph type="secondary" style={{ fontSize: 12, margin: 0 }}>
          文字回答始终正常流式显示；朗读内容会自动去掉 Markdown 与代码，复杂公式提示"请看屏幕"。
        </Paragraph>
      </Space>
    </Card>
  );
};

export default SpeechSettings;
