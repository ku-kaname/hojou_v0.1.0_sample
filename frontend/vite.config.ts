import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// 開発サーバーでは、/api をバックエンド（FastAPI）へ中継する（本番はNginxが中継する）
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/tests/setup.ts'],
    include: ['src/tests/**/*.test.{ts,tsx}'],
  },
})
