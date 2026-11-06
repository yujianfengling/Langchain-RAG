import React, { useEffect, useState } from 'react';
import { Avatar } from 'antd';
import { UserOutlined } from '@ant-design/icons';
import type { AuthUser } from '../../api/authTypes';
import { fetchAvatarBlob } from '../../api/auth';

/**
 * 账号头像。有上传图时显示图片，否则显示姓名首字。
 * 图片走带登录令牌的请求，不能直接写进 img src。
 */
const UserAvatar: React.FC<{
  user?: AuthUser | null;
  size?: number;
  onClick?: () => void;
}> = ({ user, size = 28, onClick }) => {
  const [src, setSrc] = useState('');
  const letter = (user?.display_name || user?.username || '').trim().charAt(0);

  useEffect(() => {
    if (!user?.has_avatar) {
      setSrc('');
      return;
    }
    let cancelled = false;
    let objectUrl = '';
    fetchAvatarBlob()
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setSrc('');
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [user?.id, user?.has_avatar, user?.avatar_updated_at]);

  const avatar = (
    <Avatar
      size={size}
      src={src || undefined}
      icon={!src && !letter ? <UserOutlined /> : undefined}
      style={{ backgroundColor: 'var(--color-primary)', flexShrink: 0, fontSize: Math.round(size * 0.4) }}
    >
      {!src && letter ? letter : null}
    </Avatar>
  );

  if (!onClick) return avatar;
  return (
    <button type="button" className="user-avatar-button" onClick={onClick} aria-label="更换头像">
      {avatar}
    </button>
  );
};

export default UserAvatar;
