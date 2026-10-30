import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: true,
    // 允许读取仓库根目录下的 ../web/logic.js：两套前端共用同一份纯逻辑，
    // 否则"预算怎么算"会有两个实现，早晚分叉。
    fs: { allow: ['..'] },
    // 前端 5173、后端 8000，开发时用代理避免跨域；生产直接走同源
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
