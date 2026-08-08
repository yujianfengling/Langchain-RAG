/**
 * 动画工具组件
 * 
 * 使用方式：用这些包装器包裹现有组件，不改变任何内部逻辑。
 * 例：<FadeIn><YourComponent /></FadeIn>
 */

import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';

// ============ 弹簧物理配置（taste-skill: Spring Physics） ============
const spring = {
  type: 'spring' as const,
  stiffness: 100,
  damping: 20,
};

// ============ 预设动画变体 ============

/** 淡入（从下方滑入） */
export const fadeInUp = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -8 },
  transition: { duration: 0.3, ease: [0.16, 1, 0.3, 1] },
};

/** 淡入（纯透明度） */
export const fadeIn = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.25 },
};

/** 缩放弹入 */
export const scaleIn = {
  initial: { opacity: 0, scale: 0.95 },
  animate: { opacity: 1, scale: 1 },
  exit: { opacity: 0, scale: 0.95 },
  transition: spring,
};

// ============ 包装器组件 ============

/** 页面切换动画（包裹页面级组件） */
export const AnimatedPage: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className }) => (
  <motion.div
    className={className}
    initial={{ opacity: 0, y: 8 }}
    animate={{ opacity: 1, y: 0 }}
    exit={{ opacity: 0, y: -8 }}
    transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
  >
    {children}
  </motion.div>
);

/** 卡片悬浮效果（包裹 Card 组件） */
export const HoverCard: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className }) => (
  <motion.div
    className={className}
    whileHover={{ y: -2, boxShadow: '0 4px 16px rgba(0,0,0,0.08)' }}
    whileTap={{ scale: 0.995 }}
    transition={spring}
    style={{ borderRadius: 12 }}
  >
    {children}
  </motion.div>
);

/** 按钮磁力效果（taste-skill: Magnetic Button） */
export const MagneticButton: React.FC<{ 
  children: React.ReactNode; 
  onClick?: () => void;
  className?: string;
  style?: React.CSSProperties;
}> = ({ children, onClick, className, style }) => (
  <motion.button
    className={className}
    style={{ ...style, cursor: 'pointer', border: 'none', background: 'none' }}
    whileHover={{ scale: 1.02 }}
    whileTap={{ scale: 0.97 }}
    transition={spring}
    onClick={onClick}
  >
    {children}
  </motion.button>
);

/** 列表 stagger 入场（taste-skill: Staggered Orchestration） */
export const StaggerList: React.FC<{ 
  children: React.ReactNode;
  className?: string;
}> = ({ children, className }) => (
  <motion.div
    className={className}
    initial="hidden"
    animate="visible"
    variants={{
      hidden: {},
      visible: {
        transition: {
          staggerChildren: 0.06,
        },
      },
    }}
  >
    {children}
  </motion.div>
);

/** StaggerList 的子项 */
export const StaggerItem: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className }) => (
  <motion.div
    className={className}
    variants={{
      hidden: { opacity: 0, y: 10 },
      visible: { opacity: 1, y: 0, transition: { duration: 0.3, ease: [0.16, 1, 0.3, 1] } },
    }}
  >
    {children}
  </motion.div>
);

/** 数字计数动画 */
export const CountUp: React.FC<{ value: number }> = ({ value }) => (
  <motion.span
    initial={{ opacity: 0 }}
    animate={{ opacity: 1 }}
    transition={{ duration: 0.3 }}
  >
    {value}
  </motion.span>
);

/** 弹窗/抽屉动画 */
export const AnimateWrapper = AnimatePresence;
