/// <reference types="vitest/config" />
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
  test: {
    // happy-dom 提供 window/setTimeout —— useJobStream 的心跳与退避计时器依赖它。
    // 注意：composable 只用定时器，不依赖真实 SSE 网络，故不需要 fetch mock 之外的环境。
    environment: 'happy-dom',
    include: ['src/**/*.{test,spec}.ts'],
    globals: false,
    // Windows 路径别名在 vitest 下的解析需与 vite 一致（已由 resolve.alias 提供）。
    clearMocks: true,
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
