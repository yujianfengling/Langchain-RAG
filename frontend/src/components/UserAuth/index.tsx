import React, { useState } from 'react';
import {
  Button, Form, Input, Modal, Alert,
} from 'antd';
import { LoginOutlined } from '@ant-design/icons';
import { useAuth, notifyAuthChanged } from '../../context/AuthContext';

/**
 * 登录入口（2026-10-07 收敛）
 *
 * 这里只负责"打开登录窗口并提交"，不再自己监听失效事件、不再自己 show toast：
 * 失效提示与状态清理由 AuthProvider（全站唯一实例）统一处理，
 * 否则侧栏 / 顶栏 / 抽屉里的多个实例会把同一次失效提示刷成十几条。
 */
const UserAuth: React.FC<{ block?: boolean }> = ({ block }) => {
  const { login, register } = useAuth();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form] = Form.useForm();

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
      if (mode === 'login') {
        await login(values.username.trim(), values.password);
      } else {
        await register(values.username.trim(), values.password, values.display_name?.trim());
      }
      setOpen(false);
      form.resetFields();
      notifyAuthChanged();
    } catch (e: any) {
      const detail = e?.response?.data?.message || e?.response?.data?.detail;
      setError(detail || e?.message || '操作失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Button
        block={block !== false}
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
          message={mode === 'login'
            ? '用学号（学生）或工号（教师）登录'
            : '注册后即可作答、看错题与复习安排'}
        />
        {error && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error} />}
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item
            name="username"
            label="账号"
            extra="学生填学号，教师填工号；也可以用已有的英文账号"
            rules={[{ required: true, message: '请输入账号（学号 / 工号）' }]}
          >
            <Input autoComplete="username" placeholder="例如 20250101 或 demo_student" />
          </Form.Item>
          {mode === 'register' && (
            <Form.Item name="display_name" label="姓名（可选）">
              <Input placeholder="显示名称，例如 张三" />
            </Form.Item>
          )}
          <Form.Item
            name="password"
            label="密码"
            rules={[
              { required: true, message: '请输入密码' },
              { min: 6, message: '密码至少 6 位' },
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
};

// 兼容旧引用：事件常量与广播函数转发到 AuthContext
export { AUTH_CHANGED_EVENT, notifyAuthChanged } from '../../context/AuthContext';

export default UserAuth;
