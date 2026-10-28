import type { ThemeConfig } from 'antd';

/**
 * 全系统设计规范（2026-10-07 视觉整改）
 * 颜色 / 字号 / 间距 / 圆角 / 阴影统一在这里，页面不要各写一套。
 */
export const themeConfig: ThemeConfig = {
  token: {
    colorPrimary: '#4f46e5', colorPrimaryBg: '#eef2ff', colorLink: '#4f46e5', colorLinkHover: '#4338ca',
    colorSuccess: '#15803d', colorWarning: '#b45309', colorError: '#dc2626',
    colorBgContainer: '#ffffff', colorBgLayout: '#f5f6f8', colorBgElevated: '#ffffff',
    colorText: '#182033', colorTextSecondary: '#556074', colorTextTertiary: '#737d8f',
    colorBorder: '#e3e6eb', colorBorderSecondary: '#edf0f3',
    borderRadius: 10, borderRadiusLG: 14,
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
    fontSize: 14, controlHeight: 38,
    wireframe: false,
  },
  components: {
    Menu: {
      itemColor: '#556074', itemHoverColor: '#182033', itemHoverBg: '#f2f4f7',
      itemSelectedColor: '#4f46e5', itemSelectedBg: '#eef2ff', itemActiveBg: '#eef2ff',
      horizontalItemSelectedColor: '#4f46e5', itemHeight: 42,
    },
    Card: { borderRadiusLG: 14, headerBg: 'transparent' },
    Button: { borderRadius: 8, controlHeight: 38 },
    Input: { borderRadius: 8, controlHeight: 40 },
    Select: { borderRadius: 8, controlHeight: 38 },
    Modal: { borderRadiusLG: 14 },
    Table: { borderRadius: 10, headerBg: '#f7f8fa', rowHoverBg: '#f8f8ff' },
    Tag: { borderRadiusSM: 6 },
    Tabs: { inkBarColor: '#4f46e5', itemActiveColor: '#4f46e5', itemSelectedColor: '#4f46e5', itemHoverColor: '#4338ca' },
  },
};

export default themeConfig;
