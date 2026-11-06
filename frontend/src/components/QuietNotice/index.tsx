import React from 'react';

/**
 * 一行提示，用来代替大块警告框。
 * 降级、会话回退这类信息还要能看见，但不该占掉半个页面。
 */
const QuietNotice: React.FC<{
  children: React.ReactNode;
  onClose?: () => void;
  inline?: boolean;
}> = ({ children, onClose, inline }) => (
  <div className={'quiet-notice' + (inline ? ' is-inline' : '')} role="status">
    <span>{children}</span>
    {onClose && (
      <button type="button" className="quiet-notice-close" onClick={onClose} aria-label="关闭">
        ×
      </button>
    )}
  </div>
);

export default QuietNotice;
