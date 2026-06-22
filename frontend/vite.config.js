import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react({
    // 使用 classic JSX 运行时（React.createElement），避免 react/jsx-runtime 外部化问题
    jsxRuntime: 'classic',
  })],
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
      external: [
        'react',
        'react-dom',
        'react-dom/client',
        'react-draggable',
        'react-grid-layout',
      ],
      output: {
        globals: {
          'react': 'React',
          'react-dom': 'ReactDOM',
          'react-dom/client': 'ReactDOM',
          'react-draggable': 'ReactDraggable',
          'react-grid-layout': 'ReactGridLayout',
        },
      },
    },
  },
})
