/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, import.meta.dirname, '')
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      strictPort: true,
      // Dev: the browser talks to Vite only and /api is proxied to the backend,
      // so no CORS is involved. Production builds use VITE_API_BASE_URL.
      proxy: {
        '/api': {
          target: env.VITE_DEV_API_PROXY || 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
    preview: { port: 4173 },
    // Entry chunk is React + Supabase + router (~150 kB gzip); pages are split.
    build: { sourcemap: false, chunkSizeWarningLimit: 600 },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: './src/test/setup.ts',
    },
  }
})
