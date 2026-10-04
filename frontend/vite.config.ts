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
    // 修复（2026-10-04）：编辑工具/编辑器写文件时会先在源码目录里创建临时文件
    // （形如 `.StudentsPanel.tsx.<pid>.<uuid>.tmpdir/xxx.tmp`）。Windows 上 Vite 的
    // 文件监视器对这类文件会抛 `EBUSY: resource busy or locked`，**直接导致 dev server
    // 退出**（实测过一次）。这里忽略临时文件与常见产物目录，使开发服务不再被写文件动作打死。
    watch: {
      ignored: [
        '**/.*.tmpdir/**',
        '**/*.tmp',
        '**/.git/**',
        '**/dist/**',
      ],
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
      }
    }
  }
})