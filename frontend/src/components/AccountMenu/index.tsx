import React, { useCallback, useEffect, useState } from 'react';
import { Avatar, Button, Dropdown, Space, Typography, message } from 'antd';
import { UserOutlined, SettingOutlined, BulbOutlined, LogoutOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { AuthUser, isLoggedIn, me, logout, clearToken } from '../../api/auth';
import UserAuth, { AUTH_CHANGED_EVENT, notifyAuthChanged } from '../UserAuth';

const { Text } = Typography;

/**
 * 账号菜单：设置 / 讲解偏好 / 退出登录收进头像菜单（2026-10-06 改版）。
 * 未登录时仍复用既有 UserAuth（登录/注册仍由后端权限判定）。
 */
const AccountMenu: React.FC<{ compact?: boolean }> = ({ compact }) => {
  const navigate = useNavigate();
  const [user, setUser] = useState<AuthUser | null>(null);

  const refresh = useCallback(async () => {
    if (!isLoggedIn()) { setUser(null); return; }
    try { const res: any = await me(); setUser(res && res.data ? res.data : null); }
    catch (e) { setUser(null); }
  }, []);

  useEffect(() => {
    refresh();
    const onChange = () => refresh();
    window.addEventListener(AUTH_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, onChange);
  }, [refresh]);

  if (!isLoggedIn() || !user) return <UserAuth />;

  const roleLabel = user.role === 'teacher' ? '教师' : user.role === 'admin' ? '管理员' : '学生';
  const items = [
    { key: 'who', label: (user.display_name || user.username) + ' · ' + roleLabel, disabled: true },
    { type: 'divider' as const },
    { key: 'settings', icon: <SettingOutlined />, label: '设置与运行状态' },
    { key: 'preference', icon: <BulbOutlined />, label: '讲解偏好' },
    { type: 'divider' as const },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
  ];

  const onClick = async ({ key }: { key: string }) => {
    if (key === 'settings') navigate('/settings');
    else if (key === 'preference') navigate('/settings?tab=preference');
    else if (key === 'logout') {
      try { await logout(); } catch (e) { /* 后端不可达也允许本地退出 */ }
      clearToken();
      notifyAuthChanged();
      message.success('已退出登录');
      navigate('/student');
    }
  };

  return (
    <Dropdown menu={{ items, onClick }} trigger={['click']} placement="topRight">
      <Button type="text" block style={{ height: 'auto', padding: '6px 8px', textAlign: 'left' }}>
        <Space size={8} style={{ width: '100%' }}>
          <Avatar size={28} icon={<UserOutlined />} style={{ backgroundColor: '#0f766e', flexShrink: 0 }} />
          <Text style={{ maxWidth: compact ? 96 : 104, fontSize: 13 }} ellipsis>
            {user.display_name || user.username}
          </Text>
        </Space>
      </Button>
    </Dropdown>
  );
};

export default AccountMenu;
