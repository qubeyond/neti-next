import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), ['BACKEND_UPSTREAM', 'AGENT_UPSTREAM'])
  return {
    plugins: [react()],
    server: {
      port: 8080,
      strictPort: true,
      proxy: {
        '/api': process.env.BACKEND_UPSTREAM ?? env.BACKEND_UPSTREAM ?? 'http://127.0.0.1:8000',
        '/agent': process.env.AGENT_UPSTREAM ?? env.AGENT_UPSTREAM ?? 'http://127.0.0.1:8001',
      },
    },
  }
})
