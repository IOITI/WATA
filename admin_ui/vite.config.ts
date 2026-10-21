import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

import { pinetsZoneFormat, pinetsZoneFormatDeps } from './vite/pinetsZoneFormat.ts'
import { velaPineWorker } from './vite/velaPineWorker.ts'
import { velaSeconds, velaSecondsDeps } from './vite/velaSeconds.ts'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), velaPineWorker(), velaSeconds()],
  // Vela's chart learns bars of seconds, and PineTS keeps a formatter per time zone, in the dev server
  // too, whose dependencies Vite prepares itself.
  optimizeDeps: {
    rolldownOptions: { plugins: [velaSecondsDeps(), pinetsZoneFormatDeps()] },
  },
  // The Pine worker of the Technical Analysis pages: one classic file whose name the
  // admin recognises, so that it gets the only policy allowing PineTS to compile
  // (src/admin/app.py, WORKER_PATH). Any other worker keeps an ordinary name, and
  // with it the pages' policy.
  worker: {
    format: 'iife',
    // The worker holds PineTS, which keeps a formatter per time zone there (vite/pinetsZoneFormat.ts).
    plugins: () => [pinetsZoneFormat()],
    rolldownOptions: {
      output: {
        entryFileNames: (chunk) =>
          chunk.name === 'pine.worker' ? 'assets/pine-worker-[hash].js' : 'assets/[name]-[hash].js',
      },
    },
  },
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
      // The Host header is passed on as the browser sent it: the admin refuses a
      // change whose Origin is not its own Host, which is what signing in from
      // this dev server would otherwise look like.
      '/api': { target: 'http://127.0.0.1:8082', changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
