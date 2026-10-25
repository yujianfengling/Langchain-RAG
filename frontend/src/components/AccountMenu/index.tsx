import React from 'react';
import { Avatar, Button, Dropdown, Space, Typography } from 'antd';
import { UserOutlined, SettingOutlined, BulbOutlined, LogoutOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import UserAuth from '../UserAuth';

const { Text } = Typography;

/**
 * 账号菜单：设置 / 讲解偏好 / 退出登录收进头像菜单。
 * 登录态来自 AuthProvider（唯一来源），本组件不再自行请求 /auth/me，
 * 避免侧栏、顶栏、抽屉多个实例各发一次请求并各自处理 401。
 */
const AccountMenu: React.FC<{ compact?: boolean; onOpenProfile?: () => void }> = ({
  compact, onOpenProfile,
}) => {
  const navigate = useNavigate();
  const { status, user, logout } = useAuth();

  if (status !== 'authenticated' || !user) {
    // 未登录 / 校验中：只给登录入口；校验中不闪烁登录按钮
    if (status === 'initializing') {
      return (
        <Button type="text" block style={{ height: 'auto', padding: '6px 8px' }} loading>
          正在恢复登录状态…
        </Button>
      );
    }
    return <UserAuth />;
  }

  const roleLabel = user.role === 'teacher' ? '教师' : user.role === 'admin' ? '管理员' : '学生';
  const items = [
    { key: 'who', label: (user.display_name || user.username) + ' · ' + roleLabel, disabled: true },
    { type: 'divider' as const },
    { key: 'profile', icon: <UserOutlined />, label: '我的主页' },
    { key: 'settings', icon: <SettingOutlined />, label: '设置与运行状态' },
    { key: 'preference', icon: <BulbOutlined />, label: '讲解偏好' },
    { type: 'divider' as const },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
  ];

  const onClick = async ({ key }: { key: string }) => {
    if (key === 'profile') { if (onOpenProfile) onOpenProfile(); else navigate('/me'); }
    else if (key === 'settings') navigate('/settings');
    else if (key === 'preference') navigate('/settings?tab=preference');
    else if (key === 'logout') {
      await logout();
      navigate('/student');
    }
  };

  return (
    <Dropdown menu={{ items, onClick }} trigger={['click']} placement="topRight">
      <Button type="text" block style={{ height: 'auto', padding: '6px 8px', textAlign: 'left' }}>
        <Space size={8} style={{ width: '100%' }}>
          <Avatar size={28} icon={<UserOutlined />} style={{ backgroundColor: 'var(--color-primary)', flexShrink: 0 }} />
          <Text style={{ maxWidth: compact ? 96 : 104, fontSize: 13 }} ellipsis>
            {user.display_name || user.username}
          </Text>
        </Space>
      </Button>
    </Dropdown>
  );
};

export default AccountMenu;
