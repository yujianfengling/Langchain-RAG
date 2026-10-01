import React, { useCallback, useEffect, useState } from 'react';
import {
  Avatar, Button, Form, Input, Modal, Space, Tag, Typography, message, Alert,
} from 'antd';
import { UserOutlined, LogoutOutlined, LoginOutlined } from '@ant-design/icons';
import {
  AuthUser, login, register, logout, me, setToken, clearToken, isLoggedIn,
} from '../../api/auth';
import { AUTH_EXPIRED_EVENT } from '../../api/axios';

const { Text } = Typography;

/** 登录态变化广播事件（其他组件据此刷新登录相关 UI） */
export const AUTH_CHANGED_EVENT = 'edu:auth-changed';

export const notifyAuthChanged = () => {
  try {
    window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
  } catch {
    /* 忽略 */
  }
};

/**
 * 用户登录入口（阶段一）
 *
 * 学习数据（作答、错题、复习、计划）必须归属到真实用户，
 * 因此自测提交等写操作要求先登录；未登录时后端按历史账号处理只读兼容。
 */
const UserAuth: React.FC = () => {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form] = Form.useForm();

  const refresh = useCallback(async () => {
    if (!isLoggedIn()) {
      setUser(null);
      return;
    }
    try {
      const res: any = await me();
      setUser(res?.data || null);
    } catch {
      // 令牌失效：拦截器已清理，这里只同步 UI
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refresh();
    const onExpired = () => {
      setUser(null);
      message.warning('登录状态已失效，请重新登录');
      notifyAuthChanged();
    };
    const onChanged = () => refresh();
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    window.addEventListener(AUTH_CHANGED_EVENT, onChanged);
    return () => {
      window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
      window.removeEventListener(AUTH_CHANGED_EVENT, onChanged);
    };
  }, [refresh]);

  const handleSubmit = async () => {
    let values: any;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res: any = mode === 'login'
        ? await login(values.username.trim(), values.password)
        : await register(values.username.trim(), values.password, values.display_name?.trim());
      const data = res?.data;
      if (data?.token) {
        setToken(data.token);
        const u: AuthUser = {
          id: data.id, username: data.username, display_name: data.display_name,
          is_legacy: data.is_legacy,
        };
        setUser(u);
        setOpen(false);
        form.resetFields();
        notifyAuthChanged();
        message.success(mode === 'login' ? '登录成功' : '注册成功');
      } else {
        setError(res?.message || '操作失败，请稍后重试');
      }
    } catch (e: any) {
      setError(e?.response?.data?.message || '操作失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  const handleLogout = async () => {
    try {
      await logout();
    } catch {
      /* 即使后端撤销失败，本地也退出，避免卡在不确定状态 */
    }
    clearToken();
    setUser(null);
    notifyAuthChanged();
    message.success('已退出登录');
  };

  if (!user) {
    return (
      <>
        <Button
          block
          size="small"
          icon={<LoginOutlined />}
          onClick={() => { setMode('login'); setError(null); setOpen(true); }}
        >
          登录 / 注册
        </Button>
        <Modal
          title={mode === 'login' ? '登录' : '注册'}
          open={open}
          onCancel={() => setOpen(false)}
          onOk={handleSubmit}
          confirmLoading={submitting}
          okText={mode === 'login' ? '登录' : '注册'}
          cancelText="取消"
          destroyOnHidden
        >
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="登录后作答、错题、复习与计划会归属到你的账号"
          />
          {error && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error} />}
          <Form form={form} layout="vertical" requiredMark={false}>
            <Form.Item
              name="username"
              label="用户名"
              rules={[{ required: true, message: '请输入用户名' }]}
            >
              <Input autoComplete="username" placeholder="3-32 位字母/数字/下划线/中文" />
            </Form.Item>
            {mode === 'register' && (
              <Form.Item name="display_name" label="昵称（可选）">
                <Input placeholder="显示名称" />
              </Form.Item>
            )}
            <Form.Item
              name="password"
              label="口令"
              rules={[
                { required: true, message: '请输入口令' },
                { min: 6, message: '口令至少 6 位' },
              ]}
            >
              <Input.Password autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
            </Form.Item>
          </Form>
          <Button type="link" size="small" style={{ paddingLeft: 0 }}
                  onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(null); }}>
            {mode === 'login' ? '还没有账号？去注册' : '已有账号？去登录'}
          </Button>
        </Modal>
      </>
    );
  }

  return (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      <Space size={8} style={{ width: '100%' }}>
        <Avatar size={26} icon={<UserOutlined />} style={{ backgroundColor: '#0f766e' }} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <Text strong style={{ fontSize: 13, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {user.display_name || user.username}
          </Text>
          {user.is_legacy && <Tag color="orange" style={{ fontSize: 10, lineHeight: '16px' }}>历史账号</Tag>}
        </div>
      </Space>
      <Button size="small" block icon={<LogoutOutlined />} onClick={handleLogout}>
        退出登录
      </Button>
    </Space>
  );
};

export default UserAuth;
