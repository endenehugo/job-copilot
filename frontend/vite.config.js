import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发期代理：/api 走 v1 接口；/conversation/image 是历史消息里持久化的
// 图片 URL（无 /api/v1 前缀），必须单独转发到后端
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8018', changeOrigin: true },
      '/conversation/image': { target: 'http://127.0.0.1:8018', changeOrigin: true },
    },
  },
})
