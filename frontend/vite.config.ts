import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  // 注意（2026-10-02 实测）：**不要**在这里加按包名的粗粒度 manualChunks。
  // 试过把 react / antd / framer-motion / echarts 拆成 vendor chunk，结果首屏
  // 从 874 KB 涨到 1580 KB（+81%）——单独成 chunk 后 Rollup 保留了 antd 大量
  // 未被使用的代码，tree-shaking 失效。真正的优化是**路由级懒加载**（见
  // src/App.tsx 的 lazy + Suspense，首屏从 3.94MB 降到 874KB）。
  // 若要再动拆包，必须实测首屏：把 dist/index.html 里所有 /assets/*.js 加起来。
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
      }
    }
  }
})