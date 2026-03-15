import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

const frontendPort = Number.parseInt(process.env.FRONTEND_PORT || '5174', 10) || 5174;
const proxyTarget = process.env.VITE_PROXY_TARGET || process.env.BACKEND_PROXY_TARGET || 'http://127.0.0.1:8008';

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: frontendPort,
    proxy: {
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
      },
      '/health': {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
});
