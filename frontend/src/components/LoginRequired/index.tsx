import React from 'react';
import { Card, Typography, Space, Alert } from 'antd';
import { LoginOutlined } from '@ant-design/icons';
import UserAuth from '../UserAuth';

const { Title, Paragraph, Text } = Typography;

/**
 * 受保护页面的统一入口（2026-10-07）
 *
 * 未登录 / 登录已过期时**只渲染这一个登录卡片**：
 * 页面组件根本不挂载，因此不会发起课程、任务、作业、掌握度等个人接口请求，
 * 也就不会出现"登录提示 + 加载失败 + 暂无数据"同时刷屏的情况。
 * 登录成功后认证状态变为已登录，本卡片自动卸载，原地址的页面随即正常渲染。
 */
const LoginRequired: React.FC<{ reason?: 'anonymous' | 'expired' }> = ({ reason = 'anonymous' }) => (
  <div style={{ maxWidth: 560, margin: '64px auto', padding: 16 }}>
    <Card>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Title level={4} style={{ margin: 0 }}>
          <LoginOutlined style={{ color: 'var(--color-primary)', marginRight: 8 }} />
          请先登录
        </Title>
        {reason === 'expired' ? (
          <Alert
            type="warning"
            showIcon
            message="登录状态已失效"
            description="为保护你的学习记录，已停止加载当前页面的数据。重新登录后会回到这个页面。"
          />
        ) : (
          <Paragraph type="secondary" style={{ margin: 0 }}>
            课程、作业、练习与学习记录都归属到具体账号。登录后即可继续当前页面。
          </Paragraph>
        )}
        <UserAuth block />
        <Text type="secondary" style={{ fontSize: 12 }}>
          登录成功后会自动回到你刚才要打开的页面。
        </Text>
      </Space>
    </Card>
  </div>
);

export default LoginRequired;
