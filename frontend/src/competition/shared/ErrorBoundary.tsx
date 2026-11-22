import React from 'react';
import { Alert, Button } from 'antd';

/**
 * 比赛版页面级错误边界：局部渲染异常时给出可见的说明与重试，
 * 不让整页白屏（试测中曾在「现场补测」入口观察到一次白屏，未复现；这是兜底）。
 */
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    // 控制台留痕便于排查；不吞掉页面其余部分
    // eslint-disable-next-line no-console
    console.error('[competition] 页面渲染异常：', error);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24 }}>
          <Alert
            type="error"
            showIcon
            message="这个页面没能完整加载"
            description={`渲染时出现问题：${this.state.error.message}。其余页面不受影响；可返回重试。`}
            action={<Button onClick={() => this.setState({ error: null })}>重试</Button>}
          />
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
