import { resolve } from 'node:path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'

// electron-vite 配置：三入口均指向 frontend/（项目结构 spec 要求）
export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, 'frontend/main/index.ts') }
      }
    }
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, 'frontend/preload/index.ts') }
      }
    }
  },
  renderer: {
    root: resolve(__dirname, 'frontend/renderer'),
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, 'frontend/renderer/index.html') }
      }
    },
    resolve: {
      alias: {
        '@': resolve(__dirname, 'frontend/renderer'),
        '@main': resolve(__dirname, 'frontend/main')
      }
    },
    plugins: [react()]
  }
})
