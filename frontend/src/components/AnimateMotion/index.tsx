/**
 * 动画工具组件
 * 
 * 教育系统风格：专业、柔和、不花哨
 * 使用方式：用包装器包裹现有组件，不改变任何内部逻辑
 */

import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';

// ============ 弹簧物理配置 ============
const spring = { type: 'spring' as const, stiffness: 100, damping: 20 };

// ============ 页面切换 ============
export const AnimatedPage: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className }) => (
  <motion.div
    className={className}
    initial={{ opacity: 0, y: 8 }}
    animate={{ opacity: 1, y: 0 }}
    exit={{ opacity: 0, y: -6 }}
    transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
  >
    {children}
  </motion.div>
);

// ============ 聊天气泡弹入（教育风格：柔和、温暖） ============
export const MessageBubble: React.FC<{ children: React.ReactNode; delay?: number }> = ({ children, delay = 0 }) => (
  <motion.div
    initial={{ opacity: 0, y: 16, scale: 0.97 }}
    animate={{ opacity: 1, y: 0, scale: 1 }}
    transition={{ 
      type: 'spring', 
      stiffness: 120, 
      damping: 18, 
      delay,
    }}
  >
    {children}
  </motion.div>
);

// ============ 列表入场级联（知识库、学习计划） ============
export const StaggerList: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className }) => (
  <motion.div
    className={className}
    initial="hidden"
    animate="visible"
    variants={{
      hidden: {},
      visible: { transition: { staggerChildren: 0.05 } },
    }}
  >
    {children}
  </motion.div>
);

export const StaggerItem: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className }) => (
  <motion.div
    className={className}
    variants={{
      hidden: { opacity: 0, y: 12 },
      visible: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.16, 1, 0.3, 1] } },
    }}
  >
    {children}
  </motion.div>
);

// ============ 骨架屏加载（专业感） ============
export const SkeletonCard: React.FC<{ width?: string; height?: number }> = ({ width = '100%', height = 80 }) => (
  <motion.div
    style={{
      width,
      height,
      borderRadius: 10,
      background: 'linear-gradient(90deg, #f1f5f9 25%, #e2e8f0 50%, #f1f5f9 75%)',
      backgroundSize: '200% 100%',
    }}
    animate={{ backgroundPosition: ['200% 0', '-200% 0'] }}
    transition={{ duration: 1.5, repeat: Infinity, ease: 'linear' }}
  />
);

// ============ 按钮磁力效果（精致高级感） ============
export const MagneticButton: React.FC<{
  children: React.ReactNode;
  onClick?: () => void;
  className?: string;
  style?: React.CSSProperties;
  disabled?: boolean;
}> = ({ children, onClick, className, style, disabled }) => {
  const ref = React.useRef<HTMLButtonElement>(null);

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!ref.current || disabled) return;
    const rect = ref.current.getBoundingClientRect();
    const x = e.clientX - rect.left - rect.width / 2;
    const y = e.clientY - rect.top - rect.height / 2;
    ref.current.style.transform = `translate(${x * 0.15}px, ${y * 0.15}px)`;
  };

  const handleMouseLeave = () => {
    if (!ref.current) return;
    ref.current.style.transform = 'translate(0, 0)';
  };

  return (
    <motion.button
      ref={ref}
      className={className}
      style={{ ...style, cursor: disabled ? 'default' : 'pointer', border: 'none', background: 'none', transition: 'transform 0.2s cubic-bezier(0.16, 1, 0.3, 1)' }}
      whileHover={disabled ? {} : { scale: 1.03 }}
      whileTap={disabled ? {} : { scale: 0.96 }}
      transition={spring}
      onClick={onClick}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      disabled={disabled}
    >
      {children}
    </motion.button>
  );
};

// ============ 卡片悬浮（柔和高级感） ============
export const HoverCard: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className }) => (
  <motion.div
    className={className}
    whileHover={{ y: -3, boxShadow: '0 8px 24px rgba(0,0,0,0.08)' }}
    transition={spring}
    style={{ borderRadius: 12 }}
  >
    {children}
  </motion.div>
);

// ============ 数字跳动（学习进度、统计） ============
export const AnimatedNumber: React.FC<{ value: number }> = ({ value }) => (
  <motion.span
    key={value}
    initial={{ opacity: 0, y: 8 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ type: 'spring', stiffness: 120, damping: 15 }}
  >
    {value}
  </motion.span>
);

// ============ AnimatePresence 包装器 ============
export const AnimateWrapper = AnimatePresence;
