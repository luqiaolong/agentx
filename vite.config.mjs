import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// Tauri 2.x 简化前端构建配置（取代 electron.vite.config.ts 三入口）
// 仅构建 renderer，main/preload 由 Rust 主进程接管
export default defineConfig({
  root: resolve(__dirname, 'frontend/renderer'),
  resolve: {
    alias: {
      '@': resolve(__dirname, 'frontend/renderer'),
    },
  },
  plugins: [react(), tailwindcss()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    strictPort: true,
  },
})
