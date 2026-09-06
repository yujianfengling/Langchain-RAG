import React, { useState } from 'react';
import { Typography } from 'antd';
import { RightOutlined, DownOutlined } from '@ant-design/icons';

const { Text } = Typography;

interface CollapsiblePanelProps {
  title: string;
  icon?: React.ReactNode;
  defaultOpen?: boolean;
  badge?: number | string;
  children: React.ReactNode;
  extra?: React.ReactNode;
}

const CollapsiblePanel: React.FC<CollapsiblePanelProps> = ({
  title,
  icon,
  defaultOpen = false,
  badge,
  children,
  extra,
}) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <div style={{
      marginBottom: 8,
      borderRadius: 8,
      overflow: 'hidden',
      border: '1px solid #f0f0f0',
      background: '#fff',
    }}>
      <div
        onClick={() => setIsOpen(!isOpen)}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 12px',
          cursor: 'pointer',
          background: isOpen ? '#fafafa' : '#fff',
          borderBottom: isOpen ? '1px solid #f0f0f0' : 'none',
          transition: 'all 0.2s',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {isOpen ? (
            <DownOutlined style={{ fontSize: 10, color: '#999' }} />
          ) : (
            <RightOutlined style={{ fontSize: 10, color: '#999' }} />
          )}
          {icon && <span style={{ fontSize: 14 }}>{icon}</span>}
          <Text strong style={{ fontSize: 13 }}>{title}</Text>
          {badge !== undefined && badge !== null && (
            <span style={{
              background: '#f0f0f0',
              borderRadius: 10,
              padding: '0 6px',
              fontSize: 11,
              color: '#666',
              lineHeight: '18px',
            }}>
              {badge}
            </span>
          )}
        </div>
        {extra && <div onClick={(e) => e.stopPropagation()}>{extra}</div>}
      </div>
      {isOpen && (
        <div style={{ padding: '12px' }}>
          {children}
        </div>
      )}
    </div>
  );
};

export default CollapsiblePanel;
