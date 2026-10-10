import { URL, fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 后端 FastAPI 统一 /api 前缀；SSE（/api/jobs/{id}/stream）同样命中本规则。
      // ws: false —— SSE 是普通 HTTP 长连接，禁止走 websocket 升级路径。
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
        ws: false,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            // 去掉 accept-encoding：避免任何中间层压缩把 SSE 帧缓冲起来，导致进度不实时。
            proxyReq.removeHeader('accept-encoding')
          })
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
})
