import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/health': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          // 将 react-grid-layout (± react-draggable) 分离为独立 chunk，
          // 避免其 CJS 内部循环依赖在 Rolldown ESM 转换中产生 TDZ（Cannot access 'L/G' before initialization）
          if (id.includes('node_modules/react-grid-layout') || id.includes('node_modules/react-draggable')) {
            return 'grid'
          }
        },
      },
    },
  },
})
