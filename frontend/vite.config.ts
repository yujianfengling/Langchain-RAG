import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), {
    name: 'pdfjs-support-assets',
    apply: 'build',
    generateBundle() {
      // CJK PDFs can use external character maps instead of embedding fonts.
      // Emit the resources from the locked pdfjs-dist package, including its
      // notices, so backend-hosted previews never depend on a public CDN.
      for (const directory of ['cmaps', 'standard_fonts']) {
        const base = new URL(`./node_modules/pdfjs-dist/${directory}/`, import.meta.url)
        for (const file of readdirSync(fileURLToPath(base))) {
          this.emitFile({ type: 'asset', fileName: `pdfjs/${directory}/${file}`,
            source: readFileSync(new URL(file, base)) })
        }
      }
      this.emitFile({ type: 'asset', fileName: 'pdfjs/LICENSE',
        source: readFileSync(new URL('./node_modules/pdfjs-dist/LICENSE', import.meta.url)) })
    },
  }],
  // 注意（2026-10-02 实测）：**不要**在这里加按包名的粗粒度 manualChunks。
  // 试过把 react / antd / framer-motion / echarts 拆成 vendor chunk，结果首屏
  // 从 874 KB 涨到 1580 KB（+81%）——单独成 chunk 后 Rollup 保留了 antd 大量
  // 未被使用的代码，tree-shaking 失效。真正的优化是**路由级懒加载**（见
  // src/App.tsx 的 lazy + Suspense，首屏从 3.94MB 降到 874KB）。
  // 若要再动拆包，必须实测首屏：把 dist/index.html 里所有 /assets/*.js 加起来。
  server: {
    // 目标后端可用 DSH_API_TARGET 覆盖：隔离验收会起第二个 dev server 指向独立后端，
    // 避免验收数据写进演示库（2026-10-07）。默认仍是 8000，行为不变。
    port: Number(process.env.DSH_DEV_PORT || 3000),
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
        target: process.env.DSH_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
      }
    }
  }
})
