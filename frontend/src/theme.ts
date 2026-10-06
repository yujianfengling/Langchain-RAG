import type { ThemeConfig } from 'antd';

/**
 * 全系统设计规范（2026-10-07 视觉整改）
 * 颜色 / 字号 / 间距 / 圆角 / 阴影统一在这里，页面不要各写一套。
 */
export const themeConfig: ThemeConfig = {
  token: {
    colorPrimary: '#0f766e', colorPrimaryBg: '#f0fdfa', colorLink: '#0f766e', colorLinkHover: '#0d9488',
    colorSuccess: '#059669', colorWarning: '#d97706', colorError: '#dc2626',
    colorBgContainer: '#ffffff', colorBgLayout: '#f8fafc', colorBgElevated: '#ffffff',
    colorText: '#0f172a', colorTextSecondary: '#475569', colorTextTertiary: '#94a3b8',
    colorBorder: '#e2e8f0', colorBorderSecondary: '#f1f5f9',
    borderRadius: 10, borderRadiusLG: 14,
    fontFamily: "'Geist', 'Satoshi', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif",
    fontSize: 15, controlHeight: 38,
  },
  components: {
    Menu: {
      itemColor: '#0f172a', itemHoverColor: '#0f766e', itemHoverBg: '#f0fdfa',
      itemSelectedColor: '#0f766e', itemSelectedBg: '#ccfbf1', itemActiveBg: '#ccfbf1',
      horizontalItemSelectedColor: '#0f766e', itemHeight: 44,
    },
    Card: { borderRadiusLG: 14 },
    Button: { borderRadius: 8, controlHeight: 38 },
    Input: { borderRadius: 8, controlHeight: 40 },
    Select: { borderRadius: 8, controlHeight: 38 },
    Modal: { borderRadiusLG: 16 },
    Table: { borderRadius: 12, headerBg: '#f8fafc', rowHoverBg: '#f0fdfa' },
    Tag: { borderRadiusSM: 6 },
    Tabs: { inkBarColor: '#0f766e', itemActiveColor: '#0f766e', itemSelectedColor: '#0f766e', itemHoverColor: '#0d9488' },
  },
};

export default themeConfig;
