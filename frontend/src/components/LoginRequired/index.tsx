import React from 'react';
import { Alert, Typography } from 'antd';
import UserAuth from '../UserAuth';

const { Title, Text } = Typography;

/**
 * 受保护页面的统一入口（2026-10-07）
 *
 * 未登录 / 登录已过期时**只渲染这一个登录界面**：
 * 页面组件根本不挂载，因此不会发起课程、任务、作业、掌握度等个人接口请求，
 * 也就不会出现"登录提示 + 加载失败 + 暂无数据"同时刷屏的情况。
 * 登录成功后认证状态变为已登录，本界面自动卸载，原地址的页面随即正常渲染。
 *
 * 2026-10-31（ui-simplify）：不再套"请先登录"大卡片——直接展示登录表单本体。
 * 小品牌 + 标题 + 表单 + 低强调注册切换；失效提示只保留一条轻量 Alert。
 * 返回原页面的机制不变（notifyAuthChanged → 页面自动渲染）。
 */
const LoginRequired: React.FC<{ reason?: 'anonymous' | 'expired' }> = ({ reason = 'anonymous' }) => (
  <div style={{
    width: 380, maxWidth: 'calc(100vw - 32px)', margin: '10vh auto 0',
    padding: '0 4px',
  }}>
    <div style={{ marginBottom: 20 }}>
      <Text type="secondary" style={{ fontSize: 12, letterSpacing: 1 }}>知学 · RAG 智慧学习</Text>
      <Title level={3} style={{ margin: '6px 0 0' }}>登录知学</Title>
    </div>
    {reason === 'expired' && (
      <Alert
        type="warning" showIcon style={{ marginBottom: 16 }}
        message="登录状态已失效，请重新登录"
      />
    )}
    <UserAuth inline />
  </div>
);

export default LoginRequired;
