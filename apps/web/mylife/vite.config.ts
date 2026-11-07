import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  base: '/mylife/',
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // dev 后端是自签 HTTPS（configs/dev/cert.pem）→ secure:false
      '/api': { target: 'https://localhost:8080', secure: false },
    },
  },
  build: { outDir: 'dist' },
})
