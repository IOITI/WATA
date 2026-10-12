import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    // Served by the admin FastAPI app (src/admin/app.py) and shipped with ./src.
    outDir: '../src/admin/static',
    emptyOutDir: true,
    // Nothing inlined as a data: URI: the admin's Content-Security-Policy only allows its own origin.
    assetsInlineLimit: 0,
  },
  server: {
    proxy: {
      // The admin API, or the fake one started with `npm run dev:api`.
      '/api': 'http://127.0.0.1:8082',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
